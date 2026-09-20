"""Probe definitions for the Qblox survey.

A probe is data: the clocks it declares and the operations it schedules,
each at an absolute start time. runner.py builds the Schedule from it. Values
are in the units a Qblox user writes: ns for time, Hz, degrees, and
amplitude as a fraction of full scale.

Every probe asks about one parameter of one operation, the "probed" one.
The runner reads that parameter back from the compiled program and compares
it with what was requested.
"""

DRIVE = ("q0:mw", "q0.01", 100e6)      # port, clock, clock frequency
READOUT = ("q0:res", "q0.ro", 50e6)


def square(t, dur, amp=0.5, where=DRIVE, probed=False):
    return {"op": "square", "port": where[0], "clock": where[1],
            "t": t, "duration": dur, "amplitude": amp, "probed": probed}


def numerical(t, samples, where=DRIVE, probed=False):
    """A waveform of len(samples) samples at 1 ns each.

    NumericalPulse takes sample times, and its duration is the last time, so
    N samples need N + 1 time points. The last value repeats the one before
    and is never played."""
    return {"op": "numerical", "port": where[0], "clock": where[1], "t": t,
            "samples": list(samples), "probed": probed}


def set_frequency(t, freq, where=DRIVE, probed=True):
    return {"op": "set_frequency", "clock": where[1], "t": t, "frequency": freq,
            "probed": probed}


def shift_phase(t, degrees, where=DRIVE, probed=True):
    return {"op": "shift_phase", "clock": where[1], "t": t, "phase": degrees,
            "probed": probed}


def acquire(t, dur, where=READOUT, probed=False):
    return {"op": "acquire", "port": where[0], "clock": where[1], "t": t,
            "duration": dur, "probed": probed}


def probe(axis, param, requested, note, ops, clocks=None, clock_phases=None):
    return {"axis": axis, "param": param, "requested": requested, "note": note,
            "clocks": clocks or {DRIVE[1]: DRIVE[2], READOUT[1]: READOUT[2]},
            "clock_phases": clock_phases or {}, "ops": ops}


def length_probes():
    out = []
    for d, note in ((-4, "negative length"), (0, "zero length"), (1, "1 ns"),
                    (2, "2 ns"), (3, "3 ns, below the 4 ns instruction slot"),
                    (4, "4 ns, the instruction slot"), (5, "5 ns"),
                    (20, "20 ns"), (20.5, "20.5 ns, off the 1 ns grid"),
                    (20.25, "20.25 ns, off the 1 ns grid"),
                    (2999, "2999 ns, one below the offset threshold"),
                    (3000, "3000 ns, long square uses the offset path"),
                    (65535, "65535 ns, 16-bit field max"),
                    (65536, "65536 ns, one past the 16-bit field"),
                    (100000, "100000 ns, longer than one instruction"),
                    (16777216, "2**24 ns")):
        out.append(probe("length", "duration", d, note,
                         [square(0, d, probed=True)]))
    # a short pulse is fine when the next operation starts 4 ns after it
    for d, gap in ((3, 1), (2, 2), (1, 3), (3, 0)):
        out.append(probe("length", "duration", d,
                         f"{d} ns pulse, next pulse starts {d + gap} ns after it",
                         [square(0, d, probed=True), square(d + gap, 20, 0.3)]))
    return out


def timing_probes():
    out = []
    for t, note in ((0, "start 0"), (1, "start 1 ns"), (3, "start 3 ns"),
                    (4, "start 4 ns"), (0.5, "start 0.5 ns, off the 1 ns grid"),
                    (1.5, "start 1.5 ns, off the 1 ns grid"),
                    (-4, "negative start")):
        out.append(probe("timing", "start", t, note, [square(t, 20, probed=True)]))
    for gap in (0, 1, 2, 3, 4, 8):
        out.append(probe("timing", "start", 20 + gap,
                         f"second pulse {gap} ns after the first ends",
                         [square(0, 20), square(20 + gap, 20, 0.3, probed=True)]))
    out.append(probe("timing", "start", 10, "second pulse overlaps the first by 10 ns",
                     [square(0, 20), square(10, 20, 0.3, probed=True)]))
    out.append(probe("timing", "start", 0, "two pulses at the same time on one port",
                     [square(0, 20), square(0, 20, 0.3, probed=True)]))
    return out


