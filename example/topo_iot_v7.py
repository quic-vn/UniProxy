#!/usr/bin/env python3
"""IoT-gateway topology v7 on Mininet-WiFi (one Wi-Fi hop per path).

v7 = topo_iot_v6.py (kept unchanged) + --jitter-pct: netem delay variation given as a
percentage of the ONE-WAY DELAY of each path's netem qdiscs, so the two paths get
different jitter (J1 = pct/100 x delay1 on r2-eth0/r2-eth1, J2 = pct/100 x delay2 on
r3-eth0/r3-eth1; e.g. 8 % with delay1 5 ms, delay2 50 ms -> 0.4 ms and 4 ms). Exclusive
with --jitter (fixed ms on all four qdiscs, v3 semantics). The values applied are written
to <log-dir>/netem_params.txt. Intended static configuration (figure of 2026-10-05):
h1-r1 = r1-ap1 = r1-ap2 = 10 m, cake 40/25 Mbit/s (runner --bw1/--bw2), rwnd 1 MB
(runner), jitter 8 %, loss 0.5 %, no mobility. Note: one 802.11a hop under wmediumd
carries ~36 Mbit/s TCP, so with cake 40/25 the Wi-Fi hops (h1-r1 in particular) are the
bottleneck, not cake; this is a property of that configuration, not of the code.

v6 = topo_iot_v5.py (kept unchanged) with housekeeping only; the network, radios, mediums,
propagation, qdisc placement, rates, mobility and workload are v5's, so results are
comparable one-to-one. Changes: (1) the --iot-ap-method manual fallback (hostapd started by
the script, inherited from topo_iot.py v1) is removed; the IoT AP is always the official
setMasterMode and the run aborts if the radio is not in AP mode after build();
(2) <log-dir>/mob_loop_started.txt records when net.is_mob_started() (Mininet-WiFi master
8b92a60b, 2024-11) first reported the Tracked loop running, next to mob_started.txt (the
trigger instant); (3) the wmediumd config mn_wifi generated (medium_array, positions,
propagation) and wmediumd's log are copied to <log-dir>/wmediumd_config.cfg and
wmediumd.log as evidence of the mediums actually used; (4) version strings "2.6"/"2.7"
are no longer used to describe Mininet-WiFi (see the end of this docstring).

v5 = topo_iot_v4.py (kept unchanged) with the qdisc placement of the original wired
example/topo.py: netem on r2-eth0/r2-eth1 and r3-eth0/r3-eth1 (one per direction, netem
default limit), downlink cake root on r4-eth0/r4-eth1, and the uplink cake, which topo.py
puts on r1-eth1/r1-eth2 (now Wi-Fi), on the AP's wired port towards r2/r3. No qdisc on
any Wi-Fi interface. The AP port names are written to <log-dir>/ap_ports.txt. Rates stay
scaled (default 16/8 Mbit/s) because 802.11a under wmediumd carries ~36 Mbit/s per hop and
802.11n/ac do not work with wmediumd here (see CLAUDE.md, HT/VHT sweep 2026-10-01).


v4 = topo_iot_v3.py (kept unchanged) + one addition: --loss P adds random loss P %
(no correlation) to the same four wired netem qdiscs that carry --delay/--jitter,
giving "netem loss P% delay D J limit 100000" as experiments/network_variation does
on topo.py ("netem loss 0.10% delay D 0.25ms"). Everything else, wireless
configuration, cake 16/8 placement and mobility included, is v3's.

v3 = topo_iot_v2.py (kept unchanged as the control) + two additions, the wireless
configuration (radios, mediums, propagation, AP/association API, cake placement)
is identical: (1) r1 may walk a list of waypoints; (2) --jitter adds netem delay
variation on the wired qdiscs (as experiments/network_variation does on topo.py).

    h1 ~~ r1 (IoT gateway, client-side proxy) ~~ ap1 ── r2 ──┐
                                               ~~ ap2 ── r3 ──┴── r4 ── r5 ── h2
    ~~ Wi-Fi (mac80211_hwsim + wmediumd)      ── Ethernet

Everything wireless is done with the public Mininet-WiFi API, in the order of
the official examples (mn_wifi/examples/sta_ap_mode.py, mobility.py):

- Nodes/positions/propagation: addStation/addAccessPoint(position=...),
  setPropagationModel(logDistance), setInitialMediums (one wmediumd medium per
  hop), configureNodes.
- IoT access point: Node.setMasterMode on r1-wlan0 (before build, as in
  sta_ap_mode.py). The IoT radio must be wlan index 0: Intf.setMasterMode calls
  node.getNameToWintf(self) with an object and getNameToWintf returns wintfs[0]
  for anything that is not a string (mn_wifi/node.py:99-101, link.py:367-383).
  After build() the AP is verified with `iw dev`; there is no manual hostapd
  fallback (removed in v6).
- Association: net.addLink(r1, ap1, port1=1, port2=0) / addLink(r1, ap2,
  port1=2, port2=0) -> infra_wmediumd_link -> Intf.associate (net.py:927-932,
  link.py:567-580). h1 associates to r1's AP with h1.wintfs[0].associate(
  r1.wintfs[0]) (the same Intf method; addLink between two stations would
  create an Ethernet link, net.py:934-941). Association requires
  distance <= AP range (logDistance default ~176-189 m for 802.11a). The
  `iw connect` issued by associate() is asynchronous and the kernel does not
  retry; observed on 2.7: the first attempt fails even with the AP enabled and
  beaconing (empty BSS cache), the second succeeds. The topology therefore
  waits for the APs (type AP + 1 s), polls the link state and re-issues
  Intf.iw_connect (the method associate() uses) up to 3 times; the outcome per
  radio is written to <log-dir>/assoc_method.txt (api = made by Intf.associate,
  manual = this script had to re-issue `iw connect`, failed). Auto
  association/handover stays off (allAutoAssociation=False): auto_association
  would turn r1 into an AP node (net.py:1746-1755). A supplicant-like watchdog
  per managed radio re-associates after a loss (<log-dir>/assoc_<intf>.log).
- Mobility: tracked mobility (startMobility/mobility/stopMobility with
  node.coord waypoints, reverse=True, mob_rep) executed by mn_wifi's Tracked
  thread. The only deviation from the examples is *when* it starts: instead
  of build() starting the thread, net.check_if_mob() (the call build() makes)
  is issued when <log-dir>/mob_go appears (--mob-trigger go; the runner
  creates it right before the workload) or right after setup (now), so every
  run sees the same trajectory phase. mob_started.txt records that instant;
  mob_loop_started.txt records when net.is_mob_started() first returned True
  (the Tracked thread is in its loop; absent when the API does not exist).
  r1 walks a list of 2+ waypoints (--r1-move "dx,dy:dx,dy[:...]") in
  --mob-period seconds per direction (segment time proportional to segment
  length, e.g. 7 equally spaced waypoints in 30 s = 5 s per segment), then
  reverses. h1 either stays at r1's start point + (0, h1-dist) (default: the
  IoT hop then lengthens as r1 walks away), has its own two-waypoint
  trajectory (--h1-move, --h1-period, --h1-delay) or rides with r1
  (--h1-follow, constant IoT hop). Note: mn_wifi's Tracked.run is a busy loop
  (one core at 100 % while moving).
- Wired impairment: --delay1/--delay2 (one-way, per direction) and --jitter
  (netem delay variation, uniform, no correlation, applied to all four wired
  netem qdiscs r2-eth1/r4-eth0/r3-eth1/r4-eth1, as experiments/network_variation
  does on the wired topology). No netem on the AP-side ports r2-eth0/r3-eth0.
- 1 Hz logs in <log-dir>: positions.csv (node.position as set by mn_wifi),
  radio_<intf>.csv (station dump), ifstats_<intf>.csv (per-path counters),
  vmstat.txt; mob_error.txt if the nodes did not move 10 s after the trigger.
- Strict argument parsing (unknown options are an error).

Radios: r1-wlan0 = IoT AP (SSID iot) serving h1-wlan0; r1-wlan1 ~ ap1
(SSID path1); r1-wlan2 ~ ap2 (SSID path2).
Addresses: h1 10.0.0.1 / r1-wlan0 10.0.0.2, r1-wlan1 10.0.1.1 / r2-eth0
10.0.1.2, r1-wlan2 10.0.2.1 / r3-eth0 10.0.2.2, rest as topo.py.
TPROXY must intercept on r1-wlan0 (client_iot.sh, IOT_IF).

Mininet-WiFi has no releases: "2.6"/"2.7" are only the VERSION string in mn_wifi/net.py.
Describe the installed Mininet-WiFi by the commit of its source (the runner records
env_mn_wifi_commit and env_mn_wifi_net_sha256) and wmediumd by its binary sha256.
Verified with intrig-unicamp/mininet-wifi master d0d3c94 (2026-09-10) and wmediumd
(ramonfontes, branch mininet-wifi) ebfdc7c; the APIs used also exist in the master
snapshots of 2022-2025 that report VERSION "2.6".
"""

