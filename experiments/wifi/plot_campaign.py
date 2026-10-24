#!/usr/bin/env python3
"""Campaign plots for the IoT Wi-Fi runners (v5/v6/v7 layout), from the files already saved.

Reads <root>/<SCENARIO>/figure4a/<ALGO>/rtt_<X>ms/batch_<NN>/ (or <root>/figure4a/... for a single
run) and writes PNGs to <root>/plots_campaign/:
  throughput.png   aggregate throughput vs RTT of path 2, one panel per scenario: mean of the batch
                   means +- CI95 (Student t), single batches as dots, the IoT-hop ceiling dashed;
  scenario_effect.png  % change of every scenario vs the first one, per algorithm and RTT;
  per_path.png     path 1 / path 2 throughput (Mbit/s, from ifstats r1-wlan1/r1-wlan2 over the
                   loaded seconds of the workload) with the ceilings: cake, Wi-Fi hop, Mathis
                   1.22*MSS/(RTT*sqrt(p)) when the scenario has loss;
  ping.png         end-to-end ping during the load per path: mean RTT and loss %;
  radio.png        Wi-Fi link quality per scenario: minimum signal and MAC retry % per hop.
Figure 4b roots (v8 runner: <SCENARIO>/figure4b/<ALGO>/rwnd_<X>MB/batch_<NN>) are detected
automatically (or --fig 4b): the x axis becomes the receive window (categorical, Path-2 RTT fixed).
With --watch S it redraws every S seconds while the campaign runs, adds live.png (the attempt in
progress: per-second path rates and the tests finished so far) and live.html, which reloads itself;
open it in a browser (file://<root>/plots_campaign/live.html). Batches appear in the plots as soon
as the runner has validated them; the loop ends when cross_scenario.txt appears (campaign done).

Usage: python3 experiments/wifi/plot_campaign.py <campaign_root> [--watch 30]
       [--wifi 30.6,30.9] [--iot 35.6] [--rtt1 10]
--wifi/--iot draw the Wi-Fi hop ceilings; they depend on distance, so they are drawn only when
given. For the v7 campaign (10 m) use the single-flow iperf values measured on 2026-10-05:
--wifi 30.6,30.9 --iot 35.6.
"""
import argparse
import csv
import glob
import json
import math
import os
import re
import statistics as st
import sys
import time

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import signal_report as SR  # same directory
except ImportError:  # pragma: no cover
    SR = None

LABELS = {'TRR': 'UniProxy (TRR)', 'RR': 'MPQUIC (RR+MinRTT)', 'SAPS': 'MPQUIC (RR+SAPS)'}
COLORS = {'TRR': '#1f6fb2', 'RR': '#c2410c', 'SAPS': '#2f7a5b'}
ORDER = ['TRR', 'RR', 'SAPS']
# x axis: Figure 4a = RTT of path 2 (numeric), Figure 4b = receive window (categorical); set in main().
FIG = {'name': '4a', 'dir': 'figure4a', 'glob': 'rtt_*ms', 'rtt2': None, 'pos': {}}


def xval(name):
    return int(name[4:-2]) if FIG['name'] == '4a' else float(name[5:-2])


def xp(v):
    """Plot position of x value v."""
    return v if FIG['name'] == '4a' else FIG['pos'].get(v, 0)


def xlab(v):
    return '%g' % v


def set_x(ax, xs):
    ax.set_xticks([xp(v) for v in xs])
    ax.set_xticklabels([xlab(v) for v in xs])


def xname():
    return 'RTT Path 2 (ms)' if FIG['name'] == '4a' else 'rwnd (MB), RTT Path 2 = %s ms' % FIG['rtt2']
MSS = 1350
T95 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145]


def t95(n):
    return T95[n - 2] if 2 <= n <= len(T95) + 1 else 1.96


def color(a):
    return COLORS.get(a, plt.rcParams['axes.prop_cycle'].by_key()['color'][hash(a) % 10])


