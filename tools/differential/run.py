"""Run the corpus against both qconform and a vendor oracle, and record both
answers.

One JSONL row per program: the checker's verdict and the rules it fired, the
vendor's outcome, and what the lowering lost. Rows carry no timestamps and are
written with sorted keys, so a rerun over the same corpus is byte-identical.

The vendor outcome uses the survey's four names. compiled splits into accept
and accept_round by reading back every lowered value and comparing it to what
was asked for. The survey runner cannot do that split here, because it
compares one probed parameter and a generated program has many elements.

The oracle is the backend in tools/oracle/ that the oracle descriptor names
in identification.library.name.

Usage:
  python tools/differential/run.py <corpus-dir> <descriptor.json> <config.json> <out.jsonl>
      [--oracle-descriptor <descriptor.json>]

The checker reads <descriptor.json>. The lowering reads the oracle
descriptor, which defaults to the same file. Fault injection passes a mutant
as the first and the correct descriptor as the second, so a mutant changes
the checker's answer and never the question put to the vendor.
"""

import argparse
import json
import logging
import subprocess
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

warnings.filterwarnings("ignore")
logging.getLogger().setLevel(logging.ERROR)

import oracle                                  # noqa: E402
from oracle.base import LoweringError          # noqa: E402

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


def fired_rules(report):
    if not report:
        return []
    return sorted({r["rule"] for r in report.get("rejections", [])})


def fatal_elements(report):
    """Element ids a fatal rejection points at."""
    if not report:
        return []
    return sorted({r["element"] for r in report.get("rejections", [])
                   if r["severity"] == "fatal"})


def unused_frame_updates(program):
    """set_frequency and shift_phase elements that no later output uses.

    Elements are ordered per frame in file order, so an update reaches the
    vendor only through a later play or capture on the same frame. The
    checker checks the frame when it is set, and the vendor checks what a
    pulse uses, so a refusal on one of these is the checker being stricter,
    not wrong. Triage needs the ids to prove that per row.

    A set_frequency is also unused when another set_frequency on the same
    frame replaces it before any output. A shift_phase is not: shifts add up,
    so an earlier one still reaches the next output.
    """
    elements = program["elements"]
    out = []
    for i, e in enumerate(elements):
        if e["kind"] not in ("set_frequency", "shift_phase"):
            continue
        used = False
        for later in elements[i + 1:]:
            if later.get("frame") != e["frame"]:
                continue
            if later["kind"] in ("play", "capture"):
                used = True
                break
            if e["kind"] == "set_frequency" and later["kind"] == "set_frequency":
                break
        if not used:
            out.append(e["id"])
    return sorted(out)


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
    ap.add_argument("--oracle-descriptor", default=None)
    args = ap.parse_args()

    corpus = Path(args.corpus)
    index = json.loads((corpus / "index.json").read_text())
    descriptor = json.loads(Path(args.oracle_descriptor or args.descriptor).read_text())
    vendor = oracle.load(descriptor, args.config)

    rows = []
    for case in index["cases"]:
        program_path = corpus / case["program"]
        program = json.loads(program_path.read_text())

        verdict, report, stderr = run_qconform(args.descriptor, program_path)

        row = {
            "case": case["name"],
            "channels": sorted(ch["name"] for ch in program["channels"]),
            "qconform_verdict": verdict,
            "qconform_rules": fired_rules(report),
            "qconform_checked": checked_classes(report),
            "qconform_fatal_elements": fatal_elements(report),
            "unused_frame_updates": unused_frame_updates(program),
        }
        if verdict == "tool_error":
            row["qconform_stderr"] = stderr.strip()

        try:
            plan = vendor.plan(program, descriptor)
        except LoweringError as e:
            # The harness cannot ask the question. Not agreement, not
            # disagreement, and it must not be counted as either.
            row["vendor_outcome"] = "not_lowerable"
            row["vendor_detail"] = {"error_msg": str(e)[:300]}
            row["lowering_lost_cells"] = []
            rows.append(row)
            continue

        compiled = vendor.compile(plan)
        outcome = compiled.outcome
        if outcome == "compiled":
            changed, registers = vendor.observe(compiled, plan)
            outcome = "accept"
            if changed:
                outcome = "accept_round"
                row["vendor_changed"] = changed
            row["vendor_registers"] = registers
            # Quantities this oracle cannot read back for this program. A
            # predicted repair to one of them is neither confirmed nor
            # denied, so triage must not call it over-predicted.
            unobservable = vendor.unobservable(plan)
            if unobservable:
                row["vendor_unobservable"] = sorted(unobservable)
        elif compiled.detail:
            row["vendor_detail"] = compiled.detail

        row["vendor_outcome"] = outcome
        row["lowering_lost_cells"] = plan.lost_cells()
        if plan.barrier_roundings:
            row["barrier_roundings"] = plan.barrier_roundings
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
