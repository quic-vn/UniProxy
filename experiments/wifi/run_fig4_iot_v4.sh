#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# IoT-gateway runner v4 for example/topo_iot_v4.py = run_fig4_iot_v3.sh (kept unchanged) +
# LOSS_PCT (topology --loss: random loss in % on the four wired netem qdiscs, e.g. 0.1 as in
# experiments/network_variation), verified numerically in the r2/r3_delay readiness checks and
# recorded in metadata (loss_pct). Results default to results/figure4_iot_v4/<stamp>.
# Everything else (wireless setup, cake, rwnd, mobility, checks) is v3's:
#
# IoT-gateway runner v3 for example/topo_iot_v3.py: run_fig4_iot_v2.sh (kept unchanged as the
# control) + JITTER_MS (topology --jitter, netem delay variation on the wired qdiscs, verified
# in the r2/r3_delay readiness checks and recorded in metadata). Wireless setup identical to v2.
#   h1 ~~ r1-wlan0 (IoT AP, setMasterMode) | r1-wlan1 ~~ ap1 -- r2 -- r4 (Path 1)
#                                          | r1-wlan2 ~~ ap2 -- r3 -- r4 (Path 2)
# run_fig4_iot.sh + topo_iot.py (two Wi-Fi hops per path) are kept as the control.
#
# - The IoT AP must come from the official Mininet-WiFi API: iot_ap_method.txt has to
#   read "api" (ALLOW_MANUAL_IOT_AP=1 accepts "manual", for reproducing topo_iot.py).
# - TPROXY rules + proxy client on r1 come from client_iot.sh (IOT_IF=r1-wlan0), the
#   same script used for manual runs; its sha256 is recorded in metadata.
#
# - TOPO_ARGS carries distances/mobility, e.g.
#   TOPO_ARGS="--r1-move=-40,0:40,0 --h1-follow --mob-period 20".
#   The runner adds --bw1/--bw2 (CAKE_BW1/2), --delay2 RTT/2, --mob-trigger go and
#   --log-dir <attempt dir>; these must not appear in TOPO_ARGS.
# - Mobility is synchronised with the workload: the h1 command creates
#   <attempt>/mob_go and workload_start.txt right before client.py, so every run
#   starts at point A of the trajectory at workload start.
# - TPROXY intercepts on r1-wlan0 (the IoT AP).
# - Path delays are on the wired side (uplink r2-eth1/r3-eth1, downlink
#   r4-eth0/r4-eth1 with cake as netem child); no netem on r2-eth0/r3-eth0.
# - Readiness: r1-wlan1/r1-wlan2/h1-wlan0 associated to their SSID with signal
#   >= MIN_SIGNAL_DBM, r1-wlan0 in AP mode (api), qdiscs/delays, servers, TPROXY.
# - During the workload: ping every PING_INTERVAL s end-to-end per path
#   (r1 10.0.1.1/10.0.2.1 -> r5 10.0.6.2), per Wi-Fi hop (r1 -> 10.0.1.2/10.0.2.2)
#   and on the IoT hop (h1 -> 10.0.0.2); the topology logs positions.csv,
#   radio_<intf>.csv, ifstats_<intf>.csv (per-path byte counters) and vmstat.txt
#   at 1 Hz. All are copied to the batch dir.
# - Environment versions (kernel, Mininet-WiFi, wmediumd, hostapd, iw, ovs) are
#   written to metadata and checked on resume.
# - RTTS_STR selects the Path-2 RTT points (default "20 40 60 80 100").
# - Defaults: CAKE_BW1=16 CAKE_BW2=8 FIG4A_RWND_MB=0.16 (= 1 MB x 24/150;
#   provisional until calibrated for this topology).
# - Results default to results/figure4_iot_v2/<stamp>.
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

# Cake shaper rates (integer Mbit) of path 1 / path 2, passed to the topology as
# --bw1/--bw2 (2:1 like the wired 100/50 setup). One 802.11a hop gives ~24.5 Mbit/s
# TCP at 70 m and ~13.4 at 110 m (calibration of 2026-09-26, exp=3).
CAKE_BW1="${CAKE_BW1:-16}"
CAKE_BW2="${CAKE_BW2:-8}"

