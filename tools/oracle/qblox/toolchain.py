"""The Qblox toolchain, run offline in two stages.

  compile   qblox-scheduler turns a Schedule into Q1ASM and sequencer
            settings. This stage checks the 1 ns time grid, the spacing
            between operations, pulse amplitudes, and the NCO band of a
            SetClockFrequency.
  prepare   ClusterComponent.prepare applies the settings to a dummy cluster
            through the qcodes validators (the initial NCO frequency, for
            one) and uploads each sequence, which runs the vendor's q1asm
            assembler.

Both stages are vendor code. The assembler alone checks only instruction
field widths, so the oracle is the pair. See notes/qblox-feasibility.txt.

The survey and the oracle backend both use this module, so a probe and a
differential row reach the vendor by the same path.
"""

import contextlib
import logging
import re
import tempfile
import warnings

warnings.filterwarnings("ignore")

# hardware_description instrument_type -> qblox_instruments ClusterType name
MODULE_TYPES = {"QCM": "CLUSTER_QCM", "QRM": "CLUSTER_QRM",
                "QCM_RF": "CLUSTER_QCM_RF", "QRM_RF": "CLUSTER_QRM_RF"}

# The assembler reports an error as "<file>:<line>:<col>: <message>".
ASSEMBLER_ERROR = re.compile(r"^tmp\.q1asm:\d+:\d+: .*$", re.M)
# qblox-scheduler names a waveform after Python's hash() of its data, which
# is salted per process. A name in an error message would make a rerun
# differ, so it is replaced.
WAVEFORM_NAME = re.compile(r"'-?\d{12,20}'")
ADDRESS = re.compile(r" at 0x[0-9a-f]+")


def clean_message(text, limit=300):
    """A vendor message with the per-process parts removed."""
    text = WAVEFORM_NAME.sub("'<waveform>'", text)
    text = ADDRESS.sub("", text)
    return text[:limit]


class Result:
    """One run of the toolchain.

    outcome   'compiled', 'reject' or 'crash'
    stage     'compile' or 'prepare': where a refusal came from
    detail    the vendor's error, for a refusal
    compiled  the compiled instructions of the one cluster, for a readback
    """

    def __init__(self, outcome, stage, detail=None, compiled=None):
        self.outcome = outcome
        self.stage = stage
        self.detail = detail
        self.compiled = compiled


def cluster_name(hw_config):
    names = [k for k, v in hw_config["hardware_description"].items()
             if v.get("instrument_type") == "Cluster"]
    if len(names) != 1:
        raise ValueError(f"the config must describe exactly one cluster, found {names}")
    return names[0]


class Toolchain:
    """A dummy cluster built from a hardware compilation config, and a
    compiler for it. One per process: qcodes instrument names are global."""

    def __init__(self, hw_config):
        from qblox_instruments import Cluster, ClusterType
        from qblox_scheduler import QuantumDevice, SerialCompiler
        from qblox_scheduler.instrument_coordinator.components.qblox import ClusterComponent

        logging.getLogger().setLevel(logging.ERROR)
        self.name = cluster_name(hw_config)
        modules = hw_config["hardware_description"][self.name]["modules"]
        dummy = {int(slot): getattr(ClusterType, MODULE_TYPES[m["instrument_type"]])
                 for slot, m in modules.items()}
        self.cluster = Cluster(self.name, dummy_cfg=dummy)
        self.component = ClusterComponent(self.cluster)
        self.device = QuantumDevice(f"qconform_{self.name}")
        self.device.hardware_config = hw_config
        self.config = self.device.generate_compilation_config()
        self.compiler = SerialCompiler("qconform")
        self.scratch = tempfile.TemporaryDirectory(prefix="qconform-qblox-")

    def module(self, module_name):
        """cluster0_module2 -> the dummy module object."""
        slot = int(module_name.rsplit("module", 1)[1])
        return getattr(self.cluster, f"module{slot}")

    def run(self, schedule):
        try:
            compiled = self.compiler.compile(schedule, config=self.config)
        except (ValueError, RuntimeError) as e:
            return Result("reject", "compile", error(e))
        except Exception as e:  # any other failure is the vendor failing without meaning to
            return Result("crash", "compile", error(e))

        instructions = compiled.compiled_instructions.get(self.name, {})
        # The dummy transport writes the assembler's input and output files
        # into the current directory.
        with contextlib.chdir(self.scratch.name):
            try:
                self.component.prepare(instructions)
            except (ValueError, RuntimeError) as e:
                return Result("reject", "prepare", self.prepare_error(e, instructions))
            except Exception as e:  # as above
                return Result("crash", "prepare", error(e))
        return Result("compiled", "prepare", compiled=instructions)

    def prepare_error(self, e, instructions):
        """An assembler failure carries the whole sequence in its message.
        The assembler's own error line is the part that says why."""
        detail = error(e)
        if "Assembly failed" not in str(e):
            return detail
        for name in instructions:
            if "module" not in name:
                continue
            found = ASSEMBLER_ERROR.findall(self.module(name).get_assembler_log())
            if found:
                detail["error_msg"] = clean_message(" / ".join(found))
                detail["error_type"] = "AssemblerError"
                break
        return detail


