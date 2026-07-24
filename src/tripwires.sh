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
#
# Usage: ./tripwires.sh <built-binary>
# Comments are stripped before matching, so prose about floats does not trip.

set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$here"
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

if [ "$status" -eq 0 ]; then
    echo "tripwires: ok"
fi
exit "$status"
