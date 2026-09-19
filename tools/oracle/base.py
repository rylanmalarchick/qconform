"""What every oracle backend shares.

A backend turns a qconform program into one vendor's calls, runs the vendor
toolchain on them, and reads back what the vendor did. The pieces here are
the ones that do not depend on the vendor: exact rationals, the rounding the
descriptors declare, the record of what a conversion lost, and the grid-cell
comparison a readback is judged by.
"""

from fractions import Fraction


def rat(d):
    """A qconform {"num": N, "den": D} as an exact Fraction."""
    return Fraction(d["num"], d["den"])


def round_half_even(value):
    """Round a Fraction to the nearest integer, ties to even.

    This is the rounding mode the descriptor declares for time, so the grid
    cell computed here is the cell the vendor would land in.
    """
    floor = value.numerator // value.denominator
    rem = value - floor
    if rem < Fraction(1, 2):
        return floor
    if rem > Fraction(1, 2):
        return floor + 1
    return floor if floor % 2 == 0 else floor + 1


def same_grid_cell(requested, got, step, quantity):
    """Did the vendor change this value, in the units the program asked for.

    Two questions, never one: was the request already a whole multiple of the
    step, and did the readback land on the same multiple. Comparing the
    rounded request against the readback alone is tautological, because
    rounding the request is exactly what the vendor does.

    Phase is periodic, so both sides are reduced to one turn first. A tone
    asked for at 1 + 1/2**31 turns reads back as 1/2**31, which is the same
    angle. Calling that a repair is the harness measuring itself.
    """
    if quantity == "phase":
        requested = requested % 1
        got = got % 1
    if step and step > 0:
        want_steps = requested / step
        got_steps = got / step
        return not (want_steps.denominator != 1
                    or round_half_even(want_steps) != round_half_even(got_steps))
    return requested == got


class Loss:
    """What the conversion to a vendor value cost, for one value.

    exact      the value qconform means, as a Fraction
    passed     the value actually handed to the vendor, as a Fraction
    grid       the grid step the vendor quantizes to, or None
    same_cell  whether both land in the same grid cell
    """

    def __init__(self, kind, element_id, exact, passed_value, grid=None):
        self.kind = kind
        self.element_id = element_id
        self.exact = exact
        self.passed = Fraction(passed_value)
        self.grid = grid
        if grid is None or grid == 0:
            self.same_cell = self.exact == self.passed
        else:
            self.same_cell = (round_half_even(self.exact / grid)
                              == round_half_even(self.passed / grid))

    def as_row(self):
        return {
            "kind": self.kind,
            "element": self.element_id,
            "exact": [self.exact.numerator, self.exact.denominator],
            "passed": [self.passed.numerator, self.passed.denominator],
            "same_cell": self.same_cell,
        }


class LoweringError(Exception):
    """The program cannot be expressed in the vendor API at all.

    This is not a vendor rejection. It means the harness cannot ask the
    question, and the row must be recorded as such rather than counted as
    either agreement or disagreement.
    """


class Plan:
    """The vendor calls a program lowers to, computed before any vendor object
    exists so the translation can be inspected and tested on its own.

    This part is what the harness reads from every backend. A backend adds
    the calls themselves.
    """

    def __init__(self):
        # Barriers whose alignment did not land exactly on a member channel's
        # lattice. The lowering rounds there, and so does the checker, so by
        # the time a start time is compared the repair has already been
        # applied on both sides and looks like no change. Recording the
        # rounding is what keeps that case distinguishable from a real
        # over-prediction.
        self.barrier_roundings = []
        self.losses = []

    def loss_rows(self):
        return [l.as_row() for l in self.losses]

    def lost_cells(self):
        """Elements where the converted value landed in a different grid
        cell. These make the row harness-attributable."""
        return [l.as_row() for l in self.losses if not l.same_cell]


class Compiled:
    """The vendor's answer to one plan.

    outcome  'compiled', 'reject' or 'crash'. The accept / accept_round split
             needs a readback, which Oracle.observe performs.
    detail   the vendor's error, for a refusal
    handle   the vendor's compiled object, for observe
    """

    def __init__(self, outcome, detail=None, handle=None):
        self.outcome = outcome
        self.detail = detail
        self.handle = handle


class Oracle:
    """One vendor toolchain, configured for one board.

    The constructor reads the config and imports nothing from the vendor, so
    the corpus generator can ask for channel defaults without a vendor
    environment. The vendor package loads on the first plan.
    """

    name = None

    def channel_defaults(self, channel):
        """Fields a generated program needs on this descriptor channel so it
        describes the device the oracle compiles for (a mixer, say)."""
        raise NotImplementedError

    def plan(self, program, descriptor):
        """qconform program JSON to a Plan. Raises LoweringError."""
        raise NotImplementedError

    def compile(self, plan):
        """Run the vendor toolchain on a plan. Returns a Compiled."""
        raise NotImplementedError

    def observe(self, compiled, plan):
        """For a compiled plan: (changed, registers). changed lists every
        quantity the vendor moved, in the vocabulary triage compares (freq,
        phase, gain, total_length, start_time). registers is what the
        hardware would see, for the record."""
        raise NotImplementedError
