#!/usr/bin/env bash
# Figure-4b-style receive-window sweep on the static IoT v7 topology (example/topo_iot_v7.py,
# unchanged), runner experiments/wifi/run_fig4_iot_v8.sh (= v7 runner + RWNDS_STR/FIG4B_RTT):
#   h1 ~~ r1 (10 m) ~~ ap1 (10 m) -- r2 -- r4 -- r5 -- h2 ; r1 ~~ ap2 (10 m) -- r3 -- r4
#   802.11a, cake 40 (path 1) / 25 (path 2) Mbit/s, RTT path 1 = 10 ms, path 2 = FIG4B_RTT (100),
#   receive window swept over RWNDS_STR (default 0.5 0.75 1 1.5 2 5 10 MB), Figure 4a not run.
# Scenarios (SCENARIOS, default both, run in this order):
#   STATIC_DELAY        delay only (as the original Figure 4b).
#   STATIC_JITTER_LOSS  control: + jitter 8 % of each path's one-way delay (0.4 ms / 4 ms at
#                       RTT 100) + loss 0.5 % => loss-limited, expected flat in rwnd.
# Defaults: 3 batches x 10 repeats, TRR RR SAPS, readiness signal >= -65 dBm.
# Rough duration: 7 rwnd x 3 algorithms x 3 batches x ~3 min = ~3.2 h per scenario.
# The rwnd = 1 MB point equals the RTT 100 ms point of the v7 campaign (same network, binary
# and workload if client.py is unchanged): REF_CAMPAIGN (default results/figure4_iot_v7/
# campaign_20261005T111654Z when present) is used for that cross-check in cross_scenario.txt.
# run_iot_v7_campaign.sh and run_fig4_iot_v7.sh are kept unchanged.
#
# Usage (from the repo root, passwordless sudo):
#   bash experiments/wifi/run_iot_v8_rwnd_campaign.sh 2>&1 | tee ~/iot_v8_rwnd.log
# Env: SCENARIOS, BATCHES (3), RWNDS_STR, FIG4B_RTT (100), ALGORITHMS_STR ("TRR RR SAPS"),
#      CAKE_BW1/CAKE_BW2 (40/25), JITTER_PCT (8) / LOSS_PCT (0.5) overrides for the control,
#      DIST (10), MIN_SIGNAL_DBM (-65), MN_WIFI_SRC, REF_CAMPAIGN,
#      CAMPAIGN_ROOT (default results/figure4_iot_v8/campaign_<stamp>; reuse to resume).
set -Eeuo pipefail

REPO="${REPO:-$(pwd)}"
RUNNER="$REPO/experiments/wifi/run_fig4_iot_v8.sh"
SCENARIOS="${SCENARIOS:-STATIC_DELAY STATIC_JITTER_LOSS}"
CAMPAIGN_ROOT="${CAMPAIGN_ROOT:-$REPO/results/figure4_iot_v8/campaign_$(date -u +%Y%m%dT%H%M%SZ)}"
DEFAULT_REF="$REPO/results/figure4_iot_v7/campaign_20261005T111654Z"
REF_CAMPAIGN="${REF_CAMPAIGN:-$([[ -d "$DEFAULT_REF" ]] && echo "$DEFAULT_REF" || echo none)}"
DIST="${DIST:-10}"
export CAKE_BW1="${CAKE_BW1:-40}" CAKE_BW2="${CAKE_BW2:-25}" FIG4A_RWND_MB=1
export REPO
export BATCHES="${BATCHES:-3}"
export RWNDS_STR="${RWNDS_STR:-0.5 0.75 1 1.5 2 5 10}"
export FIG4B_RTT="${FIG4B_RTT:-100}"
export ALGORITHMS_STR="${ALGORITHMS_STR:-TRR RR SAPS}"
export RUN_FIG4A=0 RUN_FIG4B=1

scenario_args() {
  case "$1" in
    STATIC_DELAY|STATIC_JITTER_LOSS) echo "--dist1 $DIST --dist2 $DIST --h1-dist $DIST" ;;
    *) echo "unknown scenario $1" >&2; return 1 ;;
  esac
}
scenario_min_signal() { echo -65; }
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
  echo "batches=$BATCHES rwnds=$RWNDS_STR algorithms=$ALGORITHMS_STR rtt2=$FIG4B_RTT"
  echo "cake_bw1=$CAKE_BW1 cake_bw2=$CAKE_BW2 dist_m=$DIST"
  echo "workload_sha256=$(sha256sum "$REPO/example/client.py" | awk '{print $1}')"
  echo "workload_connections=$(grep -oP '^\s*num\s*=\s*\K\d+' "$REPO/example/client.py" | head -1)"
  echo "ref_campaign=$REF_CAMPAIGN"
} > "$CAMPAIGN_ROOT/campaign.txt"
echo "[$(date '+%F %T')] campaign root: $CAMPAIGN_ROOT"
grep -E '^(batches|workload_connections|ref_campaign)=' "$CAMPAIGN_ROOT/campaign.txt"