import argparse
import glob
import math
import os
import shutil
import subprocess
import sys
import threading
import time

from mininet.link import TCLink
from mininet.log import setLogLevel, info, warn, error
from mn_wifi.cli import CLI
from mn_wifi.link import wmediumd
from mn_wifi.net import Mininet_wifi
from mn_wifi.wmediumdConnector import interference

R1_POS = (200.0, 100.0, 0.0)
UNLIMITED_REPS = 10000

IOT_IF, P1_IF, P2_IF = 'r1-wlan0', 'r1-wlan1', 'r1-wlan2'

RECONNECT_SH = r'''#!/bin/sh
# usage: reconnect.sh <intf> <ssid> <bssid> <log>
i=$1; s=$2; b=$3; log=$4
while :; do
  if ! iw dev "$i" link 2>/dev/null | grep -q "SSID: $s"; then
    echo "$(date +%s.%N | cut -c1-14) $i lost/not associated to $s -> reconnect" >> "$log"
    iw dev "$i" disconnect >/dev/null 2>&1
    iw dev "$i" connect "$s" "$b" >/dev/null 2>&1
  fi
  sleep 1
done
'''

# One CSV row per peer per second:
# epoch,intf,peer,signal_dbm,tx_bitrate,rx_bitrate,tx_packets,tx_retries,tx_failed
RADIO_LOG_SH = r'''#!/bin/sh
# usage: radio_log.sh <intf> <csv>
i=$1; out=$2
echo "epoch,intf,peer,signal_dbm,tx_bitrate_mbps,rx_bitrate_mbps,tx_packets,tx_retries,tx_failed" > "$out"
while :; do
  t=$(date +%s.%N | cut -c1-14)
  iw dev "$i" station dump 2>/dev/null | awk -v t="$t" -v i="$i" '
    function emit() { printf "%s,%s,%s,%s,%s,%s,%s,%s,%s\n", t, i, sta, sig, txb, rxb, txp, txr, txf }
    $1 == "Station" { if (sta != "") emit(); sta = $2; sig = txb = rxb = txp = txr = txf = "" }
    $1 == "signal:" { sig = $2 }
    $1 == "tx" && $2 == "bitrate:" { txb = $3 }
    $1 == "rx" && $2 == "bitrate:" { rxb = $3 }
    $1 == "tx" && $2 == "packets:" { txp = $3 }
    $1 == "tx" && $2 == "retries:" { txr = $3 }
    $1 == "tx" && $2 == "failed:" { txf = $3 }
    END { if (sta != "") emit(); else printf "%s,%s,none,,,,,,\n", t, i }' >> "$out"
  sleep 1
done
'''

