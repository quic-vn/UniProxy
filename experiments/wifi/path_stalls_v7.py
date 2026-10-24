#!/usr/bin/env python3
"""Detect per-path stalls in run_fig4_iot_v2.sh results from the 1 Hz ifstats logs.

A stall = one path carries only the background pings (<= PKT_IDLE packets/s on
r1's path radio) for >= MIN_STALL_S consecutive seconds while the workload is
running and the other path still carries >= PKT_BUSY packets/s. The Wi-Fi state
at the stall start (signal, MAC tx failures on that radio) is reported so a
proxy/scheduler stall can be told apart from a radio outage.

Usage:
  python3 experiments/wifi/path_stalls.py <result_root or campaign_root>... [--csv out.csv]
Result roots are scanned for figure4a/<ALGO>/rtt_<X>ms/batch_<NN> directories.
"""

import argparse
import csv
import pathlib
import sys

PKT_IDLE = 6      # pings only: 2 pkt/s per ping stream, <= 3 streams touch a path radio
PKT_BUSY = 100
MIN_STALL_S = 10
PATH_IF = {'path1': 'r1-wlan1', 'path2': 'r1-wlan2'}


def load_counts(path, key):
    rows = [(float(r['epoch']), int(r[key])) for r in csv.DictReader(open(path))]
    return {round(a[0]): (b[1] - a[1]) / (b[0] - a[0]) for a, b in zip(rows, rows[1:]) if b[0] > a[0]}


def radio_at(path, t):
    best = None
    for r in csv.DictReader(open(path)):
        if r['peer'] == 'none':
            continue
        e = round(float(r['epoch']))
        if e <= t and (best is None or e > best[0]):
            best = (e, r)
    return best[1] if best else {}


def scan_batch(bdir):
    try:
        ws = float((bdir / 'workload_start.txt').read_text())
        we = float((bdir / 'workload_end.txt').read_text())
    except FileNotFoundError:
        return []
    rx = {}
    for p, intf in PATH_IF.items():
        f = bdir / f'ifstats_{intf}.csv'
        if not f.exists():
            return []
        rx[p] = load_counts(f, 'rx_packets')
    events = []
    for p, other in (('path1', 'path2'), ('path2', 'path1')):
        # run = seconds where p is idle and other is busy; seconds where both are idle
        # (gap between two client.py tests) neither extend nor break the run.
        start, last, busy_s = None, None, 0
        def flush():
            if start is not None and busy_s >= MIN_STALL_S:
                events.append((p, start, last - start + 1))
        for t in range(int(ws) + 1, int(we)):
            p_idle = rx[p].get(t, 0) <= PKT_IDLE
            o_busy = rx[other].get(t, 0) >= PKT_BUSY
            if p_idle and o_busy:
                if start is None:
                    start = t
                last, busy_s = t, busy_s + 1
            elif not p_idle:
                flush()
                start, last, busy_s = None, None, 0
        flush()
    out = []
    for p, t_start, dur in events:
        r = radio_at(bdir / f'radio_{PATH_IF[p]}.csv', t_start)
        out.append({
            'batch_dir': str(bdir), 'path': p,
            'stall_start_s': round(t_start - ws, 1), 'stall_s': dur,
            'reaches_end': t_start + dur >= int(we) - 1,
            'signal_dbm_at_start': r.get('signal_dbm', ''),
            'tx_failed_at_start': r.get('tx_failed', ''),
            'workload_s': round(we - ws, 1),
        })
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('roots', nargs='+')
    ap.add_argument('--csv', help='write events to this CSV')
    a = ap.parse_args()
    batches, skipped, events = 0, 0, []
    for root in a.roots:
        for bdir in sorted(pathlib.Path(root).glob('**/figure4a/*/rtt_*ms/batch_*')):
            if not (bdir / 'throughput.csv').exists():
                continue
            if not all((bdir / f).exists() for f in ('workload_start.txt', 'workload_end.txt',
                                                     'ifstats_r1-wlan1.csv', 'ifstats_r1-wlan2.csv')):
                skipped += 1
                continue
            batches += 1
            events += scan_batch(bdir)
    print(f'batches scanned: {batches} (skipped, no ifstats: {skipped}), stall events: {len(events)}, '
          f'batches with a stall: {len({e["batch_dir"] for e in events})}')
    for e in events:
        rel = pathlib.Path(e['batch_dir'])
        tag = '/'.join(rel.parts[-5:-3]) + ' ' + '/'.join(rel.parts[-3:])
        print(f"  {tag}: {e['path']} stalled at +{e['stall_start_s']}s for {e['stall_s']}s"
              f"{' (to end)' if e['reaches_end'] else ''}; signal {e['signal_dbm_at_start']} dBm, "
              f"tx_failed {e['tx_failed_at_start']}")
    if a.csv and events:
        with open(a.csv, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(events[0].keys()))
            w.writeheader()
            w.writerows(events)
        print('written', a.csv)
    return 0


if __name__ == '__main__':
    sys.exit(main())
