#!/usr/bin/env python3
"""Wi-Fi link-quality report from the per-batch logs of the IoT runners (v2+).

For every batch directory under <root> that holds radio_<intf>.csv (1 Hz station dump of
the topology) and iw_*_link_pre/post.txt, iw_*_station_pre/post.txt (runner snapshots), it
reports per Wi-Fi link:
  - signal (dBm): min / median / max over the run (radio_*.csv, the station side);
  - PHY tx bitrate (Mbit/s): median over the run;
  - MAC retries and failures of the run: delta(post - pre) of "tx retries" / "tx failed" over
    "tx packets" from the iw station dumps, for the uplink (station -> AP, radio_*.csv and
    iw_<sta>_station_*) and the downlink (AP -> station, iw_ap{1,2}_station_* and
    iw_r1-wlan0_ap_station_* = r1's IoT AP towards h1).
Then one table per scenario (median of the per-batch values, min of the minima, pooled
retry %), with a verdict: signal >= -65 dBm and retries < 1 % => "good"; -75..-65 or
retries < 5 % => "fair"; otherwise "weak".

Usage: python3 signal_report.py <campaign-or-result-root> [--csv out.csv]
"""
import argparse
import csv
import os
import re
import statistics as st
import sys

LINKS = [  # (label, station radio csv, station iw prefix, AP-side station dump prefix)
    ('IoT h1~r1', 'h1-wlan0', 'iw_h1-wlan0', 'iw_r1-wlan0_ap'),
    ('Path1 r1~ap1', 'r1-wlan1', 'iw_r1-wlan1', 'iw_ap1'),
    ('Path2 r1~ap2', 'r1-wlan2', 'iw_r1-wlan2', 'iw_ap2'),
]


def parse_station_dump(path):
    """First station of an `iw dev X station dump` -> dict of counters (ints) and signal."""
    out = {}
    try:
        txt = open(path, errors='replace').read()
    except OSError:
        return out
    for key, pat in (('tx_packets', r'tx packets:\s+(\d+)'), ('tx_retries', r'tx retries:\s+(\d+)'),
                     ('tx_failed', r'tx failed:\s+(\d+)'), ('signal', r'signal:\s+(-?\d+)')):
        m = re.search(pat, txt)
        if m:
            out[key] = int(m.group(1))
    m = re.search(r'tx bitrate:\s+([\d.]+)', txt)
    if m:
        out['tx_bitrate'] = float(m.group(1))
    return out


def radio_series(path):
    sig, rate, first, last = [], [], None, None
    try:
        rows = list(csv.DictReader(open(path)))
    except OSError:
        return None
    for r in rows:
        try:
            sig.append(int(r['signal_dbm']))
        except (KeyError, ValueError):
            pass
        try:
            rate.append(float(r['tx_bitrate_mbps']))
        except (KeyError, ValueError):
            pass
        try:
            cur = (int(r['tx_packets']), int(r['tx_retries']), int(r['tx_failed']))
            first = first or cur
            last = cur
        except (KeyError, ValueError):
            pass
    if not sig:
        return None
    d = {'n': len(sig), 'sig_min': min(sig), 'sig_med': st.median(sig), 'sig_max': max(sig),
         'rate_med': st.median(rate) if rate else None}
    if first and last:
        d['ul_pkts'], d['ul_retries'], d['ul_failed'] = (last[0] - first[0], last[1] - first[1], last[2] - first[2])
    return d


def batch_dirs(root):
    for dp, dn, fn in os.walk(root):
        if any(f.startswith('radio_') and f.endswith('.csv') for f in fn) and 'manifest.json' in fn:
            yield dp
        dn[:] = [d for d in dn if not d.startswith('attempt_')]


def batch_meta(bdir):
    """scenario / algorithm / rtt from the path: <root>/<SCENARIO>/figure4a/<ALGO>/rtt_Xms/batch_NN."""
    parts = bdir.rstrip('/').split('/')
    algo = rtt = scen = ''
    for i, p in enumerate(parts):
        if p.startswith('rtt_') and p.endswith('ms'):
            rtt = p[4:-2]
            algo = parts[i - 1]
            scen = parts[i - 3] if i >= 3 and parts[i - 2] == 'figure4a' else ''
    return scen or os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(bdir)))), algo, rtt, parts[-1]


def pct(num, den):
    return 100.0 * num / den if den else float('nan')


