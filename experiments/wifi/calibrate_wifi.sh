#!/usr/bin/env bash
# Calibrate the wmediumd channel model of example/topo_wifi.py.
#
# For each distance in DISTS, starts the topology with
#   --wmediumd --native-bw --dist1 D --dist2 D  (+ EXTRA_TOPO_ARGS)
# and measures, per path: signal, tx bitrate, downlink TCP throughput (iperf -R,
# i.e. the direction of the UniProxy download workload), ping RTT/jitter and
# MAC tx retries/failures. Output: $OUT/calibration.csv plus raw files.
#
# Usage (from the repo root):
#   sudo -v
#   DISTS="10 50 90 130 170" bash experiments/wifi/calibrate_wifi.sh
# Env:
#   DISTS            distances in metres (default "10 30 50 70 90 110 130 150 170")
#   IPERF_TIME       seconds per iperf run (default 10)
#   EXTRA_TOPO_ARGS  extra args for topo_wifi.py, e.g. "--exp 3.5 --fading 1"
#   OUT              output dir (default results/wifi_calibration/<stamp>)
set -Eeuo pipefail

REPO="${REPO:-$(pwd)}"
TOPO="$REPO/example/topo_wifi.py"
DISTS="${DISTS:-10 30 50 70 90 110 130 150 170}"
IPERF_TIME="${IPERF_TIME:-10}"
EXTRA_TOPO_ARGS="${EXTRA_TOPO_ARGS:-}"
OUT="${OUT:-$REPO/results/wifi_calibration/$(date -u +%Y%m%dT%H%M%SZ)}"

[[ -f "$TOPO" ]] || { echo "ERROR: $TOPO not found (run from repo root or set REPO)" >&2; exit 1; }
sudo -n true 2>/dev/null || { echo "ERROR: needs passwordless sudo; run 'sudo -v' first" >&2; exit 1; }
command -v iperf >/dev/null || { echo "ERROR: iperf not found" >&2; exit 1; }
mkdir -p "$OUT"
echo "dists=$DISTS iperf_time=$IPERF_TIME extra_topo_args=$EXTRA_TOPO_ARGS" > "$OUT/params.txt"

for d in $DISTS; do
  dd="$OUT/dist_${d}m"
  mkdir -p "$dd"
  echo "[$(date '+%T')] distance ${d} m"
  sudo -n mn -c >/dev/null 2>&1 || true

  cat > "$dd/commands" <<EOF
r5 bash -lc 'iperf -s > /dev/null 2>&1 &'
sh sleep 1
r1 bash -lc 'for w in r1-wlan0 r1-wlan1; do iw dev \$w link > "$dd/\${w}_link.txt" 2>&1; iw dev \$w station dump > "$dd/\${w}_station_pre.txt" 2>&1; done'
ap1 bash -lc 'w=\$(iw dev | awk "/Interface ap1-wlan/{print \\\$2}" | head -1); iw dev \$w station dump > "$dd/ap1_station_pre.txt" 2>&1'
ap2 bash -lc 'w=\$(iw dev | awk "/Interface ap2-wlan/{print \\\$2}" | head -1); iw dev \$w station dump > "$dd/ap2_station_pre.txt" 2>&1'
r1 bash -lc 'ping -c 20 -i 0.2 -I 10.0.1.1 10.0.6.2 > "$dd/path1_ping.txt" 2>&1'
r1 bash -lc 'ping -c 20 -i 0.2 -I 10.0.2.1 10.0.6.2 > "$dd/path2_ping.txt" 2>&1'
r1 bash -lc 'timeout $((IPERF_TIME + 15)) iperf -c 10.0.6.2 -B 10.0.1.1 -t $IPERF_TIME -R -f m > "$dd/path1_iperf.txt" 2>&1'
r1 bash -lc 'timeout $((IPERF_TIME + 15)) iperf -c 10.0.6.2 -B 10.0.2.1 -t $IPERF_TIME -R -f m > "$dd/path2_iperf.txt" 2>&1'
r1 bash -lc 'for w in r1-wlan0 r1-wlan1; do iw dev \$w station dump > "$dd/\${w}_station_post.txt" 2>&1; done'
ap1 bash -lc 'w=\$(iw dev | awk "/Interface ap1-wlan/{print \\\$2}" | head -1); iw dev \$w station dump > "$dd/ap1_station_post.txt" 2>&1'
ap2 bash -lc 'w=\$(iw dev | awk "/Interface ap2-wlan/{print \\\$2}" | head -1); iw dev \$w station dump > "$dd/ap2_station_post.txt" 2>&1'
r5 bash -lc 'pkill iperf || true'
EOF

  # EXTRA_TOPO_ARGS is intentionally unquoted so it splits into arguments.
  # shellcheck disable=SC2086
  printf 'source %s\nexit\n' "$dd/commands" | \
    sudo -n python3 "$TOPO" --wmediumd --native-bw --dist1 "$d" --dist2 "$d" $EXTRA_TOPO_ARGS \
    > "$dd/mininet.log" 2>&1 || echo "  WARN: mininet returned nonzero (see $dd/mininet.log)"
