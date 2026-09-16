"""Compare two survey catalogs row by row.

Rows are joined on the probe key, not on file position. Fields that record
the environment (qick_version, numpy_version) are dropped before comparing,
because they differ on every row by construction.

Each difference is one of:
  outcome   the vendor answer changed (accept / accept_round / reject / crash)
  detail    same answer, different message, observed value, delta or warnings
  added     a probe the other catalog has and the baseline does not
  removed   a probe the baseline has and the other catalog does not

Exit 0 when the comparison ran, whatever it found. Exit 1 on a duplicate
probe key, because a join on a non-unique key silently pairs the wrong rows.

Usage: python tools/versions/compare_catalog.py <baseline-dir> <other-dir>
"""

import collections
import json
import sys
from pathlib import Path

KEY = ("axis", "config", "gen_ch", "gen_type", "kind", "param", "note", "requested")
ENVIRONMENT = ("qick_version", "numpy_version")


def load(directory):
    rows = {}
    for path in sorted(Path(directory).glob("*.jsonl")):
        for line in path.read_text().splitlines():
            row = json.loads(line)
            key = tuple(json.dumps(row.get(k), sort_keys=True) for k in KEY)
            if key in rows:
                sys.exit(f"duplicate probe key in {path.name}: {dict(zip(KEY, key))}")
            rows[key] = {k: v for k, v in row.items() if k not in ENVIRONMENT}
    return rows


def label(key):
    fields = dict(zip(KEY, (json.loads(k) for k in key)))
    return (f"{fields['config']} {fields['axis']} gen{fields['gen_ch']} "
            f"{fields['kind']} {fields['param']}={fields['requested']} ({fields['note']})")


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: compare_catalog.py <baseline-dir> <other-dir>")
    base = load(sys.argv[1])
    other = load(sys.argv[2])

    found = collections.defaultdict(list)
    for key in sorted(base.keys() | other.keys()):
        if key not in other:
            found["removed"].append((key, None))
        elif key not in base:
            found["added"].append((key, None))
        elif base[key]["outcome"] != other[key]["outcome"]:
            found["outcome"].append((key, (base[key], other[key])))
        elif base[key] != other[key]:
            found["detail"].append((key, (base[key], other[key])))

    print(f"rows: baseline {len(base)}, other {len(other)}")
    for kind in ("outcome", "detail", "added", "removed"):
        print(f"{kind}: {len(found[kind])}")

    by_axis = collections.Counter()
    for kind in ("outcome", "detail", "added", "removed"):
        for key, _ in found[kind]:
            fields = dict(zip(KEY, (json.loads(k) for k in key)))
            by_axis[(fields["config"], fields["axis"], kind)] += 1
    if by_axis:
        print("\nby config, axis and kind")
        for (config, axis, kind), n in sorted(by_axis.items()):
            print(f"  {config:22} {axis:12} {kind:8} {n}")

    for kind in ("outcome", "detail", "added", "removed"):
        if not found[kind]:
            continue
        print(f"\n{kind}")
        for key, pair in found[kind]:
            print(f"  {label(key)}")
            if pair is None:
                continue
            b, o = pair
            for field in sorted(b.keys() | o.keys()):
                if b.get(field) != o.get(field):
                    print(f"    {field}: {json.dumps(b.get(field))} -> {json.dumps(o.get(field))}")


if __name__ == "__main__":
    main()
