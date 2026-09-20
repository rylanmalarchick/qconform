"""Build the qconform capability descriptor for a Qblox cluster from its
hardware compilation config and the Qblox survey catalog.

Every constraint cites catalog rows (tools/survey/catalog/qblox-*), and
check_descriptor.py resolves each citation. The numbers are the ones the
rows show and the ones qblox-scheduler 1.0.0b8 states in
backends/qblox/constants.py:

  time         1 ns grid for durations and start times, refused off grid
  spacing      4 ns between operation starts on a sequencer, 4 ns between
               frequency updates, 300 ns between acquisitions
  frequency    NCO band +-500 MHz inclusive, 0.25 Hz steps, round half even
  phase        1e9 steps per turn, round half even
  amplitude    1/32768 steps, round half even, +1.0 clamped to 32767/32768
  envelope     16384 samples per sequencer, 1 ns per sample
  program      16384 instructions per QCM sequencer, 12288 per QRM

A qconform frame is one port and one clock, which qblox-scheduler compiles
to one sequencer. That is why the spacing rules, the waveform memory and the
instruction budget are per frame.

Usage: python build_qblox.py   (writes descriptors/qblox-*.json)
"""

import json
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).parent
CONFIGS = HERE.parent / "survey" / "configs"
OUT = HERE / "descriptors"

SCHEDULER_VERSION = "1.0.0b8"     # the surveyed scheduler
INSTRUMENTS_VERSION = "1.3.1"     # the surveyed driver and assembler

NS = Fraction(1, 10**9)
NCO_FREQ_STEP = Fraction(1, 4)             # Hz
NCO_FREQ_LIMIT = 500 * 10**6               # Hz
NCO_PHASE_STEP = Fraction(1, 10**9)        # turns
GAIN_STEP = Fraction(1, 32768)
GAIN_SATURATE = Fraction(32767, 32768)
WAVEFORM_MEMORY = 16384                    # samples per sequencer
INSTRUCTIONS = {"QCM": 16384, "QRM": 12288}
# The scheduler counts the loop label as an instruction, so a program of one
# 20 ns pulse is 12: 10 of setup and loop, and 2 for the pulse.
PROGRAM_OVERHEAD = 10
# An output element is set_awg_gain then play, so it costs two instructions,
# not one. The budget_instr rows carry the assembler's own counts and every
# one of them fits 2n + PROGRAM_OVERHEAD: 8200 pulses refused at 16410 and
# 6200 readout-port pulses refused at 12410.
INSTRUCTIONS_PER_OUTPUT = 2
# The most instructions one element compiled to across the cost axis: a 1 ms
# acquisition. A long wait or square compiles to a loop, so the count stays
# bounded however long the element is.
PER_ELEMENT_MAX = 10
START_SPACING_NS = 4
FREQUENCY_UPDATE_SPACING_NS = 4
CAPTURE_SPACING_NS = 300
MIN_ACQUISITION_NS = 4                     # the integration_length validator
MAX_ACQUISITION_NS = 16_000_000


def rat(f):
    f = Fraction(f)
    return {"num": f.numerator, "den": f.denominator}


def ev(config, axis, note_contains, outcome):
    return {"config": config, "axis": axis,
            "note_contains": note_contains, "outcome": outcome}


