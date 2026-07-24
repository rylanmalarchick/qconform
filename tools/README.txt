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
                JSONL catalog in survey/catalog/ — the evidence every
                descriptor constraint is required to cite. Runs against the
                captured board configs in survey/configs/, so it needs the
                qick package but not a board.
                Needs: pip install -r survey/requirements.txt

  descriptor/   build_descriptor.py turns a captured vendor config plus the
                survey catalog into a capability descriptor.
                check_descriptor.py is the gate: every constraint must trace
                to a catalog row that resolves, or the descriptor does not
                ship. This is what makes a descriptor an empirical artifact
                rather than transcribed documentation.
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
Vendor behaviour is a function of the (firmware config, library version)
pair, so requirements.txt files pin exact versions. A survey re-run under a
different qick release is a different survey and produces a different
descriptor, by design — see documentation/descriptor-format-v0.txt.
