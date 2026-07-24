"""Faithfulness check for reconstructed configs.

Loads each reconstructed JSON through qick's QickConfig and diffs its
printed description against the stored published dump. The comparison
ends at the "QICK box daughter cards" marker if present (that section is
printed by the RF-board runtime, not by QickConfig, so a JSON round-trip
cannot and need not reproduce it).

Exit 0 = both match; nonzero = diff printed.
"""

import difflib
import sys
from pathlib import Path

from qick.qick_asm import QickConfig

HERE = Path(__file__).parent
CONFIGS = HERE / "configs"

PAIRS = [
    ("zcu216-rb-r27.json", "zcu216-rb-r27.dump.txt"),
    ("zcu216-qce2025-r26.json", "zcu216-qce2025-r26.dump.txt"),
]

CUTOFF = "QICK box daughter cards"


def normalize(text):
    lines = []
    for line in text.splitlines():
        if line.startswith(CUTOFF):
            break
        lines.append(line.rstrip())
    while lines and not lines[-1]:
        lines.pop()
    return lines


def main():
    failed = False
    for json_name, dump_name in PAIRS:
        got = normalize(str(QickConfig(str(CONFIGS / json_name))))
        want = normalize((CONFIGS / dump_name).read_text())
        if got == want:
            print(f"OK   {json_name} reproduces {dump_name}")
        else:
            failed = True
            print(f"FAIL {json_name} vs {dump_name}:")
            sys.stdout.writelines(difflib.unified_diff(
                want, got, fromfile=dump_name, tofile=json_name, lineterm=""))
            print()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
