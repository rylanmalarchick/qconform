"""Build qconform capability descriptors from QICK configs + survey catalog.

Parameters come from the config JSON; constraints come from a curated
table below, every entry carrying evidence references into the phase-2
catalog (tools/survey/catalog). check_descriptor.py enforces that every
reference resolves to a real catalog row -- the phase 3 gate.

Exact arithmetic convention: vendor clock floats (245.76, 599.04 MHz...)
are decimal-intended values; Fraction(str(x)) recovers them exactly and
float round-trip equality is asserted. No float survives into output.

Time model per channel: unit dt = 1 / lcm(f_channel, f_time) so that
one channel clock cycle = duration_grid * dt and one tProc timing tick
= schedule_grid * dt, both exact integers. This is what makes QICK's
two-clock reality (durations on the fabric clock, schedule times on the
tProc clock) representable without floats.

Usage: python build_descriptor.py   (writes descriptors/*.json)
"""

import json
from fractions import Fraction
from math import lcm
from pathlib import Path

HERE = Path(__file__).parent
CONFIGS = HERE.parent / "survey" / "configs"
OUT = HERE / "descriptors"

QICK_LIB_VERSION = "0.2.418"   # the surveyed library; oracle = f(config, library)

B_DDS = 32
B_PHASE = 32


def frac_mhz(x):
    """Vendor float MHz -> exact Fraction, asserting decimal intent."""
    f = Fraction(str(x))
    if float(f) != x:
        raise ValueError(f"clock {x} is not a clean decimal; cannot take as exact")
    return f


def rat(f):
    f = Fraction(f)
    return {"num": f.numerator, "den": f.denominator}


def time_unit(f_channel_mhz, f_time_mhz):
    """Return (unit_seconds, duration_grid, schedule_grid)."""
    f1, f2 = frac_mhz(f_channel_mhz), frac_mhz(f_time_mhz)
    L = Fraction(lcm(f1.numerator * f2.denominator, f2.numerator * f1.denominator),
                 f1.denominator * f2.denominator)
    dur = L / f1
    sched = L / f2
    assert dur.denominator == 1 and sched.denominator == 1
    return Fraction(1, 1) / (L * 10**6), int(dur), int(sched)


def ev(config, axis, note_contains, outcome):
    return {"config": config, "axis": axis,
            "note_contains": note_contains, "outcome": outcome}


# evidence config per gen type: where the survey exercised that class
EV_CFG = {
    "axis_signal_gen_v6": "zcu216-testbench",
    "axis_sg_int4_v2": "zcu216-qce2025-r26",
    "axis_sg_mixmux8_v1": "zcu216-qce2025-r26",
}


