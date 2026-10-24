#!/usr/bin/env python3
"""Mininet-WiFi variant of topo.py: both paths use a wireless first hop.

    h1 --eth-- r1 (station, 2 radios)
               r1-wlan0 ~~ ap1 (ssid path1, ch1) --eth-- r2 --+
               r1-wlan1 ~~ ap2 (ssid path2, ch2) --eth-- r3 --+-- r4 -- r5 -- h2

Addresses, node names and routing are identical to topo.py, except that
r1-eth1/r1-eth2 are replaced by r1-wlan0/r1-wlan1. r1-eth0 is kept so the
TPROXY rule in client.sh keeps working. ap1/ap2 are standalone L2 bridges, so
r1-wlan0 <-> r2-eth0 share 10.0.1.0/24 and r1-wlan1 <-> r3-eth0 share 10.0.2.0/24.

By default the cake shapers (100/50 Mbit) of topo.py are kept on the path
endpoints. Use --native-bw to remove the shapers and let the wireless links be
the bottleneck.

Two channel models:
- default ("ideal"): plain mac80211_hwsim, no wmediumd. Frames are never lost
  and the PHY rate does not limit throughput, so results match the wired topo.
- --wmediumd: wmediumd in interference mode with node positions and the
  logDistance propagation model. r1 is at the origin, ap1 is --dist1 metres
  away and ap2 --dist2 metres away, so each path's SNR (hence PHY rate, MAC
  retransmissions and delay jitter) is set by its distance. The two paths are
  put in separate wmediumd mediums unless --shared-medium is given.

Written against Mininet-WiFi 2.7.
"""

import argparse
import sys
import time

from mininet.link import TCLink
from mininet.log import setLogLevel, info, warn
from mn_wifi.cli import CLI
from mn_wifi.link import wmediumd
from mn_wifi.net import Mininet_wifi
from mn_wifi.wmediumdConnector import interference

# r1 position (metres); ap1 is placed on +x, ap2 on +y.
R1_POS = (100.0, 100.0, 0.0)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--mode', default=None,
                   help="802.11 mode of both APs (g, a, n, ac); default n, "
                        "or a with --wmediumd (HT modes fail under wmediumd "
                        "on Mininet-WiFi 2.6: ARP/association breaks)")
    p.add_argument('--ch1', default='36', help='channel of ap1 / path 1')
    p.add_argument('--ch2', default='149', help='channel of ap2 / path 2')
    p.add_argument('--ht-capab', default='[HT40+]',
                   help="hostapd ht_capab for n/ac modes; '' to disable")
    p.add_argument('--wbw', type=float, default=1000,
                   help='tc rate (Mbit) mn_wifi puts on the wireless links; '
                        'large default so it never becomes the bottleneck')
    p.add_argument('--native-bw', action='store_true',
                   help='do not add the cake shapers')
    p.add_argument('--bw1', type=float, default=100,
                   help='cake rate (Mbit) of path 1; default 100')
    p.add_argument('--bw2', type=float, default=50,
                   help='cake rate (Mbit) of path 2; default 50')
    p.add_argument('--assoc-timeout', type=float, default=15,
                   help='seconds to wait for both radios to associate')
    w = p.add_argument_group('wmediumd channel model')
    w.add_argument('--wmediumd', action='store_true',
                   help='use wmediumd interference mode with positions')
    w.add_argument('--dist1', type=float, default=10,
                   help='r1-ap1 distance in metres (path 1); default 10')
    w.add_argument('--dist2', type=float, default=10,
                   help='r1-ap2 distance in metres (path 2); default 10')
    w.add_argument('--exp', type=float, default=3.0,
                   help='logDistance path-loss exponent (2 = free space); default 3')
    w.add_argument('--fading', type=float, default=0,
                   help='wmediumd fading coefficient (0 = no fading); default 0')
    w.add_argument('--noise-th', type=float, default=-91,
                   help='wmediumd noise threshold in dBm; default -91')
    w.add_argument('--txpower', type=float, default=None,
                   help='tx power (dBm) of r1 radios and APs; default mn_wifi value')
    w.add_argument('--shared-medium', action='store_true',
                   help='put both paths in one wmediumd medium (they interfere)')
    args, _ = p.parse_known_args()
    if args.mode is None:
        args.mode = 'a' if args.wmediumd else 'n'
    return args


