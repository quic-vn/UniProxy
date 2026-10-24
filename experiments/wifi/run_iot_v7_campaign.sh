#!/usr/bin/env bash
# Static Figure-4a-style campaign on the IoT v7 topology (example/topo_iot_v7.py, runner
# experiments/wifi/run_fig4_iot_v7.sh), configuration of the figure of 2026-10-05:
#   h1 ~~ r1 (10 m) ~~ ap1 (10 m) -- r2 -- r4 -- r5 -- h2 ; r1 ~~ ap2 (10 m) -- r3 -- r4
#   802.11a everywhere, cake 40 (path 1) / 25 (path 2) Mbit/s, rwnd 1 MB (not scaled),
#   netem one-way delay 5 ms (path 1) and RTT/2 (path 2), h1 and r1 static.
# Scenarios (SCENARIOS, default both):
#   STATIC_DELAY        delay only.
#   STATIC_JITTER_LOSS  + jitter 8 % of each path's one-way delay (0.4 ms / RTT/2 x 8 %) + loss 0.5 %.
# Defaults: RTT 20 60 100, 3 batches, TRR RR SAPS. Readiness signal >= -65 dBm (expected ~-46 dBm
# at 10 m). Caveat recorded in the runner header: one 802.11a hop carries ~36 Mbit/s, so the
# Wi-Fi hops, not cake, bound the throughput here (sum of cake = 65 Mbit/s is only the metadata
# "theoretical capacity"). After the runs, signal_report.py summarises signal / PHY rate / MAC
# retries of h1-wlan0, r1-wlan1, r1-wlan2 and the AP sides from the batch logs.
# run_iot_v5_campaign.sh / run_iot_v6_campaign.sh are kept unchanged.
#
# Usage (from the repo root, passwordless sudo):
#   bash experiments/wifi/run_iot_v7_campaign.sh 2>&1 | tee ~/iot_v7_campaign.log
# Env: SCENARIOS, BATCHES (3), RTTS_STR ("20 60 100"), ALGORITHMS_STR ("TRR RR SAPS"),
#      CAKE_BW1/CAKE_BW2/FIG4A_RWND_MB (40/25/1), JITTER_PCT (8) / LOSS_PCT (0.5) overrides for the
#      impairment scenario, DIST (10, all three Wi-Fi distances in m), MIN_SIGNAL_DBM (-65),
#      MN_WIFI_SRC, REF_CAMPAIGN (default none), CAMPAIGN_ROOT (default
#      results/figure4_iot_v7/campaign_<stamp>; reuse to resume).
set -Eeuo pipefail

REPO="${REPO:-$(pwd)}"
RUNNER="$REPO/experiments/wifi/run_fig4_iot_v7.sh"
SCENARIOS="${SCENARIOS:-STATIC_DELAY STATIC_JITTER_LOSS}"
CAMPAIGN_ROOT="${CAMPAIGN_ROOT:-$REPO/results/figure4_iot_v7_10conns/campaign_$(date -u +%Y%m%dT%H%M%SZ)}"
REF_CAMPAIGN="${REF_CAMPAIGN:-none}"
DIST="${DIST:-10}"
export CAKE_BW1="${CAKE_BW1:-40}" CAKE_BW2="${CAKE_BW2:-25}" FIG4A_RWND_MB="${FIG4A_RWND_MB:-1}"
export REPO
export BATCHES="${BATCHES:-3}"
export RTTS_STR="${RTTS_STR:-20 60 100}"
export ALGORITHMS_STR="${ALGORITHMS_STR:-TRR RR SAPS}"
export RUN_FIG4A=1 RUN_FIG4B="${RUN_FIG4B:-0}"

scenario_args() {
  case "$1" in
    STATIC_DELAY|STATIC_JITTER_LOSS) echo "--dist1 $DIST --dist2 $DIST --h1-dist $DIST" ;;
    *) echo "unknown scenario $1" >&2; return 1 ;;
  esac
}

# Readiness signal threshold (runner MIN_SIGNAL_DBM): ~-46 dBm expected at 10 m (802.11a, exp=3).
scenario_min_signal() { echo -65; }

# Wired netem jitter (% of each path's one-way delay) and loss (%) per scenario unless
# JITTER_PCT / LOSS_PCT are set. JITTER_MS stays 0 (exclusive with JITTER_PCT).
scenario_jitter_pct() {
  case "$1" in STATIC_JITTER_LOSS) echo 8 ;; *) echo 0 ;; esac
}
scenario_loss() {
  case "$1" in STATIC_JITTER_LOSS) echo 0.5 ;; *) echo 0 ;; esac
}

[[ -f "$RUNNER" ]] || { echo "ERROR: $RUNNER not found (run from repo root or set REPO)" >&2; exit 1; }
sudo -n true 2>/dev/null || { echo "ERROR: run 'sudo -v' first" >&2; exit 1; }
for s in $SCENARIOS; do scenario_args "$s" > /dev/null; done
mkdir -p "$CAMPAIGN_ROOT"

