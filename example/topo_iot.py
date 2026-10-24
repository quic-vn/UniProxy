#!/usr/bin/env python3
"""IoT-gateway variant of the UniProxy topology on Mininet-WiFi.

    h1 ~~ r1 (IoT gateway, client-side proxy) ~~ ap1 ~~ r2 ──┐
                                               ~~ ap2 ~~ r3 ──┴── r4 ── r5 ── h2
    ~~ Wi-Fi (mac80211_hwsim + wmediumd)      ── Ethernet

- h1: station (h1-wlan0), associates to the AP that r1 runs on r1-wlan2.
- r1: station with 3 radios: r1-wlan0 -> ap1 (SSID path1), r1-wlan1 -> ap2
  (SSID path2), r1-wlan2 in AP mode (SSID iot, hostapd started by this
  script inside r1's namespace) serving h1. r1 runs the
  client-side proxy; TPROXY must intercept on r1-wlan2 (not r1-eth0).
- ap1/ap2: access points (L2 bridges); r2/r3 are stations of ap1/ap2 with a
  wired port to r4. Each path therefore has two wireless hops (r1~ap~r2/r3).
- Three separate wmediumd mediums: {h1, r1-wlan2}, {r1-wlan0, ap1, r2},
  {r1-wlan1, ap2, r3}. wmediumd runs in interference mode with the
  logDistance propagation model.
- Layout (metres): r1 at (200,100); ap1 at dist1 on -x, ap2 at dist2 on +x, so
  moving r1 along x strengthens one path while weakening the other; r2/r3
  are --bh-dist from their AP; h1 is --h1-dist from r1 on +y.
- Mobility (Mininet-WiFi tracked mobility): --r1-move / --h1-move make a node
  go back and forth between two points (offsets from its initial position),
  --mob-period seconds per direction, repeated --mob-reps times.
- Mininet-WiFi auto association/handover is disabled (allAutoAssociation=False);
  every managed radio runs a small reconnect loop (like a supplicant) that
  re-associates to its own SSID after a loss of association. Reconnects are
  logged with timestamps to <log-dir>/assoc_<intf>.log.

Addresses keep topo.py's subnets: h1 10.0.0.1 / r1-wlan2 10.0.0.2,
r1-wlan0 10.0.1.1 / r2-wlan0 10.0.1.2, r1-wlan1 10.0.2.1 / r3-wlan0 10.0.2.2.

Written against Mininet-WiFi 2.7.
"""

import argparse
import os
import sys
import time

from mininet.link import TCLink
from mininet.log import setLogLevel, info, warn
from mn_wifi.cli import CLI
from mn_wifi.link import wmediumd
from mn_wifi.net import Mininet_wifi
from mn_wifi.wmediumdConnector import interference

R1_POS = (200.0, 100.0, 0.0)

