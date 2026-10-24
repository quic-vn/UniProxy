#!/usr/bin/env bash
# TPROXY + client-side proxy on r1 for the IoT topologies. Single source of truth:
# experiments/wifi/run_fig4_iot_v2.sh runs this script inside r1 too.
#
#   bash client_iot.sh          rules + run the proxy client (foreground, exec)
#   bash client_iot.sh setup    rules only
# Env: IOT_IF   ingress interface of h1's traffic (default r1-wlan0 = topo_iot_v2.py;
#               topo_iot.py uses r1-wlan2), SERVER_ENDPOINT (default 10.0.6.2:7500),
#      LISTEN (default tcp:127.0.0.1:6500)
set -eu
IOT_IF="${IOT_IF:-r1-wlan0}"
SERVER_ENDPOINT="${SERVER_ENDPOINT:-10.0.6.2:7500}"
LISTEN="${LISTEN:-tcp:127.0.0.1:6500}"
PORT="${LISTEN##*:}"

# Hosts running Tailscale/WireGuard set net.ipv4.conf.all.src_valid_mark=1 and new
# namespaces inherit it; rp_filter then validates the source with the TPROXY mark via
# table 100 ("local") and drops the SYN. Force 0 in this namespace only.
sysctl -qw net.ipv4.conf.all.src_valid_mark=0

# Idempotent: re-running does not duplicate rules.
ip rule show | grep -q "fwmark 0x1 lookup 100" || ip rule add fwmark 1 table 100
ip route show table 100 2>/dev/null | grep -q "^local default dev lo" || ip route add local 0.0.0.0/0 dev lo table 100
RULE=(PREROUTING --in-interface "$IOT_IF" -p tcp -d 10.0.7.0/24 -j TPROXY
      --on-port "$PORT" --on-ip 127.0.0.1 --tproxy-mark 1/1)
iptables -t mangle -C "${RULE[@]}" 2>/dev/null || iptables -t mangle -A "${RULE[@]}"

if [[ "${1:-}" == "setup" ]]; then exit 0; fi
exec ./quic-tun/client/cmd/cmd --listen-on "$LISTEN" --server-endpoint "$SERVER_ENDPOINT"
