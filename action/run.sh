#!/usr/bin/env bash
#
# What the action runs. It lives in a file rather than inline in action.yml so
# that it can be run on a laptop:
#
#   CONTRACTLINT_REGISTRY=registry action/run.sh
#
# Writes the JSON report, prints the text one, appends it to the job summary when
# there is one, and exits with contractlint's own code so the build stops.

set -uo pipefail

registry="${CONTRACTLINT_REGISTRY:-registry}"
subject="${CONTRACTLINT_SUBJECT:-}"
proposed="${CONTRACTLINT_PROPOSED:-}"
consumers="${CONTRACTLINT_CONSUMERS:-true}"
strict="${CONTRACTLINT_STRICT:-false}"
report="${CONTRACTLINT_REPORT:-contractlint-report.json}"

if ! command -v contractlint >/dev/null 2>&1; then
  echo "run.sh: contractlint is not on PATH; pip install the package first" >&2
  exit 2
fi

argv=(check)
[ -n "$subject" ] && argv+=("$subject")
[ -n "$proposed" ] && argv+=(--proposed "$proposed")
[ "$consumers" = "true" ] && argv+=(--consumers)
[ "$strict" = "true" ] && argv+=(--strict)

# The JSON report is written even when the gate fails, because the failing run is
# the one somebody wants to read afterwards.
contractlint --registry "$registry" --format json "${argv[@]}" >"$report"
json_code=$?

text=$(mktemp)
contractlint --registry "$registry" "${argv[@]}" >"$text" 2>&1
code=$?
cat "$text"

if [ "$json_code" -eq 2 ] || [ "$code" -eq 2 ]; then
  echo "run.sh: contractlint refused to answer, so this is not a pass" >&2
fi

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "## data-contracts"
    echo
    echo '```'
    cat "$text"
    echo '```'
  } >>"$GITHUB_STEP_SUMMARY"
fi

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  if [ "$code" -eq 0 ]; then ok=true; else ok=false; fi
  echo "ok=$ok" >>"$GITHUB_OUTPUT"
  echo "report=$report" >>"$GITHUB_OUTPUT"
fi

rm -f "$text"
exit "$code"
