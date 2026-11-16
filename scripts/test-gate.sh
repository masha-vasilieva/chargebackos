#!/usr/bin/env bash
# Chargeback OS test gate (CLAUDE.md "Test gate").
#
#   scripts/test-gate.sh           step A: everything that must pass before staging
#   scripts/test-gate.sh --stage   step A + step B: also UI tests against staging
#                                  (SSH tunnel) and staging /api/healthz == 200
#
# Exits 0 only if every suite passes with 0 failures, 0 errors and 0 skips.
# A suite that cannot run (missing tool, tunnel down, 0 tests found) is a FAIL.
# Full logs go to a temp folder printed at the end.
#
# Runs on Masha's Mac. Needs: Postgres 16 (Homebrew; started if not running),
# uv, node, e2e/node_modules (cd e2e && npm install), pnpm deps for typecheck.

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

STAGE=0
for arg in "$@"; do
  case "$arg" in
    --stage) STAGE=1 ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (use --stage or --help)" >&2; exit 2 ;;
  esac
done

LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/test-gate.XXXXXX")"
PG_BIN=/opt/homebrew/opt/postgresql@16/bin
PG_DATA=/opt/homebrew/var/postgresql@16
TUNNEL_PORT=18080
TUNNEL_PATTERN="${TUNNEL_PORT}:localhost:80"

NAMES=()
RESULTS=()
DETAILS=()

record() { # name PASS|FAIL detail
  NAMES+=("$1"); RESULTS+=("$2"); DETAILS+=("$3")
  printf '  -> %s: %s (%s)\n\n' "$2" "$1" "$3"
}

header() { printf '== %s\n' "$1"; }

# --- commit being tested -----------------------------------------------------
SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
DIRTY="$(git status --porcelain --untracked-files=no 2>/dev/null | wc -l | tr -d ' ')"
echo "Test gate on commit $SHA ($([ "$DIRTY" = 0 ] && echo 'clean tree' || echo "$DIRTY modified tracked files"))"
echo "Mode: step A$([ $STAGE = 1 ] && echo ' + step B (staging)')"
echo

# --- A1. backend unit + regression (incl. regression_golden.json) ------------
header "A1 backend unit + regression"
if ! "$PG_BIN/pg_isready" -q 2>/dev/null; then
  echo "  starting Postgres 16"
  LC_ALL=en_US.UTF-8 "$PG_BIN/pg_ctl" -D "$PG_DATA" -l /opt/homebrew/var/log/pg16.log start >/dev/null 2>&1
  for _ in 1 2 3 4 5 6 7 8 9 10; do "$PG_BIN/pg_isready" -q && break; sleep 1; done
fi
if ! "$PG_BIN/pg_isready" -q 2>/dev/null; then
  record "backend unit + regression" FAIL "Postgres not reachable"
elif ! command -v uv >/dev/null; then
  record "backend unit + regression" FAIL "uv not installed"
else
  LOG="$LOG_DIR/backend.log"
  DATABASE_URL="postgresql+psycopg://$USER@localhost/chargeback_test" \
  SESSION_SECRET=localtest \
  SANDBOX_OAUTH_ORIGIN=replit-development \
  REPLIT_DEV_DOMAIN=localhost.test \
    uv run --no-sync python -m unittest discover -s artifacts/api-server/tests >"$LOG" 2>&1
  CODE=$?
  RAN="$(grep -Eo '^Ran [0-9]+ tests?' "$LOG" | tail -1 | grep -Eo '[0-9]+' || true)"
  STATUS_LINE="$(grep -E '^(OK|FAILED)' "$LOG" | tail -1)"
  if [ $CODE -ne 0 ] || [ -z "$RAN" ] || [ "$RAN" = 0 ]; then
    record "backend unit + regression" FAIL "exit $CODE, ran ${RAN:-0}, ${STATUS_LINE:-no summary}"
    grep -E '^(FAIL|ERROR):' "$LOG" | head -20 | sed 's/^/     /'
  elif echo "$STATUS_LINE" | grep -Eq 'skipped|expected failures|unexpected successes'; then
    record "backend unit + regression" FAIL "ran $RAN, $STATUS_LINE (skips count as failures)"
  else
    record "backend unit + regression" PASS "$RAN passed, 0 failed"
  fi