FREQS = ((0, "0 Hz"), (100e6, "100 MHz"), (100e6 + 0.1, "100 MHz + 0.1 Hz"),
         (100e6 + 0.125, "100 MHz + 0.125 Hz"), (100e6 + 0.25, "100 MHz + 0.25 Hz, one step"),
         (100e6 + 0.3, "100 MHz + 0.3 Hz"), (-100e6, "-100 MHz"),
         (500e6, "500 MHz, the NCO maximum"), (500e6 + 0.25, "500 MHz + one step"),
         (500e6 + 1, "500 MHz + 1 Hz"), (-500e6, "-500 MHz, the NCO minimum"),
         (-500e6 - 1, "-500 MHz - 1 Hz"), (600e6, "600 MHz"), (1e9, "1 GHz"))


def freq_probes():
    out = []
    for f, note in FREQS:
        out.append(probe("freq", "clock_frequency", f, f"clock at {note}",
                         [square(0, 20, probed=True)],
                         clocks={DRIVE[1]: f, READOUT[1]: READOUT[2]}))
    for f, note in FREQS:
        out.append(probe("freq", "set_frequency", f, f"SetClockFrequency to {note}",
                         [square(0, 20), set_frequency(20, f), square(24, 20, 0.3)]))
    return out


def phase_probes():
    out = []
    # a clock resource takes a phase; does the backend use it
    for p, note in ((45, "45 deg"), (10.0000001, "10.0000001 deg")):
        out.append(probe("phase", "clock_phase", p, f"ClockResource phase {note}",
                         [square(0, 20, probed=True)],
                         clocks={DRIVE[1]: DRIVE[2], READOUT[1]: READOUT[2]},
                         clock_phases={DRIVE[1]: p}))
    for p, note in ((0, "0 deg"), (10, "10 deg"), (10.0000001, "10.0000001 deg"),
                    (360 / 1e9, "one NCO phase step"), (180 / 1e9, "half a step"),
                    (359.9999999, "359.9999999 deg"), (360, "360 deg"),
                    (720, "720 deg"), (-10, "-10 deg"), (1e-7, "1e-7 deg")):
        out.append(probe("phase", "phase_shift", p, f"ShiftClockPhase {note}",
                         [square(0, 20), shift_phase(20, p), square(24, 20, 0.3)]))
    return out


def gain_probes():
    out = []
    for a, note in ((0, "zero"), (0.5, "0.5"), (1 / 3, "1/3"), (0.3, "0.3"),
                    (1.0, "full scale"), (-1.0, "negative full scale"),
                    (1 - 2 ** -16, "1 - 2**-16"), (2 ** -15, "one LSB at 2**15"),
                    (2 ** -16, "half an LSB at 2**15"), (1 + 1e-9, "just over full scale"),
                    (1.2, "1.2"), (-1.2, "-1.2")):
        out.append(probe("gain", "amplitude", a, f"square amplitude {note}",
                         [square(0, 20, a, probed=True)]))
    for a, note in ((0.5, "0.5"), (1 / 3, "1/3"), (1.0, "full scale"), (1.2, "1.2")):
        out.append(probe("gain", "amplitude", a, f"long square (offset path) amplitude {note}",
                         [square(0, 3000, a, probed=True)]))
    return out


def envelope_probes():
    out = []
    # 5 through 7 and 9 are what tell a sample grid of 4 from a minimum of 4
    # samples. Without them the accepted counts are all multiples of 4 and the
    # grid the descriptor declares is a guess. 1 and 3 do not decide it: they
    # are refused for the 4 ns wait between operations, which is start_spacing.
    for n, note in ((1, "1 sample"), (3, "3 samples"), (4, "4 samples"),
                    (5, "5 samples"), (6, "6 samples"), (7, "7 samples"),
                    (8, "8 samples"), (9, "9 samples"), (12, "12 samples"),
                    (16384, "16384 samples, the waveform memory"),
                    (16385, "16385 samples, one past the waveform memory")):
        samples = [0.5] * n
        out.append(probe("envelope", "samples", n, f"numerical pulse, {note}",
                         [numerical(0, samples, probed=True)]))
    # the requested value is the largest magnitude, which becomes the gain
    ramp = [k / 8 for k in range(8)]
    out.append(probe("envelope", "envelope_scale", 7 / 8, "numerical ramp 0..7/8",
                     [numerical(0, ramp, probed=True)]))
    out.append(probe("envelope", "envelope_scale", 1 / 3, "numerical ramp 0..1/3",
                     [numerical(0, [k / 21 for k in range(8)], probed=True)]))
    for v, note in ((1.0, "full scale"), (1.2, "1.2"), (-1.0, "negative full scale")):
        out.append(probe("envelope", "envelope_scale", v, f"numerical pulse at {note}",
                         [numerical(0, [v] * 8, probed=True)]))
    # waveform memory is per sequencer: two distinct waveforms that fit alone
    out.append(probe("envelope", "samples", 16384 + 8,
                     "two waveforms, 16384 + 8 samples on one sequencer",
                     [numerical(0, [0.5] * 16384), numerical(16400, [0.25] * 8, probed=True)]))
    return out


