#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# Mininet-WiFi variant of experiments/figure4_100/run_fig4_all_100.sh.
# Both paths use a wireless first hop (example/topo_wifi.py):
#   r1-wlan0 ~~ ap1 -- r2 (Path 1), r1-wlan1 ~~ ap2 -- r3 (Path 2).
# Differences from the wired runner:
# - TOPO defaults to example/topo_wifi.py; TOPO_ARGS is passed to it
#   (e.g. TOPO_ARGS="--mode n --ch1 36 --ch2 149" or "--native-bw").
# - Readiness checks r1-wlan0/r1-wlan1 (association + IPv4) instead of r1-eth1/r1-eth2.
# - With --native-bw the cake 100/50 Mbit shaper checks are skipped.
# - Wireless state (iw link, station dump) is saved per attempt.
# - Results default to results/figure4_wifi/<stamp>; analyzer is reused from figure4_100.
#
# Scope: UniProxy (TRR), MPQUIC (RR+MinRTT), MPQUIC (RR+SAPS)
#
# Default repository:
#   /home/minhhai/trungnt-UniProxy
#
# Key properties:
# - Figure 4a finishes + validates + plots before Figure 4b starts.
# - Each batch sweeps all x-axis points.
# - Parameter order is balanced by default to reduce temporal-order bias.
# - Algorithm execution order is reproducibly randomized by default.
# - Every successful batch has a manifest bound to exact binaries/workload/topology.
# - Stale/mismatched batch data is not silently reused.
# - Process/qdisc/TPROXY readiness is checked with sentinels, not only sleeps.
# - Workload has a timeout and failed attempts are retried but never counted.
# - Raw logs are retained by default.
# - Exact pre-run source files and binaries are restored on exit/error/Ctrl-C.
# ============================================================

REPO="${REPO:-/home/minhhai/trungnt-UniProxy}"

BATCHES="${BATCHES:-10}"
REPEATS_PER_BATCH="${REPEATS_PER_BATCH:-10}"
ALGORITHMS_STR="${ALGORITHMS_STR:-TRR RR SAPS}"
RUN_FIG4A="${RUN_FIG4A:-1}"
RUN_FIG4B="${RUN_FIG4B:-1}"

# Publication runs should retain raw evidence. Set to 1 only after review/archival.
CLEAN_AFTER_ALGO="${CLEAN_AFTER_ALGO:-0}"

# fixed | balanced | random
PARAM_ORDER_MODE="${PARAM_ORDER_MODE:-balanced}"
PARAM_ORDER_SEED="${PARAM_ORDER_SEED:-20260914}"

# fixed | random. Randomization is reproducible through ALGORITHM_ORDER_SEED.
ALGORITHM_ORDER_MODE="${ALGORITHM_ORDER_MODE:-random}"
ALGORITHM_ORDER_SEED="${ALGORITHM_ORDER_SEED:-20260914}"

MAX_ATTEMPTS="${MAX_ATTEMPTS:-3}"
WORKLOAD_TIMEOUT="${WORKLOAD_TIMEOUT:-900}"

# Stability timing remains as a guard, but readiness is additionally verified.
POST_MN_C_SLEEP="${POST_MN_C_SLEEP:-3}"
AFTER_SERVER_START_SLEEP="${AFTER_SERVER_START_SLEEP:-1}"
AFTER_TPROXY_SLEEP="${AFTER_TPROXY_SLEEP:-1}"
AFTER_R1_START_SLEEP="${AFTER_R1_START_SLEEP:-1}"
AFTER_WORKLOAD_SLEEP="${AFTER_WORKLOAD_SLEEP:-1}"
AFTER_PROCESS_KILL_SLEEP="${AFTER_PROCESS_KILL_SLEEP:-2}"
BETWEEN_ATTEMPT_SLEEP="${BETWEEN_ATTEMPT_SLEEP:-3}"

# Readiness polling.
READY_RETRIES="${READY_RETRIES:-50}"
READY_INTERVAL="${READY_INTERVAL:-0.2}"

# Sanity/report thresholds consumed by analyzer.
THEORETICAL_CAPACITY_MBPS="${THEORETICAL_CAPACITY_MBPS:-150}"
CAPACITY_TOLERANCE_PCT="${CAPACITY_TOLERANCE_PCT:-5}"
CV_WARNING_PCT="${CV_WARNING_PCT:-10}"

RTTS=(20 40 60 80 100)
RWNDS=(1 1.25 1.5 1.75 2 2.25 2.5 5 10)
read -r -a ALGORITHMS_INPUT <<< "$ALGORITHMS_STR"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RESULT_ROOT="${RESULT_ROOT:-$REPO/results/figure4_wifi/$STAMP}"
ANALYZER="${ANALYZER:-$SCRIPT_DIR/../figure4_100/analyze_fig4.py}"

SCHED="$REPO/mp-quic/scheduler.go"
PROTOCOL="$REPO/mp-quic/internal/protocol/protocol.go"
SERVER_PARAMS="$REPO/mp-quic/internal/protocol/server_parameters.go"
TOPO="${TOPO:-$REPO/example/topo_wifi.py}"
TOPO_ARGS="${TOPO_ARGS:-}"
NATIVE_BW=0
[[ " $TOPO_ARGS " == *" --native-bw "* ]] && NATIVE_BW=1
WORKLOAD="$REPO/example/client.py"
CLIENT_CSV="$REPO/example/throughput.csv"
CLIENT_BIN="$REPO/quic-tun/client/cmd/cmd"
SERVER_BIN="$REPO/quic-tun/server/cmd/cmd"

TMP_BACKUP="$(mktemp -d)"
BINARY_CACHE="$TMP_BACKUP/binary_cache"
HAD_CLIENT_BIN=0
HAD_SERVER_BIN=0
FAILED_ATTEMPTS="$RESULT_ROOT/failed_attempts.csv"
RUN_ORDER_CSV="$RESULT_ROOT/run_order.csv"

