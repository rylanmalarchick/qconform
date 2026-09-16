"""Explain fault injection results, row by row, so survivors can be sorted.

Code here compares and groups. It assigns no verdicts. Classes come from
results/dispositions.txt, written by hand with evidence, and this tool only
checks that the file covers every survivor exactly once and counts it.

Subcommands:
  groups  <summary> <rows-dir>              survivors grouped by signature
  show    <summary> <rows-dir> <id> <mode>  every row that differs from baseline
  table   <summary> <rows-dir> <dispositions>  coverage check and rate table

A signature is the multiset of (baseline disposition -> mutant disposition)
over rows joined on case name, plus the count of new checker tool errors.
A case present on only one side counts as "absent".

<rows-dir> is run_mutants.py's rows/ directory.
"""

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "differential"))

from triage import disposition, vendor_behaviors  # noqa: E402

CLASSES = ("equivalent", "corpus_gap", "harness_gap", "masked_by_documented_leniency",
           "checker_refused_programs")


def load_summary(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def descriptor_for(row):
    """The descriptor triage needs for vendor_behavior. Mutants never change
    vendor_behavior, so the config's golden descriptor serves."""
    return json.loads((ROOT / "tests/golden/descriptors" / f"{row['config']}.json").read_text())


def dispositions(rows_dir, mode, mid, behaviors):
    out = {}
    path = Path(rows_dir) / mode / f"{mid}.jsonl"
    for line in path.read_text().splitlines():
        r = json.loads(line)
        d, reason = disposition(r, behaviors)
        out[r["case"]] = (d, reason, r)
    return out


def diff(rows_dir, row):
    behaviors = vendor_behaviors(descriptor_for(row))
    base = dispositions(rows_dir, row["mode"], f"{row['config']}.baseline", behaviors)
    mut = dispositions(rows_dir, row["mode"], row["id"], behaviors)
    changes = []
    for case in sorted(base.keys() | mut.keys()):
        b = base.get(case)
        m = mut.get(case)
        bd = b[0] if b else "absent"
        md = m[0] if m else "absent"
        brow = b[2] if b else {}
        mrow = m[2] if m else {}
        same_verdict = (brow.get("qconform_verdict"), brow.get("qconform_rules")) == \
                       (mrow.get("qconform_verdict"), mrow.get("qconform_rules"))
        if bd == md and same_verdict:
            continue
        changes.append({
            "case": case, "from": bd, "to": md,
            "verdict": f"{brow.get('qconform_verdict')} -> {mrow.get('qconform_verdict')}",
            "rules": f"{brow.get('qconform_rules')} -> {mrow.get('qconform_rules')}",
            "vendor": mrow.get("vendor_outcome", brow.get("vendor_outcome")),
            "reason": (m or b)[1],
            "stderr": mrow.get("qconform_stderr", "")[:160],
            "new_tool_error": mrow.get("qconform_verdict") == "tool_error"
                              and brow.get("qconform_verdict") != "tool_error",
        })
    return changes


def signature(changes):
    trans = collections.Counter(f"{c['from']}->{c['to']}" for c in changes)
    verdict_only = sum(1 for c in changes if c["from"] == c["to"])
    tool = sum(1 for c in changes if c["new_tool_error"])
    return (tuple(sorted(trans.items())), verdict_only, tool)


def cmd_groups(summary, rows_dir):
    groups = collections.defaultdict(list)
    example = {}
    for row in load_summary(summary):
        if row["role"] == "baseline" or row["status"] != "survived":
            continue
        changes = diff(rows_dir, row)
        sig = signature(changes)
        groups[sig].append(f"{row['id']} [{row['mode']}]")
        example.setdefault(sig, changes[:3])
    for sig, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        trans, verdict_only, tool = sig
        print(f"\n== {len(members)} survivors: transitions {dict(trans) or '{}'}, "
              f"verdict-only changes {verdict_only}, new tool errors {tool}")
        for c in example[sig]:
            print(f"   e.g. {c['case']}: {c['from']}->{c['to']}; {c['verdict']}; "
                  f"{c['rules']}; vendor {c['vendor']}; {c['reason'][:90]}"
                  + (f"; stderr {c['stderr']}" if c["stderr"] else ""))
        for mem in sorted(members):
            print(f"   {mem}")


def cmd_show(summary, rows_dir, mid, mode):
    row = next(r for r in load_summary(summary) if r["id"] == mid and r["mode"] == mode)
    print(json.dumps({k: row.get(k) for k in ("id", "mode", "status", "dispositions",
                                              "field", "old", "new")}))
    for c in diff(rows_dir, row):
        print(f"  {c['case']}: {c['from']}->{c['to']}; {c['verdict']}; {c['rules']}; "
              f"vendor {c['vendor']}; {c['reason'][:110]}"
              + (f"; stderr {c['stderr']}" if c["stderr"] else ""))


def parse_dispositions(path):
    """Blocks separated by blank lines. Keys: class, modes, reason, evidence,
    ids (one per following indented line)."""
    entries = []
    for block in Path(path).read_text().split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln.strip() and not ln.startswith("#")]
        if not lines:
            continue
        e = {"ids": []}
        key = None
        for ln in lines:
            if ln.startswith("  ") and key == "ids":
                e["ids"].append(ln.strip())
            elif ln.startswith("  ") and key:
                e[key] += " " + ln.strip()
            else:
                key, _, value = ln.partition(":")
                key = key.strip()
                e[key] = value.strip() if key != "ids" else e["ids"]
        if e.get("class") not in CLASSES:
            sys.exit(f"unknown class {e.get('class')!r} in block starting {lines[0]!r}")
        if e.get("modes") not in ("original", "mutated", "both"):
            sys.exit(f"bad modes {e.get('modes')!r} for class {e['class']}")
        for field in ("reason", "evidence"):
            if not e.get(field):
                sys.exit(f"{e['class']} block with ids {e['ids'][:2]} has no {field}")
        entries.append(e)
    return entries