def readout_probes():
    out = []
    for d, note in ((0, "zero"), (1, "1 ns"), (3, "3 ns"), (4, "4 ns"),
                    (100, "100 ns"), (1000, "1000 ns"), (1000.5, "1000.5 ns"),
                    (16000000, "16000000 ns, the scheduler maximum"),
                    (16000001, "16000001 ns"),
                    (16777212, "16777212 ns, the validator maximum"),
                    (16777216, "16777216 ns")):
        out.append(probe("readout", "integration_length", d, f"acquisition of {note}",
                         [acquire(0, d, probed=True)]))
    for gap in (299, 300):
        out.append(probe("readout", "start", gap, f"second acquisition {gap} ns after the first",
                         [acquire(0, 100), acquire(gap, 100, probed=True)]))
    # the integration length validator, reached only when the acquisition is
    # not also the last operation: a long pulse on the drive port keeps the
    # schedule running past it
    for d in (1, 3, 4, 5):
        out.append(probe("readout", "integration_length", d,
                         f"acquisition of {d} ns while the drive port runs 100 ns",
                         [acquire(0, d, probed=True), square(0, 100, 0.2)]))
    # an acquisition instruction occupies 4 ns whatever its integration
    # length, and the wait that follows is 0 or at least 4 ns
    for gap in (4, 5, 7, 8):
        out.append(probe("readout", "start", gap,
                         f"readout pulse {gap} ns after an acquisition starts",
                         [acquire(0, 100, probed=True),
                          square(gap, 20, 0.2, where=READOUT)]))
    for d in (4, 5, 7, 8, 100):
        out.append(probe("readout", "integration_length", d,
                         f"acquisition of {d} ns is the last operation",
                         [square(0, 20, 0.2), acquire(20, d, probed=True)]))
    # one integration length per sequencer
    for d1, d2 in ((100, 100), (100, 200), (200, 100)):
        out.append(probe("readout", "integration_length", d2,
                         f"acquisitions of {d1} then {d2} ns on one sequencer",
                         [acquire(0, d1), acquire(1000, d2, probed=True)]))
    return out


def budget_probes():
    out = []
    # each square is two instructions (set_awg_gain, play); the QCM holds 16384
    for n in (1000, 8000, 8200):
        ops = [square(24 * k, 20, 0.5 if k % 2 else 0.25) for k in range(n)]
        ops[-1]["probed"] = True
        out.append(probe("budget_instr", "n_pulses", n, f"{n} square pulses in a row", ops))
    # the QRM holds 12288
    for n in (6100, 6200):
        ops = [square(24 * k, 20, 0.5 if k % 2 else 0.25, where=READOUT) for k in range(n)]
        ops[-1]["probed"] = True
        out.append(probe("budget_instr", "n_pulses", n, f"{n} readout-port pulses in a row", ops))
    # the limit is per sequencer: two clocks on one port, 8000 pulses each
    clocks = {DRIVE[1]: DRIVE[2], DRIVE2[1]: DRIVE2[2], READOUT[1]: READOUT[2]}
    ops = []
    for k in range(8000):
        ops.append(square(24 * k, 20, 0.5 if k % 2 else 0.25))
        ops.append(square(24 * k, 20, 0.5 if k % 2 else 0.25, where=DRIVE2))
    ops[-1]["probed"] = True
    out.append(probe("budget_instr", "n_pulses", 8000,
                     "8000 pulses on each of two clocks of one port", ops, clocks=clocks))
    # waveform memory is per sequencer too
    out.append(probe("budget_instr", "samples", 16384,
                     "16384-sample waveform on each of two clocks of one port",
                     [numerical(0, [0.5] * 16384),
                      numerical(0, [0.25] * 16384, where=DRIVE2, probed=True)],
                     clocks=clocks))
    return out


