"""Phase-3 gate: every descriptor constraint traces to catalog evidence.

For each emitted descriptor: validate against the descriptor schema
(via tools/format/validate.py logic), then resolve every evidence
reference -- (config, axis, note_contains, outcome) must match at least
one row in tools/survey/catalog/<config>__<axis>.jsonl. Any constraint,
vendor_behavior, or budget without resolving evidence fails the gate.

Exit 0 = gate passed for all descriptors.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
CATALOG = HERE.parent / "survey" / "catalog"
sys.path.insert(0, str(HERE.parent / "format"))
from validate import validate_file  # noqa: E402


def rows_for(config, axis):
    path = CATALOG / f"{config}__{axis}.jsonl"
    if not path.exists():
        return None
    return [json.loads(line) for line in path.read_text().splitlines()]


def resolve(evref):
    rows = rows_for(evref["config"], evref["axis"])
    if rows is None:
        return f"no catalog file {evref['config']}__{evref['axis']}.jsonl"
    hits = [r for r in rows
            if evref["note_contains"] in r.get("note", "")
            and r.get("outcome") == evref["outcome"]]
    if not hits:
        return (f"no row in {evref['config']}__{evref['axis']} with "
                f"note~'{evref['note_contains']}' outcome={evref['outcome']}")
    return None


def check(path):
    errs = validate_file(path)
    if errs:
        return [f"schema/format: {e}" for e in errs]
    doc = json.loads(Path(path).read_text())
    problems = []

    def check_evidence(owner, entries):
        for e in entries:
            for evref in e["evidence"]:
                miss = resolve(evref)
                if miss:
                    problems.append(f"{owner} {e['id']}: {miss}")

    for ch in doc["channels"]:
        check_evidence(f"channel {ch['name']} constraint", ch["constraints"])
        check_evidence(f"channel {ch['name']} vendor_behavior",
                       ch.get("vendor_behavior", []))
    check_evidence("budget", doc["budgets"])
    return problems


def main():
    failed = False
    targets = sys.argv[1:] or sorted((HERE / "descriptors").glob("*.json"))
    for path in targets:
        problems = check(path)
        if problems:
            failed = True
            print(f"FAIL {path}")
            for p in problems[:25]:
                print(f"  {p}")
        else:
            print(f"GATE OK {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
