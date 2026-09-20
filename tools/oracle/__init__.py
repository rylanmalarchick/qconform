"""The vendor side of the differential harness.

An oracle answers one question for one vendor toolchain: what does the
vendor do with this qconform program. tools/differential/ asks the question
and records the answer. Each backend here knows one vendor.

  qick/    QICK asm_v2, compiled from a captured board config
  qblox/   qblox-scheduler and the q1asm assembler, on a dummy cluster

load() picks the backend that a descriptor names in
identification.library.name. A backend imports its vendor package only when
it plans a program, so the corpus generator and triage run without a vendor
environment.
"""

import importlib

BACKENDS = {"qick": "oracle.qick", "qblox": "oracle.qblox"}


def backend(name):
    if name not in BACKENDS:
        raise ValueError(f"no oracle backend for library {name!r}; "
                         f"known: {sorted(BACKENDS)}")
    return importlib.import_module(BACKENDS[name])


def library(descriptor):
    return descriptor["identification"]["library"]["name"]


def load(descriptor, config_path):
    """The oracle for the library a descriptor was surveyed against."""
    return backend(library(descriptor)).Oracle(config_path)


def rule_to_behavior():
    """Which documented vendor behavior explains a rule firing while the
    vendor still compiles, over every backend.

    Behavior ids are vendor-specific and a row only counts a behavior its own
    channels declare, so the union answers the same as the one backend
    would."""
    out = {}
    for name in sorted(BACKENDS):
        for rule, ids in backend(name).RULE_TO_BEHAVIOR.items():
            out.setdefault(rule, set()).update(ids)
    return out
