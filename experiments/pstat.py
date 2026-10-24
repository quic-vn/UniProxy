#!/usr/bin/env python3
"""Doc cac dong PSTAT trong r5_server.log (moi giay mot dong cho moi path).

  python3 experiments/pstat.py <thu_muc_lan_chay> [...]        # tom tat moi lan chay
  python3 experiments/pstat.py -t <thu_muc_lan_chay>           # them bang tung giay

Tom tat trong giai doan suy giam [impair_at, restore_at] (lay tu config.json):
  share_p : ti le frame du lieu stream gui tren path bi suy giam
  dfr/s   : frame du lieu moi giay tren moi path
  loss    : so packet mat tren path bi suy giam
  fc%     : ti le giay ma ket noi bi cua so nhan chan (fcage < 1000 ms)
  unrel%  : ti le giay path bi suy giam mang co unreliable
"""
import json
import os
import re
import sys
import time

LINE = re.compile(r'^(\d{4}/\d\d/\d\d \d\d:\d\d:\d\d)(?:\.\d+)? PSTAT (.*)$')


def parse(d):
    rows = []
    for l in open(os.path.join(d, 'r5_server.log'), errors='replace'):
        m = LINE.match(l.strip())
        if not m:
            continue
        ts = time.mktime(time.strptime(m.group(1), '%Y/%m/%d %H:%M:%S'))
        kv = dict(x.split('=', 1) for x in m.group(2).split())
        kv['ts'] = ts
        rows.append(kv)
    return rows


def load_t0(d):
    p = os.path.join(d, 'load_start.txt')
    if os.path.exists(p):
        return float(open(p).read().split()[0])
    for l in open(os.path.join(d, 'events.txt')):
        if 'load started' in l:
            return float(l.split('t0=')[1].split()[0])
    return None


def main():
    args = sys.argv[1:]
    table = False
    if args and args[0] == '-t':
        table, args = True, args[1:]
    if not args:
        sys.exit(__doc__)
    print("%-34s %-5s %8s %9s %9s %6s %5s %6s %5s %8s" % ("lan_chay", "FQ", "share_p", "dfr/s_p1", "dfr/s_p2", "loss", "fc%", "unrel%", "pf%", "pf_sau_R"))
    for d in args:
        d = d.rstrip('/')
        cfg = json.load(open(os.path.join(d, 'config.json')))
        rows = parse(d)
        t0 = load_t0(d)
        if not rows or t0 is None:
            print("%-34s (khong co PSTAT/t0 - binary cu?)" % os.path.basename(d))
            continue
        pids = sorted({int(r['p']) for r in rows})
        imp_idx = cfg['impair_path'] - 1           # path 1 = pathID nho hon, path 2 = lon hon
        pimp = pids[imp_idx] if len(pids) > imp_idx else pids[-1]
        lo, hi = (cfg['impair_at'], cfg['restore_at']) if cfg['workload'] == 'long' else (-1e9, 1e9)
        win = [r for r in rows if lo + 1 <= r['ts'] - t0 <= hi]
        by = {p: [r for r in win if int(r['p']) == p] for p in pids}
        n = max(1, len(by[pimp]))
        dfr = {p: sum(int(r['dfr']) for r in by[p]) for p in pids}
        tot = sum(dfr.values()) or 1
        lossr = [int(r['losses']) for r in by[pimp]]
        loss = (lossr[-1] - lossr[0]) if len(lossr) > 1 else 0
        fc = sum(1 for r in by[pimp] if 0 <= int(r['fcage']) < 1000) / n * 100
        un = sum(1 for r in by[pimp] if r['unrel'] == 'true') / n * 100
        pf = sum(1 for r in by[pimp] if r.get('pf') == 'true') / n * 100
        # sau RESTORE: path bi suy giam con pf o giay cuoi cung khong? (path "chet vinh vien")
        tail = [r for r in rows if int(r['p']) == pimp]
        pf_end = ('CON pf' if tail and tail[-1].get('pf') == 'true' else 'het pf') if cfg['workload'] == 'long' else '-'
        print("%-34s %-5s %7.1f%% %9.0f %9.0f %6d %4.0f%% %5.0f%% %4.0f%% %8s" % (
            os.path.basename(d), cfg.get('fq_enable'), dfr[pimp] / tot * 100,
            dfr[pids[0]] / n, dfr[pids[-1]] / n, loss, fc, un, pf, pf_end))
        if table:
            print("   %6s " % "t" + " | ".join("p%-2d %5s %6s %6s %7s %7s %5s %3s %4s %6s" % (
                p, "dfr", "srtt", "lrtt", "cwnd", "inf", "dLoss", "pf", "unrl", "pen") for p in pids) + " | fcage")
            prev = {}
            for ts in sorted({r['ts'] for r in rows}):
                cells = []
                fca = ''
                for p in pids:
                    r = next((x for x in rows if x['ts'] == ts and int(x['p']) == p), None)
                    if r is None:
                        cells.append(" " * 62)
                        continue
                    ls = int(r['losses'])
                    dl = ls - prev.get(p, ls)
                    prev[p] = ls
                    cells.append("p%-2d %5s %6s %6s %7s %7s %5d %3s %4s %6s" % (
                        p, r['dfr'], r['srtt'], r['lrtt'], r['cwnd'], r['inf'], dl,
                        'Y' if r.get('pf') == 'true' else '.', 'Y' if r['unrel'] == 'true' else '.', r['pen']))
                    fca = r['fcage']
                mark = ''
                if cfg['workload'] == 'long':
                    if abs(ts - t0 - cfg['impair_at']) < 0.5:
                        mark = '  <- IMPAIR'
                    elif abs(ts - t0 - cfg['restore_at']) < 0.5:
                        mark = '  <- RESTORE'
                print("   %+6.0f " % (ts - t0) + " | ".join(cells) + " | %s%s" % (fca, mark))
            print()


if __name__ == '__main__':
    main()
