#!/usr/bin/env bash
# Rerun the survey and the differential under other qick releases.
#
# The descriptors stay pinned to qick 0.2.418. Only the vendor library
# changes, so every difference against the tracked catalog and results is
# what that pin protects against.
#
# Each version needs its own venv at ~/.venvs/qconform-qick-<version> with
# qick==<version> and numpy==2.5.1. 0.2.418 uses the survey venv.
#
# Usage: tools/versions/study.sh <out-dir> <version>...

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CONFIGS=(testbench qce2025-r26 rb-r27)
STABLE_PY="$HOME/.venvs/stable/bin/python"

if [ $# -lt 2 ]; then
    echo "usage: study.sh <out-dir> <version>..." >&2
    exit 3
fi
OUT="$1"
shift

python_for() {
    if [ "$1" = "0.2.418" ]; then
        echo "$HOME/.venvs/qconform-survey/bin/python"
    else
        echo "$HOME/.venvs/qconform-qick-$1/bin/python"
    fi
}

make -C "$ROOT" -s qconform

for version in "$@"; do
    py="$(python_for "$version")"
    got="$("$py" -W ignore -c 'import qick; print(qick.__version__)' 2>/dev/null)"
    if [ "$got" != "$version" ]; then
        echo "venv for $version has qick $got" >&2
        exit 3
    fi

    dir="$OUT/$version"
    rm -rf "$dir"
    mkdir -p "$dir/catalog" "$dir/results"
    echo "=== qick $version"

    for c in "${CONFIGS[@]}"; do
        cfg="$ROOT/tools/survey/configs/zcu216-$c.json"
        desc="$ROOT/tests/golden/descriptors/$c.json"
        corpus="$OUT/corpus-$c"

        "$py" -W ignore "$ROOT/tools/survey/runner.py" "$cfg" "$dir/catalog" \
            > "$dir/survey-$c.log" 2>&1

        if [ ! -d "$corpus" ]; then
            "$py" -W ignore "$ROOT/tools/differential/corpus.py" "$desc" "$corpus" \
                --seed 1 --config "$cfg" > /dev/null
        fi
        "$py" -W ignore "$ROOT/tools/differential/run.py" "$corpus" "$desc" "$cfg" \
            "$dir/results/$c.jsonl" > /dev/null
        "$py" -W ignore "$ROOT/tools/differential/triage.py" \
            "$dir/results/$c.jsonl" "$desc" > "$dir/triage-$c.txt"
        "$STABLE_PY" "$ROOT/tools/versions/compare_results.py" \
            "$ROOT/tools/differential/results/$c.jsonl" "$dir/results/$c.jsonl" \
            > "$dir/results-diff-$c.txt"
        grep 'UNSOUND' "$dir/triage-$c.txt" | sed "s/^/  $c: /"
    done

    "$STABLE_PY" "$ROOT/tools/versions/compare_catalog.py" \
        "$ROOT/tools/survey/catalog" "$dir/catalog" > "$dir/catalog-diff.txt"
    head -5 "$dir/catalog-diff.txt" | sed 's/^/  /'
done
