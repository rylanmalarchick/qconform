tools
=====

Python that produced the artifacts in this repo, and utilities for working
with the formats. None of it is required to build, run, or test qconform.

  make            builds the checker
  make check      unit tests, the golden corpus, the invariant tripwires

Both need a C compiler and POSIX sh. Nothing here is on that path, and no
Python is imported by anything under src/. If you only want the checker, you
can ignore this directory entirely.

What each part is for
---------------------

  survey/       The empirical constraint survey: a black-box probe harness
                that feeds programs to the QICK asm_v2 toolchain and records
                what it accepts, rejects, or silently repairs. Output is the
                JSONL catalog in survey/catalog/. Every descriptor
                constraint must cite a row of it. Runs against the
                captured board configs in survey/configs/, so it needs the
                qick package but not a board.
                Needs: pip install -r survey/requirements.txt

  descriptor/   build_descriptor.py turns a captured vendor config plus the
                survey catalog into a capability descriptor.
                check_descriptor.py is the gate: every constraint must trace
                to a catalog row that resolves, or the descriptor does not
                ship.
                The descriptors it produces are in descriptor/descriptors/,
                and frozen copies are what tests/golden/ runs against.
                Needs: nothing beyond the standard library.

  exporter/     Lowers a compiled QICK asm_v2 program to the qconform program
                format. The worked Ramsey example in the golden corpus came
                from here.
                Needs: pip install qick numpy (see survey/requirements.txt)

  format/       validate.py checks any qconform JSON artifact against its
                schema plus the rules JSON Schema cannot express: no floats
                anywhere, integers within i64, canonical rationals, unique
                names, no dangling references. Useful if you are authoring a
                descriptor or an exporter.
                Needs: pip install -r format/requirements.txt

Version pinning
---------------
Vendor behavior is a function of the (firmware config, library version)
pair, so requirements.txt files pin exact versions. A survey re-run under a
different qick release is a different survey and produces a different
descriptor, by design. See documentation/descriptor-format-v0.txt.

A re-run tests the pin. Under qick 0.2.418 and numpy 2.5.1 the survey
rewrites all 45 files in survey/catalog/ byte-identically. That test is
how the numpy pin was corrected: it read 2.4.6 while every committed row
recorded 2.5.1.

Install the survey pins in their own environment rather than a shared one,
because the pin is exact:

  uv venv ~/.venvs/qconform-survey --python 3.12
  uv pip install --python ~/.venvs/qconform-survey/bin/python \
      -r tools/survey/requirements.txt
