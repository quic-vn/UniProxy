#!/usr/bin/env python3
"""Tong hop cac lan chay fqsat_manual_test.py.

Dung:  python3 experiments/summarize_runs.py <danh_sach> [-q]
<danh_sach>: moi dong "<kich_ban> <FQ_ENABLE> <thu_muc>" (hoac "<FQ_ENABLE> <thu_muc>").
-q: chi in dong trung binh va kiem dinh, bo tung lan chay.

Long : trung binh cua so 3-6 (suy giam), 4, 7, 8-10 (sau phuc hoi), ca lan; so dong FQSAT.
FCT  : p50/p95 FCT theo kich thuoc (ms), so flow loi, throughput bulk nen; so dong FQSAT.
Moi kich ban in tung lan chay, roi trung binh +- do lech chuan, khoang tin cay 95% (t) va
p-value Mann-Whitney hai phia (chinh xac khi n1+n2 <= 20, xap xi chuan khi lon hon) cho
TRR goc (false) so voi TRR+FQSAT (true).
"""
import csv
import itertools
import math
import os
import sys
from collections import OrderedDict, defaultdict

# t_{0.975, df} cho df = 1..30; tren 30 dung 1.96
T975 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
        2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
        2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042]


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return float('nan')
    k = (len(xs) - 1) * q
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def mean_sd_ci(v):
    n = len(v)
    if n == 0:
        return float('nan'), float('nan'), float('nan')
    mu = sum(v) / n
    if n == 1:
        return mu, 0.0, float('nan')
    sd = math.sqrt(sum((x - mu) ** 2 for x in v) / (n - 1))  # do lech chuan mau
    t = T975[n - 2] if n - 1 <= 30 else 1.96
    return mu, sd, t * sd / math.sqrt(n)


def ranks(values):
    """Hang trung binh (midrank) cho cac gia tri co the trung nhau."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    r = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def mann_whitney(a, b):
    """p-value hai phia cua kiem dinh Mann-Whitney U. Chinh xac (hoan vi) khi n1+n2 <= 20."""
    n1, n2 = len(a), len(b)
    if n1 == 0 or n2 == 0:
        return float('nan')
    allv = list(a) + list(b)
    r = ranks(allv)
    u_obs = sum(r[:n1]) - n1 * (n1 + 1) / 2
    mu = n1 * n2 / 2
    if n1 + n2 <= 20:
        dev = abs(u_obs - mu)
        idx = range(n1 + n2)
        cnt = tot = 0
        for comb in itertools.combinations(idx, n1):
            u = sum(r[i] for i in comb) - n1 * (n1 + 1) / 2
            tot += 1
            if abs(u - mu) >= dev - 1e-9:
                cnt += 1
        return cnt / tot
    # xap xi chuan co hieu chinh trung lap
    N = n1 + n2
    ties = defaultdict(int)
    for v in allv:
        ties[v] += 1
    tie_term = sum(t ** 3 - t for t in ties.values())
    var = n1 * n2 / 12 * ((N + 1) - tie_term / (N * (N - 1)))
    if var <= 0:
        return 1.0
    z = (abs(u_obs - mu) - 0.5) / math.sqrt(var)
    return math.erfc(z / math.sqrt(2))


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
    args = [a for a in sys.argv[1:] if a != '-q']
    quiet = '-q' in sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    runs = []
    for line in open(args[0]):
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
        if not quiet:
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
                mu, sd, _ = mean_sd_ci(v)
                cells.append("%6.1f+-%-4.1f" % (mu, sd))
            print("   %-6s %s  (n=%d, %s)" % (mode, " ".join("%12s" % c for c in cells), len(agg[mode]),
                                             "TRR goc" if mode == 'false' else "TRR+FQSAT"))
        if 'false' in agg and 'true' in agg:
            cells = []
            for k in keys:
                a = [x[k] for x in agg['false'] if not math.isnan(x[k])]
                b = [x[k] for x in agg['true'] if not math.isnan(x[k])]
                if k == 'FQSAT' or not a or not b:
                    cells.append("%12s" % "")
                    continue
                mua, _, cia = mean_sd_ci(a)
                mub, _, cib = mean_sd_ci(b)
                diff = mub - mua
                rel = diff / mua * 100 if mua else float('nan')
                p = mann_whitney(a, b)
                mark = '*' if p < 0.05 else ' '
                cells.append("%+5.1f%% p=%.2f%s" % (rel, p, mark) if p == p else "%12s" % "")
            print("   %-6s %s  (FQSAT so voi TRR goc; * = p<0.05 Mann-Whitney)" % ("khac", " ".join("%12s" % c for c in cells)))
            cells = []
            for k in keys:
                a = [x[k] for x in agg['false'] if not math.isnan(x[k])]
                b = [x[k] for x in agg['true'] if not math.isnan(x[k])]
                if k == 'FQSAT' or len(a) < 2 or len(b) < 2:
                    cells.append("%12s" % "")
                    continue
                _, _, cia = mean_sd_ci(a)
                _, _, cib = mean_sd_ci(b)
                cells.append("%5.1f / %4.1f" % (cia, cib))
            print("   %-6s %s  (nua rong khoang tin cay 95%%: TRR / FQSAT)" % ("ci95", " ".join("%12s" % c for c in cells)))
        print()


if __name__ == '__main__':
    main()