done
sudo -n mn -c >/dev/null 2>&1 || true

python3 - "$OUT" <<'PY'
import csv, pathlib, re, sys
out = pathlib.Path(sys.argv[1])

def read(p):
    try:
        return p.read_text(errors="replace")
    except FileNotFoundError:
        return ""

def grab(pattern, text, cast=str):
    m = re.search(pattern, text)
    return cast(m.group(1)) if m else ""

def station_counter(text, key):
    return grab(r"%s:\s+(\d+)" % re.escape(key), text, int)

rows = []
for dd in sorted(out.glob("dist_*m"), key=lambda p: float(p.name[5:-1])):
    dist = float(dd.name[5:-1])
    for path, wlan, ap in (("path1", "r1-wlan0", "ap1"), ("path2", "r1-wlan1", "ap2")):
        link = read(dd / f"{wlan}_link.txt")
        pre = read(dd / f"{wlan}_station_pre.txt")
        post = read(dd / f"{wlan}_station_post.txt")
        ap_pre = read(dd / f"{ap}_station_pre.txt")
        ap_post = read(dd / f"{ap}_station_post.txt")
        ping = read(dd / f"{path}_ping.txt")
        iperf = read(dd / f"{path}_iperf.txt")
        mbps = re.findall(r"([\d.]+)\s+Mbits/sec", iperf)
        rtt = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", ping)
        def delta(key, a_txt=pre, b_txt=post):
            a, b = station_counter(a_txt, key), station_counter(b_txt, key)
            return b - a if a != "" and b != "" else ""
        rows.append({
            "dist_m": dist,
            "path": path,
            "connected": "Connected to" in link,
            "signal_dbm": grab(r"signal:\s+(-?\d+)", link),
            "tx_bitrate": grab(r"tx bitrate:\s+([^\n]+)", post or link).strip(),
            "iperf_down_mbps": mbps[-1] if mbps else "",
            "ping_loss": grab(r"(\d+(?:\.\d+)?)% packet loss", ping),
            "rtt_avg_ms": rtt.group(2) if rtt else "",
            "rtt_mdev_ms": rtt.group(4) if rtt else "",
            "tx_packets": delta("tx packets"),
            "tx_retries": delta("tx retries"),
            "tx_failed": delta("tx failed"),
            # Downlink (AP -> r1) is the direction of the workload data.
            "ap_tx_bitrate": grab(r"tx bitrate:\s+([^\n]+)", ap_post).strip(),
            "ap_tx_packets": delta("tx packets", ap_pre, ap_post),
            "ap_tx_retries": delta("tx retries", ap_pre, ap_post),
            "ap_tx_failed": delta("tx failed", ap_pre, ap_post),
        })

csv_path = out / "calibration.csv"
with open(csv_path, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["dist_m"])
    w.writeheader()
    w.writerows(rows)

print(f"\nCalibration table: {csv_path}\n")
cols = ["dist_m", "path", "signal_dbm", "ap_tx_bitrate", "iperf_down_mbps",
        "rtt_avg_ms", "rtt_mdev_ms", "ap_tx_packets", "ap_tx_retries", "ap_tx_failed"]
print("  ".join(f"{c:>14}" for c in cols))
for r in rows:
    print("  ".join(f"{str(r[c])[:14]:>14}" for c in cols))
PY
