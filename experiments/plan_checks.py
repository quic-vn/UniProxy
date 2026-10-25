#!/usr/bin/env python3
"""Kiem tra tu dong cho tung buoc cua run_plan.sh, doc cac dong PSTAT va log FQSAT.

  python3 experiments/plan_checks.py <buoc> <danh_sach_lan_chay>

Moi dong ket qua co nhan [OK] / [WARN] / [INFO]. WARN la "can xem", khong phai loi chac chan.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pstat  # noqa: E402


def load(d):
    cfg = json.load(open(os.path.join(d, 'config.json')))
    rows = pstat.parse(d)
    t0 = pstat.load_t0(d)
    return cfg, rows, t0


def path_ids(rows):
    return sorted({int(r['p']) for r in rows})


def impaired_pid(cfg, rows):
    pids = path_ids(rows)
    idx = cfg['impair_path'] - 1
    return pids[idx] if len(pids) > idx else pids[-1]


def at(rows, pid, t0, t):
    """Dong PSTAT cua path pid gan thoi diem t0+t nhat (trong +-0.6 s)."""
    best = None
    for r in rows:
        if int(r['p']) != pid:
            continue
        dt = abs(r['ts'] - t0 - t)
        if dt <= 0.6 and (best is None or dt < best[0]):
            best = (dt, r)
    return best[1] if best else None


def ms(v):
    return float(v) if v not in (None, '') else float('nan')


def runs(listfile):
    out = []
    for line in open(listfile):
        f = line.split()
        if len(f) == 3:
            out.append(f)
    return out


def tag(ok):
    return '[OK]  ' if ok else '[WARN]'


def check_common(step, lst):
    """Kiem tra dung cho moi buoc long: co bat/go, srtt sau RESTORE, sender o server."""
    print("-- kiem tra tu dong buoc %s" % step)
    for scen, fq, d in lst:
        if not os.path.exists(os.path.join(d, 'config.json')):
            print("   [WARN] %s: thieu config.json" % os.path.basename(d))
            continue
        cfg, rows, t0 = load(d)
        if cfg.get('workload') != 'long' or not rows or t0 is None:
            continue
        pid = impaired_pid(cfg, rows)
        rw = cfg.get('rwnd_bytes', {}).get('InitialConnectionFlowControlWindow')
        nflag = sum(1 for l in open(os.path.join(d, 'r5_server.log'), errors='replace') if 'FQSAT:' in l)
        name = os.path.basename(d)
        if fq == 'true':
            # 1) so lan doi co
            if rw and rw >= 1000000:
                print("   %s %s: %d dong co (ky vong 0 o rwnd 1 MB)" % (tag(nflag == 0), name, nflag))
            else:
                print("   %s %s: %d dong co (ky vong 2: bat luc IMPAIR, go sau RESTORE)" % (tag(nflag == 2), name, nflag))
            # 2) srtt cua path bi suy giam 8 s sau RESTORE
            base_ms = 2 * float(re.sub('[a-z]', '', cfg['base_delay2' if cfg['impair_path'] == 2 else 'base_delay1']))
            r = at(rows, pid, t0, cfg['restore_at'] + 8)
            if r:
                srtt = ms(r['srtt'])
                print("   %s %s: srtt path %d 8 s sau RESTORE = %.1f ms (RTT goc %.0f ms; ky vong < %.0f)" %
                      (tag(srtt < 2 * base_ms + 2), name, pid, srtt, base_ms, 2 * base_ms + 2))
            # 3) sender=true o server trong luc tai
            win = [x for x in rows if cfg['impair_at'] + 1 <= x['ts'] - t0 <= cfg['restore_at'] and 'sender' in x]
            if win:
                frac = sum(1 for x in win if x['sender'] == 'true') / len(win)
                print("   %s %s: sender=true trong %.0f%% so giay (ky vong > 90%%)" % (tag(frac > 0.9), name, frac * 100))
        else:
            print("   [INFO] %s: TRR goc, %d dong co" % (name, nflag))


def check_step2(lst):
    """Quet rwnd: TRR goc co tut khong, FQSAT co gan co khong, theo tung muc rwnd."""
    print("-- quet rwnd: cua so 4 (cua so dau sau IMPAIR) theo muc rwnd")
    by = {}
    for scen, fq, d in lst:
        tp = os.path.join(d, 'throughput.csv')
        if not os.path.exists(tp):
            continue
        a = [float(x) for x in open(tp).read().split()]
        rw = scen.split('_rw')[-1]
        nflag = sum(1 for l in open(os.path.join(d, 'r5_server.log'), errors='replace') if 'FQSAT:' in l)
        by.setdefault((scen.split('_rw')[0], int(rw)), {}).setdefault(fq, []).append((a[3], a[1], nflag))
    print("   %-4s %8s %14s %14s %10s  nhan xet" % ("kb", "rwnd", "cs4 TRR", "cs4 FQSAT", "co FQSAT"))
    for (s, rw) in sorted(by):
        f = by[(s, rw)].get('false', [])
        t = by[(s, rw)].get('true', [])
        if not f or not t:
            continue
        cs4f = sum(x[0] for x in f) / len(f)
        cs4t = sum(x[0] for x in t) / len(t)
        pre = sum(x[1] for x in f) / len(f)   # cua so 2 = truoc suy giam
        flags = sum(x[2] for x in t) / len(t)
        drop = (pre - cs4f) / pre * 100 if pre else 0
        note = ""
        if drop > 5 and flags == 0:
            note = "TRR tut %.0f%% ma FQSAT khong gan co => FQ_FILL_ON=0.8 qua chat o muc nay" % drop
        elif drop > 5 and cs4t > cs4f * 1.02:
            note = "TRR tut %.0f%%, FQSAT giu duoc" % drop
        elif drop <= 5 and flags > 0:
            note = "TRR khong tut nhung FQSAT van gan co: kiem tra co gay hai khong"
        else:
            note = "khong khac biet dang ke"
        print("   %-4s %8d %14.1f %14.1f %10.1f  %s" % (s, rw, cs4f, cs4t, flags, note))


def check_step4(lst):
    """FCT: so p95 va so flow loi; canh bao neu p50 flow nho xau di."""
    print("-- short flow: ky vong S7 bang nhau; S1x: FQSAT giam p95_100k va so loi")
    by = {}
    for scen, fq, d in lst:
        fp = os.path.join(d, 'fct.csv')
        if not os.path.exists(fp):
            continue
        import csv
        rows = list(csv.DictReader(open(fp)))
        ok100 = sorted(float(r['fct_s']) * 1000 for r in rows if int(r['size_bytes']) == 100000 and r['status'] == 'ok')
        ok10 = sorted(float(r['fct_s']) * 1000 for r in rows if int(r['size_bytes']) == 10000 and r['status'] == 'ok')
        err = sum(1 for r in rows if r['status'] != 'ok')
        p95 = ok100[int(0.95 * (len(ok100) - 1))] if ok100 else float('nan')
        p50s = ok10[len(ok10) // 2] if ok10 else float('nan')
        by.setdefault(scen, {}).setdefault(fq, []).append((p95, p50s, err))
    for scen in by:
        f, t = by[scen].get('false', []), by[scen].get('true', [])
        if not f or not t:
            continue
        m = lambda xs, i: sum(x[i] for x in xs) / len(xs)
        line = "   %-14s p95_100k %.0f -> %.0f ms, p50_10k %.0f -> %.0f ms, loi %.1f -> %.1f" % (
            scen, m(f, 0), m(t, 0), m(f, 1), m(t, 1), m(f, 2), m(t, 2))
        if scen.startswith('S7'):
            print("   %s %s" % (tag(abs(m(t, 0) - m(f, 0)) < 0.1 * max(m(f, 0), 1)), line.strip()))
        else:
            better = m(t, 0) < m(f, 0) and m(t, 2) <= m(f, 2)
            worse50 = m(t, 1) > 1.2 * m(f, 1)
            print("   %s %s%s" % (tag(better), line.strip(), "  (p50 flow 10k xau di >20%: xem FQ_RANK_LOSS_MS)" if worse50 else ""))


def check_step5(lst):
    """MODE=both: cwnd path 1 o giay -12 phai nhu TRR goc (khong con tac dung phu cua client)."""
    print("-- FQSAT o ca hai phia: cwnd path nhanh o giay -12 (ky vong ~110 KB nhu TRR goc, khong phai ~170 KB)")
    for scen, fq, d in lst:
        if not os.path.exists(os.path.join(d, 'config.json')):
            continue
        cfg, rows, t0 = load(d)
        if not rows or t0 is None:
            continue
        pids = path_ids(rows)
        r = at(rows, pids[0], t0, -12)
        if r:
            cw = int(r['cwnd'])
            okk = cw < 140000 if fq == 'true' else True
            print("   %s %s FQ=%s: cwnd path %d @-12s = %d" % (tag(okk), os.path.basename(d), fq, pids[0], cw))


def main():
    step, listfile = sys.argv[1], sys.argv[2]
    lst = runs(listfile)
    if step in ('1', '3', '7'):
        check_common(step, lst)
    elif step == '2':
        check_common(step, lst)
        check_step2(lst)
    elif step == '4':
        check_step4(lst)
    elif step == '5':
        check_common(step, lst)
        check_step5(lst)


if __name__ == '__main__':
    main()
