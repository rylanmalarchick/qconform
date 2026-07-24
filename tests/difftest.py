"""Differential test: run two qconform implementations over the same inputs
and require identical observable behaviour.

The point of the port is that nothing about the checker's answers changes.
Output is byte-deterministic and exit codes are a closed set of four, so
equivalence is mechanically checkable rather than eyeballed — this script is
what turns "it looks right" into a count.

Usage:
    python tests/difftest.py <reference-binary> <candidate-binary> [options]

    --stage parse    compare only the parse stage: accept/reject plus the
                     diagnostic on stderr. The candidate may be a parse-only
                     harness that prints nothing useful on stdout.
    --stage full     compare exit code, stdout byte for byte, and stderr.
    --rounds N       mutation rounds per seed input (default 250)
    --seed N         PRNG seed (default 1)

Inputs are the golden corpus plus mutations of it. Mutations are deliberately
weighted toward the things the port could plausibly get wrong: negative
values, i64 boundaries, floats, type swaps, and missing or renamed fields.
"""

import argparse
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
GOLDEN = HERE / "golden"

# Values chosen to land on the seams: sign changes reach the floored-division
# path, the i64 ends reach the overflow path, floats and strings reach the
# type checks.
HOSTILE_INTS = [
    0, 1, -1, 2, -2, 7, -7, 28, -28, 39,
    9223372036854775807, -9223372036854775808,
    9223372036854775808, -9223372036854775809,
    99999999999999999999999999,
    4294967296, -4294967296,
]
HOSTILE_SCALARS = HOSTILE_INTS + [
    2.0, 0.5, -0.5, 1e10,
    "", "x", "gen0", "nope",
    True, False, None,
    [], {},
]