def timing_constraints(cfg, capture):
    """The grid and spacing rules every sequencer carries."""
    cons = [
        {"id": "pulse_length_grid", "quantity": "time", "shape": "range_units",
         "severity": "fatal",
         "evidence": [ev(cfg, "readout" if capture else "length",
                         "1000.5 ns" if capture else "20.5 ns, off the 1 ns grid",
                         "reject")]},
        {"id": "schedule_grid", "quantity": "time", "shape": "range_units",
         "severity": "fatal",
         "evidence": [ev(cfg, "timing", "start 0.5 ns, off the 1 ns grid", "reject"),
                      ev(cfg, "timing", "start 1 ns", "accept")]},
        {"id": "start_spacing", "quantity": "time", "shape": "range_units",
         "severity": "fatal", "min_units": START_SPACING_NS,
         "initial_phase_update": True,
         "evidence": [ev(cfg, "spacing", "SetClockFrequency 1 ns before the next pulse", "reject"),
                      ev(cfg, "spacing", "ShiftClockPhase 3 ns before the next pulse", "reject"),
                      ev(cfg, "spacing", "SetClockFrequency 4 ns before the next pulse", "accept"),
                      ev(cfg, "spacing", "SetClockFrequency 0 ns before the next pulse", "accept"),
                      ev(cfg, "length", "3 ns pulse, next pulse starts 3 ns after it", "reject"),
                      ev(cfg, "length", "1 ns pulse, next pulse starts 4 ns after it", "accept"),
                      ev(cfg, "length", "3 ns, below the 4 ns instruction slot", "reject"),
                      ev(cfg, "spacing", "1 ns drive pulse while the readout port runs 100 ns",
                         "accept"),
                      ev(cfg, "spacing", "acquisition 1 ns after a readout pulse", "reject"),
                      ev(cfg, "phase", "ClockResource phase 45 deg", "accept_round")]},
        {"id": "frequency_update_spacing", "quantity": "time", "shape": "range_units",
         "severity": "fatal", "min_units": FREQUENCY_UPDATE_SPACING_NS,
         "evidence": [ev(cfg, "spacing", "two SetClockFrequency at the same time", "reject"),
                      ev(cfg, "spacing", "two SetClockFrequency 3 ns apart", "reject"),
                      ev(cfg, "spacing", "two SetClockFrequency 4 ns apart", "accept"),
                      ev(cfg, "spacing", "two ShiftClockPhase at the same time", "accept")]},
    ]
    return cons


def carrier_constraints(cfg):
    """Frequency and phase: the NCO of the sequencer."""
    return [
        {"id": "frequency_range", "quantity": "frequency", "shape": "range_resolution",
         "severity": "fatal",
         "min": rat(-NCO_FREQ_LIMIT), "max": rat(NCO_FREQ_LIMIT),
         "resolution": rat(NCO_FREQ_STEP),
         "evidence": [ev(cfg, "freq", "SetClockFrequency to 500 MHz + one step", "reject"),
                      ev(cfg, "freq", "SetClockFrequency to -500 MHz - 1 Hz", "reject"),
                      ev(cfg, "freq", "clock at 500 MHz + one step", "reject"),
                      ev(cfg, "freq", "clock at 500 MHz, the NCO maximum", "accept"),
                      ev(cfg, "freq", "SetClockFrequency to 100 MHz + 0.3 Hz", "accept_round"),
                      ev(cfg, "freq", "SetClockFrequency to 100 MHz + 0.25 Hz, one step",
                         "accept")]},
        {"id": "phase_resolution", "quantity": "phase", "shape": "range_resolution",
         "severity": "vendor_repairable", "resolution": rat(NCO_PHASE_STEP),
         "evidence": [ev(cfg, "phase", "ShiftClockPhase 10 deg", "accept_round"),
                      ev(cfg, "phase", "ShiftClockPhase one NCO phase step", "accept")]},
    ]


def output_constraints(cfg):
    """Amplitude and envelope rules of an output path."""
    return [
        {"id": "pulse_length_range", "quantity": "time", "shape": "range_units",
         "severity": "fatal", "min_units": 1,
         "evidence": [ev(cfg, "length", "zero length", "reject"),
                      ev(cfg, "length", "1 ns pulse, next pulse starts 4 ns after it", "accept")]},
        {"id": "amplitude_range", "quantity": "amplitude", "shape": "range_resolution",
         "severity": "fatal", "min": rat(-1), "max": rat(1),
         "resolution": rat(GAIN_STEP), "saturate_max": rat(GAIN_SATURATE),
         "evidence": [ev(cfg, "gain", "square amplitude 1.2", "reject"),
                      ev(cfg, "gain", "square amplitude just over full scale", "reject"),
                      ev(cfg, "gain", "square amplitude full scale", "accept_round"),
                      ev(cfg, "gain", "square amplitude negative full scale", "accept"),
                      ev(cfg, "gain", "square amplitude 1/3", "accept_round"),
                      ev(cfg, "gain", "square amplitude one LSB at 2**15", "accept")]},
        {"id": "envelope_sample_grid", "quantity": "count", "shape": "grid_samples",
         "severity": "fatal", "grid": 1,
         "evidence": [ev(cfg, "envelope", "numerical pulse, 4 samples", "accept"),
                      ev(cfg, "envelope", "numerical pulse, 8 samples", "accept")]},
        {"id": "envelope_duration_exact", "quantity": "time", "shape": "range_units",
         "severity": "fatal",
         "evidence": [ev(cfg, "envelope", "numerical pulse, 8 samples", "accept"),
                      ev(cfg, "cost", "one 8-sample numerical pulse", "accept")]},
        {"id": "envelope_amplitude", "quantity": "amplitude", "shape": "range_resolution",
         "severity": "fatal", "min": rat(-1), "max": rat(1),
         "resolution": rat(GAIN_STEP), "saturate_max": rat(GAIN_SATURATE),
         "evidence": [ev(cfg, "envelope", "numerical pulse at 1.2", "reject"),
                      ev(cfg, "envelope", "numerical pulse at full scale", "accept_round"),
                      ev(cfg, "envelope", "numerical pulse at negative full scale", "accept"),
                      ev(cfg, "envelope", "numerical ramp 0..7/8", "accept")]},
    ]


