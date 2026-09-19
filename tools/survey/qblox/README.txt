Qblox constraint survey
=======================

Black-box probe harness for the Qblox toolchain, run with no hardware. Each
probe is a qblox-scheduler Schedule. The runner compiles it and prepares it
on a dummy cluster, which applies the qcodes validators and runs the q1asm
assembler. Output is JSONL catalog rows in the same format as the QICK
survey.

The oracle is the pair of stages. The assembler alone checks only
instruction field widths offline: it accepts a 1 ns play, a waveform value
of 1.5 and 20000 samples. qblox-scheduler checks the documented limits.

Run
---
From the repository root:

  uv venv ~/.venvs/qconform-qblox --python 3.12
  uv pip install --python ~/.venvs/qconform-qblox/bin/python \
      -r tools/survey/qblox/requirements.txt
  ~/.venvs/qconform-qblox/bin/python tools/survey/qblox/runner.py \
      tools/survey/configs/qblox-qcm-qrm.json [outdir]

outdir defaults to tools/survey/catalog/. A rerun under the same pins is
byte-identical.

Row fields
----------
  stage     construct (operation argument check), compile (qblox-scheduler)
            or prepare (validators and assembler). For a refusal, the step
            that refused. For an accept, prepare.
  observed  events: the compiled body as "<t ns>:<instruction>", read by
            tools/oracle/qblox/toolchain.py timeline(). readback: the probed
            value in the probe's units.

What cannot be read back offline
--------------------------------
The initial NCO frequency is a sequencer setting. The dummy stores it as the
float it was given, so its 0.25 Hz quantization is not observable. A
SetClockFrequency is an instruction, and its quantization is observable.

A waveform's shape is uploaded as floats and quantized by the instrument.
Its scale is a gain register, which is observable.

Files
-----
  probes.py    probe definitions
  runner.py    compile, prepare, read back, classify, write rows
  ../configs/qblox-qcm-qrm.json   a hardware compilation config: one QCM
               (q0:mw) and one QRM (q0:res)
