#!/usr/bin/env bash
# Three dynamic / short-flow scenarios on the IoT v7 topology (example/topo_iot_v7.py, unchanged),
# runner experiments/wifi/run_fig4_iot_v9.sh (= v8 + WORKLOAD/WORKLOAD_ARGS + OUTAGE_*).
# Common: 802.11a, cake 40 / 25 Mbit/s, rwnd 1 MB, delay only (no jitter / loss), RTT path 1 =
# 10 ms, path 2 = RTTS_STR (default 20 60 100), 3 batches x 10 repeats, TRR RR SAPS, Figure 4a only.
# Scenarios (SCENARIOS, default all three, run in this order):
#   MOBILE_SWAP   path quality changes over time, anti-correlated: ap1 at x = 130, ap2 at x = 270
#                 (--dist1 70 --dist2 70, both outside r1's track so r1 never passes over an AP),
#                 r1 walks x = 140 <-> 260 (--r1-move=-60,0:60,0, 30 s per leg, reverse => 60 s
#                 cycle), h1 rides with r1 at 10 m (--h1-follow: constant IoT hop). Wi-Fi distance
#                 path 1: 10 -> 130 m, path 2: 130 -> 10 m (one 802.11a hop: ~36 Mbit/s at 10 m,
#                 ~9.5 at 130 m) => the low-RTT path is the good one at the start and the bad one
#                 half a cycle later. Workload client.py (bulk). Readiness >= -90 dBm (~-86 at 130 m).
#                 (The first smoke used --dist1 50 --dist2 50: APs on the track, r1 passed 0 m from
#                 them, signal up to -2 dBm.)
#   PATH1_OUTAGE  path failure and recovery: static 10 m (as v7), continuous bulk load of 5
#                 connections for 120 s (example/client_long.py), path 1's wired backhaul is
#                 blackholed (iptables DROP in r2) for OUTAGE_DUR = 10 s at 53 s and 93 s after
#                 workload start (~40 s and ~80 s into the load). Wi-Fi stays associated.
#   SHORT_FLOWS   short transfers (web / IoT objects): static 10 m, example/client_fct.py: Poisson
#                 arrivals 10 flows/s, sizes 10 kB / 100 kB / 1 MB (50/35/15 %), 10 rounds of 10 s,
#                 flow completion time per flow in fct.csv (offered ~15 Mbit/s, ~45 % of capacity).
# After the runs: cross_scenario.txt (throughput table: bulk Mbit/s for MOBILE_SWAP, per-window
# Mbit/s for PATH1_OUTAGE, short-flow goodput for SHORT_FLOWS - not comparable across scenarios),
# scenario_metrics.txt (analyze_iot_v9.py: rate per position bin, outage stall / recovery, FCT),
# path_stalls.txt (the outages appear there as path-1 stalls by design), signal_report.txt,
# plots_campaign/.  Rough duration: 27 batches x ~3 min per scenario = ~1.4 h, ~4.5 h in total.
# run_iot_v7_campaign.sh / run_iot_v8_rwnd_campaign.sh are kept unchanged.
#
# Usage (from the repo root, passwordless sudo):
#   bash experiments/wifi/run_iot_v9_campaign.sh 2>&1 | tee ~/iot_v9.log
# Env: SCENARIOS, BATCHES (3), RTTS_STR ("20 60 100"), ALGORITHMS_STR ("TRR RR SAPS"),
#      CAKE_BW1/CAKE_BW2 (40/25), FIG4A_RWND_MB (1), MOB_ARGS (MOBILE_SWAP topology args),
#      OUTAGE_AT ("53 93"), OUTAGE_DUR (10), LONG_ARGS (client_long.py args), FCT_ARGS
#      (client_fct.py args), MN_WIFI_SRC, CAMPAIGN_ROOT (default results/figure4_iot_v9/
#      campaign_<stamp>; reuse to resume).
set -Eeuo pipefail