# Interface counters at 1 Hz (cumulative; diff consecutive rows for rates).
IFSTATS_SH = r'''#!/bin/sh
# usage: ifstats.sh <intf> <csv>
i=$1; out=$2; s=/sys/class/net/$i/statistics
echo "epoch,intf,rx_bytes,rx_packets,rx_dropped,tx_bytes,tx_packets,tx_dropped" > "$out"
while :; do
  echo "$(date +%s.%N | cut -c1-14),$i,$(cat $s/rx_bytes),$(cat $s/rx_packets),$(cat $s/rx_dropped),$(cat $s/tx_bytes),$(cat $s/tx_packets),$(cat $s/tx_dropped)" >> "$out"
  sleep 1
done
'''


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, allow_abbrev=False,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--mode', default='a',
                   help="802.11 mode of ap1/ap2; default a "
                        "(HT/VHT modes do not work under wmediumd: legacy rate table only)")
    p.add_argument('--ch1', default='36', help='channel of ap1 / path 1; default 36')
    p.add_argument('--ch2', default='149', help='channel of ap2 / path 2; default 149')
    p.add_argument('--mode-iot', default='a',
                   help="802.11 mode of r1's IoT AP; default a; "
                        "use g with --ch-iot 1/6/11 for a 2.4 GHz IoT link")
    p.add_argument('--ch-iot', default='44', help="channel of r1's IoT AP; default 44")
    p.add_argument('--dist1', type=float, default=70,
                   help='initial r1-ap1 distance (m); default 70')
    p.add_argument('--dist2', type=float, default=110,
                   help='initial r1-ap2 distance (m); default 110')
    p.add_argument('--h1-dist', type=float, default=5,
                   help="h1-r1 distance (m) at r1's initial position; default 5")
    p.add_argument('--exp', type=float, default=3.0,
                   help='logDistance path-loss exponent; default 3')
    p.add_argument('--fading', type=int, default=0,
                   help='wmediumd fading coefficient (integer, all links); default 0')
    p.add_argument('--noise-th', type=int, default=-91,
                   help='wmediumd noise threshold (dBm); default -91')
    p.add_argument('--txpower', type=float, default=None,
                   help='tx power (dBm) of every radio; default mn_wifi value')
    p.add_argument('--native-bw', action='store_true', help='do not add cake shapers')
    p.add_argument('--bw1', type=float, default=16, help='cake rate (Mbit) path 1; default 16')
    p.add_argument('--bw2', type=float, default=8, help='cake rate (Mbit) path 2; default 8')
    p.add_argument('--delay1', type=float, default=5,
                   help='one-way delay (ms) of path 1, on r2-eth1 and r4-eth0; default 5')
    p.add_argument('--delay2', type=float, default=5,
                   help='one-way delay (ms) of path 2, on r3-eth1 and r4-eth1; '
                        'default 5 (runner sets RTT/2)')
    p.add_argument('--jitter', type=float, default=0,
                   help='netem delay variation (ms, uniform +-jitter, no correlation) added to '
                        'every wired netem qdisc of both paths (r2-eth1, r4-eth0, r3-eth1, '
                        'r4-eth1), as in experiments/network_variation; default 0 = none')
    p.add_argument('--jitter-pct', type=float, default=0,
                   help='netem delay variation as %% of the one-way delay of each path '
                        '(path 1: %% of --delay1 on r2-eth0/r2-eth1; path 2: %% of --delay2 on '
                        'r3-eth0/r3-eth1), uniform, no correlation; exclusive with --jitter; '
                        'default 0 = none')
    p.add_argument('--loss', type=float, default=0,
                   help='netem random loss (%%, no correlation) added to the same four wired '
                        'netem qdiscs, e.g. 0.1 as in experiments/network_variation; default 0 = none')
    m = p.add_argument_group('mobility (Mininet-WiFi tracked mobility, start synchronised)')
    m.add_argument('--r1-move', default=None, metavar='DX1,DY1:DX2,DY2[:...]',
                   help='r1 goes back and forth along these waypoints (offsets in m from '
                        'R1_POS, 2 or more), e.g. "-40,0:40,0" or '
                        '"-60,0:-40,0:-20,0:0,0:20,0:40,0:60,0"')
    m.add_argument('--h1-move', default=None, metavar='DX1,DY1:DX2,DY2',
                   help="h1 goes back and forth between these two offsets (m) from its "
                        "initial position (r1's start point + (0, h1-dist)), e.g. \"0,0:0,30\"")
    m.add_argument('--h1-follow', action='store_true',
                   help="h1 is carried with r1 (h1 = r1 + (0, h1-dist))")
    m.add_argument('--mob-period', type=float, default=20,
                   help='seconds for one traversal of the r1 waypoint list (one direction); '
                        'default 20. Segments take time proportional to their length')
    m.add_argument('--h1-period', type=float, default=None,
                   help='seconds per leg of h1; default = --mob-period. With --r1-move, '
                        'h1 makes floor((mob-period - h1-delay) / h1-period) legs per r1 leg')
    m.add_argument('--h1-delay', type=float, default=0,
                   help='seconds h1 waits at the start of every repetition; default 0')
    m.add_argument('--mob-reps', type=int, default=0,
                   help='number of r1 legs (h1 legs when r1 is static), then stop; '
                        'default 0 = unlimited')
    m.add_argument('--mob-trigger', choices=('now', 'go'), default='now',
                   help='now: start right after setup; go: start when <log-dir>/mob_go '
                        'appears; default now')
    p.add_argument('--assoc-timeout', type=float, default=20,
                   help='seconds to wait for all radios to associate at start')
    p.add_argument('--log-dir', default='/tmp/topo_iot_v7',
                   help='directory for logs; default /tmp/topo_iot_v7')
    # Move specs may start with '-' (e.g. "-40,0:40,0"), which argparse would
    # take for an option: glue them to their flag as "--r1-move=<spec>".
    argv, fixed, i = sys.argv[1:], [], 0
    while i < len(argv):
        if argv[i] in ('--r1-move', '--h1-move') and i + 1 < len(argv):
            fixed.append('{}={}'.format(argv[i], argv[i + 1]))
            i += 2
        else:
            fixed.append(argv[i])
            i += 1
    args = p.parse_args(fixed)
    if args.h1_period is None:
        args.h1_period = args.mob_period
    if args.mob_period <= 0 or args.h1_period <= 0:
        p.error('--mob-period/--h1-period must be > 0')
    if args.h1_delay < 0:
        p.error('--h1-delay must be >= 0')
    if args.jitter < 0:
        p.error('--jitter must be >= 0')
    if args.jitter_pct < 0:
        p.error('--jitter-pct must be >= 0')
    if args.jitter > 0 and args.jitter_pct > 0:
        p.error('--jitter and --jitter-pct are exclusive')
    if not 0 <= args.loss < 100:
        p.error('--loss must be in [0, 100)')
    if args.h1_follow and args.h1_move:
        p.error('--h1-follow and --h1-move are exclusive: h1 either rides with r1 '
                'or has its own trajectory')
    if args.r1_move and args.h1_move and args.h1_delay + args.h1_period > args.mob_period + 1e-9:
        p.error('h1 leg (--h1-delay + --h1-period) must fit into one r1 leg (--mob-period); '
                'Mininet-WiFi tracked mobility repeats all nodes with the same period')
    return args


