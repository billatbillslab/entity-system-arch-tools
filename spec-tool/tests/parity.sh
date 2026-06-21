#!/usr/bin/env bash
# parity.sh — the regression oracle for the unified spec toolkit.
#
# Captures the exact stdout + exit code of every `spec <subcommand>` invocation
# into golden fixtures, and verifies later builds reproduce them byte-for-byte.
# It guards the unified tool against drift as the model/analyzers evolve.
#
#   parity.sh capture   # (re)write golden fixtures from the unified CLI
#   parity.sh verify    # run the same matrix, diff against golden, fail on drift
#
# Each case writes <name>.out (stdout) and <name>.exit (exit code). Gates are
# defined as much by their exit code as their text, so both are part of parity.
# Re-baseline (capture) only when an intentional change to output lands, and do
# it deliberately per-delta — never blanket-capture to make red go green.
#
# SELF-CONTAINED CORPUS. The matrix runs entirely against a SYNTHETIC fixture
# corpus committed under tests/fixtures/corpus/ — purpose-built docs that
# exercise the analyzers' rules with synthetic names and NO calendar dates and
# NO internal document names. The public tool repo carries no real spec corpus,
# so the oracle ships its own; `make parity` is reproducible on any checkout.
#
# Absolute fixture paths are scrubbed to a stable `corpus/...` prefix (see
# scrub()) so the golden is identical regardless of where the repo is checked
# out. That scrub is the only post-processing; output is otherwise verbatim.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GOLDEN="$HERE/golden"
TOOL="$(cd "$HERE/.." && pwd)"           # the spec-tool package dir
PY="${PYTHON:-python3}"
CLI="$TOOL/cli.py"

# The synthetic corpus the whole matrix runs against.
CORPUS="$HERE/fixtures/corpus"
SPECS_DIR="$CORPUS/specs"

# Per-spec fixtures: a clean root spec, a type-heavy spec, and the meta-spec —
# the three shapes the readers most need to exercise.
SPECS_REL=(
  "ENTITY-CORE-PROTOCOL.md"
  "ENTITY-NATIVE-TYPE-SYSTEM.md"
  "SPECIFICATION-FORMAT.md"
)

# Scrub machine-specific absolute paths to a stable, leak-free prefix. The path
# analyzers (standards/style/topology) print paths resolved against the package
# REPO_ROOT, which is an absolute path on disk; everything from the fixture
# corpus root onward is the stable, meaningful part. Collapse the rest.
scrub() {
  sed -e "s#[^ \"]*/fixtures/corpus/#corpus/#g"
}

# The invocation matrix: "name|subcommand|args..." (args are space-split).
matrix() {
  local i tag spec
  for i in "${!SPECS_REL[@]}"; do
    spec="$SPECS_DIR/${SPECS_REL[$i]}"
    tag="$(basename "${SPECS_REL[$i]}" .md)"
    echo "tree-$tag|tree|$spec"
    echo "tree-$tag-symbols|tree|$spec --symbols"
    echo "tree-$tag-refs|tree|$spec --refs"
    echo "tree-$tag-json|tree|$spec --json"
    echo "render-$tag-all|render|$spec --what all"
    echo "render-$tag-json|render|$spec --what all --format json"
  done
  # Corpus commands run against the synthetic specs/ tree (passed explicitly so
  # they never touch the absent real corpus the bundled config names).
  echo "topology|topology|$SPECS_DIR"
  echo "topology-json|topology|$SPECS_DIR --json"
  echo "standards|standards|--root $SPECS_DIR"
  echo "style|style|--root $SPECS_DIR"
}

# Run one matrix case, scrubbed. Sets globals OUT (stdout+stderr, scrubbed) and
# CODE (the CLI's exit code — captured BEFORE the scrub pipe, so a gate's
# non-zero exit is recorded faithfully rather than masked by sed's exit).
run_case() {
  local sub="$1"; shift
  local tmp; tmp="$(mktemp)"
  # shellcheck disable=SC2086
  "$PY" "$CLI" $sub "$@" >"$tmp" 2>&1; CODE=$?
  OUT="$(scrub <"$tmp")"
  rm -f "$tmp"
}

cmd_capture() {
  mkdir -p "$GOLDEN"
  local name sub args
  while IFS='|' read -r name sub args; do
    [ -z "$name" ] && continue
    # shellcheck disable=SC2086
    run_case "$sub" $args
    printf '%s\n' "$OUT" > "$GOLDEN/$name.out"
    printf '%s\n' "$CODE" > "$GOLDEN/$name.exit"
    printf '  captured %-28s (exit %s, %s lines)\n' "$name" "$CODE" "$(printf '%s\n' "$OUT" | wc -l | tr -d ' ')"
  done < <(matrix)
  echo "golden fixtures written to $GOLDEN"
}

cmd_verify() {
  local fails=0 name sub args out code g_out g_exit
  while IFS='|' read -r name sub args; do
    [ -z "$name" ] && continue
    # shellcheck disable=SC2086
    run_case "$sub" $args
    out="$OUT"; code="$CODE"
    g_out="$GOLDEN/$name.out"; g_exit="$GOLDEN/$name.exit"
    if [ ! -f "$g_out" ]; then echo "MISSING golden: $name (run capture)"; fails=$((fails+1)); continue; fi
    if ! diff -q <(printf '%s\n' "$out") "$g_out" >/dev/null; then
      echo "DRIFT (stdout): $name [spec $sub]"; diff <(cat "$g_out") <(printf '%s\n' "$out") | head -20; fails=$((fails+1))
    elif [ "$code" != "$(cat "$g_exit")" ]; then
      echo "DRIFT (exit): $name [spec $sub]  golden=$(cat "$g_exit") got=$code"; fails=$((fails+1))
    else
      printf '  ok %s [spec %s]\n' "$name" "$sub"
    fi
  done < <(matrix)
  if [ "$fails" -ne 0 ]; then echo "FAIL: $fails case(s) drifted from golden"; return 1; fi
  echo "OK: all cases match golden"
}

case "${1:-}" in
  capture) cmd_capture ;;
  verify)  cmd_verify ;;
  *) echo "usage: parity.sh {capture|verify}" >&2; exit 2 ;;
esac