# A scenario that fails (runner gives up after MAX_ATTEMPTS) does not stop the next one;
# rerun with the same CAMPAIGN_ROOT to resume it (validated batches are skipped).
failed=""
for s in $SCENARIOS; do
  echo "[$(date '+%F %T')] ===== SCENARIO $s ====="
  RESULT_ROOT="$CAMPAIGN_ROOT/$s" TOPO_ARGS="$(scenario_args "$s")" \
    MIN_SIGNAL_DBM="${MIN_SIGNAL_DBM:-$(scenario_min_signal "$s")}" \
    JITTER_MS=0 JITTER_PCT="${JITTER_PCT:-$(scenario_jitter_pct "$s")}" \
    LOSS_PCT="${LOSS_PCT:-$(scenario_loss "$s")}" bash "$RUNNER" || {
      echo "[$(date '+%F %T')] SCENARIO $s FAILED; resume with CAMPAIGN_ROOT=$CAMPAIGN_ROOT" >&2
      failed+=" $s"
    }
done

python3 - "$CAMPAIGN_ROOT" "$REF_CAMPAIGN" "$FIG4B_RTT" $SCENARIOS <<'PY' | tee "$CAMPAIGN_ROOT/cross_scenario.txt"
import csv, pathlib, sys
root, ref, rtt2, scen = pathlib.Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4:]

def summary(p, xkey):
    out = {}
    if p.exists():
        for r in csv.DictReader(open(p)):
            out[(r['algorithm'], float(r[xkey]))] = (float(r['mean_mbps']), float(r['batch_mean_ci95_mbps']))
    return out

order = [s for s in ('STATIC_DELAY', 'STATIC_JITTER_LOSS') if s in scen]
S = {s: summary(root / s / 'figure4b_summary.csv', 'rwnd_mb') for s in order}
algos = [a for a in ('TRR', 'RR', 'SAPS') if any(a == k[0] for s in order for k in S[s])]
algos += sorted({k[0] for s in order for k in S[s]} - set(algos))
xs = sorted({k[1] for s in order for k in S[s]})
cell = lambda v: '%9.2f +-%5.2f' % v if v else '%16s' % '-'
pct = lambda x, y: '%+8.1f' % (100 * (x[0] / y[0] - 1)) if x and y else '%8s' % '-'
lab = lambda x: '%g MB' % x
print('Figure 4b v8 (static, cake 40/25, RTT path 2 = %d ms), mean +- CI95 (Mbit/s)' % rtt2)
print('%-19s %-6s ' % ('scenario', 'algo') + ' '.join('%16s' % lab(x) for x in xs))
for s in order:
    for a in algos:
        print('%-19s %-6s ' % (s, a) + ' '.join(cell(S[s].get((a, x))) for x in xs))
print('\nTRR gain over RR / SAPS (%)')
for s in order:
    for b in ('RR', 'SAPS'):
        print('%-19s vs %-5s ' % (s, b) + ' '.join(pct(S[s].get(('TRR', x)), S[s].get((b, x))) for x in xs))
if xs:
    print('\nChange from the smallest to the largest rwnd (%g -> %g MB, %%)' % (xs[0], xs[-1]))
    for s in order:
        print('%-19s ' % s + '  '.join('%s %s' % (a, pct(S[s].get((a, xs[-1])), S[s].get((a, xs[0]))).strip()) for a in algos))
if 'STATIC_JITTER_LOSS' in order and 'STATIC_DELAY' in order:
    print('\nJitter 8 % + loss 0.5 % effect (STATIC_JITTER_LOSS vs STATIC_DELAY, %)')
    for a in algos:
        print('%-6s ' % a + ' '.join(pct(S['STATIC_JITTER_LOSS'].get((a, x)), S['STATIC_DELAY'].get((a, x))) for x in xs))
if ref != 'none':
    refp = pathlib.Path(ref)
    print('\nCross-check rwnd 1 MB here vs RTT %d ms of the reference Figure 4a (%s)' % (rtt2, refp.name))
    for s in order:
        R = summary(refp / s / 'figure4a_summary.csv', 'rtt_ms')
        if not R:
            continue
        for a in algos:
            print('%-19s %-6s v8 %s   ref %s   diff %s' % (s, a, cell(S[s].get((a, 1.0))), cell(R.get((a, float(rtt2)))),
                                                       pct(S[s].get((a, 1.0)), R.get((a, float(rtt2)))).strip()))
PY
python3 "$REPO/experiments/wifi/path_stalls.py" "$CAMPAIGN_ROOT" --csv "$CAMPAIGN_ROOT/path_stalls.csv" | tee "$CAMPAIGN_ROOT/path_stalls.txt"
python3 "$REPO/experiments/wifi/signal_report.py" "$CAMPAIGN_ROOT" --csv "$CAMPAIGN_ROOT/signal_report.csv" | tee "$CAMPAIGN_ROOT/signal_report.txt"
python3 "$REPO/experiments/wifi/plot_campaign.py" "$CAMPAIGN_ROOT" --fig 4b --wifi 30.6,30.9 --iot 35.6 || true
if [[ -n "$failed" ]]; then
  echo "[$(date '+%F %T')] CAMPAIGN INCOMPLETE (failed:$failed): $CAMPAIGN_ROOT"
  exit 1
fi
echo "[$(date '+%F %T')] CAMPAIGN DONE: $CAMPAIGN_ROOT"
