"""Phase-3 gate: every descriptor constraint traces to catalog evidence.

For each emitted descriptor: validate against the descriptor schema
(via tools/format/validate.py logic), then resolve every evidence
reference -- (config, axis, note_contains, outcome) must match at least
one row in tools/survey/catalog/<config>__<axis>.jsonl. Any constraint,
vendor_behavior, or budget without resolving evidence fails the gate.

A resolving reference says only that a row exists. A budget also makes an
arithmetic claim, and a cost model can contradict the very rows it cites: the
Qblox instruction budget declared one instruction per output next to rows that
show two. So a budget whose evidence reaches counted rows must also PREDICT
them, accept for accept and reject for reject.

Exit 0 = gate passed for all descriptors.
"""

import json
import re
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


def row_module(row):
    """The module a catalog row was measured on, or None if it does not say.

    An accepted row records the module it ran on. A refused row carries only
    the toolchain's message, which names the module in quotes.
    """
    module = (row.get("observed") or {}).get("module")
    if module:
        return module
    match = re.search(r"'([A-Za-z0-9]+_module[0-9]+)'", row.get("error_msg", "") or "")
    return match.group(1) if match else None


def budget_modules(doc, budget):
    """The modules this budget's channels live on.

    A Qblox channel names its hardware path, cluster0.module2.complex_output_0,
    and the toolchain spells the same module cluster0_module2. A QICK channel
    names an IP core instead, so there is no module and the caller judges every
    row it reaches.
    """
    names = budget.get("channels") or [ch["name"] for ch in doc["channels"]]
    modules = set()
    for ch in doc["channels"]:
        if ch["name"] not in names:
            continue
        parts = (ch.get("vendor_type") or "").split(".")
        if len(parts) >= 3:
            modules.add(f"{parts[0]}_{parts[1]}")
    return modules


def decisive_rows(doc, budget, evref):
    """The rows that can judge this budget's arithmetic.

    A row judges a budget only if it was measured on the hardware the budget
    describes, because an outcome is an outcome against that board's limit. So
    the row's config must be the one this descriptor describes, and its module
    must be one this budget counts for. A row that says neither, as every QICK
    row does, is judged on the config alone.

    The parameter name selects counted rows: a budget axis also carries probes
    of other quantities, such as waveform samples on the Qblox budget_instr
    axis.
    """
    if not doc["identification"]["name"].endswith(evref["config"]):
        return []
    modules = budget_modules(doc, budget)
    out = []
    for row in rows_for(evref["config"], evref["axis"]) or []:
        if evref["note_contains"] not in row.get("note", ""):
            continue
        if row.get("param") != "n_pulses" or not isinstance(row.get("requested"), int):
            continue
        module = row_module(row)
        if modules and module is not None and module not in modules:
            continue
        out.append(row)
    return out


def check_cost_model(doc, budget):
    """A linear cost model must predict every row that can judge it.

    Returns the problems and how many rows were judged. A budget whose rows
    were all filtered out is not proved wrong, and it is not proved right
    either, so the caller reports the count.
    """
    cost = budget["cost_model"]
    if cost["kind"] != "linear":
        return [], 0
    per_item = cost["per_item"]
    overhead = cost.get("overhead", 0)
    limit = budget["limit"]
    problems, judged = [], 0
    for evref in budget["evidence"]:
        for row in decisive_rows(doc, budget, evref):
            n = row["requested"]
            predicted = "reject" if per_item * n + overhead > limit else "accept"
            judged += 1
            if predicted != row["outcome"]:
                problems.append(
                    f"cost model predicts {predicted} at n={n} "
                    f"({per_item}*{n}+{overhead} against limit {limit}), "
                    f"catalog row '{row['note']}' says {row['outcome']}")
    return problems, judged


def check(path):
    errs = validate_file(path)
    if errs:
        return [f"schema/format: {e}" for e in errs], 0
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
    judged = 0
    for b in doc["budgets"]:
        misses, n = check_cost_model(doc, b)
        judged += n
        for miss in misses:
            problems.append(f"budget {b['id']}: {miss}")
    return problems, judged


def main():
    failed = False
    targets = sys.argv[1:] or sorted((HERE / "descriptors").glob("*.json"))
    for path in targets:
        problems, judged = check(path)
        if problems:
            failed = True
            print(f"FAIL {path}")
            for p in problems[:25]:
                print(f"  {p}")
        else:
            print(f"GATE OK {path} ({judged} budget rows predicted)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
