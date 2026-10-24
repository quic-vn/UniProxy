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
endpoints, so Wi-Fi only adds MAC-layer behaviour (contention, link-layer
retransmission, aggregation, rate adaptation). Use --native-bw to remove the
shapers and let the wireless links be the bottleneck.
"""

import argparse
import sys
import time

from mininet.link import TCLink
from mininet.log import setLogLevel, info, warn
from mn_wifi.cli import CLI
from mn_wifi.net import Mininet_wifi


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--mode', default='n',
                   help="802.11 mode of both APs (g, a, n, ac); default n")
    p.add_argument('--ch1', default='36', help='channel of ap1 / path 1')
    p.add_argument('--ch2', default='149', help='channel of ap2 / path 2')
    p.add_argument('--ht-capab', default='[HT40+]',
                   help="hostapd ht_capab for n/ac modes; '' to disable")
    p.add_argument('--wbw', type=float, default=1000,
                   help='tc rate (Mbit) mn_wifi puts on the wireless links; '
                        'large default so it never becomes the bottleneck')
    p.add_argument('--native-bw', action='store_true',
                   help='do not add the 100/50 Mbit cake shapers')
    p.add_argument('--assoc-timeout', type=float, default=15,
                   help='seconds to wait for both radios to associate')
    args, _ = p.parse_known_args()
    return args


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
    net = Mininet_wifi()

    ap_params = {}
    if args.ht_capab and args.mode in ('n', 'ac'):
        ap_params['ht_capab'] = args.ht_capab

    h1 = net.addHost('h1')  # client
    h2 = net.addHost('h2')  # server
    r1 = net.addStation('r1', wlans=2)  # client-side proxy
    r2 = net.addHost('r2')
    r3 = net.addHost('r3')
    r4 = net.addHost('r4')
    r5 = net.addHost('r5')  # server-side proxy
    ap1 = net.addAccessPoint('ap1', ssid='path1', mode=args.mode,
                             channel=args.ch1, failMode='standalone', **ap_params)
    ap2 = net.addAccessPoint('ap2', ssid='path2', mode=args.mode,
                             channel=args.ch2, failMode='standalone', **ap_params)

    net.configureWifiNodes()  # name available in both Mininet-WiFi 2.6 and 2.7

    # Wired links; interface names are pinned so they match topo.py.
    net.addLink(h1, r1, cls=TCLink, intfName1='h1-eth0', intfName2='r1-eth0')
    net.addLink(ap1, r2, cls=TCLink, intfName2='r2-eth0')
    net.addLink(ap2, r3, cls=TCLink, intfName2='r3-eth0')
    net.addLink(r2, r4, cls=TCLink, intfName1='r2-eth1', intfName2='r4-eth0')
    net.addLink(r3, r4, cls=TCLink, intfName1='r3-eth1', intfName2='r4-eth1')
    net.addLink(r4, r5, cls=TCLink, intfName1='r4-eth2', intfName2='r5-eth0')
    net.addLink(r5, h2, cls=TCLink, intfName1='r5-eth1', intfName2='h2-eth0')

    # Wireless links: port1 selects the station radio, port2 the AP radio.
    net.addLink(r1, ap1, port1=0, port2=0, bw=args.wbw)
    net.addLink(r1, ap2, port1=1, port2=0, bw=args.wbw)

    net.build()
    ap1.start([])
    ap2.start([])

    radios = {
        'r1-wlan0': ('path1', ap1.wintfs[0].mac),
        'r1-wlan1': ('path2', ap2.wintfs[0].mac),
    }
    wait_associated(r1, radios, args.assoc_timeout)

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
        # `replace` because mn_wifi already installed a root qdisc on r1-wlanX.
        r1.cmd('tc qdisc replace dev r1-wlan0 root cake bandwidth 100mbit besteffort flowblind')
        r4.cmd('tc qdisc add dev r4-eth0 root cake bandwidth 100mbit besteffort flowblind')
        r1.cmd('tc qdisc replace dev r1-wlan1 root cake bandwidth 50mbit besteffort flowblind')
        r4.cmd('tc qdisc add dev r4-eth1 root cake bandwidth 50mbit besteffort flowblind')

    CLI(net)
    net.stop()


if __name__ == '__main__':
    sys.exit(main())