RECONNECT_SH = r'''#!/bin/sh
# usage: reconnect.sh <intf> <ssid> <bssid> <log>
i=$1; s=$2; b=$3; log=$4
while :; do
  if ! iw dev "$i" link 2>/dev/null | grep -q "SSID: $s"; then
    echo "$(date +%H:%M:%S.%N | cut -c1-12) $i lost/not associated to $s -> reconnect" >> "$log"
    iw dev "$i" disconnect >/dev/null 2>&1
    iw dev "$i" connect "$s" "$b" >/dev/null 2>&1
  fi
  sleep 1
done
'''


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--mode', default='a',
                   help="802.11 mode of ap1/ap2 and of r1's IoT AP; default a "
                        "(HT modes failed under wmediumd on Mininet-WiFi 2.6)")
    p.add_argument('--ch1', default='36', help='channel of ap1 / path 1')
    p.add_argument('--ch2', default='149', help='channel of ap2 / path 2')
    p.add_argument('--ch-iot', default='44', help="channel of r1's IoT AP (h1 link)")
    p.add_argument('--dist1', type=float, default=70,
                   help='initial r1-ap1 distance (m); default 70')
    p.add_argument('--dist2', type=float, default=110,
                   help='initial r1-ap2 distance (m); default 110')
    p.add_argument('--bh-dist', type=float, default=5,
                   help='ap1-r2 and ap2-r3 distance (m); default 5')
    p.add_argument('--h1-dist', type=float, default=5,
                   help='initial h1-r1 distance (m); default 5')
    p.add_argument('--exp', type=float, default=3.0,
                   help='logDistance path-loss exponent; default 3')
    p.add_argument('--fading', type=int, default=0,
                   help='wmediumd fading coefficient (integer, all links); default 0')
    p.add_argument('--noise-th', type=int, default=-91,
                   help='wmediumd noise threshold (dBm); default -91')
    p.add_argument('--txpower', type=float, default=None,
                   help='tx power (dBm) of every radio; default mn_wifi value')
    p.add_argument('--native-bw', action='store_true', help='do not add cake shapers')
    p.add_argument('--bw1', type=float, default=10, help='cake rate (Mbit) path 1; default 10')
    p.add_argument('--bw2', type=float, default=5, help='cake rate (Mbit) path 2; default 5')
    p.add_argument('--delay1', type=float, default=5,
                   help='one-way delay (ms) of path 1, applied on r2-eth1 and r4-eth0; default 5')
    p.add_argument('--delay2', type=float, default=5,
                   help='one-way delay (ms) of path 2, applied on r3-eth1 and r4-eth1; '
                        'default 5 (runner sets RTT/2)')
    m = p.add_argument_group('mobility (Mininet-WiFi tracked mobility)')
    m.add_argument('--r1-move', default=None, metavar='DX1,DY1:DX2,DY2',
                   help='r1 goes back and forth between these two offsets (m) '
                        'from its initial position, e.g. "-40,0:40,0"')
    m.add_argument('--h1-move', default=None, metavar='DX1,DY1:DX2,DY2',
                   help='h1 goes back and forth between these two offsets (m) '
                        'from its initial position, e.g. "0,0:0,30"')
    m.add_argument('--mob-period', type=int, default=20,
                   help='seconds per direction of every movement; default 20')
    m.add_argument('--mob-reps', type=int, default=10000,
                   help='number of one-way movements (reverse on odd ones); default 10000')
    p.add_argument('--assoc-timeout', type=float, default=20,
                   help='seconds to wait for all radios to associate at start')
    p.add_argument('--log-dir', default='/tmp/topo_iot',
                   help='directory for association logs; default /tmp/topo_iot')
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
    args, _ = p.parse_known_args(fixed)
    return args


def pos_str(pos):
    return ','.join('{:g}'.format(c) for c in pos)


def add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def parse_move(spec, base):
    """'dx1,dy1:dx2,dy2' -> two absolute positions relative to base."""
    pts = []
    for part in spec.split(':'):
        d = [float(v) for v in part.split(',')]
        d += [0.0] * (3 - len(d))
        pts.append(add(base, d))
    if len(pts) != 2:
        raise SystemExit('move spec must be "dx1,dy1:dx2,dy2", got {!r}'.format(spec))
    return pts


def mac_of(node, intf):
    return node.cmd('cat /sys/class/net/{}/address'.format(intf)).strip()


def link_state(node, intf):
    return node.cmd('iw dev {} link'.format(intf))


def wait_associated(radios, timeout):
    """radios: list of (node, intf, ssid, bssid). Retry until each radio is
    associated to its own SSID."""
    deadline = time.time() + timeout
    pending = list(radios)
    while pending and time.time() < deadline:
        still = []
        for node, intf, ssid, bssid in pending:
            link = link_state(node, intf)
            if 'Connected to' in link and 'SSID: {}'.format(ssid) in link:
                info('*** {} associated to {}\n'.format(intf, ssid))
                continue
            if 'Connected to' in link:
                node.cmd('iw dev {} disconnect'.format(intf))
            node.cmd('iw dev {} connect {} {}'.format(intf, ssid, bssid))
            still.append((node, intf, ssid, bssid))
        pending = still
        if pending:
            time.sleep(1)
    for _, intf, ssid, _ in pending:
        warn('*** {} did NOT associate to {}\n'.format(intf, ssid))
    return not pending