# Receive window (MB) used for Figure 4a. The wired setup uses 1 MB at 150 Mbit/s;
# scaled with capacity to keep the same rwnd/BDP ratio (1 MB x 24/150 = 0.16 MB).
FIG4A_RWND_MB="${FIG4A_RWND_MB:-0.16}"

# Readiness: minimum signal (dBm) of r1-wlan0, r1-wlan1 and h1-wlan0.
MIN_SIGNAL_DBM="${MIN_SIGNAL_DBM:--89}"
# Interval (s) of the pings that run during the workload.
PING_INTERVAL="${PING_INTERVAL:-0.5}"
# netem delay variation (ms) on the wired qdiscs of both paths (topology --jitter); 0 = none.
JITTER_MS="${JITTER_MS:-0}"
# netem random loss (%, no correlation) on the same wired qdiscs (topology --loss); 0 = none.
LOSS_PCT="${LOSS_PCT:-0}"
# Interfaces of r1 in topo_iot_v2.py: IoT AP (TPROXY ingress), path 1, path 2.
IOT_IF="${IOT_IF:-r1-wlan0}"
P1_IF="${P1_IF:-r1-wlan1}"
P2_IF="${P2_IF:-r1-wlan2}"
# 1 accepts iot_ap_method=manual (hostapd started by the topology, topo_iot.py behaviour).
ALLOW_MANUAL_IOT_AP="${ALLOW_MANUAL_IOT_AP:-0}"

# Sanity/report thresholds consumed by analyzer.
THEORETICAL_CAPACITY_MBPS="${THEORETICAL_CAPACITY_MBPS:-$((CAKE_BW1 + CAKE_BW2))}"
CAPACITY_TOLERANCE_PCT="${CAPACITY_TOLERANCE_PCT:-5}"
CV_WARNING_PCT="${CV_WARNING_PCT:-10}"

# Path-2 RTT points (ms), e.g. RTTS_STR="20 60 100".
read -r -a RTTS <<< "${RTTS_STR:-20 40 60 80 100}"
RWNDS=(1 1.25 1.5 1.75 2 2.25 2.5 5 10)
read -r -a ALGORITHMS_INPUT <<< "$ALGORITHMS_STR"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RESULT_ROOT="${RESULT_ROOT:-$REPO/results/figure4_iot_v4/$STAMP}"
ANALYZER="${ANALYZER:-$SCRIPT_DIR/analyze_fig4_wifi.py}"

SCHED="$REPO/mp-quic/scheduler.go"
PROTOCOL="$REPO/mp-quic/internal/protocol/protocol.go"
SERVER_PARAMS="$REPO/mp-quic/internal/protocol/server_parameters.go"
TOPO="${TOPO:-$REPO/example/topo_iot_v4.py}"
TOPO_ARGS="${TOPO_ARGS:-}"
NATIVE_BW=0
[[ " $TOPO_ARGS " == *" --native-bw "* ]] && NATIVE_BW=1
MOBILE=0
[[ " $TOPO_ARGS " == *" --r1-move"* || " $TOPO_ARGS " == *" --h1-move"* ]] && MOBILE=1
WORKLOAD="$REPO/example/client.py"
CLIENT_SCRIPT="$REPO/client_iot.sh"
CLIENT_CSV="$REPO/example/throughput.csv"
CLIENT_BIN="$REPO/quic-tun/client/cmd/cmd"
SERVER_BIN="$REPO/quic-tun/server/cmd/cmd"

TMP_BACKUP="$(mktemp -d)"
chmod 755 "$TMP_BACKUP"
# Numeric netem check: tc prints delay/jitter after kernel tick rounding (0.25 ms -> "249us"),
# so compare values with a tolerance instead of matching strings. v4: the loss field
# ("loss 0.1%", printed after delay/jitter) must equal LOSS_PCT, or be absent when it is 0.
NETEM_CHECK="$TMP_BACKUP/netem_check.py"
cat > "$NETEM_CHECK" <<'NETEMPY'
import re, sys
path, want_delay, want_jitter, want_loss = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
unit = {'s': 1000.0, 'ms': 1.0, 'us': 0.001}
lines = [l for l in open(path) if 'netem' in l]
if len(lines) != 1:
    sys.exit(1)
