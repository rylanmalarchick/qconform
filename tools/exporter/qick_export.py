"""Export a compiled asm_v2 program to qconform program JSON.

Walks the compiled artifacts (prog_list instructions, waves registry,
pulse/envelope tables) rather than re-deriving timing, so the exported
program is exactly what the toolchain will run: POST-quantization,
integer-exact by construction. Consequence stated honestly: qconform's
quantization rules cannot fire on exporter output (grids hold by
construction); silent-repair detection for QICK users comes from the
differential harness. Hand-authored program JSON is where those rules
bite.

Time model (matches the descriptor): per channel,
  unit = 1 / lcm(channel clock, tProc timing clock)   [exact rational s]
  one channel cycle  = duration_grid * unit
  one tProc tick     = schedule_grid * unit
All exported durations/times are integers in channel units.

v0 scope: non-swept programs, reps handled as a single body iteration,
no overlapping pulses on one channel (NotImplementedError otherwise).
"""

from fractions import Fraction
from math import lcm

B_DDS = 32
B_PHASE = 32


def _frac_mhz(x):
    f = Fraction(str(x))
    if float(f) != x:
        raise ValueError(f"clock {x} is not a clean decimal")
    return f


def _rat(f):
    f = Fraction(f)
    return {"num": f.numerator, "den": f.denominator}


def _unit(f_channel, f_time):
    f1, f2 = _frac_mhz(f_channel), _frac_mhz(f_time)
    L = Fraction(lcm(f1.numerator * f2.denominator, f2.numerator * f1.denominator),
                 f1.denominator * f2.denominator)
    return Fraction(1, 1) / (L * 10**6), int(L / f1), int(L / f2)


def _reg_to_freq_hz(reg, f_dds_mhz):
    # exported frequency is the signed DDS-domain representative in
    # [-f_dds/2, f_dds/2) -- the band the toolchain itself accepts. A user
    # frequency above f_dds/2 (e.g. 4900 MHz on a 9584.64 MHz DDS) exports
    # as its negative alias; that IS the DDS tone the hardware synthesizes
    reg = reg % 2**B_DDS
    if reg >= 2**(B_DDS - 1):
        reg -= 2**B_DDS
    return _frac_mhz(f_dds_mhz) * 10**6 * Fraction(reg, 2**B_DDS)


def _reg_to_turns(reg):
    return Fraction(reg % 2**B_PHASE, 2**B_PHASE)


class _FrameState:
    def __init__(self, name):
        self.name = name
        self.freq = Fraction(0)
        self.phase = Fraction(0)
        self.cursor = 0          # units; end of last emitted element