def error(e):
    return {"error_type": type(e).__name__, "error_msg": clean_message(str(e))}


# ---------------------------------------------------------------------------
# Reading a compiled program back

# Instructions that take real time, and the argument that holds it in ns.
TIMED = {"upd_param": 0, "play": 2, "acquire": 2, "acquire_weighed": 4, "wait": 0}
# Instructions that take none. Anything else stops the readback, because
# counting it as zero time would be a guess.
UNTIMED = {"set_mrk", "set_freq", "set_ph", "set_ph_delta", "set_awg_gain",
           "set_awg_offs", "reset_ph", "nop", "move", "add", "sub", "loop", "stop",
           "wait_sync"}
# A compiled schedule is short and its loops count down from literals. The
# interpreter stops with an error past this many steps rather than hang on a
# program it has misread.
MAX_STEPS = 10_000_000


def parse_program(text):
    """Q1ASM text to [(label or None, op, [args])]. Comments dropped."""
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        label = None
        if ":" in line.split()[0]:
            label, _, line = line.partition(":")
            line = line.strip()
            if not line:
                out.append((label, None, []))
                continue
        op, _, rest = line.partition(" ")
        args = [a.strip() for a in rest.split(",")] if rest.strip() else []
        out.append((label, op, args))
    return out


def timeline(text):
    """The events of the schedule body, each with its start time in ns.

    The program runs as the sequencer runs it: registers, loops and jumps
    are followed, and time advances by each timed instruction's duration
    argument. The first label opens qblox-scheduler's repetition loop. Time
    before it (wait_sync and the setup) is not schedule time, so the clock
    starts there.

    The loop opens with reset_ph and upd_param 4, which run before schedule
    time 0. When the first operation starts later than 0, the scheduler folds
    that delay into the same upd_param (upd_param 5 for a start at 1 ns). So
    after a preamble upd_param N the clock reads N - 4.

    Returns [{"op", "args", "t"}] for every instruction executed inside the
    repetition loop, one pass of it. Only literal durations are counted: a
    register duration raises, because the time would be a guess.
    """
    lines = parse_program(text)
    labels = {label: i for i, (label, _, _) in enumerate(lines) if label is not None}
    start = min(labels.values())
    regs = {}
    t = None
    events = []
    pc = 0
    for _ in range(MAX_STEPS):
        if pc >= len(lines):
            return events
        label, op, args = lines[pc]
        pc += 1
        if pc - 1 == start:
            t = 0
        if op is None:
            continue
        if op not in TIMED and op not in UNTIMED:
            raise ValueError(f"unknown instruction {op!r}: its duration is not known")
        if op == "stop":
            return events
        if op == "move":
            regs[args[1]] = value(args[0], regs)
            continue
        if op in ("add", "sub"):
            a, b = value(args[0], regs), value(args[1], regs)
            regs[args[2]] = a + b if op == "add" else a - b
            continue
        if op == "loop":
            regs[args[0]] -= 1
            target = labels[args[1].lstrip("@")]
            if target == start and regs[args[0]] == 0:
                return events          # one pass of the repetition loop
            if regs[args[0]] != 0:
                pc = target
            continue
        if t is None:
            continue                   # setup before the repetition loop
        if (not events and op == "reset_ph" and pc < len(lines)
                and lines[pc][1] == "upd_param" and int(lines[pc][2][0]) >= 4):
            t = int(lines[pc][2][0]) - 4
            pc += 1
            continue
        events.append({"op": op, "args": args, "t": t})
        if op in TIMED:
            arg = args[TIMED[op]]
            if not arg.lstrip("-").isdigit():
                raise ValueError(f"{op} {','.join(args)}: duration is not a literal")
            t += int(arg)
    raise ValueError(f"program did not finish in {MAX_STEPS} steps")


def value(operand, regs):
    return regs[operand] if operand.startswith("R") else int(operand)