def main():
    args = parse_args()
    setLogLevel('info')
    os.makedirs(args.log_dir, exist_ok=True)

    # Auto association/handover off: associations are managed below.
    net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference,
                       noise_th=args.noise_th, fading_cof=args.fading,
                       allAutoAssociation=False)

    x, y, z = R1_POS
    r1_pos = R1_POS
    h1_pos = (x, y + args.h1_dist, z)
    ap1_pos = (x - args.dist1, y, z)
    ap2_pos = (x + args.dist2, y, z)
    r2_pos = (ap1_pos[0], ap1_pos[1] - args.bh_dist, z)
    r3_pos = (ap2_pos[0], ap2_pos[1] - args.bh_dist, z)

    moves = {}
    if args.r1_move:
        moves['r1'] = parse_move(args.r1_move, r1_pos)
        r1_pos = moves['r1'][0]
    if args.h1_move:
        moves['h1'] = parse_move(args.h1_move, h1_pos)
        h1_pos = moves['h1'][0]

    radio = {}
    if args.txpower is not None:
        radio['txpower'] = args.txpower
    ap_common = dict(mode=args.mode, failMode='standalone', **radio)

    h1 = net.addStation('h1', position=pos_str(h1_pos), **radio)              # IoT device
    r1 = net.addStation('r1', wlans=3, position=pos_str(r1_pos), **radio)     # IoT gateway
    r2 = net.addStation('r2', position=pos_str(r2_pos), **radio)
    r3 = net.addStation('r3', position=pos_str(r3_pos), **radio)
    r4 = net.addHost('r4')
    r5 = net.addHost('r5')                                                    # server-side proxy
    h2 = net.addHost('h2')                                                    # server
    ap1 = net.addAccessPoint('ap1', ssid='path1', channel=args.ch1,
                             position=pos_str(ap1_pos), **ap_common)
    ap2 = net.addAccessPoint('ap2', ssid='path2', channel=args.ch2,
                             position=pos_str(ap2_pos), **ap_common)

    net.setPropagationModel(model='logDistance', exp=args.exp)
    net.setInitialMediums((('h1-wlan0', 'r1-wlan2'),
                           ('r1-wlan0', ap1, 'r2-wlan0'),
                           ('r1-wlan1', ap2, 'r3-wlan0')))

    net.configureWifiNodes()
    # r1-wlan2 becomes the IoT AP after build(): hostapd is started directly
    # (setMasterMode did not bring the AP up on Mininet-WiFi 2.6).

    # Wired links (names pinned to match topo.py where the role is the same).
    net.addLink(r2, r4, cls=TCLink, intfName1='r2-eth1', intfName2='r4-eth0')
    net.addLink(r3, r4, cls=TCLink, intfName1='r3-eth1', intfName2='r4-eth1')
    net.addLink(r4, r5, cls=TCLink, intfName1='r4-eth2', intfName2='r5-eth0')
    net.addLink(r5, h2, cls=TCLink, intfName1='r5-eth1', intfName2='h2-eth0')

    if moves:
        net.startMobility(time=0, mob_rep=args.mob_reps, reverse=True)
        for name, (a, b) in moves.items():
            node = net.get(name)
            net.mobility(node, 'start', time=0, position=pos_str(a))
            net.mobility(node, 'stop', time=args.mob_period, position=pos_str(b))
        net.stopMobility(time=args.mob_period)

    net.build()
    ap1.start([])
    ap2.start([])

    # IoT access point on r1-wlan2 (hostapd inside r1's namespace).
    hw_mode = 'a' if args.mode in ('a', 'n', 'ac') else args.mode
    ap_conf = os.path.join(args.log_dir, 'hostapd_r1-wlan2.conf')
    ap_pid = os.path.join(args.log_dir, 'hostapd_r1-wlan2.pid')
    ap_log = os.path.join(args.log_dir, 'hostapd_r1-wlan2.log')
    with open(ap_conf, 'w') as f:
        f.write('interface=r1-wlan2\ndriver=nl80211\nssid=iot\n'
                'hw_mode={}\nchannel={}\ncountry_code=US\n'.format(hw_mode, args.ch_iot))
    r1.cmd('hostapd -B -P {} {} > {} 2>&1'.format(ap_pid, ap_conf, ap_log))
    for _ in range(20):
        if 'type AP' in r1.cmd('iw dev r1-wlan2 info'):
            info('*** r1-wlan2: IoT AP up (SSID iot, channel {})\n'.format(args.ch_iot))
            break
        time.sleep(0.5)
    else:
        warn('*** r1-wlan2: IoT AP did NOT come up; hostapd output:\n{}\n'.format(
            r1.cmd('cat {}'.format(ap_log))))

    info('*** wmediumd interference: mode={} exp={} fading={} noise_th={}\n'.format(
        args.mode, args.exp, args.fading, args.noise_th))
    for name, pos in (('h1', h1_pos), ('r1', r1_pos), ('ap1', ap1_pos), ('r2', r2_pos),
                      ('ap2', ap2_pos), ('r3', r3_pos)):
        info('*** position {}: {}\n'.format(name, pos_str(pos)))
    for name, (a, b) in moves.items():
        info('*** mobility {}: {} <-> {}, {} s per direction, {} moves\n'.format(
            name, pos_str(a), pos_str(b), args.mob_period, args.mob_reps))

    bss = {'path1': ap1.wintfs[0].mac, 'path2': ap2.wintfs[0].mac,
           'iot': mac_of(r1, 'r1-wlan2')}
    radios = [(r1, 'r1-wlan0', 'path1', bss['path1']),
              (r1, 'r1-wlan1', 'path2', bss['path2']),
              (r2, 'r2-wlan0', 'path1', bss['path1']),
              (r3, 'r3-wlan0', 'path2', bss['path2']),
              (h1, 'h1-wlan0', 'iot', bss['iot'])]
    wait_associated(radios, args.assoc_timeout)
    for node, intf, _, _ in radios:
        sig = [l.strip() for l in link_state(node, intf).splitlines()
               if 'signal' in l or 'tx bitrate' in l]
        info('*** {}: {}\n'.format(intf, '; '.join(sig) or 'not connected'))

    # Supplicant-like reconnect loop per managed radio.
    script = os.path.join(args.log_dir, 'reconnect.sh')
    with open(script, 'w') as f:
        f.write(RECONNECT_SH)
    watchdogs = []
    for node, intf, ssid, bssid in radios:
        log = os.path.join(args.log_dir, 'assoc_{}.log'.format(intf))
        open(log, 'w').close()
        pid = node.cmd('sh {} {} {} {} {} > /dev/null 2>&1 & echo $!'.format(
            script, intf, ssid, bssid, log)).strip().splitlines()[-1]
        watchdogs.append((node, pid))

    # Addressing.
    for node, intfs in ((h1, ['h1-wlan0']),
                        (r1, ['r1-wlan0', 'r1-wlan1', 'r1-wlan2']),
                        (r2, ['r2-wlan0', 'r2-eth1']),
                        (r3, ['r3-wlan0', 'r3-eth1']),
                        (r4, ['r4-eth0', 'r4-eth1', 'r4-eth2']),
                        (r5, ['r5-eth0', 'r5-eth1']),
                        (h2, ['h2-eth0'])):
        for intf in intfs:
            node.cmd('ip addr flush dev {}'.format(intf))
    for r in (r1, r2, r3, r4, r5):
        r.cmd('echo 1 > /proc/sys/net/ipv4/ip_forward')

    h1.cmd('ip addr add 10.0.0.1/24 dev h1-wlan0')
    r1.cmd('ip addr add 10.0.0.2/24 dev r1-wlan2')
    r1.cmd('ip addr add 10.0.1.1/24 dev r1-wlan0')
    r1.cmd('ip addr add 10.0.2.1/24 dev r1-wlan1')
    r2.cmd('ip addr add 10.0.1.2/24 dev r2-wlan0')
    r2.cmd('ip addr add 10.0.4.1/24 dev r2-eth1')
    r3.cmd('ip addr add 10.0.2.2/24 dev r3-wlan0')
    r3.cmd('ip addr add 10.0.5.1/24 dev r3-eth1')
    r4.cmd('ip addr add 10.0.4.2/24 dev r4-eth0')
    r4.cmd('ip addr add 10.0.5.2/24 dev r4-eth1')
    r4.cmd('ip addr add 10.0.6.1/24 dev r4-eth2')
    r5.cmd('ip addr add 10.0.6.2/24 dev r5-eth0')
    r5.cmd('ip addr add 10.0.7.1/24 dev r5-eth1')
    h2.cmd('ip addr add 10.0.7.2/24 dev h2-eth0')

    # Routing (same logic as topo.py, wireless interface names).
    h1.cmd('ip route flush table main')
    h1.cmd('ip route add 10.0.0.0/24 dev h1-wlan0 scope link')
    h1.cmd('ip route add default via 10.0.0.2 dev h1-wlan0')

    r1.cmd('ip rule add from 10.0.0.2 table 1')
    r1.cmd('ip rule add from 10.0.1.1 table 2')
    r1.cmd('ip rule add from 10.0.2.1 table 3')
    r1.cmd('ip route add 10.0.0.0/24 dev r1-wlan2 scope link table 1')
    r1.cmd('ip route add default via 10.0.0.1 dev r1-wlan2 table 1')
    r1.cmd('ip route add 10.0.1.0/24 dev r1-wlan0 scope link table 2')
    r1.cmd('ip route add default via 10.0.1.2 dev r1-wlan0 table 2')
    r1.cmd('ip route add 10.0.2.0/24 dev r1-wlan1 scope link table 3')
    r1.cmd('ip route add default via 10.0.2.2 dev r1-wlan1 table 3')
    r1.cmd('ip route flush table main')
    r1.cmd('ip route add 10.0.0.0/24 dev r1-wlan2 scope link')
    r1.cmd('ip route add 10.0.1.0/24 dev r1-wlan0 scope link')
    r1.cmd('ip route add 10.0.2.0/24 dev r1-wlan1 scope link')
    r1.cmd('ip route add 10.0.4.0/24 via 10.0.1.2 dev r1-wlan0')
    r1.cmd('ip route add 10.0.5.0/24 via 10.0.2.2 dev r1-wlan1')
    r1.cmd('ip route add default via 10.0.1.2 dev r1-wlan0')

    r2.cmd('ip route flush table main')
    r2.cmd('ip route add 10.0.1.0/24 dev r2-wlan0 scope link')
    r2.cmd('ip route add 10.0.4.0/24 dev r2-eth1 scope link')
    r2.cmd('ip route add 10.0.0.0/24 via 10.0.1.1 dev r2-wlan0')
    r2.cmd('ip route add 10.0.2.0/24 via 10.0.1.1 dev r2-wlan0')
    r2.cmd('ip route add default via 10.0.4.2 dev r2-eth1')

    r3.cmd('ip route flush table main')
    r3.cmd('ip route add 10.0.2.0/24 dev r3-wlan0 scope link')
    r3.cmd('ip route add 10.0.5.0/24 dev r3-eth1 scope link')
    r3.cmd('ip route add 10.0.0.0/24 via 10.0.2.1 dev r3-wlan0')
    r3.cmd('ip route add 10.0.1.0/24 via 10.0.2.1 dev r3-wlan0')
    r3.cmd('ip route add default via 10.0.5.2 dev r3-eth1')

    r4.cmd('ip route add 10.0.0.0/24 via 10.0.4.1 dev r4-eth0')
    r4.cmd('ip route add 10.0.1.0/24 via 10.0.4.1 dev r4-eth0')
    r4.cmd('ip route add 10.0.2.0/24 via 10.0.5.1 dev r4-eth1')
    r4.cmd('ip route add default via 10.0.6.2 dev r4-eth2')
    r5.cmd('ip route add default via 10.0.6.1 dev r5-eth0')
    h2.cmd('ip route add default via 10.0.7.1 dev h2-eth0')

    # Propagation delay on the WIRED side only (netem's 1000-packet FIFO in
    # front of a Wi-Fi bottleneck caused 200-500 ms bufferbloat): uplink on
    # r2-eth1/r3-eth1, downlink on r4-eth0/r4-eth1. Wi-Fi interfaces keep the
    # default mac80211 queueing. With cake, cake is attached as netem's child
    # so the downlink queue (and its AQM) stays in cake.
    cake = 'cake bandwidth {:g}mbit besteffort flowblind'
    for up_node, up_intf, down_intf, delay, bw in (
            (r2, 'r2-eth1', 'r4-eth0', args.delay1, args.bw1),
            (r3, 'r3-eth1', 'r4-eth1', args.delay2, args.bw2)):
        netem = 'netem delay {:g}ms limit 100000'.format(delay)
        up_node.cmd('tc qdisc replace dev {} root handle 1: {}'.format(up_intf, netem))
        r4.cmd('tc qdisc replace dev {} root handle 1: {}'.format(down_intf, netem))
        if not args.native_bw:
            out = r4.cmd('tc qdisc add dev {} parent 1:1 handle 10: {}'.format(
                down_intf, cake.format(bw)))
            if out.strip():
                warn('*** cake under netem on {} failed: {}\n'.format(down_intf, out.strip()))
    info('*** one-way delay: path1 {:g} ms, path2 {:g} ms (wired side)\n'.format(
        args.delay1, args.delay2))

    if not args.native_bw:
        r1.cmd('tc qdisc replace dev r1-wlan0 root ' + cake.format(args.bw1))
        r1.cmd('tc qdisc replace dev r1-wlan1 root ' + cake.format(args.bw2))
        info('*** cake: path1 {:g} Mbit, path2 {:g} Mbit\n'.format(args.bw1, args.bw2))

    CLI(net)

    for node, pid in watchdogs:
        if pid.isdigit():
            node.cmd('kill {} 2>/dev/null'.format(pid))
    r1.cmd('test -f {0} && kill $(cat {0}) 2>/dev/null'.format(ap_pid))
    net.stop()


if __name__ == '__main__':
    sys.exit(main())
