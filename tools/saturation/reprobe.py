"""Summarize fault injection survivors re-run at more corpus seeds.

Input: run_mutants.py output directories, one per seed, each holding
summary.jsonl, and the recorded S2 result in tools/mutation/results/.

Seed 1 must reproduce the recorded status of every re-run (mutant, mode),
or the other seeds cannot be compared with it. Then, for each survivor, the
first seed at which it is detected, and the cumulative count of survivors
caught by seed, per disposition class.

A survivor caught at a later seed was a corpus gap that more random programs
close. One still surviving at the last seed was not reached by these seeds.
Neither says what a different generator would do.

Usage: python tools/saturation/reprobe.py <out-file> <reprobe-dir-seed-1> [<dir-seed-2> ...]
The seed is read from the directory name suffix (reprobe-<seed>).
"""

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "mutation"))

import explain  # noqa: E402


def load(path):
    return {(r["id"], r["mode"]): r for r in map(json.loads, Path(path).read_text().splitlines())
            if r["role"] != "baseline"}


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    out_path = Path(sys.argv[1])
    dirs = sorted((Path(d) for d in sys.argv[2:]), key=lambda d: int(d.name.rsplit("-", 1)[1]))
    seeds = [int(d.name.rsplit("-", 1)[1]) for d in dirs]
    if seeds[0] != 1:
        sys.exit("the first directory must be seed 1")

    recorded = load(ROOT / "tools/mutation/results/summary.jsonl")
    classes = {}
    for e in explain.parse_dispositions(ROOT / "tools/mutation/results/dispositions.txt"):
        modes = ("original", "mutated") if e["modes"] == "both" else (e["modes"],)
        for mid in e["ids"]:
            for mode in modes:
                classes[(mid, mode)] = e["class"]

    runs = {seed: load(d / "summary.jsonl") for seed, d in zip(seeds, dirs)}
    seed1 = runs[1]
    mismatch = [k for k in seed1 if seed1[k]["status"] != recorded[k]["status"]]
    if mismatch:
        sys.exit(f"seed 1 does not reproduce the recorded statuses: {mismatch[:5]} ({len(mismatch)})")
    for seed, run in runs.items():
        if run.keys() != seed1.keys():
            sys.exit(f"seed {seed} ran a different set of mutants than seed 1")

    survivors = sorted(k for k in seed1 if seed1[k]["status"] == "survived")
    first = {}
    for k in survivors:
        for seed in seeds:
            if runs[seed][k]["status"] == "detected":
                first[k] = seed
                break

    lines = [f"survivors re-run: {len(survivors)} (mutant, mode) pairs, seeds {seeds[0]}-{seeds[-1]}",
             f"seed 1 reproduces the recorded status of all {len(seed1)} re-run pairs", ""]
    lines.append("caught by seed, cumulative, per class")
    by_class = collections.Counter(classes[k] for k in survivors)
    header = "  class                           total " + " ".join(f"s{s:<3}" for s in seeds)
    lines.append(header)
    for cls in sorted(by_class):
        row = [f"  {cls:31} {by_class[cls]:5} "]
        for seed in seeds:
            row.append(f"{sum(1 for k in survivors if classes[k] == cls and first.get(k, 99) <= seed):<4}")
        lines.append("".join(row[:1]) + " ".join(row[1:]))
    total = [sum(1 for k in survivors if first.get(k, 99) <= s) for s in seeds]
    lines.append(f"  {'all':31} {len(survivors):5} " + " ".join(f"{t:<4}" for t in total))
    lines.append("")
    lines.append("caught, with the first seed that caught them")
    for k in sorted(first, key=lambda k: (first[k], k)):
        failing = runs[first[k]][k].get("failing_cases", {})
        example = next((f"{d}: {cases[0]}" for d, cases in failing.items() if cases), "")
        lines.append(f"  seed {first[k]:<3} {k[0]} [{k[1]}] ({classes[k]}) {example}")
    out_path.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:lines.index("caught, with the first seed that caught them")]))


if __name__ == "__main__":
    main()