def vendor_behavior(cfg):
    return [
        {"id": "clock_phase_ignored",
         "vendor_action": "a ClockResource phase is dropped; the sequencer starts at phase 0. "
                          "A nonzero initial frame phase reaches the device only as a phase "
                          "update at time 0",
         "qconform_severity": "vendor_repairable", "semantics_preserving": False,
         "evidence": [ev(cfg, "phase", "ClockResource phase 45 deg", "accept_round")]},
        {"id": "initial_nco_frequency_quantized_on_instrument",
         "vendor_action": "the initial NCO frequency is a sequencer setting; the instrument "
                          "quantizes it to 0.25 Hz, and offline the setting keeps the float",
         "qconform_severity": "vendor_repairable", "semantics_preserving": False,
         "evidence": [ev(cfg, "freq", "clock at 100 MHz + 0.3 Hz", "accept")]},
        {"id": "same_time_phase_updates_merged",
         "vendor_action": "two phase shifts at one time become one update of their sum, "
                          "quantized once",
         "qconform_severity": "vendor_repairable", "semantics_preserving": True,
         "evidence": [ev(cfg, "spacing", "two ShiftClockPhase at the same time", "accept")]},
    ]


def channel(name, kind, vendor_type, cfg, capture=False):
    ch = {
        "name": name, "kind": kind, "vendor_type": vendor_type,
        "unit": rat(NS), "sample_unit": rat(NS),
        "duration_grid": 1, "schedule_grid": 1,
        "capabilities": {},
        "constraints": timing_constraints(cfg, capture) + carrier_constraints(cfg),
        "vendor_behavior": vendor_behavior(cfg),
    }
    if capture:
        ch["constraints"] += [
            {"id": "readout_length_range", "quantity": "time", "shape": "range_units",
             "severity": "fatal", "min_units": MIN_ACQUISITION_NS,
             "max_units": MAX_ACQUISITION_NS,
             "evidence": [ev(cfg, "readout", "acquisition of 16000001 ns", "reject"),
                          ev(cfg, "readout", "acquisition of 16000000 ns", "accept"),
                          ev(cfg, "readout", "acquisition of 3 ns while the drive port runs",
                             "reject"),
                          ev(cfg, "readout", "acquisition of 4 ns while the drive port runs",
                             "accept")]},
            {"id": "capture_spacing", "quantity": "time", "shape": "range_units",
             "severity": "fatal", "min_units": CAPTURE_SPACING_NS,
             "evidence": [ev(cfg, "readout", "second acquisition 299 ns after the first", "reject"),
                          ev(cfg, "readout", "second acquisition 300 ns after the first", "accept")]},
            {"id": "capture_slot", "quantity": "time", "shape": "range_units",
             "severity": "fatal", "min_units": START_SPACING_NS,
             "evidence": [ev(cfg, "readout", "readout pulse 5 ns after an acquisition starts",
                             "reject"),
                          ev(cfg, "readout", "readout pulse 4 ns after an acquisition starts",
                             "accept_round"),
                          ev(cfg, "readout", "readout pulse 8 ns after an acquisition starts",
                             "accept_round"),
                          ev(cfg, "readout", "acquisition of 5 ns is the last operation",
                             "reject"),
                          ev(cfg, "readout", "acquisition of 8 ns is the last operation",
                             "accept")]},
            {"id": "capture_length_uniform", "quantity": "time", "shape": "range_units",
             "severity": "fatal",
             "evidence": [ev(cfg, "readout", "acquisitions of 100 then 200 ns on one sequencer",
                             "reject"),
                          ev(cfg, "readout", "acquisitions of 100 then 100 ns on one sequencer",
                             "accept")]},
        ]
    else:
        ch["capabilities"] = {
            "envelope_memory_samples": WAVEFORM_MEMORY,
            "envelope_memory_scope": "frame",
            "envelope_sample_grid": 1,
            "envelope_max_abs": 32768,
        }
        ch["constraints"] += output_constraints(cfg)
    return ch


