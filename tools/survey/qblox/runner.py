"""Black-box probe runner for Qblox: schedule a probe, compile and prepare it,
and record what the toolchain did.

Usage: python tools/survey/qblox/runner.py tools/survey/configs/<name>.json [outdir]

Each probe is compiled by qblox-scheduler and prepared on a dummy cluster,
which runs the qcodes validators and the q1asm assembler. See
tools/oracle/qblox/toolchain.py. The outcome is one of the survey's four
names:

  accept        compiled; the readback equals the request
  accept_round  compiled; the readback differs (a silent repair)
  reject        a ValueError or RuntimeError from an operation's argument
                check, the scheduler, a validator, or the assembler. 'stage'
                says which step: construct, compile or prepare
  crash         any other exception

Rows go to <outdir>/<config>__<axis>.jsonl with sorted keys and no
timestamps. A rerun under the same pins is byte-identical.
"""

import json
import sys
from importlib.metadata import version
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))   # tools/, for the oracle package

from oracle.qblox.toolchain import Toolchain, error, timeline   # noqa: E402

from probes import build_probes   # noqa: E402

AXES = ("length", "timing", "freq", "phase", "gain", "envelope", "readout",
        "budget_instr", "config")
GAIN_FULL_SCALE = 32768        # set_awg_gain and set_awg_offs units per 1.0
NCO_FREQ_STEPS_PER_HZ = 4      # set_freq units
NCO_PHASE_STEPS = 10 ** 9      # set_ph_delta units per turn


def build_schedule(probe):
    import qblox_scheduler as qs

    s = qs.Schedule(probe["note"])
    for name, freq in sorted(probe["clocks"].items()):
        s.add_resource(qs.ClockResource(name, freq))
    first = None
    t_first = None
    for spec in probe["ops"]:
        op = make_operation(qs, spec)
        if first is None:
            first = s.add(op, rel_time=spec["t"] * 1e-9)
            t_first = spec["t"]
        else:
            s.add(op, ref_op=first, ref_pt="start",
                  rel_time=(spec["t"] - t_first) * 1e-9)
    return s


def make_operation(qs, spec):
    kind = spec["op"]
    if kind == "square":
        return qs.SquarePulse(amplitude=spec["amplitude"], duration=spec["duration"] * 1e-9,
                              port=spec["port"], clock=spec["clock"])
    if kind == "numerical":
        samples = spec["samples"] + spec["samples"][-1:]
        return qs.NumericalPulse(samples=samples,
                                 t_samples=[k * 1e-9 for k in range(len(samples))],
                                 port=spec["port"], clock=spec["clock"])
    if kind == "set_frequency":
        return qs.SetClockFrequency(clock=spec["clock"], frequency=spec["frequency"])
    if kind == "shift_phase":
        return qs.ShiftClockPhase(phase_shift=spec["phase"], clock=spec["clock"])
    if kind == "acquire":
        return qs.SSBIntegrationComplex(port=spec["port"], clock=spec["clock"],
                                        duration=spec["duration"] * 1e-9)
    raise ValueError(f"unknown probe operation {kind!r}")


def module_for(hw, port):
    """The cluster module a port is wired to, from the connectivity graph."""
    for src, dst in hw["connectivity"]["graph"]:
        targets = dst if isinstance(dst, list) else [dst]
        if port in targets:
            cluster, module, _ = src.split(".")
            return f"{cluster}_{module}"
    raise ValueError(f"port {port!r} is not in the connectivity graph")


def sequencer_for(compiled, module):
    """(sequencer name, settings, timeline) of the one sequencer the compile
    put a program on in this module."""
    used = [(seq, st) for seq, st in sorted(compiled[module]["sequencers"].items())
            if st.sequence and st.sequence["program"]]
    if len(used) != 1:
        raise ValueError(f"{module}: expected one programmed sequencer, found {len(used)}")
    seq, st = used[0]
    return seq, st, timeline(st.sequence["program"])


