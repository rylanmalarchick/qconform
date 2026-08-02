"""Disposition every row, and report coverage.

The asymmetry is the point. qconform refusing a program the vendor compiles
costs a user a false alarm. qconform accepting a program the vendor refuses
breaks the soundness claim. Only the second is a gate violation.

Dispositions:
  agree           both answers say the same thing
  unsound         qconform called it realizable and the vendor refused. This
                  is the failure the gate forbids
  missed_repair   qconform predicted no repair and the vendor repaired
                  silently. Not unsound, the program still runs, but it is a
                  miss in the capability the tool exists to provide
  vendor_lenient  qconform refused and the vendor compiled. Expected where
                  the descriptor's vendor_behavior list documents it, because
                  the failure surfaces later than compile time
  unobserved      qconform predicted a repair to something this harness
                  cannot read back. Not evidence either way
  harness         the conversion to a double moved a value into another grid
                  cell, so the harness caused the difference
  open            a real disagreement with no explanation yet. These are a
                  to-do list, not a result

Usage: python tools/differential/triage.py <results.jsonl> <descriptor.json>
"""

import argparse
import collections
import json
from pathlib import Path

# A repair to these is invisible to the readback in run.py. The start time of
# a pulse lives in the instruction stream rather than on the pulse object.
UNOBSERVABLE_RULES = {"schedule_grid"}

ACCEPTED = ("pass", "pass_with_repairs")
REFUSED = ("reject", "crash")


# Which documented vendor behavior explains a rule firing while the vendor
# still compiles. The descriptor lists the behaviors and the rules separately
# and does not link them, so the link is stated here.
#
# In each of these the vendor accepts the program and emits a register it
# cannot honor. A frequency past the DDS range wraps, a gain past full scale
# stays out of range, and an envelope past memory overruns at board load. The
# consequence is at run time, so compile-time acceptance is not disagreement.
RULE_TO_BEHAVIOR = {
    "frequency_range": "frequency_alias_mod_f_dds",
    "amplitude_range": "gain_over_full_scale",
    "envelope_memory": "envelope_memory_overflow_unchecked",
}


def vendor_behaviors(descriptor):
    """Rules the descriptor says the vendor handles differently from the
    checker on purpose."""
    out = {}
    for ch in descriptor.get("channels", []):
        for vb in ch.get("vendor_behavior", []):
            out[vb["id"]] = vb
    return out


def disposition(row, behaviors):
    verdict = row["qconform_verdict"]
    outcome = row["vendor_outcome"]
    rules = set(row.get("qconform_rules", []))

    if outcome == "not_lowerable":
        return "harness", "the harness cannot express this program in the vendor API"
    if row.get("lowering_lost_cells"):
        return "harness", "a converted value landed in another grid cell"

    if verdict == "tool_error":
        return "harness", "the checker refused the generated program as malformed"

    if verdict in ACCEPTED and outcome in REFUSED:
        detail = row.get("vendor_detail", {})
        return "unsound", f"vendor refused: {detail.get('error_msg', '')[:120]}"

    if verdict == "fail" and outcome in REFUSED:
        return "agree", "both refused"

    documented = sorted({RULE_TO_BEHAVIOR[r] for r in rules
                         if RULE_TO_BEHAVIOR.get(r) in behaviors})

    if verdict == "fail" and outcome in ("accept", "accept_round"):
        if documented:
            return "vendor_lenient", f"documented vendor_behavior: {documented[0]}"
        return "open", f"qconform refused ({sorted(rules)}), vendor compiled"

    if verdict == "pass" and outcome == "accept_round":
        return "missed_repair", "vendor repaired a program qconform passed clean"

    if verdict == "pass" and outcome == "accept":
        return "agree", "both clean"

    if verdict == "pass_with_repairs" and outcome == "accept_round":
        return "agree", "both say the vendor repairs this"

    if verdict == "pass_with_repairs" and outcome == "accept":
        if rules and rules <= UNOBSERVABLE_RULES:
            return "unobserved", f"repair predicted to {sorted(rules)}, not readable back"
        if documented:
            return "vendor_lenient", f"documented vendor_behavior: {documented[0]}"
        return "open", f"qconform predicted a repair ({sorted(rules)}) the vendor did not apply"

    return "open", f"{verdict} against {outcome}"


def coverage(rows, descriptor):
    """Per class: did it fire, and was it checked without firing.

    Both come from qconform's own report, so there is no second source of
    truth to drift from the checker.
    """
    fires = collections.Counter()
    holds = collections.Counter()
    for r in rows:
        fired = set(r.get("qconform_rules", []))
        for cls in r.get("qconform_checked", []):
            if cls in fired:
                fires[cls] += 1
            else:
                holds[cls] += 1
    return fires, holds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("descriptor")
    args = ap.parse_args()

    rows = [json.loads(l) for l in Path(args.results).read_text().splitlines() if l.strip()]
    descriptor = json.loads(Path(args.descriptor).read_text())
    behaviors = vendor_behaviors(descriptor)

    by_disp = collections.defaultdict(list)
    for r in rows:
        disp, why = disposition(r, behaviors)
        r["_disposition"] = disp
        r["_why"] = why
        by_disp[disp].append(r)

    print(f"programs: {len(rows)}")
    print()
    print("disposition")
    for disp in ("agree", "vendor_lenient", "unobserved", "missed_repair",
                 "harness", "open", "unsound"):
        n = len(by_disp.get(disp, []))
        if n:
            print(f"  {disp:16} {n}")

    print()
    unsound = by_disp.get("unsound", [])
    print(f"UNSOUND PASSES: {len(unsound)}   <- the gate")
    for r in unsound:
        print(f"  {r['case']}: {r['_why']}")

    for disp in ("missed_repair", "open"):
        items = by_disp.get(disp, [])
        if items:
            print()
            print(f"{disp} ({len(items)}), each needs a reason:")
            for r in items:
                print(f"  {r['case']}: {r['_why']}")

    fires, holds = coverage(rows, descriptor)

    print()
    print("coverage, from the checker's own manifests")
    print(f"  {'class':24} {'fires':>6} {'holds':>6}  covered")
    seen = sorted(set(fires) | set(holds))
    covered = 0
    for cls in seen:
        ok = fires[cls] > 0 and holds[cls] > 0
        covered += 1 if ok else 0
        print(f"  {cls:24} {fires[cls]:6} {holds[cls]:6}  {'yes' if ok else 'no'}")
    print(f"  covered on both sides: {covered} of {len(seen)} exercised classes")


if __name__ == "__main__":
    main()