m = re.search(r'delay (\d+(?:\.\d+)?)(s|ms|us)(?:\s+(\d+(?:\.\d+)?)(s|ms|us))?', lines[0])
if not m:
    sys.exit(1)
delay = float(m.group(1)) * unit[m.group(2)]
jitter = float(m.group(3)) * unit[m.group(4)] if m.group(3) else 0.0
losses = re.findall(r'\bloss (?:random )?(\d+(?:\.\d+)?)%', lines[0])
if re.search(r'\bloss\b', lines[0]) and len(losses) != 1:
    sys.exit(1)
loss = float(losses[0]) if losses else 0.0
ok_delay = abs(delay - want_delay) <= max(0.01, 0.01 * want_delay)
ok_jitter = (jitter == 0.0) if want_jitter == 0 else abs(jitter - want_jitter) <= max(0.005, 0.02 * want_jitter)
ok_loss = (not losses) if want_loss == 0 else abs(loss - want_loss) <= max(1e-6, 0.01 * want_loss)
sys.exit(0 if ok_delay and ok_jitter and ok_loss else 1)
NETEMPY
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
# Seconds the system has spent suspended since boot (CLOCK_BOOTTIME - CLOCK_MONOTONIC).
suspended_s() { python3 -c 'import time; print("%.3f" % (time.clock_gettime(time.CLOCK_BOOTTIME) - time.clock_gettime(time.CLOCK_MONOTONIC)))'; }

