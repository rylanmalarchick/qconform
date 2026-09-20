"""Qblox as an oracle: qblox-scheduler compiles the schedule, and the q1asm
assembler behind a dummy cluster assembles it.

  toolchain.py  the two stages, and reading a compiled program's timeline
  lower.py      qconform program to a Schedule
  observe.py    what the toolchain changed
  preflight.py  every golden program the checker accepts lowers and compiles

The config is the scheduler's hardware compilation config. A descriptor
channel names its hardware path in vendor_type, and the config's
connectivity graph maps that path to a port.
"""

import json
from pathlib import Path

from oracle import base

# Qblox refuses what it cannot honor, so no rule fires while the toolchain
# still compiles. The behaviors the descriptor documents (a dropped clock
# phase, the initial NCO frequency quantized on the instrument) are not
# rules, and observe() reports what it cannot see instead.
RULE_TO_BEHAVIOR = {}


def merged_phase(plan):
    """Does any frame take more than one phase update at one time."""
    for entries in plan.expect.values():
        at = {}
        for e in entries:
            if e["kind"] == "phase":
                at[e["t"]] = at.get(e["t"], 0) + 1
        if any(n > 1 for n in at.values()):
            return True
    return False


class Oracle(base.Oracle):
    name = "qblox"

    def __init__(self, config_path):
        self.config_path = Path(config_path)
        self.config = json.loads(self.config_path.read_text())
        self._toolchain = None

    @property
    def toolchain(self):
        if self._toolchain is None:
            from oracle.qblox.toolchain import Toolchain
            self._toolchain = Toolchain(self.config)
        return self._toolchain

    def channel_defaults(self, channel):
        """Nothing: a Qblox sequencer carries no mixer the program must
        declare. The NCO is the frame's own frequency."""
        return {}

    def plan(self, program, descriptor):
        from oracle.qblox.lower import build_plan
        return build_plan(program, descriptor, self.config)

    def compile(self, plan):
        from oracle.base import LoweringError
        from oracle.qblox.lower import build_schedule
        from oracle.qblox.toolchain import error
        try:
            schedule = build_schedule(plan)
        except LoweringError:
            raise
        except (ValueError, RuntimeError) as e:
            # building an operation is the vendor's first check: pydantic
            # validates the arguments before any schedule exists
            return base.Compiled("reject", {**error(e), "stage": "construct"})
        except Exception as e:  # the vendor failed without meaning to
            return base.Compiled("crash", {**error(e), "stage": "construct"})
        result = self.toolchain.run(schedule)
        detail = dict(result.detail, stage=result.stage) if result.detail else None
        return base.Compiled(result.outcome, detail, result)

    def observe(self, compiled, plan):
        from oracle.qblox.observe import observe
        return observe(compiled.handle, plan)

    def unobservable(self, plan):
        """Quantities this oracle cannot read back for this program.

        freq: the instrument quantizes the initial NCO frequency, and offline
        the setting keeps the float it was given.

        phase: the toolchain merges the phase updates at one time into a
        single set_ph_delta, so only their sum reaches a register. A repair to
        one of them is gone before the register is written, and a sum that
        lands on the step hides every repair in it. observe.py compares the
        sum for the same reason.
        """
        out = ["freq"] if plan.unobservable else []
        if merged_phase(plan):
            out.append("phase")
        return sorted(out)
