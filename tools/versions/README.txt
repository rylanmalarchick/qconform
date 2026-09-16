versions
========

Reruns the survey and the differential under other qick releases, with the
descriptors still pinned to qick 0.2.418. Every difference against the
tracked catalog and results is a case the version pin exists for.

Run it
------
Each version needs its own environment, with numpy held at the survey pin
so qick is the only variable:

  v=0.2.367
  uv venv ~/.venvs/qconform-qick-$v --python 3.12
  uv pip install --python ~/.venvs/qconform-qick-$v/bin/python \
      qick==$v numpy==2.5.1

Then:

  tools/versions/study.sh <out-dir> 0.2.365 0.2.367 0.2.371 0.2.432

0.2.418 uses the survey environment. Running the study at 0.2.418 must
reproduce tools/survey/catalog/ and tools/differential/results/ byte for
byte. That run is the check that the driver adds nothing of its own.

What each part does
-------------------
  study.sh             Per version: the survey on all three configs, the
                       seed-1 differential corpus against the 0.2.418
                       descriptors, triage, and both comparisons below.
  compare_catalog.py   Joins two catalogs on the probe key, drops the
                       environment fields, and sorts each difference into
                       outcome, detail, added or removed. Refuses a
                       duplicate key.
  compare_results.py   Joins two result files on the case name and sorts
                       each vendor change into toolchain or harness. The
                       sort is a first pass. Read every harness row before
                       counting it.

Recorded result
---------------
results/<version>/ holds, per config, the triage output and the comparison
against 0.2.418, plus one catalog comparison per version. Result rows are
kept only where they differ from tools/differential/results/: 0.2.371 and
0.2.432 reproduce those files byte for byte, so they carry no copy. The full
catalogs are not tracked. study.sh regenerates them byte for byte.