def cost_probes():
    """Instructions each kind of element costs, across durations. The
    readback is the length of the probed sequencer's loop body in
    instructions; the baseline row is one 20 ns pulse."""
    out = [probe("cost", "instructions", "baseline", "one 20 ns pulse",
                 [square(0, 20, probed=True)])]
    for d in (20, 3000, 65535, 65536, 131072, 1000000, 16777216):
        out.append(probe("cost", "instructions", f"square {d}", f"one {d} ns square pulse",
                         [square(0, d, probed=True)]))
    for gap in (100, 65535, 65536, 131072, 1000000, 16777216):
        out.append(probe("cost", "instructions", f"gap {gap}",
                         f"two 20 ns pulses with a {gap} ns gap",
                         [square(0, 20), square(20 + gap, 20, 0.3, probed=True)]))
    for n in (8, 1000, 16384):
        out.append(probe("cost", "instructions", f"numerical {n}",
                         f"one {n}-sample numerical pulse",
                         [numerical(0, [0.5] * n, probed=True)]))
    out.append(probe("cost", "instructions", "set_frequency",
                     "pulse, SetClockFrequency, pulse",
                     [square(0, 20), set_frequency(20, 120e6, probed=False),
                      square(24, 20, 0.3, probed=True)]))
    out.append(probe("cost", "instructions", "shift_phase",
                     "pulse, ShiftClockPhase, pulse",
                     [square(0, 20), shift_phase(20, 10, probed=False),
                      square(24, 20, 0.3, probed=True)]))
    for d in (100, 1000000):
        out.append(probe("cost", "instructions", f"acquire {d}", f"one {d} ns acquisition",
                         [acquire(0, d, probed=True)]))
    return out


DRIVE2 = ("q0:mw", "q0.12", 150e6)     # a second clock on the drive port


def spacing_probes():
    """Where the 4 ns between operation starts applies, and to what."""
    out = []
    clocks = {DRIVE[1]: DRIVE[2], DRIVE2[1]: DRIVE2[2], READOUT[1]: READOUT[2]}
    # an NCO update between two pulses, at several distances from the second
    for gap in (0, 1, 2, 3, 4):
        out.append(probe("spacing", "start", 24 + 20,
                         f"SetClockFrequency {gap} ns before the next pulse",
                         [square(0, 20), set_frequency(24 + 20 - gap, 120e6, probed=False),
                          square(24 + 20, 20, 0.3, probed=True)]))
        out.append(probe("spacing", "start", 24 + 20,
                         f"ShiftClockPhase {gap} ns before the next pulse",
                         [square(0, 20), shift_phase(24 + 20 - gap, 90, probed=False),
                          square(24 + 20, 20, 0.3, probed=True)]))
    for gap in (1, 3, 4):
        out.append(probe("spacing", "start", 24 + 20 + gap,
                         f"two SetClockFrequency {gap} ns apart",
                         [square(0, 20), set_frequency(24, 120e6, probed=False),
                          set_frequency(24 + gap, 130e6, probed=False),
                          square(24 + 20 + gap, 20, 0.3, probed=True)]))
    # NCO updates at the same time: frequency and phase keep separate tracks
    for a_op, b_op, note in (
            (set_frequency(24, 120e6, probed=False), set_frequency(24, 130e6, probed=False),
             "two SetClockFrequency at the same time"),
            (shift_phase(24, 10, probed=False), shift_phase(24, 20, probed=False),
             "two ShiftClockPhase at the same time"),
            (set_frequency(24, 120e6, probed=False), shift_phase(24, 20, probed=False),
             "SetClockFrequency and ShiftClockPhase at the same time")):
        out.append(probe("spacing", "start", 24, note,
                         [square(0, 20), a_op, b_op, square(24, 20, 0.3, probed=True)]))
    # a pulse right at the end of the schedule, when another port runs longer
    for d in (1, 2, 3):
        out.append(probe("spacing", "duration", d,
                         f"{d} ns drive pulse while the readout port runs 100 ns",
                         [square(0, d, probed=True),
                          square(0, 100, 0.2, where=READOUT)]))
    # two clocks on one port are two sequencers; do they constrain each other
    for t2 in (0, 1, 10):
        out.append(probe("spacing", "start", t2,
                         f"pulses on two clocks of one port, {t2} ns apart",
                         [square(0, 20), square(t2, 20, 0.3, where=DRIVE2, probed=True)],
                         clocks=clocks))
    # a capture and a readout pulse on the same sequencer
    for gap in (0, 1, 3, 4):
        out.append(probe("spacing", "start", 100 + gap,
                         f"acquisition {gap} ns after a readout pulse starts at 100",
                         [square(0, 100, 0.2, where=READOUT),
                          square(100, 200, 0.2, where=READOUT),
                          acquire(100 + gap, 1000, probed=True)]))
    return out


def build_probes():
    return (length_probes() + timing_probes() + freq_probes() + phase_probes()
            + gain_probes() + envelope_probes() + readout_probes() + budget_probes()
            + spacing_probes() + cost_probes())