REPO="${REPO:-$(pwd)}"
RUNNER="$REPO/experiments/wifi/run_fig4_iot_v9.sh"
SCENARIOS="${SCENARIOS:-MOBILE_SWAP PATH1_OUTAGE SHORT_FLOWS}"
CAMPAIGN_ROOT="${CAMPAIGN_ROOT:-$REPO/results/figure4_iot_v9/campaign_$(date -u +%Y%m%dT%H%M%SZ)}"
STATIC_ARGS="--dist1 10 --dist2 10 --h1-dist 10"
MOB_ARGS="${MOB_ARGS:---dist1 70 --dist2 70 --h1-dist 10 --h1-follow --r1-move=-60,0:60,0 --mob-period 30}"
OUTAGE_AT="${OUTAGE_AT:-53 93}"
OUTAGE_DUR="${OUTAGE_DUR:-10}"
LONG_ARGS="${LONG_ARGS:---conns 5 --duration 120}"
FCT_ARGS="${FCT_ARGS:---lam 10 --sizes 10000:0.5,100000:0.35,1000000:0.15}"
export CAKE_BW1="${CAKE_BW1:-40}" CAKE_BW2="${CAKE_BW2:-25}" FIG4A_RWND_MB="${FIG4A_RWND_MB:-1}"
export REPO
export BATCHES="${BATCHES:-3}"
export RTTS_STR="${RTTS_STR:-20 60 100}"
export ALGORITHMS_STR="${ALGORITHMS_STR:-TRR RR SAPS}"
export RUN_FIG4A=1 RUN_FIG4B=0

scenario_args() {
  case "$1" in
    MOBILE_SWAP) echo "$MOB_ARGS" ;;
    PATH1_OUTAGE|SHORT_FLOWS) echo "$STATIC_ARGS" ;;
    *) echo "unknown scenario $1" >&2; return 1 ;;
  esac
}
# Readiness signal: path 2 starts 130 m from ap2 in MOBILE_SWAP (~-86 dBm); ~-46..-53 dBm at 10 m.
scenario_min_signal() { case "$1" in MOBILE_SWAP) echo -90 ;; *) echo -65 ;; esac; }
scenario_workload() {
  case "$1" in
    PATH1_OUTAGE) echo "$REPO/example/client_long.py" ;;
    SHORT_FLOWS) echo "$REPO/example/client_fct.py" ;;
    *) echo "$REPO/example/client.py" ;;
  esac
}
scenario_workload_args() {
  case "$1" in PATH1_OUTAGE) echo "$LONG_ARGS" ;; SHORT_FLOWS) echo "$FCT_ARGS" ;; *) echo "" ;; esac
}
scenario_outage_path() { case "$1" in PATH1_OUTAGE) echo 1 ;; *) echo 0 ;; esac; }

[[ -f "$RUNNER" ]] || { echo "ERROR: $RUNNER not found (run from repo root or set REPO)" >&2; exit 1; }
for f in example/client.py example/client_long.py example/client_fct.py experiments/wifi/analyze_iot_v9.py; do
  [[ -f "$REPO/$f" ]] || { echo "ERROR: missing $REPO/$f" >&2; exit 1; }
done
sudo -n true 2>/dev/null || { echo "ERROR: run 'sudo -v' first" >&2; exit 1; }
for s in $SCENARIOS; do scenario_args "$s" > /dev/null; done
mkdir -p "$CAMPAIGN_ROOT"

{
  echo "scenarios=$SCENARIOS"
  for s in $SCENARIOS; do
    echo "scenario_${s}_topo_args=$(scenario_args "$s")"
    echo "scenario_${s}_min_signal_dbm=$(scenario_min_signal "$s")"
    echo "scenario_${s}_workload=$(scenario_workload "$s") $(scenario_workload_args "$s")"
    echo "scenario_${s}_workload_sha256=$(sha256sum "$(scenario_workload "$s")" | awk '{print $1}')"
    echo "scenario_${s}_outage=path $(scenario_outage_path "$s") at $OUTAGE_AT dur $OUTAGE_DUR"
  done
  echo "batches=$BATCHES rtts=$RTTS_STR algorithms=$ALGORITHMS_STR run_fig4b=$RUN_FIG4B"
  echo "cake_bw1=$CAKE_BW1 cake_bw2=$CAKE_BW2 fig4a_rwnd_mb=$FIG4A_RWND_MB"
  echo "client_py_connections=$(grep -oP '^\s*num\s*=\s*\K\d+' "$REPO/example/client.py" | head -1)"
} > "$CAMPAIGN_ROOT/campaign.txt"
echo "[$(date '+%F %T')] campaign root: $CAMPAIGN_ROOT"
grep -E '^(scenarios|batches|client_py_connections)=' "$CAMPAIGN_ROOT/campaign.txt"