def build(config_name):
    hw = json.loads((CONFIGS / f"{config_name}.json").read_text())
    (cluster, desc), = [(k, v) for k, v in hw["hardware_description"].items()
                        if v["instrument_type"] == "Cluster"]
    channels, budgets = [], []
    for src, dst in hw["connectivity"]["graph"]:
        _, module, path = src.split(".")
        mtype = desc["modules"][module.removeprefix("module")]["instrument_type"]
        base = dst.replace(":", "_")
        # vendor_type is the hardware path, which the connectivity graph
        # maps to a port, so the lowering can find the port from the config
        if path.startswith("complex_output"):
            name = base if mtype == "QCM" else f"{base}_out"
            channels.append(channel(name, "drive", src, config_name))
        elif path.startswith("complex_input"):
            channels.append(channel(base, "readout", src, config_name, capture=True))
        else:
            raise ValueError(f"{src}: no qconform channel for this path")
        channels[-1]["_module_type"] = mtype

    for mtype in ("QCM", "QRM"):
        names = [ch["name"] for ch in channels if ch["_module_type"] == mtype]
        if not names:
            continue
        over, fits = ({"QCM": ("8200 square pulses in a row", "8000 square pulses in a row"),
                       "QRM": ("6200 readout-port pulses", "6100 readout-port pulses")}[mtype])
        budgets.append({
            "id": "pmem_words", "limit": INSTRUCTIONS[mtype], "scope": "frame",
            "channels": names,
            "cost_model": {"kind": "linear", "per_item": INSTRUCTIONS_PER_OUTPUT,
                           "overhead": PROGRAM_OVERHEAD,
                           "per_element_max": PER_ELEMENT_MAX},
            "evidence": [ev(config_name, "budget_instr", over, "reject"),
                         ev(config_name, "budget_instr", fits, "accept"),
                         ev(config_name, "budget_instr", "8000 pulses on each of two clocks",
                            "accept"),
                         ev(config_name, "cost", "one 1000000 ns acquisition", "accept"),
                         ev(config_name, "cost", "one 20 ns pulse", "accept")],
        })
    for ch in channels:
        ch.pop("_module_type")

    return {
        "format": "qconform-descriptor",
        "format_version": 0,
        "identification": {
            "name": f"qblox-{config_name.removeprefix('qblox-')}",
            "board": f"Qblox cluster, dummy ({', '.join(m['instrument_type'] for m in desc['modules'].values())})",
            "fw_timestamp": "none: dummy cluster",
            "cfg_sw_version": f"qblox-instruments {INSTRUMENTS_VERSION}",
            "library": {"name": "qblox", "version": SCHEDULER_VERSION},
            "descriptor_version": "0",
        },
        "frames": "native",
        "rounding": {"time": "nearest_half_even", "frequency": "nearest_half_even",
                     "phase": "nearest_half_even", "amplitude": "nearest_half_even"},
        "channels": channels,
        "budgets": budgets,
    }


def main():
    OUT.mkdir(exist_ok=True)
    for cfg in sorted(p.stem for p in CONFIGS.glob("qblox-*.json")):
        d = build(cfg)
        path = OUT / f"{d['identification']['name']}-v0.json"
        path.write_text(json.dumps(d, indent=1) + "\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
