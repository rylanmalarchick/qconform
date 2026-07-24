qconform
========

A vendor-neutral conformance checker for pulse-level quantum control.

Given a pulse program and a device's declared capabilities, it decides whether
the program is realizable on that device. Design-time and deterministic; no
runtime, and no hardware ownership.

Build
-----

  make

C99, no dependencies. Any of GCC, Clang, or ICC will do; 128-bit integers are
required, so MSVC needs clang-cl.

Run
---

  ./qconform <descriptor.json> <program.json>

The report goes to stdout as JSON. Exit codes:

  0  pass
  1  fail                (at least one fatal rejection)
  2  pass_with_repairs   (rejections, all vendor_repairable)
  3  tool error          (usage, io, malformed input, invalid descriptor)

A program the device cannot realize is a normal result, reported as data.
Only exit 3 means the tool could not answer.

Test
----

  make check      unit tests, the golden corpus, and the invariant tripwires
  make sanitize   the same, built with UBSan and ASan

The golden corpus is the contract, not a property of this implementation:
tests/golden/manifest.tsv lists each case with its expected exit code and
report, and tests/golden/run.sh will run it against any qconform binary.

  ./tests/golden/run.sh /path/to/some/other/qconform

Layout
------

  src/                 the checker
  documentation/       format specifications and JSON Schemas
  tests/golden/        differential corpus (inputs + frozen expected reports)
  tests/difftest.py    compares two implementations over corpus + mutations
  tools/               descriptor authoring, survey, and export (Python)
