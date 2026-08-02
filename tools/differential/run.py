"""Run the corpus against both qconform and asm_v2, and record both answers.

One JSONL row per program: the checker's verdict and the rules it fired, the
vendor's outcome, and what the lowering lost. Rows carry no timestamps and are
written with sorted keys, so a rerun over the same corpus is byte-identical.

The vendor outcome uses the survey's four names. compiled splits into accept
and accept_round by reading back every lowered pulse and comparing it to what
was asked for. tools/survey/runner.py cannot do that split here, because it
compares one probed parameter and a generated program has many elements. See
notes/oracle-path.txt.

Usage:
  python tools/differential/run.py <corpus-dir> <descriptor.json> <config.json> <out.jsonl>
"""

import argparse
import json
import logging
import subprocess
import sys
import warnings
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

warnings.filterwarnings("ignore")
logging.getLogger().setLevel(logging.ERROR)

from qick.qick_asm import QickConfig          # noqa: E402

from lower import (build_plan, compile_plan, LoweringError,   # noqa: E402
                   round_half_even)

QCONFORM = ROOT / "qconform"
VERDICT_BY_EXIT = {0: "pass", 1: "fail", 2: "pass_with_repairs", 3: "tool_error"}


def run_qconform(descriptor, program_path):
    r = subprocess.run([str(QCONFORM), str(descriptor), str(program_path)],
                       capture_output=True, timeout=120)
    verdict = VERDICT_BY_EXIT.get(r.returncode, f"exit_{r.returncode}")
    report = None
    if r.stdout:
        report = json.loads(r.stdout)
    return verdict, report, r.stderr.decode()[:300]


# How to turn a vendor readback into the same units the request was made in.
# get_pulse_param returns microseconds, megahertz and degrees.
TO_REQUEST_UNITS = {
    "total_length": lambda v: Fraction(v) / 1_000_000,   # us  -> seconds
    "freq": lambda v: Fraction(v) * 1_000_000,           # MHz -> Hz
    "phase": lambda v: Fraction(v) / 360,                # deg -> turns
    "gain": lambda v: Fraction(v),                       # full-scale fraction
}

# Rules whose repair changes something this readback cannot see. The start
# time of a pulse lives in the instruction stream, not on the pulse, so a
# schedule_grid repair is invisible here. Saying the vendor did not repair
# would be a claim the instrument cannot support.
UNOBSERVABLE_RULES = {"schedule_grid"}


def readback_outcome(prog, plan):
    """Split compiled into accept or accept_round.

    Every quantity the vendor exposes is compared against what was asked for,
    not just the length. A repair to frequency, phase or gain is as much a
    silent repair as one to duration, and the checker predicts all of them.

    The comparison asks two things per quantity. Was the request already a
    whole multiple of the step the vendor quantizes to, and did the readback
    land on the same multiple. Comparing the rounded request against the
    readback alone would be tautological, because rounding the request is
    exactly what the vendor does.
    """
    changed = []
    for ch, name, kwargs in plan.pulses:
        for quantity, (requested, step) in plan.pulse_grid.get(name, {}).items():
            try:
                raw = float(prog.get_pulse_param(name, quantity))
            except (KeyError, ValueError, TypeError):
                continue
            got = TO_REQUEST_UNITS[quantity](raw)
            if step and step > 0:
                want_steps = requested / step
                got_steps = got / step
                off_grid = want_steps.denominator != 1
                moved = round_half_even(want_steps) != round_half_even(got_steps)
                same = not (off_grid or moved)
            else:
                same = requested == got
            if not same:
                changed.append({
                    "pulse": name,
                    "quantity": quantity,
                    "requested": str(requested),
                    "readback": str(got),
                })
    return ("accept_round" if changed else "accept"), changed


def raw_registers(prog, plan):
    """The register values the vendor will actually emit.

    get_pulse_param reports the value that was asked for. The register is what
    the hardware sees, and the two differ exactly where the vendor accepts
    something it cannot represent: a frequency past the DDS range wraps, and a
    gain past full scale stays out of range. tools/survey/runner.py reads the
    same registers for the same reason.
    """
    out = {}
    for ch, name, kwargs in plan.pulses:
        waves = prog.pulses[name].waveforms if name in prog.pulses else []
        if not waves:
            continue
        w = waves[0]
        out[name] = {k: int(w[k]) for k in ("freq", "phase", "gain", "length")
                     if k in w and not hasattr(w[k], "spans")}
    return out


def fired_rules(report):
    if not report:
        return []
    return sorted({r["rule"] for r in report.get("rejections", [])})


def checked_classes(report):
    if not report:
        return []
    return sorted(c["class"] for c in report.get("coverage", [])
                  if c["status"] == "checked")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("descriptor")
    ap.add_argument("config")
    ap.add_argument("out")
    args = ap.parse_args()

    corpus = Path(args.corpus)
    index = json.loads((corpus / "index.json").read_text())
    descriptor = json.loads(Path(args.descriptor).read_text())
    soccfg = QickConfig(str(args.config))

    rows = []
    for case in index["cases"]:
        program_path = corpus / case["program"]
        program = json.loads(program_path.read_text())

        verdict, report, stderr = run_qconform(args.descriptor, program_path)

        row = {
            "case": case["name"],
            "qconform_verdict": verdict,
            "qconform_rules": fired_rules(report),
            "qconform_checked": checked_classes(report),
        }
        if verdict == "tool_error":
            row["qconform_stderr"] = stderr.strip()

        try:
            plan = build_plan(program, descriptor, soccfg)
        except LoweringError as e:
            # The harness cannot ask the question. Not agreement, not
            # disagreement, and it must not be counted as either.
            row["vendor_outcome"] = "not_lowerable"
            row["vendor_detail"] = {"error_msg": str(e)[:300]}
            row["lowering_lost_cells"] = []
            rows.append(row)
            continue

        prog, outcome, detail = compile_plan(plan, soccfg)
        if outcome == "compiled":
            outcome, changed = readback_outcome(prog, plan)
            if changed:
                row["vendor_changed"] = changed
            row["vendor_registers"] = raw_registers(prog, plan)
        elif detail:
            row["vendor_detail"] = detail

        row["vendor_outcome"] = outcome
        row["lowering_lost_cells"] = plan.lost_cells()
        rows.append(row)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")

    counts = {}
    for r in rows:
        key = (r["qconform_verdict"], r["vendor_outcome"])
        counts[key] = counts.get(key, 0) + 1
    print(f"{len(rows)} rows written to {out}")
    for (v, o), n in sorted(counts.items()):
        print(f"  {v:18} x {o:16} {n}")


if __name__ == "__main__":
    main()
