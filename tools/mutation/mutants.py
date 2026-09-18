"""Enumerate single-change mutants of a capability descriptor.

A mutant is the descriptor with exactly one thing wrong in it: a limit one
step off, a resolution twice too coarse, a severity flipped, a constraint
missing, a grid or capability wrong, a budget off by one. Each is a defect a
descriptor author could plausibly make. Fault injection runs every mutant
through the differential and counts which ones the harness catches.

Targets are the channels the corpus probes: one representative per channel
class, chosen by the corpus generator's own Descriptor.classes. With
--duplicates, the same channel mutants go on the first channel identical to
each representative instead, to measure what a value error on a duplicate
channel costs. A class with one channel has no duplicate. Budget, rounding and known mutants are not repeated.

Two named mutants re-inject descriptor defects the harness found before:
  K2  axis_sg_int4_v2 amplitude resolution 1/32766 instead of 5/147447
  K3  axis_sg_mixmux8_v1 pulse length max 2**32 - 1 instead of 2**31 - 1
K1 and K4 were checker defects and live in patches/.

Output: <out>/<id>/descriptor.json per mutant, and <out>/manifest.jsonl with
one row per mutant, sorted by id. The same input gives the same set.

Usage: python tools/mutation/mutants.py <descriptor.json> <config-name> <out> [--duplicates]
"""

import argparse
import copy
import json
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "differential"))

from corpus import Descriptor, class_key  # noqa: E402

SEVERITY_FLIP = {"fatal": "vendor_repairable", "vendor_repairable": "fatal"}
ROUNDING_FLIP = {"nearest_half_even": "trunc_toward_zero",
                 "trunc_toward_zero": "nearest_half_even"}


def frac(r):
    return Fraction(r["num"], r["den"])


def rat(f):
    f = Fraction(f)
    return {"num": f.numerator, "den": f.denominator}