def verdict(sig_min, ul_pct, dl_pct):
    worst = max(x for x in (ul_pct, dl_pct) if x == x) if any(x == x for x in (ul_pct, dl_pct)) else 0.0
    if sig_min >= -65 and worst < 1:
        return 'good'
    if sig_min >= -75 and worst < 5:
        return 'fair'
    return 'weak'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('root')
    ap.add_argument('--csv', default=None, help='write per-batch rows to this CSV')
    a = ap.parse_args()
    rows = []
    for b in sorted(batch_dirs(a.root)):
        scen, algo, rtt, batch = batch_meta(b)
        for label, radio, sta_pfx, ap_pfx in LINKS:
            rs = radio_series(os.path.join(b, 'radio_{}.csv'.format(radio)))
            if rs is None:
                continue
            pre = parse_station_dump(os.path.join(b, '{}_station_pre.txt'.format(ap_pfx)))
            post = parse_station_dump(os.path.join(b, '{}_station_post.txt'.format(ap_pfx)))
            dl = {k: post.get(k, 0) - pre.get(k, 0) for k in ('tx_packets', 'tx_retries', 'tx_failed')} \
                if post and pre and 'tx_packets' in post and 'tx_packets' in pre else {}
            link_post = parse_station_dump(os.path.join(b, '{}_link_post.txt'.format(sta_pfx)))
            rows.append({
                'scenario': scen, 'algorithm': algo, 'rtt_ms': rtt, 'batch': batch, 'link': label,
                'samples': rs['n'], 'signal_min_dbm': rs['sig_min'], 'signal_med_dbm': rs['sig_med'],
                'signal_max_dbm': rs['sig_max'], 'phy_tx_med_mbps': rs['rate_med'],
                'ul_tx_packets': rs.get('ul_pkts'), 'ul_retry_pct': pct(rs.get('ul_retries', 0), rs.get('ul_pkts', 0)),
                'ul_tx_failed': rs.get('ul_failed'),
                'dl_tx_packets': dl.get('tx_packets'), 'dl_retry_pct': pct(dl.get('tx_retries', 0), dl.get('tx_packets', 0)) if dl else float('nan'),
                'dl_tx_failed': dl.get('tx_failed'), 'ap_signal_dbm_post': post.get('signal'),
                'sta_link_signal_post': link_post.get('signal'),
            })
    if not rows:
        print('no batch with radio_*.csv under', a.root)
        return 1
    if a.csv:
        with open(a.csv, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    scen_order = sorted({r['scenario'] for r in rows})
    for scen in scen_order:
        print('== %s' % scen)
        print('%-14s %7s %8s %8s %8s %7s %9s %8s %9s %8s %-5s' % (
            'link', 'batches', 'sig_min', 'sig_med', 'sig_max', 'phy_med', 'ul_retry%', 'ul_fail', 'dl_retry%', 'dl_fail', 'verdict'))
        for label, *_ in LINKS:
            sel = [r for r in rows if r['scenario'] == scen and r['link'] == label]
            if not sel:
                continue
            ul_num = sum((r['ul_retry_pct'] / 100.0) * (r['ul_tx_packets'] or 0) for r in sel if r['ul_retry_pct'] == r['ul_retry_pct'])
            ul_den = sum(r['ul_tx_packets'] or 0 for r in sel)
            dl_sel = [r for r in sel if r['dl_tx_packets']]
            dl_num = sum((r['dl_retry_pct'] / 100.0) * r['dl_tx_packets'] for r in dl_sel)
            dl_den = sum(r['dl_tx_packets'] for r in dl_sel)
            sig_min = min(r['signal_min_dbm'] for r in sel)
            ul_p, dl_p = pct(ul_num, ul_den), (pct(dl_num, dl_den) if dl_den else float('nan'))
            phy = [r['phy_tx_med_mbps'] for r in sel if r['phy_tx_med_mbps'] is not None]
            print('%-14s %7d %8d %8.0f %8d %7s %9.2f %8d %9s %8s %-5s' % (
                label, len(sel), sig_min, st.median(r['signal_med_dbm'] for r in sel),
                max(r['signal_max_dbm'] for r in sel), ('%.0f' % st.median(phy)) if phy else '-',
                ul_p, sum(r['ul_tx_failed'] or 0 for r in sel),
                ('%.2f' % dl_p) if dl_p == dl_p else '-', sum(r['dl_tx_failed'] or 0 for r in dl_sel) if dl_sel else '-',
                verdict(sig_min, ul_p, dl_p)))
        print()
    print('verdict: good = signal >= -65 dBm and MAC retries < 1 %; fair = >= -75 dBm and < 5 %; weak otherwise.')
    print('ul = station -> AP (radio_*.csv of the station), dl = AP -> station (iw station dump on the AP side).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
