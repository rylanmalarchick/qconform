"""Lower a qconform program to an asm_v2 program.

qconform programs are exact. Every duration is an integer count of a
rational time unit, and every frequency, phase and amplitude is a rational.
The vendor API takes microseconds, megahertz and degrees as doubles. This
module performs that conversion and records what it lost, because a
disagreement caused here is not a defect in the checker or in the vendor.

Direction matters. tools/exporter/qick_export.py goes the other way, from a
compiled asm_v2 program to qconform JSON, and its own docstring explains why
that direction cannot serve the differential harness: exporter output is
post-quantization, so the grid rules cannot fire on it. A program the vendor
rejects also never compiles, so it could never be exported, and the unsound
PASS the gate looks for could never appear.

Frames are compiled away for QICK. The descriptor says so, and the lowering
follows: it walks the element list keeping each frame's clock, frequency and
phase, and gives every play its own pulse definition carrying the frame state
at that moment.
"""

from fractions import Fraction

import numpy as np
from qick.asm_v2 import AveragerProgramV2


def rat(d):
    """A qconform {"num": N, "den": D} as an exact Fraction."""
    return Fraction(d["num"], d["den"])


def channel_index(name):
    """Descriptor channel name to vendor channel index.

    gen0 is gens[0] and ro0 is readouts[0]. The caller checks vendor_type
    against the config, so a wrong mapping fails loudly instead of compiling
    something unintended.
    """
    for prefix in ("gen", "ro"):
        if name.startswith(prefix) and name[len(prefix):].isdigit():
            return prefix, int(name[len(prefix):])
    raise ValueError(f"channel name {name!r} does not map to a vendor channel")


def declare_kwargs(soccfg, ch):
    """Arguments declare_gen needs for this generator.

    A generator with a digital mixer refuses to compile without mixer_freq,
    and a muxed generator needs its tone table. Omitting either makes the
    vendor reject the program for a reason the program did not cause, which
    would be recorded as a disagreement that is really a harness bug.

    Same logic as tools/survey/runner.py declare_kwargs. Kept separate rather
    than imported: the survey is a standalone tool and phase 5 must not make
    it a library by accident.
    """
    g = soccfg["gens"][ch]
    kw = {"ch": ch, "nqz": 1}
    if g.get("has_mixer"):
        kw["mixer_freq"] = g["f_dds"] / 4
    if "mux" in g["type"]:
        kw["mux_freqs"] = [g["f_dds"] / 8, g["f_dds"] / 16]
        if g.get("has_gain"):
            kw["mux_gains"] = [0.5, 0.4]
        if g.get("has_phase"):
            kw["mux_phases"] = [0.0, 0.0]
    return kw


def round_half_even(value):
    """Round a Fraction to the nearest integer, ties to even.

    This is the rounding mode the descriptor declares for time, so the grid
    cell computed here is the cell the vendor would land in.
    """
    floor = value.numerator // value.denominator
    rem = value - floor
    if rem < Fraction(1, 2):
        return floor
    if rem > Fraction(1, 2):
        return floor + 1
    return floor if floor % 2 == 0 else floor + 1


class Loss:
    """What the conversion to a double cost, for one value.

    exact      the value qconform means, as a Fraction
    passed     the double actually handed to the vendor, as a Fraction
    grid       the grid step the vendor quantizes to, or None
    same_cell  whether both land in the same grid cell
    """

    def __init__(self, kind, element_id, exact, passed_double, grid=None):
        self.kind = kind
        self.element_id = element_id
        self.exact = exact
        self.passed = Fraction(passed_double)
        self.grid = grid
        if grid is None or grid == 0:
            self.same_cell = self.exact == self.passed
        else:
            self.same_cell = (round_half_even(self.exact / grid)
                              == round_half_even(self.passed / grid))

    def as_row(self):
        return {
            "kind": self.kind,
            "element": self.element_id,
            "exact": [self.exact.numerator, self.exact.denominator],
            "passed": [self.passed.numerator, self.passed.denominator],
            "same_cell": self.same_cell,
        }


