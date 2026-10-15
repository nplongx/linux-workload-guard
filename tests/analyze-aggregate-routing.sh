#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/lwg-aggregate-analysis.XXXXXX")
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
cd "$ROOT"
python3 benchmarks/analyze-aggregate-routing.py \
  --out "$TMP/pairs.tsv" --summary "$TMP/summary.txt" \
  benchmarks/results/routing-contention-scope-aggregate-synthetic-weight20-blockA3-pass15-5rep30-2026-10-15.tsv \
  benchmarks/results/routing-contention-scope-aggregate-synthetic-weight80-blockB3-pass15-5rep30-2026-10-15.tsv \
  benchmarks/results/routing-contention-scope-aggregate-synthetic-weight80-blockB4-pass15-5rep30-2026-10-15.tsv \
  benchmarks/results/routing-contention-scope-aggregate-synthetic-weight20-blockA4-pass15-5rep30-2026-10-15.tsv \
  > "$TMP/analyzer.stdout"
grep -q '^paired_rows=20$' "$TMP/summary.txt"
grep -q '^all_pairs_retained=true$' "$TMP/summary.txt"
grep -q '^block=B4 weight=80 n=5 median=0.824 range=\[0.671,1.035\]' "$TMP/summary.txt"
grep -q '^pass15_predeclared_weight_effect_interpretation=CONFOUNDED_INCONCLUSIVE$' "$TMP/summary.txt"
grep -q '^pass15_contrast_B3_minus_A3=-0.090' "$TMP/summary.txt"
grep -q '^pass15_contrast_B4_minus_A4=-0.165' "$TMP/summary.txt"
python3 - "$TMP/pairs.tsv" <<'PY'
import csv, sys
with open(sys.argv[1], newline="") as source:
    rows = list(csv.DictReader(source, delimiter="\t"))
assert len(rows) == 20, len(rows)
for block in ("A3", "B3", "B4", "A4"):
    selected = [row for row in rows if row["block"] == block]
    assert len(selected) == 5, (block, len(selected))
assert {row["acceptance_status"] for row in rows if row["block"] == "B4"} == {"FAIL"}
assert all(row["throughput_ratio"] not in ("", None) for row in rows if row["block"] in ("A3", "B3", "B4", "A4"))
print("aggregate analysis regression: ok")
PY
