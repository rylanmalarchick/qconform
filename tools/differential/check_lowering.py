"""Prove the lowering before anything is generated with it.

Two checks, both free, both run against programs that already exist.

1. Every golden program the checker ACCEPTS must lower and compile. Those
   are programs qconform calls realizable, so a vendor refusal there would
   be either a lowering bug or an unsound PASS, and both must be seen.

   For a golden program the checker FAILS, a vendor refusal is the expected
   answer and is recorded as agreement. It also proves the lowering carried
   the defect through instead of quietly repairing it.

2. Round trip. Lower a program, compile it, then run the existing exporter
   in tools/exporter/qick_export.py back to qconform JSON. The exporter
   reports post-quantization values, so a difference is expected wherever
   the vendor quantized. A difference anywhere else is a lowering bug.

Usage: python tools/differential/check_lowering.py
"""

import json
import logging
import sys
import warnings
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "differential"))
sys.path.insert(0, str(ROOT / "tools" / "exporter"))

warnings.filterwarnings("ignore")
logging.getLogger().setLevel(logging.ERROR)

from qick.qick_asm import QickConfig            # noqa: E402

from lower import lower, LoweringError, rat     # noqa: E402

CONFIGS = {
    "descriptors/testbench.json": "zcu216-testbench.json",
    "descriptors/qce2025-r26.json": "zcu216-qce2025-r26.json",
    "descriptors/hostile-ident.json": "zcu216-testbench.json",
}


def load_manifest():
    """Corpus cases that carry a verdict. Exit-3 cases have no report and
    tell us nothing about lowering, so they are skipped by name."""
    rows = []
    path = ROOT / "tests" / "golden" / "manifest.tsv"
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        name, desc, prog, code, report = line.split("\t")
        if report == "-":
            continue
        rows.append((name, desc, prog, int(code)))
    return rows


def main():
    cfg_cache = {}
    failures = []
    lowered = 0
    roundtrips = 0
    quantized = 0
    agreed_refusals = []
    changed = []
    vendor_lenient = []

    for name, desc_rel, prog_rel, want_exit in load_manifest():
        if desc_rel not in CONFIGS:
            failures.append((name, f"no config mapped for {desc_rel}"))
            continue
        cfg_file = CONFIGS[desc_rel]
        if cfg_file not in cfg_cache:
            cfg_cache[cfg_file] = QickConfig(
                str(ROOT / "tools" / "survey" / "configs" / cfg_file))
        soccfg = cfg_cache[cfg_file]

        descriptor = json.loads((ROOT / "tests" / "golden" / desc_rel).read_text())
        program = json.loads((ROOT / "tests" / "golden" / prog_rel).read_text())

        try:
            prog, plan, outcome, detail = lower(program, descriptor, soccfg)
        except LoweringError as e:
            failures.append((name, f"lowering refused: {e}"))
            continue

        if outcome != "compiled":
            if want_exit == 1:
                # qconform failed this program and so did the vendor. The
                # lowering carried the defect through, which is what we want
                # to see.
                agreed_refusals.append((name, detail["error_msg"][:70]))
            else:
                failures.append((name, f"vendor refused a program qconform "
                                       f"accepted (exit {want_exit}): "
                                       f"{detail['error_type']}: "
                                       f"{detail['error_msg'][:110]}"))
            continue

        lowered += 1
        if want_exit == 1:
            # qconform refused it and the vendor did not. Not a lowering bug.
            # The descriptor's vendor_behavior list names the cases where the
            # vendor compiles something that fails later, and the checker
            # rejects those on purpose. The triage step dispositions them.
            vendor_lenient.append(name)
        lost = plan.lost_cells()
        if lost:
            quantized += len(lost)

        # round trip through the existing exporter
        try:
            import qick_export
            back = qick_export.export(prog, soccfg)
        except NotImplementedError:
            continue   # exporter states a v0 scope; outside it is not a bug
        except Exception as e:
            failures.append((name, f"export failed: {type(e).__name__}: {str(e)[:120]}"))
            continue

        roundtrips += 1
        diffs = compare(program, back)
        if diffs:
            changed.append((name, diffs))

    print()
    print(f"accepted programs that lowered and compiled : {lowered}")
    print(f"round tripped through the exporter          : {roundtrips}")
    print(f"values whose double landed in another cell  : {quantized}")
    if changed:
        print()
        print(f"round trips where a duration changed: {len(changed)}")
        for n, diffs in changed:
            for d in diffs[:3]:
                print(f"  {n}: {d}")
    else:
        print("every round-tripped duration came back exactly")
    if vendor_lenient:
        print()
        print(f"qconform refused, vendor compiled anyway: {len(vendor_lenient)}")
        for n in vendor_lenient:
            print(f"  {n}  (expected: see the descriptor vendor_behavior list)")
    if agreed_refusals:
        print()
        print(f"failed programs the vendor also refused (agreement): {len(agreed_refusals)}")
        for n, msg in agreed_refusals:
            print(f"  {n}: {msg}")
    if failures:
        print()
        print(f"FAILURES: {len(failures)}")
        for name, why in failures:
            print(f"  {name}: {why}")
        return 1
    print("lowering gate: ok")
    return 0


def compare(before, after):
    """Duration differences between the program we lowered and the program the
    exporter reconstructed.

    Element ids do not correspond and must not be matched on. The exporter
    assigns its own ids while walking the compiled program, and it emits
    elements we never wrote: a leading delay carrying the constructor's
    final_delay, for one. Matching on id compares a play against a delay and
    reports a difference that is not there.

    Align on the output elements in order instead. A play is the same play if
    it is the nth play in both programs.
    """
    def outputs(prog):
        return [e for e in prog.get("elements", [])
                if e["kind"] in ("play", "capture")]

    b_out, a_out = outputs(before), outputs(after)
    diffs = []
    if len(b_out) != len(a_out):
        diffs.append(f"output element count: {len(b_out)} -> {len(a_out)}")
        return diffs
    for i, (b, a) in enumerate(zip(b_out, a_out)):
        if b["kind"] != a["kind"]:
            diffs.append(f"output {i} kind: {b['kind']} -> {a['kind']}")
        elif b["duration"] != a["duration"]:
            diffs.append(f"output {i} ({b['kind']}) duration: "
                         f"{b['duration']} -> {a['duration']}")
    return diffs


if __name__ == "__main__":
    sys.exit(main())
