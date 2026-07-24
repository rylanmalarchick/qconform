"""Reconstruct QICK tProc v2 config JSONs from published text dumps.

Only one machine-readable tProc v2 soccfg JSON exists publicly (the qick
repo testbench). Real-board configs are published only as QickConfig text
dumps in notebook outputs / READMEs. This script rebuilds loadable JSONs
from two such dumps, using the testbench JSON as the field template and
qick 0.2.418 driver-source constants for fields the dump does not print.
Faithfulness proof: reconstruct_check.py re-prints each JSON through
QickConfig and diffs against the stored published dump.

Sources (fetched 2026-07-10):
  rb:  openquantumhardware/qick
       firmware/notebooks/qick_rb/RB_tProc_v2_experiment.ipynb (MIT)
  qce: openquantumhardware/QCE2025
       fw/2025-08-16_216_tprocv2r26_rfbv2_standard/README.txt (no license
       file; used as a data source only)

Field provenance per file: configs/provenance.txt
"""

import copy
import json
from pathlib import Path

HERE = Path(__file__).parent
CONFIGS = HERE / "configs"

REFCLK = 245.76
FS_TILE0 = 9584.64      # DAC tile 0: 245.76 * 39
FS_TILE123 = 6881.28    # DAC tiles 1-3: 245.76 * 28
F_FABRIC_INT4 = 430.08
F_DDS_INT4 = 1720.32    # 6881.28 / interpolation 4


def build_rb(template):
    """RB notebook config: testbench plus exactly three field changes."""
    cfg = copy.deepcopy(template)
    cfg["sw_version"] = "0.2.371"
    cfg["fw_timestamp"] = "Tue Oct 21 16:43:27 2025"
    cfg["tprocs"][0]["pmem_size"] = 16384
    return cfg


# (type, dac, maxlen or None)  from the QCE2025 README dump
QCE_GENS = [
    ("axis_signal_gen_v6", "00", 65536),
    ("axis_signal_gen_v6", "01", 16384),
    ("axis_signal_gen_v6", "02", 32768),
    ("axis_signal_gen_v6", "03", 16384),
    ("axis_sg_mixmux8_v1", "10", None),
    ("axis_sg_int4_v2", "11", 16384),
    ("axis_sg_int4_v2", "12", 8192),
    ("axis_sg_int4_v2", "13", 16384),
    ("axis_sg_int4_v2", "20", 8192),
    ("axis_sg_int4_v2", "21", 8192),
    ("axis_sg_int4_v2", "22", 8192),
    ("axis_sg_int4_v2", "23", 8192),
    ("axis_sg_int4_v2", "30", 8192),
    ("axis_sg_int4_v2", "31", 8192),
    ("axis_sg_int4_v2", "32", 8192),
    ("axis_sg_int4_v2", "33", 8192),
]

# (ro_type, adc, trigger_port, tproc_ch)  from the dump
QCE_READOUTS = [
    ("axis_dyn_readout_v1", "20", 10, 0),
    ("axis_dyn_readout_v1", "22", 11, 1),
    ("axis_pfb_readout_v4", "21", 12, 2),
    ("axis_pfb_readout_v4", "21", 13, 3),
    ("axis_pfb_readout_v4", "21", 14, 4),
    ("axis_pfb_readout_v4", "21", 15, 5),
    ("axis_pfb_readout_v4", "21", 16, 6),
    ("axis_pfb_readout_v4", "21", 17, 7),
    ("axis_pfb_readout_v4", "21", 6, -1),
    ("axis_pfb_readout_v4", "21", 7, -1),
    ("axis_dyn_readout_v1", "10", 18, -1),
]


def qce_gen(i, gtype, dac, maxlen, v6_template):
    if gtype == "axis_signal_gen_v6":
        g = copy.deepcopy(v6_template)
        g["maxlen"] = maxlen
    else:
        # int4/mixmux fields: printed values from the dump; type constants
        # from qick drivers/generator.py (AbsIntSignalGen, AxisSgMixMux8V1)
        g = {
            "type": gtype,
            "maxv": 32766,          # AbsSignalGen.MAXV = 2**15-2
            "has_mixer": True,
            "has_dds": True,
            "b_dds": 32,
            "b_phase": 32,
            "fs": FS_TILE123,
            "fs_mult": 28,
            "fs_div": 1,
            "interpolation": 4,     # AbsIntSignalGen.FS_INTERPOLATION
            "f_fabric": F_FABRIC_INT4,
            "f_dds": F_DDS_INT4,
            "fdds_div": 4,
            "revision": 2,
            "version": "1.0",
        }
        if gtype == "axis_sg_int4_v2":
            g.update({
                "maxlen": maxlen,
                "samps_per_clk": 1,     # AbsArbSignalGen.SAMPS_PER_CLK
                "maxv_scale": 0.9,      # AbsIntSignalGen.MAXV_SCALE
                "complex_env": True,
            })
        else:  # mixmux8: const-only, no envelope memory
            g.update({
                "n_tones": 8,
                "has_gain": True,
                "has_phase": True,
                "maxv_scale": 1.0,
                "samps_per_clk": 4,
            })
    g["dac"] = dac
    g["tproc_ch"] = i
    g["fullpath"] = f"qce_gen_{i}"
    return g


