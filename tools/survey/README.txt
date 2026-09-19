survey
======

The empirical constraint survey. Each vendor has a black-box probe harness
that feeds programs to the vendor toolchain with no hardware and records what
the toolchain accepts, rejects, or silently repairs.

  qick/       the QICK asm_v2 survey. See qick/README.txt.
  configs/    captured and reconstructed board configs, with provenance
  catalog/    the survey output, one JSONL file per (config, axis). Every
              descriptor constraint cites a row here, and
              tools/descriptor/check_descriptor.py resolves the citation.

A catalog file is named <config>__<axis>.jsonl. The config name says which
board, and so which vendor, the rows came from.