def pos_str(pos):
    return ','.join('{:g}'.format(c) for c in pos)


def add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def parse_offsets(spec):
    """'dx1,dy1:dx2,dy2[:dx3,dy3...]' -> list of 3-D offsets (at least two)."""
    pts = []
    for part in spec.split(':'):
        try:
            d = [float(v) for v in part.split(',')]
        except ValueError:
            raise SystemExit('bad move spec {!r}'.format(spec))
        if not 1 <= len(d) <= 3:
            raise SystemExit('bad move spec {!r}'.format(spec))
        pts.append(tuple(d + [0.0] * (3 - len(d))))
    if len(pts) < 2:
        raise SystemExit('move spec needs at least two waypoints "dx1,dy1:dx2,dy2[:...]", got {!r}'.format(spec))
    return pts


def plan_mobility(args):
    """Waypoints and timing for Mininet-WiFi tracked mobility.

    Returns (start positions, plan) where plan is None when nothing moves, else
    {'rep_len': s, 'nodes': {name: {'coord': [pos, ...], 'start': s, 'stop': s}}}.
    One repetition = one r1 leg (A->B); reverse=True makes the next repetition
    B->A, so every node has period 2 x its leg. h1 (independent) does n legs of
    --h1-period inside one repetition after --h1-delay; A'/B' are offsets from
    r1's start point + (0, h1-dist)."""
    r1_off = parse_offsets(args.r1_move) if args.r1_move else None
    h1_off = parse_offsets(args.h1_move) if args.h1_move else None
    r1_wp = [add(R1_POS, o) for o in r1_off] if r1_off else [R1_POS]
    r1_a = r1_wp[0]
    up = (0.0, args.h1_dist, 0.0)
    nodes = {}
    if r1_off:
        nodes['r1'] = {'coord': r1_wp, 'start': 0.0, 'stop': args.mob_period}
        if args.h1_follow:
            nodes['h1'] = {'coord': [add(w, up) for w in r1_wp], 'start': 0.0,
                           'stop': args.mob_period}
    h1_a = add(r1_a, up)
    if h1_off:
        if len(h1_off) != 2:
            raise SystemExit('--h1-move takes exactly two waypoints')
        a, b = add(h1_a, h1_off[0]), add(h1_a, h1_off[1])
        rep = args.mob_period if r1_off else args.h1_delay + args.h1_period
        n = max(1, int(math.floor((rep - args.h1_delay) / args.h1_period + 1e-9)))
        coord = [a if k % 2 == 0 else b for k in range(n + 1)]
        nodes['h1'] = {'coord': coord, 'start': args.h1_delay,
                       'stop': args.h1_delay + n * args.h1_period}
        h1_a = a
    start = {'r1': r1_a, 'h1': nodes['h1']['coord'][0] if 'h1' in nodes else h1_a}
    if not nodes:
        return start, None
    return start, {'rep_len': max(v['stop'] for v in nodes.values()), 'nodes': nodes}


def start_tracked_mobility(net, node_objs, plan, reps):
    """Official tracked-mobility API (examples/mobility.py), then the step that
    build() performs (check_if_mob) so the Tracked thread starts now."""
    net.startMobility(time=0, mob_rep=reps or UNLIMITED_REPS, reverse=True)
    for name, spec in plan['nodes'].items():
        node = node_objs[name]
        node.coord = [pos_str(c) for c in spec['coord']]
        net.mobility(node, 'start', time=spec['start'], position=pos_str(spec['coord'][0]))
        net.mobility(node, 'stop', time=spec['stop'], position=pos_str(spec['coord'][-1]))
    net.stopMobility(time=plan['rep_len'])
    net.check_if_mob()


class MobilityWatch(threading.Thread):
    """Waits for the trigger, starts tracked mobility, logs node.position at 1 Hz,
    records mob_loop_started.txt when net.is_mob_started() first returns True and
    flags mob_error.txt when the nodes have not moved 10 s after the start."""

    def __init__(self, args, net, node_objs, plan, start_pos):
        super().__init__(daemon=True)
        self.args, self.net, self.nodes, self.plan, self.start_pos = args, net, node_objs, plan, start_pos
        self.stop_ev = threading.Event()
        self.t0 = None

    def positions(self):
        return {n: tuple(float(c) for c in self.nodes[n].position) for n in ('r1', 'h1')}

    def run(self):
        a = self.args
        go = os.path.join(a.log_dir, 'mob_go')
        log = open(os.path.join(a.log_dir, 'positions.csv'), 'w')
        log.write('epoch,elapsed_s,node,x,y,z\n')
        checked, next_log, loop_marked = False, 0.0, False
        try:
            while not self.stop_ev.is_set():
                now = time.time()
                if self.plan and self.t0 is None and (a.mob_trigger == 'now' or os.path.exists(go)):
                    try:
                        start_tracked_mobility(self.net, self.nodes, self.plan, a.mob_reps)
                    except Exception:  # noqa: BLE001 -- surface API failures to the runner
                        import traceback
                        with open(os.path.join(a.log_dir, 'mob_error.txt'), 'w') as f:
                            traceback.print_exc(file=f)
                        warn('*** tracked mobility FAILED to start, see mob_error.txt\n')
                        raise
                    self.t0 = time.time()
                    with open(os.path.join(a.log_dir, 'mob_started.txt'), 'w') as f:
                        f.write('{:.3f}\n'.format(self.t0))
                if self.plan and self.t0 is not None and not loop_marked:
                    # Official hook (mob_object.get_mob_started): True once Tracked.run is
                    # inside its loop. Older Mininet-WiFi without the method: record nothing.
                    if not hasattr(self.net, 'is_mob_started'):
                        loop_marked = True
                    else:
                        try:
                            started = bool(self.net.is_mob_started())
                        except Exception:  # noqa: BLE001
                            started = False
                        if started:
                            loop_marked = True
                            with open(os.path.join(a.log_dir, 'mob_loop_started.txt'), 'w') as f:
                                f.write('{:.3f}\n'.format(time.time()))
                elapsed = None if self.t0 is None else now - self.t0
                if now < next_log:
                    self.stop_ev.wait(0.1)   # poll at 10 Hz (trigger, is_mob_started), log at 1 Hz
                    continue
                next_log = now + 1.0
                pos = self.positions()
                for name, p in pos.items():
                    log.write('{:.3f},{},{},{:.2f},{:.2f},{:.2f}\n'.format(
                        now, '' if elapsed is None else '{:.2f}'.format(elapsed), name, *p[:3]))
                log.flush()
                if self.plan and elapsed is not None and elapsed >= 10 and not checked:
                    checked = True
                    moved = any(max(abs(x - y) for x, y in zip(pos[n], self.start_pos[n])) > 0.5
                                for n in self.plan['nodes'])
                    if not moved:
                        with open(os.path.join(a.log_dir, 'mob_error.txt'), 'w') as f:
                            f.write('nodes did not move within 10 s of the trigger: {}\n'.format(pos))
                        warn('*** tracked mobility did not move the nodes, see mob_error.txt\n')
                self.stop_ev.wait(0.1)
        finally:
            log.close()


