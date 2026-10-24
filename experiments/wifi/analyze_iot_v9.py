#!/usr/bin/env python3
"""Scenario metrics of the v9 campaign (run_iot_v9_campaign.sh), from the files saved per batch.

<root>/<SCENARIO>/figure4a/<ALGO>/rtt_<X>ms/batch_<NN>/ ; the kind of analysis follows the files
present in each scenario:
  fct.csv (SHORT_FLOWS, client_fct.py)  flow completion time per size class: pooled median / p95 /
        p99 (ms), mean of the per-batch medians +- CI95 (Student t over batches), flows not ok.
  outage.txt + rate_1s.csv (PATH1_OUTAGE, client_long.py)  per outage event, from the client's
        per-second aggregate rate and the per-path ifstats rate (r1-wlan1 / r1-wlan2):
        during %   mean aggregate in [down+2 s, up) / mean in [down-10 s, down);
        stall s    seconds in [down, up) with aggregate < 10 % of the pre-outage mean
                   (time without progress = failure detection / failover lag);
        recover s  seconds after "up" until the failed path carries >= 50 % of its pre-outage
                   rate again ("never" if not before the next outage / end of load);
        post %     mean aggregate in [up+10 s, up+20 s) / pre-outage mean.
  positions.csv with a moving r1 (MOBILE_SWAP)  aggregate / path 1 / path 2 rate (ifstats, loaded
        seconds only) per 20 m bin of r1's x position, pooled over batches, and the mean
        difference between going (x increasing) and returning at the same position (lag).
Writes <root>/scenario_metrics.txt (and prints it) plus fct_summary.csv, outage_events.csv,
mobility_bins.csv.

Usage: python3 experiments/wifi/analyze_iot_v9.py <campaign_root>
"""
import csv
import glob
import math
import os
import statistics as st
import sys

T95 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228]
ORDER = ['TRR', 'RR', 'SAPS']


def ci(xs):
    if len(xs) < 2:
        return 0.0
    t = T95[len(xs) - 2] if len(xs) - 2 < len(T95) else 1.96
    return t * st.stdev(xs) / math.sqrt(len(xs))


def pctl(xs, q):
    xs = sorted(xs)
    if not xs:
        return float('nan')
    k = (len(xs) - 1) * q
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def read_kv(path):
    out = {}
    try:
        for line in open(path, errors='replace'):
            if '=' in line:
                k, v = line.rstrip('\n').split('=', 1)
                out[k] = v
    except OSError:
        pass
    return out


