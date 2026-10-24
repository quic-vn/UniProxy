#!/usr/bin/env python3
"""Thi nghiem tay UniProxy TRR (+ co `unreliable` cua FQSAT) tren topo cua topo.py.

Topo: path 1 qua r2 (cake --bw1, netem --base-delay1 moi chieu), path 2 qua r3 (cake --bw2,
netem --base-delay2 moi chieu). RTT goc cua path i = 2 x base-delay-i.

--workload long (mac dinh): example/client_long.py --conns N --duration D (bulk, tung giay).
    Tai t0+impair-at: path --impair-path bi netem delay/jitter/loss; tai t0+restore-at tro ve base.
--workload fct: example/client_fct.py (short flow, Poisson, kich thuoc 10kB/100kB/1MB) +
    tuy chon --bg-conns luong bulk nen (client_long). Suy giam duoc ap TRUOC khi tai bat dau va
    giu den het (suy giam keo dai, dung tinh huong Discussion cua UniProxy); impair-at/restore-at
    khong dung.
Gia tri 0 (0ms, 0%) cua --jitter/--loss thi bo menh de do khoi lenh netem.

Ket qua trong --out (mac dinh results/fqsat_manual/<stamp>/): config.json, events.txt, summary.txt,
r5_server.log, r1_client.log, h1_*.log, rate_1s.csv/throughput.csv (long), fct.csv (fct).

Vi du:
  sudo python3 experiments/fqsat_manual_test.py                                   # nhu truoc
  sudo python3 experiments/fqsat_manual_test.py --impair-path 2 --delay 20ms --jitter 30ms --loss 0%
  sudo python3 experiments/fqsat_manual_test.py --workload fct --impair-path 1 --base-delay2 20ms \
       --delay 5ms --jitter 0ms --loss 2% --bg-conns 2
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time

from mininet.link import TCLink
from mininet.log import info, setLogLevel
from mininet.net import Mininet

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
SERVER_BIN = os.path.join(REPO, 'quic-tun', 'server', 'cmd', 'cmd')
CLIENT_BIN = os.path.join(REPO, 'quic-tun', 'client', 'cmd', 'cmd')


def build_topo(net, bw1=100, bw2=50, d1='5ms', d2='5ms'):
    """Copy nguyen van phan dung topo cua topo.py (bo CLI)."""
    h1 = net.addHost('h1')  # client
    h2 = net.addHost('h2')  # server
    r1 = net.addHost('r1')  # client-side proxy
    r2 = net.addHost('r2')
    r3 = net.addHost('r3')
    r4 = net.addHost('r4')
    r5 = net.addHost('r5')  # server-side proxy

    net.addLink(h1, r1)
    net.addLink(r1, r2)
    net.addLink(r1, r3)
    net.addLink(r2, r4)
    net.addLink(r3, r4)
    net.addLink(r4, r5)
    net.addLink(r5, h2)
    net.build()

    h1.cmd("ifconfig h1-eth0 0")
    for h, n in ((r1, 3), (r2, 2), (r3, 2), (r4, 3), (r5, 2)):
        for i in range(n):
            h.cmd("ifconfig %s-eth%d 0" % (h.name, i))
    h2.cmd("ifconfig h2-eth0 0")

    for r in (r1, r2, r3, r4, r5):
        r.cmd("echo 1 > /proc/sys/net/ipv4/ip_forward")

    h1.cmd("ifconfig h1-eth0 10.0.0.1 netmask 255.255.255.0")
    r1.cmd("ifconfig r1-eth0 10.0.0.2 netmask 255.255.255.0")
    r1.cmd("ifconfig r1-eth1 10.0.1.1 netmask 255.255.255.0")
    r1.cmd("ifconfig r1-eth2 10.0.2.1 netmask 255.255.255.0")
    r2.cmd("ifconfig r2-eth0 10.0.1.2 netmask 255.255.255.0")
    r2.cmd("ifconfig r2-eth1 10.0.4.1 netmask 255.255.255.0")
    r3.cmd("ifconfig r3-eth0 10.0.2.2 netmask 255.255.255.0")
    r3.cmd("ifconfig r3-eth1 10.0.5.1 netmask 255.255.255.0")
    r4.cmd("ifconfig r4-eth0 10.0.4.2 netmask 255.255.255.0")
    r4.cmd("ifconfig r4-eth1 10.0.5.2 netmask 255.255.255.0")
    r4.cmd("ifconfig r4-eth2 10.0.6.1 netmask 255.255.255.0")
    r5.cmd("ifconfig r5-eth0 10.0.6.2 netmask 255.255.255.0")
    r5.cmd("ifconfig r5-eth1 10.0.7.1 netmask 255.255.255.0")
    h2.cmd("ifconfig h2-eth0 10.0.7.2 netmask 255.255.255.0")

    h1.cmd("ip route flush table main")
    h1.cmd("ip route add 10.0.0.0/24 dev h1-eth0 scope link")
    h1.cmd("ip route add default via 10.0.0.2 dev h1-eth0")

    r1.cmd("ip rule add from 10.0.0.2 table 1")
    r1.cmd("ip rule add from 10.0.1.1 table 2")
    r1.cmd("ip rule add from 10.0.2.1 table 3")
    r1.cmd("ip route add 10.0.0.0/24 dev r1-eth0 scope link table 1")
    r1.cmd("ip route add default via 10.0.0.1 dev r1-eth0 table 1")
    r1.cmd("ip route add 10.0.1.0/24 dev r1-eth1 scope link table 2")
    r1.cmd("ip route add default via 10.0.1.2 dev r1-eth1 table 2")
    r1.cmd("ip route add 10.0.2.0/24 dev r1-eth2 scope link table 3")
    r1.cmd("ip route add default via 10.0.2.2 dev r1-eth2 table 3")
    r1.cmd("ip route add 10.0.0.0/24 dev r1-eth0 scope link")
    r1.cmd("ip route add 10.0.1.0/24 dev r1-eth1 scope link")
    r1.cmd("ip route add 10.0.2.0/24 dev r1-eth2 scope link")
    r1.cmd("ip route add 10.0.4.0/24 via 10.0.1.2 dev r1-eth1")
    r1.cmd("ip route add 10.0.5.0/24 via 10.0.2.2 dev r1-eth2")
    r1.cmd("ip route add default via 10.0.1.2 dev r1-eth1")

    r2.cmd("ip route add 10.0.1.0/24 dev r2-eth0 scope link")
    r2.cmd("ip route add 10.0.4.0/24 dev r2-eth1 scope link")
    r2.cmd("ip route add 10.0.0.0/24 via 10.0.1.1 dev r2-eth0")
    r2.cmd("ip route add 10.0.2.0/24 via 10.0.1.1 dev r2-eth0")
    r2.cmd("ip route add 10.0.3.0/24 via 10.0.1.1 dev r2-eth0")
    r2.cmd("ip route add 10.0.5.0/24 via 10.0.4.2 dev r2-eth1")
    r2.cmd("ip route add 10.0.6.0/24 via 10.0.4.2 dev r2-eth1")
    r2.cmd("ip route add 10.0.7.0/24 via 10.0.4.2 dev r2-eth1")
    r2.cmd("ip route add default via 10.0.4.2 dev r2-eth1")

    r3.cmd("ip route add 10.0.2.0/24 dev r3-eth0 scope link")
    r3.cmd("ip route add 10.0.5.0/24 dev r3-eth1 scope link")
    r3.cmd("ip route add 10.0.0.0/24 via 10.0.2.1 dev r3-eth0")
    r3.cmd("ip route add 10.0.1.0/24 via 10.0.2.1 dev r3-eth0")
    r3.cmd("ip route add 10.0.3.0/24 via 10.0.2.1 dev r3-eth0")
    r3.cmd("ip route add 10.0.4.0/24 via 10.0.5.2 dev r3-eth1")
    r3.cmd("ip route add 10.0.6.0/24 via 10.0.5.2 dev r3-eth1")
    r3.cmd("ip route add 10.0.7.0/24 via 10.0.5.2 dev r3-eth1")
    r3.cmd("ip route add default via 10.0.5.2 dev r3-eth1")

    r5.cmd("ip route add 10.0.6.0/24 dev r5-eth0 scope link")
    r5.cmd("ip route add 10.0.7.0/24 dev r5-eth1 scope link")
    r5.cmd("ip route add default via 10.0.6.1 dev r5-eth0")

    r4.cmd("ip route add 10.0.4.0/24 dev r4-eth0 scope link")
    r4.cmd("ip route add 10.0.5.0/24 dev r4-eth1 scope link")
    r4.cmd("ip route add 10.0.6.0/24 dev r4-eth2 scope link")
    r4.cmd("ip route add 10.0.0.0/24 via 10.0.4.1 dev r4-eth0")
    r4.cmd("ip route add 10.0.1.0/24 via 10.0.4.1 dev r4-eth0")
    r4.cmd("ip route add 10.0.2.0/24 via 10.0.5.1 dev r4-eth1")
    r4.cmd("ip route add default via 10.0.6.2 dev r4-eth2")

    h2.cmd("ip route add 10.0.7.0/24 dev h2-eth0 scope link")
    h2.cmd("ip route add default via 10.0.7.1 dev h2-eth0")

    r2.cmd("tc qdisc add dev r2-eth0 root handle 1: netem delay %s" % d1)
    r2.cmd("tc qdisc add dev r2-eth1 root handle 1: netem delay %s" % d1)
    r1.cmd("tc qdisc add dev r1-eth1 root cake bandwidth %gmbit besteffort flowblind" % bw1)
    r4.cmd("tc qdisc add dev r4-eth0 root cake bandwidth %gmbit besteffort flowblind" % bw1)
    r3.cmd("tc qdisc add dev r3-eth0 root handle 1: netem delay %s" % d2)
    r3.cmd("tc qdisc add dev r3-eth1 root handle 1: netem delay %s" % d2)
    r1.cmd("tc qdisc add dev r1-eth2 root cake bandwidth %gmbit besteffort flowblind" % bw2)
    r4.cmd("tc qdisc add dev r4-eth1 root cake bandwidth %gmbit besteffort flowblind" % bw2)
    return dict(h1=h1, h2=h2, r1=r1, r2=r2, r3=r3, r4=r4, r5=r5)


def event(out, msg):
    line = "%.3f %s" % (time.time(), msg)
    info("*** %s\n" % line)
    with open(os.path.join(out, 'events.txt'), 'a') as f:
        f.write(line + "\n")


def wait_file(path, timeout):
    t_end = time.time() + timeout
    while time.time() < t_end:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return True
        time.sleep(0.5)
    return False


def is_zero(v):
    return v is None or re.fullmatch(r'\s*0+(\.0*)?\s*(ms|us|s|%)?\s*', str(v)) is not None


def netem_args(delay, jitter, loss):
    a = 'delay %s' % delay
    if not is_zero(jitter):
        a += ' %s' % jitter
    if not is_zero(loss):
        a += ' loss %s' % loss
    return a


def read_rwnd():
    rw = {}
    for f, names in (("protocol.go", ["InitialConnectionFlowControlWindow"]),
                     ("server_parameters.go", ["ReceiveConnectionFlowControlWindow",
                      "DefaultMaxReceiveConnectionFlowControlWindowServer",
                      "DefaultMaxReceiveConnectionFlowControlWindowClient"])):
        txt = open(os.path.join(REPO, "mp-quic", "internal", "protocol", f)).read()
        for n in names:
            m = re.search(r"const\s+%s\b[^=]*=\s*(\d+)" % n, txt)
            rw[n] = int(m.group(1)) if m else None
    return rw


def read_fq_enable():
    txt = open(os.path.join(REPO, "mp-quic", "scheduler.go")).read()
    m = re.search(r"^\s*FQ_ENABLE\s*=\s*(true|false)", txt, re.M)
    return m.group(1) if m else None


def wait_pid(node, pid, timeout):
    t_end = time.time() + timeout
    while time.time() < t_end and node.cmd('kill -0 %s 2>/dev/null && echo alive' % pid).strip() == 'alive':
        time.sleep(1)
    return time.time() < t_end


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return float('nan')
    k = (len(xs) - 1) * q
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def fct_summary(path):
    rows = list(csv.DictReader(open(path)))
    out = ["fct.csv: %d flow" % len(rows)]
    by = {}
    for r in rows:
        by.setdefault(int(r['size_bytes']), []).append(r)
    out.append("   %-9s %5s %5s %9s %9s %9s %9s" % ("size", "ok", "fail", "mean_ms", "p50_ms", "p95_ms", "p99_ms"))
    allok = []
    for size in sorted(by):
        ok = [float(r['fct_s']) * 1000 for r in by[size] if r['status'] == 'ok']
        allok += ok
        fail = len(by[size]) - len(ok)
        mean = sum(ok) / len(ok) if ok else float('nan')
        out.append("   %-9d %5d %5d %9.1f %9.1f %9.1f %9.1f" % (size, len(ok), fail, mean, pct(ok, .5), pct(ok, .95), pct(ok, .99)))
    if allok:
        out.append("   %-9s %5d %5s %9.1f %9.1f %9.1f %9.1f" % ("all", len(allok), "", sum(allok) / len(allok),
                                                              pct(allok, .5), pct(allok, .95), pct(allok, .99)))
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--workload', choices=('long', 'fct'), default='long')
    p.add_argument('--conns', type=int, default=5, help='long: so ket noi bulk')
    p.add_argument('--duration', type=int, default=90, help='long: giay tai lien tuc (sau 10s warm-up + 3s nghi)')
    p.add_argument('--impair-path', type=int, choices=(1, 2), default=2, help='path bi suy giam: 1 = r2, 2 = r3')
    p.add_argument('--impair-at', type=float, default=20, help='long: giay (tu t0) bat dau suy giam')
    p.add_argument('--restore-at', type=float, default=60, help='long: giay (tu t0) phuc hoi')
    p.add_argument('--delay', default='45ms', help='delay moi chieu cua path bi suy giam khi suy giam')
    p.add_argument('--jitter', default='20ms')
    p.add_argument('--loss', default='2%')
    p.add_argument('--base-delay1', default='5ms', help='delay goc moi chieu path 1 (r2)')
    p.add_argument('--base-delay2', default='5ms', help='delay goc moi chieu path 2 (r3)')
    p.add_argument('--bw1', type=float, default=100, help='Mbit/s path 1 (r2), topo.py = 100')
    p.add_argument('--bw2', type=float, default=50, help='Mbit/s path 2 (r3), topo.py = 50')
    p.add_argument('--bg-conns', type=int, default=0, help='fct: so ket noi bulk nen chay song song')
    p.add_argument('--rounds', type=int, default=10, help='fct: so vong (moi vong round-s giay + 3s nghi)')
    p.add_argument('--round-s', type=float, default=10.0)
    p.add_argument('--lam', type=float, default=10.0, help='fct: flow/s')
    p.add_argument('--sizes', default='10000:0.5,100000:0.35,1000000:0.15')
    p.add_argument('--out', default=None)
    args = p.parse_args()

    if os.geteuid() != 0:
        sys.exit("Can chay bang sudo (Mininet).")
    for b in (SERVER_BIN, CLIENT_BIN):
        if not os.access(b, os.X_OK):
            sys.exit("Thieu binary %s. Build: cd %s && go build" % (b, os.path.dirname(b)))

    out = args.out or os.path.join(REPO, 'results', 'fqsat_manual', time.strftime('%Y%m%dT%H%M%S'))
    os.makedirs(out, exist_ok=True)
    cfg = dict(vars(args), fq_enable=read_fq_enable(), rwnd_bytes=read_rwnd(), out=out)
    json.dump(cfg, open(os.path.join(out, 'config.json'), 'w'), indent=2)

    setLogLevel('info')
    subprocess.call(['mn', '-c'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    net = Mininet(link=TCLink)
    n = build_topo(net, args.bw1, args.bw2, args.base_delay1, args.base_delay2)
    h1, h2, r1, r5 = n['h1'], n['h2'], n['r1'], n['r5']
    imp_node = n['r2'] if args.impair_path == 1 else n['r3']
    imp_devs = ['%s-eth0' % imp_node.name, '%s-eth1' % imp_node.name]
    base_imp = args.base_delay1 if args.impair_path == 1 else args.base_delay2
    imp_netem = netem_args(args.delay, args.jitter, args.loss)

    def impair(tag):
        for dev in imp_devs:
            imp_node.cmd('tc qdisc change dev %s root handle 1: netem %s' % (dev, imp_netem))
        event(out, "IMPAIR path%d (%s): netem %s %s" % (args.impair_path, imp_node.name, imp_netem, tag))
        info(imp_node.cmd('tc qdisc show dev %s' % imp_devs[0]))

    def restore(tag):
        for dev in imp_devs:
            imp_node.cmd('tc qdisc change dev %s root handle 1: netem delay %s' % (dev, base_imp))
        event(out, "RESTORE path%d (%s): netem delay %s %s" % (args.impair_path, imp_node.name, base_imp, tag))

    try:
        event(out, "topo up; workload=%s FQ_ENABLE=%s bw1=%gMbit bw2=%gMbit base_delay1=%s base_delay2=%s "
              "rwnd(bytes)=%s; out=%s" % (args.workload, cfg['fq_enable'], args.bw1, args.bw2,
                                          args.base_delay1, args.base_delay2, cfg['rwnd_bytes'], out))

        h2.cmd('cd %s && python3 example/server.py > %s/h2_server.log 2>&1 &' % (REPO, out))
        r5.cmd('cd %s && QUIC_GO_LOG_LEVEL=info %s --listen-on 10.0.6.2:7500 > %s/r5_server.log 2>&1 &'
               % (REPO, SERVER_BIN, out))
        time.sleep(1.5)
        if ':7500' not in r5.cmd('ss -lun'):
            raise RuntimeError("server proxy r5 khong lang nghe :7500, xem %s/r5_server.log" % out)

        # TPROXY tren r1 (giong client.sh; src_valid_mark=0 nhu runner cua repo)
        r1.cmd('sysctl -w net.ipv4.conf.all.src_valid_mark=0')
        r1.cmd('ip rule add fwmark 1 table 100')
        r1.cmd('ip route add local 0.0.0.0/0 dev lo table 100')
        r1.cmd('iptables -t mangle -A PREROUTING --in-interface r1-eth0 -p tcp -d 10.0.7.0/24 '
               '-j TPROXY --on-port 6500 --on-ip 127.0.0.1 --tproxy-mark 1/1')
        r1.cmd('cd %s && QUIC_GO_LOG_LEVEL=info %s --listen-on tcp:127.0.0.1:6500 '
               '--server-endpoint 10.0.6.2:7500 > %s/r1_client.log 2>&1 &' % (REPO, CLIENT_BIN, out))
        time.sleep(1.5)
        if ':6500' not in r1.cmd('ss -ltn'):
            raise RuntimeError("client proxy r1 khong lang nghe :6500, xem %s/r1_client.log" % out)
        event(out, "proxies up")

        if args.workload == 'long':
            # client_long.py ghi load_start.txt va rate_1s.csv vao --log-dir, throughput.csv vao cwd
            h1.cmd('cd %s && python3 %s/example/client_long.py --conns %d --duration %d --log-dir %s '
                   '> %s/h1_client.log 2>&1 & echo $! > %s/h1.pid'
                   % (out, REPO, args.conns, args.duration, out, out, out))
            if not wait_file(os.path.join(out, 'load_start.txt'), 60):
                raise RuntimeError("client_long khong bat dau tai (khong co load_start.txt), xem %s/h1_client.log" % out)
            t0 = float(open(os.path.join(out, 'load_start.txt')).read().split()[0])
            event(out, "load started t0=%.3f" % t0)
            time.sleep(max(0, t0 + args.impair_at - time.time()))
            impair("(t=+%.1fs)" % (time.time() - t0))
            time.sleep(max(0, t0 + args.restore_at - time.time()))
            restore("(t=+%.1fs)" % (time.time() - t0))
            wait_pid(h1, open(os.path.join(out, 'h1.pid')).read().strip(), args.duration + 30)
            event(out, "workload finished")
        else:
            # Suy giam keo dai: ap truoc khi tai bat dau, giu den het
            impair("(truoc khi tai bat dau, giu den het)")
            fct_total = 13 + args.rounds * (args.round_s + 3.0)
            if args.bg_conns > 0:
                bg = os.path.join(out, 'bg')
                os.makedirs(bg, exist_ok=True)
                h1.cmd('cd %s && python3 %s/example/client_long.py --conns %d --duration %d --log-dir %s '
                       '> %s/h1_bg.log 2>&1 & echo $! > %s/h1_bg.pid'
                       % (bg, REPO, args.bg_conns, int(fct_total + 30), bg, out, out))
                event(out, "background bulk: %d ket noi" % args.bg_conns)
            fd = os.path.join(out, 'fct')
            os.makedirs(fd, exist_ok=True)
            h1.cmd('cd %s && python3 %s/example/client_fct.py --rounds %d --round-s %g --lam %g --sizes %s '
                   '--log-dir %s > %s/h1_fct.log 2>&1 & echo $! > %s/h1_fct.pid'
                   % (fd, REPO, args.rounds, args.round_s, args.lam, args.sizes, out, out, out))
            event(out, "fct workload started: rounds=%d round_s=%g lam=%g" % (args.rounds, args.round_s, args.lam))
            done = wait_pid(h1, open(os.path.join(out, 'h1_fct.pid')).read().strip(), fct_total + 300)
            event(out, "fct workload %s" % ("finished" if done else "TIMEOUT"))
            if args.bg_conns > 0:
                h1.cmd('kill %s 2>/dev/null' % open(os.path.join(out, 'h1_bg.pid')).read().strip())
            restore("(sau khi tai ket thuc)")
    finally:
        r5.cmd('pkill -f "server/cmd/cmd"')
        r1.cmd('pkill -f "client/cmd/cmd"')
        h2.cmd('pkill -f "example/server.py"')
        net.stop()

    # Tom tat
    lines = ["config: workload=%s FQ_ENABLE=%s impair_path=%d netem='%s' base_delay1=%s base_delay2=%s bw=%g/%g"
             % (args.workload, cfg['fq_enable'], args.impair_path, imp_netem, args.base_delay1,
                args.base_delay2, args.bw1, args.bw2)]
    for name in ('r5_server.log', 'r1_client.log'):
        path = os.path.join(out, name)
        if os.path.exists(path):
            with open(path, errors='replace') as f:
                flags = [l.rstrip() for l in f if 'FQSAT:' in l]
            lines.append("%s: %d dong FQSAT" % (name, len(flags)))
            lines.extend("   " + l for l in flags[:40])
    ev = os.path.join(out, 'events.txt')
    if os.path.exists(ev):
        lines.append("events.txt:")
        lines.extend("   " + l.rstrip() for l in open(ev))
    if args.workload == 'long':
        tp = os.path.join(out, 'throughput.csv')
        if os.path.exists(tp):
            lines.append("throughput.csv (Mbit/s theo cua so):")
            lines.extend("   " + l.rstrip() for l in open(tp))
    else:
        fp = os.path.join(out, 'fct.csv')
        lines.extend(fct_summary(fp) if os.path.exists(fp) else ["KHONG co fct.csv, xem h1_fct.log"])
        bgt = os.path.join(out, 'bg', 'throughput.csv')
        if os.path.exists(bgt):
            v = [float(x) for x in open(bgt).read().split()]
            lines.append("bulk nen: trung binh %.1f Mbit/s (%d cua so)" % (sum(v) / len(v), len(v)))
    summary = "\n".join(lines)
    with open(os.path.join(out, 'summary.txt'), 'w') as f:
        f.write(summary + "\n")
    # tra quyen thu muc ket qua cho user goi sudo (tranh loi Permission denied ve sau)
    uid, gid = os.environ.get('SUDO_UID'), os.environ.get('SUDO_GID')
    if uid and gid:
        subprocess.call(['chown', '-R', '%s:%s' % (uid, gid), os.path.dirname(out)])
    print("\n===== SUMMARY =====\n" + summary + "\n\nKet qua: " + out)


if __name__ == '__main__':
    main()
