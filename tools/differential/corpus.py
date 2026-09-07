"""Generate the phase 5 corpus.

Two kinds of program, for two different jobs.

Boundary ladders. For each limit the descriptor declares, emit values far
below, just below, exactly at, just above and far above it. The just-below
and exactly-at rungs carry the weight, because the gate is about programs
qconform accepts and those are where an accept is most likely to be wrong.
These find a limit that is stated wrongly.

Randomized programs. The ladders probe one limit at a time and every program
they emit was designed, so they cannot find a combination nobody thought of.
The randomized programs compose several elements at once with values drawn
near the limits. These find what the ladders do not imply.

Both run for every distinct generator class the descriptor declares, not only
the first channel. A mux or an interpolated generator carries different
constraints from a v6.

Seeded. The seed chooses which rungs combine and in what order. The same seed
produces byte-identical output, so a result can be replayed. The per-generator
seed is derived with crc32 and not with hash(), which Python salts per process:
seeding from hash() drew a different program body on every run while leaving the
case names and the order stable, so the corpus looked reproducible and was not.

Every program is valid against documentation/schemas/program-v0.schema.json
and passes what parse.c enforces beyond it. A program the checker refuses as
malformed carries no verdict and would tell us nothing about soundness.

Usage:
  python tools/differential/corpus.py <descriptor.json> <outdir> [--seed N]
"""

import argparse
import json
from fractions import Fraction
from pathlib import Path
import random
import zlib


def rat(f):
    f = Fraction(f)
    return {"num": f.numerator, "den": f.denominator}


def ladder(limit, step, span=3):
    """Values around a limit, as (value, rung) pairs.

    step is the smallest meaningful increment, normally the grid. The rungs
    are named so the coverage report can say which part of a limit was
    exercised rather than only how many programs ran.
    """
    return [
        (limit - span * step, "far_below"),
        (limit - step, "just_below"),
        (limit, "at"),
        (limit + step, "just_above"),
        (limit + span * step, "far_above"),
    ]


class Descriptor:
    """The limits the generator needs, read once from the descriptor so the
    corpus follows the device rather than hard-coded numbers."""

    def __init__(self, path):
        self.raw = json.loads(Path(path).read_text())
        self.channels = {c["name"]: c for c in self.raw["channels"]}
        self.gens = [c for c in self.raw["channels"] if c["kind"] == "drive"]
        self.readouts = [c for c in self.raw["channels"] if c["kind"] == "readout"]

    def constraint(self, channel, rule):
        for c in self.channels[channel]["constraints"]:
            if c["id"] == rule:
                return c
        return None

    def unconstrained(self):
        return [c["name"] for c in self.raw["channels"] if not c["constraints"]]

    def classes(self, kind):
        """One representative channel per distinct behavior class.

        Channels of the same vendor type with the same grids, constraints and
        capabilities behave the same, so probing all sixteen generators on a
        board would cost time and buy nothing. Probing one of each kind is
        what finds a rule that only a mux or an interpolated generator can
        reach. tools/survey/probes.py groups the same way.
        """
        seen = {}
        for c in self.raw["channels"]:
            if c["kind"] != kind:
                continue
            key = (c["vendor_type"], c["duration_grid"], c["schedule_grid"],
                   tuple(sorted(x["id"] for x in c["constraints"])),
                   tuple(sorted(c.get("capabilities", {}))))
            seen.setdefault(key, c)
        return list(seen.values())


def base(channel, unit, sample_unit=None, mixer_hz=None, tones=None):
    ch = {"name": channel, "unit": rat(unit)}
    if sample_unit is not None:
        ch["sample_unit"] = rat(sample_unit)
    if mixer_hz is not None:
        ch["mixer_frequency"] = rat(mixer_hz)
    if tones is not None:
        ch["tones"] = [{"frequency": rat(f), "phase": rat(ph), "amplitude": rat(a)}
                       for f, ph, a in tones]
    return ch


