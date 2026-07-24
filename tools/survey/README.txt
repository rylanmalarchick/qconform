asm_v2 constraint survey
========================

Black-box probe harness for the QICK asm_v2 (tProc v2) toolchain, run
hardware-free against captured/reconstructed board configs. Output is a
JSONL catalog of (probe, outcome) rows: the empirical constraint surface
that seeds the qconform QICK descriptor, the rule candidates with
observed severities, and the differential-harness oracle half.

Run
---
  pip install -r requirements.txt   (versions pinned; the oracle is a
                                     function of the qick version)
  python reconstruct.py             rebuild the two reconstructed configs
  python reconstruct_check.py       prove them against the published dumps
  python runner.py configs/<name>.json [outdir]

Outcomes per probe: accept, accept_round (silent quantization/repair),
reject (typed vendor error), crash (unhelpful failure). Runs are
deterministic: no RNG, no timestamps; identical inputs give
byte-identical catalog files.

Files
-----
  probes.py             probe definitions, derived from config values
  runner.py             executor/classifier/JSONL writer
  reconstruct.py        text dump -> config JSON reconstruction
  reconstruct_check.py  faithfulness proof (dump round-trip)
  configs/              three ZCU216 tProc v2 configs + provenance
  catalog/              survey output, tracked

Triage summary: notes/survey-qick-2026-07.txt (local notes).
