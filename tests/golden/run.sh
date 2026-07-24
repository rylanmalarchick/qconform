#!/bin/sh
# Run the golden corpus against a qconform binary.
#
#   ./tests/golden/run.sh path/to/qconform
#
# Reads manifest.tsv; for each case runs the binary on (descriptor, program)
# and requires the exit code to match. Cases with a report path also require
# stdout to be byte-identical to it; cases with "-" require empty stdout.
# Exits 0 if every case passes, 1 otherwise.

set -eu

if [ $# -ne 1 ]; then
    echo "usage: $0 <qconform-binary>" >&2
    exit 2
fi

bin=$1
case $bin in /*) ;; *) bin=$PWD/$bin ;; esac
if [ ! -x "$bin" ]; then
    echo "$0: '$bin' is not an executable" >&2
    exit 2
fi

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$here"

pass=0
fail=0
out=$(mktemp)
trap 'rm -f "$out"' EXIT

while IFS='	' read -r name desc prog want_exit want_report; do
    case $name in ''|\#*) continue ;; esac

    set +e
    "$bin" "$desc" "$prog" >"$out" 2>/dev/null
    got_exit=$?
    set -e

    if [ "$got_exit" -ne "$want_exit" ]; then
        echo "FAIL $name: exit $got_exit, wanted $want_exit"
        fail=$((fail + 1))
        continue
    fi

    if [ "$want_report" = "-" ]; then
        if [ -s "$out" ]; then
            echo "FAIL $name: expected empty stdout, got $(wc -c <"$out") bytes"
            fail=$((fail + 1))
            continue
        fi
    elif ! cmp -s "$out" "$want_report"; then
        echo "FAIL $name: stdout differs from $want_report"
        fail=$((fail + 1))
        continue
    fi

    pass=$((pass + 1))
done < manifest.tsv

echo "golden: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