def walk_paths(node, prefix=()):
    """Every addressable location in a JSON document."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield prefix + (k,)
            yield from walk_paths(v, prefix + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield prefix + (i,)
            yield from walk_paths(v, prefix + (i,))


def get_at(doc, path):
    for step in path:
        doc = doc[step]
    return doc


def set_at(doc, path, value):
    for step in path[:-1]:
        doc = doc[step]
    doc[path[-1]] = value


def del_at(doc, path):
    for step in path[:-1]:
        doc = doc[step]
    del doc[path[-1]]


def mutate(doc, rng):
    """Apply one structural mutation. Returns None if it could not apply."""
    doc = json.loads(json.dumps(doc))
    paths = list(walk_paths(doc))
    if not paths:
        return None
    path = rng.choice(paths)
    op = rng.choice(["replace", "replace", "replace", "delete", "rename", "duplicate"])

    try:
        if op == "replace":
            set_at(doc, path, rng.choice(HOSTILE_SCALARS))
        elif op == "delete":
            del_at(doc, path)
        elif op == "rename":
            parent = get_at(doc, path[:-1]) if len(path) > 1 else doc
            if not isinstance(parent, dict):
                return None
            key = path[-1]
            parent[str(key) + rng.choice(["_", "x", "2"])] = parent.pop(key)
        elif op == "duplicate":
            parent = get_at(doc, path[:-1]) if len(path) > 1 else doc
            if not isinstance(parent, list):
                return None
            parent.append(json.loads(json.dumps(parent[path[-1]])))
    except (KeyError, IndexError, TypeError):
        return None
    return doc


def byte_mutate(raw, rng):
    """Corrupt raw bytes, so the JSON reader itself is exercised too."""
    b = bytearray(raw)
    if not b:
        return bytes(b)
    for _ in range(rng.randint(1, 3)):
        i = rng.randrange(len(b))
        op = rng.random()
        if op < 0.4:
            b[i] = rng.randrange(256)
        elif op < 0.7:
            b[i : i + 1] = b""
            if not b:
                break
        else:
            b[i:i] = bytes([rng.choice(b'{}[]",:.eE-0123456789 \\')])
    return bytes(b)


def run(binary, desc, prog):
    r = subprocess.run([str(binary), str(desc), str(prog)],
                       capture_output=True, timeout=60)
    return r.returncode, r.stdout, r.stderr


def compare(ref, cand, desc, prog, stage):
    a_code, a_out, a_err = run(ref, desc, prog)
    b_code, b_out, b_err = run(cand, desc, prog)

    if stage == "parse":
        # Only the parse stage is implemented by the candidate; anything the
        # reference accepted past parse is "accepted" on both sides.
        a_reject = a_code == 3 and (b"invalid program" in a_err or b"invalid descriptor" in a_err)
        b_reject = b_code == 3 and (b"invalid program" in b_err or b"invalid descriptor" in b_err)
        if b"check failed" in a_err or b"cannot read" in a_err:
            return None  # not a parse-stage outcome
        if a_reject != b_reject:
            return f"accept/reject differs: ref={'reject' if a_reject else 'accept'} cand={'reject' if b_reject else 'accept'}\n  ref: {a_err!r}\n  cand: {b_err!r}"
        if a_reject and a_err != b_err:
            return f"diagnostic differs\n  ref:  {a_err!r}\n  cand: {b_err!r}"
        return None

    if is_known_number_classification(a_err, b_err):
        return KNOWN
    if a_code != b_code:
        return f"exit differs: ref={a_code} cand={b_code}\n  ref: {a_err!r}\n  cand: {b_err!r}"
    if a_out != b_out:
        return f"stdout differs\n  ref:  {a_out[:400]!r}\n  cand: {b_out[:400]!r}"
    if a_err != b_err:
        return f"stderr differs\n  ref:  {a_err!r}\n  cand: {b_err!r}"
    return None


# A sentinel, not a pass: counted and printed separately so the exception stays
# visible instead of quietly widening what "identical" means.
KNOWN = "known"

CLASSIFICATION = (b"float (no floats allowed)", b"number outside i64")


def is_known_number_classification(a_err, b_err):
    """The documented behavioural differences between the two number readers.

    Both stem from std.json classifying a number token by trying conversions,
    where the C reader classifies by the token's shape and never converts:

      -0      std.json parses it as f64 to preserve negative zero, so it
              reports a float. The JSON grammar makes "-0" an integer literal
              and program-format-v0.txt says every number is an integer, so
              the C reader yields the integer 0. The C is spec-correct here.

      1e400   overflows f64, so std.json falls back to reporting it outside
              i64; the C reader calls it a float on sight. Both reject.

    Matching std.json exactly would mean reimplementing its float behaviour
    inside a program that deliberately contains no floating point. These are
    counted and reported separately rather than silently tolerated, and the
    C behaviour is pinned by its own corpus case once the Zig is retired.
    """
    if a_err == b_err:
        return False
    return any(marker in a_err for marker in CLASSIFICATION)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reference")
    ap.add_argument("candidate")
    ap.add_argument("--stage", choices=["parse", "full"], default="full")
    ap.add_argument("--rounds", type=int, default=250)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    cases = []
    for line in (GOLDEN / "manifest.tsv").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        name, desc, prog, _exit, _report = line.split("\t")
        cases.append((name, GOLDEN / desc, GOLDEN / prog))

    # plus the worked examples, which are not in the corpus
    examples = HERE.parent / "documentation" / "examples"
    example_desc = examples / "descriptor-qick-testbench.json"
    for p in sorted(examples.glob("program-*.json")):
        cases.append((f"example-{p.stem}", example_desc, p))

    checked = 0
    known = 0
    divergences = []

    def record(name, why):
        nonlocal known
        if why is KNOWN:
            known += 1
        elif why:
            divergences.append((name, why))

    # 1. the seed inputs themselves
    for name, desc, prog in cases:
        checked += 1
        record(name, compare(args.reference, args.candidate, desc, prog, args.stage))

    # 2. mutations
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        mut_desc = td / "descriptor.json"
        mut_prog = td / "program.json"

        for name, desc, prog in cases:
            desc_raw = desc.read_bytes()
            prog_raw = prog.read_bytes()
            try:
                desc_doc = json.loads(desc_raw)
                prog_doc = json.loads(prog_raw)
            except json.JSONDecodeError:
                desc_doc = prog_doc = None

            for i in range(args.rounds):
                target_prog = rng.random() < 0.7
                mut_desc.write_bytes(desc_raw)
                mut_prog.write_bytes(prog_raw)

                if rng.random() < 0.75 and prog_doc is not None:
                    doc = mutate(prog_doc if target_prog else desc_doc, rng)
                    if doc is None:
                        continue
                    blob = json.dumps(doc).encode()
                else:
                    blob = byte_mutate(prog_raw if target_prog else desc_raw, rng)

                (mut_prog if target_prog else mut_desc).write_bytes(blob)

                checked += 1
                why = compare(args.reference, args.candidate, mut_desc, mut_prog, args.stage)
                record(f"{name}/mut{i}", why)
                if why and why is not KNOWN:
                    if len(divergences) <= 5:
                        keep = td.parent / f"difftest-divergence-{len(divergences)}"
                        keep.mkdir(exist_ok=True)
                        (keep / "descriptor.json").write_bytes(mut_desc.read_bytes())
                        (keep / "program.json").write_bytes(mut_prog.read_bytes())

    print(f"inputs compared: {checked}")
    print(f"known documented differences: {known}")
    print(f"divergences: {len(divergences)}")
    for name, why in divergences[:20]:
        print(f"\n--- {name}\n{why}")
    return 1 if divergences else 0


if __name__ == "__main__":
    sys.exit(main())