class Builder:
    """Accumulates one program. Element ids are assigned in order, so they are
    unique by construction and parse.c cannot reject the result."""

    def __init__(self, channels, frames):
        self.channels = channels
        self.frames = frames
        self.waveforms = []
        self.elements = []
        self._next = 0

    def wf_const(self, name, amplitude):
        self.waveforms.append({"name": name, "kind": "const",
                               "amplitude": rat(amplitude)})
        return name

    def wf_samples(self, name, i, q, full_scale=32766):
        self.waveforms.append({"name": name, "kind": "samples",
                               "full_scale": full_scale, "i": i, "q": q})
        return name

    def add(self, **el):
        el["id"] = self._next
        self._next += 1
        self.elements.append(el)
        return el["id"]

    def program(self):
        return {
            "format": "qconform-program",
            "format_version": 0,
            "channels": self.channels,
            "frames": self.frames,
            "waveforms": self.waveforms,
            "elements": self.elements,
        }


def gen_frames(gen, ro=None):
    frames = [{"name": "f0", "channel": gen,
               "frequency": rat(0), "phase": rat(0)}]
    if ro is not None:
        frames.append({"name": "r0", "channel": ro,
                       "frequency": rat(0), "phase": rat(0)})
    return frames


def mux_cases(d, gen, rng, count):
    """The corpus for a muxed generator.

    A mux channel carries its frequency, phase and amplitude in a tone table
    and its pulses name tones with a mask, so the frame-based and
    waveform-based builders do not describe it. It gets its own ladders over
    the same limits, plus the two rules only it can reach.
    """
    name = gen["name"]
    unit = Fraction(gen["unit"]["num"], gen["unit"]["den"])
    dgrid, sgrid = gen["duration_grid"], gen["schedule_grid"]
    mixer = gen.get("_mixer_hz") or Fraction(0)
    n_tones = gen.get("capabilities", {}).get("n_tones")
    fc = d.constraint(name, "frequency_range")
    pc = d.constraint(name, "phase_resolution")
    ac = d.constraint(name, "amplitude_range")
    fres = Fraction(fc["resolution"]["num"], fc["resolution"]["den"]) if fc and "resolution" in fc else None
    pres = Fraction(pc["resolution"]["num"], pc["resolution"]["den"]) if pc and "resolution" in pc else None
    ares = Fraction(ac["resolution"]["num"], ac["resolution"]["den"]) if ac and "resolution" in ac else None
    out = []

    # A tone that sits on every limit. Every ladder below perturbs one field
    # of it, so a rung that fires names the one thing it changed.
    ok_tone = (mixer, Fraction(0), Fraction(1, 2))

    def prog(tones, mask, duration=None, lead=0):
        b = Builder([base(name, unit, mixer_hz=gen.get("_mixer_hz"), tones=tones)],
                    gen_frames(name))
        if lead:
            b.add(kind="delay", frame="f0", duration=lead)
        b.add(kind="play", frame="f0", mask=mask,
              duration=60 * dgrid if duration is None else duration)
        return b.program()

    lc = d.constraint(name, "pulse_length_range")
    if lc is not None:
        for limit, which in ((lc.get("min_units"), "min"), (lc.get("max_units"), "max")):
            if limit is None:
                continue
            for value, rung in ladder(limit, dgrid):
                if value < 0:
                    continue
                out.append((f"mux_pulse_length_range_{which}_{rung}",
                            prog([ok_tone], [0], duration=value)))
    for offset, rung in ((0, "on_grid"), (1, "one_unit_off"),
                         (dgrid // 2, "half_grid_off")):
        out.append((f"mux_pulse_length_grid_{rung}",
                    prog([ok_tone], [0], duration=60 * dgrid + offset)))
    for offset, rung in ((0, "on_grid"), (1, "one_unit_off"),
                         (sgrid // 2, "half_grid_off")):
        out.append((f"mux_schedule_grid_{rung}",
                    prog([ok_tone], [0], lead=sgrid * 4 + offset)))
    out.append(("mux_negative_duration", prog([ok_tone], [0], duration=-dgrid)))

    # tone frequency, against the band the device sees after the mixer
    if fc is not None:
        step = fres if fres else Fraction(1)
        for key, which in (("min", "min"), ("max", "max")):
            if key not in fc:
                continue
            limit = Fraction(fc[key]["num"], fc[key]["den"]) + mixer
            for value, rung in ladder(limit, step):
                out.append((f"mux_tone_frequency_{which}_{rung}",
                            prog([(value, Fraction(0), Fraction(1, 2))], [0])))
        if fres is not None:
            for mult, rung in ((100, "on_resolution"), (Fraction(1, 2), "half_step_off")):
                out.append((f"mux_tone_frequency_resolution_{rung}",
                            prog([(mixer + fres * mult, Fraction(0), Fraction(1, 2))], [0])))

    if pres is not None:
        for mult, rung in ((100, "on_resolution"), (Fraction(1, 2), "half_step_off")):
            out.append((f"mux_tone_phase_resolution_{rung}",
                        prog([(mixer, pres * mult, Fraction(1, 2))], [0])))

    if ac is not None:
        step = ares if ares else Fraction(1)
        for key, which in (("min", "min"), ("max", "max")):
            if key not in ac:
                continue
            limit = Fraction(ac[key]["num"], ac[key]["den"])
            for value, rung in ladder(limit, step):
                out.append((f"mux_tone_amplitude_{which}_{rung}",
                            prog([(mixer, Fraction(0), value)], [0])))
        if ares is not None:
            for mult, rung in ((100, "on_resolution"), (Fraction(1, 2), "half_step_off")):
                out.append((f"mux_tone_amplitude_resolution_{rung}",
                            prog([(mixer, Fraction(0), ares * mult)], [0])))

    # the two rules only a mux channel reaches
    if n_tones is not None:
        for k, rung in ((n_tones - 1, "under"), (n_tones, "at"),
                        (n_tones + 1, "over")):
            if k < 1:
                continue
            out.append((f"mux_tone_count_{rung}",
                        prog([ok_tone] * k, [0])))
        table = [ok_tone] * n_tones
        for mask, rung in (([0], "first"), ([n_tones - 1], "last"),
                           ([0, n_tones - 1], "two"), ([0, 0], "duplicate"),
                           ([n_tones], "at_count"), ([n_tones + 3], "past_count")):
            out.append((f"mux_tone_mask_{rung}", prog(table, mask, )))

    # randomized programs: several plays, tone values drawn near the limits
    def near_limit(limit, step):
        """A value at, just inside, or just outside a limit."""
        return limit + step * rng.choice((-2, -1, 0, 1, 2))

    for i in range(count):
        n = rng.randint(1, min(4, n_tones or 4))
        tones = []
        for _ in range(n):
            f = mixer + (near_limit(Fraction(fc["max"]["num"], fc["max"]["den"]), fres)
                         if fc is not None and fres is not None else Fraction(0))
            ph = near_limit(Fraction(1), pres) if pres is not None else Fraction(0)
            a = near_limit(Fraction(1), ares) if ares is not None else Fraction(1, 2)
            tones.append((f, ph, a))
        b = Builder([base(name, unit, mixer_hz=gen.get("_mixer_hz"), tones=tones)],
                    gen_frames(name))
        for _ in range(rng.randint(1, 4)):
            mask = sorted(rng.sample(range(n), rng.randint(1, n)))
            b.add(kind="play", frame="f0", mask=mask,
                  duration=rng.randint(1, 200) * dgrid)
            if rng.random() < 0.4:
                b.add(kind="delay", frame="f0", duration=rng.randint(0, 10) * sgrid)
        out.append((f"mux_random_{i:03d}", b.program()))
    return out


def cases_pulse_length(d, gen):
    """pulse_length_range and pulse_length_grid."""
    c = d.constraint(gen["name"], "pulse_length_range")
    grid = gen["duration_grid"]
    unit = Fraction(gen["unit"]["num"], gen["unit"]["den"])
    out = []

    if c is not None:
        for limit, which in ((c.get("min_units"), "min"), (c.get("max_units"), "max")):
            if limit is None:
                continue
            for value, rung in ladder(limit, grid):
                if value < 0:
                    continue
                b = Builder([base(gen["name"], unit, mixer_hz=gen.get("_mixer_hz"))], gen_frames(gen["name"]))
                b.wf_const("w0", Fraction(1, 2))
                b.add(kind="play", frame="f0", waveform="w0", duration=value)
                out.append((f"pulse_length_range_{which}_{rung}", b.program()))

    # off the duration grid by one unit and by half a grid step
    nominal = 60 * grid
    for offset, rung in ((1, "one_unit_off"), (grid // 2, "half_grid_off"),
                         (grid - 1, "one_below_next"), (0, "on_grid")):
        b = Builder([base(gen["name"], unit, mixer_hz=gen.get("_mixer_hz"))], gen_frames(gen["name"]))
        b.wf_const("w0", Fraction(1, 2))
        b.add(kind="play", frame="f0", waveform="w0", duration=nominal + offset)
        out.append((f"pulse_length_grid_{rung}", b.program()))
    return out


def cases_schedule_grid(d, gen):
    """schedule_grid, reached by starting a pulse off the tProc tick."""
    grid = gen["schedule_grid"]
    dgrid = gen["duration_grid"]
    unit = Fraction(gen["unit"]["num"], gen["unit"]["den"])
    out = []
    for offset, rung in ((0, "on_grid"), (1, "one_unit_off"),
                         (grid // 2, "half_grid_off")):
        b = Builder([base(gen["name"], unit, mixer_hz=gen.get("_mixer_hz"))], gen_frames(gen["name"]))
        b.wf_const("w0", Fraction(1, 2))
        b.add(kind="delay", frame="f0", duration=grid * 4 + offset)
        b.add(kind="play", frame="f0", waveform="w0", duration=60 * dgrid)
        out.append((f"schedule_grid_{rung}", b.program()))
    return out


def cases_frequency(d, gen):
    """frequency_range and frequency_resolution."""
    c = d.constraint(gen["name"], "frequency_range")
    if c is None:
        return []
    unit = Fraction(gen["unit"]["num"], gen["unit"]["den"])
    dgrid = gen["duration_grid"]
    out = []
    res = Fraction(c["resolution"]["num"], c["resolution"]["den"]) if "resolution" in c else None

    for key, which in (("min", "min"), ("max", "max")):
        if key not in c:
            continue
        limit = Fraction(c[key]["num"], c[key]["den"])
        step = res if res else Fraction(1)
        for value, rung in ladder(limit, step):
            b = Builder([base(gen["name"], unit, mixer_hz=gen.get("_mixer_hz"))], gen_frames(gen["name"]))
            b.wf_const("w0", Fraction(1, 2))
            b.add(kind="set_frequency", frame="f0", frequency=rat(value))
            b.add(kind="play", frame="f0", waveform="w0", duration=60 * dgrid)
            out.append((f"frequency_range_{which}_{rung}", b.program()))

    if res is not None:
        for mult, rung in ((100, "on_resolution"), (Fraction(1, 2), "half_step_off")):
            value = res * mult
            b = Builder([base(gen["name"], unit, mixer_hz=gen.get("_mixer_hz"))], gen_frames(gen["name"]))
            b.wf_const("w0", Fraction(1, 2))
            b.add(kind="set_frequency", frame="f0", frequency=rat(value))
            b.add(kind="play", frame="f0", waveform="w0", duration=60 * dgrid)
            out.append((f"frequency_resolution_{rung}", b.program()))
    return out


def cases_phase(d, gen):
    c = d.constraint(gen["name"], "phase_resolution")
    if c is None or "resolution" not in c:
        return []
    res = Fraction(c["resolution"]["num"], c["resolution"]["den"])
    unit = Fraction(gen["unit"]["num"], gen["unit"]["den"])
    dgrid = gen["duration_grid"]
    out = []
    for value, rung in ((res * 1000, "on_resolution"),
                        (res / 2, "half_step_off"),
                        (res * Fraction(3, 2), "one_and_half_steps")):
        b = Builder([base(gen["name"], unit, mixer_hz=gen.get("_mixer_hz"))], gen_frames(gen["name"]))
        b.wf_const("w0", Fraction(1, 2))
        b.add(kind="shift_phase", frame="f0", phase=rat(value))
        b.add(kind="play", frame="f0", waveform="w0", duration=60 * dgrid)
        out.append((f"phase_resolution_{rung}", b.program()))
    return out


def cases_amplitude(d, gen):
    c = d.constraint(gen["name"], "amplitude_range")
    if c is None:
        return []
    unit = Fraction(gen["unit"]["num"], gen["unit"]["den"])
    dgrid = gen["duration_grid"]
    res = Fraction(c["resolution"]["num"], c["resolution"]["den"]) if "resolution" in c else Fraction(1, 1000)
    out = []
    for key, which in (("min", "min"), ("max", "max")):
        if key not in c:
            continue
        limit = Fraction(c[key]["num"], c[key]["den"])
        for value, rung in ladder(limit, res):
            b = Builder([base(gen["name"], unit, mixer_hz=gen.get("_mixer_hz"))], gen_frames(gen["name"]))
            b.wf_const("w0", rat(value)["num"] and value or Fraction(0))
            b.add(kind="play", frame="f0", waveform="w0", duration=60 * dgrid)
            out.append((f"amplitude_range_{which}_{rung}", b.program()))
    for value, rung in ((res * 100, "on_resolution"), (res / 2, "half_step_off")):
        b = Builder([base(gen["name"], unit, mixer_hz=gen.get("_mixer_hz"))], gen_frames(gen["name"]))
        b.wf_const("w0", value)
        b.add(kind="play", frame="f0", waveform="w0", duration=60 * dgrid)
        out.append((f"amplitude_resolution_{rung}", b.program()))
    return out


def cases_envelope(d, gen):
    """envelope_sample_grid, envelope_amplitude, envelope_memory."""
    caps = gen.get("capabilities", {})
    grid = caps.get("envelope_sample_grid")
    max_abs = caps.get("envelope_max_abs")
    mem = caps.get("envelope_memory_samples")
    if grid is None:
        return []
    unit = Fraction(gen["unit"]["num"], gen["unit"]["den"])
    sample_unit = (Fraction(gen["sample_unit"]["num"], gen["sample_unit"]["den"])
                   if "sample_unit" in gen else unit)
    dgrid = gen["duration_grid"]
    out = []

    def envelope_program(n, amp, name):
        b = Builder([base(gen["name"], unit, sample_unit, gen.get("_mixer_hz"))], gen_frames(gen["name"]))
        b.wf_samples("e0", [amp] * n, [0] * n)
        b.add(kind="play", frame="f0", waveform="e0", duration=60 * dgrid)
        return (name, b.program())

    safe_amp = (max_abs // 2) if max_abs else 1000
    for n, rung in ((grid * 8, "on_grid"), (grid * 8 + 1, "one_sample_over"),
                    (grid * 8 - 1, "one_sample_under")):
        out.append(envelope_program(n, safe_amp, f"envelope_sample_grid_{rung}"))

    if max_abs is not None:
        for amp, rung in ladder(max_abs, 1, span=2):
            if amp < 0:
                continue
            out.append(envelope_program(grid * 8, amp, f"envelope_amplitude_{rung}"))

    if mem is not None:
        # one waveform just under the memory limit, and one just over
        for n, rung in ((mem // grid * grid, "at_memory"),
                        ((mem // grid + 1) * grid, "over_memory")):
            out.append(envelope_program(n, safe_amp, f"envelope_memory_{rung}"))
    return out


def cases_readout(d, gen, ro):
    c = d.constraint(ro["name"], "readout_length_range")
    if c is None:
        return []
    gunit = Fraction(gen["unit"]["num"], gen["unit"]["den"])
    runit = Fraction(ro["unit"]["num"], ro["unit"]["den"])
    grid = ro["duration_grid"]
    out = []
    for key, which in (("min_units", "min"), ("max_units", "max")):
        if c.get(key) is None:
            continue
        for value, rung in ladder(c[key], grid):
            if value < 0:
                continue
            b = Builder(
                [base(gen["name"], gunit, mixer_hz=gen.get("_mixer_hz")), base(ro["name"], runit)],
                gen_frames(gen["name"], ro["name"]),
            )
            b.wf_const("w0", Fraction(1, 2))
            b.add(kind="play", frame="f0", waveform="w0",
                  duration=60 * gen["duration_grid"])
            b.add(kind="barrier", frames=[])
            b.add(kind="capture", frame="r0", duration=value)
            out.append((f"readout_length_range_{which}_{rung}", b.program()))
    return out


def cases_negative(d, gen):
    unit = Fraction(gen["unit"]["num"], gen["unit"]["den"])
    dgrid = gen["duration_grid"]
    out = []
    for value, rung in ((-dgrid, "negative_duration"), (0, "zero_duration")):
        b = Builder([base(gen["name"], unit, mixer_hz=gen.get("_mixer_hz"))], gen_frames(gen["name"]))
        b.wf_const("w0", Fraction(1, 2))
        b.add(kind="play", frame="f0", waveform="w0", duration=value)
        out.append((f"negative_duration_{rung}", b.program()))
    return out


def cases_unconstrained(d, gen):
    """unconstrained_channel, reachable only where the descriptor declares a
    channel with no constraints. On QICK those are the pfb readouts."""
    names = d.unconstrained()
    if not names:
        return []
    ro_name = names[0]
    ro = d.channels[ro_name]
    gunit = Fraction(gen["unit"]["num"], gen["unit"]["den"])
    runit = Fraction(ro["unit"]["num"], ro["unit"]["den"])
    b = Builder(
        [base(gen["name"], gunit, mixer_hz=gen.get("_mixer_hz")), base(ro_name, runit)],
        gen_frames(gen["name"], ro_name),
    )
    b.wf_const("w0", Fraction(1, 2))
    b.add(kind="play", frame="f0", waveform="w0", duration=60 * gen["duration_grid"])
    b.add(kind="barrier", frames=[])
    b.add(kind="capture", frame="r0", duration=ro["duration_grid"] * 10)
    return [("unconstrained_channel_bound", b.program())]


def cases_budgets(d, gen):
    """pmem_words and wmem_words. These need scale rather than precision, so
    they are a small separate part of the corpus."""
    unit = Fraction(gen["unit"]["num"], gen["unit"]["den"])
    dgrid = gen["duration_grid"]
    budgets = {b["id"]: b for b in d.raw["budgets"]}
    out = []

    pmem = budgets.get("pmem_words")
    if pmem:
        overhead = pmem["cost_model"].get("overhead", 0)
        per = pmem["cost_model"].get("per_item", 1)
        need = (pmem["limit"] - overhead) // per
        for n, rung in ((need // 2, "half_limit"), (need + 8, "over_limit")):
            b = Builder([base(gen["name"], unit, mixer_hz=gen.get("_mixer_hz"))], gen_frames(gen["name"]))
            b.wf_const("w0", Fraction(1, 2))
            for _ in range(max(n, 1)):
                b.add(kind="play", frame="f0", waveform="w0", duration=3 * dgrid)
            out.append((f"pmem_words_{rung}", b.program()))

    wmem = budgets.get("wmem_words")
    if wmem:
        for n, rung in ((wmem["limit"] // 2, "half_limit"),
                        (wmem["limit"] + 4, "over_limit")):
            b = Builder([base(gen["name"], unit, mixer_hz=gen.get("_mixer_hz"))], gen_frames(gen["name"]))
            for i in range(max(n, 1)):
                b.wf_const(f"w{i}", Fraction(i % 32 + 1, 64))
                b.add(kind="play", frame="f0", waveform=f"w{i}", duration=3 * dgrid)
            out.append((f"wmem_words_{rung}", b.program()))
    return out


def random_cases(d, gen, rng, count):
    """Programs the boundary ladders do not imply.

    The ladders probe one limit at a time, which is what finds a limit stated
    wrongly. They cannot find a combination nobody thought of, because every
    program they emit was designed. These compose several elements at once,
    with values drawn near the limits rather than uniformly, so they stay in
    the region where an accept can be wrong.

    Seeded from the caller. Nothing here reads a clock.
    """
    unit = Fraction(gen["unit"]["num"], gen["unit"]["den"])
    dgrid = gen["duration_grid"]
    sgrid = gen["schedule_grid"]
    plr = d.constraint(gen["name"], "pulse_length_range")
    lo = plr.get("min_units", dgrid) if plr else dgrid
    hi = plr.get("max_units", 4000 * dgrid) if plr else 4000 * dgrid
    freq = d.constraint(gen["name"], "frequency_range")
    amp = d.constraint(gen["name"], "amplitude_range")

    def near_limit(limit, step):
        """A value at, just inside, or just outside a limit."""
        return limit + step * rng.choice((-2, -1, 0, 1, 2))

    out = []
    for i in range(count):
        b = Builder([base(gen["name"], unit, mixer_hz=gen.get("_mixer_hz"))], gen_frames(gen["name"]))
        n_wf = rng.randint(1, 3)
        for w in range(n_wf):
            if amp is not None and "max" in amp and "resolution" in amp:
                a_max = Fraction(amp["max"]["num"], amp["max"]["den"])
                a_res = Fraction(amp["resolution"]["num"], amp["resolution"]["den"])
                value = near_limit(a_max, a_res) * Fraction(rng.choice((1, 1, 1, -1)))
            else:
                value = Fraction(rng.randint(0, 100), 128)
            b.wf_const(f"w{w}", value)

        for _ in range(rng.randint(1, 6)):
            pick = rng.random()
            if pick < 0.55:
                dur = rng.choice((
                    near_limit(lo, dgrid),
                    near_limit(hi, dgrid),
                    rng.randint(1, 200) * dgrid + rng.choice((0, 0, 1, dgrid // 2)),
                ))
                b.add(kind="play", frame="f0", waveform=f"w{rng.randrange(n_wf)}",
                      duration=max(dur, 0))
            elif pick < 0.75:
                b.add(kind="delay", frame="f0",
                      duration=rng.randint(1, 50) * sgrid + rng.choice((0, 0, 1)))
            elif pick < 0.9 and freq is not None and "max" in freq:
                f_max = Fraction(freq["max"]["num"], freq["max"]["den"])
                f_res = (Fraction(freq["resolution"]["num"], freq["resolution"]["den"])
                         if "resolution" in freq else Fraction(1))
                b.add(kind="set_frequency", frame="f0",
                      frequency=rat(near_limit(f_max, f_res)
                                    * Fraction(rng.choice((1, -1)))))
            else:
                b.add(kind="shift_phase", frame="f0",
                      phase=rat(Fraction(rng.randint(0, 4095), 4096)))

        if not any(e["kind"] == "play" for e in b.elements):
            b.add(kind="play", frame="f0", waveform="w0", duration=60 * dgrid)
        out.append((f"random_{i:03d}", b.program()))
    return out


def tag_seed(seed, tag):
    """A per-generator seed that is the same in every process.

    Python salts hash() on a string per process, so seeding from it drew a
    different program on every run. The case names and the shuffle order were
    stable, which is why the corpus looked reproducible: only the randomized
    program bodies moved. crc32 is a fixed function of the bytes and has no
    salt.
    """
    return seed + zlib.crc32(tag.encode("utf-8"))


def build_corpus(descriptor_path, seed, random_programs=40, config_path=None):
    d = Descriptor(descriptor_path)
    rng = random.Random(seed)

    # A channel whose frequency_range is post_mixer must declare the mixer it
    # is configured with. The checker refuses the program otherwise, because
    # it cannot know the band the device sees. The value comes from the config
    # so the program describes the same device the oracle will compile for.
    if config_path is not None:
        cfg = json.loads(Path(config_path).read_text())
        for ch in d.raw["channels"]:
            if ch["kind"] != "drive":
                continue
            if not any(c.get("post_mixer") for c in ch["constraints"]):
                continue
            idx = int(ch["name"][3:])
            g = cfg["gens"][idx]
            if g.get("has_mixer"):
                ch["_mixer_hz"] = Fraction(str(g["f_dds"])) * 1_000_000 / 4

    cases = []
    # One representative generator per behavior class. A mux or interpolated
    # generator declares different constraints from a v6, so a corpus that
    # only probes gen0 cannot reach the rules the others carry.
    for gen in d.classes("drive"):
        tag = gen["name"]
        per_gen = []
        # A mux channel has a tone table and mask plays, so the frame-based
        # and waveform-based builders do not describe it and it gets its own.
        if gen.get("capabilities", {}).get("n_tones"):
            per_gen += mux_cases(d, gen, random.Random(tag_seed(seed, tag)), count=12)
            cases += [(f"{tag}__{name}", prog) for name, prog in per_gen]
            continue
        per_gen += cases_pulse_length(d, gen)
        per_gen += cases_schedule_grid(d, gen)
        per_gen += cases_frequency(d, gen)
        per_gen += cases_phase(d, gen)
        per_gen += cases_amplitude(d, gen)
        per_gen += cases_envelope(d, gen)
        per_gen += cases_negative(d, gen)
        per_gen += random_cases(d, gen, random.Random(tag_seed(seed, tag)),
                                random_programs)
        for ro in d.classes("readout"):
            per_gen += cases_readout(d, gen, ro)
        cases += [(f"{tag}__{name}", prog) for name, prog in per_gen]

    # Budgets and the unconstrained channel are whole-program properties, so
    # one generator is enough for them.
    gen0 = d.classes("drive")[0]
    cases += cases_budgets(d, gen0)
    cases += cases_unconstrained(d, gen0)

    # The seed fixes the order. Nothing else consumes randomness at this
    # point, so a rerun with the same seed writes byte-identical files.
    rng.shuffle(cases)

    seen = {}
    named = []
    for name, program in cases:
        seen[name] = seen.get(name, 0) + 1
        suffix = "" if seen[name] == 1 else f"_{seen[name]}"
        named.append((f"{name}{suffix}", program))
    return named


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("descriptor")
    ap.add_argument("outdir")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--config", default=None,
                    help="board config, needed for channels with a post_mixer "
                         "constraint so the program can declare its mixer")
    ap.add_argument("--random", type=int, default=40,
                    help="randomized programs per generator class")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("*.json"):
        old.unlink()

    corpus = build_corpus(args.descriptor, args.seed, args.random, args.config)
    index = []
    for name, program in corpus:
        path = outdir / f"{name}.json"
        path.write_text(json.dumps(program, indent=1, sort_keys=True) + "\n")
        index.append({"name": name, "program": path.name})

    (outdir / "index.json").write_text(
        json.dumps({"descriptor": str(args.descriptor), "seed": args.seed,
                    "count": len(index), "cases": index},
                   indent=1, sort_keys=True) + "\n")
    print(f"{len(index)} programs written to {outdir}")


if __name__ == "__main__":
    main()