def gen_channel(i, g, f_time):
    gt = g["type"]
    ec = EV_CFG[gt]
    mux = "mux" in gt
    unit, dur_grid, sched_grid = time_unit(g["f_fabric"], f_time)
    f_dds_hz = frac_mhz(g["f_dds"]) * 10**6
    freq_res = f_dds_hz / 2**B_DDS

    ch = {
        "name": f"gen{i}",
        "kind": "drive",
        "vendor_type": gt,
        "unit": rat(unit),
        "duration_grid": dur_grid,
        "schedule_grid": sched_grid,
        "capabilities": {"phrst": gt != "axis_sg_mixmux8_v1"},
        "constraints": [],
        "vendor_behavior": [],
    }
    cons = ch["constraints"]
    vb = ch["vendor_behavior"]

    # The mux pulse-length check in the vendor allows up to 2**32, but the
    # tProc time immediate is a signed 32-bit field, so the value that must
    # fit is 2**31 - 1. Declaring 2**32 - 1 passed three programs the
    # toolchain refuses; the survey's own max-boundary row rejected and was
    # recorded as a vendor quirk instead of correcting the limit.
    max_cycles = 2**31 - 1 if mux else 2**16 - 1
    cons.append({
        "id": "pulse_length_range", "quantity": "time", "shape": "range_units",
        "severity": "fatal",
        "min_units": 3 * dur_grid, "max_units": max_cycles * dur_grid,
        "evidence": [ev(ec, "length", "min boundary", "accept"),
                     ev(ec, "length", "below min 3 cycles", "reject"),
                     ev(ec, "length", "first over max", "reject")],
    })
    cons.append({
        "id": "pulse_length_grid", "quantity": "time", "shape": "range_units",
        "severity": "vendor_repairable",
        "evidence": [ev(ec, "length", "fraction 20+0.25 cycles", "accept_round")]
        if not mux else [ev(ec, "length", "nominal", "accept")],
    })

    if not mux:
        if g["interpolation"] != 1:
            cons.append({
                "id": "frequency_range", "quantity": "frequency",
                "shape": "range_resolution", "severity": "fatal",
                "min": rat(-f_dds_hz / 2), "max": rat(f_dds_hz / 2),
                "resolution": rat(freq_res), "post_mixer": True,
                "evidence": [ev(ec, "freq", "just under lower edge", "reject"),
                             ev(ec, "freq", "1.5x f_dds", "reject")],
            })
        else:
            cons.append({
                "id": "frequency_range", "quantity": "frequency",
                "shape": "range_resolution", "severity": "vendor_repairable",
                "min": rat(-f_dds_hz / 2), "max": rat(f_dds_hz / 2),
                "resolution": rat(freq_res), "post_mixer": False,
                "evidence": [ev(ec, "freq", "quantization +0.3 step", "accept_round")],
            })
            vb.append({
                "id": "frequency_alias_mod_f_dds",
                "vendor_action": "out-of-band frequency silently aliases mod 2**32 "
                                 "DDS units; readback hides the wrap",
                "qconform_severity": "vendor_repairable",
                "semantics_preserving": False,
                "evidence": [ev(ec, "freq", "1.5x f_dds", "accept"),
                             ev(ec, "freq", "full f_dds", "accept")],
            })
        cons.append({
            "id": "phase_resolution", "quantity": "phase",
            "shape": "range_resolution", "severity": "vendor_repairable",
            "resolution": rat(Fraction(1, 2**B_PHASE)),
            "evidence": [ev(ec, "phase", "quantization +0.3 step", "accept_round")],
        })
        cons.append({
            "id": "amplitude_range", "quantity": "amplitude",
            "shape": "range_resolution", "severity": "vendor_repairable",
            "min": rat(Fraction(-1)), "max": rat(Fraction(1)),
            # The gain register is trunc(gain * maxv * maxv_scale), so the
            # achievable step is 1/(maxv * maxv_scale) and not 1/maxv. An
            # interpolated generator reports maxv_scale 0.9 while a v6 reports
            # 1.0, so reading maxv alone gave every int4 channel the v6 number
            # and the checker predicted no repair where the toolchain rounds.
            "resolution": rat(Fraction(1, g["maxv"])
                              / Fraction(str(g.get("maxv_scale", 1.0)))),
            # A scaled channel skips registers as k walks, so its gain_lsb
            # rows round. An unscaled one advances every step and they accept.
            # Cite the outcome the channel actually produces.
            "evidence": [ev(ec, "gain", "raw +0.6 (trunc vs round)", "accept_round"),
                         ev(ec, "gain_lsb", "of maxv, maxv_scale",
                            "accept_round"
                            if Fraction(str(g.get("maxv_scale", 1.0))) != 1
                            else "accept")],
        })
        vb.append({
            "id": "gain_over_full_scale",
            "vendor_action": "gain beyond +/-1.0 accepted; raw register exceeds "
                             "maxv (hardware behavior undetermined, suspected wrap)",
            "qconform_severity": "vendor_repairable",
            "semantics_preserving": False,
            "evidence": [ev(ec, "gain", "well over full scale", "accept")],
        })
    else:
        ch["capabilities"]["n_tones"] = g["n_tones"]

        # A mux pulse carries only style, mask and length, so frequency, gain
        # and phase belong to the tone table and are checked per tone.
        #
        # FATAL, and the direction matters. Above the band the vendor refuses.
        # Below it the vendor folds the tone onto its Nyquist image and
        # compiles, which is recorded below as a vendor_behavior. Declaring
        # this vendor_repairable would pass programs the vendor refuses.
        cons.append({
            "id": "frequency_range", "quantity": "frequency",
            "shape": "range_resolution", "severity": "fatal",
            "min": rat(-f_dds_hz / 2), "max": rat(f_dds_hz / 2),
            "resolution": rat(freq_res), "post_mixer": True,
            "evidence": [ev(ec, "mux", "tone just over upper edge", "reject"),
                         ev(ec, "mux", "tone at 1.5x f_dds", "reject"),
                         ev(ec, "mux", "tone quantization +0.3 step",
                            "accept_round")],
        })
        cons.append({
            "id": "phase_resolution", "quantity": "phase",
            "shape": "range_resolution", "severity": "vendor_repairable",
            "resolution": rat(Fraction(1, 2**B_PHASE)),
            "evidence": [ev(ec, "mux", "tone phase quantization +0.3 step",
                            "accept_round")],
        })
        # The mux gain register is round(gain * maxv). It rounds and applies
        # no maxv_scale, where the non-mux path truncates and applies one, so
        # the step is 1/maxv and this channel does not share it with an int4
        # that reports the same maxv.
        cons.append({
            "id": "amplitude_range", "quantity": "amplitude",
            "shape": "range_resolution", "severity": "vendor_repairable",
            "min": rat(Fraction(-1)), "max": rat(Fraction(1)),
            "resolution": rat(Fraction(1, g["maxv"])),
            "evidence": [ev(ec, "mux", "tone k=1 of maxv (mux gain lsb)",
                            "accept"),
                         ev(ec, "mux", "tone raw +0.6 (trunc vs round)",
                            "accept_round")],
        })
        # Both carry no parameter. The mask limit is the program's own tone
        # table length, and the tone count limit is capabilities.n_tones.
        cons.append({
            "id": "mux_tone_mask", "quantity": "count",
            "shape": "range_units", "severity": "fatal",
            "evidence": [ev(ec, "mux",
                            "mask tone index == n_tones with n_tones declared",
                            "reject"),
                         ev(ec, "mux", "mask names the last declared tone",
                            "accept")],
        })
        cons.append({
            "id": "mux_tone_count", "quantity": "count",
            "shape": "range_units", "severity": "fatal",
            "evidence": [ev(ec, "mux", "tone table exactly n_tones", "accept"),
                         ev(ec, "mux", "tone table over n_tones", "accept")],
        })
        vb.append({
            "id": "mux_tone_nyquist_image_fold",
            "vendor_action": "a tone below the lower band edge is remapped to "
                             "its Nyquist image and compiled; freq_rounded "
                             "reports the request, so the readback hides it",
            "qconform_severity": "fatal",
            "semantics_preserving": False,
            "evidence": [ev(ec, "mux", "tone just under lower edge",
                            "accept_round"),
                         ev(ec, "mux", "tone well under lower edge",
                            "accept_round")],
        })
        vb.append({
            "id": "mux_tone_count_unchecked",
            "vendor_action": "a tone table longer than n_tones compiles; "
                             "nothing compares the table against the hardware",
            "qconform_severity": "fatal",
            "semantics_preserving": False,
            "evidence": [ev(ec, "mux", "tone table over n_tones", "accept")],
        })
        vb.append({
            "id": "gain_over_full_scale",
            "vendor_action": "tone gain beyond +/-1.0 accepted; raw register "
                             "exceeds maxv (hardware behavior undetermined)",
            "qconform_severity": "vendor_repairable",
            "semantics_preserving": False,
            "evidence": [ev(ec, "mux", "tone gain over full scale", "accept")],
        })
        vb.append({
            "id": "mux_mask_duplicate_tone_ignored",
            "vendor_action": "a mask naming one tone twice compiles unchanged, "
                             "because the mask is a bitmask",
            "qconform_severity": "vendor_repairable",
            "semantics_preserving": True,
            "evidence": [ev(ec, "mux", "mask names one tone twice", "accept")],
        })

    if "maxlen" in g and not mux:
        # envelope sample rate = f_fabric * samps_per_clk (matches the dump's
        # envelope-us math on both v6 and int4; NOT the DAC fs for int4)
        ch["sample_unit"] = rat(Fraction(1, 1)
                                / (frac_mhz(g["f_fabric"]) * 10**6
                                   * g["samps_per_clk"]))
        ch["capabilities"].update({
            "envelope_memory_samples": g["maxlen"],
            "envelope_sample_grid": g["samps_per_clk"],
            "envelope_max_abs": g["maxv"],
        })
        cons.append({
            "id": "envelope_sample_grid", "quantity": "count",
            "shape": "grid_samples", "severity": "fatal",
            "grid": g["samps_per_clk"],
            "evidence": [ev(ec, "envelope", "length not multiple of samps_per_clk",
                            "reject")],
        })
        cons.append({
            "id": "envelope_amplitude", "quantity": "amplitude",
            "shape": "range_resolution", "severity": "fatal",
            "min": rat(Fraction(-g["maxv"], g["maxv"])),
            "max": rat(Fraction(g["maxv"], g["maxv"])),
            "resolution": rat(Fraction(1, g["maxv"])),
            "evidence": [ev(ec, "envelope", "envelope amplitude over maxv", "reject")],
        })
        vb.append({
            "id": "envelope_memory_overflow_unchecked",
            "vendor_action": "envelopes exceeding memory compile silently; failure "
                             "surfaces only at hardware load",
            "qconform_severity": "fatal",
            "semantics_preserving": False,
            "evidence": [ev(ec, "envelope", "envelope exceeds memory", "accept")],
        })

    vb.append({
        "id": "negative_time_accepted",
        "vendor_action": "negative pulse time accepted silently at compile",
        "qconform_severity": "fatal",
        "semantics_preserving": False,
        "evidence": [ev(EV_CFG["axis_signal_gen_v6"], "timing",
                        "negative pulse time", "accept")],
    })
    return ch


