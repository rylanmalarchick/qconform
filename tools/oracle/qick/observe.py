"""Read back what asm_v2 did to a compiled program.

get_pulse_param reports lengths, frequencies, phases and gains in
microseconds, megahertz and degrees. Start times are not stored on a pulse
and come from the compiled instruction stream. Every value is converted back
to the units the program asked in before it is compared.
"""

from fractions import Fraction

from oracle.base import round_half_even, same_grid_cell


# How to turn a vendor readback into the same units the request was made in.
# get_pulse_param returns microseconds, megahertz and degrees.
TO_REQUEST_UNITS = {
    "total_length": lambda v: Fraction(v) / 1_000_000,   # us  -> seconds
    "freq": lambda v: Fraction(v) * 1_000_000,           # MHz -> Hz
    "phase": lambda v: Fraction(v) / 360,                # deg -> turns
    "gain": lambda v: Fraction(v),                       # full-scale fraction
}

def readback_outcome(prog, plan):
    """Split compiled into accept or accept_round.

    Every quantity the vendor exposes is compared against what was asked for,
    not just the length. A repair to frequency, phase or gain is as much a
    silent repair as one to duration, and the checker predicts all of them.

    The comparison asks two things per quantity. Was the request already a
    whole multiple of the step the vendor quantizes to, and did the readback
    land on the same multiple. Comparing the rounded request against the
    readback alone would be tautological, because rounding the request is
    exactly what the vendor does.
    """
    changed = []
    # Readout configs are pulses to asm_v2 too, and get_pulse_param reads
    # their length back the same way. Without them a capture's duration
    # repair was invisible, and the checker's correct prediction read as open.
    names = [name for _, name, _ in plan.pulses] + [name for _, name, _, _ in plan.readoutconfigs]
    for name in names:
        for quantity, (requested, step) in plan.pulse_grid.get(name, {}).items():
            try:
                raw = float(prog.get_pulse_param(name, quantity))
            except (KeyError, ValueError, TypeError):
                continue
            got = TO_REQUEST_UNITS[quantity](raw)
            if not same_grid_cell(requested, got, step, quantity):
                changed.append({
                    "pulse": name,
                    "quantity": quantity,
                    "requested": str(requested),
                    "readback": str(got),
                })
    changed += tone_changes(prog, plan)
    return ("accept_round" if changed else "accept"), changed


# The tone table field each quantity is read back from, and the factor that
# puts it in the units the program asked for.
# A tone change is recorded under the same quantity name as a pulse change,
# so triage compares one vocabulary: freq, phase, gain.
TONE_QUANTITY = {"frequency": "freq", "phase": "phase", "amplitude": "gain"}

TONE_READBACK = {"frequency": ("freq_rounded", lambda v: Fraction(v) * 1_000_000),
                 "phase": ("phase_rounded", lambda v: Fraction(v) / 360),
                 "amplitude": ("gain_rounded", Fraction)}


def tone_changes(prog, plan):
    """Repairs the vendor made to a declared tone.

    calc_muxgen_regs quantizes each tone when the generator is declared, and a
    mux pulse carries a mask rather than a frequency, phase or gain, so
    get_pulse_param cannot see the repair. The same two questions as above:
    was the request already on the step, and did the value move.
    """
    changed = []
    for (ch, ti), quantities in plan.tone_grid.items():
        tones = prog.gen_chs.get(ch, {}).get("mux_tones") or []
        if ti >= len(tones):
            continue
        t = tones[ti]
        for quantity, (requested, step) in quantities.items():
            key, to_request = TONE_READBACK[quantity]
            if key not in t:
                continue
            got = to_request(t[key])
            if not same_grid_cell(requested, got, step, quantity):
                changed.append({
                    "tone": f"gen{ch}[{ti}]",
                    "quantity": TONE_QUANTITY[quantity],
                    "requested": str(requested),
                    "readback": str(got),
                })
    return changed