fi

# --- A2. frontend unit (node --test) ----------------------------------------
header "A2 frontend unit (node --test)"
LOG="$LOG_DIR/frontend.log"
if ! command -v node >/dev/null; then
  record "frontend unit" FAIL "node not installed"
else
  node --test --test-reporter=tap artifacts/chargeback-os/tests/*.mjs >"$LOG" 2>&1
  CODE=$?
  count() { grep -E "^# $1 [0-9]+" "$LOG" | tail -1 | grep -Eo '[0-9]+' || echo 0; }
  T=$(count tests); P=$(count pass); F=$(count fail); S=$(count skipped); TD=$(count todo); C=$(count cancelled)
  if [ $CODE -ne 0 ] || [ "$T" = 0 ] || [ "$F" != 0 ] || [ "$C" != 0 ]; then
    record "frontend unit" FAIL "exit $CODE, $P passed, $F failed, $C cancelled of $T"
    grep -E '^not ok' "$LOG" | head -20 | sed 's/^/     /'
  elif [ "$S" != 0 ] || [ "$TD" != 0 ]; then
    record "frontend unit" FAIL "$P passed, $S skipped, $TD todo (skips count as failures)"
  else
    record "frontend unit" PASS "$P passed, 0 failed"
  fi
fi

# --- A3. typecheck -----------------------------------------------------------
header "A3 typecheck"
LOG="$LOG_DIR/typecheck.log"
(npx -y pnpm@10 run typecheck:libs && cd artifacts/chargeback-os && npx -y pnpm@10 run typecheck) >"$LOG" 2>&1
CODE=$?
if [ $CODE -ne 0 ]; then
  ERRS="$(grep -c 'error TS' "$LOG" || true)"
  record "typecheck" FAIL "exit $CODE, ${ERRS:-0} TS errors"
  grep -E 'error|not found' "$LOG" | head -20 | sed 's/^/     /'
  grep -q 'tsc: command not found\|not found' "$LOG" && \
    echo "     hint: npx -y pnpm@10 install --frozen-lockfile"
else
  record "typecheck" PASS "0 errors"
fi

# --- Playwright helper -------------------------------------------------------
# run_playwright <name> <project> [env...]: runs one e2e project with a JSON
# report and judges it on the stats, not just the exit code.
run_playwright() {
  local name="$1" project="$2"; shift 2
  local log="$LOG_DIR/e2e-$project.log" json="$LOG_DIR/e2e-$project.json"
  if [ ! -x e2e/node_modules/.bin/playwright ]; then
    record "$name" FAIL "e2e deps missing (cd e2e && npm install)"; return
  fi
  (cd e2e && env "$@" PLAYWRIGHT_JSON_OUTPUT_NAME="$json" \
    ./node_modules/.bin/playwright test --project="$project" --forbid-only \
    --reporter=list,json) >"$log" 2>&1
  local code=$?
  local stats
  stats="$(node -e '
    const fs = require("fs");
    try {
      const s = JSON.parse(fs.readFileSync(process.argv[1], "utf8")).stats || {};
      const errs = (JSON.parse(fs.readFileSync(process.argv[1], "utf8")).errors || []).length;
      console.log([s.expected||0, s.unexpected||0, s.flaky||0, s.skipped||0, errs].join(" "));
    } catch (e) { console.log("x"); }
  ' "$json")"
  if [ "$stats" = x ]; then
    record "$name" FAIL "exit $code, no JSON report"; tail -15 "$log" | sed 's/^/     /'; return
  fi
  set -- $stats
  local ok=$1 bad=$2 flaky=$3 skip=$4 errs=$5
  if [ $code -ne 0 ] || [ "$bad" != 0 ] || [ "$errs" != 0 ] || [ "$ok" = 0 ]; then
    record "$name" FAIL "exit $code, $ok passed, $bad failed, $errs errors"
    grep -E '✘|Error:' "$log" | head -20 | sed 's/^/     /'
  elif [ "$flaky" != 0 ] || [ "$skip" != 0 ]; then
    record "$name" FAIL "$ok passed, $flaky flaky, $skip skipped (counts as failure)"
  else
    record "$name" PASS "$ok passed, 0 failed"
  fi
}

# --- A4. e2e browser unit ----------------------------------------------------
header "A4 e2e browser unit"
run_playwright "e2e browser unit" unit

# --- B. staging: tunnel, healthz, UI tests ----------------------------------
STARTED_TUNNEL=0
if [ $STAGE = 1 ]; then
  header "B0 SSH tunnel to staging (localhost:$TUNNEL_PORT)"
  tunnel_health() { curl -s -o /dev/null -m 15 -w '%{http_code}' "http://localhost:$TUNNEL_PORT/api/healthz" 2>/dev/null; }
  start_tunnel() {
    ssh -o ExitOnForwardFailure=yes -o ConnectTimeout=20 -f -N -L "$TUNNEL_PATTERN" replit \
      >>"$LOG_DIR/tunnel.log" 2>&1 && STARTED_TUNNEL=1
    sleep 1
  }
  nc -z localhost "$TUNNEL_PORT" 2>/dev/null || start_tunnel
  # A tunnel left over from an earlier session can be open but dead (for example
  # after a workspace restart): if healthz does not answer, rebuild it once.
  if nc -z localhost "$TUNNEL_PORT" 2>/dev/null && [ "$(tunnel_health)" != 200 ] && [ $STARTED_TUNNEL = 0 ]; then
    echo "  existing tunnel does not answer; rebuilding it"
    pkill -KILL -f "ssh .*-L $TUNNEL_PATTERN replit" 2>/dev/null || true
    sleep 1
    start_tunnel
  fi
  # give a fresh tunnel a few seconds to start answering
  for _ in 1 2 3 4 5 6 7 8; do [ "$(tunnel_health)" = 200 ] && break; sleep 2; done
  if nc -z localhost "$TUNNEL_PORT" 2>/dev/null; then
    echo "  tunnel up$([ $STARTED_TUNNEL = 1 ] && echo ' (started by this run)')"; echo
    TUNNEL_OK=1
  else
    TUNNEL_OK=0
    echo "  tunnel failed:"; sed 's/^/     /' "$LOG_DIR/tunnel.log" 2>/dev/null; echo
  fi

  header "B1 staging /api/healthz"
  if [ $TUNNEL_OK = 1 ]; then
    HC="$(curl -s -o "$LOG_DIR/healthz.txt" -w '%{http_code}' --max-time 20 "http://localhost:$TUNNEL_PORT/api/healthz")"
    if [ "$HC" = 200 ]; then record "staging healthz" PASS "HTTP 200"
    else record "staging healthz" FAIL "HTTP ${HC:-none}"; fi
  else
    record "staging healthz" FAIL "tunnel down"
  fi

  header "B2 e2e UI tests against staging"
  if [ $TUNNEL_OK = 1 ]; then
    run_playwright "e2e UI (staging)" ui "BASE_URL=http://localhost:$TUNNEL_PORT"
  else
    record "e2e UI (staging)" FAIL "tunnel down"
  fi

  if [ $STARTED_TUNNEL = 1 ]; then
    pkill -f "ssh .*-L $TUNNEL_PATTERN replit" 2>/dev/null || true
  fi
fi

# --- summary -----------------------------------------------------------------
echo "================ TEST GATE SUMMARY ($SHA) ================"
FAILED=0
i=0
while [ $i -lt ${#NAMES[@]} ]; do
  printf '%-4s  %-28s %s\n' "${RESULTS[$i]}" "${NAMES[$i]}" "${DETAILS[$i]}"
  [ "${RESULTS[$i]}" = PASS ] || FAILED=$((FAILED + 1))
  i=$((i + 1))
done
echo "Logs: $LOG_DIR"
if [ $FAILED -ne 0 ]; then
  echo "GATE: FAILED ($FAILED of ${#NAMES[@]} suites). Do not deploy."
  exit 1
fi
if [ $STAGE = 1 ]; then
  echo "GATE: PASSED (step A + B). Production still needs Masha's OK in chat."
else
  echo "GATE: PASSED (step A). OK to push to staging."
fi
[ "$DIRTY" = 0 ] || echo "Note: tree has uncommitted changes; rerun on the exact commit you ship."
exit 0
