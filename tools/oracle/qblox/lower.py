"""Lower a qconform program to a qblox-scheduler Schedule.

A qconform frame becomes a clock resource and a channel becomes the port its
hardware path is wired to, so one frame on one channel is one port-clock,
which the scheduler compiles to one sequencer. Every operation is placed at
its absolute time.

  play (const)      SquarePulse
  play (samples)    NumericalPulse, each sample code / envelope_max_abs
  capture           SSBIntegrationComplex
  set_frequency     SetClockFrequency
  shift_phase       ShiftClockPhase, turns * 360 degrees
  delay, barrier    frame clock arithmetic only

The scheduler takes seconds, Hz, degrees and amplitudes as floats. The
conversion is recorded per value, as for QICK, because a disagreement
caused here is not a defect in the checker or the vendor.

Two things the device cannot hold are expressed the way it can:

  - A clock resource drops its phase (survey: "ClockResource phase"), so a
    frame's nonzero initial phase is a ShiftClockPhase at time 0.
  - A frame with no play or capture has no port, so its frequency and phase
    updates reach no sequencer. They are not lowered. Triage reads such
    rows through unused_frame_updates, as for QICK.

The lowering refuses what it cannot express: a sample waveform whose play
duration differs from its sample count (Qblox does not pad), and a sample
unit other than the descriptor's.
"""

from fractions import Fraction

from oracle.base import Loss, LoweringError, rat, round_half_even
from oracle.base import Plan as BasePlan

NS = Fraction(1, 10**9)
GAIN_STEP = Fraction(1, 32768)
FREQ_STEP = Fraction(1, 4)
PHASE_STEP = Fraction(1, 10**9)


class Plan(BasePlan):
    """The operations a program lowers to, before any vendor object exists."""

    def __init__(self):
        super().__init__()
        self.clocks = {}        # frame -> initial frequency, Hz (Fraction)
        self.ports = {}         # frame -> port
        # (t seconds exact, order, frame, kind, params); built into the
        # Schedule in time order
        self.ops = []
        # frame -> what the compiled sequencer should show, for observe
        self.expect = {}
        # frames whose initial frequency is off the NCO step: the instrument
        # quantizes it, which offline cannot be seen
        self.unobservable = set()


def port_of(hw, vendor_type):
    """The port a hardware path is wired to."""
    for src, dst in hw["connectivity"]["graph"]:
        if src == vendor_type:
            if not isinstance(dst, str):
                raise LoweringError(f"{vendor_type} is wired to several ports: {dst}")
            return dst
    raise LoweringError(f"{vendor_type} is not in the config's connectivity graph")


