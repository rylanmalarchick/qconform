"""Author the golden corpus: inputs + expected reports.

Deliberate regeneration step, never automatic: rerun this script, hand-audit
the printed summary against the survey catalog severities, then commit the
frozen files. tests/golden/run.sh byte-compares against them, driven by
manifest.tsv, which this script also writes.

Inputs are frozen copies (descriptors included) so regenerating tools/
artifacts cannot silently shift the gate.

Usage: python tests/golden/generate.py <qconform-binary>

The binary is a required argument: the corpus is the contract, not the
property of any one implementation, so regeneration never builds anything
or assumes a toolchain.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent

TB_DESC = "descriptors/testbench.json"
QCE_DESC = "descriptors/qce2025-r26.json"

GEN0_UNIT = {"num": 1, "den": 16773120000}
GEN0_SAMPLE_UNIT = {"num": 1, "den": 9584640000}


def base_program(elements, waveforms=None, freq_hz=0):
    return {
        "format": "qconform-program",
        "format_version": 0,
        "channels": [{"name": "gen0", "unit": GEN0_UNIT,
                      "sample_unit": GEN0_SAMPLE_UNIT}],
        "frames": [{"name": "f0", "channel": "gen0",
                    "frequency": {"num": freq_hz, "den": 1},
                    "phase": {"num": 0, "den": 1}}],
        "waveforms": waveforms if waveforms is not None else
                     [{"name": "w0", "kind": "const",
                       "amplitude": {"num": 1, "den": 2}}],
        "elements": elements,
    }


def play(id_, dur, wf="w0"):
    return {"id": id_, "kind": "play", "frame": "f0",
            "waveform": wf, "duration": dur}


# case name -> (descriptor, program dict or raw bytes, expected exit code)
CASES = {
    # exported Ramsey program, exporter output is realizable by construction
    "ramsey-pass": (TB_DESC, "COPY_RAMSEY", 0),
    # catalog: length below 3 cycles -> RuntimeError (fatal)
    "below-min-length": (TB_DESC, base_program([play(0, 2 * 28)]), 1),
    # catalog: fractional cycles silently quantize (vendor_repairable)
    "off-grid-duration": (TB_DESC, base_program([play(0, 60 * 28 + 5)]), 2),
    # catalog: v6 out-of-band freq silently aliases (vendor_repairable);
    # 0.75 * f_dds is register-aligned so only the alias fires
    "freq-alias-v6": (TB_DESC, base_program(
        [{"id": 0, "kind": "set_frequency", "frame": "f0",
          "frequency": {"num": 7188480000, "den": 1}},
         play(1, 60 * 28)]), 2),
    # catalog: int4 (interpolated) enforces +-f_dds/2 fatally
    "freq-out-of-band-int4": (QCE_DESC, {
        "format": "qconform-program", "format_version": 0,
        "channels": [{"name": "gen5", "unit": {"num": 1, "den": 430080000}}],
        "frames": [{"name": "f5", "channel": "gen5",
                    "frequency": {"num": 1290240000, "den": 1},
                    "phase": {"num": 0, "den": 1}}],
        "waveforms": [{"name": "w0", "kind": "const",
                       "amplitude": {"num": 1, "den": 2}}],
        "elements": [{"id": 0, "kind": "play", "frame": "f5",
                      "waveform": "w0", "duration": 60}],
    }, 1),
    # the phase-2 jewel: vendor compiles envelope overflow silently;
    # qconform rejects statically (QCE gen6: 8192-sample memory, grid 1)
    "envelope-over-memory": (QCE_DESC, {
        "format": "qconform-program", "format_version": 0,
        "channels": [{"name": "gen6", "unit": {"num": 1, "den": 430080000},
                      "sample_unit": {"num": 1, "den": 430080000}}],
        "frames": [{"name": "f6", "channel": "gen6",
                    "frequency": {"num": 0, "den": 1},
                    "phase": {"num": 0, "den": 1}}],
        "waveforms": [{"name": "big", "kind": "samples", "full_scale": 32766,
                       "i": [1000] * 8193, "q": [0] * 8193}],
        "elements": [{"id": 0, "kind": "play", "frame": "f6",
                      "waveform": "big", "duration": 8193}],
    }, 1),
    # catalog: envelope length must be a multiple of samps_per_clk (fatal)
    "envelope-off-sample-grid": (TB_DESC, base_program(
        [play(0, 100 * 28, wf="e0")],
        waveforms=[{"name": "e0", "kind": "samples", "full_scale": 32766,
                    "i": [1000] * 100, "q": [0] * 100}]), 1),
    # catalog: gain over full scale accepted silently (vendor_repairable)
    "amplitude-over-fullscale": (TB_DESC, base_program(
        [play(0, 60 * 28)],
        waveforms=[{"name": "w0", "kind": "const",
                    "amplitude": {"num": 3, "den": 2}}]), 2),
    # every identification string the report echoes carries a JSON-hostile
    # byte; pins that the report stays parseable (report is byte-compared)
    "hostile-identification": ("descriptors/hostile-ident.json",
                               base_program([play(0, 60 * 28)]), 0),
    # "-0" is an integer literal in the JSON grammar and the format says every
    # number is an integer, so it reads as 0 rather than as a float. The Zig
    # implementation rejected this program: std.json parsed the token as f64
    # to preserve negative zero, and the checker refused the float. Pinned
    # here because the C behaviour is the one the specification describes.
    "negative-zero-is-integer": (TB_DESC, base_program(
        [play(0, 60 * 28)],
        waveforms=[{"name": "w0", "kind": "const",
                    "amplitude": {"num": -0, "den": 1}}]), 0),
}

# identification strings that must survive the report's escaping
HOSTILE_IDENT = {
    "name": 'a\nb\tc"d\\e',
    "board": "ZCU216",
    "fw_timestamp": "\x00\x1f",
    "cfg_sw_version": "0.2.367",
    "library": {"name": "q\\k", "version": '2"0'},
    "descriptor_version": "v\x0b0",
}

# malformed inputs: exit 3, no report on stdout
MALFORMED = {
    "malformed-float": lambda p: json.dumps(p).replace('"den": 2}', '"den": 2.0}', 1),
    "malformed-dup-id": lambda p: json.dumps(
        {**p, "elements": p["elements"] + [dict(p["elements"][0])]}),
    "malformed-dangling-frame": lambda p: json.dumps(p).replace(
        '"frame": "f0"', '"frame": "nope"', 1),
    # seconds-per-unit must be positive: a negative time base would run the
    # frame clock backwards and yield a verdict instead of a tool error
    "malformed-negative-unit": lambda p: json.dumps(
        {**p, "channels": [{**p["channels"][0],
                            "unit": {"num": -1, "den": 16773120000}}]}),
    # |INT64_MIN| is 2^63 and fits neither i64 nor a rational numerator, so
    # the peak-magnitude scan must refuse rather than clamp. Found by the
    # differential harness: this aborted the checker before it was fixed.
    "malformed-envelope-sample-min": lambda p: json.dumps(
        {**p,
         "waveforms": [{"name": "e0", "kind": "samples", "full_scale": 32766,
                        "i": [-9223372036854775808, 0], "q": [0, 0]}],
         "elements": [{"id": 0, "kind": "play", "frame": "f0",
                       "waveform": "e0", "duration": 60 * 28}]}),
}


MANIFEST_HEADER = """\
# qconform golden corpus. Any implementation can run this; see run.sh.
# Paths are relative to this directory. exit: 0 pass, 1 fail,
# 2 pass_with_repairs, 3 tool error. report "-" means exit 3: stdout must
# be empty and nothing is byte-compared.
#
# case\tdescriptor\tprogram\texit\treport
"""


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: python tests/golden/generate.py <qconform-binary>")
    qconform = Path(sys.argv[1]).resolve()
    if not qconform.is_file():
        sys.exit(f"not a file: {qconform}")
    QCONFORM = qconform
    manifest = []

    desc_dir = HERE / "descriptors"
    desc_dir.mkdir(exist_ok=True)
    shutil.copy(ROOT / "tools/descriptor/descriptors/qick-zcu216-testbench-v0.json",
                desc_dir / "testbench.json")
    shutil.copy(ROOT / "tools/descriptor/descriptors/qick-zcu216-qce2025-r26-v0.json",
                desc_dir / "qce2025-r26.json")

    # a descriptor that gives a rule a severity its emit site cannot express.
    # pulse_length_range never reports a repair, so vendor_repairable would
    # produce a report that breaks report-format-v0.txt.
    bad_sev = json.loads((desc_dir / "testbench.json").read_text())
    for c in bad_sev["channels"][0]["constraints"]:
        if c["id"] == "pulse_length_range":
            c["severity"] = "vendor_repairable"
    (desc_dir / "bad-severity.json").write_text(json.dumps(bad_sev, indent=1) + "\n")

    # a descriptor declaring a rule the checker never reads as a constraint.
    # frequency_resolution is emitted from the resolution field of
    # frequency_range, so declaring it directly would be silently ignored.
    ignored = json.loads((desc_dir / "testbench.json").read_text())
    ignored["channels"][0]["constraints"].append({
        "id": "frequency_resolution", "quantity": "frequency",
        "shape": "range_resolution", "severity": "vendor_repairable",
        "resolution": {"num": 25, "den": 62}, "evidence": [],
    })
    (desc_dir / "ignored-constraint.json").write_text(json.dumps(ignored, indent=1) + "\n")

    # a descriptor whose cost model overflows any real program: the budget
    # total must be refused, not wrapped. Found by the differential harness.
    big = json.loads((desc_dir / "testbench.json").read_text())
    big["budgets"] = [{"id": "pmem_words", "limit": 4096,
                       "cost_model": {"kind": "linear",
                                      "per_item": 9223372036854775807,
                                      "overhead": 0},
                       "evidence": []}]
    (desc_dir / "overflow-cost.json").write_text(json.dumps(big, indent=1) + "\n")

    # a descriptor the checker must refuse (exit 3)
    bad = json.loads((desc_dir / "testbench.json").read_text())
    bad["channels"][0]["duration_grid"] = 0
    (desc_dir / "zero-grid.json").write_text(json.dumps(bad, indent=1) + "\n")

    # a descriptor whose identification block is hostile to a naive writer
    hostile = json.loads((desc_dir / "testbench.json").read_text())
    hostile["identification"] = HOSTILE_IDENT
    (desc_dir / "hostile-ident.json").write_text(json.dumps(hostile, indent=1) + "\n")

    summary = []
    for name, (desc, prog, want_exit) in CASES.items():
        d = HERE / name
        d.mkdir(exist_ok=True)
        if prog == "COPY_RAMSEY":
            shutil.copy(ROOT / "tools/exporter/ramsey-testbench.json",
                        d / "program.json")
        else:
            blob = json.dumps(prog, indent=1) + "\n"
            if name == "negative-zero-is-integer":
                # json.dumps normalises -0 to 0, so write the token by hand
                blob = blob.replace('"num": 0,\n    "den": 1', '"num": -0,\n    "den": 1', 1)
                assert '"num": -0' in blob, "failed to inject the -0 token"
            (d / "program.json").write_text(blob)
        r = subprocess.run([str(QCONFORM), str(HERE / desc), str(d / "program.json")],
                           capture_output=True)
        if r.returncode != want_exit:
            sys.exit(f"{name}: exit {r.returncode}, wanted {want_exit}\n"
                     f"{r.stderr.decode()}\n{r.stdout.decode()[:500]}")
        (d / "expected.json").write_text(r.stdout.decode())
        manifest.append((name, desc, f"{name}/program.json", want_exit,
                         f"{name}/expected.json"))
        rep = json.loads(r.stdout)
        rules = [(x["rule"], x["severity"]) for x in rep["rejections"]]
        summary.append(f"{name}: exit {want_exit} verdict {rep['verdict']} {rules}")

    valid = base_program([play(0, 60 * 28)])
    for name, mutate in MALFORMED.items():
        d = HERE / name
        d.mkdir(exist_ok=True)
        (d / "program.json").write_text(mutate(valid))
        r = subprocess.run([str(QCONFORM), str(HERE / TB_DESC), str(d / "program.json")],
                           capture_output=True)
        if r.returncode != 3 or r.stdout:
            sys.exit(f"{name}: exit {r.returncode}, stdout {len(r.stdout)}B; wanted 3, empty")
        manifest.append((name, TB_DESC, f"{name}/program.json", 3, "-"))
        summary.append(f"{name}: exit 3, stderr: {r.stderr.decode().strip()[:70]}")

    for case, desc_file in (("malformed-bad-severity-descriptor", "bad-severity.json"),
                            ("malformed-ignored-constraint-descriptor", "ignored-constraint.json")):
        d = HERE / case
        d.mkdir(exist_ok=True)
        (d / "program.json").write_text(json.dumps(valid, indent=1) + "\n")
        r = subprocess.run([str(QCONFORM), str(desc_dir / desc_file),
                            str(d / "program.json")], capture_output=True)
        if r.returncode != 3 or r.stdout:
            sys.exit(f"{case}: exit {r.returncode}; wanted 3, empty stdout")
        manifest.append((case, f"descriptors/{desc_file}", f"{case}/program.json", 3, "-"))
        summary.append(f"{case}: exit 3, stderr: {r.stderr.decode().strip()[:70]}")

    d = HERE / "malformed-overflow-cost-descriptor"
    d.mkdir(exist_ok=True)
    (d / "program.json").write_text(json.dumps(valid, indent=1) + "\n")
    r = subprocess.run([str(QCONFORM), str(desc_dir / "overflow-cost.json"),
                        str(d / "program.json")], capture_output=True)
    if r.returncode != 3 or r.stdout:
        sys.exit(f"overflow-cost: exit {r.returncode}; wanted 3, empty stdout")
    manifest.append(("malformed-overflow-cost-descriptor", "descriptors/overflow-cost.json",
                     "malformed-overflow-cost-descriptor/program.json", 3, "-"))
    summary.append(f"malformed-overflow-cost-descriptor: exit 3, stderr: {r.stderr.decode().strip()[:70]}")

    d = HERE / "malformed-zero-grid-descriptor"
    d.mkdir(exist_ok=True)
    (d / "program.json").write_text(json.dumps(valid, indent=1) + "\n")
    r = subprocess.run([str(QCONFORM), str(desc_dir / "zero-grid.json"),
                        str(d / "program.json")], capture_output=True)
    if r.returncode != 3 or r.stdout:
        sys.exit(f"zero-grid: exit {r.returncode}; wanted 3, empty stdout")
    manifest.append(("malformed-zero-grid-descriptor", "descriptors/zero-grid.json",
                     "malformed-zero-grid-descriptor/program.json", 3, "-"))
    summary.append(f"malformed-zero-grid-descriptor: exit 3, stderr: {r.stderr.decode().strip()[:70]}")

    (HERE / "manifest.tsv").write_text(
        MANIFEST_HEADER + "".join("\t".join(str(f) for f in row) + "\n"
                                  for row in manifest))

    print("\nAUDIT SUMMARY (check each against the survey catalog):")
    for line in summary:
        print(" ", line)


if __name__ == "__main__":
    main()
