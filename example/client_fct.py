#!/usr/bin/env python3
"""Short-flow workload (flow completion time, FCT) for the IoT runners v9+; drop-in for client.py.

Same server (example/server.py, request "size N"), same outer structure as client.py:
a 10 s bulk warm-up flow ("rate"), 3 s pause, then --rounds rounds separated by 3 s pauses.
Each round is an open-loop Poisson arrival process: flows start at exponential inter-arrival
times of mean 1/--lam s for --round-s seconds; each flow is its own TCP connection (= one MPQUIC
stream through the proxy) that downloads one object whose size is drawn from --sizes
(bytes:probability). Arrival times and sizes come from a fixed seed (+ round index), so every
algorithm / batch sees the same flow sequence. FCT = connect() .. last byte received, i.e. what the
application sees, including the tunnel handshake (stream open + 512 B token + server dial + ack).
After the last arrival of a round the client waits for every flow (at most --flow-timeout s).

Outputs
  ./throughput.csv     one line per round: goodput in Mbit/s = bytes of the completed flows /
                       (last finish - first start). It keeps the runners' validation and analyzer
                       working; with an offered load below capacity it mostly reflects the load.
                       A round without any completed flow is written as 0.001 (floor, so that a real
                       stall is recorded instead of the attempt being rejected and retried).
  <log-dir>/fct.csv    round, flow, size_bytes, start_epoch, start_rel_s, fct_s, status
                       (status ok | short | timeout | error_<errno>).
The log never contains Python exception names (the runners reject attempts whose client log
contains "Traceback"/"TimeoutError"/"ConnectionResetError"); failures are recorded per flow.

Default load: lam 10 flows/s, sizes 10 kB (50 %), 100 kB (35 %), 1 MB (15 %): mean 190 kB, offered
~15.2 Mbit/s (~45 % of the ~34-35.6 Mbit/s of the static v7 configuration).
"""
import argparse
import csv
import os
import random
import socket
import threading
import time

BUFFER = 65536


def parse_sizes(spec):
    out = []
    for part in spec.split(','):
        size, prob = part.split(':')
        out.append((int(float(size)), float(prob)))
    total = sum(p for _, p in out)
    if total <= 0:
        raise SystemExit('--sizes: probabilities must sum to > 0')
    return [(s, p / total) for s, p in out]


def warmup(host, port, seconds):
    """Same warm-up as client.py: one bulk "rate" flow for `seconds` s."""
    try:
        with socket.create_connection((host, port), timeout=30) as s:
            s.settimeout(30)
            s.sendall(b'rate 150000000\n')
            t0 = time.monotonic()
            while time.monotonic() - t0 < seconds:
                if not s.recv(BUFFER):
                    break
    except (OSError, socket.timeout) as e:
        print('warm-up failed: %s' % getattr(e, 'errno', 'timeout'), flush=True)


def one_flow(host, port, size, timeout, rec):
    t0 = time.monotonic()
    rec['start_epoch'] = time.time()
    got = 0
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(b'size %d\n' % size)
            while got < size:
                if time.monotonic() - t0 > timeout:
                    raise socket.timeout()
                d = s.recv(BUFFER)
                if not d:
                    break
                got += len(d)
        rec['fct'] = time.monotonic() - t0
        rec['status'] = 'ok' if got >= size else 'short'
    except socket.timeout:
        rec['fct'] = time.monotonic() - t0
        rec['status'] = 'timeout'
    except OSError as e:
        rec['fct'] = time.monotonic() - t0
        rec['status'] = 'error_%s' % (e.errno if e.errno is not None else 'x')
    rec['bytes'] = got


def run_round(args, sizes, rnd, writer):
    rng = random.Random(args.seed + rnd)
    arrivals, t = [], rng.expovariate(args.lam)
    while t < args.round_s:
        size = rng.choices([s for s, _ in sizes], weights=[p for _, p in sizes])[0]
        arrivals.append((t, size))
        t += rng.expovariate(args.lam)
    recs, threads = [], []
    t_round = time.monotonic()
    for k, (at, size) in enumerate(arrivals):
        delay = t_round + at - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        rec = {'flow': k, 'size': size, 'start_rel': time.monotonic() - t_round}
        th = threading.Thread(target=one_flow, args=(args.host, args.port, size, args.flow_timeout, rec), daemon=True)
        th.start()
        recs.append(rec)
        threads.append(th)
    deadline = time.monotonic() + args.flow_timeout + 5
    for th in threads:
        th.join(max(0.0, deadline - time.monotonic()))
    ok_bytes, first, last = 0, None, None
    for r in recs:
        status = r.get('status', 'timeout')   # still running after the deadline => timeout
        fct = r.get('fct', args.flow_timeout)
        writer.writerow([rnd + 1, r['flow'], r['size'], '%.6f' % r.get('start_epoch', 0.0),
                         '%.4f' % r['start_rel'], '%.6f' % fct, status])
        if status == 'ok':
            ok_bytes += r['size']
            first = r['start_rel'] if first is None else min(first, r['start_rel'])
            last = r['start_rel'] + fct if last is None else max(last, r['start_rel'] + fct)
    n_ok = sum(1 for r in recs if r.get('status') == 'ok')
    if ok_bytes and last > first:
        goodput = ok_bytes * 8 / (last - first) / 1e6
    else:
        goodput = 0.001
        print('round %d: no completed flow, goodput floor 0.001 recorded' % (rnd + 1), flush=True)
    print('Round %d: %d flows, %d ok, goodput %.3f Mbit/s' % (rnd + 1, len(recs), n_ok, goodput), flush=True)
    return goodput


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--host', default='10.0.7.2')
    p.add_argument('--port', type=int, default=5000)
    p.add_argument('--lam', type=float, default=10.0, help='flow arrival rate (flows/s); default 10')
    p.add_argument('--sizes', default='10000:0.5,100000:0.35,1000000:0.15',
                   help='object sizes bytes:probability, comma separated')
    p.add_argument('--rounds', type=int, default=10)
    p.add_argument('--round-s', type=float, default=10.0, help='arrival window of one round (s)')
    p.add_argument('--pause', type=float, default=3.0)
    p.add_argument('--warmup', type=float, default=10.0)
    p.add_argument('--flow-timeout', type=float, default=30.0)
    p.add_argument('--seed', type=int, default=20261006)
    p.add_argument('--log-dir', default='.')
    a = p.parse_args()
    sizes = parse_sizes(a.sizes)
    os.makedirs(a.log_dir, exist_ok=True)
    warmup(a.host, a.port, a.warmup)
    time.sleep(a.pause)
    goodputs = []
    with open(os.path.join(a.log_dir, 'fct.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['round', 'flow', 'size_bytes', 'start_epoch', 'start_rel_s', 'fct_s', 'status'])
        for rnd in range(a.rounds):
            goodputs.append(run_round(a, sizes, rnd, w))
            f.flush()
            time.sleep(a.pause)
    with open('./throughput.csv', 'w', newline='') as f:
        csv.writer(f).writerows([[g] for g in goodputs])


if __name__ == '__main__':
    main()
