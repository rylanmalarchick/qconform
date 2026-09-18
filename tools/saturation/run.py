"""Measure whether more corpus finds more: the differential over many seeds.

The boundary ladders do not depend on the seed. Only the randomized programs
do. So each seed adds randomized programs, and a program another seed already
produced adds nothing. Programs are identified by the SHA-256 of their JSON
file, not by case name, because names repeat across seeds.

For each config and seed this runs corpus.py and run.py, then accumulates in
seed order and writes one line per seed to <out>/<config>.tsv:

  seed                   the last seed included
  programs_unique        distinct programs so far
  unsound, missed_repair, over_predicted, open, conservative, vendor_lenient,
  harness
                         dispositions over the distinct programs
  rule_sets              distinct sets of fired rules
  outcome_pairs          distinct (verdict, vendor outcome) pairs
  classes_both_sides     classes that both fired and held somewhere
  barrier_programs       distinct programs with a barrier element
  barrier_roundings      of those, rows where a barrier rounding was recorded
  barrier_not_agree      of those, rows whose disposition is not agree

Two checks run before anything is written. Seed 1's rows must be
byte-identical to tools/differential/results/<config>.jsonl. And a program
that appears under two seeds must produce the same row apart from its case
name, or the oracle is not a function of the program.

Every number here is empirical. A flat curve says more random programs of
this generator's kind stopped finding new behavior. It does not say nothing
is left.

Usage: python tools/saturation/run.py <out-dir> [--seeds 1-10] [--configs ...] [--jobs N]
Needs the survey environment, like tools/differential/.
"""

import argparse
import collections
import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "differential"))

from triage import disposition, vendor_behaviors  # noqa: E402

CONFIGS = ("testbench", "qce2025-r26", "rb-r27")
COLUMNS = ("seed", "programs_unique", "unsound", "missed_repair", "over_predicted", "open",
           "conservative",
           "vendor_lenient", "harness", "rule_sets", "outcome_pairs", "classes_both_sides",
           "barrier_programs", "barrier_roundings", "barrier_not_agree")


def paths(config):
    return (ROOT / "tests/golden/descriptors" / f"{config}.json",
            ROOT / "tools/survey/configs" / f"zcu216-{config}.json")


def run_seed(out, config, seed):
    desc, cfg = paths(config)
    corpus = out / config / f"corpus-{seed}"
    rows = out / config / f"rows-{seed}.jsonl"
    for cmd in ([sys.executable, ROOT / "tools/differential/corpus.py", desc, corpus,
                 "--seed", str(seed), "--config", cfg],
                [sys.executable, ROOT / "tools/differential/run.py", corpus, desc, cfg, rows]):
        r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"{config} seed {seed}: {cmd[1]} failed\n{r.stderr[-2000:]}")
    return config, seed


def parse_seeds(text):
    lo, _, hi = text.partition("-")
    return list(range(int(lo), int(hi or lo) + 1))


def accumulate(out, config, seeds):
    desc, _ = paths(config)
    behaviors = vendor_behaviors(json.loads(desc.read_text()))
    by_hash = {}
    lines = []
    for seed in seeds:
        corpus = out / config / f"corpus-{seed}"
        index = json.loads((corpus / "index.json").read_text())
        rows = {r["case"]: r for r in map(json.loads,
                (out / config / f"rows-{seed}.jsonl").read_text().splitlines())}
        for case in index["cases"]:
            text = (corpus / case["program"]).read_bytes()
            h = hashlib.sha256(text).hexdigest()
            row = rows[case["name"]]
            key = {k: v for k, v in row.items() if k != "case"}
            if h in by_hash:
                if by_hash[h]["key"] != key:
                    sys.exit(f"{config}: program {h[:12]} gave different rows under two seeds "
                             f"({by_hash[h]['case']}, {case['name']})")
                continue
            program = json.loads(text)
            by_hash[h] = {"key": key, "case": f"seed {seed}: {case['name']}", "row": row,
                          "barrier": any(e["kind"] == "barrier" for e in program["elements"])}

        disp = collections.Counter()
        rule_sets, pairs = set(), set()
        fires, holds = collections.Counter(), collections.Counter()
        barrier = rounded = barrier_bad = 0
        for entry in by_hash.values():
            row = entry["row"]
            d, _ = disposition(row, behaviors)
            disp[d] += 1
            rule_sets.add(tuple(row["qconform_rules"]))
            pairs.add((row["qconform_verdict"], row["vendor_outcome"]))
            fired = set(row["qconform_rules"])
            for cls in row["qconform_checked"]:
                (fires if cls in fired else holds)[cls] += 1
            if entry["barrier"]:
                barrier += 1
                if row.get("barrier_roundings"):
                    rounded += 1
                if d != "agree":
                    barrier_bad += 1
        both = sum(1 for c in set(fires) | set(holds) if fires[c] and holds[c])
        lines.append((seed, len(by_hash), disp["unsound"], disp["missed_repair"], disp["over_predicted"],
                      disp["open"],
                      disp["conservative"], disp["vendor_lenient"], disp["harness"],
                      len(rule_sets), len(pairs), both, barrier, rounded, barrier_bad))
    return lines, by_hash


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--seeds", default="1-10")
    ap.add_argument("--configs", nargs="*", default=list(CONFIGS))
    ap.add_argument("--jobs", type=int, default=8)
    args = ap.parse_args()
    out = Path(args.out).resolve()
    seeds = parse_seeds(args.seeds)

    jobs = [(c, s) for c in args.configs for s in seeds]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        list(pool.map(lambda j: run_seed(out, *j), jobs))

    for config in args.configs:
        if 1 in seeds:
            got = (out / config / "rows-1.jsonl").read_bytes()
            want = (ROOT / "tools/differential/results" / f"{config}.jsonl").read_bytes()
            if got != want:
                sys.exit(f"{config}: seed 1 does not reproduce the tracked results")

    for config in args.configs:
        lines, by_hash = accumulate(out, config, seeds)
        with open(out / f"{config}.tsv", "w") as f:
            f.write("\t".join(COLUMNS) + "\n")
            for line in lines:
                f.write("\t".join(str(x) for x in line) + "\n")
        failing = sorted(e["case"] for e in by_hash.values()
                         if disposition(e["row"], vendor_behaviors(
                             json.loads(paths(config)[0].read_text())))[0]
                         in ("unsound", "missed_repair", "over_predicted", "open"))
        with open(out / f"{config}-failing.txt", "w") as f:
            f.write("".join(f"{c}\n" for c in failing))
        print(f"{config}: {lines[-1]}")


if __name__ == "__main__":
    main()