def build_plan(program, descriptor, hw):
    plan = Plan()
    desc_by_name = {c["name"]: c for c in descriptor["channels"]}

    bind = {}
    for pc in program["channels"]:
        name = pc["name"]
        if name not in desc_by_name:
            raise LoweringError(f"channel {name!r} is not in the descriptor")
        dc = desc_by_name[name]
        caps = dc.get("capabilities", {})
        if "sample_unit" in pc and rat(pc["sample_unit"]) != rat(dc.get("sample_unit", {"num": 1, "den": 10**9})):
            raise LoweringError(f"{name}: sample unit differs from the device's 1 ns")
        bind[name] = {
            "kind": dc["kind"],
            "port": port_of(hw, dc["vendor_type"]),
            "unit": rat(pc["unit"]),
            "max_abs": caps.get("envelope_max_abs"),
        }

    frames = {f["name"]: f for f in program["frames"]}
    waveforms = {w["name"]: w for w in program["waveforms"]}
    used = {el["frame"] for el in program["elements"] if el["kind"] in ("play", "capture")}

    state = {name: {"clock": 0, "channel": f["channel"]} for name, f in frames.items()}
    order = 0

    def seconds(frame, units):
        return Fraction(units) * bind[state[frame]["channel"]]["unit"]

    def add(t, frame, kind, **params):
        nonlocal order
        plan.ops.append((t, order, frame, kind, params))
        order += 1

    def expect(frame, entry):
        plan.expect.setdefault(frame, []).append(entry)

    for name in sorted(used):
        f = frames[name]
        freq, phase = rat(f["frequency"]), rat(f["phase"])
        plan.clocks[name] = freq
        plan.ports[name] = bind[f["channel"]]["port"]
        plan.losses.append(Loss(f"{name}_frequency", -1, freq, Fraction(float(freq)), FREQ_STEP))
        if (freq / FREQ_STEP).denominator != 1:
            plan.unobservable.add(name)
        if phase != 0:
            # the device has no initial phase; set it by an update at time 0
            deg = float(phase * 360)
            plan.losses.append(Loss(f"{name}_phase", -1, phase, Fraction(deg) / 360, PHASE_STEP))
            add(Fraction(0), name, "shift_phase", degrees=deg)
            expect(name, {"kind": "phase", "t": Fraction(0), "turns": phase, "element": -1})

    for el in program["elements"]:
        kind, eid = el["kind"], el["id"]

        if kind == "barrier":
            members = el.get("frames") or list(frames)
            latest = max((seconds(m, state[m]["clock"]) for m in members), default=Fraction(0))
            for m in members:
                unit = bind[state[m]["channel"]]["unit"]
                exact = latest / unit
                if exact.denominator != 1:
                    plan.barrier_roundings.append({
                        "element": eid, "frame": m,
                        "exact_units": str(exact), "rounded_units": round_half_even(exact)})
                state[m]["clock"] = round_half_even(exact)
            continue

        frame = el["frame"]
        st = state[frame]
        b = bind[st["channel"]]
        t = seconds(frame, st["clock"])

        if kind == "delay":
            st["clock"] += el["duration"]
            continue

        if kind in ("shift_phase", "set_frequency"):
            if frame not in used:
                continue
            if kind == "shift_phase":
                turns = rat(el["phase"])
                deg = float(turns * 360)
                plan.losses.append(Loss("phase", eid, turns, Fraction(deg) / 360, PHASE_STEP))
                add(t, frame, "shift_phase", degrees=deg)
                expect(frame, {"kind": "phase", "t": t, "turns": turns, "element": eid})
            else:
                hz = rat(el["frequency"])
                plan.losses.append(Loss("frequency", eid, hz, Fraction(float(hz)), FREQ_STEP))
                add(t, frame, "set_frequency", hz=float(hz))
                expect(frame, {"kind": "freq", "t": t, "hz": hz, "element": eid})
            continue

        dur = seconds(frame, el["duration"])
        plan.losses.append(Loss("start", eid, t, Fraction(float(t)), NS))
        plan.losses.append(Loss("duration", eid, dur, Fraction(float(dur)), NS))

        if kind == "capture":
            if b["kind"] != "readout":
                raise LoweringError(f"element {eid}: capture on a drive channel")
            add(t, frame, "acquire", duration=float(dur))
            expect(frame, {"kind": "capture", "t": t, "duration": dur, "element": eid})
            st["clock"] += el["duration"]
            continue

        if kind != "play":
            raise LoweringError(f"element {eid}: unsupported kind {kind!r}")
        if b["kind"] != "drive":
            raise LoweringError(f"element {eid}: play on a readout channel")
        wf = waveforms[el["waveform"]]
        if wf["kind"] == "const":
            amp = rat(wf["amplitude"])
            plan.losses.append(Loss("amplitude", eid, amp, Fraction(float(amp)), GAIN_STEP))
            add(t, frame, "square", amplitude=float(amp), duration=float(dur))
            expect(frame, {"kind": "play", "t": t, "duration": dur, "gain": amp,
                           "element": eid})
        else:
            n = len(wf["i"])
            if dur != n * NS:
                raise LoweringError(f"element {eid}: {n} samples played for {dur} s; "
                                    f"Qblox does not pad a waveform")
            if not b["max_abs"]:
                raise LoweringError(f"element {eid}: the channel declares no envelope_max_abs")
            scale = b["max_abs"]
            i = [Fraction(v, scale) for v in wf["i"]]
            q = [Fraction(v, scale) for v in wf["q"]]
            samples = ([complex(float(a), float(c)) for a, c in zip(i, q)]
                       if any(q) else [float(a) for a in i])
            add(t, frame, "numerical", samples=samples)
            expect(frame, {"kind": "play", "t": t, "duration": dur,
                           "gain": envelope_scale(i), "element": eid})
        st["clock"] += el["duration"]

    if not plan.ops:
        raise LoweringError("no frame carries a play or capture, so there is nothing to compile")
    return plan


def envelope_scale(values):
    """The gain the scheduler gives a waveform: its first sample of largest
    magnitude, with its sign."""
    best = Fraction(0)
    for v in values:
        if abs(v) > abs(best):
            best = v
    return best


def build_schedule(plan):
    """The Schedule for a plan. Constructing an operation checks its
    arguments, so this is the first vendor stage and may raise."""
    import qblox_scheduler as qs

    s = qs.Schedule("qconform")
    for frame in sorted(plan.clocks):
        s.add_resource(qs.ClockResource(frame, float(plan.clocks[frame])))
    first = None
    t_first = None
    for t, _, frame, kind, p in sorted(plan.ops, key=lambda op: (op[0], op[1])):
        port = plan.ports[frame]
        if kind == "square":
            op = qs.SquarePulse(amplitude=p["amplitude"], duration=p["duration"],
                                port=port, clock=frame)
        elif kind == "numerical":
            samples = p["samples"] + p["samples"][-1:]
            op = qs.NumericalPulse(samples=samples,
                                   t_samples=[k * 1e-9 for k in range(len(samples))],
                                   port=port, clock=frame)
        elif kind == "acquire":
            op = qs.SSBIntegrationComplex(port=port, clock=frame, duration=p["duration"])
        elif kind == "set_frequency":
            op = qs.SetClockFrequency(clock=frame, frequency=p["hz"])
        elif kind == "shift_phase":
            op = qs.ShiftClockPhase(phase_shift=p["degrees"], clock=frame)
        else:
            raise ValueError(f"unknown planned operation {kind!r}")
        if first is None:
            first = s.add(op, rel_time=float(t))
            t_first = t
        else:
            s.add(op, ref_op=first, ref_pt="start", rel_time=float(t - t_first))
    return s
