"""Run every mutant through the differential, in two corpus modes, and count.

Modes:
  original  the corpus comes from the correct descriptor. Only the checker's
            answer can change, so this measures the checker.
  mutated   the corpus comes from the mutant. A wrong descriptor also moves
            the boundary ladders, which is what a real descriptor bug does.

In both modes run.py gets the mutant as the checker's descriptor and the
correct descriptor for the lowering (--oracle-descriptor), so the question put
to the vendor never depends on the mutant.

Per (mutant, mode) the status is one of:
  refused_by_checker  every row is a checker tool error: the checker's own
                      descriptor validation caught the mutant
  corpus_error        corpus.py failed on the mutant (mutated mode)
  detected            at least one unsound, missed_repair, over_predicted or
                      open row
  survived            none of the above
harness_shift is set when the harness disposition count differs from the
unmutated run of the same config and mode. Those rows need reading.

Checker patches (K1, K4) are applied in a git worktree of HEAD and built
there. make check also runs in that worktree, and its exit is recorded,
because the tripwires can catch a checker defect before any corpus runs.

The first row per config and mode is the unmutated descriptor. It must come
out survived with no failure rows, or the counts mean nothing.

With --seed, both corpus modes use that seed. The ladders do not depend on
it and the randomized programs do, so a survivor re-run at other seeds shows
whether more random programs would catch it.

Usage: python tools/mutation/run_mutants.py <out-dir> [--jobs N] [--seed S]
           [--only ID ... | --only-file PATH]
Needs the survey environment (qick), like tools/differential/.
"""

import argparse
import collections
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "differential"))

from triage import disposition, vendor_behaviors  # noqa: E402

CONFIGS = ("testbench", "qce2025-r26")
MODES = ("original", "mutated")
FAILURES = ("unsound", "missed_repair", "over_predicted", "open")
PATCHES = {
    "qce2025-r26.known.K1_post_mixer_ignored": "k1-post-mixer-ignored.patch",
    "qce2025-r26.known.K4_n_tones_unread": "k4-n-tones-unread.patch",
}


def paths(config):
    return (ROOT / "tests" / "golden" / "descriptors" / f"{config}.json",
            ROOT / "tools" / "survey" / "configs" / f"zcu216-{config}.json")


def run(cmd, cwd=None):
    return subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True)


def build_mutants(out):
    """Generate every mutant set. Returns manifest rows, baselines first."""
    rows = []
    for config in CONFIGS:
        desc, _ = paths(config)
        sets = [("rep", [])]
        if config == "qce2025-r26":
            sets.append(("dup", ["--duplicates"]))
        rows.append({"id": f"{config}.baseline", "config": config, "role": "baseline",
                     "descriptor": str(desc)})
        for name, flags in sets:
            target = out / "mutants" / config / name
            r = run([sys.executable, ROOT / "tools/mutation/mutants.py", desc, config, target, *flags])
            if r.returncode != 0:
                sys.exit(f"mutants.py failed for {config} {name}: {r.stderr}")
            for line in (target / "manifest.jsonl").read_text().splitlines():
                m = json.loads(line)
                m["descriptor"] = str(target / m["id"] / "descriptor.json")
                rows.append(m)
        if config == "qce2025-r26":
            for mid, patch in sorted(PATCHES.items()):
                rows.append({"id": mid, "config": config, "role": "known", "channel": None,
                             "kind": mid.split(".", 1)[1], "field": "src/check.c",
                             "old": None, "new": patch, "descriptor": str(desc),
                             "patch": patch})
    return rows


def build_worktree(out, patch):
    """A worktree of HEAD with one checker patch applied and built."""
    wt = out / "worktrees" / patch.removesuffix(".patch")
    if wt.exists():
        run(["git", "worktree", "remove", "--force", wt], cwd=ROOT)
        shutil.rmtree(wt, ignore_errors=True)
    wt.parent.mkdir(parents=True, exist_ok=True)
    for cmd in (["git", "worktree", "add", "--detach", wt, "HEAD"],):
        r = run(cmd, cwd=ROOT)
        if r.returncode != 0:
            sys.exit(f"worktree failed: {r.stderr}")
    r = run(["git", "apply", ROOT / "tools/mutation/patches" / patch], cwd=wt)
    if r.returncode != 0:
        sys.exit(f"patch {patch} does not apply: {r.stderr}")
    r = run(["make", "-s", "qconform"], cwd=wt)
    if r.returncode != 0:
        sys.exit(f"patched build failed: {r.stderr}")
    check = run(["make", "check"], cwd=wt)
    tail = [ln for ln in (check.stdout + check.stderr).splitlines() if ln.strip()][-3:]
    return wt, {"make_check_exit": check.returncode, "make_check_tail": tail}


