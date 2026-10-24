#!/usr/bin/env python3
"""Tong hop cac lan chay fqsat_manual_test.py.

Dung:  python3 experiments/summarize_runs.py <danh_sach>
<danh_sach>: moi dong "<kich_ban> <FQ_ENABLE> <thu_muc>" (hoac "<FQ_ENABLE> <thu_muc>").
Long : trung binh cua so 3-6 (suy giam), 4, 7, 8-10 (sau phuc hoi), ca lan; so dong FQSAT.
FCT  : p50/p95 FCT theo kich thuoc (ms), so flow loi, throughput bulk nen; so dong FQSAT.
Moi kich ban in tung lan chay roi trung binh +- do lech chuan theo FQ_ENABLE.
"""
import csv
import math
import os
import sys
from collections import OrderedDict, defaultdict


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return float('nan')
    k = (len(xs) - 1) * q
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def n_flag(d):
    p = os.path.join(d, 'r5_server.log')
    if not os.path.exists(p):
        return float('nan')
    return sum(1 for l in open(p, errors='replace') if 'FQSAT:' in l)


def long_metrics(d):
    p = os.path.join(d, 'throughput.csv')
    a = [float(x) for x in open(p).read().split()]
    if len(a) < 10:
        return None
    return OrderedDict([('suy_giam_3-6', sum(a[2:6]) / 4), ('cs4', a[3]), ('cs7', a[6]),
                        ('sau_phuc_hoi_8-10', sum(a[7:10]) / 3), ('ca_lan', sum(a[:10]) / 10),
                        ('FQSAT', n_flag(d))])


def fct_metrics(d):
    p = os.path.join(d, 'fct.csv')
    if not os.path.exists(p):
        return None
    rows = list(csv.DictReader(open(p)))
    m = OrderedDict()
    allok = []
    for size, tag in ((10000, '10k'), (100000, '100k'), (1000000, '1M')):
        ok = [float(r['fct_s']) * 1000 for r in rows if int(r['size_bytes']) == size and r['status'] == 'ok']
        allok += ok
        m['p50_' + tag] = pct(ok, .5)
        m['p95_' + tag] = pct(ok, .95)
    m['mean_all'] = sum(allok) / len(allok) if allok else float('nan')
    m['loi'] = sum(1 for r in rows if r['status'] != 'ok')
    bg = os.path.join(d, 'bg', 'throughput.csv')
    if os.path.exists(bg):
        v = [float(x) for x in open(bg).read().split()]
        m['bulk_nen'] = sum(v) / len(v) if v else float('nan')
    m['FQSAT'] = n_flag(d)
    return m


def main():
    runs = []
    for line in open(sys.argv[1]):
        f = line.split()
        if len(f) == 2:
            f = ['-'] + f
        if len(f) == 3:
            runs.append(tuple(f))
    by = OrderedDict()
    for scen, mode, d in runs:
        by.setdefault(scen, []).append((mode, d))
    for scen, lst in by.items():
        rows = []
        for mode, d in lst:
            m = fct_metrics(d) if os.path.exists(os.path.join(d, 'fct.csv')) else None
            kind = 'fct'
            if m is None and os.path.exists(os.path.join(d, 'throughput.csv')):
                m, kind = long_metrics(d), 'long'
            rows.append((mode, d, m, kind))
        good = [r for r in rows if r[2]]
        if not good:
            print("== %s: khong doc duoc ket qua" % scen)
            continue
        keys = list(good[0][2].keys())
        unit = 'ms (FCT), Mbit/s (bulk)' if good[0][3] == 'fct' else 'Mbit/s'
        print("== %s (%s, don vi %s)" % (scen, good[0][3], unit))
        print("   %-6s %s  thu_muc" % ("FQ", " ".join("%12s" % k for k in keys)))
        for mode, d, m, _ in rows:
            if m is None:
                print("   %-6s %s" % (mode, "LOI: " + d))
                continue
            print("   %-6s %s  %s" % (mode, " ".join("%12.1f" % m[k] for k in keys), os.path.basename(d)))
        agg = defaultdict(list)
        for mode, _, m, _ in good:
            agg[mode].append(m)
        for mode in ('false', 'true'):
            if mode not in agg:
                continue
            cells = []
            for k in keys:
                v = [x[k] for x in agg[mode] if not math.isnan(x[k])]
                mu = sum(v) / len(v) if v else float('nan')
                sd = math.sqrt(max(0.0, sum(x * x for x in v) / len(v) - mu * mu)) if v else float('nan')
                cells.append("%6.1f+-%-4.1f" % (mu, sd))
            print("   %-6s %s  (n=%d, %s)" % (mode, " ".join("%12s" % c for c in cells), len(agg[mode]),
                                             "TRR goc" if mode == 'false' else "TRR+FQSAT"))
        print()


if __name__ == '__main__':
    main()
