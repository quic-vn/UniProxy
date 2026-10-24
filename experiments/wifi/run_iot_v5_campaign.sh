#!/usr/bin/env bash
# Figure 4a campaign on the IoT v5 topology (example/topo_iot_v5.py, runner
# experiments/wifi/run_fig4_iot_v5.sh): the qdisc placement of the original example/topo.py
# (netem on r2-eth0/r2-eth1 and r3-eth0/r3-eth1 with netem's default limit; cake on the AP wired
# ports for the uplink and root on r4-eth0/r4-eth1 for the downlink; no qdisc on Wi-Fi) with the
# v3 wireless setup, trajectory and scale (802.11a, cake 16/8 Mbit/s, rwnd 0.16 MB).
# run_iot_v3_campaign.sh / run_iot_v4_campaign.sh are kept unchanged.
#
# Scenarios (SCENARIOS, default "WP_DELAY WP_JITTER"), i.e. the two netem cases:
#   WP_DELAY        delay only: path 1 5 ms per direction, path 2 RTT/2 per direction.
#   WP_JITTER       same + jitter 0.25 ms (uniform, no correlation) on all four netem qdiscs.
#   WP_JITTER_LOSS  optional: WP_JITTER + random loss 0.1 % (experiments/network_variation).
# All: r1 walks the 7 waypoints x=140..260 (5 s per segment, 30 s per direction, reverse),
# h1 static at (140,105). Defaults follow the original Figure 4a: RTT 20 40 60 80 100, 5 batches.
# REF_CAMPAIGN (default results/figure4_iot_v3/campaign_20260930T050836Z when present, "none" to
# skip) adds a "Placement effect (v5 vs v3)" table for the RTT points both campaigns have.
#
# Usage (from the repo root, passwordless sudo):
#   bash experiments/wifi/run_iot_v5_campaign.sh 2>&1 | tee ~/iot_v5_campaign.log
# Env: SCENARIOS, BATCHES (5), RTTS_STR ("20 40 60 80 100"), ALGORITHMS_STR ("TRR RR SAPS"),
#      CAKE_BW1/CAKE_BW2/FIG4A_RWND_MB (runner defaults 16/8/0.16), JITTER_MS / LOSS_PCT,
#      REF_CAMPAIGN, CAMPAIGN_ROOT (default results/figure4_iot_v5/campaign_<stamp>; reuse to resume).
set -Eeuo pipefail

REPO="${REPO:-$(pwd)}"
RUNNER="$REPO/experiments/wifi/run_fig4_iot_v5.sh"
SCENARIOS="${SCENARIOS:-WP_DELAY WP_JITTER}"
CAMPAIGN_ROOT="${CAMPAIGN_ROOT:-$REPO/results/figure4_iot_v5/campaign_$(date -u +%Y%m%dT%H%M%SZ)}"
DEFAULT_REF="$REPO/results/figure4_iot_v3/campaign_20260930T050836Z"
REF_CAMPAIGN="${REF_CAMPAIGN:-$([[ -d "$DEFAULT_REF" ]] && echo "$DEFAULT_REF" || echo none)}"
export REPO
export BATCHES="${BATCHES:-5}"
export RTTS_STR="${RTTS_STR:-20 40 60 80 100}"
export ALGORITHMS_STR="${ALGORITHMS_STR:-TRR RR SAPS}"
export RUN_FIG4A=1 RUN_FIG4B="${RUN_FIG4B:-0}"

scenario_args() {
  case "$1" in
    WP_DELAY|WP_JITTER|WP_JITTER_LOSS) echo "--r1-move=-60,0:-40,0:-20,0:0,0:20,0:40,0:60,0 --mob-period 30" ;;
    *) echo "unknown scenario $1" >&2; return 1 ;;
  esac
}

# Readiness signal threshold (runner MIN_SIGNAL_DBM): path 2 starts at 170 m (-90 dBm).
scenario_min_signal() { echo -92; }

# Wired netem jitter (ms) and loss (%) per scenario unless JITTER_MS / LOSS_PCT are set.
scenario_jitter() {
  case "$1" in WP_JITTER|WP_JITTER_LOSS) echo 0.25 ;; *) echo 0 ;; esac
}
scenario_loss() {
  case "$1" in WP_JITTER_LOSS) echo 0.1 ;; *) echo 0 ;; esac
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
    echo "scenario_${s}_jitter_ms=${JITTER_MS:-$(scenario_jitter "$s")}"
    echo "scenario_${s}_loss_pct=${LOSS_PCT:-$(scenario_loss "$s")}"
  done
  echo "batches=$BATCHES rtts=$RTTS_STR algorithms=$ALGORITHMS_STR run_fig4b=$RUN_FIG4B"
  echo "cake_bw1=${CAKE_BW1:-default} cake_bw2=${CAKE_BW2:-default} fig4a_rwnd_mb=${FIG4A_RWND_MB:-default}"
  echo "ref_campaign=$REF_CAMPAIGN"
} > "$CAMPAIGN_ROOT/campaign.txt"

for s in $SCENARIOS; do
  echo "[$(date '+%F %T')] ===== SCENARIO $s ====="
  RESULT_ROOT="$CAMPAIGN_ROOT/$s" TOPO_ARGS="$(scenario_args "$s")" \
    MIN_SIGNAL_DBM="${MIN_SIGNAL_DBM:-$(scenario_min_signal "$s")}" \
    JITTER_MS="${JITTER_MS:-$(scenario_jitter "$s")}" \
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

order = [s for s in ('WP_DELAY', 'WP_JITTER', 'WP_JITTER_LOSS') if s in scen]
S = {s: summary(root / s) for s in order}
algos = sorted({a for s in order for (a, _) in S[s]})
rtts = sorted({t for s in order for (_, t) in S[s]})
cell = lambda v: '%9.2f +-%5.2f' % v if v else '%16s' % '-'
pct = lambda x, y: '%+8.1f' % (100 * (x[0] / y[0] - 1)) if x and y else '%8s' % '-'
print('Figure 4a v5, mean +- CI95 (Mbit/s)')
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
for x, y, name in (('WP_JITTER', 'WP_DELAY', 'Jitter effect'), ('WP_JITTER_LOSS', 'WP_JITTER', 'Loss effect')):
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
        print('\nPlacement effect (%s: v5 vs v3 %s, %%; only RTTs in both)' % (s, refp.name))
        for a in algos:
            print('%-6s ' % a + ' '.join(pct(S[s].get((a, t)), R.get((a, t))) for t in rtts))
PY
# Per-path stall events (a path carrying only pings while the other is busy) per batch.
python3 "$REPO/experiments/wifi/path_stalls.py" "$CAMPAIGN_ROOT" --csv "$CAMPAIGN_ROOT/path_stalls.csv" | tee "$CAMPAIGN_ROOT/path_stalls.txt"
echo "[$(date '+%F %T')] CAMPAIGN DONE: $CAMPAIGN_ROOT"