def qce_readout(i, rtype, adc, tport, tproc_ch, dyn_template):
    if rtype == "axis_dyn_readout_v1":
        r = copy.deepcopy(dyn_template)
    else:
        # pfb v4: printed values from dump; DOWNSAMPLING=4 from
        # drivers/readout.py; static readout so no tproc_ctrl key
        r = copy.deepcopy(dyn_template)
        del r["tproc_ctrl"]
        r.update({
            "ro_type": "axis_pfb_readout_v4",
            "ro_fullpath": f"qce_pfb_{i}",
            "buf_maxlen": 1024,
            "f_output": 38.4,
            "f_dds": 38.4,
            "f_fabric": 38.4,
            "decimation": 16,
        })
    r["adc"] = adc
    r["trigger_port"] = tport
    r["tproc_ch"] = tproc_ch
    r["avgbuf_fullpath"] = f"qce_avgbuf_{i}"
    return r


def build_qce(template):
    cfg = copy.deepcopy(template)
    cfg["sw_version"] = "0.2.365"
    cfg["fw_timestamp"] = "Sat Aug 16 12:14:08 2025"

    v6_t = copy.deepcopy(template["gens"][0])
    cfg["gens"] = [qce_gen(i, t, d, m, v6_t)
                   for i, (t, d, m) in enumerate(QCE_GENS)]

    dyn_t = copy.deepcopy(template["readouts"][0])
    cfg["readouts"] = [qce_readout(i, t, a, p, c, dyn_t)
                       for i, (t, a, p, c) in enumerate(QCE_READOUTS)]

    # rf converter entries for the tiles this firmware uses
    rf = cfg["rf"]
    dac_t = copy.deepcopy(rf["dacs"]["00"])
    for tile in (1, 2, 3):
        for blk in range(4):
            d = copy.deepcopy(dac_t)
            d.update({"index": [tile, blk], "fs": FS_TILE123,
                      "fs_mult": 28, "f_fabric": F_FABRIC_INT4,
                      "interpolation": 4})
            rf["dacs"][f"{tile}{blk}"] = d
    adc_t = copy.deepcopy(rf["adcs"]["20"])
    for name in ("10", "11"):
        a = copy.deepcopy(adc_t)
        a["index"] = [int(name[0]), int(name[1])]
        rf["adcs"][name] = a
    rf["clk_groups"] = [
        [["qick_processor_0", "core clock"], ["qick_processor_0", "timing clock"],
         ["dac", 1], ["dac", 2], ["dac", 3]],
        [["dac", 0]],
        [["adc", 1], ["adc", 2]],
    ]

    tp = cfg["tprocs"][0]
    tp["revision"] = 26
    tp["f_core"] = 215.04
    tp["clk_srcs"]["core clock"]["f_clk"] = 215.04
    tp["start_pin"] = "SPARE5_1V8"
    tp["in_port_qty"] = 8
    tp["output_pins"] = [["trig", i, 0, f"SPARE{i}_1V8"] for i in range(5)]

    cfg["time_taggers"] = [{
        "type": "qick_time_tagger",
        "fullpath": "qick_time_tagger_0",
        "cmp_slope": False,
        "cmp_inter": 0,
        "tag_mem_size": 4096,
        "arm_store": True,
        "arm_mem_size": 1024,
        "smp_store": True,
        "smp_mem_size": 16384,
        "trigger": {"type": "tport", "port": 5, "bit": 0},
        "peripheral": "A",
        "adc_qty": 1,
        "adcs": ["11"],
    }]

    bufnames = [r["avgbuf_fullpath"] for r in cfg["readouts"]]
    cfg["ddr4_buf"]["readouts"] = bufnames
    cfg["mr_buf"]["readouts"] = [bufnames[0], bufnames[1], bufnames[10]]
    return cfg


def main():
    template = json.loads((CONFIGS / "zcu216-testbench.json").read_text())
    for name, build in [("zcu216-rb-r27", build_rb), ("zcu216-qce2025-r26", build_qce)]:
        out = CONFIGS / f"{name}.json"
        out.write_text(json.dumps(build(template), indent=1) + "\n")
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