def cmd_table(summary, rows_dir, disp_path):
    rows = [r for r in load_summary(summary) if r["role"] != "baseline"]
    assigned = {}
    for e in parse_dispositions(disp_path):
        modes = ("original", "mutated") if e["modes"] == "both" else (e["modes"],)
        for mid in e["ids"]:
            for mode in modes:
                if (mid, mode) in assigned:
                    sys.exit(f"{mid} [{mode}] is assigned twice")
                assigned[(mid, mode)] = e["class"]

    survivors = {(r["id"], r["mode"]) for r in rows if r["status"] == "survived"}
    missing = sorted(survivors - assigned.keys())
    extra = sorted(assigned.keys() - survivors)
    if missing or extra:
        sys.exit(f"unassigned survivors: {missing[:10]} ({len(missing)})\n"
                 f"assigned but not survivors: {extra[:10]} ({len(extra)})")

    def outcome(r):
        if r["status"] == "survived":
            return assigned[(r["id"], r["mode"])]
        return r["status"]

    columns = ("detected", "refused_by_checker", "checker_refused_programs", "equivalent",
               "masked_by_documented_leniency", "harness_gap", "corpus_gap")
    table = collections.defaultdict(collections.Counter)
    for r in rows:
        table[(r["mode"], r["role"])][outcome(r)] += 1
        table[(r["mode"], "all")][outcome(r)] += 1

    print("mode      role            " + " ".join(f"{c[:12]:>12}" for c in columns)
          + "   raw_rate  rate_excl")
    for key in sorted(table):
        c = table[key]
        total = sum(c.values())
        caught = c["detected"]
        excluded = c["equivalent"] + c["refused_by_checker"] + c["checker_refused_programs"]
        raw = caught / total if total else 0
        excl = caught / (total - excluded) if total - excluded else 0
        print(f"{key[0]:9} {key[1]:15} " + " ".join(f"{c[col]:>12}" for col in columns)
              + f"   {raw:8.3f}  {excl:9.3f}")
    print("\nraw_rate = detected / all mutants. rate_excl = detected / (all - equivalent"
          " - refused_by_checker - checker_refused_programs).")


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "groups":
        cmd_groups(sys.argv[2], sys.argv[3])
    elif cmd == "show" and len(sys.argv) == 6:
        cmd_show(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    elif cmd == "table" and len(sys.argv) == 5:
        cmd_table(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
