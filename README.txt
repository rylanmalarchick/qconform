qconform
========

A vendor-neutral conformance checker for pulse-level quantum control.

Given a pulse program and a device's declared capabilities, it decides whether
the program is realizable on that device. The check is design-time and
deterministic. It needs no runtime and no device.

Build
-----

  make

C99, no dependencies. Any of GCC, Clang, or ICC will do. The code needs
128-bit integers, so MSVC needs clang-cl.

Run
---

  ./qconform <descriptor.json> <program.json>

The report goes to stdout as JSON. Exit codes:

  0  pass
  1  fail                (at least one fatal rejection)
  2  pass_with_repairs   (rejections, all vendor_repairable)
  3  tool error          (usage, io, malformed input, invalid descriptor)

Exit 0, 1, and 2 are answers about the device. Exit 3 means the tool
could not answer.

Test
----

  make check      unit tests, the golden corpus, and the invariant tripwires
  make sanitize   the same, built with UBSan and ASan

tests/golden/manifest.tsv lists each case with its expected exit code and
report. tests/golden/run.sh runs the corpus against any qconform binary,
so a second implementation can be measured against the same cases.

  ./tests/golden/run.sh /path/to/some/other/qconform

Layout
------

  src/                 the checker
  documentation/       format specifications, JSON Schemas, and the
                       port notes explaining the arithmetic helpers
  tests/unit/          unit tests
  tests/golden/        differential corpus (inputs + frozen expected reports)
  tests/tripwires.sh   invariant checks over src/ and the built binary
  tests/difftest.py    compares two implementations over corpus + mutations
  tools/               the Python that produced the artifacts here. See
                       tools/README.txt. Not needed to build or test.