def read_kv(path):
    out = {}
    try:
        for line in open(path, errors='replace'):
            if '=' in line:
                k, v = line.rstrip('\n').split('=', 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def scenarios(root):
    if os.path.isdir(os.path.join(root, FIG['dir'])):
        return [(os.path.basename(os.path.normpath(root)), root)]
    out = []
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if os.path.isdir(os.path.join(p, FIG['dir'])):
            out.append((d, p))
    camp = read_kv(os.path.join(root, 'campaign.txt'))
    want = camp.get('scenarios', '').split()
    if want:
        out.sort(key=lambda s: want.index(s[0]) if s[0] in want else 99)
    return out


def scen_params(sdir):
    m = read_kv(os.path.join(sdir, 'metadata.txt'))
    f = lambda k, d: float(m.get(k, d) or d)
    return {'cake': (f('cake_bw1_mbit', 0), f('cake_bw2_mbit', 0)), 'loss': f('loss_pct', 0),
            'jitter_pct': f('jitter_pct', 0), 'jitter_ms': f('jitter_ms', 0), 'rwnd': f('figure4a_rwnd_mb', 0),
            'rtt2': f('figure4b_rtt_ms', 100)}


def per_sec(path, key='rx_bytes'):
    try:
        rows = [(float(r['epoch']), int(r[key])) for r in csv.DictReader(open(path))]
    except (OSError, KeyError, ValueError):
        return {}
    return {round(b[0]): (b[1] - a[1]) * 8 / (b[0] - a[0]) / 1e6 for a, b in zip(rows, rows[1:]) if b[0] > a[0]}


def first_float(path):
    try:
        return float(open(path).read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def path_rates(d):
    """Mean path-1 / path-2 / h1 rate over the loaded seconds of the workload in dir d."""
    ws, we = first_float(os.path.join(d, 'workload_start.txt')), first_float(os.path.join(d, 'workload_end.txt'))
    if ws is None:
        return None
    we = we or time.time()
    h1 = per_sec(os.path.join(d, 'ifstats_h1-wlan0.csv'))
    p1 = per_sec(os.path.join(d, 'ifstats_r1-wlan1.csv'))
    p2 = per_sec(os.path.join(d, 'ifstats_r1-wlan2.csv'))
    rows = [(p1.get(t, 0.0), p2.get(t, 0.0), v) for t, v in h1.items() if ws + 13 <= t <= we and v > 1]
    if not rows:
        return None
    return tuple(st.mean(r[i] for r in rows) for i in range(3))


def ping_stats(path):
    try:
        txt = open(path, errors='replace').read()
    except OSError:
        return None
    lo = re.search(r'([\d.]+)% packet loss', txt)
    rt = re.search(r'= [\d.]+/([\d.]+)/[\d.]+/([\d.]+) ms', txt)
    if not lo or not rt:
        return None
    return float(rt.group(1)), float(lo.group(1))


def collect(root):
    """data[scen][algo][x] = list of batch dicts (only batches the runner validated); x = RTT of
    path 2 (Figure 4a) or receive window in MB (Figure 4b)."""
    data, params = {}, {}
    for name, sdir in scenarios(root):
        params[name] = scen_params(sdir)
        if FIG['name'] == '4b':
            FIG['rtt2'] = '%g' % params[name]['rtt2']
        for b in glob.glob(os.path.join(sdir, FIG['dir'], '*', FIG['glob'], 'batch_*')):
            if not (os.path.isfile(os.path.join(b, 'throughput.csv')) and os.path.isfile(os.path.join(b, 'manifest.json'))):
                continue
            algo = b.split(os.sep)[-3]
            rtt = xval(b.split(os.sep)[-2])
            try:
                vals = [float(x) for x in open(os.path.join(b, 'throughput.csv')).read().split()]
            except ValueError:
                continue
            rec = {'mean': st.mean(vals), 'dir': b, 'paths': path_rates(b),
                   'ping1': ping_stats(os.path.join(b, 'ping_e2e_path1.txt')),
                   'ping2': ping_stats(os.path.join(b, 'ping_e2e_path2.txt'))}
            data.setdefault(name, {}).setdefault(algo, {}).setdefault(rtt, []).append(rec)
    FIG['pos'] = {v: i for i, v in enumerate(sorted({r for s in data.values() for a in s.values() for r in a}))}
    return data, params


def algos_of(data):
    seen = sorted({a for s in data.values() for a in s}, key=lambda a: (ORDER.index(a) if a in ORDER else 9, a))
    return seen


def rtts_of(data):
    return sorted({r for s in data.values() for a in s.values() for r in a})


def mean_ci(xs):
    if not xs:
        return None, None
    m = st.mean(xs)
    return m, (t95(len(xs)) * st.stdev(xs) / math.sqrt(len(xs)) if len(xs) > 1 else 0.0)


def save(fig, path):
    tmp = path + '.tmp.png'
    fig.savefig(tmp, dpi=150, bbox_inches='tight')
    plt.close(fig)
    os.replace(tmp, path)


def fig_throughput(data, params, args, out):
    sc, al, rt = list(data), algos_of(data), rtts_of(data)
    fig, axes = plt.subplots(1, len(sc), figsize=(5.2 * len(sc), 4), sharey=True, squeeze=False)
    for ax, s in zip(axes[0], sc):
        for i, a in enumerate(al):
            xs, ys, es, ns = [], [], [], []
            for r in rt:
                bm = [b['mean'] for b in data[s].get(a, {}).get(r, [])]
                if bm:
                    m, ci = mean_ci(bm)
                    xs.append(xp(r)); ys.append(m); es.append(ci); ns.append(len(bm))
                    ax.scatter([xp(r) + (i - 1) * (1.5 if FIG['name'] == '4a' else 0.08)] * len(bm), bm, s=10, color=color(a), alpha=.35, zorder=2)
            if xs:
                ax.errorbar(xs, ys, yerr=es, marker='o', capsize=3, color=color(a), label=LABELS.get(a, a), zorder=3)
        if args.iot:
            ax.axhline(args.iot, ls='--', lw=1, color='#7a4fa3')
            ax.text(xp(rt[0]) if rt else 0, args.iot, ' chặng IoT %.1f' % args.iot, va='bottom', fontsize=8, color='#7a4fa3')
        p = params.get(s, {})
        ax.set_title('%s\ncake %g/%g, %s, jitter %s, loss %g %%' % (
            s, p.get('cake', (0, 0))[0], p.get('cake', (0, 0))[1],
            ('rwnd %g MB' % p.get('rwnd', 0)) if FIG['name'] == '4a' else ('RTT P2 %g ms' % p.get('rtt2', 100)),
            ('%g %%' % p['jitter_pct']) if p.get('jitter_pct') else ('%g ms' % p.get('jitter_ms', 0)), p.get('loss', 0)), fontsize=9)
        ax.set_xlabel(xname()); set_x(ax, rt); ax.grid(alpha=.3)
    axes[0][0].set_ylabel('Thông lượng tổng (Mbit/s)')
    axes[0][0].legend(fontsize=8, loc='lower left')
    fig.text(0.5, -0.02, 'Trung bình của trung bình batch ± CI95 (Student t); chấm nhạt = từng batch', ha='center', fontsize=8)
    save(fig, os.path.join(out, 'throughput.png'))


def fig_scenario_effect(data, out):
    sc, al, rt = list(data), algos_of(data), rtts_of(data)
    if len(sc) < 2:
        return
    base = sc[0]
    fig, axes = plt.subplots(1, len(sc) - 1, figsize=(5.5 * (len(sc) - 1), 3.6), squeeze=False)
    w = 0.8 / max(1, len(al))
    for ax, s in zip(axes[0], sc[1:]):
        for i, a in enumerate(al):
            xs, ys = [], []
            for j, r in enumerate(rt):
                b0 = [b['mean'] for b in data[base].get(a, {}).get(r, [])]
                b1 = [b['mean'] for b in data[s].get(a, {}).get(r, [])]
                if b0 and b1:
                    xs.append(j + (i - (len(al) - 1) / 2) * w); ys.append(100 * (st.mean(b1) / st.mean(b0) - 1))
            if xs:
                ax.bar(xs, ys, width=w, color=color(a), label=LABELS.get(a, a))
                for x, y in zip(xs, ys):
                    ax.text(x, y, '%+.0f' % y, ha='center', va='bottom' if y >= 0 else 'top', fontsize=7)
        ax.axhline(0, color='k', lw=.8)
        ax.set_xticks(range(len(rt))); ax.set_xticklabels([('RTT %d' if FIG['name'] == '4a' else '%g MB') % r for r in rt])
        ax.set_ylabel('% thay đổi'); ax.set_title('%s so với %s' % (s, base), fontsize=9); ax.grid(alpha=.3, axis='y')
    axes[0][0].legend(fontsize=8)
    save(fig, os.path.join(out, 'scenario_effect.png'))


def fig_per_path(data, params, args, out):
    sc, al, rt = list(data), algos_of(data), rtts_of(data)
    fig, axes = plt.subplots(len(sc), 2, figsize=(10.5, 3.4 * len(sc)), squeeze=False)
    for row, s in enumerate(sc):
        p = params.get(s, {})
        for col in (0, 1):
            ax = axes[row][col]
            for a in al:
                xs, ys, es = [], [], []
                for r in rt:
                    v = [b['paths'][col] for b in data[s].get(a, {}).get(r, []) if b['paths']]
                    if v:
                        m, ci = mean_ci(v); xs.append(xp(r)); ys.append(m); es.append(ci)
                if xs:
                    ax.errorbar(xs, ys, yerr=es, marker='o', capsize=3, color=color(a), label=LABELS.get(a, a))
            cake = p.get('cake', (0, 0))[col]
            if cake:
                ax.axhline(cake, color='#2f7a5b', lw=1.2, ls='-'); ax.text(xp(rt[-1]) if rt else 0, cake, 'cake %g ' % cake, ha='right', va='bottom', fontsize=8, color='#2f7a5b')
            if args.wifi:
                wf = args.wifi[col]
                ax.axhline(wf, color='#3b8f3b', lw=1, ls=':'); ax.text(xp(rt[0]) if rt else 0, wf, ' Wi-Fi %.1f' % wf, va='bottom', fontsize=8, color='#3b8f3b')
            if p.get('loss', 0) > 0 and rt:
                pr = p['loss'] / 100.0
                cap = lambda r: 1.22 * MSS * 8 / (r / 1000.0 * math.sqrt(pr)) / 1e6
                if FIG['name'] == '4a':
                    xr = list(range(min(rt), max(rt) + 1, 2))
                    ys = [cap(args.rtt1)] * len(xr) if col == 0 else [cap(r) for r in xr]
                else:  # Figure 4b: both RTTs fixed -> flat lines
                    xr = [xp(rt[0]), xp(rt[-1])] * 2
                    xr = sorted(xr)
                    ys = [cap(args.rtt1 if col == 0 else p.get('rtt2', 100))] * len(xr)
                ax.plot(xr, ys, color='#c62828', ls='--', lw=1)
                ax.text(xr[len(xr) // 3], min(ys[len(xr) // 3], 44), ' Mathis p=%g %%' % p['loss'], va='bottom', fontsize=8, color='#c62828')
            ax.set_ylim(bottom=0, top=max(45, (cake or 0) * 1.1))
            ax.set_title('%s · Path %d%s' % (s, col + 1, ' (RTT %g ms cố định)' % args.rtt1 if col == 0 else ''), fontsize=9)
            set_x(ax, rt); ax.grid(alpha=.3)
            if col == 0:
                ax.set_ylabel('Mbit/s (giây có tải)')
            if row == len(sc) - 1:
                ax.set_xlabel(xname())
    axes[0][1].legend(fontsize=7, loc='upper right')
    save(fig, os.path.join(out, 'per_path.png'))


def fig_ping(data, out):
    sc, al, rt = list(data), algos_of(data), rtts_of(data)
    fig, axes = plt.subplots(len(sc), 2, figsize=(10.5, 3.2 * len(sc)), squeeze=False)
    for row, s in enumerate(sc):
        for a in al:
            for k, ls in (('ping1', '-'), ('ping2', '--')):
                xs, rtt_v, loss_v = [], [], []
                for r in rt:
                    v = [b[k] for b in data[s].get(a, {}).get(r, []) if b[k]]
                    if v:
                        xs.append(xp(r)); rtt_v.append(st.mean(x[0] for x in v)); loss_v.append(st.mean(x[1] for x in v))
                if xs:
                    lab = '%s P%s' % (a, k[-1])
                    axes[row][0].plot(xs, rtt_v, ls=ls, marker='o', color=color(a), label=lab)
                    axes[row][1].plot(xs, loss_v, ls=ls, marker='o', color=color(a), label=lab)
        axes[row][0].set_title('%s · RTT ping e2e khi có tải (ms)' % s, fontsize=9)
        axes[row][1].set_title('%s · mất gói ping e2e (%%)' % s, fontsize=9)
        for ax in axes[row]:
            set_x(ax, rt); ax.grid(alpha=.3)
    axes[0][0].legend(fontsize=7, ncol=2)
    for ax in axes[-1]:
        ax.set_xlabel(xname() + '; nét liền Path 1, nét đứt Path 2')
    save(fig, os.path.join(out, 'ping.png'))


def fig_radio(root, data, out):
    if SR is None:
        return
    rows = []
    for s in data:
        for algo in data[s]:
            for r in data[s][algo]:
                for b in data[s][algo][r]:
                    for label, radio, _, ap_pfx in SR.LINKS:
                        rs = SR.radio_series(os.path.join(b['dir'], 'radio_%s.csv' % radio))
                        if not rs:
                            continue
                        pre = SR.parse_station_dump(os.path.join(b['dir'], '%s_station_pre.txt' % ap_pfx))
                        post = SR.parse_station_dump(os.path.join(b['dir'], '%s_station_post.txt' % ap_pfx))
                        dl = (post.get('tx_retries', 0) - pre.get('tx_retries', 0), post.get('tx_packets', 0) - pre.get('tx_packets', 0)) if post and pre else (0, 0)
                        rows.append((s, label, rs['sig_min'], rs['sig_med'], rs.get('ul_retries', 0), rs.get('ul_pkts', 0), dl[0], dl[1]))
    if not rows:
        return
    sc, links = list(data), [l[0] for l in SR.LINKS]
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.4))
    w = 0.8 / len(sc)
    for i, s in enumerate(sc):
        xs = [j + (i - (len(sc) - 1) / 2) * w for j in range(len(links))]
        sig = [min((r[2] for r in rows if r[0] == s and r[1] == l), default=float('nan')) for l in links]
        med = [st.median([r[3] for r in rows if r[0] == s and r[1] == l] or [float('nan')]) for l in links]
        ret = []
        for l in links:
            sel = [r for r in rows if r[0] == s and r[1] == l]
            num = sum(r[4] + r[6] for r in sel); den = sum(r[5] + r[7] for r in sel)
            ret.append(100.0 * num / den if den else 0.0)
        axes[0].bar(xs, [-x for x in sig], width=w, label=s + ' (min)', alpha=.55)
        axes[0].scatter(xs, [-x for x in med], color='k', s=12, zorder=3)
        axes[1].bar(xs, ret, width=w, label=s)
    axes[0].axhline(65, color='#c2410c', ls='--', lw=1); axes[0].text(-0.45, 65, ' ngưỡng −65 dBm', va='bottom', fontsize=8, color='#c2410c')
    axes[0].set_ylabel('−tín hiệu (dB, thấp hơn = tốt hơn)'); axes[0].set_title('Tín hiệu: cột = min, chấm = trung vị', fontsize=9)
    axes[1].set_ylabel('% frame phát lại (hai chiều)'); axes[1].set_title('Retry MAC', fontsize=9)
    for ax in axes:
        ax.set_xticks(range(len(links))); ax.set_xticklabels(links, fontsize=8); ax.grid(alpha=.3, axis='y')
    axes[1].legend(fontsize=7)
    save(fig, os.path.join(out, 'radio.png'))


def active_attempt(root):
    best, best_t = None, 0
    for f in glob.glob(os.path.join(root, '**', 'attempt_*', 'ifstats_h1-wlan0.csv'), recursive=True):
        t = os.path.getmtime(f)
        if t > best_t:
            best, best_t = os.path.dirname(f), t
    return best if best and time.time() - best_t < 60 else None


def fig_live(root, out, progress):
    d = active_attempt(root)
    fig, ax = plt.subplots(figsize=(10.5, 3.6))
    if not d:
        ax.text(.5, .5, 'Không có lần đo nào đang chạy (%s)' % progress, ha='center', va='center', transform=ax.transAxes)
        ax.set_axis_off(); save(fig, os.path.join(out, 'live.png'))
        return
    parts = d.split(os.sep)
    label = ' / '.join(parts[-6:-1]) + ' / ' + parts[-1]
    ws = first_float(os.path.join(d, 'workload_start.txt'))
    if ws is not None:
        for intf, name, c in (('r1-wlan1', 'Path 1', '#1f6fb2'), ('r1-wlan2', 'Path 2', '#b4581b'), ('h1-wlan0', 'h1 (tổng)', '#7a4fa3')):
            ser = per_sec(os.path.join(d, 'ifstats_%s.csv' % intf))
            ts = sorted(t for t in ser if t >= ws)
            ax.plot([t - ws for t in ts], [ser[t] for t in ts], color=c, lw=1.4 if name != 'h1 (tổng)' else 2, label=name)
        tests = []
        try:
            lines = open(os.path.join(d, 'h1_client.log'), errors='replace').read().split('\n')
            tests = [float(x) for x in lines if re.fullmatch(r'\s*\d+(\.\d+)?\s*', x)]
        except OSError:
            pass
        for k, v in enumerate(tests):
            x = 13 + k * 13 + 10   # warm-up 10 s + 3 s, then each test 10 s + 3 s pause
            ax.scatter([x], [v], marker='D', color='k', zorder=4)
            ax.text(x, v, ' %.1f' % v, fontsize=7, va='bottom')
        ax.set_title('Đang chạy: %s · %d/10 lần đo xong (◆ = thông lượng client.py)  ·  %s' % (label, len(tests), progress), fontsize=9)
    ax.set_xlabel('giây kể từ workload_start'); ax.set_ylabel('Mbit/s'); ax.grid(alpha=.3); ax.legend(fontsize=8, loc='upper right')
    ax.set_xlim(0, 150)
    save(fig, os.path.join(out, 'live.png'))


def progress_text(root, data):
    done = sum(len(v) for s in data.values() for a in s.values() for v in a.values())
    try:
        txt = open(os.path.join(root, 'campaign.txt')).read()
    except OSError:
        txt = ''
    m = re.search(r'batches=(\d+) (?:rtts|rwnds)=([\d. ]+?) algorithms=([A-Za-z ]+?) (?:run_fig4b|rtt2)', txt)
    sc = re.search(r'^scenarios=(.*)$', txt, re.M)
    if m:
        total = int(m.group(1)) * len(m.group(2).split()) * len(m.group(3).split()) * max(1, len(sc.group(1).split()) if sc else 1)
        return '%d/%d batch' % (done, total)
    return '%d batch xong' % done


def write_html(out, every):
    names = [n for n in ('live.png', 'throughput.png', 'scenario_effect.png', 'per_path.png', 'ping.png', 'radio.png')
             if os.path.exists(os.path.join(out, n))]
    stamp = int(time.time())
    body = '\n'.join('<p><img src="%s?t=%d" style="max-width:100%%"></p>' % (n, stamp) for n in names)
    html = ('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="%d"><title>campaign live</title>'
            '<body style="font-family:sans-serif;margin:16px"><p>Cập nhật %s, tự tải lại mỗi %d s.</p>%s</body>'
            % (every, time.strftime('%H:%M:%S'), every, body))
    tmp = os.path.join(out, 'live.html.tmp')
    open(tmp, 'w').write(html)
    os.replace(tmp, os.path.join(out, 'live.html'))


def draw(root, args, live):
    out = os.path.join(root, 'plots_campaign')
    os.makedirs(out, exist_ok=True)
    data, params = collect(root)
    prog = progress_text(root, data)
    if data:
        fig_throughput(data, params, args, out)
        fig_scenario_effect(data, out)
        fig_per_path(data, params, args, out)
        fig_ping(data, out)
        fig_radio(root, data, out)
    if live:
        fig_live(root, out, prog)
        write_html(out, args.watch)
    return out, prog


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('root')
    ap.add_argument('--watch', type=int, default=0, help='redraw every N seconds (live mode); 0 = once')
    ap.add_argument('--wifi', default=None, help='Wi-Fi ceiling of path 1,path 2 (Mbit/s), e.g. 30.6,30.9 (v7, 10 m)')
    ap.add_argument('--iot', type=float, default=None, help='IoT hop (h1~r1) ceiling (Mbit/s), e.g. 35.6 (v7, 10 m)')
    ap.add_argument('--rtt1', type=float, default=10, help='RTT of path 1 (ms)')
    ap.add_argument('--fig', choices=['auto', '4a', '4b'], default='auto',
                    help='x axis: 4a = RTT sweep, 4b = rwnd sweep; auto = 4b only if the root has figure4b and no figure4a')
    a = ap.parse_args()
    if a.fig == 'auto':
        has = lambda d: bool(glob.glob(os.path.join(a.root, d)) or glob.glob(os.path.join(a.root, '*', d)))
        a.fig = '4b' if has('figure4b') and not has('figure4a') else '4a'
    if a.fig == '4b':
        FIG.update({'name': '4b', 'dir': 'figure4b', 'glob': 'rwnd_*MB', 'rtt2': '100'})
    a.wifi = tuple(float(x) for x in a.wifi.split(',')) if a.wifi else None
    if not os.path.isdir(a.root):
        sys.exit('no such directory: %s' % a.root)
    if not a.watch:
        out, prog = draw(a.root, a, live=False)
        print('plots in %s (%s)' % (out, prog))
        return 0
    print('watching %s every %d s; open file://%s in a browser; Ctrl-C to stop' % (
        a.root, a.watch, os.path.abspath(os.path.join(a.root, 'plots_campaign', 'live.html'))))
    try:
        while True:
            out, prog = draw(a.root, a, live=True)
            print('[%s] %s' % (time.strftime('%H:%M:%S'), prog), flush=True)
            if os.path.exists(os.path.join(a.root, 'cross_scenario.txt')) and not active_attempt(a.root):
                draw(a.root, a, live=True)
                print('campaign done; final plots in', out)
                return 0
            time.sleep(a.watch)
    except KeyboardInterrupt:
        print('stopped')
    return 0


if __name__ == '__main__':
    sys.exit(main())
