"""Validate qconform JSON artifacts: schema plus the rules JSON Schema
cannot express. Prototypes the Zig core's input validation and serves as
its test oracle.

Usage: python validate.py <file.json> [...]
The format is detected from the "format" field. Exit 0 = all valid.

Beyond-schema checks (all formats): no floats anywhere (any JSON number
with a fractional part is rejected at parse), integers within i64,
rationals canonical (den > 0, gcd(|num|, den) == 1).
Program-specific: unique names per array, unique element ids, no
dangling frame/channel/waveform references, samples i/q equal length,
sample_unit present on channels playing samples waveforms.
"""

import json
import math
import sys
from pathlib import Path

import jsonschema

SCHEMAS = Path(__file__).parent.parent.parent / "documentation" / "schemas"
I64_MIN, I64_MAX = -2**63, 2**63 - 1


def parse_strict(path):
    """Parse rejecting floats; return (doc, errors)."""
    errs = []

    def hook(s):
        errs.append(f"float literal {s} (no floats allowed)")
        return float(s)

    doc = json.loads(Path(path).read_text(), parse_float=hook)
    return doc, errs


def walk_numbers(node, path, errs):
    if isinstance(node, bool):
        return
    if isinstance(node, int):
        if not I64_MIN <= node <= I64_MAX:
            errs.append(f"{path}: integer out of i64 range")
    elif isinstance(node, dict):
        if set(node) == {"num", "den"} and isinstance(node.get("num"), int) \
                and isinstance(node.get("den"), int):
            if node["den"] <= 0:
                errs.append(f"{path}: rational den must be > 0")
            elif math.gcd(abs(node["num"]), node["den"]) != 1:
                errs.append(f"{path}: rational not canonical (gcd != 1)")
        for k, v in node.items():
            walk_numbers(v, f"{path}.{k}", errs)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk_numbers(v, f"{path}[{i}]", errs)


def check_program(doc, errs):
    for arr in ("channels", "frames", "waveforms"):
        names = [x["name"] for x in doc[arr]]
        if len(set(names)) != len(names):
            errs.append(f"duplicate names in {arr}")
    chans = {c["name"]: c for c in doc["channels"]}
    frames = {f["name"]: f for f in doc["frames"]}
    wfs = {w["name"]: w for w in doc["waveforms"]}
    for f in doc["frames"]:
        if f["channel"] not in chans:
            errs.append(f"frame {f['name']}: dangling channel {f['channel']}")
    for w in doc["waveforms"]:
        if w["kind"] == "samples" and len(w["i"]) != len(w["q"]):
            errs.append(f"waveform {w['name']}: i/q length mismatch")
    ids = [e["id"] for e in doc["elements"]]
    if len(set(ids)) != len(ids):
        errs.append("duplicate element ids")
    for e in doc["elements"]:
        if "frame" in e and e["frame"] not in frames:
            errs.append(f"element {e['id']}: dangling frame {e['frame']}")
        for fr in e.get("frames", []):
            if fr not in frames:
                errs.append(f"element {e['id']}: dangling frame {fr}")
        if e["kind"] == "play":
            w = wfs.get(e["waveform"])
            if w is None:
                errs.append(f"element {e['id']}: dangling waveform {e['waveform']}")
            elif w["kind"] == "samples":
                ch = chans[frames[e["frame"]]["channel"]]
                if "sample_unit" not in ch:
                    errs.append(f"element {e['id']}: samples waveform on channel "
                                f"{ch['name']} which declares no sample_unit")


SCHEMA_BY_FORMAT = {
    "qconform-program": "program-v0.schema.json",
    "qconform-report": "report-v0.schema.json",
    "qconform-descriptor": "descriptor-v0.schema.json",
}


def validate_file(path):
    doc, errs = parse_strict(path)
    fmt = doc.get("format")
    schema_name = SCHEMA_BY_FORMAT.get(fmt)
    if schema_name is None:
        return [f"unknown format field: {fmt!r}"]
    schema = json.loads((SCHEMAS / schema_name).read_text())
    v = jsonschema.Draft202012Validator(schema)
    errs += [f"schema: {e.json_path}: {e.message[:100]}"
             for e in sorted(v.iter_errors(doc), key=lambda e: str(e.json_path))]
    walk_numbers(doc, "$", errs)
    if fmt == "qconform-program" and not errs:
        check_program(doc, errs)
    return errs


def main():
    failed = False
    for path in sys.argv[1:]:
        errs = validate_file(path)
        if errs:
            failed = True
            print(f"FAIL {path}")
            for e in errs[:20]:
                print(f"  {e}")
        else:
            print(f"OK   {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
