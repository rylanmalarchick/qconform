mutation
========

Fault injection for the differential. Plants one defect at a time in a
descriptor or in the checker, reruns the differential, and records whether
the harness catches it.

Run it
------
Needs the survey environment (see tools/README.txt) and a C compiler.

  python tools/mutation/run_mutants.py <out-dir> [--jobs N] [--only ID ...]

Output is <out-dir>/summary.jsonl, one row per (mutant, corpus mode), sorted
and byte-identical on a rerun. Row-level results go to <out-dir>/rows/.

What each part does
-------------------
  mutants.py       Writes single-change mutants of a descriptor: limits one
                   step off, resolutions and grids doubled or halved,
                   severities flipped, constraints deleted, capabilities and
                   budgets off by one, rounding modes flipped. Targets are the
                   channel class representatives the corpus probes, or with
                   --duplicates a non-representative channel of each class.
                   Also K2 and K3, two descriptor defects found earlier.
  patches/         K1 and K4, two checker defects found earlier, as patches
                   against src/check.c.
  run_mutants.py   Runs every mutant in two corpus modes. original: the
                   corpus comes from the correct descriptor. mutated: the
                   corpus comes from the mutant. The lowering always reads the
                   correct descriptor. Checker patches are built and run in a
                   git worktree.

Reading the result
------------------
  refused_by_checker  the checker's descriptor validation refused the mutant
  corpus_error        the corpus generator failed on the mutant
  detected            at least one unsound, missed_repair or open row
  survived            nothing flagged it

The unmutated descriptor of each config runs as a baseline in both modes. It
must survive with the tracked disposition counts. A survivor is not a defect
by itself. See results/dispositions.txt.