class LoweringError(Exception):
    """The program cannot be expressed in the vendor API at all.

    This is not a vendor rejection. It means the harness cannot ask the
    question, and the row must be recorded as such rather than counted as
    either agreement or disagreement.
    """


class Plan:
    """The vendor calls a program lowers to, computed before any vendor object
    exists so the translation can be inspected and tested on its own."""

    def __init__(self):
        self.declare_gens = []      # kwargs for declare_gen
        self.declare_readouts = []  # (ch, length_us)
        self.envelopes = []         # (ch, name, idata, qdata)
        self.pulses = []            # (ch, name, kwargs)
        # name -> {quantity: (requested_exact, step)}; the step is the grid or
        # resolution the vendor quantizes that quantity to
        self.pulse_grid = {}
        self.readoutconfigs = []    # (ch, name, freq_mhz, length_us)
        self.body = []              # ('pulse'|'trigger', ...)
        self.losses = []

    def loss_rows(self):
        return [l.as_row() for l in self.losses]

    def lost_cells(self):
        """Elements where the double landed in a different grid cell. These
        make the row harness-attributable."""
        return [l.as_row() for l in self.losses if not l.same_cell]


def build_plan(program, descriptor, soccfg):
    """qconform program JSON to a Plan. Pure: touches no vendor object."""
    plan = Plan()

    desc_by_name = {c["name"]: c for c in descriptor["channels"]}

    # bind program channels to descriptor channels, and both to the config
    bind = {}
    for pc in program["channels"]:
        name = pc["name"]
        if name not in desc_by_name:
            raise LoweringError(f"channel {name!r} is not in the descriptor")
        dc = desc_by_name[name]
        prefix, idx = channel_index(name)
        table = "gens" if prefix == "gen" else "readouts"
        if idx >= len(soccfg[table]):
            raise LoweringError(f"{name} is beyond the config {table} table")
        vendor_type = soccfg[table][idx].get("type") or soccfg[table][idx].get("ro_type")
        if vendor_type != dc["vendor_type"]:
            raise LoweringError(
                f"{name}: descriptor says {dc['vendor_type']!r}, config says {vendor_type!r}")
        # The resolution a quantity is quantized to, where the descriptor
        # declares one. Without it the same-cell test degenerates to bit-exact
        # equality, which every non-dyadic rational fails, and real
        # disagreements would be discarded as harness noise.
        res = {}
        for c in dc["constraints"]:
            if "resolution" not in c:
                continue
            if c["id"] in ("frequency_range", "frequency_resolution"):
                res["frequency"] = rat(c["resolution"])
            elif c["id"] == "phase_resolution":
                res["phase"] = rat(c["resolution"])
            elif c["id"] in ("amplitude_range", "amplitude_resolution"):
                res["amplitude"] = rat(c["resolution"])

        bind[name] = {
            "kind": prefix,
            "index": idx,
            "prog_unit": rat(pc["unit"]),
            "desc_unit": rat(dc["unit"]),
            "duration_grid": dc["duration_grid"],
            "schedule_grid": dc["schedule_grid"],
            "resolution": res,
        }

    frames = {f["name"]: dict(f) for f in program["frames"]}
    waveforms = {w["name"]: w for w in program["waveforms"]}

    for ch_name, b in bind.items():
        if b["kind"] == "gen":
            plan.declare_gens.append(declare_kwargs(soccfg, b["index"]))

    # envelopes are per waveform, declared once on each channel that plays them
    declared_envelopes = set()
    # distinct pulse definitions, keyed by everything that makes them distinct
    reused = {}

    state = {}
    for name, f in frames.items():
        state[name] = {
            "clock": 0,                       # in program channel units
            "freq": rat(f["frequency"]),      # Hz
            "phase": rat(f["phase"]),         # turns
            "channel": f["channel"],
        }

    def seconds(frame_name, units):
        return Fraction(units) * bind[state[frame_name]["channel"]]["prog_unit"]

    def grid_seconds(frame_name, which):
        b = bind[state[frame_name]["channel"]]
        return Fraction(b[which]) * b["desc_unit"]

    for el in program["elements"]:
        kind = el["kind"]
        eid = el["id"]

        if kind == "barrier":
            members = el.get("frames") or list(frames)
            latest = max(
                (seconds(m, state[m]["clock"]) for m in members),
                default=Fraction(0),
            )
            for m in members:
                unit = bind[state[m]["channel"]]["prog_unit"]
                state[m]["clock"] = round_half_even(latest / unit)
            continue

        if kind == "shift_phase":
            state[el["frame"]]["phase"] += rat(el["phase"])
            continue

        if kind == "set_frequency":
            state[el["frame"]]["freq"] = rat(el["frequency"])
            continue

        fname = el["frame"]
        st = state[fname]
        b = bind[st["channel"]]

        if kind == "delay":
            st["clock"] += el["duration"]
            continue

        # play and capture both occupy time and both start at the frame clock
        start_s = seconds(fname, st["clock"])
        start_us = float(start_s * 1_000_000)
        plan.losses.append(Loss("start", eid, start_s,
                                Fraction(start_us) / 1_000_000,
                                grid_seconds(fname, "schedule_grid")))

        dur_s = seconds(fname, el["duration"])
        dur_us = float(dur_s * 1_000_000)
        plan.losses.append(Loss("duration", eid, dur_s,
                                Fraction(dur_us) / 1_000_000,
                                grid_seconds(fname, "duration_grid")))

        if kind == "capture":
            if b["kind"] != "ro":
                raise LoweringError(f"element {eid}: capture on a generator channel")
            # A pfb readout is static: PYNQ fixes its frequency and the vendor
            # refuses add_readoutconfig on it. This lowering only speaks the
            # dynamic readout API, so it cannot express a capture there and
            # must say so rather than let the vendor refuse a program for a
            # reason the program did not cause.
            ro_type = soccfg["readouts"][b["index"]].get("ro_type", "")
            if "dyn" not in ro_type:
                raise LoweringError(
                    f"element {eid}: {ro_type} is a static readout; this "
                    f"harness only lowers the dynamic readout API")
            plan.declare_readouts.append((b["index"], dur_us))
            cfg_name = f"ro{eid}"
            plan.readoutconfigs.append((b["index"], cfg_name,
                                        float(st["freq"] / 1_000_000), dur_us))
            plan.body.append(("trigger", b["index"], cfg_name, start_us))
            st["clock"] += el["duration"]
            continue

        if kind != "play":
            raise LoweringError(f"element {eid}: unsupported kind {kind!r}")
        if b["kind"] != "gen":
            raise LoweringError(f"element {eid}: play on a readout channel")

        wf = waveforms[el["waveform"]]
        amp = None
        kwargs = {
            "freq": float(st["freq"] / 1_000_000),   # Hz to MHz
            "phase": float(st["phase"] * 360),       # turns to degrees
        }
        plan.losses.append(Loss("frequency", eid, st["freq"],
                                Fraction(kwargs["freq"]) * 1_000_000,
                                b["resolution"].get("frequency")))
        plan.losses.append(Loss("phase", eid, st["phase"],
                                Fraction(kwargs["phase"]) / 360,
                                b["resolution"].get("phase")))

        if wf["kind"] == "const":
            amp = rat(wf["amplitude"])
            kwargs.update(style="const", gain=float(amp), length=dur_us)
            plan.losses.append(Loss("amplitude", eid, amp, Fraction(kwargs["gain"]),
                                    b["resolution"].get("amplitude")))
        else:
            env_name = f"e_{wf['name']}"
            key = (b["index"], env_name)
            if key not in declared_envelopes:
                declared_envelopes.add(key)
                gcfg = soccfg["gens"][b["index"]]
                idata = np.array(wf["i"], dtype=np.int32)
                qdata = (np.array(wf["q"], dtype=np.int32)
                         if gcfg.get("complex_env") else None)
                plan.envelopes.append((b["index"], env_name, idata, qdata))
            kwargs.update(style="arb", envelope=env_name, gain=1.0)

        # Define each distinct pulse once and replay it. A fresh definition
        # per element would allocate a waveform entry per play, which exhausts
        # the vendor's wave table on a program that repeats one pulse, and the
        # refusal would look like an unsound PASS when it is an artifact of
        # how this lowering wrote the program.
        key = (b["index"], el["waveform"], kwargs.get("freq"), kwargs.get("phase"),
               kwargs.get("gain"), kwargs.get("length"), kwargs.get("envelope"))
        pulse_name = reused.get(key)
        if pulse_name is None:
            pulse_name = f"p{len(reused)}"
            reused[key] = pulse_name
            plan.pulses.append((b["index"], pulse_name, kwargs))
            observed = {
                "freq": (st["freq"], b["resolution"].get("frequency")),
                "phase": (st["phase"], b["resolution"].get("phase")),
            }
            if "length" in kwargs:
                observed["total_length"] = (dur_s, grid_seconds(fname, "duration_grid"))
            if amp is not None:
                observed["gain"] = (amp, b["resolution"].get("amplitude"))
            plan.pulse_grid[pulse_name] = observed
        plan.body.append(("pulse", b["index"], pulse_name, start_us))
        st["clock"] += el["duration"]

    return plan