def mac_of(node, intf):
    return node.cmd('cat /sys/class/net/{}/address'.format(intf)).strip()


def link_state(node, intf):
    return node.cmd('iw dev {} link'.format(intf))


def wait_associated(radios, timeout):
    """radios: list of (node, intf, ssid, bssid). Verify the associations made
    by the Mininet-WiFi API; retry like a supplicant until each radio is on its
    own SSID."""
    deadline = time.time() + timeout
    pending = list(radios)
    manual = set()
    while pending and time.time() < deadline:
        still = []
        for node, intf, ssid, bssid in pending:
            link = link_state(node, intf)
            if 'Connected to' in link and 'SSID: {}'.format(ssid) in link:
                info('*** {} associated to {} ({})\n'.format(
                    intf, ssid, 'after manual connect' if intf in manual else 'by the Mininet-WiFi API'))
                continue
            if 'Connected to' in link:
                node.cmd('iw dev {} disconnect'.format(intf))
            node.cmd('iw dev {} connect {} {}'.format(intf, ssid, bssid))
            manual.add(intf)
            still.append((node, intf, ssid, bssid))
        pending = still
        if pending:
            time.sleep(1)
    for _, intf, ssid, _ in pending:
        warn('*** {} did NOT associate to {}\n'.format(intf, ssid))
    return {intf: ('failed' if any(i == intf for _, i, _, _ in pending) else
                   'manual' if intf in manual else 'api') for _, intf, _, _ in radios}


def assoc_checkpoint(label, links, wait=3.0):
    """Log which API-made associations are up (polling up to `wait` s): the
    association started by Intf.associate (iw connect) completes asynchronously."""
    deadline = time.time() + wait
    while True:
        state = {intf: 'Connected to' in link_state(node, intf) for node, intf, _ in links}
        if all(state.values()) or time.time() >= deadline:
            break
        time.sleep(0.5)
    info('*** association {}: {}\n'.format(
        label, ', '.join('{}={}'.format(i, 'up' if ok else 'down') for i, ok in state.items())))
    return state


def wait_ap_enabled(aps, timeout=15.0):
    """aps: list of (node, intf); wait until hostapd has each radio in AP mode."""
    deadline = time.time() + timeout
    pending = list(aps)
    while pending and time.time() < deadline:
        pending = [(n, i) for n, i in pending if 'type AP' not in n.cmd('iw dev {} info'.format(i))]
        if pending:
            time.sleep(0.5)
    for _, intf in pending:
        warn('*** {} is not in AP mode yet\n'.format(intf))
    info('*** AP radios enabled: {}\n'.format(', '.join(i for _, i in aps if (_, i) not in pending)))


def iot_ap_up(r1, tries=20):
    for _ in range(tries):
        if 'type AP' in r1.cmd('iw dev {} info'.format(IOT_IF)):
            return True
        time.sleep(0.5)
    return False


def save_wmediumd_files(log_dir):
    """Copy the config mn_wifi generated for wmediumd (medium_array, positions, propagation
    model) and wmediumd's log into <log-dir>: the evidence of the mediums actually used.
    Called after configureNodes (config) and again at teardown (complete log)."""
    cfg = log = None
    try:
        from mn_wifi.wmediumdConnector import WStarter
        cfg = WStarter.wmd_config_name
        log = getattr(WStarter.wmd_logfile, 'name', None)
    except Exception:  # noqa: BLE001 -- older Mininet-WiFi: fall back to the /tmp pattern
        pass
    if not cfg:
        cands = sorted(glob.glob('/tmp/mn_wmd_config_*.cfg'), key=os.path.getmtime)
        cfg = cands[-1] if cands else None
    for src, dst in ((cfg, 'wmediumd_config.cfg'), (log, 'wmediumd.log')):
        if src and os.path.isfile(src):
            try:
                shutil.copyfile(src, os.path.join(log_dir, dst))
            except OSError as e:
                warn('*** could not copy {}: {}\n'.format(src, e))


def bg(node, cmd):
    """Start a background command in node's namespace; returns its pid."""
    out = node.cmd('{} > /dev/null 2>&1 & echo $!'.format(cmd)).strip().splitlines()
    return out[-1] if out else ''


def ap_port_to(ap, router):
    """Name of the AP's wired interface whose link ends on `router` (mn_wifi names it).
    Wireless links of mn_wifi keep plain strings (e.g. 'wifi') as link ends, so only
    ends that are interface objects with a node are compared."""
    for intf in ap.intfList():
        link = getattr(intf, 'link', None)
        if link is None:
            continue
        ends = [i for i in (getattr(link, 'intf1', None), getattr(link, 'intf2', None))
                if getattr(i, 'node', None) is not None]
        if intf in ends and any(i is not intf and i.node is router for i in ends):
            return intf.name
    return None


