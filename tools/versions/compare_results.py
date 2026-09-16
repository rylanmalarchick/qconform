"""Compare two differential result files row by row, and attribute each change.

Rows are joined on the case name. A row counts as changed when the vendor half
differs: vendor_outcome, vendor_detail, vendor_changed or vendor_registers.
The qconform half cannot change between qick versions, and a difference there
is reported as a harness fault.

A changed vendor answer gets a first-pass attribution. It is a sort, not a
verdict: a vendor can also crash without meaning to, so every harness row is
read by hand before anything is counted.
  toolchain  the vendor raised RuntimeError or ValueError (a typed refusal),
             or compiled to different values
  harness    any other exception type. lower.py maps those to "crash", and a
             TypeError or AttributeError under another qick is far more likely
             an API change the harness does not speak than a vendor answer

Exit 1 if the two files do not hold the same cases.

Usage: python tools/versions/compare_results.py <baseline.jsonl> <other.jsonl>
"""

import collections
import json
import sys
from pathlib import Path

VENDOR = ("vendor_outcome", "vendor_detail", "vendor_changed", "vendor_registers")
QCONFORM = ("qconform_verdict", "qconform_rules", "qconform_checked")
TYPED = ("RuntimeError", "ValueError")


def load(path):
    return {row["case"]: row for row in map(json.loads, Path(path).read_text().splitlines())}


def attribute(row):
    detail = row.get("vendor_detail") or {}
    if row["vendor_outcome"] == "crash" or (
            detail.get("error_type") and detail["error_type"] not in TYPED):
        return "harness"
    return "toolchain"


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: compare_results.py <baseline.jsonl> <other.jsonl>")
    base = load(sys.argv[1])
    other = load(sys.argv[2])
    if base.keys() != other.keys():
        sys.exit(f"case sets differ: {sorted(base.keys() ^ other.keys())[:5]}")

    changed = collections.defaultdict(list)
    for case in sorted(base):
        b, o = base[case], other[case]
        if any(b.get(f) != o.get(f) for f in QCONFORM):
            changed["qconform_half_changed"].append(case)
            continue
        if all(b.get(f) == o.get(f) for f in VENDOR):
            continue
        kind = "outcome" if b["vendor_outcome"] != o["vendor_outcome"] else "detail"
        changed[(kind, attribute(o))].append(case)

    print(f"rows: {len(base)}")
    for key in sorted(changed, key=str):
        print(f"{key if isinstance(key, str) else ' '.join(key)}: {len(changed[key])}")

    messages = collections.Counter()
    for key, cases in sorted(changed.items(), key=lambda kv: str(kv[0])):
        print(f"\n{key if isinstance(key, str) else ' '.join(key)}")
        for case in cases:
            b, o = base[case], other[case]
            print(f"  {case}: {b['qconform_verdict']}, vendor "
                  f"{b['vendor_outcome']} -> {o['vendor_outcome']}")
            detail = o.get("vendor_detail") or {}
            if detail.get("error_msg"):
                messages[(detail.get("error_type"), detail["error_msg"][:90])] += 1

    if messages:
        print("\nnew vendor messages, by count")
        for (etype, msg), n in messages.most_common():
            print(f"  {n:4}  {etype}: {msg}")


if __name__ == "__main__":
    main()
