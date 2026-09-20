"""Read a compiled Qblox program back and say what the toolchain changed.

Every quantity is compared in the units the program asked in, against the
step the sequencer quantizes it to: 1 ns for time, 1/4 Hz for the NCO
frequency, 1e-9 turns for phase, 1/32768 for gain. The names are the ones
triage compares across vendors: start_time, total_length, freq, phase, gain.

What the compiled program shows:

  play a,b,d        a waveform play. Its length is the waveform's sample
                    count, not the instruction's third argument, which is
                    the time until the next instruction
  set_awg_offs      a long square, which the scheduler plays as a DC offset
                    followed by a closing waveform
  set_awg_gain      the amplitude of the play that follows
  set_freq          an NCO frequency update, in 1/4 Hz
  set_ph_delta      an NCO phase update, in 1e-9 turns. Updates at one time
                    are merged into their sum
  acquire           a capture. Its length is the sequencer's
                    integration_length_acq setting

The initial NCO frequency is a setting rather than an instruction, and the
dummy keeps the float it was given, so its quantization is not observable
here. The plan records which frames that applies to.
"""

from fractions import Fraction

from oracle.base import round_half_even, same_grid_cell
from oracle.qblox.lower import FREQ_STEP, GAIN_STEP, NS, PHASE_STEP
from oracle.qblox.toolchain import timeline

GAIN_FULL_SCALE = 32768


def observe(result, plan):
    """(changed, registers) for a compiled plan."""
    changed = []
    registers = {}
    for frame in sorted(plan.expect):
        key = f"{plan.ports[frame]}-{frame}"
        if key not in result.portclocks:
            changed.append({"quantity": "start_time", "frame": frame,
                            "requested": "a sequencer", "readback": "none"})
            continue
        module, seq = result.portclocks[key]
        st = result.compiled[module]["sequencers"][seq]
        events = timeline(st.sequence["program"])
        waves = {w["index"]: w["data"] for w in st.sequence["waveforms"].values()}
        registers[frame] = [f"{e['t']}:{e['op']} {','.join(e['args'])}".strip()
                            for e in events]
        changed += frame_changes(frame, plan, st, events, waves)
    return changed, registers


def frame_changes(frame, plan, st, events, waves):
    changed = []
    by_time = {}
    for e in events:
        by_time.setdefault(e["t"], []).append(e)

    # phase updates at one time are merged, so the expectation is their sum
    phase_at = {}
    for entry in plan.expect[frame]:
        if entry["kind"] == "phase":
            t = ns_of(entry["t"])
            phase_at[t] = phase_at.get(t, Fraction(0)) + entry["turns"]

    seen_phase = set()
    for entry in plan.expect[frame]:
        t = ns_of(entry["t"])
        here = by_time.get(t, [])
        kind = entry["kind"]

        if kind == "phase":
            if t in seen_phase:
                continue
            seen_phase.add(t)
            got = [int(e["args"][0]) for e in here if e["op"] == "set_ph_delta"]
            want = phase_at[t]
            readback = Fraction(got[0]) * PHASE_STEP if got else Fraction(0)
            if not same_grid_cell(want, readback, PHASE_STEP, "phase"):
                changed.append(change("phase", frame, entry, want, readback))
        elif kind == "freq":
            got = [int(e["args"][0]) for e in here if e["op"] == "set_freq"]
            if not got:
                changed.append(change("freq", frame, entry, entry["hz"], "none"))
            else:
                readback = Fraction(got[0]) * FREQ_STEP
                if not same_grid_cell(entry["hz"], readback, FREQ_STEP, "freq"):
                    changed.append(change("freq", frame, entry, entry["hz"], readback))
        elif kind == "capture":
            if not [e for e in here if e["op"].startswith("acquire")]:
                changed.append(change("start_time", frame, entry, t, "no acquire"))
                continue
            length = Fraction(st.integration_length_acq) * NS
            if not same_grid_cell(entry["duration"], length, NS, "total_length"):
                changed.append(change("total_length", frame, entry, entry["duration"], length))
        else:
            changed += play_changes(frame, entry, t, here, by_time, waves)
    return changed


def play_changes(frame, entry, t, here, by_time, waves):
    """A play: did it start where it was asked to, last as long, and keep its
    amplitude."""
    out = []
    gain_quantum = round_half_even(entry["gain"] / GAIN_STEP)
    if gain_quantum == 0:
        # below half a gain step the scheduler drops the waveform, which is
        # the same as rounding the amplitude to zero
        if not [e for e in here if e["op"] in ("play", "set_awg_offs")]:
            if entry["gain"] != 0:
                out.append(change("gain", frame, entry, entry["gain"], 0))
            return out

    plays = [e for e in here if e["op"] == "play"]
    offs = [e for e in here if e["op"] == "set_awg_offs" and e["args"][0] != "0"]
    if not plays and not offs:
        out.append(change("start_time", frame, entry, t, "no play"))
        return out

    gains = [int(e["args"][0]) for e in here if e["op"] in ("set_awg_gain", "set_awg_offs")]
    if gains:
        readback = Fraction(gains[0], GAIN_FULL_SCALE)
        if not same_grid_cell(entry["gain"], readback, GAIN_STEP, "gain"):
            out.append(change("gain", frame, entry, entry["gain"], readback))

    length = play_length(t, plays, offs, by_time, waves)
    if length is not None and not same_grid_cell(entry["duration"], length, NS, "total_length"):
        out.append(change("total_length", frame, entry, entry["duration"], length))
    return out


def play_length(t, plays, offs, by_time, waves):
    """How long the pulse starting at t lasts, in seconds.

    A short square or a numerical pulse is one play of a waveform, and its
    length is the sample count. A long square is a DC offset that runs until
    a closing play, so it lasts from the offset to the end of that play."""
    if plays:
        return Fraction(len(waves.get(int(plays[0]["args"][0]), []))) * NS
    tail = None
    for when in sorted(by_time):
        if when < t:
            continue
        for e in by_time[when]:
            if e["op"] == "play":
                tail = e
                break
        if tail is not None:
            break
    if tail is None:
        return None
    samples = len(waves.get(int(tail["args"][0]), []))
    return Fraction(tail["t"] - t + samples) * NS


def ns_of(seconds):
    """A time in seconds as whole nanoseconds. A time off the grid never
    reaches a compiled program: the toolchain refuses it."""
    return round_half_even(seconds / NS)


def change(quantity, frame, entry, requested, readback):
    return {"quantity": quantity, "frame": frame, "element": entry["element"],
            "requested": str(requested), "readback": str(readback)}
