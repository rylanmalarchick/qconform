saturation
==========

Reruns the differential over many corpus seeds and records, seed by seed,
whether the growing corpus finds anything new.

Run it
------
Needs the survey environment (see tools/README.txt).

  python tools/saturation/run.py <out-dir> --seeds 1-10 [--jobs N]

It writes <out-dir>/<config>.tsv with one cumulative line per seed, and
<out-dir>/<config>-failing.txt with every unsound, missed_repair or open
program found at any seed. Seed 1 must reproduce
tools/differential/results/ byte for byte, and a program produced by two
seeds must give the same row, or the run stops.

Fault injection survivors re-run at other seeds with
tools/mutation/run_mutants.py --seed S --only-file IDS.

Recorded result
---------------
results/ holds the tsv and failing files from the recorded run, and the
survivor re-probe summary.
