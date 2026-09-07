#!/bin/sh
# Invariant tripwires. These guard properties that are easy to state, easy to
# break by accident, and invisible in a passing test run.
#
#   I1  no floating point, ever. Not a conversion, not a type, not a symbol.
#   H1  no bare signed / or % in the checker: C truncates where the checker
#       was written against floored division, and the two disagree only on
#       negative operands.
#   H2  no llabs/abs on int64: undefined at INT64_MIN, which a descriptor may
#       legally contain.
#   I4  every descriptor field the parser reads is read by the checker. A
#       field that is declared, schema'd, parsed and then ignored reads as a
#       limit that is enforced and is not one. post_mixer was exactly that,
#       and four unsound passes came of it. n_tones was the same until a rule
#       consumed it. Fields that are deliberately declarative are listed
#       below, so adding one is a decision and not an oversight.
#
# Usage: ./tests/tripwires.sh <built-binary>
# Comments are stripped before matching, so prose about floats does not trip.

set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$here/../src"
binary=${1:-}
status=0

# Strip // and /* */ comments and string literals, so only code is matched.
strip() {
    awk '
    {
        line = ""; i = 1; n = length($0)
        while (i <= n) {
            c = substr($0, i, 1); d = substr($0, i, 2)
            if (block) { if (d == "*/") { block = 0; i += 2 } else i++; continue }
            if (d == "/*") { block = 1; i += 2; continue }
            if (d == "//") break
            if (c == "\"") {          # skip over a string literal
                i++
                while (i <= n) {
                    e = substr($0, i, 1)
                    if (e == "\\") { i += 2; continue }
                    if (e == "\"") { i++; break }
                    i++
                }
                line = line "\"\""
                continue
            }
            line = line c; i++
        }
        print FILENAME ":" FNR ":" line
    }' "$1"
}

report() {
    printf 'TRIPWIRE %s\n' "$1"
    status=1
}

# I1: no float type, no float conversion, in any source file.
for f in *.c *.h; do
    if strip "$f" | grep -nE '\b(float|double)\b|\b(strtod|strtof|atof|scanf|sscanf|fscanf)\b' >/dev/null; then
        report "$f mentions a floating-point type or conversion:"
        strip "$f" | grep -E '\b(float|double)\b|\b(strtod|strtof|atof|scanf|sscanf|fscanf)\b'
    fi
done

# I1, again, where it actually counts: the linked binary must not reference a
# float-parsing symbol. Source can lie; the symbol table cannot.
if [ -n "$binary" ] && command -v nm >/dev/null 2>&1; then
    if nm -u "$binary" 2>/dev/null | grep -E '\b(strtod|strtof|atof|__isoc99_sscanf|sscanf)\b' >/dev/null; then
        report "$binary references a float-parsing symbol:"
        nm -u "$binary" | grep -E 'strtod|strtof|atof|sscanf'
    fi
fi

# H1: the checker must go through floor_div/floor_mod.
if strip check.c | grep -E '[^*] (%|/) ' >/dev/null; then
    report "check.c uses bare signed / or %; use floor_div/floor_mod:"
    strip check.c | grep -E '[^*] (%|/) '
fi

# H2: llabs and abs are undefined at INT64_MIN; abs_i64 is not.
for f in *.c *.h; do
    if strip "$f" | grep -nE '\b(llabs|imaxabs)\b|\babs\(' >/dev/null; then
        report "$f uses llabs/abs; use abs_i64/abs_i128:"
        strip "$f" | grep -E '\b(llabs|imaxabs)\b|\babs\('
    fi
done

# I4: every descriptor field the parser fills must be read somewhere in
# check.c. A field that is declared, schema'd, parsed and then ignored reads
# as a limit that is enforced and is not one. post_mixer was exactly that, and
# four unsound passes came of it.
#
# Matched by struct and not by bare name, because the names collide: a program
# channel and a descriptor channel both have n_tones, and matching the name
# alone would let the program-side use vouch for the descriptor-side field. A
# Capabilities field must appear as capabilities.<name>; every other field
# must appear as a member access.
#
# Exempt, declarative by decision:
#   phrst        the program format has no phase-reset element, so nothing can
#                exercise it. It describes the channel.
#   RoundMode    the mode the toolchain applies to an in-range value. The
#     fields     rules decide whether a value sits on its step, not what the
#                toolchain would round it to, so no verdict depends on the
#                mode. Skipped by type, below.
#
# Exempt and OPEN, tracked rather than silent. Each is a gap this tripwire
# found on its first run, not a decision:
#   reserved_min, reserved_max
#                a cost model may declare registers the toolchain reserves.
#                Nothing reads them, so the loop_registers budget does not
#                account for them. That budget is not_applicable today because
#                the program format has no loops.
#   sample_unit  the descriptor's own sample rate. The checker reads the
#                program's and never compares the two, so a program that
#                declares a different sample rate from the device is not
#                caught.
I4_EXEMPT="phrst reserved_min reserved_max sample_unit"
pairs=$(strip capability.h | sed 's/^[^:]*:[0-9]*://' | awk '
    /^typedef struct/ { n = 0; next }
    /^} [A-Za-z_]+;/  { name = $2; sub(/;/, "", name)
                        for (i = 0; i < n; i++) print name "." f[i]; n = 0; next }
    { if (match($0, /^ *(bool|int64_t|Rat) [a-z_][a-z_0-9]*;/)) {
          split($0, a, " "); g = a[2]; sub(/;/, "", g)
          if (g !~ /^has_/) f[n++] = g } }')
# One token per field, iterated with `for`. A `while read` fed by a pipe runs
# in a subshell, where report() would set status and the caller would still
# exit 0.
for pair in $pairs; do
    struct=${pair%%.*}
    field=${pair#*.}
    case " $I4_EXEMPT " in *" $field "*) continue;; esac
    if [ "$struct" = "Capabilities" ]; then
        pattern="capabilities\.$field\b"
    else
        pattern="[.>]$field\b"
    fi
    if ! strip check.c | grep -qE "$pattern"; then
        report "capability.h: $struct.$field is parsed and never read by check.c:"
        printf '  no match for %s in check.c\n' "$pattern"
    fi
done

if [ "$status" -eq 0 ]; then
    echo "tripwires: ok"
fi
exit "$status"