def constraint_mutants(ch, c):
    """(kind, field, old, new, apply) for one constraint. apply edits a
    channel in place."""
    out = []
    cid = c["id"]

    def set_field(field, value):
        def apply(chan):
            for x in chan["constraints"]:
                if x["id"] == cid:
                    x[field] = value
        return apply

    if c["shape"] == "range_resolution":
        res = frac(c["resolution"]) if "resolution" in c else None
        for bound in ("min", "max"):
            if bound in c and res is not None:
                v = frac(c[bound])
                for sign, name in ((-1, "minus_step"), (1, "plus_step")):
                    new = rat(v + sign * res)
                    out.append((f"{bound}_{name}", bound, c[bound], new, set_field(bound, new)))
        if res is not None:
            for factor, name in ((2, "resolution_x2"), (Fraction(1, 2), "resolution_half")):
                new = rat(res * factor)
                out.append((name, "resolution", c["resolution"], new, set_field("resolution", new)))
        if "post_mixer" in c:
            new = not c["post_mixer"]
            out.append(("post_mixer_flip", "post_mixer", c["post_mixer"], new,
                        set_field("post_mixer", new)))
    elif c["shape"] == "range_units":
        grid = ch["duration_grid"]
        for bound in ("min_units", "max_units"):
            if bound in c:
                for sign, name in ((-1, "minus_grid"), (1, "plus_grid")):
                    new = c[bound] + sign * grid
                    out.append((f"{bound}_{name}", bound, c[bound], new, set_field(bound, new)))
    elif c["shape"] == "grid_samples":
        for new, name in ((c["grid"] * 2, "grid_x2"), (c["grid"] // 2, "grid_half")):
            out.append((name, "grid", c["grid"], new, set_field("grid", new)))

    sev = SEVERITY_FLIP[c["severity"]]
    out.append(("severity_flip", "severity", c["severity"], sev, set_field("severity", sev)))

    def delete(chan):
        chan["constraints"] = [x for x in chan["constraints"] if x["id"] != cid]
    out.append(("delete", "constraint", cid, None, delete))
    return [(f"{cid}.{kind}", field, old, new, apply) for kind, field, old, new, apply in out]


def channel_mutants(ch):
    out = []

    def set_top(field, value):
        def apply(chan):
            chan[field] = value
        return apply

    def set_cap(field, value):
        def apply(chan):
            chan["capabilities"][field] = value
        return apply

    for field in ("duration_grid", "schedule_grid"):
        new = ch[field] * 2
        out.append((f"channel.{field}_x2", field, ch[field], new, set_top(field, new)))
    new = rat(frac(ch["unit"]) * 2)
    out.append(("channel.unit_x2", "unit", ch["unit"], new, set_top("unit", new)))

    caps = ch.get("capabilities", {})
    if "envelope_memory_samples" in caps:
        step = caps.get("envelope_sample_grid", 1)
        for sign, name in ((-1, "minus_grid"), (1, "plus_grid")):
            new = caps["envelope_memory_samples"] + sign * step
            out.append((f"capability.envelope_memory_samples_{name}", "envelope_memory_samples",
                        caps["envelope_memory_samples"], new, set_cap("envelope_memory_samples", new)))
    if "envelope_sample_grid" in caps:
        new = caps["envelope_sample_grid"] * 2
        out.append(("capability.envelope_sample_grid_x2", "envelope_sample_grid",
                    caps["envelope_sample_grid"], new, set_cap("envelope_sample_grid", new)))
    if "envelope_max_abs" in caps:
        for sign, name in ((-1, "minus_one"), (1, "plus_one")):
            new = caps["envelope_max_abs"] + sign
            out.append((f"capability.envelope_max_abs_{name}", "envelope_max_abs",
                        caps["envelope_max_abs"], new, set_cap("envelope_max_abs", new)))
    if "n_tones" in caps:
        for sign, name in ((-1, "minus_one"), (1, "plus_one")):
            new = caps["n_tones"] + sign
            out.append((f"capability.n_tones_{name}", "n_tones", caps["n_tones"], new,
                        set_cap("n_tones", new)))
    if "phrst" in caps:
        new = not caps["phrst"]
        out.append(("capability.phrst_flip", "phrst", caps["phrst"], new, set_cap("phrst", new)))
    return out


def descriptor_mutants(raw):
    """Budget and rounding mutants, which live outside the channels."""
    out = []
    for b in raw["budgets"]:
        if b["cost_model"]["kind"] != "linear":
            continue
        bid = b["id"]

        def set_budget(field, value, bid=bid):
            def apply(d):
                for x in d["budgets"]:
                    if x["id"] == bid:
                        if field == "limit":
                            x["limit"] = value
                        else:
                            x["cost_model"][field] = value
            return apply

        for sign, name in ((-1, "minus_one"), (1, "plus_one")):
            new = b["limit"] + sign
            out.append((f"budget.{bid}.limit_{name}", "limit", b["limit"], new,
                        set_budget("limit", new)))
            new = b["cost_model"]["overhead"] + sign
            if new >= 0:
                out.append((f"budget.{bid}.overhead_{name}", "overhead",
                            b["cost_model"]["overhead"], new, set_budget("overhead", new)))

    for quantity, mode in sorted(raw["rounding"].items()):
        new = ROUNDING_FLIP[mode]

        def apply(d, quantity=quantity, new=new):
            d["rounding"][quantity] = new
        out.append((f"rounding.{quantity}_flip", quantity, mode, new, apply))
    return out


def known_mutants(raw):
    """K2 and K3: descriptor defects the harness found in phase 5 and 6a."""
    out = []
    int4 = [c["name"] for c in raw["channels"] if c["vendor_type"] == "axis_sg_int4_v2"]
    mux = [c["name"] for c in raw["channels"] if c["vendor_type"] == "axis_sg_mixmux8_v1"]
    if int4:
        def k2(d):
            for c in d["channels"]:
                if c["name"] in int4:
                    for x in c["constraints"]:
                        if x["id"] == "amplitude_range":
                            x["resolution"] = {"num": 1, "den": 32766}
        out.append(("known.K2_int4_amplitude_resolution", "*int4", "resolution",
                    {"num": 5, "den": 147447}, {"num": 1, "den": 32766}, k2))
    if mux:
        def k3(d):
            for c in d["channels"]:
                if c["name"] in mux:
                    for x in c["constraints"]:
                        if x["id"] == "pulse_length_range":
                            x["max_units"] = 2**32 - 1
        out.append(("known.K3_mux_length_max", "*mux", "max_units",
                    2**31 - 1, 2**32 - 1, k3))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("descriptor")
    ap.add_argument("config")
    ap.add_argument("out")
    ap.add_argument("--duplicates", action="store_true")
    args = ap.parse_args()

    raw = json.loads(Path(args.descriptor).read_text())
    d = Descriptor(args.descriptor)
    reps = [c for kind in ("drive", "readout") for c in d.classes(kind) if c["constraints"]]

    targets = [] if args.duplicates else [("representative", c) for c in reps]
    if args.duplicates:
        rep_names = {c["name"] for c in reps}
        for c in reps:
            dup = next((x for x in raw["channels"]
                        if x["name"] not in rep_names and class_key(x) == class_key(c)), None)
            if dup is not None:
                targets.append(("duplicate", dup))

    entries = []  # (id, role, channel, target, field, old, new, apply_to_descriptor)
    for role, ch in targets:
        name = ch["name"]
        edits = [(k, f, o, n, a) for c in ch["constraints"] for k, f, o, n, a in constraint_mutants(ch, c)]
        edits += channel_mutants(ch)
        for kind, field, old, new, apply in edits:
            def on_descriptor(desc, name=name, apply=apply):
                for chan in desc["channels"]:
                    if chan["name"] == name:
                        apply(chan)
            entries.append((f"{args.config}.{name}.{kind}", role, name, kind, field, old, new,
                            on_descriptor))
    if not args.duplicates:
        for kind, field, old, new, apply in descriptor_mutants(raw):
            entries.append((f"{args.config}.{kind}", "descriptor", None, kind, field, old, new, apply))
        for kind, channel, field, old, new, apply in known_mutants(raw):
            entries.append((f"{args.config}.{kind}", "known", channel, kind, field, old, new, apply))

    ids = [e[0] for e in entries]
    if len(ids) != len(set(ids)):
        sys.exit("duplicate mutant id")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for mid, role, channel, kind, field, old, new, apply in sorted(entries, key=lambda e: e[0]):
        m = copy.deepcopy(raw)
        apply(m)
        if m == raw:
            sys.exit(f"mutant {mid} changes nothing")
        (out / mid).mkdir(exist_ok=True)
        (out / mid / "descriptor.json").write_text(json.dumps(m, indent=1) + "\n")
        manifest.append({"id": mid, "config": args.config, "role": role, "channel": channel,
                         "kind": kind, "field": field, "old": old, "new": new})
    with open(out / "manifest.jsonl", "w") as f:
        for row in manifest:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"{len(manifest)} mutants written to {out}")


if __name__ == "__main__":
    main()
