differential
============

Phase 5 evidence: qconform's verdict against the QICK asm_v2 toolchain, over
a generated corpus, on every distinct channel class a descriptor declares.

This is not tests/difftest.py. That one compares two qconform implementations
against each other. This one compares qconform against the vendor.

Run it
------
  make differential

That needs the survey environment, because it drives the vendor toolchain:

  uv venv ~/.venvs/qconform-survey --python 3.12
  uv pip install --python ~/.venvs/qconform-survey/bin/python \
      -r tools/survey/requirements.txt

There is no hardware in this loop and there must not be. asm_v2 compiles from
a captured board configuration in tools/survey/configs/.

What each part does
-------------------
  corpus.py          Generates programs from a descriptor. Boundary ladders
                     walk each declared limit; randomized programs compose
                     several elements with values near the limits. One
                     representative per channel class, because a corpus that
                     probes one generator cannot reach the rules the others
                     carry. Seeded, and byte-identical on a rerun.
  lower.py           Turns a qconform program into vendor calls. Pure: the
                     plan is computed before any vendor object exists, so the
                     translation can be inspected on its own. Refuses what it
                     cannot express rather than guessing.
  run.py             Records both answers per program, plus the raw registers.
                     get_pulse_param reports what was asked for; the register
                     is what the hardware sees, and the two differ exactly
                     where the vendor accepts what it cannot represent.
  triage.py          Dispositions every row and prints the gate.
  check_lowering.py  Pre-flight. Every golden program the checker accepts must
                     lower and compile. Run it before trusting a result.

Reading the result
------------------
The two directions are not equally serious.

  qconform accepts, vendor refuses    UNSOUND. The gate forbids it.
  qconform refuses, vendor compiles   a false alarm, or a documented vendor
                                      behavior, which is vendor_lenient
  pass_with_repairs, vendor repairs    agreement

An open row is unfinished work and not a result.

The oracle is a function of the qick version. A different release is a
different result, which is why the pin is exact.
