differential
============

Phase 5 evidence: qconform's verdict against a vendor toolchain, over a
generated corpus, on every distinct channel class a descriptor declares.

The vendor side is an oracle backend in tools/oracle/. The descriptor names
its library in identification.library.name, and that picks the backend. Today
the one backend is QICK asm_v2 (tools/oracle/qick/).

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
  run.py             Records both answers per program, plus the raw registers.
                     It asks the oracle to plan, compile and observe each
                     program. It imports no vendor package.
  triage.py          Dispositions every row and prints the gate.

In tools/oracle/, per backend:

  lower.py           Turns a qconform program into vendor calls. Pure: the
                     plan is computed before any vendor object exists, so the
                     translation can be inspected on its own. Refuses what it
                     cannot express rather than guessing.
  observe.py         Reads back what the vendor did. For QICK, get_pulse_param
                     reports what was asked for. The register is what the
                     hardware sees, and the two differ exactly where the
                     vendor accepts what it cannot represent.
  preflight.py       Every golden program the checker accepts must lower and
                     compile. make differential runs it first.

Reading the result
------------------
The two directions are not equally serious.

  qconform accepts, vendor refuses    UNSOUND. The gate forbids it.
  qconform refuses, vendor compiles   a false alarm, or a documented vendor
                                      behavior, which is vendor_lenient
  pass_with_repairs, vendor repairs    agreement

An open row is unfinished work and not a result.

The oracle is a function of the vendor library version. A different release
is a different result, which is why the pin is exact.