def first_float(path):
    try:
        return float(open(path).read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def per_sec(path):
    """{round(epoch): Mbit/s} from an ifstats csv (rx_bytes)."""
    try:
        rows = [(float(r['epoch']), int(r['rx_bytes'])) for r in csv.DictReader(open(path))]
    except (OSError, KeyError, ValueError):
        return {}
    return {round(b[0]): (b[1] - a[1]) * 8 / (b[0] - a[0]) / 1e6 for a, b in zip(rows, rows[1:]) if b[0] > a[0]}


def batches(root):
    out = []
    for sdir in sorted(glob.glob(os.path.join(root, '*', 'figure4a'))):
        scen = os.path.basename(os.path.dirname(sdir))
        for b in sorted(glob.glob(os.path.join(sdir, '*', 'rtt_*ms', 'batch_*'))):
            if os.path.isfile(os.path.join(b, 'throughput.csv')) and os.path.isfile(os.path.join(b, 'manifest.json')):
                parts = b.split(os.sep)
                out.append((scen, parts[-3], int(parts[-2][4:-2]), b))
    return out


def akey(a):
    return (ORDER.index(a) if a in ORDER else 9, a)


def size_label(n):
    return '%g kB' % (n / 1000) if n < 1000000 else '%g MB' % (n / 1e6)


# ---------------------------------------------------------------- short flows
def fct_section(rows, lines, root):
    data = {}  # (scen, algo, rtt, size) -> {'all': [...], 'batch_medians': [...], 'bad': n}
    for scen, algo, rtt, b in rows:
        p = os.path.join(b, 'fct.csv')
        if not os.path.isfile(p):
            continue
        per = {}
        for r in csv.DictReader(open(p)):
            size = int(r['size_bytes'])
            d = data.setdefault((scen, algo, rtt, size), {'all': [], 'batch_medians': [], 'bad': 0, 'n': 0})
            d['n'] += 1
            if r['status'] == 'ok':
                v = float(r['fct_s']) * 1000
                d['all'].append(v)
                per.setdefault(size, []).append(v)
            else:
                d['bad'] += 1
        for size, v in per.items():
            data[(scen, algo, rtt, size)]['batch_medians'].append(st.median(v))
    if not data:
        return
    out = []
    for scen in sorted({k[0] for k in data}):
        lines.append('== %s: flow completion time (ms), pooled flows; "bmed" = mean of batch medians +- CI95' % scen)
        lines.append('%-5s %4s %8s %6s %5s %9s %9s %9s %17s' % ('algo', 'RTT', 'size', 'flows', 'notok', 'median', 'p95', 'p99', 'bmed'))
        keys = sorted((k for k in data if k[0] == scen), key=lambda k: (k[3], k[2], akey(k[1])))
        for k in keys:
            d = data[k]
            med, p95, p99 = pctl(d['all'], .5), pctl(d['all'], .95), pctl(d['all'], .99)
            bm = d['batch_medians']
            lines.append('%-5s %4d %8s %6d %5d %9.1f %9.1f %9.1f %9.1f +-%6.1f' % (
                k[1], k[2], size_label(k[3]), d['n'], d['bad'], med, p95, p99, st.mean(bm) if bm else float('nan'), ci(bm)))
            out.append({'scenario': scen, 'algorithm': k[1], 'rtt_ms': k[2], 'size_bytes': k[3], 'flows': d['n'],
                        'not_ok': d['bad'], 'median_ms': med, 'p95_ms': p95, 'p99_ms': p99,
                        'batch_median_mean_ms': st.mean(bm) if bm else '', 'batch_median_ci95_ms': ci(bm),
                        'n_batches': len(bm)})
        lines.append('')
    write_csv(os.path.join(root, 'fct_summary.csv'), out)


# ---------------------------------------------------------------- outage
def mean_in(series, a, b):
    v = [x for t, x in series.items() if a <= t < b]
    return st.mean(v) if v else float('nan')


def outage_section(rows, lines, root):
    events = []
    for scen, algo, rtt, b in rows:
        op = os.path.join(b, 'outage.txt')
        rp = os.path.join(b, 'rate_1s.csv')
        if not (os.path.isfile(op) and os.path.isfile(rp)):
            continue
        meta = read_kv(os.path.join(root, scen, 'metadata.txt'))
        fpath = int(meta.get('outage_path', '1') or 1)
        agg = {}
        for r in csv.DictReader(open(rp)):
            agg[float(r['epoch'])] = float(r['mbps'])
        load_end = max(agg) + 1 if agg else 0
        pf = per_sec(os.path.join(b, 'ifstats_r1-wlan%d.csv' % fpath))
        ev, cur = [], {}
        for line in open(op):
            kind, t = line.split()[:2]
            if kind == 'down':
                cur = {'down': float(t)}
            elif kind == 'up' and 'down' in cur:
                cur['up'] = float(t)
                ev.append(cur)
                cur = {}
        for i, e in enumerate(ev):
            d, u = e['down'], e['up']
            nxt = ev[i + 1]['down'] if i + 1 < len(ev) else load_end
            pre = mean_in(agg, d - 10, d)
            during = mean_in(agg, d + 2, u)
            stall = sum(1 for t, x in agg.items() if d <= t < u and x < 0.1 * pre)
            p_pre = mean_in(pf, d - 10, d)
            rec = None
            for t in sorted(pf):
                if u <= t < nxt and pf[t] >= 0.5 * p_pre:
                    rec = t - u
                    break
            post = mean_in(agg, u + 10, u + 20)
            events.append({'scenario': scen, 'algorithm': algo, 'rtt_ms': rtt, 'batch': os.path.basename(b),
                           'event': i + 1, 'failed_path': fpath, 'pre_mbps': pre, 'during_mbps': during,
                           'during_pct': 100 * during / pre if pre else float('nan'), 'stall_s': stall,
                           'failed_path_pre_mbps': p_pre,
                           'recover_s': '' if rec is None else round(rec, 1),
                           'post_pct': 100 * post / pre if pre else float('nan')})
    if not events:
        return
    for scen in sorted({e['scenario'] for e in events}):
        lines.append('== %s: path outage events (mean over events; recover = s until the failed path is back at >= 50 %%)' % scen)
        lines.append('%-5s %4s %6s %8s %10s %8s %10s %12s %8s' % ('algo', 'RTT', 'events', 'pre', 'during %', 'stall s', 'recover s', 'never back', 'post %'))
        sel = [e for e in events if e['scenario'] == scen]
        for algo, rtt in sorted({(e['algorithm'], e['rtt_ms']) for e in sel}, key=lambda k: (k[1], akey(k[0]))):
            es = [e for e in sel if e['algorithm'] == algo and e['rtt_ms'] == rtt]
            rec = [e['recover_s'] for e in es if e['recover_s'] != '']
            fin = lambda k: [e[k] for e in es if e[k] == e[k]]
            lines.append('%-5s %4d %6d %8.1f %10.1f %8.1f %10s %12d %8.1f' % (
                algo, rtt, len(es), st.mean(fin('pre_mbps') or [float('nan')]), st.mean(fin('during_pct') or [float('nan')]),
                st.mean(e['stall_s'] for e in es), ('%.1f' % st.mean(rec)) if rec else '-', len(es) - len(rec),
                st.mean(fin('post_pct') or [float('nan')])))
        lines.append('')
    write_csv(os.path.join(root, 'outage_events.csv'), events)


# ---------------------------------------------------------------- mobility
def mobility_section(rows, lines, root):
    acc = {}  # (scen, algo, rtt, bin, dir) -> lists
    for scen, algo, rtt, b in rows:
        pp = os.path.join(b, 'positions.csv')
        if not (os.path.isfile(pp) and os.path.isfile(os.path.join(b, 'mob_started.txt'))):
            continue
        xs = {}
        try:
            for r in csv.DictReader(open(pp)):
                if r['node'] == 'r1':
                    xs[round(float(r['epoch']))] = float(r['x'])
        except (KeyError, ValueError):
            continue
        if len(set(round(v) for v in xs.values())) < 3:
            continue  # r1 did not move
        ws, we = first_float(os.path.join(b, 'workload_start.txt')), first_float(os.path.join(b, 'workload_end.txt'))
        if ws is None or we is None:
            continue
        h1 = per_sec(os.path.join(b, 'ifstats_h1-wlan0.csv'))
        p1 = per_sec(os.path.join(b, 'ifstats_r1-wlan1.csv'))
        p2 = per_sec(os.path.join(b, 'ifstats_r1-wlan2.csv'))
        for t, v in h1.items():
            if not (ws + 13 <= t <= we and v > 1 and t in xs and t - 1 in xs and t + 1 in xs):
                continue
            x, dx = xs[t], xs[t + 1] - xs[t - 1]
            if abs(dx) < 1e-6:
                continue
            lo = 20 * math.floor(x / 20)
            k = (scen, algo, rtt, lo, 'go' if dx > 0 else 'back')
            d = acc.setdefault(k, {'tot': [], 'p1': [], 'p2': []})
            d['tot'].append(v); d['p1'].append(p1.get(t, 0.0)); d['p2'].append(p2.get(t, 0.0))
    if not acc:
        return
    out = []
    for scen in sorted({k[0] for k in acc}):
        bins = sorted({k[3] for k in acc if k[0] == scen})
        lines.append('== %s: rate (Mbit/s, loaded seconds) per 20 m bin of r1 x; total (path1/path2), both directions pooled' % scen)
        lines.append('%-5s %4s ' % ('algo', 'RTT') + ' '.join('%17s' % ('x %d-%d' % (b0, b0 + 20)) for b0 in bins) + '   go-back')
        for algo, rtt in sorted({(k[1], k[2]) for k in acc if k[0] == scen}, key=lambda k: (k[1], akey(k[0]))):
            cells, diffs = [], []
            for b0 in bins:
                g = acc.get((scen, algo, rtt, b0, 'go'), {'tot': [], 'p1': [], 'p2': []})
                r = acc.get((scen, algo, rtt, b0, 'back'), {'tot': [], 'p1': [], 'p2': []})
                tot, q1, q2 = g['tot'] + r['tot'], g['p1'] + r['p1'], g['p2'] + r['p2']
                if tot:
                    cells.append('%5.1f (%4.1f/%4.1f)' % (st.mean(tot), st.mean(q1), st.mean(q2)))
                    out.append({'scenario': scen, 'algorithm': algo, 'rtt_ms': rtt, 'x_bin': b0, 'seconds': len(tot),
                                'total_mbps': st.mean(tot), 'path1_mbps': st.mean(q1), 'path2_mbps': st.mean(q2),
                                'go_total_mbps': st.mean(g['tot']) if g['tot'] else '',
                                'back_total_mbps': st.mean(r['tot']) if r['tot'] else ''})
                else:
                    cells.append('%17s' % '-')
                if g['tot'] and r['tot']:
                    diffs.append(st.mean(g['tot']) - st.mean(r['tot']))
            lines.append('%-5s %4d ' % (algo, rtt) + ' '.join(cells) + '   %+5.1f' % (st.mean(diffs) if diffs else float('nan')))
        lines.append('go-back = mean over bins of (rate going in +x) - (rate returning): != 0 means the rate depends on the')
        lines.append('direction of motion at the same position (adaptation lag / history), not only on the position.')
        lines.append('')
    write_csv(os.path.join(root, 'mobility_bins.csv'), out)


def write_csv(path, rows):
    if not rows:
        return
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main():
    if len(sys.argv) != 2 or not os.path.isdir(sys.argv[1]):
        sys.exit(__doc__)
    root = sys.argv[1]
    rows = batches(root)
    lines = ['v9 scenario metrics: %s (%d validated batches)' % (root, len(rows)), '']
    mobility_section(rows, lines, root)
    outage_section(rows, lines, root)
    fct_section(rows, lines, root)
    txt = '\n'.join(lines) + '\n'
    open(os.path.join(root, 'scenario_metrics.txt'), 'w').write(txt)
    print(txt)


if __name__ == '__main__':
    main()