def pos_str(pos):
    return ','.join('{:g}'.format(c) for c in pos)


def wait_associated(r1, radios, timeout):
    """Retry until every radio is associated to its *own* SSID.

    Checking the SSID (not only 'Connected to') keeps the radio->AP mapping
    correct even if a Mininet-WiFi version maps addLink ports differently.
    """
    deadline = time.time() + timeout
    pending = dict(radios)
    while pending and time.time() < deadline:
        for intf, (ssid, _) in list(pending.items()):
            link = r1.cmd('iw dev {} link'.format(intf))
            if 'Connected to' in link and 'SSID: {}'.format(ssid) in link:
                info('*** {} associated to {}\n'.format(intf, ssid))
                del pending[intf]
            elif 'Connected to' in link:
                r1.cmd('iw dev {} disconnect'.format(intf))
        if pending:
            time.sleep(1)
            for intf, (ssid, bssid) in pending.items():
                r1.cmd('iw dev {} connect {} {}'.format(intf, ssid, bssid))
    for intf, (ssid, _) in pending.items():
        warn('*** {} did NOT associate to {}\n'.format(intf, ssid))
    return not pending


def main():
    args = parse_args()
    setLogLevel('info')
    if args.wmediumd:
        net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference,
                           noise_th=args.noise_th, fading_cof=args.fading)
    else:
        net = Mininet_wifi()

    ap_params, ap1_extra, ap2_extra, sta_params = {}, {}, {}, {}
    if args.ht_capab and args.mode in ('n', 'ac'):
        ap_params['ht_capab'] = args.ht_capab
    if args.wmediumd:
        x, y, z = R1_POS
        sta_params['position'] = pos_str(R1_POS)
        ap1_extra['position'] = pos_str((x + args.dist1, y, z))
        ap2_extra['position'] = pos_str((x, y + args.dist2, z))
        if args.txpower is not None:
            sta_params['txpower'] = args.txpower
            ap_params['txpower'] = args.txpower

    h1 = net.addHost('h1')  # client
    h2 = net.addHost('h2')  # server
    r1 = net.addStation('r1', wlans=2, **sta_params)  # client-side proxy
    r2 = net.addHost('r2')
    r3 = net.addHost('r3')
    r4 = net.addHost('r4')
    r5 = net.addHost('r5')  # server-side proxy
    ap1 = net.addAccessPoint('ap1', ssid='path1', mode=args.mode, channel=args.ch1,
                             failMode='standalone', **dict(ap_params, **ap1_extra))
    ap2 = net.addAccessPoint('ap2', ssid='path2', mode=args.mode, channel=args.ch2,
                             failMode='standalone', **dict(ap_params, **ap2_extra))

    if args.wmediumd:
        net.setPropagationModel(model='logDistance', exp=args.exp)
        if not args.shared_medium:
            # One medium per path, so the two paths do not share airtime.
            net.setInitialMediums((('r1-wlan0', ap1), ('r1-wlan1', ap2)))

    net.configureWifiNodes()  # name available in both Mininet-WiFi 2.6 and 2.7

    # Wired links; interface names are pinned so they match topo.py.
    net.addLink(h1, r1, cls=TCLink, intfName1='h1-eth0', intfName2='r1-eth0')
    net.addLink(ap1, r2, cls=TCLink, intfName2='r2-eth0')
    net.addLink(ap2, r3, cls=TCLink, intfName2='r3-eth0')
    net.addLink(r2, r4, cls=TCLink, intfName1='r2-eth1', intfName2='r4-eth0')
    net.addLink(r3, r4, cls=TCLink, intfName1='r3-eth1', intfName2='r4-eth1')
    net.addLink(r4, r5, cls=TCLink, intfName1='r4-eth2', intfName2='r5-eth0')
    net.addLink(r5, h2, cls=TCLink, intfName1='r5-eth1', intfName2='h2-eth0')

    if not args.wmediumd:
        # Wireless links: port1 selects the station radio, port2 the AP radio.
        net.addLink(r1, ap1, port1=0, port2=0, bw=args.wbw)
        net.addLink(r1, ap2, port1=1, port2=0, bw=args.wbw)
    # With wmediumd, net.build() associates each radio to the first AP in
    # range not yet used by the other radio (mn_wifi auto association);
    # wait_associated() below corrects the mapping if needed.

    net.build()
    ap1.start([])
    ap2.start([])

    if args.wmediumd:
        info('*** wmediumd interference: mode={} exp={} fading={} noise_th={} medium={}\n'.format(
            args.mode, args.exp, args.fading, args.noise_th,
            'shared' if args.shared_medium else 'per-path'))
        if args.mode in ('n', 'ac'):
            warn('*** HT/VHT mode with wmediumd failed on Mininet-WiFi 2.6 '
                 '(ARP/association breaks); use --mode a\n')
        for ap, d in ((ap1, args.dist1), (ap2, args.dist2)):
            rng = getattr(ap.wintfs[0], 'range', None)
            info('*** {}: distance {:g} m, range {} m\n'.format(ap.name, d, rng))
            if rng and d > rng:
                warn('*** {} is out of range; the radio will not associate\n'.format(ap.name))

    radios = {
        'r1-wlan0': ('path1', ap1.wintfs[0].mac),
        'r1-wlan1': ('path2', ap2.wintfs[0].mac),
    }
    wait_associated(r1, radios, args.assoc_timeout)
    for intf in radios:
        sig = [l.strip() for l in r1.cmd('iw dev {} link'.format(intf)).splitlines()
               if 'signal' in l or 'tx bitrate' in l]
        info('*** {}: {}\n'.format(intf, '; '.join(sig) or 'not connected'))

    for node, intfs in ((h1, ['h1-eth0']),
                        (r1, ['r1-eth0', 'r1-wlan0', 'r1-wlan1']),
                        (r2, ['r2-eth0', 'r2-eth1']),
                        (r3, ['r3-eth0', 'r3-eth1']),
                        (r4, ['r4-eth0', 'r4-eth1', 'r4-eth2']),
                        (r5, ['r5-eth0', 'r5-eth1']),
                        (h2, ['h2-eth0'])):
        for intf in intfs:
            node.cmd('ip addr flush dev {}'.format(intf))

    for r in (r1, r2, r3, r4, r5):
        r.cmd('echo 1 > /proc/sys/net/ipv4/ip_forward')

    h1.cmd('ip addr add 10.0.0.1/24 dev h1-eth0')

    r1.cmd('ip addr add 10.0.0.2/24 dev r1-eth0')
    r1.cmd('ip addr add 10.0.1.1/24 dev r1-wlan0')
    r1.cmd('ip addr add 10.0.2.1/24 dev r1-wlan1')

    r2.cmd('ip addr add 10.0.1.2/24 dev r2-eth0')
    r2.cmd('ip addr add 10.0.4.1/24 dev r2-eth1')

    r3.cmd('ip addr add 10.0.2.2/24 dev r3-eth0')
    r3.cmd('ip addr add 10.0.5.1/24 dev r3-eth1')

    r4.cmd('ip addr add 10.0.4.2/24 dev r4-eth0')
    r4.cmd('ip addr add 10.0.5.2/24 dev r4-eth1')
    r4.cmd('ip addr add 10.0.6.1/24 dev r4-eth2')

    r5.cmd('ip addr add 10.0.6.2/24 dev r5-eth0')
    r5.cmd('ip addr add 10.0.7.1/24 dev r5-eth1')

    h2.cmd('ip addr add 10.0.7.2/24 dev h2-eth0')

    h1.cmd('ip route flush table main')
    h1.cmd('ip route add 10.0.0.0/24 dev h1-eth0 scope link')
    h1.cmd('ip route add default via 10.0.0.2 dev h1-eth0')

    r1.cmd('ip rule add from 10.0.0.2 table 1')
    r1.cmd('ip rule add from 10.0.1.1 table 2')
    r1.cmd('ip rule add from 10.0.2.1 table 3')

    r1.cmd('ip route add 10.0.0.0/24 dev r1-eth0 scope link table 1')
    r1.cmd('ip route add default via 10.0.0.1 dev r1-eth0 table 1')

    r1.cmd('ip route add 10.0.1.0/24 dev r1-wlan0 scope link table 2')
    r1.cmd('ip route add default via 10.0.1.2 dev r1-wlan0 table 2')

    r1.cmd('ip route add 10.0.2.0/24 dev r1-wlan1 scope link table 3')
    r1.cmd('ip route add default via 10.0.2.2 dev r1-wlan1 table 3')

    r1.cmd('ip route flush table main')
    r1.cmd('ip route add 10.0.0.0/24 dev r1-eth0 scope link')
    r1.cmd('ip route add 10.0.1.0/24 dev r1-wlan0 scope link')
    r1.cmd('ip route add 10.0.2.0/24 dev r1-wlan1 scope link')
    r1.cmd('ip route add 10.0.4.0/24 via 10.0.1.2 dev r1-wlan0')
    r1.cmd('ip route add 10.0.5.0/24 via 10.0.2.2 dev r1-wlan1')
    r1.cmd('ip route add default via 10.0.1.2 dev r1-wlan0')

    r2.cmd('ip route add 10.0.0.0/24 via 10.0.1.1 dev r2-eth0')
    r2.cmd('ip route add 10.0.2.0/24 via 10.0.1.1 dev r2-eth0')
    r2.cmd('ip route add 10.0.3.0/24 via 10.0.1.1 dev r2-eth0')
    r2.cmd('ip route add 10.0.5.0/24 via 10.0.4.2 dev r2-eth1')
    r2.cmd('ip route add 10.0.6.0/24 via 10.0.4.2 dev r2-eth1')
    r2.cmd('ip route add 10.0.7.0/24 via 10.0.4.2 dev r2-eth1')
    r2.cmd('ip route add default via 10.0.4.2 dev r2-eth1')

    r3.cmd('ip route add 10.0.0.0/24 via 10.0.2.1 dev r3-eth0')
    r3.cmd('ip route add 10.0.1.0/24 via 10.0.2.1 dev r3-eth0')
    r3.cmd('ip route add 10.0.3.0/24 via 10.0.2.1 dev r3-eth0')
    r3.cmd('ip route add 10.0.4.0/24 via 10.0.5.2 dev r3-eth1')
    r3.cmd('ip route add 10.0.6.0/24 via 10.0.5.2 dev r3-eth1')
    r3.cmd('ip route add 10.0.7.0/24 via 10.0.5.2 dev r3-eth1')
    r3.cmd('ip route add default via 10.0.5.2 dev r3-eth1')

    r5.cmd('ip route add default via 10.0.6.1 dev r5-eth0')

    r4.cmd('ip route add 10.0.0.0/24 via 10.0.4.1 dev r4-eth0')
    r4.cmd('ip route add 10.0.1.0/24 via 10.0.4.1 dev r4-eth0')
    r4.cmd('ip route add 10.0.2.0/24 via 10.0.5.1 dev r4-eth1')
    r4.cmd('ip route add default via 10.0.6.2 dev r4-eth2')

    h2.cmd('ip route add default via 10.0.7.1 dev h2-eth0')

    # Base propagation delay, same as topo.py (runners override r2/r3 with
    # `tc qdisc replace`).
    r2.cmd('tc qdisc add dev r2-eth0 root handle 1: netem delay 5ms')
    r2.cmd('tc qdisc add dev r2-eth1 root handle 1: netem delay 5ms')
    r3.cmd('tc qdisc add dev r3-eth0 root handle 1: netem delay 5ms')
    r3.cmd('tc qdisc add dev r3-eth1 root handle 1: netem delay 5ms')

    if not args.native_bw:
        # `replace` because mn_wifi may already have a root qdisc on r1-wlanX.
        cake = 'root cake bandwidth {:g}mbit besteffort flowblind'
        r1.cmd('tc qdisc replace dev r1-wlan0 ' + cake.format(args.bw1))
        r4.cmd('tc qdisc add dev r4-eth0 ' + cake.format(args.bw1))
        r1.cmd('tc qdisc replace dev r1-wlan1 ' + cake.format(args.bw2))
        r4.cmd('tc qdisc add dev r4-eth1 ' + cake.format(args.bw2))
        info('*** cake: path1 {:g} Mbit, path2 {:g} Mbit\n'.format(args.bw1, args.bw2))

    CLI(net)
    net.stop()


if __name__ == '__main__':
    sys.exit(main())
