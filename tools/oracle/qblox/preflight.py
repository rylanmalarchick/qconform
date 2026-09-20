"""Prove the Qblox lowering before anything is generated with it.

Every golden program on a Qblox descriptor is lowered and run through the
toolchain. A program the checker accepts must compile: a refusal there is
either a lowering bug or an unsound pass, and both must be seen. For a
program the checker fails, a refusal is the expected answer and is recorded
as agreement.

Usage: python tools/oracle/qblox/preflight.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))

import oracle                                      # noqa: E402
from oracle.base import LoweringError              # noqa: E402

CONFIG = ROOT / "tools/survey/configs/qblox-qcm-qrm.json"


def cases():
    """Golden cases that carry a verdict, on a Qblox descriptor."""
    rows = []
    for line in (ROOT / "tests/golden/manifest.tsv").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        name, desc, prog, code, report = line.split("\t")
        if report == "-":
            continue
        doc = json.loads((ROOT / "tests/golden" / desc).read_text())
        if doc["identification"]["library"]["name"] != "qblox":
            continue
        rows.append((name, doc, ROOT / "tests/golden" / prog, int(code)))
    return rows


def main():
    vendor = oracle.load(json.loads((ROOT / "tests/golden/descriptors/qblox-qcm-qrm.json").read_text()),
                         CONFIG)
    failures, compiled, agreed, lenient, unexpressible = [], 0, [], [], []

    for name, descriptor, prog_path, want_exit in cases():
        program = json.loads(prog_path.read_text())
        try:
            plan = vendor.plan(program, descriptor)
        except LoweringError as e:
            if want_exit == 1:
                # The checker refuses this program and the vendor API cannot
                # express it either. That is not a lowering bug, and no
                # verdict is hidden: the checker already says no.
                unexpressible.append((name, str(e)[:70]))
            else:
                failures.append((name, f"lowering refused a program qconform accepted "
                                       f"(exit {want_exit}): {e}"))
            continue
        result = vendor.compile(plan)
        if result.outcome != "compiled":
            if want_exit == 1:
                agreed.append((name, result.detail["error_msg"][:70]))
            else:
                failures.append((name, f"vendor refused a program qconform accepted "
                                       f"(exit {want_exit}) at {result.detail['stage']}: "
                                       f"{result.detail['error_msg'][:110]}"))
            continue
        compiled += 1
        if want_exit == 1:
            lenient.append(name)
        changed, _ = vendor.observe(result, plan)
        if want_exit == 2 and not changed:
            failures.append((name, "qconform predicted a repair the toolchain did not make"))
        if want_exit == 0 and changed:
            failures.append((name, f"the toolchain changed {[c['quantity'] for c in changed]} "
                                   f"in a program qconform passed clean"))

    print()
    print(f"accepted programs that lowered and compiled : {compiled}")
    if lenient:
        print()
        print(f"qconform refused, vendor compiled anyway: {len(lenient)}")
        for n in lenient:
            print(f"  {n}")
    if unexpressible:
        print()
        print(f"refused programs the vendor API cannot express: {len(unexpressible)}")
        for n, why in unexpressible:
            print(f"  {n}: {why}")
    if agreed:
        print()
        print(f"failed programs the vendor also refused (agreement): {len(agreed)}")
        for n, why in agreed:
            print(f"  {n}: {why}")
    if failures:
        print()
        print(f"FAILURES: {len(failures)}")
        for name, why in failures:
            print(f"  {name}: {why}")
        return 1
    print("lowering gate: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