def scheduled_times(prog, soccfg):
    """The time each output actually lands on, from the instruction stream.

    A start time is not stored on the pulse. It lives in the compiled
    instruction stream, which is why an earlier version of this harness could
    not see a schedule_grid repair at all. tools/exporter/qick_export.py
    recovers it the same way: TIME with inc_ref accumulates a reference, and
    each WPORT_WR or TRIG carries an offset from it, both in tProc ticks.

    The vendor timeline does not start where the program does. A reference
    increment for the constructor's final_delay is emitted before the first
    output, so raw absolute ticks sit a fixed offset above the requested
    times. The exporter models the same offset as a leading delay element.
    Subtracting the reference accumulated before the first output puts both
    timelines on the same origin.

    Returns {(channel, kind): [ticks from the program origin, in order]}.
    """
    gen_by_port, ro_by_trig = {}, {}
    for ch in sorted(prog.gen_chs):
        gen_by_port[str(soccfg["gens"][ch]["tproc_ch"])] = ch
    for ch in sorted(prog.ro_chs):
        ro_by_trig[str(soccfg["readouts"][ch]["trigger_port"])] = ch

    out = {}
    ref_ticks = 0
    baseline = None
    for ins in prog.prog_list:
        cmd = ins["CMD"]
        is_gen = cmd == "WPORT_WR" and ins["DST"] in gen_by_port
        is_ro = (cmd == "TRIG" and ins.get("SRC") == "set"
                 and ins["DST"] in ro_by_trig)

        if cmd == "TIME" and ins.get("C_OP") == "inc_ref":
            ref_ticks += int(ins["LIT"].lstrip("#"))
            continue
        if not (is_gen or is_ro):
            continue

        if baseline is None:
            baseline = ref_ticks
        t_abs = ref_ticks + int(ins["TIME"].lstrip("@")) - baseline
        key = ((gen_by_port[ins["DST"]], "gen") if is_gen
               else (ro_by_trig[ins["DST"]], "ro"))
        out.setdefault(key, []).append(t_abs)
    return out


def start_time_changes(prog, plan, soccfg):
    """Compare every requested start time against the time the vendor chose.

    Asks the same two questions the duration comparison asks. Was the request
    already a whole number of tProc ticks, and did the vendor land on a
    different tick. Either one means the vendor moved the pulse.
    """
    actual = scheduled_times(prog, soccfg)
    seen = {}
    changed = []
    for ch, kind, requested_s, grid_s in plan.schedule:
        key = (ch, kind)
        i = seen.get(key, 0)
        seen[key] = i + 1
        ticks = actual.get(key, [])
        if i >= len(ticks) or not grid_s or grid_s <= 0:
            continue
        got_s = Fraction(ticks[i]) * grid_s
        want_ticks = requested_s / grid_s
        off_grid = want_ticks.denominator != 1
        moved = round_half_even(want_ticks) != ticks[i]
        if off_grid or moved:
            changed.append({
                "quantity": "start_time",
                "channel": f"{kind}{ch}",
                "requested_ticks": str(want_ticks),
                "scheduled_ticks": ticks[i],
            })
    return changed


def raw_registers(prog, plan):
    """The register values the vendor will actually emit.

    get_pulse_param reports the value that was asked for. The register is what
    the hardware sees, and the two differ exactly where the vendor accepts
    something it cannot represent: a frequency past the DDS range wraps, and a
    gain past full scale stays out of range. tools/survey/runner.py reads the
    same registers for the same reason.
    """
    out = {}
    for ch, name, kwargs in plan.pulses:
        waves = prog.pulses[name].waveforms if name in prog.pulses else []
        if not waves:
            continue
        w = waves[0]
        # A mux waveform carries literal zeros in freq, phase and gain: those
        # belong to the tone table and the mask lives in conf. Recording the
        # zeros would read as "the hardware sees zero", which is false, so a
        # mux pulse reports only what it actually has.
        keys = (("length",) if "mask" in kwargs
                else ("freq", "phase", "gain", "length"))
        out[name] = {k: int(w[k]) for k in keys
                     if k in w and not hasattr(w[k], "spans")}
    # The tone registers, which are where a mux channel's frequency, phase and
    # gain actually land. calc_muxgen_regs quantizes them at declare time, so
    # they are not readable through get_pulse_param.
    for kw in plan.declare_gens:
        tones = prog.gen_chs.get(kw["ch"], {}).get("mux_tones")
        if not tones:
            continue
        out[f"gen{kw['ch']}_tones"] = [
            {k: int(t[k]) for k in ("freq_int", "gain_int", "phase_int") if k in t}
            for t in tones]
    return out