def ro_channel(i, r, f_time, config_name):
    unit, dur_grid, sched_grid = time_unit(r["f_output"], f_time)
    ch = {
        "name": f"ro{i}",
        "kind": "readout",
        "vendor_type": r["ro_type"],
        "unit": rat(unit),
        "duration_grid": dur_grid,
        "schedule_grid": sched_grid,
        "constraints": [],
    }
    if "tproc_ctrl" in r:  # dynamic readout: surveyed
        ch["constraints"].append({
            "id": "readout_length_range", "quantity": "time",
            "shape": "range_units", "severity": "fatal",
            "min_units": 3 * dur_grid, "max_units": (2**16 - 1) * dur_grid,
            "evidence": [ev("zcu216-testbench", "readout", "below min 3", "reject"),
                         ev("zcu216-testbench", "readout", "first over max", "reject")],
        })
    # static (pfb) readouts carry no surveyed constraints in v0: a checker
    # must report them as unchecked, not silently passed
    return ch


def build(config_name):
    cfg = json.loads((CONFIGS / f"{config_name}.json").read_text())
    tproc = cfg["tprocs"][0]
    f_time = tproc["f_time"]
    ec = "zcu216-testbench"  # budgets surveyed on every config; testbench rows cited

    return {
        "format": "qconform-descriptor",
        "format_version": 0,
        "identification": {
            "name": f"qick-{config_name}",
            "board": cfg["board"],
            "fw_timestamp": cfg["fw_timestamp"],
            "cfg_sw_version": cfg["sw_version"],
            "library": {"name": "qick", "version": QICK_LIB_VERSION},
            "descriptor_version": "0",
        },
        "frames": "compiled_away",
        "rounding": {
            "time": "nearest_half_even",
            "frequency": "nearest_half_even",
            "phase": "nearest_half_even",
            "amplitude": "trunc_toward_zero",
        },
        "channels": [gen_channel(i, g, f_time) for i, g in enumerate(cfg["gens"])]
                    + [ro_channel(i, r, f_time, config_name)
                       for i, r in enumerate(cfg["readouts"])],
        "budgets": [
            {"id": "pmem_words", "limit": tproc["pmem_size"],
             "cost_model": {"kind": "linear", "per_item": 1, "overhead": 14},
             "evidence": [ev(ec, "budget_pmem", "program memory", "reject")]},
            {"id": "wmem_words", "limit": tproc["wmem_size"],
             "cost_model": {"kind": "linear", "per_item": 1, "overhead": 0},
             "evidence": [ev(ec, "budget_wmem", "waveform memory", "reject")]},
            {"id": "loop_registers", "limit": tproc["dreg_qty"],
             "cost_model": {"kind": "indeterminate_band", "per_item": 1,
                            "reserved_min": 0, "reserved_max": 4},
             "evidence": [ev(ec, "budget_regs", "data registers", "reject")]},
        ],
    }


def main():
    OUT.mkdir(exist_ok=True)
    for name in ("zcu216-testbench", "zcu216-rb-r27", "zcu216-qce2025-r26"):
        out = OUT / f"qick-{name}-v0.json"
        out.write_text(json.dumps(build(name), indent=1) + "\n")
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