class LoweredProgram(AveragerProgramV2):
    """Replays a Plan into the vendor object. No translation happens here."""

    def _initialize(self, cfg):
        plan = cfg["plan"]
        for kw in plan.declare_gens:
            self.declare_gen(**kw)
        for ch, length_us in plan.declare_readouts:
            self.declare_readout(ch=ch, length=length_us)
        for ch, name, idata, qdata in plan.envelopes:
            self.add_envelope(ch=ch, name=name, idata=idata, qdata=qdata)
        for ch, name, freq, length_us in plan.readoutconfigs:
            self.add_readoutconfig(ch=ch, name=name, freq=freq, length=length_us)
        for ch, name, kwargs in plan.pulses:
            self.add_pulse(ch=ch, name=name, **kwargs)

    def _body(self, cfg):
        for op in cfg["plan"].body:
            if op[0] == "pulse":
                _, ch, name, t_us = op
                self.pulse(ch=ch, name=name, t=t_us)
            else:
                _, ch, cfg_name, t_us = op
                self.send_readoutconfig(ch=ch, name=cfg_name, t=t_us)
                self.trigger(ros=[ch], t=t_us)


def compile_plan(plan, soccfg):
    """Run the oracle on a plan.

    Constructing the vendor object compiles it. AveragerProgramV2.__init__
    calls compile() itself, so the vendor's checks fire here and not at a
    later explicit call. Everything this can raise is the vendor's answer.

    Returns (program_or_None, outcome, detail). Outcome is one of the survey's
    four names, minus the accept/accept_round split, which needs a readback
    the caller performs: this returns 'compiled', 'reject' or 'crash'.
    """
    try:
        prog = LoweredProgram(soccfg, reps=1, final_delay=1.0, cfg={"plan": plan})
        return prog, "compiled", None
    except (RuntimeError, ValueError) as e:
        return None, "reject", {"error_type": type(e).__name__,
                                "error_msg": str(e)[:300]}
    except Exception as e:  # the vendor failed without meaning to; see notes/oracle-path.txt
        return None, "crash", {"error_type": type(e).__name__,
                               "error_msg": str(e)[:300]}


def lower(program, descriptor, soccfg):
    """Plan the program and run the oracle on it."""
    plan = build_plan(program, descriptor, soccfg)
    prog, outcome, detail = compile_plan(plan, soccfg)
    return prog, plan, outcome, detail