{
  echo "scenarios=$SCENARIOS"
  for s in $SCENARIOS; do
    echo "scenario_${s}_topo_args=$(scenario_args "$s")"
    echo "scenario_${s}_min_signal_dbm=${MIN_SIGNAL_DBM:-$(scenario_min_signal "$s")}"
    echo "scenario_${s}_jitter_pct=${JITTER_PCT:-$(scenario_jitter_pct "$s")}"
    echo "scenario_${s}_loss_pct=${LOSS_PCT:-$(scenario_loss "$s")}"
  done
  echo "batches=$BATCHES rtts=$RTTS_STR algorithms=$ALGORITHMS_STR run_fig4b=$RUN_FIG4B"
  echo "cake_bw1=$CAKE_BW1 cake_bw2=$CAKE_BW2 fig4a_rwnd_mb=$FIG4A_RWND_MB dist_m=$DIST"
  echo "ref_campaign=$REF_CAMPAIGN"
} > "$CAMPAIGN_ROOT/campaign.txt"

for s in $SCENARIOS; do
  echo "[$(date '+%F %T')] ===== SCENARIO $s ====="
  RESULT_ROOT="$CAMPAIGN_ROOT/$s" TOPO_ARGS="$(scenario_args "$s")" \
    MIN_SIGNAL_DBM="${MIN_SIGNAL_DBM:-$(scenario_min_signal "$s")}" \
    JITTER_MS=0 JITTER_PCT="${JITTER_PCT:-$(scenario_jitter_pct "$s")}" \
    LOSS_PCT="${LOSS_PCT:-$(scenario_loss "$s")}" bash "$RUNNER"
done

python3 - "$CAMPAIGN_ROOT" "$REF_CAMPAIGN" $SCENARIOS <<'PY' | tee "$CAMPAIGN_ROOT/cross_scenario.txt"
import csv, pathlib, sys
root, ref, scen = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3:]

def summary(d):
    out = {}
    p = d / 'figure4a_summary.csv'
    if p.exists():
        for r in csv.DictReader(open(p)):
            out[(r['algorithm'], int(r['rtt_ms']))] = (float(r['mean_mbps']), float(r['batch_mean_ci95_mbps']))
    return out

order = [s for s in ('STATIC_DELAY', 'STATIC_JITTER_LOSS') if s in scen]
S = {s: summary(root / s) for s in order}
algos = sorted({a for s in order for (a, _) in S[s]})
rtts = sorted({t for s in order for (_, t) in S[s]})
cell = lambda v: '%9.2f +-%5.2f' % v if v else '%16s' % '-'
pct = lambda x, y: '%+8.1f' % (100 * (x[0] / y[0] - 1)) if x and y else '%8s' % '-'
print('Figure 4a v7 (static, cake 40/25, rwnd 1 MB), mean +- CI95 (Mbit/s)')
print('%-15s %-6s ' % ('scenario', 'algo') + ' '.join('%16s' % ('RTT %d' % t) for t in rtts))
for s in order:
    for a in algos:
        print('%-15s %-6s ' % (s, a) + ' '.join(cell(S[s].get((a, t))) for t in rtts))
print('\nTRR gain over RR / SAPS (%)')
for s in order:
    for b in ('RR', 'SAPS'):
        print('%-15s vs %-5s ' % (s, b) + ' '.join(pct(S[s].get(('TRR', t)), S[s].get((b, t))) for t in rtts))
print('\nDrop from lowest to highest RTT (%)')
for s in order:
    print('%-15s ' % s + '  '.join('%s %s' % (a, pct(S[s].get((a, rtts[-1])), S[s].get((a, rtts[0]))).strip()) for a in algos))
for x, y, name in (('STATIC_JITTER_LOSS', 'STATIC_DELAY', 'Jitter 8 % + loss 0.5 % effect'),):
    if x in order and y in order:
        print('\n%s (%s vs %s, %%)' % (name, x, y))
        for a in algos:
            print('%-6s ' % a + ' '.join(pct(S[x].get((a, t)), S[y].get((a, t))) for t in rtts))
if ref != 'none':
    refp = pathlib.Path(ref)
    for s in order:
        R = summary(refp / s)
        if not R:
            continue
        print('\nReference effect (%s: v7 vs %s, %%; only RTTs in both)' % (s, refp.name))
        for a in algos:
            print('%-6s ' % a + ' '.join(pct(S[s].get((a, t)), R.get((a, t))) for t in rtts))
PY
# Per-path stall events (a path carrying only pings while the other is busy) per batch.
python3 "$REPO/experiments/wifi/path_stalls.py" "$CAMPAIGN_ROOT" --csv "$CAMPAIGN_ROOT/path_stalls.csv" | tee "$CAMPAIGN_ROOT/path_stalls.txt"
# Wi-Fi link quality (signal, PHY rate, MAC retries) of h1-wlan0, r1-wlan1, r1-wlan2 and the AP sides.
python3 "$REPO/experiments/wifi/signal_report.py" "$CAMPAIGN_ROOT" --csv "$CAMPAIGN_ROOT/signal_report.csv" | tee "$CAMPAIGN_ROOT/signal_report.txt"
echo "[$(date '+%F %T')] CAMPAIGN DONE: $CAMPAIGN_ROOT"