def observe(tc, hw, probe, compiled):
    """The readback of the probed parameter, plus the raw record of what the
    sequencer was told. Values are in the probe's units."""
    probed = next(op for op in probe["ops"] if op["probed"])
    port = probed.get("port") or next(op["port"] for op in probe["ops"]
                                      if op.get("clock") == probed["clock"] and "port" in op)
    mod = module_for(hw, port)
    seq, st, events = sequencer_for(compiled, mod)
    waves = {w["index"]: w["data"] for w in st.sequence["waveforms"].values()}
    obs = {"module": mod, "sequencer": seq,
           "events": [f"{e['t']}:{e['op']} {','.join(e['args'])}".strip() for e in events],
           "wave_lengths": sorted(len(d) for d in waves.values())}
    param = probe["param"]
    pulse_ops = [op for op in probe["ops"] if op["op"] in ("square", "numerical")]
    starts = sorted({e["t"] for e in events
                     if e["op"] == "play" or (e["op"] == "set_awg_offs" and e["args"][0] != "0")})

    if param == "duration":
        obs["readback"] = pulse_duration(events, waves)
    elif param == "start" and probed["op"] == "acquire":
        acq = [e["t"] for e in events if e["op"].startswith("acquire")]
        idx = [op for op in probe["ops"] if op["op"] == "acquire"].index(probed)
        if idx < len(acq):
            obs["readback"] = acq[idx]
    elif param == "start":
        idx = sorted(pulse_ops, key=lambda op: op["t"]).index(probed)
        if idx < len(starts):
            obs["readback"] = starts[idx]
        # Two pulses on one port-clock that overlap are merged into one
        # waveform stream. A gain nobody asked for is the sign of it.
        asked = {round(op["amplitude"] * GAIN_FULL_SCALE) for op in pulse_ops}
        gains = sorted({int(e["args"][0]) for e in events if e["op"] == "set_awg_gain"})
        obs["gains"] = gains
        obs["merged"] = any(g not in asked for g in gains)
    elif param == "clock_frequency":
        obs["readback"] = float(getattr(tc.module(mod), "sequencer" + seq[3:]).nco_freq())
    elif param == "set_frequency":
        vals = [int(e["args"][0]) for e in events if e["op"] == "set_freq"]
        obs["raw"] = vals
        if vals:
            obs["readback"] = vals[0] / NCO_FREQ_STEPS_PER_HZ
    elif param == "phase_shift":
        vals = [int(e["args"][0]) for e in events if e["op"] == "set_ph_delta"]
        obs["raw"] = vals
        if vals:
            obs["readback"] = vals[0] * 360 / NCO_PHASE_STEPS
    elif param == "amplitude":
        vals = [int(e["args"][0]) for e in events
                if e["op"] in ("set_awg_gain", "set_awg_offs") and e["args"][0] != "0"]
        obs["raw"] = vals
        obs["readback"] = vals[0] / GAIN_FULL_SCALE if vals else 0.0
    elif param in ("samples", "envelope_scale"):
        # The scheduler splits a waveform into a normalized shape and a gain
        # of its largest magnitude. The gain register is quantized here and
        # can be read. The shape stays a float until the instrument quantizes
        # it on upload, which the dummy does not, so it is not read back.
        plays = [e for e in events if e["op"] == "play"]
        gains = [int(e["args"][0]) for e in events if e["op"] == "set_awg_gain"]
        if plays:
            data = waves.get(int(plays[-1]["args"][0]), [])
            if param == "samples":
                obs["readback"] = len(data)
            elif gains:
                obs["gain_raw"] = gains[-1]
                obs["readback"] = gains[-1] / GAIN_FULL_SCALE
    elif param == "integration_length":
        obs["readback"] = st.integration_length_acq
    elif param == "n_pulses":
        obs["readback"] = sum(1 for e in events if e["op"] == "play")
        obs["instructions"] = len(st.sequence["program"].splitlines())
        del obs["events"]
    return obs


def pulse_duration(events, waves):
    """How long the first pulse lasts: its waveform length, or for a long
    square, from the offset turning on to the end of the closing waveform."""
    offs = [e for e in events if e["op"] == "set_awg_offs" and e["args"][0] != "0"]
    plays = [e for e in events if e["op"] == "play"]
    if offs:
        tail = next(e for e in plays if e["t"] >= offs[0]["t"])
        return tail["t"] + len(waves[int(tail["args"][0])]) - offs[0]["t"]
    if plays:
        return len(waves[int(plays[0]["args"][0])])
    return None


def classify(probe, obs):
    rb = obs.get("readback")
    if obs.get("merged"):
        return "accept_round"
    if rb is None:
        return "accept"
    return "accept" if rb == probe["requested"] else "accept_round"


def run_probe(tc, hw, probe):
    row = {"axis": probe["axis"], "param": probe["param"],
           "requested": probe["requested"], "note": probe["note"]}
    # Building an operation is the first vendor check: pydantic validates the
    # arguments (a negative duration, for one) before any schedule exists.
    try:
        schedule = build_schedule(probe)
    except (ValueError, RuntimeError) as e:
        return {**row, "stage": "construct", "outcome": "reject", **error(e)}
    except Exception as e:  # any other failure is the vendor failing without meaning to
        return {**row, "stage": "construct", "outcome": "crash", **error(e)}
    result = tc.run(schedule)
    row["stage"] = result.stage
    if result.outcome == "compiled":
        obs = observe(tc, hw, probe, result.compiled)
        row["observed"] = obs
        row["outcome"] = classify(probe, obs)
    else:
        row["outcome"] = result.outcome
        row.update(result.detail)
    return row


def config_rows(tc, hw):
    """What the dummy cluster reports about itself: module types, sequencer
    counts, and the validator bounds the prepare step applies."""
    rows = []
    for slot, m in sorted(hw["hardware_description"][tc.name]["modules"].items()):
        mod = getattr(tc.cluster, f"module{slot}")
        seq = mod.sequencer0
        bounds = {}
        for name in ("nco_freq", "gain_awg_path0", "offset_awg_path0",
                     "integration_length_acq", "nco_phase_offs"):
            if name in seq.parameters:
                bounds[name] = str(seq.parameters[name].vals)
        rows.append({"axis": "config", "param": "module", "requested": m["instrument_type"],
                     "note": f"slot {slot}", "outcome": "accept",
                     "observed": {"n_sequencers": len(mod.sequencers), "bounds": bounds}})
    return rows


def main():
    cfg_path = Path(sys.argv[1])
    outdir = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE.parent / "catalog"
    outdir.mkdir(exist_ok=True)
    cfg_name = cfg_path.stem
    hw = json.loads(cfg_path.read_text())

    tc = Toolchain(hw)
    meta = {"config": cfg_name,
            "qblox_scheduler_version": version("qblox-scheduler"),
            "qblox_instruments_version": version("qblox-instruments"),
            "numpy_version": version("numpy")}

    by_axis = {"config": [{**meta, **r} for r in config_rows(tc, hw)]}
    for p in build_probes():
        by_axis.setdefault(p["axis"], []).append({**meta, **run_probe(tc, hw, p)})

    for axis in AXES:
        rows = by_axis[axis]
        path = outdir / f"{cfg_name}__{axis}.jsonl"
        with open(path, "w") as f:
            for r in rows:
                f.write(json.dumps(r, sort_keys=True) + "\n")
        counts = {}
        for r in rows:
            counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
        print(f"{path.name}: {len(rows)} rows {counts}")


if __name__ == "__main__":
    main()
