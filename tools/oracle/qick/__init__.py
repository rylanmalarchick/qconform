"""QICK asm_v2 as an oracle.

asm_v2 compiles from a captured board config with no hardware. Constructing
the program object compiles it, so the vendor's checks fire at that point.

  lower.py      qconform program to a Plan of asm_v2 calls, and the compile
  observe.py    what the vendor did to a compiled program
  preflight.py  every golden program the checker accepts lowers and compiles
"""

import json
from fractions import Fraction
from pathlib import Path

from oracle import base

# Which documented vendor behavior explains a rule firing while the vendor
# still compiles. The descriptor lists the behaviors and the rules separately
# and does not link them, so the link is stated here.
#
# In each of these the vendor accepts the program and emits a register it
# cannot honor. A frequency past the DDS range wraps, a gain past full scale
# stays out of range, and an envelope past memory overruns at board load. The
# consequence is at run time, so compile-time acceptance is not disagreement.
# One rule can have different documented behavior on different channel classes,
# so each maps to a set and a row is vendor_lenient when the descriptor
# declares any of them. A v6 aliases an out-of-band frequency; a mux channel
# folds the tone onto its Nyquist image instead.
RULE_TO_BEHAVIOR = {
    "frequency_range": {"frequency_alias_mod_f_dds",
                        "mux_tone_nyquist_image_fold"},
    "amplitude_range": {"gain_over_full_scale"},
    "envelope_memory": {"envelope_memory_overflow_unchecked"},
    "mux_tone_count": {"mux_tone_count_unchecked"},
}


class Oracle(base.Oracle):
    name = "qick"

    def __init__(self, config_path):
        self.config_path = Path(config_path)
        self.config = json.loads(self.config_path.read_text())
        self._soccfg = None

    @property
    def soccfg(self):
        if self._soccfg is None:
            from qick.qick_asm import QickConfig
            self._soccfg = QickConfig(str(self.config_path))
        return self._soccfg

    def channel_defaults(self, channel):
        """A generator with a digital mixer: the mixer the config runs it at.

        A channel whose frequency_range is post_mixer must declare its mixer.
        The checker refuses the program otherwise, because it cannot know the
        band the device sees. The value comes from the config so the program
        describes the same device the oracle compiles for.
        """
        if channel["kind"] != "drive":
            return {}
        g = self.config["gens"][int(channel["name"][3:])]
        if not g.get("has_mixer"):
            return {}
        return {"_mixer_hz": Fraction(str(g["f_dds"])) * 1_000_000 / 4}

    def plan(self, program, descriptor):
        from oracle.qick.lower import build_plan
        return build_plan(program, descriptor, self.soccfg)

    def compile(self, plan):
        from oracle.qick.lower import compile_plan
        prog, outcome, detail = compile_plan(plan, self.soccfg)
        return base.Compiled(outcome, detail, prog)

    def observe(self, compiled, plan):
        from oracle.qick.observe import (readback_outcome, raw_registers,
                                         start_time_changes)
        prog = compiled.handle
        _, changed = readback_outcome(prog, plan)
        changed = changed + start_time_changes(prog, plan, self.soccfg)
        return changed, raw_registers(prog, plan)