def main():
    args = parse_args()
    setLogLevel('info')
    os.makedirs(args.log_dir, exist_ok=True)
    for stale in ('mob_go', 'mob_started.txt', 'mob_loop_started.txt', 'mob_error.txt',
                  'iot_ap_method.txt', 'wmediumd_config.cfg', 'wmediumd.log', 'netem_params.txt'):
        try:
            os.remove(os.path.join(args.log_dir, stale))
        except FileNotFoundError:
            pass

    # Auto association/handover off: associations are made explicitly below.
    net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference,
                       noise_th=args.noise_th, fading_cof=args.fading,
                       allAutoAssociation=False)

    x, y, z = R1_POS
    ap1_pos = (x - args.dist1, y, z)
    ap2_pos = (x + args.dist2, y, z)
    start, plan = plan_mobility(args)
    r1_start, h1_start = start['r1'], start['h1']

    radio = {}
    if args.txpower is not None:
        radio['txpower'] = args.txpower
    ap_common = dict(mode=args.mode, failMode='standalone', **radio)

    h1 = net.addStation('h1', position=pos_str(h1_start), **radio)             # IoT device
    r1 = net.addStation('r1', wlans=3, position=pos_str(r1_start), **radio)    # IoT gateway
    r2 = net.addHost('r2')
    r3 = net.addHost('r3')
    r4 = net.addHost('r4')
    r5 = net.addHost('r5')                                                     # server-side proxy
    h2 = net.addHost('h2')                                                     # server
    ap1 = net.addAccessPoint('ap1', ssid='path1', channel=args.ch1,
                             position=pos_str(ap1_pos), **ap_common)
    ap2 = net.addAccessPoint('ap2', ssid='path2', channel=args.ch2,
                             position=pos_str(ap2_pos), **ap_common)

    net.setPropagationModel(model='logDistance', exp=args.exp)
    net.setInitialMediums((('h1-wlan0', IOT_IF), (P1_IF, ap1), (P2_IF, ap2)))
    getattr(net, 'configureNodes', net.configureWifiNodes)()
    save_wmediumd_files(args.log_dir)

    # IoT access point on r1-wlan0 with the official API, before build() as in
    # mn_wifi/examples/sta_ap_mode.py (see module docstring for the index-0 constraint).
    r1.setMasterMode(intf=IOT_IF, ssid='iot', mode=args.mode_iot, channel=args.ch_iot)

    # Station -> AP associations with the official API (infrastructure links).
    # Intf.associate issues one asynchronous `iw connect`; the kernel does not retry
    # if the AP is not beaconing yet, and with allAutoAssociation=False nobody else
    # does, so wait for hostapd (type AP) + beacons first, then verify/retry with the
    # same Intf method the API uses (iw_connect).
    aps_up = [(ap1, ap1.wintfs[0].name), (ap2, ap2.wintfs[0].name), (r1, IOT_IF)]
    wait_ap_enabled(aps_up)
    time.sleep(1.0)  # beacon interval is 100 ms; let the first beacons go out
    net.addLink(r1, ap1, port1=1, port2=0)
    net.addLink(r1, ap2, port1=2, port2=0)
    h1.wintfs[0].associate(r1.wintfs[0])
    api_links = [(r1, P1_IF, ap1.wintfs[0]), (r1, P2_IF, ap2.wintfs[0]),
                 (h1, 'h1-wlan0', r1.wintfs[0])]
    for node, intf, ap_intf in api_links:
        info('*** API association {} -> {}: distance {:.0f} m, AP range {:.0f} m\n'.format(
            intf, ap_intf.name, node.get_distance_to(ap_intf.node), float(ap_intf.range)))
    for attempt in range(3):
        state = assoc_checkpoint('after associate(), try {}'.format(attempt + 1), api_links, wait=8.0)
        if all(state.values()):
            break
        for node, intf, ap_intf in api_links:
            if not state[intf]:
                node.wintfs[node.params['wlan'].index(intf)].iw_connect(ap_intf)

    # Wired links (names pinned to match topo.py / topo_wifi.py).
    net.addLink(ap1, r2, cls=TCLink, intfName2='r2-eth0')
    net.addLink(ap2, r3, cls=TCLink, intfName2='r3-eth0')
    net.addLink(r2, r4, cls=TCLink, intfName1='r2-eth1', intfName2='r4-eth0')
    net.addLink(r3, r4, cls=TCLink, intfName1='r3-eth1', intfName2='r4-eth1')
    net.addLink(r4, r5, cls=TCLink, intfName1='r4-eth2', intfName2='r5-eth0')
    net.addLink(r5, h2, cls=TCLink, intfName1='r5-eth1', intfName2='h2-eth0')

    net.build()
    assoc_checkpoint('after build()', api_links)
    ap1.start([])
    ap2.start([])
    assoc_checkpoint('after ap.start()', api_links)

    ok = iot_ap_up(r1)
    # 'api' or 'failed' (the runner requires 'api'); the manual value no longer exists.
    with open(os.path.join(args.log_dir, 'iot_ap_method.txt'), 'w') as f:
        f.write(('api' if ok else 'failed') + '\n')
    if not ok:
        error('*** {} is not in AP mode after setMasterMode/build; aborting. iw dev:\n{}\n'.format(
            IOT_IF, r1.cmd('iw dev {} info'.format(IOT_IF))))
        net.stop()
        return 1
    info('*** {} IoT AP up: method=api (SSID iot, mode {}, channel {})\n'.format(
        IOT_IF, args.mode_iot, args.ch_iot))

    info('*** wmediumd interference: mode={} exp={} fading={} noise_th={}\n'.format(
        args.mode, args.exp, args.fading, args.noise_th))
    for name, pos in (('h1', h1_start), ('r1', r1_start), ('ap1', ap1_pos), ('ap2', ap2_pos)):
        info('*** position {}: {}\n'.format(name, pos_str(pos)))
    if plan:
        for name, spec in plan['nodes'].items():
            info('*** mobility {}: waypoints {} from t={:g} s to t={:g} s per repetition '
                 '(repetition {:g} s, reverse, reps {})\n'.format(
                     name, ' -> '.join(pos_str(c) for c in spec['coord']), spec['start'],
                     spec['stop'], plan['rep_len'], args.mob_reps or 'unlimited'))
        if args.h1_follow:
            info('*** mobility h1: carried with r1 (IoT hop constant)\n')
        info('*** mobility trigger: {}\n'.format(
            'now' if args.mob_trigger == 'now' else os.path.join(args.log_dir, 'mob_go')))

    bss = {'path1': ap1.wintfs[0].mac, 'path2': ap2.wintfs[0].mac,
           'iot': mac_of(r1, IOT_IF)}
    radios = [(r1, P1_IF, 'path1', bss['path1']),
              (r1, P2_IF, 'path2', bss['path2']),
              (h1, 'h1-wlan0', 'iot', bss['iot'])]
    assoc = wait_associated(radios, args.assoc_timeout)
    with open(os.path.join(args.log_dir, 'assoc_method.txt'), 'w') as f:
        f.write(''.join('{} {}\n'.format(intf, how) for intf, how in assoc.items()))
    for node, intf, _, _ in radios:
        sig = [l.strip() for l in link_state(node, intf).splitlines()
               if 'signal' in l or 'tx bitrate' in l]
        info('*** {}: {}\n'.format(intf, '; '.join(sig) or 'not connected'))

    # Background helpers (all killed at exit).
    helpers = []
    scripts = {}
    for name, body in (('reconnect.sh', RECONNECT_SH), ('radio_log.sh', RADIO_LOG_SH),
                       ('ifstats.sh', IFSTATS_SH)):
        scripts[name] = os.path.join(args.log_dir, name)
        with open(scripts[name], 'w') as f:
            f.write(body)
    for node, intf, ssid, bssid in radios:
        log = os.path.join(args.log_dir, 'assoc_{}.log'.format(intf))
        open(log, 'w').close()
        helpers.append((node, bg(node, 'sh {} {} {} {} {}'.format(
            scripts['reconnect.sh'], intf, ssid, bssid, log))))
    wifi_intfs = ((r1, IOT_IF), (r1, P1_IF), (r1, P2_IF), (h1, 'h1-wlan0'),
                  (ap1, ap1.wintfs[0].name), (ap2, ap2.wintfs[0].name))
    for node, intf in wifi_intfs:
        csv = os.path.join(args.log_dir, 'radio_{}.csv'.format(intf))
        helpers.append((node, bg(node, 'sh {} {} {}'.format(scripts['radio_log.sh'], intf, csv))))
    for node, intf in ((r1, IOT_IF), (r1, P1_IF), (r1, P2_IF), (h1, 'h1-wlan0')):
        csv = os.path.join(args.log_dir, 'ifstats_{}.csv'.format(intf))
        helpers.append((node, bg(node, 'sh {} {} {}'.format(scripts['ifstats.sh'], intf, csv))))
    vmstat_out = open(os.path.join(args.log_dir, 'vmstat.txt'), 'w')
    try:
        vmstat = subprocess.Popen(['vmstat', '-n', '-t', '1'], stdout=vmstat_out,
                                  stderr=subprocess.STDOUT)
    except OSError as e:
        warn('*** vmstat not started: {!r}\n'.format(e))
        vmstat = None

    # Addressing.
    for node, intfs in ((h1, ['h1-wlan0']),
                        (r1, [IOT_IF, P1_IF, P2_IF]),
                        (r2, ['r2-eth0', 'r2-eth1']),
                        (r3, ['r3-eth0', 'r3-eth1']),
                        (r4, ['r4-eth0', 'r4-eth1', 'r4-eth2']),
                        (r5, ['r5-eth0', 'r5-eth1']),
                        (h2, ['h2-eth0'])):
        for intf in intfs:
            node.cmd('ip addr flush dev {}'.format(intf))
    for r in (r1, r2, r3, r4, r5):
        r.cmd('echo 1 > /proc/sys/net/ipv4/ip_forward')

    for node, intf, ip in ((h1, 'h1-wlan0', '10.0.0.1/24'),
                           (r1, IOT_IF, '10.0.0.2/24'),
                           (r1, P1_IF, '10.0.1.1/24'),
                           (r1, P2_IF, '10.0.2.1/24'),
                           (r2, 'r2-eth0', '10.0.1.2/24'),
                           (r2, 'r2-eth1', '10.0.4.1/24'),
                           (r3, 'r3-eth0', '10.0.2.2/24'),
                           (r3, 'r3-eth1', '10.0.5.1/24'),
                           (r4, 'r4-eth0', '10.0.4.2/24'),
                           (r4, 'r4-eth1', '10.0.5.2/24'),
                           (r4, 'r4-eth2', '10.0.6.1/24'),
                           (r5, 'r5-eth0', '10.0.6.2/24'),
                           (r5, 'r5-eth1', '10.0.7.1/24'),
                           (h2, 'h2-eth0', '10.0.7.2/24')):
        try:
            node.setIP(ip, intf=intf)
        except Exception as e:  # noqa: BLE001 -- fall back to iproute2
            warn('*** setIP {} {} failed ({!r}); using ip addr\n'.format(intf, ip, e))
        if ip.split('/')[0] not in node.cmd('ip -4 addr show dev {}'.format(intf)):
            node.cmd('ip addr add {} dev {}'.format(ip, intf))

    # Routing (same logic as topo.py, wireless interface names).
    h1.cmd('ip route flush table main')
    h1.cmd('ip route add 10.0.0.0/24 dev h1-wlan0 scope link')
    h1.cmd('ip route add default via 10.0.0.2 dev h1-wlan0')

    r1.cmd('ip rule add from 10.0.0.2 table 1')
    r1.cmd('ip rule add from 10.0.1.1 table 2')
    r1.cmd('ip rule add from 10.0.2.1 table 3')
    r1.cmd('ip route add 10.0.0.0/24 dev {} scope link table 1'.format(IOT_IF))
    r1.cmd('ip route add default via 10.0.0.1 dev {} table 1'.format(IOT_IF))
    r1.cmd('ip route add 10.0.1.0/24 dev {} scope link table 2'.format(P1_IF))
    r1.cmd('ip route add default via 10.0.1.2 dev {} table 2'.format(P1_IF))
    r1.cmd('ip route add 10.0.2.0/24 dev {} scope link table 3'.format(P2_IF))
    r1.cmd('ip route add default via 10.0.2.2 dev {} table 3'.format(P2_IF))
    r1.cmd('ip route flush table main')
    r1.cmd('ip route add 10.0.0.0/24 dev {} scope link'.format(IOT_IF))
    r1.cmd('ip route add 10.0.1.0/24 dev {} scope link'.format(P1_IF))
    r1.cmd('ip route add 10.0.2.0/24 dev {} scope link'.format(P2_IF))
    r1.cmd('ip route add 10.0.4.0/24 via 10.0.1.2 dev {}'.format(P1_IF))
    r1.cmd('ip route add 10.0.5.0/24 via 10.0.2.2 dev {}'.format(P2_IF))
    r1.cmd('ip route add default via 10.0.1.2 dev {}'.format(P1_IF))

    r2.cmd('ip route flush table main')
    r2.cmd('ip route add 10.0.1.0/24 dev r2-eth0 scope link')
    r2.cmd('ip route add 10.0.4.0/24 dev r2-eth1 scope link')
    r2.cmd('ip route add 10.0.0.0/24 via 10.0.1.1 dev r2-eth0')
    r2.cmd('ip route add 10.0.2.0/24 via 10.0.1.1 dev r2-eth0')
    r2.cmd('ip route add default via 10.0.4.2 dev r2-eth1')

    r3.cmd('ip route flush table main')
    r3.cmd('ip route add 10.0.2.0/24 dev r3-eth0 scope link')
    r3.cmd('ip route add 10.0.5.0/24 dev r3-eth1 scope link')
    r3.cmd('ip route add 10.0.0.0/24 via 10.0.2.1 dev r3-eth0')
    r3.cmd('ip route add 10.0.1.0/24 via 10.0.2.1 dev r3-eth0')
    r3.cmd('ip route add default via 10.0.5.2 dev r3-eth1')

    r4.cmd('ip route add 10.0.0.0/24 via 10.0.4.1 dev r4-eth0')
    r4.cmd('ip route add 10.0.1.0/24 via 10.0.4.1 dev r4-eth0')
    r4.cmd('ip route add 10.0.2.0/24 via 10.0.5.1 dev r4-eth1')
    r4.cmd('ip route add default via 10.0.6.2 dev r4-eth2')
    r5.cmd('ip route add default via 10.0.6.1 dev r5-eth0')
    h2.cmd('ip route add default via 10.0.7.1 dev h2-eth0')

    # v5: qdisc placement of the original topo.py, transposed to the Wi-Fi topology.
    #   topo.py: cake on r1-eth1/r1-eth2 (uplink) and r4-eth0/r4-eth1 (downlink), root;
    #            netem on r2-eth0/r2-eth1 and r3-eth0/r3-eth1 (one per direction), default limit.
    #   v5:      r1-eth1/r1-eth2 are now Wi-Fi (r1-wlan1/2), so the uplink cake moves to the
    #            first wired hop, the AP port towards r2/r3 (egress = uplink); downlink cake stays
    #            root on r4-eth0/r4-eth1; netem stays on both r2 and both r3 ports. Per direction
    #            the order is cake -> netem as in topo.py. No qdisc on any Wi-Fi interface.
    cake = 'cake bandwidth {:g}mbit besteffort flowblind'
    ap_ports = {}
    for ap, router in ((ap1, r2), (ap2, r3)):
        port = ap_port_to(ap, router)
        if port is None:
            warn('*** no wired port from {} to {} found\n'.format(ap.name, router.name))
            net.stop()
            sys.exit(1)
        ap_ports[ap.name] = port
    with open(os.path.join(args.log_dir, 'ap_ports.txt'), 'w') as f:
        for name in ('ap1', 'ap2'):
            f.write('{} {}\n'.format(name, ap_ports[name]))
    jitters = {}
    for name, router, intfs, delay in (('path1', r2, ('r2-eth0', 'r2-eth1'), args.delay1),
                                       ('path2', r3, ('r3-eth0', 'r3-eth1'), args.delay2)):
        # v7: per-path jitter = --jitter (fixed ms) or --jitter-pct % of this path's one-way delay.
        jit = args.jitter if args.jitter > 0 else delay * args.jitter_pct / 100.0
        jitters[name] = jit
        # Same argument order as experiments/network_variation: "netem loss L delay D J";
        # no limit argument, i.e. netem's default (1000 packets) as in topo.py.
        netem = 'netem{} delay {:g}ms{}'.format(
            ' loss {:g}%'.format(args.loss) if args.loss > 0 else '',
            delay, ' {:g}ms'.format(jit) if jit > 0 else '')
        for intf in intfs:
            router.cmd('tc qdisc replace dev {} root handle 1: {}'.format(intf, netem))
    with open(os.path.join(args.log_dir, 'netem_params.txt'), 'w') as f:
        f.write('path delay_ms jitter_ms loss_pct\n')
        f.write('path1 {:g} {:g} {:g}\n'.format(args.delay1, jitters['path1'], args.loss))
        f.write('path2 {:g} {:g} {:g}\n'.format(args.delay2, jitters['path2'], args.loss))
    info('*** netem: path1 {:g} ms (jitter {:g} ms) on r2-eth0/r2-eth1, path2 {:g} ms '
         '(jitter {:g} ms) on r3-eth0/r3-eth1, loss {:g} %{}\n'.format(
             args.delay1, jitters['path1'], args.delay2, jitters['path2'], args.loss,
             ' [jitter = {:g} % of the one-way delay]'.format(args.jitter_pct) if args.jitter_pct > 0 else ''))
    if not args.native_bw:
        for node, intf, bw in ((ap1, ap_ports['ap1'], args.bw1), (ap2, ap_ports['ap2'], args.bw2),
                               (r4, 'r4-eth0', args.bw1), (r4, 'r4-eth1', args.bw2)):
            out = node.cmd('tc qdisc replace dev {} root {}'.format(intf, cake.format(bw)))
            if out.strip():
                warn('*** cake on {} failed: {}\n'.format(intf, out.strip()))
        info('*** cake: path1 {:g} Mbit on {} (up) and r4-eth0 (down), path2 {:g} Mbit on {} (up) '
             'and r4-eth1 (down)\n'.format(args.bw1, ap_ports['ap1'], args.bw2, ap_ports['ap2']))

    watch = MobilityWatch(args, net, {'r1': r1, 'h1': h1}, plan, start)
    watch.start()

    try:
        CLI(net)
    finally:
        watch.stop_ev.set()
        watch.join(timeout=3)
        if vmstat:
            vmstat.terminate()
        vmstat_out.close()
        for node, pid in helpers:
            if pid.isdigit():
                node.cmd('kill {} 2>/dev/null'.format(pid))
        save_wmediumd_files(args.log_dir)   # complete wmediumd log
        r1.cmd("pkill -f 'hostapd.*r1-wlan' 2>/dev/null")
        net.stop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
