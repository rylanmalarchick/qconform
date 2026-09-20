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
  over_predicted  both say the vendor repairs this program, but the checker
                  predicts a repair to a quantity the vendor left unchanged.
                  Not unsound: the program runs. It is a claim the evidence
                  contradicts, and it is how a resolution declared too coarse
                  shows up
  missed_repair   also: the vendor changed a quantity the checker predicted
                  no repair to, even when it predicted others
  conservative    qconform refused and the vendor compiled, and every fatal
                  rejection points at a set_frequency or shift_phase that no
                  later output uses. The checker checks a frame when it is
                  set and the vendor checks what a pulse uses, so this is the
                  checker being stricter, proven from the row
  open            a real disagreement with no explanation yet. These are a
                  to-do list, not a result

Usage: python tools/differential/triage.py <results.jsonl> <descriptor.json>
"""

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import oracle   # noqa: E402

# Repairs the harness cannot observe. Empty: run.py now recovers start times
# from the compiled instruction stream, so schedule_grid is observable too.
UNOBSERVABLE_RULES = set()

ACCEPTED = ("pass", "pass_with_repairs")
REFUSED = ("reject", "crash")


# Which documented vendor behavior explains a rule firing while the vendor
# still compiles. Each oracle backend states the link for its own vendor, next
# to the lowering that meets the behavior. See tools/oracle/qick/__init__.py.
RULE_TO_BEHAVIOR = oracle.rule_to_behavior()


# The readback quantity each repair-carrying rule predicts a change to. The
# names are the ones run.py records: pulse readback and tone readback share
# them.
RULE_TO_QUANTITY = {
    "frequency_range": "freq",
    "frequency_resolution": "freq",
    "phase_resolution": "phase",
    "amplitude_range": "gain",
    "amplitude_resolution": "gain",
    # an envelope's scale is a gain register, so its saturation is a gain
    # repair like a constant amplitude's
    "envelope_amplitude": "gain",
    "pulse_length_grid": "total_length",
    "schedule_grid": "start_time",
}


def attribution(row, rules, declared):
    """Compare the repairs the checker predicted with the quantities the
    vendor changed, one quantity at a time.

    A quantity is not compared when a rule predicting it has a vendor
    behavior documented on the row's channels. Those behaviors are exactly
    the cases where the readback reports the request and not what the
    register holds (a gain past full scale, an aliased DDS frequency), so the
    readback cannot say whether the repair happened."""
    changed = {c["quantity"] for c in row.get("vendor_changed", [])}
    if row.get("barrier_roundings"):
        changed.add("start_time")
    predicted = {RULE_TO_QUANTITY[r] for r in rules if r in RULE_TO_QUANTITY}
    unreadable = {RULE_TO_QUANTITY[r] for r in rules
                  if r in RULE_TO_QUANTITY and RULE_TO_BEHAVIOR.get(r, set()) & declared}
    # The oracle reports what it cannot see for this program, such as the
    # Qblox initial NCO frequency, which the instrument quantizes and the
    # offline setting keeps as the float it was given.
    unreadable |= set(row.get("vendor_unobservable", []))

    silent = sorted(changed - predicted)
    if silent:
        return "missed_repair", f"vendor changed {silent}, checker predicted no repair to it"
    over = sorted(predicted - changed - unreadable)
    if over:
        return "over_predicted", f"checker predicted a repair to {over}, vendor left it unchanged"
    return "agree", "both say the vendor repairs this, quantity by quantity"


def vendor_behaviors(descriptor):
    """The vendor behaviors each channel declares, by channel name.

    A behavior belongs to the channel that declares it. Merging them across
    the descriptor let a v6 generator's frequency alias explain a refusal on
    an int4 or a mux channel, which declares no such thing. Fault injection
    found rows filed as vendor_lenient that way."""
    return {ch["name"]: {vb["id"] for vb in ch.get("vendor_behavior", [])}
            for ch in descriptor.get("channels", [])}


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

    declared = set().union(*(behaviors.get(ch, set()) for ch in row["channels"]))
    documented = sorted({b for r in rules
                         for b in RULE_TO_BEHAVIOR.get(r, ())
                         if b in declared})

    if verdict == "fail" and outcome in ("accept", "accept_round"):
        if documented:
            return "vendor_lenient", f"documented vendor_behavior: {documented[0]}"
        fatal = set(row["qconform_fatal_elements"])
        if fatal and fatal <= set(row["unused_frame_updates"]):
            return "conservative", (f"fatal rejections only on frame updates no output "
                                    f"uses: elements {sorted(fatal)}")
        return "open", f"qconform refused ({sorted(rules)}), vendor compiled"

    if verdict == "pass" and outcome == "accept_round":
        return "missed_repair", "vendor repaired a program qconform passed clean"

    if verdict == "pass" and outcome == "accept":
        return "agree", "both clean"

    if verdict == "pass_with_repairs" and outcome == "accept_round":
        return attribution(row, rules, declared)

    if verdict == "pass_with_repairs" and outcome == "accept":
        if rules == {"schedule_grid"} and row.get("barrier_roundings"):
            # The checker fired schedule_grid because a barrier did not align
            # exactly on a member channel's lattice. Both the checker and the
            # vendor round to the same tick there, so the start time compares
            # equal after the fact. The rounding record is the evidence that
            # the repair happened rather than being over-predicted.
            n = len(row["barrier_roundings"])
            return "agree", (f"barrier alignment rounded on {n} frame(s); "
                             f"vendor landed on the same tick")
        if rules and rules <= UNOBSERVABLE_RULES:
            return "unobserved", f"repair predicted to {sorted(rules)}, not readable back"
        if documented:
            return "vendor_lenient", f"documented vendor_behavior: {documented[0]}"
        return "open", f"qconform predicted a repair ({sorted(rules)}) the vendor did not apply"

    return "open", f"{verdict} against {outcome}"


def vacuous_classes(descriptor):
    """Classes this descriptor states in a form that can never reject.

    A sample count is an integer in the program format, with no finer unit to
    carry it, so a grid of one sample accepts every count a program can state.
    The rule is checked and it holds, and no corpus can make it fire. Counting
    it as an uncovered class would read as a hole in the corpus instead of a
    property of the descriptor.

    A duration grid of one is not the same and is not listed here: a program
    states its times in its own unit, so it can ask for half a channel unit
    and miss a grid of one.

    Every channel that states the class must state it vacuously. One channel
    with a grid of 16 makes the class reachable, whatever the others declare,
    and qce2025-r26 is that descriptor: four generators on 16 samples and
    eleven on 1.
    """
    grids = collections.defaultdict(list)
    for ch in descriptor.get("channels", []):
        for c in ch.get("constraints", []):
            if c.get("shape") == "grid_samples":
                grids[c["id"]].append(c.get("grid"))
    return {cls: "every channel states a grid of 1 sample, so every sample count is on it"
            for cls, seen in grids.items() if all(g == 1 for g in seen)}


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
    order = ("agree", "vendor_lenient", "conservative", "unobserved", "over_predicted",
             "missed_repair", "harness", "open", "unsound")
    unknown = set(by_disp) - set(order)
    if unknown:
        raise SystemExit(f"dispositions with no place in the summary: {sorted(unknown)}")
    for disp in order:
        n = len(by_disp.get(disp, []))
        if n:
            print(f"  {disp:16} {n}")

    print()
    unsound = by_disp.get("unsound", [])
    print(f"UNSOUND PASSES: {len(unsound)}   <- the gate")
    for r in unsound:
        print(f"  {r['case']}: {r['_why']}")

    for disp in ("conservative", "over_predicted", "missed_repair", "open"):
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
    vacuous = vacuous_classes(descriptor)
    seen = sorted(set(fires) | set(holds))
    covered = 0
    coverable = 0
    for cls in seen:
        if cls in vacuous and fires[cls] == 0:
            print(f"  {cls:24} {fires[cls]:6} {holds[cls]:6}  vacuous")
            continue
        if cls in vacuous:
            # A class called vacuous that fired means this file's reading of
            # the descriptor is wrong, not that the corpus found something.
            raise SystemExit(f"triage: {cls} is called vacuous and fired "
                             f"{fires[cls]} times; vacuous_classes is wrong")
        coverable += 1
        ok = fires[cls] > 0 and holds[cls] > 0
        covered += 1 if ok else 0
        print(f"  {cls:24} {fires[cls]:6} {holds[cls]:6}  {'yes' if ok else 'no'}")
    print(f"  covered on both sides: {covered} of {coverable} exercised classes")
    for cls, why in sorted(vacuous.items()):
        if cls in seen:
            print(f"  vacuous: {cls}, {why}")


if __name__ == "__main__":
    main()