def export(prog, soccfg):
    """Compiled AveragerProgramV2 + QickConfig -> program-format-v0 dict."""
    if any(getattr(w[k], "spans", None) for w in prog.waves
           for k in ("freq", "phase", "gain", "length")):
        raise NotImplementedError("swept programs are out of v0 exporter scope")

    f_time = soccfg["tprocs"][0]["f_time"]

    # channel tables ------------------------------------------------------
    channels, frames, states = [], [], {}
    gen_key = {}     # tproc port -> gen ch
    for ch in sorted(prog.gen_chs):
        g = soccfg["gens"][ch]
        unit, dur_grid, sched_grid = _unit(g["f_fabric"], f_time)
        cname, fname = f"gen{ch}", f"gen{ch}_frame"
        entry = {"name": cname, "unit": _rat(unit)}
        if "maxlen" in g:
            entry["sample_unit"] = _rat(
                Fraction(1, 1) / (_frac_mhz(g["f_fabric"]) * 10**6
                                  * g["samps_per_clk"]))
        channels.append(entry)
        frames.append({"name": fname, "channel": cname,
                       "frequency": _rat(0), "phase": _rat(0)})
        states[("gen", ch)] = _FrameState(fname)
        states[("gen", ch)].grids = (dur_grid, sched_grid)
        gen_key[str(g["tproc_ch"])] = ch

    ro_by_trig, ro_by_ctrl = {}, {}
    for ch in sorted(prog.ro_chs):
        r = soccfg["readouts"][ch]
        unit, dur_grid, sched_grid = _unit(r["f_output"], f_time)
        cname, fname = f"ro{ch}", f"ro{ch}_frame"
        channels.append({"name": cname, "unit": _rat(unit)})
        frames.append({"name": fname, "channel": cname,
                       "frequency": _rat(0), "phase": _rat(0)})
        states[("ro", ch)] = _FrameState(fname)
        states[("ro", ch)].grids = (dur_grid, sched_grid)
        ro_by_trig[str(r["trigger_port"])] = ch
        if "tproc_ctrl" in r:
            ro_by_ctrl[str(r["tproc_ctrl"])] = ch

    # waveform table: one qconform waveform per distinct (pulse, wave) ----
    wave_owner = {}          # wave name -> (pulse name, style, envelope name)
    for pname, pulse in prog.pulses.items():
        style = pulse.params.get("style")
        envname = pulse.params.get("envelope")
        for w in pulse.waveforms:
            wave_owner[w.name] = (pname, style, envname)

    waveforms, wf_names = [], {}
    envelopes_used = {}

    def waveform_for(wave, gen_ch):
        if wave.name in wf_names:
            return wf_names[wave.name]
        g = soccfg["gens"][gen_ch]
        _, style, envname = wave_owner[wave.name]
        if style == "const":
            wf = {"name": wave.name, "kind": "const",
                  "amplitude": _rat(Fraction(int(wave["gain"]), g["maxv"]))}
        elif style == "arb":
            data = prog.envelopes[gen_ch]["envs"][envname]["data"]
            wf = {"name": wave.name, "kind": "samples",
                  "full_scale": g["maxv"],
                  "i": [int(v) for v in data[:, 0]],
                  "q": [int(v) for v in data[:, 1]]}
            envelopes_used[wave.name] = envname
        else:
            raise NotImplementedError(f"style {style!r} not in v0 exporter scope")
        waveforms.append(wf)
        wf_names[wave.name] = wf["name"]
        return wf["name"]

    # instruction walk ----------------------------------------------------
    elements = []
    next_id = 0

    def emit(**el):
        nonlocal next_id
        el["id"] = next_id
        next_id += 1
        elements.append(el)
        return el

    def advance_to(st, start_units):
        if start_units < st.cursor:
            raise NotImplementedError(
                f"overlapping elements on {st.name} "
                f"({start_units} < cursor {st.cursor})")
        if start_units > st.cursor:
            emit(kind="delay", frame=st.name, duration=start_units - st.cursor)
        st.cursor = start_units

    def set_frame(st, freq_hz, phase_turns):
        if freq_hz != st.freq:
            emit(kind="set_frequency", frame=st.name, frequency=_rat(freq_hz))
            st.freq = freq_hz
        if phase_turns != st.phase:
            delta = (phase_turns - st.phase) % 1
            emit(kind="shift_phase", frame=st.name, phase=_rat(delta))
            st.phase = phase_turns

    ref_ticks = 0
    for ins in prog.prog_list:
        cmd = ins["CMD"]
        if cmd == "TIME" and ins.get("C_OP") == "inc_ref":
            ref_ticks += int(ins["LIT"].lstrip("#"))
        elif cmd == "WPORT_WR":
            t_abs = ref_ticks + int(ins["TIME"].lstrip("@"))
            wave = prog.waves[int(ins["ADDR"].lstrip("&"))]
            port = ins["DST"]
            if port in gen_key:
                ch = gen_key[port]
                st = states[("gen", ch)]
                dur_grid, sched_grid = st.grids
                g = soccfg["gens"][ch]
                advance_to(st, t_abs * sched_grid)
                set_frame(st, _reg_to_freq_hz(int(wave["freq"]), g["f_dds"]),
                          _reg_to_turns(int(wave["phase"])))
                emit(kind="play", frame=st.name,
                     waveform=waveform_for(wave, ch),
                     duration=int(wave["length"]) * dur_grid)
                st.cursor += int(wave["length"]) * dur_grid
            elif port in ro_by_ctrl:
                ch = ro_by_ctrl[port]
                st = states[("ro", ch)]
                r = soccfg["readouts"][ch]
                set_frame(st, _reg_to_freq_hz(int(wave["freq"]), r["f_dds"]),
                          _reg_to_turns(int(wave["phase"])))
            else:
                raise NotImplementedError(f"WPORT_WR to unmapped port {port}")
        elif cmd == "TRIG" and ins.get("SRC") == "set":
            port = ins["DST"]
            if port in ro_by_trig:
                ch = ro_by_trig[port]
                st = states[("ro", ch)]
                dur_grid, sched_grid = st.grids
                t_abs = ref_ticks + int(ins["TIME"].lstrip("@"))
                advance_to(st, t_abs * sched_grid)
                dur = int(prog.ro_chs[ch]["length"]) * dur_grid
                emit(kind="capture", frame=st.name, duration=dur)
                st.cursor += dur

    return {
        "format": "qconform-program",
        "format_version": 0,
        "channels": channels,
        "frames": frames,
        "waveforms": waveforms,
        "elements": elements,
    }
