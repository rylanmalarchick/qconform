"""Generate the phase 5 corpus.

Boundary-driven, not random. For each limit the descriptor declares, emit a
ladder of values around it: far below, just below, exactly at, just above,
far above. The just-below and exactly-at rungs carry the weight, because the
gate is about programs qconform accepts and those are where an accept is most
likely to be wrong.

Seeded. The seed chooses which rungs combine and in what order. The same seed
produces byte-identical output, so a result can be replayed.

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


def base(channel, unit, sample_unit=None):
    ch = {"name": channel, "unit": rat(unit)}
    if sample_unit is not None:
        ch["sample_unit"] = rat(sample_unit)
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
                b = Builder([base(gen["name"], unit)], gen_frames(gen["name"]))
                b.wf_const("w0", Fraction(1, 2))
                b.add(kind="play", frame="f0", waveform="w0", duration=value)
                out.append((f"pulse_length_range_{which}_{rung}", b.program()))

    # off the duration grid by one unit and by half a grid step
    nominal = 60 * grid
    for offset, rung in ((1, "one_unit_off"), (grid // 2, "half_grid_off"),
                         (grid - 1, "one_below_next"), (0, "on_grid")):
        b = Builder([base(gen["name"], unit)], gen_frames(gen["name"]))
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
        b = Builder([base(gen["name"], unit)], gen_frames(gen["name"]))
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
            b = Builder([base(gen["name"], unit)], gen_frames(gen["name"]))
            b.wf_const("w0", Fraction(1, 2))
            b.add(kind="set_frequency", frame="f0", frequency=rat(value))
            b.add(kind="play", frame="f0", waveform="w0", duration=60 * dgrid)
            out.append((f"frequency_range_{which}_{rung}", b.program()))

    if res is not None:
        for mult, rung in ((100, "on_resolution"), (Fraction(1, 2), "half_step_off")):
            value = res * mult
            b = Builder([base(gen["name"], unit)], gen_frames(gen["name"]))
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
        b = Builder([base(gen["name"], unit)], gen_frames(gen["name"]))
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
            b = Builder([base(gen["name"], unit)], gen_frames(gen["name"]))
            b.wf_const("w0", rat(value)["num"] and value or Fraction(0))
            b.add(kind="play", frame="f0", waveform="w0", duration=60 * dgrid)
            out.append((f"amplitude_range_{which}_{rung}", b.program()))
    for value, rung in ((res * 100, "on_resolution"), (res / 2, "half_step_off")):
        b = Builder([base(gen["name"], unit)], gen_frames(gen["name"]))
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
        b = Builder([base(gen["name"], unit, sample_unit)], gen_frames(gen["name"]))
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
                [base(gen["name"], gunit), base(ro["name"], runit)],
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
        b = Builder([base(gen["name"], unit)], gen_frames(gen["name"]))
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
        [base(gen["name"], gunit), base(ro_name, runit)],
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
            b = Builder([base(gen["name"], unit)], gen_frames(gen["name"]))
            b.wf_const("w0", Fraction(1, 2))
            for _ in range(max(n, 1)):
                b.add(kind="play", frame="f0", waveform="w0", duration=3 * dgrid)
            out.append((f"pmem_words_{rung}", b.program()))

    wmem = budgets.get("wmem_words")
    if wmem:
        for n, rung in ((wmem["limit"] // 2, "half_limit"),
                        (wmem["limit"] + 4, "over_limit")):
            b = Builder([base(gen["name"], unit)], gen_frames(gen["name"]))
            for i in range(max(n, 1)):
                b.wf_const(f"w{i}", Fraction(i % 32 + 1, 64))
                b.add(kind="play", frame="f0", waveform=f"w{i}", duration=3 * dgrid)
            out.append((f"wmem_words_{rung}", b.program()))
    return out


def build_corpus(descriptor_path, seed):
    d = Descriptor(descriptor_path)
    gen = d.gens[0]
    ro = d.readouts[0] if d.readouts else None

    cases = []
    cases += cases_pulse_length(d, gen)
    cases += cases_schedule_grid(d, gen)
    cases += cases_frequency(d, gen)
    cases += cases_phase(d, gen)
    cases += cases_amplitude(d, gen)
    cases += cases_envelope(d, gen)
    cases += cases_negative(d, gen)
    cases += cases_budgets(d, gen)
    cases += cases_unconstrained(d, gen)
    if ro is not None:
        cases += cases_readout(d, gen, ro)

    # The seed fixes the order. Nothing else here consumes randomness, so a
    # rerun with the same seed writes byte-identical files.
    rng = random.Random(seed)
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
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("*.json"):
        old.unlink()

    corpus = build_corpus(args.descriptor, args.seed)
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
