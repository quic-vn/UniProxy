#!/usr/bin/env python3
"""Continuous bulk workload for path-failure / recovery runs (IoT runners v9+); drop-in for client.py.

Same server request as client.py ("rate R"), same 10 s warm-up + 3 s pause, but then --conns
connections download continuously for --duration s (one long load instead of 10 tests of 10 s
with 3 s gaps), so that a path outage and the recovery after it are seen on an uninterrupted
per-second timeline. Received bytes are counted per second of the load (all connections summed).

Outputs
  ./throughput.csv       --windows lines: aggregate Mbit/s of consecutive equal windows of the load
                         (default 10 x 12 s), so the runners' validation and analyzer work unchanged.
                         A window without any byte is written as 0.001 (floor: a real stall is
                         recorded instead of the attempt being rejected and retried).
  <log-dir>/rate_1s.csv  second, epoch (start of that second), mbps
  <log-dir>/load_start.txt  epoch at which the load started.
Failures are printed without Python exception names (the runners reject client logs that contain
"Traceback"/"TimeoutError"/"ConnectionResetError").
"""
import argparse
import csv
import os
import socket
import threading
import time

BUFFER = 65536


def warmup(host, port, seconds):
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


def conn_worker(args, idx, t_start, t_end, buckets, lock, status):
    try:
        with socket.create_connection((args.host, args.port), timeout=30) as s:
            s.settimeout(30)
            s.sendall(b'rate %d\n' % int(args.rate))
            while True:
                now = time.monotonic()
                if now >= t_end:
                    break
                d = s.recv(BUFFER)
                if not d:
                    status[idx] = 'closed'
                    return
                sec = int(time.monotonic() - t_start)
                if sec < len(buckets):
                    with lock:
                        buckets[sec] += len(d)
        status[idx] = 'ok'
    except socket.timeout:
        status[idx] = 'timeout'
    except OSError as e:
        status[idx] = 'error_%s' % (e.errno if e.errno is not None else 'x')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--host', default='10.0.7.2')
    p.add_argument('--port', type=int, default=5000)
    p.add_argument('--conns', type=int, default=5, help='parallel connections (as client.py num); default 5')
    p.add_argument('--duration', type=int, default=120, help='seconds of continuous load; default 120')
    p.add_argument('--rate', type=float, default=150e6, help='requested rate per connection (bit/s)')
    p.add_argument('--windows', type=int, default=10, help='lines of throughput.csv; default 10')
    p.add_argument('--warmup', type=float, default=10.0)
    p.add_argument('--pause', type=float, default=3.0)
    p.add_argument('--log-dir', default='.')
    a = p.parse_args()
    if a.duration % a.windows:
        raise SystemExit('--duration must be a multiple of --windows')
    os.makedirs(a.log_dir, exist_ok=True)
    warmup(a.host, a.port, a.warmup)
    time.sleep(a.pause)

    buckets = [0] * a.duration
    lock = threading.Lock()
    status = ['running'] * a.conns
    t_start = time.monotonic()
    epoch0 = time.time()
    open(os.path.join(a.log_dir, 'load_start.txt'), 'w').write('%.6f\n' % epoch0)
    t_end = t_start + a.duration
    threads = [threading.Thread(target=conn_worker, args=(a, i, t_start, t_end, buckets, lock, status), daemon=True)
               for i in range(a.conns)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(a.duration + 40)
    print('connections: %s' % ' '.join(status), flush=True)

    with open(os.path.join(a.log_dir, 'rate_1s.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['second', 'epoch', 'mbps'])
        for k, b in enumerate(buckets):
            w.writerow([k, '%.3f' % (epoch0 + k), '%.4f' % (b * 8 / 1e6)])
    wl = a.duration // a.windows
    out = []
    for i in range(a.windows):
        v = sum(buckets[i * wl:(i + 1) * wl]) * 8 / wl / 1e6
        if v <= 0:
            print('window %d: no data, floor 0.001 recorded' % (i + 1), flush=True)
            v = 0.001
        out.append(v)
        print('Window %d: %.3f Mbit/s' % (i + 1, v), flush=True)
    with open('./throughput.csv', 'w', newline='') as f:
        csv.writer(f).writerows([[v] for v in out])


if __name__ == '__main__':
    main()