# A scenario that fails (runner gives up after MAX_ATTEMPTS) does not stop the next one;
# rerun with the same CAMPAIGN_ROOT to resume it (validated batches are skipped).
failed=""
for s in $SCENARIOS; do
  echo "[$(date '+%F %T')] ===== SCENARIO $s ====="
  RESULT_ROOT="$CAMPAIGN_ROOT/$s" TOPO_ARGS="$(scenario_args "$s")" \
    MIN_SIGNAL_DBM="$(scenario_min_signal "$s")" JITTER_MS=0 JITTER_PCT=0 LOSS_PCT=0 \
    WORKLOAD="$(scenario_workload "$s")" WORKLOAD_ARGS="$(scenario_workload_args "$s")" \
    OUTAGE_PATH="$(scenario_outage_path "$s")" OUTAGE_AT="$OUTAGE_AT" OUTAGE_DUR="$OUTAGE_DUR" \
    bash "$RUNNER" || {
      echo "[$(date '+%F %T')] SCENARIO $s FAILED; resume with CAMPAIGN_ROOT=$CAMPAIGN_ROOT" >&2
      failed+=" $s"
    }
done

python3 - "$CAMPAIGN_ROOT" $SCENARIOS <<'PY' | tee "$CAMPAIGN_ROOT/cross_scenario.txt"
import csv, pathlib, sys
root, scen = pathlib.Path(sys.argv[1]), sys.argv[2:]
unit = {'MOBILE_SWAP': 'bulk Mbit/s', 'PATH1_OUTAGE': 'Mbit/s per 12 s window of a 120 s load (incl. outages)',
        'SHORT_FLOWS': 'short-flow goodput Mbit/s (~ offered load; see FCT in scenario_metrics.txt)'}
for s in scen:
    p = root / s / 'figure4a_summary.csv'
    if not p.exists():
        print('%s: no summary' % s)
        continue
    S = {(r['algorithm'], int(r['rtt_ms'])): (float(r['mean_mbps']), float(r['batch_mean_ci95_mbps'])) for r in csv.DictReader(open(p))}
    algos = [a for a in ('TRR', 'RR', 'SAPS') if any(k[0] == a for k in S)] + sorted({k[0] for k in S} - {'TRR', 'RR', 'SAPS'})
    rtts = sorted({k[1] for k in S})
    print('%s (%s), mean +- CI95' % (s, unit.get(s, 'Mbit/s')))
    print('%-6s ' % 'algo' + ' '.join('%16s' % ('RTT %d' % t) for t in rtts))
    for a in algos:
        print('%-6s ' % a + ' '.join(('%9.2f +-%5.2f' % S[(a, t)]) if (a, t) in S else '%16s' % '-' for t in rtts))
    for b in ('RR', 'SAPS'):
        if ('TRR', rtts[0]) in S:
            print('TRR vs %-5s ' % b + ' '.join(('%+15.1f%%' % (100 * (S[('TRR', t)][0] / S[(b, t)][0] - 1))) if ('TRR', t) in S and (b, t) in S else '%16s' % '-' for t in rtts))
    print()
PY
python3 "$REPO/experiments/wifi/analyze_iot_v9.py" "$CAMPAIGN_ROOT" || true
python3 "$REPO/experiments/wifi/path_stalls.py" "$CAMPAIGN_ROOT" --csv "$CAMPAIGN_ROOT/path_stalls.csv" | tee "$CAMPAIGN_ROOT/path_stalls.txt"
python3 "$REPO/experiments/wifi/signal_report.py" "$CAMPAIGN_ROOT" --csv "$CAMPAIGN_ROOT/signal_report.csv" | tee "$CAMPAIGN_ROOT/signal_report.txt"
python3 "$REPO/experiments/wifi/plot_campaign.py" "$CAMPAIGN_ROOT" --fig 4a || true
if [[ -n "$failed" ]]; then
  echo "[$(date '+%F %T')] CAMPAIGN INCOMPLETE (failed:$failed): $CAMPAIGN_ROOT"
  exit 1
fi
echo "[$(date '+%F %T')] CAMPAIGN DONE: $CAMPAIGN_ROOT"