def one(m, mode, out, original_corpus, worktrees, seed):
    config = m["config"]
    orig_desc, cfg = paths(config)
    tree = worktrees.get(m.get("patch"), (ROOT, {}))[0]
    row = {k: m.get(k) for k in ("id", "config", "role", "channel", "kind", "field", "old", "new")}
    row["mode"] = mode
    row.update(worktrees.get(m.get("patch"), (ROOT, {}))[1])

    if mode == "original" or m["role"] in ("baseline", "known") and "patch" in m:
        corpus = original_corpus[config]
    else:
        corpus = out / "corpus" / config / m["id"]
        r = run([sys.executable, tree / "tools/differential/corpus.py", m["descriptor"], corpus,
                 "--seed", str(seed), "--config", cfg])
        if r.returncode != 0:
            row["status"] = "corpus_error"
            row["error"] = r.stderr.strip().splitlines()[-1][:300] if r.stderr.strip() else ""
            return row

    rows_path = out / "rows" / mode / f"{m['id']}.jsonl"
    r = run([sys.executable, tree / "tools/differential/run.py", corpus, m["descriptor"], cfg,
             rows_path, "--oracle-descriptor", orig_desc])
    if r.returncode != 0:
        row["status"] = "run_error"
        row["error"] = r.stderr.strip().splitlines()[-1][:300] if r.stderr.strip() else ""
        return row

    results = [json.loads(line) for line in rows_path.read_text().splitlines()]
    behaviors = vendor_behaviors(json.loads(Path(m["descriptor"]).read_text()))
    counts = collections.Counter()
    failing = collections.defaultdict(list)
    for res in results:
        d, _ = disposition(res, behaviors)
        counts[d] += 1
        if d in FAILURES:
            failing[d].append(res["case"])
    row["programs"] = len(results)
    row["dispositions"] = dict(sorted(counts.items()))
    row["failing_cases"] = {k: sorted(v) for k, v in sorted(failing.items())}

    tool_errors = [res for res in results if res["qconform_verdict"] == "tool_error"]
    if results and len(tool_errors) == len(results):
        row["status"] = "refused_by_checker"
        row["error"] = tool_errors[0].get("qconform_stderr", "")[:300]
    elif any(counts[f] for f in FAILURES):
        row["status"] = "detected"
    else:
        row["status"] = "survived"
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--jobs", type=int, default=16)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--only-file", default=None, help="mutant ids, one per line")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    if args.only_file is not None:
        args.only = [ln.strip() for ln in Path(args.only_file).read_text().splitlines()
                     if ln.strip()]
    out = Path(args.out).resolve()

    manifest = build_mutants(out)
    if args.only is not None:
        keep = set(args.only)
        manifest = [m for m in manifest if m["role"] == "baseline" or m["id"] in keep]

    original_corpus = {}
    for config in CONFIGS:
        desc, cfg = paths(config)
        target = out / "corpus" / config / "_original"
        r = run([sys.executable, ROOT / "tools/differential/corpus.py", desc, target,
                 "--seed", str(args.seed), "--config", cfg])
        if r.returncode != 0:
            sys.exit(f"original corpus failed for {config}: {r.stderr}")
        original_corpus[config] = target

    worktrees = {}
    for patch in sorted({m["patch"] for m in manifest if "patch" in m}):
        worktrees[patch] = build_worktree(out, patch)

    jobs = [(m, mode) for m in manifest for mode in MODES]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        summary = list(pool.map(lambda j: one(j[0], j[1], out, original_corpus, worktrees, args.seed), jobs))

    base = {(r["config"], r["mode"]): r for r in summary if r["role"] == "baseline"}
    for r in summary:
        b = base.get((r["config"], r["mode"]))
        if b and "dispositions" in r and r is not b and r["status"] != "refused_by_checker":
            r["harness_shift"] = (r["dispositions"].get("harness", 0)
                                  != b["dispositions"].get("harness", 0))

    for patch in worktrees:
        run(["git", "worktree", "remove", "--force", worktrees[patch][0]], cwd=ROOT)

    summary.sort(key=lambda r: (r["config"], r["role"] != "baseline", r["id"], r["mode"]))
    with open(out / "summary.jsonl", "w") as f:
        for r in summary:
            f.write(json.dumps(r, sort_keys=True) + "\n")

    status = collections.Counter((r["mode"], r["status"]) for r in summary if r["role"] != "baseline")
    for r in summary:
        if r["role"] == "baseline":
            print(f"baseline {r['config']} {r['mode']}: {r['status']} {r.get('dispositions')}")
    for (mode, st), n in sorted(status.items()):
        print(f"{mode:9} {st:19} {n}")


if __name__ == "__main__":
    main()