validate_modes() {
  case "$PARAM_ORDER_MODE" in fixed|balanced|random) ;; *) die "PARAM_ORDER_MODE must be fixed, balanced, or random" ;; esac
  case "$ALGORITHM_ORDER_MODE" in fixed|random) ;; *) die "ALGORITHM_ORDER_MODE must be fixed or random" ;; esac
  [[ "$BATCHES" =~ ^[1-9][0-9]*$ ]] || die "BATCHES must be a positive integer"
  [[ "$REPEATS_PER_BATCH" =~ ^[1-9][0-9]*$ ]] || die "REPEATS_PER_BATCH must be a positive integer"
  [[ "$MAX_ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || die "MAX_ATTEMPTS must be a positive integer"
  [[ "$CAKE_BW1" =~ ^[1-9][0-9]*$ && "$CAKE_BW2" =~ ^[1-9][0-9]*$ ]] || die "CAKE_BW1/CAKE_BW2 must be positive integers (Mbit)"
  [[ "$FIG4A_RWND_MB" =~ ^[0-9]+(\.[0-9]+)?$ && "$FIG4A_RWND_MB" != "0" ]] || die "FIG4A_RWND_MB must be a positive number (MB)"
  [[ "${#RTTS[@]}" -gt 0 ]] || die "RTTS_STR is empty"
  local r; for r in "${RTTS[@]}"; do
    [[ "$r" =~ ^[1-9][0-9]*$ && $((r % 2)) -eq 0 ]] || die "RTTS_STR values must be positive even integers (ms): got '$r'"
  done
  [[ "$JITTER_MS" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "JITTER_MS must be a non-negative number (ms)"
  [[ "$LOSS_PCT" =~ ^[0-9]+(\.[0-9]+)?$ ]] && python3 -c "import sys; sys.exit(0 if float('$LOSS_PCT') < 100 else 1)" || \
    die "LOSS_PCT must be a number in [0, 100) without '%' (e.g. 0.1)"
  local opt; for opt in --bw1 --bw2 --delay1 --delay2 --jitter --loss --log-dir --mob-trigger; do
    [[ " $TOPO_ARGS " != *" $opt "* && " $TOPO_ARGS " != *" $opt="* ]] || die "$opt is set by the runner; remove it from TOPO_ARGS"
  done
  [[ "$MIN_SIGNAL_DBM" =~ ^-[1-9][0-9]*$ ]] || die "MIN_SIGNAL_DBM must be a negative integer (dBm)"
  [[ "$PING_INTERVAL" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "PING_INTERVAL must be a number (s)"
  if [[ "$ALLOW_MANUAL_IOT_AP" != "1" ]]; then
    [[ " $TOPO_ARGS " != *" --iot-ap-method"* ]] || die "--iot-ap-method in TOPO_ARGS needs ALLOW_MANUAL_IOT_AP=1 (official setMasterMode is required by default)"
  fi
}

# Tool/library versions of the emulation environment (Mininet-WiFi as seen by
# the root python3 that runs the topology).
env_info() {
  local wm
  echo "env_os=$(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown}")"
  echo "env_kernel=$(uname -r)"
  echo "env_python=$(sudo -n python3 --version 2>&1)"
  echo "env_mn_wifi=$(sudo -n python3 -c 'import os, mn_wifi; from mn_wifi.net import VERSION; print(VERSION, os.path.dirname(mn_wifi.__file__))' 2>&1 | tail -1)"
  wm="$(command -v wmediumd || true)"
  echo "env_wmediumd=${wm:-missing} sha256=$([[ -n "$wm" ]] && sha256_file "$wm" || echo none)"
  echo "env_hostapd=$(hostapd -v 2>&1 | grep -m1 -i hostapd || echo unknown)"
  echo "env_iw=$(iw --version 2>&1 | head -1)"
  echo "env_ovs=$(ovs-vsctl --version 2>/dev/null | head -1 || echo unknown)"
  echo "env_tc=$(tc -V 2>&1 | head -1)"
  echo "env_host_src_valid_mark=$(sysctl -n net.ipv4.conf.all.src_valid_mark 2>/dev/null || echo unknown)"
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
[[ -f "$CLIENT_SCRIPT" ]] || die "Missing $CLIENT_SCRIPT"
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
CLIENT_SCRIPT_SHA="$(sha256_file "$CLIENT_SCRIPT")"
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
  echo "client_script_sha256=$CLIENT_SCRIPT_SHA"
  echo "iot_if=$IOT_IF"
  echo "allow_manual_iot_ap=$ALLOW_MANUAL_IOT_AP"
  echo "native_bw=$NATIVE_BW"
  echo "cake_bw1_mbit=$CAKE_BW1"
  echo "cake_bw2_mbit=$CAKE_BW2"
  echo "figure4a_rwnd_mb=$FIG4A_RWND_MB"
  echo "min_signal_dbm=$MIN_SIGNAL_DBM"
  echo "ping_interval_s=$PING_INTERVAL"
  echo "jitter_ms=$JITTER_MS"
  echo "loss_pct=$LOSS_PCT"
  env_info
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
    "workload_sha256","topology_sha256","topology_args","cake_bw1_mbit","cake_bw2_mbit","figure4a_rwnd_mb",
    "min_signal_dbm","ping_interval_s","jitter_ms","loss_pct","client_script_sha256","iot_if","allow_manual_iot_ap",
    "env_kernel","env_python","env_mn_wifi","env_wmediumd","env_hostapd","env_iw","env_ovs","env_tc",
    "runner_sha256","analyzer_sha256",
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
# FQSAT: thuat toan "TRR_FQ" = SCHE_ALGO "TRR" + FQ_ENABLE true; moi thuat toan khac => FQ_ENABLE false
sche, fq = (("TRR", "true") if algo == "TRR_FQ" else (algo, "false"))
pat = r'(?m)^(\s*const\s+SCHE_ALGO(?:\s+\w+)?\s*=\s*)"[^"]+"(.*)$'
s2, n = re.subn(pat, lambda m: f'{m.group(1)}"{sche}"{m.group(2)}', s, count=1)
if n != 1:
    raise SystemExit(f"Could not uniquely patch SCHE_ALGO in {path}; matches={n}")
pat_fq = r'(?m)^(\s*FQ_ENABLE\s*=\s*)(true|false)(.*)$'
s3, nfq = re.subn(pat_fq, lambda m: f'{m.group(1)}{fq}{m.group(3)}', s2, count=1)
if algo == "TRR_FQ" and nfq != 1:
    raise SystemExit(f"TRR_FQ requested but FQ_ENABLE not found in {path}")
open(path, "w", encoding="utf-8").write(s3 if nfq == 1 else s2)
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
  set_rwnd_mb "$FIG4A_RWND_MB"
  build_proxies "algo=$algo rwnd=${FIG4A_RWND_MB}MB"
  cache_current_binaries "$algo" "$FIG4A_RWND_MB"

  if [[ "$RUN_FIG4B" == "1" ]]; then
    for rwnd in "${RWNDS[@]}"; do
      [[ "$rwnd" == "$FIG4A_RWND_MB" ]] && continue
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
  local susp0 susp1
  susp0="$(suspended_s)"

  sudo -n mn -c >/dev/null 2>&1 || true
  sleep "$POST_MN_C_SLEEP"
  sudo -n rm -f "$CLIENT_CSV" || true

  half=$((rtt / 2))
  cmdfile="$adir/mininet.commands"

  # Shaper checks: cake CAKE_BW1/CAKE_BW2 Mbit on the path endpoints unless --native-bw.
  local r1_q_check="true" r4_q_check="true"
  if [[ "$NATIVE_BW" != "1" ]]; then
    r1_q_check="grep -qi \"cake.*bandwidth ${CAKE_BW1}Mbit\" \"$adir/tc_${P1_IF//-/_}.txt\" && grep -qi \"cake.*bandwidth ${CAKE_BW2}Mbit\" \"$adir/tc_${P2_IF//-/_}.txt\""
    r4_q_check="grep -qi \"cake.*bandwidth ${CAKE_BW1}Mbit\" \"$adir/tc_r4_eth0.txt\" && grep -qi \"cake.*bandwidth ${CAKE_BW2}Mbit\" \"$adir/tc_r4_eth1.txt\""
  fi

  local ping_deadline=$((WORKLOAD_TIMEOUT + 60))
  local all_ready='[ -f "$adir/h2_ready.ok" ] && [ -f "$adir/r5_ready.ok" ] && [ -f "$adir/tproxy_ready.ok" ] && [ -f "$adir/r1_ready.ok" ] && [ -f "$adir/path_interfaces.ok" ] && [ -f "$adir/h1_link.ok" ] && [ -f "$adir/iot_ap.ok" ] && [ -f "$adir/r1_qdisc.ok" ] && [ -f "$adir/r4_qdisc.ok" ] && [ -f "$adir/r2_delay.ok" ] && [ -f "$adir/r3_delay.ok" ]'
  all_ready="${all_ready//\$adir/$adir}"

  cat > "$cmdfile" <<EOF
# ----- Capture/validate topology state BEFORE workload -----
r1 bash -lc 'tc qdisc show dev ${P1_IF} > "$adir/tc_${P1_IF//-/_}.txt"; tc qdisc show dev ${P2_IF} > "$adir/tc_${P2_IF//-/_}.txt"; $r1_q_check && touch "$adir/r1_qdisc.ok"'
r4 bash -lc 'tc qdisc show dev r4-eth0 > "$adir/tc_r4_eth0.txt"; tc qdisc show dev r4-eth1 > "$adir/tc_r4_eth1.txt"; $r4_q_check && touch "$adir/r4_qdisc.ok"'
r2 bash -lc 'tc qdisc show dev r2-eth0 > "$adir/tc_r2_eth0.txt"; tc qdisc show dev r2-eth1 > "$adir/tc_r2_eth1.txt"; python3 $NETEM_CHECK "$adir/tc_r2_eth1.txt" 5 $JITTER_MS $LOSS_PCT && python3 $NETEM_CHECK "$adir/tc_r4_eth0.txt" 5 $JITTER_MS $LOSS_PCT && ! grep -q netem "$adir/tc_r2_eth0.txt" && touch "$adir/r2_delay.ok"'
r3 bash -lc 'tc qdisc show dev r3-eth0 > "$adir/tc_r3_eth0.txt"; tc qdisc show dev r3-eth1 > "$adir/tc_r3_eth1.txt"; python3 $NETEM_CHECK "$adir/tc_r3_eth1.txt" $half $JITTER_MS $LOSS_PCT && python3 $NETEM_CHECK "$adir/tc_r4_eth1.txt" $half $JITTER_MS $LOSS_PCT && ! grep -q netem "$adir/tc_r3_eth0.txt" && touch "$adir/r3_delay.ok"'
r1 bash -lc 'iw dev ${IOT_IF} info > "$adir/iw_${IOT_IF}_info.txt" 2>&1; grep -q "type AP" "$adir/iw_${IOT_IF}_info.txt" && { grep -qx api "$adir/iot_ap_method.txt" || { [ "${ALLOW_MANUAL_IOT_AP}" = 1 ] && grep -qx manual "$adir/iot_ap_method.txt"; }; } && touch "$adir/iot_ap.ok"'

# ----- Application + QUIC servers -----
h2 bash -lc 'cd "$REPO"; python3 example/server.py > "$adir/h2_server.log" 2>&1 & echo \$! > "$adir/h2_server.pid"'
r5 bash -lc 'cd "$REPO"; ./quic-tun/server/cmd/cmd --listen-on 10.0.6.2:7500 > "$adir/r5_server.log" 2>&1 & echo \$! > "$adir/r5_server.pid"'
sh sleep ${AFTER_SERVER_START_SLEEP}

h2 bash -lc 'for i in \$(seq 1 ${READY_RETRIES}); do pid=\$(cat "$adir/h2_server.pid" 2>/dev/null || true); if [ -n "\$pid" ] && kill -0 "\$pid" 2>/dev/null && ss -lnt | grep -q ":5000"; then touch "$adir/h2_ready.ok"; break; fi; sleep ${READY_INTERVAL}; done'
r5 bash -lc 'for i in \$(seq 1 ${READY_RETRIES}); do pid=\$(cat "$adir/r5_server.pid" 2>/dev/null || true); if [ -n "\$pid" ] && kill -0 "\$pid" 2>/dev/null && ss -lun | grep -q ":7500"; then touch "$adir/r5_ready.ok"; break; fi; sleep ${READY_INTERVAL}; done'

# ----- TPROXY/routing (client_iot.sh setup: src_valid_mark=0, ip rule/route, TPROXY rule) -----
r1 bash -lc 'cd "$REPO"; IOT_IF=${IOT_IF} bash client_iot.sh setup > "$adir/client_iot_setup.log" 2>&1'
sh sleep ${AFTER_TPROXY_SLEEP}

r1 bash -lc 'ip rule show > "$adir/ip_rule.txt"; ip route show table 100 > "$adir/route_table100.txt"; iptables -t mangle -S PREROUTING > "$adir/iptables_prerouting.txt"; ip rule show | grep -q "fwmark 0x1" && ip route show table 100 | grep -q "local" && iptables -t mangle -C PREROUTING --in-interface ${IOT_IF} -p tcp -d 10.0.7.0/24 -j TPROXY --on-port 6500 --on-ip 127.0.0.1 --tproxy-mark 1/1 && [ "\$(sysctl -n net.ipv4.conf.all.src_valid_mark)" = 0 ] && touch "$adir/tproxy_ready.ok"'

# ----- Proxy client (client_iot.sh exec's the binary, so the pid is the client's) -----
r1 bash -lc 'cd "$REPO"; IOT_IF=${IOT_IF} bash client_iot.sh > "$adir/r1_client.log" 2>&1 & echo \$! > "$adir/r1_client.pid"'
sh sleep ${AFTER_R1_START_SLEEP}

r1 bash -lc 'for i in \$(seq 1 ${READY_RETRIES}); do pid=\$(cat "$adir/r1_client.pid" 2>/dev/null || true); if [ -n "\$pid" ] && kill -0 "\$pid" 2>/dev/null && ss -lnt | grep -q "127.0.0.1:6500" && ! grep -q "fatal error:" "$adir/r1_client.log"; then touch "$adir/r1_ready.ok"; break; fi; sleep ${READY_INTERVAL}; done'

# ----- Wireless state + readiness (association to own SSID, signal >= ${MIN_SIGNAL_DBM} dBm) -----
r1 bash -lc 'for w in ${P1_IF} ${P2_IF}; do iw dev \$w link > "$adir/iw_\${w}_link_pre.txt" 2>&1; iw dev \$w station dump > "$adir/iw_\${w}_station_pre.txt" 2>&1; done'
ap1 bash -lc 'w=\$(iw dev | awk "/Interface ap1-wlan/{print \\\$2}" | head -1); iw dev \$w station dump > "$adir/iw_ap1_station_pre.txt" 2>&1'
ap2 bash -lc 'w=\$(iw dev | awk "/Interface ap2-wlan/{print \\\$2}" | head -1); iw dev \$w station dump > "$adir/iw_ap2_station_pre.txt" 2>&1'
r1 bash -lc 'iw dev ${IOT_IF} station dump > "$adir/iw_${IOT_IF}_ap_station_pre.txt" 2>&1'
h1 bash -lc 'f="$adir/iw_h1-wlan0_link_pre.txt"; iw dev h1-wlan0 link > "\$f" 2>&1; s=\$(grep -o "signal: -[0-9]*" "\$f" | grep -o -- "-[0-9]*"); grep -q "SSID: iot" "\$f" && [ -n "\$s" ] && [ "\$s" -ge ${MIN_SIGNAL_DBM} ] && ip -4 addr show h1-wlan0 | grep -q "inet " && touch "$adir/h1_link.ok"'
r1 bash -lc 'ok=1; for p in "${P1_IF} path1" "${P2_IF} path2"; do set -- \$p; f="$adir/iw_\${1}_link_pre.txt"; s=\$(grep -o "signal: -[0-9]*" "\$f" | grep -o -- "-[0-9]*"); { grep -q "SSID: \$2" "\$f" && [ -n "\$s" ] && [ "\$s" -ge ${MIN_SIGNAL_DBM} ] && ip -4 addr show \$1 | grep -q "inet "; } || ok=0; done; [ \$ok = 1 ] && touch "$adir/path_interfaces.ok"'

# ----- Pings during the workload (end-to-end per path, per Wi-Fi hop, IoT hop) -----
r1 bash -lc 'cd "$adir"; for s in "e2e_path1 10.0.1.1 10.0.6.2" "e2e_path2 10.0.2.1 10.0.6.2" "hop_path1 10.0.1.1 10.0.1.2" "hop_path2 10.0.2.1 10.0.2.2"; do set -- \$s; ping -D -n -i ${PING_INTERVAL} -w ${ping_deadline} -I \$2 \$3 > ping_\$1.txt 2>&1 & echo \$! > ping_\$1.pid; done'
h1 bash -lc 'cd "$adir"; ping -D -n -i ${PING_INTERVAL} -w ${ping_deadline} 10.0.0.2 > ping_iot.txt 2>&1 & echo \$! > ping_iot.pid'

# ----- Workload only if every readiness condition passed; mob_go starts the trajectory -----
h1 bash -lc 'if ${all_ready}; then touch "$adir/mob_go"; date +%s.%N > "$adir/workload_start.txt"; cd "$REPO/example" && timeout ${WORKLOAD_TIMEOUT}s python3 client.py > "$adir/h1_client.log" 2>&1 && touch "$adir/workload.ok"; date +%s.%N > "$adir/workload_end.txt"; else echo "SKIPPED: readiness validation failed" > "$adir/h1_client.log"; fi'

r1 bash -lc 'for p in "$adir"/ping_*.pid; do [ -f "\$p" ] && kill -INT \$(cat "\$p") 2>/dev/null; done; true'
sh sleep ${AFTER_WORKLOAD_SLEEP}

# ----- Post-workload wireless counters (tx retries/failed, signal, bitrate) -----
r1 bash -lc 'for w in ${P1_IF} ${P2_IF}; do iw dev \$w link > "$adir/iw_\${w}_link_post.txt" 2>&1; iw dev \$w station dump > "$adir/iw_\${w}_station_post.txt" 2>&1; done'
ap1 bash -lc 'w=\$(iw dev | awk "/Interface ap1-wlan/{print \\\$2}" | head -1); iw dev \$w station dump > "$adir/iw_ap1_station_post.txt" 2>&1'
ap2 bash -lc 'w=\$(iw dev | awk "/Interface ap2-wlan/{print \\\$2}" | head -1); iw dev \$w station dump > "$adir/iw_ap2_station_post.txt" 2>&1'
r1 bash -lc 'iw dev ${IOT_IF} station dump > "$adir/iw_${IOT_IF}_ap_station_post.txt" 2>&1'
h1 bash -lc 'iw dev h1-wlan0 link > "$adir/iw_h1-wlan0_link_post.txt" 2>&1; iw dev h1-wlan0 station dump > "$adir/iw_h1-wlan0_station_post.txt" 2>&1'

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
  if ! printf 'source %s\nexit\n' "$cmdfile" | sudo -n python3 "$TOPO" $TOPO_ARGS --bw1 "$CAKE_BW1" --bw2 "$CAKE_BW2" --delay2 "$half" --jitter "$JITTER_MS" --loss "$LOSS_PCT" --mob-trigger go --log-dir "$adir" > "$adir/mininet.log" 2>&1; then
    warn "Mininet returned nonzero for $adir"
  fi

  # Preserve actual throughput output only for this attempt.
  if [[ -f "$CLIENT_CSV" ]]; then
    sudo -n cat "$CLIENT_CSV" > "$adir/throughput.csv" || true
  fi
  sudo -n rm -f "$CLIENT_CSV" || true
  sudo -n mn -c >/dev/null 2>&1 || true

  susp1="$(suspended_s)"
  echo "suspended_s_before=$susp0 suspended_s_after=$susp1" > "$adir/suspend_check.txt"
  local reason=""
  # An attempt that spans a system suspend (laptop lid/idle) is never valid.
  if python3 -c "import sys; sys.exit(0 if float('$susp1') - float('$susp0') > 1 else 1)"; then
    reason="system_suspended"
  fi
  [[ -n "$reason" ]] ||
  for f in h2_ready.ok r5_ready.ok tproxy_ready.ok r1_ready.ok path_interfaces.ok h1_link.ok iot_ap.ok r1_qdisc.ok r4_qdisc.ok r2_delay.ok r3_delay.ok workload.ok r1_post.ok r5_post.ok h2_post.ok; do
    if [[ ! -f "$adir/$f" ]]; then reason="missing_${f%.ok}"; break; fi
  done
  if [[ -z "$reason" ]] && grep -Eqs 'Traceback|TimeoutError|ConnectionResetError|fatal error:' "$adir/h1_client.log" "$adir/r1_client.log" "$adir/r5_server.log" 2>/dev/null; then
    reason="runtime_error_in_logs"
  fi
  # A dead mobility thread does not stop Mininet: require evidence it ran.
  if [[ -z "$reason" && "$MOBILE" == "1" ]] && [[ ! -f "$adir/mob_started.txt" || -f "$adir/mob_error.txt" ]]; then
    reason="mobility_failed"
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
  local f
  for f in "$adir"/tc_*.txt "$adir"/iw_*.txt "$adir"/assoc_*.log "$adir"/ping_*.txt \
           "$adir"/radio_*.csv "$adir"/ifstats_*.csv "$adir"/positions.csv "$adir"/vmstat.txt \
           "$adir"/iot_ap_method.txt "$adir"/assoc_method.txt "$adir"/client_iot_setup.log "$adir"/suspend_check.txt \
           "$adir"/workload_start.txt "$adir"/workload_end.txt "$adir"/mob_started.txt "$adir"/mob_error.txt; do
    if [[ -f "$f" ]]; then cp -f "$f" "$bdir/"; fi
  done

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
  activate_cached_binaries "$algo" "$FIG4A_RWND_MB"
  for rtt in "${order[@]}"; do
    pos=$((pos + 1))
    CURRENT_SWEEP_POSITION="$pos"
    run_batch 4a "$algo" rtt_ms "$rtt" "$rtt" "$FIG4A_RWND_MB" "$batch" "$pos"
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