ACTIVE_ALGO=""
ACTIVE_RWND=""
ACTIVE_CLIENT_SHA=""
ACTIVE_SERVER_SHA=""
ACTIVE_BUILD_MANIFEST_SHA=""
CURRENT_SWEEP_ORDER=""
CURRENT_SWEEP_POSITION=0

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
warn() { printf '[%s] WARN: %s\n' "$(date '+%F %T')" "$*" >&2; }
die() { echo "ERROR: $*" >&2; exit 1; }
sha256_file() { sha256sum "$1" | awk '{print $1}'; }

validate_modes() {
  case "$PARAM_ORDER_MODE" in fixed|balanced|random) ;; *) die "PARAM_ORDER_MODE must be fixed, balanced, or random" ;; esac
  case "$ALGORITHM_ORDER_MODE" in fixed|random) ;; *) die "ALGORITHM_ORDER_MODE must be fixed or random" ;; esac
  [[ "$BATCHES" =~ ^[1-9][0-9]*$ ]] || die "BATCHES must be a positive integer"
  [[ "$REPEATS_PER_BATCH" =~ ^[1-9][0-9]*$ ]] || die "REPEATS_PER_BATCH must be a positive integer"
  [[ "$MAX_ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || die "MAX_ATTEMPTS must be a positive integer"
}

restore_pre_run_state() {
  set +e
  sudo -n mn -c >/dev/null 2>&1 || true

  [[ -f "$TMP_BACKUP/scheduler.go" ]] && cp -f "$TMP_BACKUP/scheduler.go" "$SCHED"
  [[ -f "$TMP_BACKUP/protocol.go" ]] && cp -f "$TMP_BACKUP/protocol.go" "$PROTOCOL"
  [[ -f "$TMP_BACKUP/server_parameters.go" ]] && cp -f "$TMP_BACKUP/server_parameters.go" "$SERVER_PARAMS"

  if [[ "$HAD_CLIENT_BIN" == "1" && -f "$TMP_BACKUP/client_cmd" ]]; then
    cp -pf "$TMP_BACKUP/client_cmd" "$CLIENT_BIN"
  else
    rm -f "$CLIENT_BIN"
  fi
  if [[ "$HAD_SERVER_BIN" == "1" && -f "$TMP_BACKUP/server_cmd" ]]; then
    cp -pf "$TMP_BACKUP/server_cmd" "$SERVER_BIN"
  else
    rm -f "$SERVER_BIN"
  fi
}

cleanup_on_exit() {
  local rc=$?
  trap - EXIT INT TERM
  restore_pre_run_state
  rm -rf "$TMP_BACKUP"
  if [[ $rc -eq 0 ]]; then
    log "Restored exact pre-run source files and binaries."
  else
    echo "Experiment stopped with exit code $rc; exact pre-run source files and binaries were restored." >&2
  fi
  exit "$rc"
}
trap cleanup_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

validate_modes
[[ -d "$REPO" ]] || die "Repo not found: $REPO"
[[ -f "$SCHED" && -f "$PROTOCOL" && -f "$SERVER_PARAMS" && -f "$TOPO" && -f "$WORKLOAD" ]] || \
  die "Expected UniProxy source/topology/workload files not found under $REPO"
[[ -f "$ANALYZER" ]] || die "Missing analyzer: $ANALYZER"
command -v python3 >/dev/null || die "python3 not found"
command -v go >/dev/null || die "go not found"
command -v sha256sum >/dev/null || die "sha256sum not found"
command -v timeout >/dev/null || die "timeout not found"
sudo -n true >/dev/null 2>&1 || die "This runner requires working passwordless sudo (-n) for Mininet cleanup/start."

mkdir -p "$RESULT_ROOT"
cp -p "$SCHED" "$TMP_BACKUP/scheduler.go"
cp -p "$PROTOCOL" "$TMP_BACKUP/protocol.go"
cp -p "$SERVER_PARAMS" "$TMP_BACKUP/server_parameters.go"
if [[ -f "$CLIENT_BIN" ]]; then cp -p "$CLIENT_BIN" "$TMP_BACKUP/client_cmd"; HAD_CLIENT_BIN=1; fi
if [[ -f "$SERVER_BIN" ]]; then cp -p "$SERVER_BIN" "$TMP_BACKUP/server_cmd"; HAD_SERVER_BIN=1; fi

GIT_HEAD="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)"
GIT_BRANCH="$(git -C "$REPO" branch --show-current 2>/dev/null || echo unknown)"
WORKLOAD_SHA="$(sha256_file "$WORKLOAD")"
TOPO_SHA="$(sha256_file "$TOPO")"
RUNNER_SHA="$(sha256_file "${BASH_SOURCE[0]}")"
ANALYZER_SHA="$(sha256_file "$ANALYZER")"

# Reproducible algorithm order.
mapfile -t ALGORITHMS < <(
  python3 - "$ALGORITHM_ORDER_MODE" "$ALGORITHM_ORDER_SEED" "${ALGORITHMS_INPUT[@]}" <<'PY'
import random, sys
mode, seed, *items = sys.argv[1:]
if mode == "random":
    random.Random(int(seed)).shuffle(items)
for x in items:
    print(x)
PY
)
[[ "${#ALGORITHMS[@]}" -gt 0 ]] || die "No algorithms configured"

CURRENT_META="$TMP_BACKUP/metadata.current"
{
  echo "timestamp_utc=$STAMP"
  echo "repo=$REPO"
  echo "git_head=$GIT_HEAD"
  echo "git_branch=$GIT_BRANCH"
  echo "go_version=$(go version 2>/dev/null || echo unknown)"
  echo "batches=$BATCHES"
  echo "repeats_per_batch=$REPEATS_PER_BATCH"
  echo "algorithms_requested=$ALGORITHMS_STR"
  echo "algorithms_execution_order=${ALGORITHMS[*]}"
  echo "figure4a_rtts=${RTTS[*]}"
  echo "figure4b_rwnds=${RWNDS[*]}"
  echo "clean_after_algo=$CLEAN_AFTER_ALGO"
  echo "parameter_order_mode=$PARAM_ORDER_MODE"
  echo "parameter_order_seed=$PARAM_ORDER_SEED"
  echo "algorithm_order_mode=$ALGORITHM_ORDER_MODE"
  echo "algorithm_order_seed=$ALGORITHM_ORDER_SEED"
  echo "max_attempts=$MAX_ATTEMPTS"
  echo "workload_timeout=$WORKLOAD_TIMEOUT"
  echo "theoretical_capacity_mbps=$THEORETICAL_CAPACITY_MBPS"
  echo "capacity_tolerance_pct=$CAPACITY_TOLERANCE_PCT"
  echo "cv_warning_pct=$CV_WARNING_PCT"
  echo "workload_sha256=$WORKLOAD_SHA"
  echo "topology_sha256=$TOPO_SHA"
  echo "topology_args=$TOPO_ARGS"
  echo "native_bw=$NATIVE_BW"
  echo "runner_sha256=$RUNNER_SHA"
  echo "analyzer_sha256=$ANALYZER_SHA"
  echo "execution_order=algorithm_then_figure_then_batch_then_parameter"
} > "$CURRENT_META"

if [[ -f "$RESULT_ROOT/metadata.txt" ]]; then
  python3 - "$RESULT_ROOT/metadata.txt" "$CURRENT_META" <<'PY'
import sys
def load(path):
    out={}
    for line in open(path, encoding="utf-8", errors="replace"):
        if "=" in line:
            k,v=line.rstrip("\n").split("=",1)
            out[k]=v
    return out
old, cur = load(sys.argv[1]), load(sys.argv[2])
keys = [
    "repo","git_head","git_branch","batches","repeats_per_batch",
    "algorithms_requested","algorithms_execution_order","figure4a_rtts","figure4b_rwnds",
    "parameter_order_mode","parameter_order_seed","algorithm_order_mode","algorithm_order_seed",
    "workload_sha256","topology_sha256","topology_args","runner_sha256","analyzer_sha256",
    "execution_order",
]
bad=[(k,old.get(k),cur.get(k)) for k in keys if old.get(k)!=cur.get(k)]
if bad:
    print("RESULT_ROOT contains a different experiment configuration:", file=sys.stderr)
    for k,a,b in bad:
        print(f"  {k}: existing={a!r} current={b!r}", file=sys.stderr)
    raise SystemExit(1)
PY
  log "Validated existing RESULT_ROOT metadata for safe resume."
else
  cp -f "$CURRENT_META" "$RESULT_ROOT/metadata.txt"
fi

[[ -f "$RESULT_ROOT/git_status_before.txt" ]] || git -C "$REPO" status --short > "$RESULT_ROOT/git_status_before.txt" || true
[[ -f "$FAILED_ATTEMPTS" ]] || printf 'figure,algorithm,batch,attempt,reason\n' > "$FAILED_ATTEMPTS"
[[ -f "$RUN_ORDER_CSV" ]] || printf 'figure,algorithm,batch,position,parameter,value,order_mode,full_order\n' > "$RUN_ORDER_CSV"

# Workload audit: this does not rewrite client.py. It makes the remaining
# workload-level limitation explicit in the evidence directory.
python3 - "$WORKLOAD" "$RESULT_ROOT/client_workload_audit.txt" <<'PY'
import pathlib, re, sys
p = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
s = p.read_text(encoding="utf-8", errors="replace")
notes = []
if re.search(r"\bglobal\s+throughput\b", s) and re.search(r"\bthroughput\s*\+=", s):
    notes.append("WARNING: client.py appears to update a shared global throughput variable from worker code.")
if "time.time(" in s:
    notes.append("WARNING: client.py uses time.time(); monotonic timing would be safer for elapsed-time measurement.")
if not notes:
    notes.append("No simple static warning pattern was detected by the runner.")
notes.append("This audit is heuristic; thread-safety of client.py is not modified by the two experiment scripts.")
out.write_text("\n".join(notes) + "\n", encoding="utf-8")
PY

set_algorithm() {
  local algo="$1"
  python3 - "$SCHED" "$algo" <<'PY'
import re, sys
path, algo = sys.argv[1], sys.argv[2]
s = open(path, encoding="utf-8").read()
pat = r'(?m)^(\s*const\s+SCHE_ALGO(?:\s+\w+)?\s*=\s*)"[^"]+"(.*)$'
s2, n = re.subn(pat, lambda m: f'{m.group(1)}"{algo}"{m.group(2)}', s, count=1)
if n != 1:
    raise SystemExit(f"Could not uniquely patch SCHE_ALGO in {path}; matches={n}")
open(path, "w", encoding="utf-8").write(s2)
PY
}

set_rwnd_mb() {
  local mb="$1"
  python3 - "$PROTOCOL" "$SERVER_PARAMS" "$mb" <<'PY'
from decimal import Decimal
import re, sys
protocol, server, mb = sys.argv[1], sys.argv[2], Decimal(sys.argv[3])
value = int(mb * Decimal(1024 * 1024))
def patch(path, names):
    s = open(path, encoding="utf-8").read()
    for name in names:
        pat = rf'(?m)^(\s*const\s+{re.escape(name)}(?:\s+\w+)?\s*=\s*)([^/\n]*?)(\s*(?://.*)?)$'
        s, n = re.subn(pat, lambda m: f"{m.group(1)}{value}{m.group(3)}", s, count=1)
        if n != 1:
            raise SystemExit(f"Could not uniquely patch {name} in {path}; matches={n}")
    open(path, "w", encoding="utf-8").write(s)
patch(protocol, ["InitialConnectionFlowControlWindow"])
patch(server, [
    "ReceiveConnectionFlowControlWindow",
    "DefaultMaxReceiveConnectionFlowControlWindowServer",
    "DefaultMaxReceiveConnectionFlowControlWindowClient",
])
PY
}

build_proxies() {
  local tag="$1"
  log "BUILD $tag"
  (cd "$REPO/quic-tun/server/cmd" && go build)
  (cd "$REPO/quic-tun/client/cmd" && go build)
  [[ -x "$SERVER_BIN" && -x "$CLIENT_BIN" ]] || die "Build completed but binaries not found"
  printf 'config=%s server_sha256=%s client_sha256=%s\n' \
    "$tag" "$(sha256_file "$SERVER_BIN")" "$(sha256_file "$CLIENT_BIN")" >> "$RESULT_ROOT/builds.txt"
}

cache_current_binaries() {
  local algo="$1" rwnd="$2"
  local cdir="$BINARY_CACHE/$algo/rwnd_${rwnd}MB"
  mkdir -p "$cdir"
  cp -pf "$SERVER_BIN" "$cdir/server_cmd"
  cp -pf "$CLIENT_BIN" "$cdir/client_cmd"
  python3 - "$cdir/build_manifest.json" "$algo" "$rwnd" "$GIT_HEAD" \
    "$(sha256_file "$SERVER_BIN")" "$(sha256_file "$CLIENT_BIN")" \
    "$(sha256_file "$SCHED")" "$(sha256_file "$PROTOCOL")" "$(sha256_file "$SERVER_PARAMS")" \
    "$WORKLOAD_SHA" "$TOPO_SHA" <<'PY'
import json, sys
(path, algo, rwnd, git_head, server_sha, client_sha, sched_sha,
 protocol_sha, server_params_sha, workload_sha, topo_sha) = sys.argv[1:]
obj = {
    "algorithm": algo, "rwnd_mb": float(rwnd), "git_head": git_head,
    "server_binary_sha256": server_sha, "client_binary_sha256": client_sha,
    "scheduler_source_sha256": sched_sha, "protocol_source_sha256": protocol_sha,
    "server_parameters_source_sha256": server_params_sha,
    "workload_sha256": workload_sha, "topology_sha256": topo_sha,
}
open(path, "w", encoding="utf-8").write(json.dumps(obj, indent=2, sort_keys=True) + "\n")
PY
  log "CACHE algo=$algo rwnd=${rwnd}MB"
}

activate_cached_binaries() {
  local algo="$1" rwnd="$2"
  local cdir="$BINARY_CACHE/$algo/rwnd_${rwnd}MB"
  [[ -x "$cdir/server_cmd" && -x "$cdir/client_cmd" && -f "$cdir/build_manifest.json" ]] || \
    die "Cached configuration missing for algo=$algo rwnd=${rwnd}MB"
  cp -pf "$cdir/server_cmd" "$SERVER_BIN"
  cp -pf "$cdir/client_cmd" "$CLIENT_BIN"
  ACTIVE_ALGO="$algo"
  ACTIVE_RWND="$rwnd"
  ACTIVE_SERVER_SHA="$(sha256_file "$SERVER_BIN")"
  ACTIVE_CLIENT_SHA="$(sha256_file "$CLIENT_BIN")"
  ACTIVE_BUILD_MANIFEST_SHA="$(sha256_file "$cdir/build_manifest.json")"
}

prepare_binaries_for_algo() {
  local algo="$1" rwnd
  set_algorithm "$algo"
  set_rwnd_mb 1
  build_proxies "algo=$algo rwnd=1MB"
  cache_current_binaries "$algo" 1

  if [[ "$RUN_FIG4B" == "1" ]]; then
    for rwnd in "${RWNDS[@]}"; do
      [[ "$rwnd" == "1" ]] && continue
      set_rwnd_mb "$rwnd"
      build_proxies "algo=$algo rwnd=${rwnd}MB"
      cache_current_binaries "$algo" "$rwnd"
    done
  fi
}

ordered_values() {
  local mode="$1" seed="$2" batch="$3"; shift 3
  python3 - "$mode" "$seed" "$batch" "$@" <<'PY'
import random, sys
mode, seed, batch, *items = sys.argv[1:]
batch = int(batch)
if mode == "balanced":
    k = (batch - 1) % len(items)
    items = items[k:] + items[:k]
elif mode == "random":
    random.Random(int(seed) + batch).shuffle(items)
for x in items:
    print(x)
PY
}

valid_batch_result() {
  local bdir="$1" fig="$2" algo="$3" param_name="$4" param_value="$5" rtt="$6" rwnd="$7" batch="$8" position="$9"
  [[ -f "$bdir/throughput.csv" && -f "$bdir/manifest.json" ]] || return 1
  python3 - "$bdir/throughput.csv" "$bdir/manifest.json" "$REPEATS_PER_BATCH" \
    "$fig" "$algo" "$param_name" "$param_value" "$rtt" "$rwnd" "$batch" \
    "$GIT_HEAD" "$ACTIVE_CLIENT_SHA" "$ACTIVE_SERVER_SHA" "$WORKLOAD_SHA" "$TOPO_SHA" \
    "$ACTIVE_BUILD_MANIFEST_SHA" "$RUNNER_SHA" "$PARAM_ORDER_MODE" "$position" "$CURRENT_SWEEP_ORDER" <<'PY'
import csv, json, math, sys
(csvp, manp, repeats, fig, algo, pname, pvalue, rtt, rwnd, batch, git_head,
 client_sha, server_sha, workload_sha, topo_sha, build_manifest_sha, runner_sha,
 order_mode, position, full_order) = sys.argv[1:]
try:
    vals = []
    with open(csvp, newline="") as f:
        for row in csv.reader(f):
            if not row: continue
            x = float(row[0])
            if not math.isfinite(x) or x <= 0: raise ValueError
            vals.append(x)
    if len(vals) != int(repeats):
        raise ValueError
    m = json.load(open(manp, encoding="utf-8"))
    exact = {
        "figure": fig, "algorithm": algo, "parameter_name": pname,
        "batch": int(batch), "expected_repeats": int(repeats),
        "git_head": git_head, "client_binary_sha256": client_sha,
        "server_binary_sha256": server_sha, "workload_sha256": workload_sha,
        "topology_sha256": topo_sha, "build_manifest_sha256": build_manifest_sha,
        "runner_sha256": runner_sha, "parameter_order_mode": order_mode,
        "sweep_position": int(position), "sweep_order": full_order,
    }
    for k, v in exact.items():
        if m.get(k) != v:
            raise ValueError
    for k, v in {
        "parameter_value": float(pvalue), "rtt_ms": float(rtt), "rwnd_mb": float(rwnd)
    }.items():
        if abs(float(m.get(k)) - v) > 1e-9:
            raise ValueError
except Exception:
    raise SystemExit(1)
raise SystemExit(0)
PY
}

write_manifest() {
  local out="$1" fig="$2" algo="$3" pname="$4" pvalue="$5" rtt="$6" rwnd="$7" batch="$8" attempt="$9"
  local position="${10}" started="${11}" finished="${12}"
  python3 - "$out" "$fig" "$algo" "$pname" "$pvalue" "$rtt" "$rwnd" "$batch" "$attempt" \
    "$REPEATS_PER_BATCH" "$GIT_HEAD" "$GIT_BRANCH" "$ACTIVE_CLIENT_SHA" "$ACTIVE_SERVER_SHA" \
    "$WORKLOAD_SHA" "$TOPO_SHA" "$ACTIVE_BUILD_MANIFEST_SHA" "$RUNNER_SHA" "$ANALYZER_SHA" \
    "$PARAM_ORDER_MODE" "$PARAM_ORDER_SEED" "$position" "$CURRENT_SWEEP_ORDER" "$started" "$finished" <<'PY'
import json, sys
(path, fig, algo, pname, pvalue, rtt, rwnd, batch, attempt, repeats, git_head, git_branch,
 client_sha, server_sha, workload_sha, topo_sha, build_manifest_sha, runner_sha, analyzer_sha,
 order_mode, order_seed, position, sweep_order, started, finished) = sys.argv[1:]
obj = {
    "schema_version": 2,
    "figure": fig, "algorithm": algo, "parameter_name": pname,
    "parameter_value": float(pvalue), "rtt_ms": int(float(rtt)), "rwnd_mb": float(rwnd),
    "batch": int(batch), "attempt": int(attempt), "expected_repeats": int(repeats),
    "git_head": git_head, "git_branch": git_branch,
    "client_binary_sha256": client_sha, "server_binary_sha256": server_sha,
    "workload_sha256": workload_sha, "topology_sha256": topo_sha,
    "build_manifest_sha256": build_manifest_sha,
    "runner_sha256": runner_sha, "analyzer_sha256": analyzer_sha,
    "parameter_order_mode": order_mode, "parameter_order_seed": int(order_seed),
    "sweep_position": int(position), "sweep_order": sweep_order,
    "started_utc": started, "finished_utc": finished,
}
open(path, "w", encoding="utf-8").write(json.dumps(obj, indent=2, sort_keys=True) + "\n")
PY
}

run_attempt() {
  local fig="$1" algo="$2" pname="$3" pvalue="$4" rtt="$5" rwnd="$6" batch="$7" position="$8" attempt="$9" bdir="${10}"
  local adir="$bdir/attempt_$(printf '%02d' "$attempt")"
  local cmdfile half started finished
  mkdir -p "$adir"
  rm -f "$adir"/*.ok 2>/dev/null || true
  started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  sudo -n mn -c >/dev/null 2>&1 || true
  sleep "$POST_MN_C_SLEEP"
  sudo -n rm -f "$CLIENT_CSV" || true

  half=$((rtt / 2))
  cmdfile="$adir/mininet.commands"

  # Shaper checks: cake 100/50 Mbit on the path endpoints unless --native-bw.
  local r1_q_check="true" r4_q_check="true"
  if [[ "$NATIVE_BW" != "1" ]]; then
    r1_q_check="grep -qi cake \"$adir/tc_r1_wlan0.txt\" && grep -qi 100Mbit \"$adir/tc_r1_wlan0.txt\" && grep -qi cake \"$adir/tc_r1_wlan1.txt\" && grep -qi 50Mbit \"$adir/tc_r1_wlan1.txt\""
    r4_q_check="grep -qi cake \"$adir/tc_r4_eth0.txt\" && grep -qi 100Mbit \"$adir/tc_r4_eth0.txt\" && grep -qi cake \"$adir/tc_r4_eth1.txt\" && grep -qi 50Mbit \"$adir/tc_r4_eth1.txt\""
  fi

  cat > "$cmdfile" <<EOF
# ----- Figure-specific tc -----
r3 tc qdisc replace dev r3-eth0 root handle 1: netem delay ${half}ms
r3 tc qdisc replace dev r3-eth1 root handle 1: netem delay ${half}ms

# ----- Capture/validate topology qdisc state BEFORE workload -----
r1 bash -lc 'tc qdisc show dev r1-wlan0 > "$adir/tc_r1_wlan0.txt"; tc qdisc show dev r1-wlan1 > "$adir/tc_r1_wlan1.txt"; $r1_q_check && touch "$adir/r1_qdisc.ok"'
r4 bash -lc 'tc qdisc show dev r4-eth0 > "$adir/tc_r4_eth0.txt"; tc qdisc show dev r4-eth1 > "$adir/tc_r4_eth1.txt"; $r4_q_check && touch "$adir/r4_qdisc.ok"'
r2 bash -lc 'tc qdisc show dev r2-eth0 > "$adir/tc_r2_eth0.txt"; tc qdisc show dev r2-eth1 > "$adir/tc_r2_eth1.txt"; grep -q "delay 5ms" "$adir/tc_r2_eth0.txt" && grep -q "delay 5ms" "$adir/tc_r2_eth1.txt" && touch "$adir/r2_delay.ok"'
r3 bash -lc 'tc qdisc show dev r3-eth0 > "$adir/tc_r3_eth0.txt"; tc qdisc show dev r3-eth1 > "$adir/tc_r3_eth1.txt"; grep -q "delay ${half}ms" "$adir/tc_r3_eth0.txt" && grep -q "delay ${half}ms" "$adir/tc_r3_eth1.txt" && touch "$adir/r3_delay.ok"'

# ----- Application + QUIC servers -----
h2 bash -lc 'cd "$REPO"; python3 example/server.py > "$adir/h2_server.log" 2>&1 & echo \$! > "$adir/h2_server.pid"'
r5 bash -lc 'cd "$REPO"; ./quic-tun/server/cmd/cmd --listen-on 10.0.6.2:7500 > "$adir/r5_server.log" 2>&1 & echo \$! > "$adir/r5_server.pid"'
sh sleep ${AFTER_SERVER_START_SLEEP}

h2 bash -lc 'for i in \$(seq 1 ${READY_RETRIES}); do pid=\$(cat "$adir/h2_server.pid" 2>/dev/null || true); if [ -n "\$pid" ] && kill -0 "\$pid" 2>/dev/null && ss -lnt | grep -q ":5000"; then touch "$adir/h2_ready.ok"; break; fi; sleep ${READY_INTERVAL}; done'
r5 bash -lc 'for i in \$(seq 1 ${READY_RETRIES}); do pid=\$(cat "$adir/r5_server.pid" 2>/dev/null || true); if [ -n "\$pid" ] && kill -0 "\$pid" 2>/dev/null && ss -lun | grep -q ":7500"; then touch "$adir/r5_ready.ok"; break; fi; sleep ${READY_INTERVAL}; done'

# ----- TPROXY/routing -----
r1 ip rule add fwmark 1 table 100
r1 ip route add local 0.0.0.0/0 dev lo table 100
r1 iptables -t mangle -A PREROUTING --in-interface r1-eth0 -p tcp -d 10.0.7.0/24 -j TPROXY --on-port 6500 --on-ip 127.0.0.1 --tproxy-mark 1/1
sh sleep ${AFTER_TPROXY_SLEEP}

r1 bash -lc 'ip rule show > "$adir/ip_rule.txt"; ip route show table 100 > "$adir/route_table100.txt"; iptables -t mangle -S PREROUTING > "$adir/iptables_prerouting.txt"; ip rule show | grep -q "fwmark 0x1" && ip route show table 100 | grep -q "local" && iptables -t mangle -C PREROUTING --in-interface r1-eth0 -p tcp -d 10.0.7.0/24 -j TPROXY --on-port 6500 --on-ip 127.0.0.1 --tproxy-mark 1/1 && touch "$adir/tproxy_ready.ok"'

# ----- Proxy client -----
r1 bash -lc 'cd "$REPO"; ./quic-tun/client/cmd/cmd --listen-on tcp:127.0.0.1:6500 --server-endpoint 10.0.6.2:7500 > "$adir/r1_client.log" 2>&1 & echo \$! > "$adir/r1_client.pid"'
sh sleep ${AFTER_R1_START_SLEEP}

r1 bash -lc 'for i in \$(seq 1 ${READY_RETRIES}); do pid=\$(cat "$adir/r1_client.pid" 2>/dev/null || true); if [ -n "\$pid" ] && kill -0 "\$pid" 2>/dev/null && ss -lnt | grep -q "127.0.0.1:6500" && ! grep -q "fatal error:" "$adir/r1_client.log"; then touch "$adir/r1_ready.ok"; break; fi; sleep ${READY_INTERVAL}; done'
r1 bash -lc 'for w in r1-wlan0 r1-wlan1; do iw dev \$w link > "$adir/iw_\${w}_link_pre.txt" 2>&1; iw dev \$w station dump > "$adir/iw_\${w}_station_pre.txt" 2>&1; done'
r1 bash -lc 'grep -q "SSID: path1" "$adir/iw_r1-wlan0_link_pre.txt" && grep -q "SSID: path2" "$adir/iw_r1-wlan1_link_pre.txt" && ip -4 addr show r1-wlan0 | grep -q "inet " && ip -4 addr show r1-wlan1 | grep -q "inet " && touch "$adir/path_interfaces.ok"'

# ----- Workload only if every readiness condition passed -----
h1 bash -lc 'if [ -f "$adir/h2_ready.ok" ] && [ -f "$adir/r5_ready.ok" ] && [ -f "$adir/tproxy_ready.ok" ] && [ -f "$adir/r1_ready.ok" ] && [ -f "$adir/path_interfaces.ok" ] && [ -f "$adir/r1_qdisc.ok" ] && [ -f "$adir/r4_qdisc.ok" ] && [ -f "$adir/r2_delay.ok" ] && [ -f "$adir/r3_delay.ok" ]; then cd "$REPO/example" && timeout ${WORKLOAD_TIMEOUT}s python3 client.py > "$adir/h1_client.log" 2>&1 && touch "$adir/workload.ok"; else echo "SKIPPED: readiness validation failed" > "$adir/h1_client.log"; fi'

sh sleep ${AFTER_WORKLOAD_SLEEP}

# ----- Post-workload wireless counters (tx retries/failed, signal, bitrate) -----
r1 bash -lc 'for w in r1-wlan0 r1-wlan1; do iw dev \$w link > "$adir/iw_\${w}_link_post.txt" 2>&1; iw dev \$w station dump > "$adir/iw_\${w}_station_post.txt" 2>&1; done'

# ----- Post-workload process health before teardown -----
r1 bash -lc 'pid=\$(cat "$adir/r1_client.pid" 2>/dev/null || true); [ -n "\$pid" ] && kill -0 "\$pid" 2>/dev/null && ! grep -q "fatal error:" "$adir/r1_client.log" && touch "$adir/r1_post.ok"'
r5 bash -lc 'pid=\$(cat "$adir/r5_server.pid" 2>/dev/null || true); [ -n "\$pid" ] && kill -0 "\$pid" 2>/dev/null && touch "$adir/r5_post.ok"'
h2 bash -lc 'pid=\$(cat "$adir/h2_server.pid" 2>/dev/null || true); [ -n "\$pid" ] && kill -0 "\$pid" 2>/dev/null && touch "$adir/h2_post.ok"'

# ----- Teardown -----
r1 bash -lc 'test -f "$adir/r1_client.pid" && kill \$(cat "$adir/r1_client.pid") 2>/dev/null || true'
r5 bash -lc 'test -f "$adir/r5_server.pid" && kill \$(cat "$adir/r5_server.pid") 2>/dev/null || true'
h2 bash -lc 'test -f "$adir/h2_server.pid" && kill \$(cat "$adir/h2_server.pid") 2>/dev/null || true'
sh sleep ${AFTER_PROCESS_KILL_SLEEP}
EOF

  # TOPO_ARGS is intentionally unquoted so it splits into separate arguments.
  # shellcheck disable=SC2086
  if ! printf 'source %s\nexit\n' "$cmdfile" | sudo -n python3 "$TOPO" $TOPO_ARGS > "$adir/mininet.log" 2>&1; then
    warn "Mininet returned nonzero for $adir"
  fi

  # Preserve actual throughput output only for this attempt.
  if [[ -f "$CLIENT_CSV" ]]; then
    sudo -n cat "$CLIENT_CSV" > "$adir/throughput.csv" || true
  fi
  sudo -n rm -f "$CLIENT_CSV" || true
  sudo -n mn -c >/dev/null 2>&1 || true

  local reason=""
  for f in h2_ready.ok r5_ready.ok tproxy_ready.ok r1_ready.ok path_interfaces.ok r1_qdisc.ok r4_qdisc.ok r2_delay.ok r3_delay.ok workload.ok r1_post.ok r5_post.ok h2_post.ok; do
    if [[ ! -f "$adir/$f" ]]; then reason="missing_${f%.ok}"; break; fi
  done
  if [[ -z "$reason" ]] && grep -Eqs 'Traceback|TimeoutError|ConnectionResetError|fatal error:' "$adir/h1_client.log" "$adir/r1_client.log" "$adir/r5_server.log" 2>/dev/null; then
    reason="runtime_error_in_logs"
  fi
  if [[ -z "$reason" && ! -f "$adir/throughput.csv" ]]; then
    reason="missing_throughput_csv"
  fi
  if [[ -z "$reason" ]]; then
    if ! python3 - "$adir/throughput.csv" "$REPEATS_PER_BATCH" <<'PY'
import csv, math, sys
p, expected = sys.argv[1], int(sys.argv[2])
vals=[]
try:
    with open(p, newline="") as f:
        for row in csv.reader(f):
            if not row: continue
            x=float(row[0])
            if not math.isfinite(x) or x <= 0: raise ValueError
            vals.append(x)
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if len(vals) == expected else 1)
PY
    then
      reason="invalid_throughput_csv"
    fi
  fi

  if [[ -n "$reason" ]]; then
    echo "$fig,$algo,$batch,$attempt,$reason" >> "$FAILED_ATTEMPTS"
    warn "Attempt failed: fig=$fig algo=$algo batch=$batch $pname=$pvalue attempt=$attempt reason=$reason"
    sleep "$BETWEEN_ATTEMPT_SLEEP"
    return 1
  fi

  finished="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  cp -f "$adir/throughput.csv" "$bdir/throughput.csv"
  write_manifest "$bdir/manifest.json" "$fig" "$algo" "$pname" "$pvalue" "$rtt" "$rwnd" \
    "$batch" "$attempt" "$position" "$started" "$finished"
  cp -f "$adir/tc_r1_wlan0.txt" "$bdir/tc_r1_wlan0.txt"
  cp -f "$adir/tc_r1_wlan1.txt" "$bdir/tc_r1_wlan1.txt"
  cp -f "$adir"/iw_r1-wlan*_*.txt "$bdir/" 2>/dev/null || true
  cp -f "$adir/tc_r4_eth0.txt" "$bdir/tc_r4_eth0.txt"
  cp -f "$adir/tc_r4_eth1.txt" "$bdir/tc_r4_eth1.txt"
  cp -f "$adir/tc_r2_eth0.txt" "$bdir/tc_r2_eth0.txt"
  cp -f "$adir/tc_r2_eth1.txt" "$bdir/tc_r2_eth1.txt"
  cp -f "$adir/tc_r3_eth0.txt" "$bdir/tc_r3_eth0.txt"
  cp -f "$adir/tc_r3_eth1.txt" "$bdir/tc_r3_eth1.txt"

  valid_batch_result "$bdir" "$fig" "$algo" "$pname" "$pvalue" "$rtt" "$rwnd" "$batch" "$position"
}

run_batch() {
  local fig="$1" algo="$2" pname="$3" pvalue="$4" rtt="$5" rwnd="$6" batch="$7" position="$8"
  local b2 bdir attempt
  b2="$(printf '%02d' "$batch")"
  if [[ "$fig" == "4a" ]]; then
    bdir="$RESULT_ROOT/figure4a/$algo/rtt_${pvalue}ms/batch_${b2}"
  else
    bdir="$RESULT_ROOT/figure4b/$algo/rwnd_${pvalue}MB/batch_${b2}"
  fi
  mkdir -p "$bdir"

  if valid_batch_result "$bdir" "$fig" "$algo" "$pname" "$pvalue" "$rtt" "$rwnd" "$batch" "$position"; then
    log "SKIP validated existing batch: fig=$fig algo=$algo batch=$batch $pname=$pvalue"
    return 0
  fi

  # Stale/incomplete result is quarantined rather than silently reused.
  if [[ -f "$bdir/throughput.csv" || -f "$bdir/manifest.json" ]]; then
    local q="$bdir/stale_$(date -u +%Y%m%dT%H%M%SZ)"
    mkdir -p "$q"
    mv "$bdir/throughput.csv" "$q/" 2>/dev/null || true
    mv "$bdir/manifest.json" "$q/" 2>/dev/null || true
    warn "Quarantined stale/mismatched batch result in $q"
  fi

  for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
    log "RUN fig=$fig algo=$algo batch=$batch/$BATCHES $pname=$pvalue attempt=$attempt/$MAX_ATTEMPTS"
    if run_attempt "$fig" "$algo" "$pname" "$pvalue" "$rtt" "$rwnd" "$batch" "$position" "$attempt" "$bdir"; then
      log "PASS fig=$fig algo=$algo batch=$batch $pname=$pvalue"
      return 0
    fi
  done
  die "Batch failed after $MAX_ATTEMPTS attempts: fig=$fig algo=$algo batch=$batch $pname=$pvalue"
}

record_order() {
  local fig="$1" algo="$2" batch="$3" pname="$4"; shift 4
  local values=("$@") pos=0 full
  full="$(IFS=' '; echo "${values[*]}")"
  for v in "${values[@]}"; do
    pos=$((pos + 1))
    echo "$fig,$algo,$batch,$pos,$pname,$v,$PARAM_ORDER_MODE,\"$full\"" >> "$RUN_ORDER_CSV"
  done
}

run_figure4a_for_batch() {
  local algo="$1" batch="$2" rtt pos=0
  mapfile -t order < <(ordered_values "$PARAM_ORDER_MODE" "$PARAM_ORDER_SEED" "$batch" "${RTTS[@]}")
  CURRENT_SWEEP_ORDER="$(IFS=' '; echo "${order[*]}")"
  record_order "4a" "$algo" "$batch" "rtt_ms" "${order[@]}"
  activate_cached_binaries "$algo" 1
  for rtt in "${order[@]}"; do
    pos=$((pos + 1))
    CURRENT_SWEEP_POSITION="$pos"
    run_batch 4a "$algo" rtt_ms "$rtt" "$rtt" 1 "$batch" "$pos"
  done
}

run_figure4b_for_batch() {
  local algo="$1" batch="$2" rwnd pos=0
  mapfile -t order < <(ordered_values "$PARAM_ORDER_MODE" "$PARAM_ORDER_SEED" "$batch" "${RWNDS[@]}")
  CURRENT_SWEEP_ORDER="$(IFS=' '; echo "${order[*]}")"
  record_order "4b" "$algo" "$batch" "rwnd_mb" "${order[@]}"
  for rwnd in "${order[@]}"; do
    pos=$((pos + 1))
    CURRENT_SWEEP_POSITION="$pos"
    activate_cached_binaries "$algo" "$rwnd"
    run_batch 4b "$algo" rwnd_mb "$rwnd" 100 "$rwnd" "$batch" "$pos"
  done
}

finalize_phase() {
  local algo="$1" phase="$2"
  log "STRICT VALIDATE + AGGREGATE + PLOT algorithm=$algo phase=$phase"
  python3 "$ANALYZER" "$RESULT_ROOT" --algorithm "$algo" --phase "$phase" \
    --expected-batches "$BATCHES" --expected-repeats "$REPEATS_PER_BATCH"
  python3 "$ANALYZER" "$RESULT_ROOT" --combined
}

maybe_clean_phase() {
  local algo="$1" phase="$2"
  [[ "$CLEAN_AFTER_ALGO" == "1" ]] || return 0
  local marker="$RESULT_ROOT/summary/$algo/.figure${phase}.complete"
  local dir
  if [[ "$phase" == "4a" ]]; then
    marker="$RESULT_ROOT/summary/$algo/.figure4a.complete"
    dir="$RESULT_ROOT/figure4a/$algo"
  else
    marker="$RESULT_ROOT/summary/$algo/.figure4b.complete"
    dir="$RESULT_ROOT/figure4b/$algo"
  fi
  [[ -f "$marker" ]] || die "Refusing cleanup: completion marker missing for $algo $phase"
  tar -C "$(dirname "$dir")" -czf "$RESULT_ROOT/log_archive_${algo}_${phase}.tar.gz" "$(basename "$dir")"
  rm -rf "$dir"
  log "Archived then removed bulky raw phase directory for $algo $phase"
}

log "Results: $RESULT_ROOT"
log "Algorithm execution order: ${ALGORITHMS[*]}"
log "Parameter order mode: $PARAM_ORDER_MODE"

for algo in "${ALGORITHMS[@]}"; do
  log "========== START algorithm=$algo =========="
  prepare_binaries_for_algo "$algo"

  # Figure 4a must be fully completed, strictly validated, summarized and plotted first.
  if [[ "$RUN_FIG4A" == "1" ]]; then
    if [[ -f "$RESULT_ROOT/summary/$algo/.figure4a.complete" ]]; then
      log "SKIP already finalized Figure 4a algorithm=$algo"
    else
      log "----- START Figure 4a algorithm=$algo -----"
      for batch in $(seq 1 "$BATCHES"); do
        log "===== Figure 4a algo=$algo batch=$batch/$BATCHES ====="
        run_figure4a_for_batch "$algo" "$batch"
      done
      finalize_phase "$algo" 4a
      maybe_clean_phase "$algo" 4a
      log "----- DONE + VALIDATED + PLOTTED Figure 4a algorithm=$algo -----"
    fi
  fi

  # Figure 4b starts only after Figure 4a has been finalized when 4a is requested.
  if [[ "$RUN_FIG4B" == "1" ]]; then
    if [[ "$RUN_FIG4A" == "1" && ! -f "$RESULT_ROOT/summary/$algo/.figure4a.complete" ]]; then
      die "Refusing Figure 4b before Figure 4a completion for algorithm=$algo"
    fi
    if [[ -f "$RESULT_ROOT/summary/$algo/.figure4b.complete" ]]; then
      log "SKIP already finalized Figure 4b algorithm=$algo"
    else
      log "----- START Figure 4b algorithm=$algo -----"
      for batch in $(seq 1 "$BATCHES"); do
        log "===== Figure 4b algo=$algo batch=$batch/$BATCHES ====="
        run_figure4b_for_batch "$algo" "$batch"
      done
      finalize_phase "$algo" 4b
      maybe_clean_phase "$algo" 4b
      log "----- DONE + VALIDATED + PLOTTED Figure 4b algorithm=$algo -----"
    fi
  fi

  required_ok=1
  [[ "$RUN_FIG4A" != "1" || -f "$RESULT_ROOT/summary/$algo/.figure4a.complete" ]] || required_ok=0
  [[ "$RUN_FIG4B" != "1" || -f "$RESULT_ROOT/summary/$algo/.figure4b.complete" ]] || required_ok=0
  if [[ "$required_ok" == "1" ]]; then
    touch "$RESULT_ROOT/summary/$algo/.complete"
  else
    rm -f "$RESULT_ROOT/summary/$algo/.complete"
    die "Algorithm incomplete: $algo"
  fi
  log "========== DONE algorithm=$algo =========="
done

log "Refreshing final cumulative plots and report"
python3 "$ANALYZER" "$RESULT_ROOT" --combined

log "DONE: $RESULT_ROOT"
log "Combined Figure 4a: $RESULT_ROOT/figure4a.png"
log "Combined Figure 4b: $RESULT_ROOT/figure4b.png"
log "Automatic report: $RESULT_ROOT/report.md"
log "Raw logs retained by default (CLEAN_AFTER_ALGO=$CLEAN_AFTER_ALGO)"
