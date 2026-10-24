#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

LABELS = {
    "TRR": "UniProxy",
    "RR": "MPQUIC (RR+MinRTT)",
    "SAPS": "MPQUIC (RR+SAPS)",
    "X": "X",
    "TS": "TS",
}
ALGORITHM_ORDER = ["TRR", "RR", "SAPS", "X", "TS"]
MARKERS = ["o", "s", "^", "D", "v", "P", "X", "<", ">"]

DEFAULT_RTTS = [20, 40, 60, 80, 100]
DEFAULT_RWNDS = [1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 5.0, 10.0]


def parse_args():
    p = argparse.ArgumentParser(
        description="Strict aggregation/validation for UniProxy Figure 4a/4b experiments."
    )
    p.add_argument("result_root", type=Path)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--algorithm", help="Finalize one algorithm/phase from batch data.")
    g.add_argument("--combined", action="store_true",
                   help="Build cumulative plots/CSVs/report from completed phases.")
    p.add_argument("--phase", choices=["4a", "4b", "all"], default="all",
                   help="Phase to finalize when --algorithm is used.")
    p.add_argument("--expected-batches", type=int, default=None)
    p.add_argument("--expected-repeats", type=int, default=None)
    return p.parse_args()


def read_metadata(root: Path) -> dict[str, str]:
    path = root / "metadata.txt"
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def ints_from_metadata(meta: dict[str, str], key: str, default: list[int]) -> list[int]:
    raw = meta.get(key, "").strip()
    if not raw:
        return default[:]
    return [int(float(x)) for x in raw.split()]


def floats_from_metadata(meta: dict[str, str], key: str, default: list[float]) -> list[float]:
    raw = meta.get(key, "").strip()
    if not raw:
        return default[:]
    return [float(x) for x in raw.split()]


def ordered_algorithms(names):
    rank = {name: i for i, name in enumerate(ALGORITHM_ORDER)}
    return sorted(names, key=lambda name: (rank.get(name, len(rank)), name))


def read_values(path: Path) -> list[float]:
    vals: list[float] = []
    with path.open(newline="") as f:
        for row in csv.reader(f):
            if not row:
                continue
            x = float(row[0])
            if not math.isfinite(x) or x <= 0:
                raise ValueError(f"Invalid throughput value {row[0]!r} in {path}")
            vals.append(x)
    return vals


def read_manifest(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def same_float(a, b, tol=1e-9):
    return abs(float(a) - float(b)) <= tol


def validate_manifest(manifest: dict, *, algo: str, phase: str, batch: int,
                      param_name: str, param_value: float, rtt_ms: int, rwnd_mb: float,
                      expected_repeats: int):
    errors = []
    expected = {
        "algorithm": algo,
        "figure": phase,
        "batch": batch,
        "parameter_name": param_name,
        "expected_repeats": expected_repeats,
    }
    for k, v in expected.items():
        if manifest.get(k) != v:
            errors.append(f"manifest {k}={manifest.get(k)!r}, expected {v!r}")

    numeric = {
        "parameter_value": param_value,
        "rtt_ms": rtt_ms,
        "rwnd_mb": rwnd_mb,
    }
    for k, v in numeric.items():
        try:
            if not same_float(manifest.get(k), v):
                errors.append(f"manifest {k}={manifest.get(k)!r}, expected {v!r}")
        except Exception:
            errors.append(f"manifest {k} missing/invalid")

    for key in (
        "git_head", "client_binary_sha256", "server_binary_sha256",
        "workload_sha256", "topology_sha256", "build_manifest_sha256",
        "runner_sha256", "started_utc", "finished_utc",
    ):
        if not manifest.get(key):
            errors.append(f"manifest missing {key}")
    return errors


def expected_batch_dirs(base: Path, expected_batches: int) -> set[str]:
    return {f"batch_{b:02d}" for b in range(1, expected_batches + 1)}


def strict_collect_4a(root: Path, algo: str, expected_batches: int, expected_repeats: int,
                      expected_rtts: list[int]):
    rows = []
    errors = []
    base = root / "figure4a" / algo
    expected_param_dirs = {f"rtt_{rtt}ms" for rtt in expected_rtts}
    actual_param_dirs = {p.name for p in base.glob("rtt_*ms") if p.is_dir()} if base.exists() else set()

    missing = expected_param_dirs - actual_param_dirs
    extra = actual_param_dirs - expected_param_dirs
    if missing:
        errors.append(f"Figure 4a missing RTT directories: {sorted(missing)}")
    if extra:
        errors.append(f"Figure 4a unexpected RTT directories: {sorted(extra)}")

    for rtt in expected_rtts:
        pdir = base / f"rtt_{rtt}ms"
        actual_batches = {p.name for p in pdir.glob("batch_*") if p.is_dir()} if pdir.exists() else set()
        exp_batches = expected_batch_dirs(pdir, expected_batches)
        miss_b = exp_batches - actual_batches
        extra_b = actual_batches - exp_batches
        if miss_b:
            errors.append(f"4a RTT={rtt}: missing batches {sorted(miss_b)}")
        if extra_b:
            errors.append(f"4a RTT={rtt}: unexpected batches {sorted(extra_b)}")

        for batch in range(1, expected_batches + 1):
            bdir = pdir / f"batch_{batch:02d}"
            csvp = bdir / "throughput.csv"
            manp = bdir / "manifest.json"
            if not csvp.exists() or not manp.exists():
                if csvp.exists() != manp.exists():
                    errors.append(f"4a RTT={rtt} batch={batch}: throughput/manifest pair incomplete")
                continue
            try:
                vals = read_values(csvp)
            except Exception as e:
                errors.append(f"4a RTT={rtt} batch={batch}: {e}")
                continue
            if len(vals) != expected_repeats:
                errors.append(
                    f"4a RTT={rtt} batch={batch}: {len(vals)} repeats, expected {expected_repeats}"
                )
                continue
            try:
                manifest = read_manifest(manp)
                errors.extend(
                    f"4a RTT={rtt} batch={batch}: {msg}"
                    for msg in validate_manifest(
                        manifest, algo=algo, phase="4a", batch=batch,
                        param_name="rtt_ms", param_value=float(rtt),
                        rtt_ms=rtt, rwnd_mb=1.0,
                        expected_repeats=expected_repeats,
                    )
                )
            except Exception as e:
                errors.append(f"4a RTT={rtt} batch={batch}: invalid manifest: {e}")
                continue

            for i, value in enumerate(vals, 1):
                rows.append({
                    "algorithm": algo,
                    "rtt_ms": rtt,
                    "batch": batch,
                    "repeat_in_batch": i,
                    "global_repeat": (batch - 1) * expected_repeats + i,
                    "throughput_mbps": value,
                })
    return rows, errors


def strict_collect_4b(root: Path, algo: str, expected_batches: int, expected_repeats: int,
                      expected_rwnds: list[float]):
    rows = []
    errors = []
    base = root / "figure4b" / algo
    expected_param_dirs = {f"rwnd_{fmt_num(x)}MB" for x in expected_rwnds}
    actual_param_dirs = {p.name for p in base.glob("rwnd_*MB") if p.is_dir()} if base.exists() else set()

    missing = expected_param_dirs - actual_param_dirs
    extra = actual_param_dirs - expected_param_dirs
    if missing:
        errors.append(f"Figure 4b missing RWND directories: {sorted(missing)}")
    if extra:
        errors.append(f"Figure 4b unexpected RWND directories: {sorted(extra)}")

    for rwnd in expected_rwnds:
        pdir = base / f"rwnd_{fmt_num(rwnd)}MB"
        actual_batches = {p.name for p in pdir.glob("batch_*") if p.is_dir()} if pdir.exists() else set()
        exp_batches = expected_batch_dirs(pdir, expected_batches)
        miss_b = exp_batches - actual_batches
        extra_b = actual_batches - exp_batches
        if miss_b:
            errors.append(f"4b RWND={fmt_num(rwnd)}: missing batches {sorted(miss_b)}")
        if extra_b:
            errors.append(f"4b RWND={fmt_num(rwnd)}: unexpected batches {sorted(extra_b)}")

        for batch in range(1, expected_batches + 1):
            bdir = pdir / f"batch_{batch:02d}"
            csvp = bdir / "throughput.csv"
            manp = bdir / "manifest.json"
            if not csvp.exists() or not manp.exists():
                if csvp.exists() != manp.exists():
                    errors.append(f"4b RWND={fmt_num(rwnd)} batch={batch}: throughput/manifest pair incomplete")
                continue
            try:
                vals = read_values(csvp)
            except Exception as e:
                errors.append(f"4b RWND={fmt_num(rwnd)} batch={batch}: {e}")
                continue
            if len(vals) != expected_repeats:
                errors.append(
                    f"4b RWND={fmt_num(rwnd)} batch={batch}: {len(vals)} repeats, expected {expected_repeats}"
                )
                continue
            try:
                manifest = read_manifest(manp)
                errors.extend(
                    f"4b RWND={fmt_num(rwnd)} batch={batch}: {msg}"
                    for msg in validate_manifest(
                        manifest, algo=algo, phase="4b", batch=batch,
                        param_name="rwnd_mb", param_value=float(rwnd),
                        rtt_ms=100, rwnd_mb=float(rwnd),
                        expected_repeats=expected_repeats,
                    )
                )
            except Exception as e:
                errors.append(f"4b RWND={fmt_num(rwnd)} batch={batch}: invalid manifest: {e}")
                continue

            for i, value in enumerate(vals, 1):
                rows.append({
                    "algorithm": algo,
                    "rwnd_mb": float(rwnd),
                    "batch": batch,
                    "repeat_in_batch": i,
                    "global_repeat": (batch - 1) * expected_repeats + i,
                    "throughput_mbps": value,
                })
    return rows, errors


# Student-t two-sided 95% critical values (0.975 quantile) for df 1..30.
_T975 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
    6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
    16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
    26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
}


def tcrit95(n: int) -> float:
    if n <= 1:
        return math.nan
    df = n - 1
    if df in _T975:
        return _T975[df]
    if df <= 40:
        return 2.021
    if df <= 60:
        return 2.000
    if df <= 120:
        return 1.980
    return 1.960


def summarize(rows, xfield: str):
    by_batch = defaultdict(list)
    for r in rows:
        by_batch[(r["algorithm"], r[xfield], int(r["batch"]))].append(float(r["throughput_mbps"]))

    batch_rows = []
    grouped_batch_means = defaultdict(list)
    raw_by_point = defaultdict(list)

    for (algo, x, batch), vals in sorted(by_batch.items()):
        m = statistics.mean(vals)
        grouped_batch_means[(algo, x)].append(m)
        raw_by_point[(algo, x)].extend(vals)
        batch_rows.append({
            "algorithm": algo,
            xfield: x,
            "batch": batch,
            "n_repeats": len(vals),
            "batch_mean_mbps": m,
        })

    summary = []
    for (algo, x), means in sorted(grouped_batch_means.items(),
                                    key=lambda kv: (kv[0][0], float(kv[0][1]))):
        raw = raw_by_point[(algo, x)]
        final_mean = statistics.mean(means)
        raw_mean = statistics.mean(raw)
        raw_median = statistics.median(raw)
        raw_sd = statistics.stdev(raw) if len(raw) > 1 else 0.0
        batch_sd = statistics.stdev(means) if len(means) > 1 else 0.0
        sem = batch_sd / math.sqrt(len(means)) if len(means) > 1 else math.nan
        tc = tcrit95(len(means))
        ci = tc * sem if len(means) > 1 else math.nan
        cv = (batch_sd / final_mean * 100.0) if final_mean else math.nan
        summary.append({
            "algorithm": algo,
            xfield: x,
            "n_raw_repeats": len(raw),
            "n_batches": len(means),
            "mean_mbps": final_mean,
            "raw_mean_mbps": raw_mean,
            "median_mbps": raw_median,
            "raw_sd_mbps": raw_sd,
            "batch_mean_sd_mbps": batch_sd,
            "batch_mean_sem_mbps": sem,
            "student_t_critical_95": tc,
            "batch_mean_ci95_mbps": ci,
            "batch_mean_cv_pct": cv,
        })
    return summary, batch_rows


SUMMARY_COMMON_FIELDS = [
    "algorithm", "n_raw_repeats", "n_batches", "mean_mbps", "raw_mean_mbps",
    "median_mbps", "raw_sd_mbps", "batch_mean_sd_mbps", "batch_mean_sem_mbps",
    "student_t_critical_95", "batch_mean_ci95_mbps", "batch_mean_cv_pct",
]


def write_csv(path: Path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def read_dict_csv(path: Path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def normalize_summary(rows, xfield):
    out = []
    for r in rows:
        q = dict(r)
        q[xfield] = int(float(r[xfield])) if xfield == "rtt_ms" else float(r[xfield])
        for k in SUMMARY_COMMON_FIELDS:
            if k == "algorithm":
                continue
            if k in q and q[k] not in ("", None):
                q[k] = int(q[k]) if k in ("n_raw_repeats", "n_batches") else float(q[k])
        out.append(q)
    return out


def plot_4a(summary, png: Path, pdf: Path, title=None):
    grouped = defaultdict(list)
    for r in summary:
        grouped[r["algorithm"]].append(r)
    fig, ax = plt.subplots(figsize=(7.4, 4.6))
    for idx, algo in enumerate(ordered_algorithms(grouped)):
        pts = sorted(grouped[algo], key=lambda r: float(r["rtt_ms"]))
        xs = [float(p["rtt_ms"]) for p in pts]
        ys = [float(p["mean_mbps"]) for p in pts]
        errs = [float(p["batch_mean_ci95_mbps"]) for p in pts]
        ax.errorbar(xs, ys, yerr=errs, marker=MARKERS[idx % len(MARKERS)],
                    capsize=3, label=LABELS.get(algo, algo))
    ax.set_xlabel("RTT of Path 2 (ms)")
    ax.set_ylabel("Aggregate Throughput (Mbps)")
    ax.set_xticks(DEFAULT_RTTS)
    if title:
        ax.set_title(title)
    ax.grid(True, alpha=0.25)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(png, dpi=220)
    fig.savefig(pdf)
    plt.close(fig)


def plot_4b(summary, png: Path, pdf: Path, title=None):
    grouped = defaultdict(list)
    for r in summary:
        grouped[r["algorithm"]].append(r)
    x_order = DEFAULT_RWNDS
    xpos = list(range(len(x_order)))
    fig, ax = plt.subplots(figsize=(7.8, 4.6))
    for idx, algo in enumerate(ordered_algorithms(grouped)):
        lookup = {float(r["rwnd_mb"]): r for r in grouped[algo]}
        ys = [float(lookup[x]["mean_mbps"]) for x in x_order]
        errs = [float(lookup[x]["batch_mean_ci95_mbps"]) for x in x_order]
        ax.errorbar(xpos, ys, yerr=errs, marker=MARKERS[idx % len(MARKERS)],
                    capsize=3, label=LABELS.get(algo, algo))
    ax.set_xlabel("Receive Window Size (MB)")
    ax.set_ylabel("Aggregate Throughput (Mbps)")
    ax.set_xticks(xpos, [fmt_num(x) for x in x_order])
    if title:
        ax.set_title(title)
    ax.grid(True, alpha=0.25)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(png, dpi=220)
    fig.savefig(pdf)
    plt.close(fig)


def fmt_num(x):
    x = float(x)
    return str(int(x)) if x.is_integer() else str(x).rstrip("0").rstrip(".")


def phase_paths(root: Path, algo: str, phase: str):
    outdir = root / "summary" / algo
    plots = root / "plots" / algo
    outdir.mkdir(parents=True, exist_ok=True)
    plots.mkdir(parents=True, exist_ok=True)
    if phase == "4a":
        return (
            outdir / "figure4a_raw.csv",
            outdir / "figure4a_batch_means.csv",
            outdir / "figure4a_summary.csv",
            plots / "figure4a.png",
            plots / "figure4a.pdf",
            outdir / ".figure4a.complete",
        )
    return (
        outdir / "figure4b_raw.csv",
        outdir / "figure4b_batch_means.csv",
        outdir / "figure4b_summary.csv",
        plots / "figure4b.png",
        plots / "figure4b.pdf",
        outdir / ".figure4b.complete",
    )


def finalize_phase(root: Path, algo: str, phase: str, expected_batches: int,
                   expected_repeats: int, expected_rtts: list[int], expected_rwnds: list[float]):
    rawp, batchp, sump, png, pdf, marker = phase_paths(root, algo, phase)
    marker.unlink(missing_ok=True)

    if phase == "4a":
        rows, errors = strict_collect_4a(root, algo, expected_batches, expected_repeats, expected_rtts)
        xfield = "rtt_ms"
        raw_fields = ["algorithm", "rtt_ms", "batch", "repeat_in_batch", "global_repeat", "throughput_mbps"]
        batch_fields = ["algorithm", "rtt_ms", "batch", "n_repeats", "batch_mean_mbps"]
        summary_fields = ["algorithm", "rtt_ms"] + SUMMARY_COMMON_FIELDS[1:]
        title = f"Figure 4a — {LABELS.get(algo, algo)}"
    else:
        rows, errors = strict_collect_4b(root, algo, expected_batches, expected_repeats, expected_rwnds)
        xfield = "rwnd_mb"
        raw_fields = ["algorithm", "rwnd_mb", "batch", "repeat_in_batch", "global_repeat", "throughput_mbps"]
        batch_fields = ["algorithm", "rwnd_mb", "batch", "n_repeats", "batch_mean_mbps"]
        summary_fields = ["algorithm", "rwnd_mb"] + SUMMARY_COMMON_FIELDS[1:]
        title = f"Figure 4b — {LABELS.get(algo, algo)}"

    if errors:
        detail = "\n".join(f"  - {e}" for e in errors)
        raise SystemExit(f"STRICT VALIDATION FAILED for {algo} {phase}:\n{detail}")
    if not rows:
        raise SystemExit(f"No valid data for {algo} {phase}")

    summary, batch_rows = summarize(rows, xfield)
    write_csv(rawp, rows, raw_fields)
    write_csv(batchp, batch_rows, batch_fields)
    write_csv(sump, summary, summary_fields)
    if phase == "4a":
        plot_4a(summary, png, pdf, title)
    else:
        plot_4b(summary, png, pdf, title)
    marker.write_text("ok\n", encoding="utf-8")
    print(f"Finalized {algo} {phase}: strict matrix validation PASS")


def capacity_and_variability_warnings(summary, xfield, capacity, tolerance_pct, cv_warn):
    warnings = []
    for r in summary:
        mean = float(r["mean_mbps"])
        cv = float(r["batch_mean_cv_pct"])
        x = r[xfield]
        if mean > capacity * (1 + tolerance_pct / 100.0):
            warnings.append(
                f"{r['algorithm']} {xfield}={x}: mean {mean:.2f} Mbps exceeds "
                f"{capacity:.1f} Mbps by more than {tolerance_pct:.1f}%."
            )
        if cv > cv_warn:
            warnings.append(
                f"{r['algorithm']} {xfield}={x}: batch-mean CV {cv:.2f}% exceeds {cv_warn:.1f}%."
            )
    return warnings


def point_lookup(summary, xfield):
    return {(r["algorithm"], float(r[xfield])): r for r in summary}


def generate_report(root: Path, all4a, all4b, meta):
    capacity = float(meta.get("theoretical_capacity_mbps", 150))
    tolerance = float(meta.get("capacity_tolerance_pct", 5))
    cv_warn = float(meta.get("cv_warning_pct", 10))
    batches = int(meta.get("batches", 0) or 0)
    repeats = int(meta.get("repeats_per_batch", 0) or 0)

    lines = [
        "# Figure 4a/4b — UniProxy and MPQUIC baselines",
        "",
        "Scope: UniProxy (TRR), MPQUIC (RR+MinRTT), and MPQUIC (RR+SAPS). "
        "MPTCP baselines are intentionally outside this experiment.",
        "",
        "## Experimental unit",
        "",
        f"- {batches} independent Mininet/proxy batches × {repeats} repeated measurements per batch.",
        "- Final means and 95% confidence intervals are computed from batch means.",
        "- 95% CI uses the two-sided Student-t critical value for n_batches−1 degrees of freedom.",
        f"- Theoretical aggregate path capacity used for sanity checking: {capacity:.1f} Mbps.",
        "",
    ]

    def add_table(title, summary, xfield, xlabel):
        if not summary:
            return
        lines.extend([
            f"## {title}",
            "",
            f"| Algorithm | {xlabel} | Mean Mbps | 95% CI | Batch SD | CV % | Capacity use % |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ])
        rank = {name: i for i, name in enumerate(ALGORITHM_ORDER)}
        for r in sorted(summary, key=lambda q: (rank.get(q["algorithm"], len(rank)), float(q[xfield]))):
            mean = float(r["mean_mbps"])
            ci = float(r["batch_mean_ci95_mbps"])
            sd = float(r["batch_mean_sd_mbps"])
            cv = float(r["batch_mean_cv_pct"])
            util = mean / capacity * 100.0
            lines.append(
                f"| {LABELS.get(r['algorithm'], r['algorithm'])} | {fmt_num(r[xfield])} | "
                f"{mean:.2f} | ±{ci:.2f} | {sd:.2f} | {cv:.2f} | {util:.1f} |"
            )
        lines.append("")

    add_table("Figure 4a", all4a, "rtt_ms", "Path-2 RTT (ms)")
    add_table("Figure 4b", all4b, "rwnd_mb", "RWND (MB)")

    def add_improvements(summary, xfield, title):
        if not summary:
            return
        lookup = point_lookup(summary, xfield)
        xs = sorted({float(r[xfield]) for r in summary})
        if not all(("TRR", x) in lookup for x in xs):
            return
        lines.extend([f"## {title}: UniProxy improvement over MPQUIC baselines", ""])
        for baseline in ("RR", "SAPS"):
            if not all((baseline, x) in lookup for x in xs):
                continue
            lines.append(f"**vs {LABELS[baseline]}**")
            lines.append("")
            lines.append(f"| {xfield} | Improvement % |")
            lines.append("|---:|---:|")
            for x in xs:
                trr = float(lookup[("TRR", x)]["mean_mbps"])
                base = float(lookup[(baseline, x)]["mean_mbps"])
                imp = ((trr - base) / base * 100.0) if base else math.nan
                lines.append(f"| {fmt_num(x)} | {imp:.2f} |")
            lines.append("")

    add_improvements(all4a, "rtt_ms", "Figure 4a")
    add_improvements(all4b, "rwnd_mb", "Figure 4b")

    lines.extend(["## Trend checks", ""])
    trend_warnings = []

    if all4a:
        lu = point_lookup(all4a, "rtt_ms")
        for algo in ordered_algorithms({r["algorithm"] for r in all4a}):
            if (algo, 20.0) in lu and (algo, 100.0) in lu:
                first = float(lu[(algo, 20.0)]["mean_mbps"])
                last = float(lu[(algo, 100.0)]["mean_mbps"])
                change = (last - first) / first * 100.0 if first else math.nan
                lines.append(
                    f"- Figure 4a {LABELS.get(algo, algo)}: endpoint change from RTT 20→100 ms = {change:+.2f}%."
                )
        if all(k in lu for k in [("TRR", 20.0), ("TRR", 100.0), ("RR", 20.0), ("RR", 100.0),
                                  ("SAPS", 20.0), ("SAPS", 100.0)]):
            def decline(algo):
                a = float(lu[(algo, 20.0)]["mean_mbps"])
                b = float(lu[(algo, 100.0)]["mean_mbps"])
                return (a - b) / a * 100.0 if a else math.nan
            dtrr, drr, dsaps = decline("TRR"), decline("RR"), decline("SAPS")
            if dtrr > min(drr, dsaps):
                trend_warnings.append(
                    "Figure 4a qualitative trend warning: UniProxy declines more than at least one MPQUIC baseline "
                    "from RTT 20 to 100 ms; inspect flow control, path creation, warm-up, and tc."
                )

    if all4b:
        lu = point_lookup(all4b, "rwnd_mb")
        for base in ("RR", "SAPS"):
            if all(k in lu for k in [("TRR", 1.0), (base, 1.0), ("TRR", 10.0), (base, 10.0)]):
                gap1 = abs(float(lu[("TRR", 1.0)]["mean_mbps"]) - float(lu[(base, 1.0)]["mean_mbps"]))
                gap10 = abs(float(lu[("TRR", 10.0)]["mean_mbps"]) - float(lu[(base, 10.0)]["mean_mbps"]))
                lines.append(
                    f"- Figure 4b gap vs {LABELS[base]}: RWND 1 MB = {gap1:.2f} Mbps; "
                    f"RWND 10 MB = {gap10:.2f} Mbps."
                )
                if gap10 > gap1 + 1e-9:
                    trend_warnings.append(
                        f"Figure 4b convergence warning: gap between UniProxy and {LABELS[base]} "
                        "is larger at RWND 10 MB than at RWND 1 MB."
                    )
    lines.append("")

    warnings = []
    warnings.extend(capacity_and_variability_warnings(all4a, "rtt_ms", capacity, tolerance, cv_warn))
    warnings.extend(capacity_and_variability_warnings(all4b, "rwnd_mb", capacity, tolerance, cv_warn))
    warnings.extend(trend_warnings)

    audit = root / "client_workload_audit.txt"
    if audit.exists():
        for line in audit.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("WARNING:"):
                warnings.append("Workload audit: " + line.removeprefix("WARNING:").strip())

    lines.extend(["## Automated warnings", ""])
    if warnings:
        lines.extend(f"- {w}" for w in warnings)
    else:
        lines.append("- No automated sanity/trend warnings were triggered.")
    lines.extend([
        "",
        "Automated trend checks are diagnostics, not statistical proof. Final interpretation should also inspect "
        "the batch distributions, confidence intervals, and retained raw logs.",
        "",
    ])

    (root / "report.md").write_text("\n".join(lines), encoding="utf-8")


def combine(root: Path):
    meta = read_metadata(root)
    all4a, all4b = [], []
    raw4a, raw4b = [], []
    batch4a, batch4b = [], []

    summary_root = root / "summary"
    if not summary_root.exists():
        raise SystemExit("No summary directory exists")

    for d in sorted(summary_root.iterdir()):
        if not d.is_dir():
            continue
        if (d / ".figure4a.complete").exists():
            sp = d / "figure4a_summary.csv"
            rp = d / "figure4a_raw.csv"
            bp = d / "figure4a_batch_means.csv"
            if not (sp.exists() and rp.exists() and bp.exists()):
                raise SystemExit(f"Figure 4a marker exists but compact files are incomplete: {d}")
            all4a.extend(normalize_summary(read_dict_csv(sp), "rtt_ms"))
            raw4a.extend(read_dict_csv(rp))
            batch4a.extend(read_dict_csv(bp))
        if (d / ".figure4b.complete").exists():
            sp = d / "figure4b_summary.csv"
            rp = d / "figure4b_raw.csv"
            bp = d / "figure4b_batch_means.csv"
            if not (sp.exists() and rp.exists() and bp.exists()):
                raise SystemExit(f"Figure 4b marker exists but compact files are incomplete: {d}")
            all4b.extend(normalize_summary(read_dict_csv(sp), "rwnd_mb"))
            raw4b.extend(read_dict_csv(rp))
            batch4b.extend(read_dict_csv(bp))

    if not all4a and not all4b:
        raise SystemExit("No completed Figure 4a/4b phase found")

    if all4a:
        write_csv(root / "figure4a_summary.csv", all4a,
                  ["algorithm", "rtt_ms"] + SUMMARY_COMMON_FIELDS[1:])
        write_csv(root / "figure4a_raw.csv", raw4a,
                  ["algorithm", "rtt_ms", "batch", "repeat_in_batch", "global_repeat", "throughput_mbps"])
        write_csv(root / "figure4a_batch_means.csv", batch4a,
                  ["algorithm", "rtt_ms", "batch", "n_repeats", "batch_mean_mbps"])
        plot_4a(all4a, root / "figure4a.png", root / "figure4a.pdf",
                "Figure 4a — UniProxy and MPQUIC baselines")

    if all4b:
        write_csv(root / "figure4b_summary.csv", all4b,
                  ["algorithm", "rwnd_mb"] + SUMMARY_COMMON_FIELDS[1:])
        write_csv(root / "figure4b_raw.csv", raw4b,
                  ["algorithm", "rwnd_mb", "batch", "repeat_in_batch", "global_repeat", "throughput_mbps"])
        write_csv(root / "figure4b_batch_means.csv", batch4b,
                  ["algorithm", "rwnd_mb", "batch", "n_repeats", "batch_mean_mbps"])
        plot_4b(all4b, root / "figure4b.png", root / "figure4b.pdf",
                "Figure 4b — UniProxy and MPQUIC baselines")

    generate_report(root, all4a, all4b, meta)
    print(f"Combined analysis complete: {root}")
    print(f"Report: {root / 'report.md'}")


def main():
    args = parse_args()
    root = args.result_root.resolve()
    if not root.exists():
        raise SystemExit(f"Result root does not exist: {root}")

    meta = read_metadata(root)
    batches = args.expected_batches or int(meta.get("batches", "0") or 0)
    repeats = args.expected_repeats or int(meta.get("repeats_per_batch", "0") or 0)
    if args.algorithm:
        if batches <= 0 or repeats <= 0:
            raise SystemExit("Expected batch/repeat counts are missing; pass --expected-batches/--expected-repeats.")
        rtts = ints_from_metadata(meta, "figure4a_rtts", DEFAULT_RTTS)
        rwnds = floats_from_metadata(meta, "figure4b_rwnds", DEFAULT_RWNDS)
        phases = ["4a", "4b"] if args.phase == "all" else [args.phase]
        for phase in phases:
            finalize_phase(root, args.algorithm, phase, batches, repeats, rtts, rwnds)
    else:
        combine(root)


if __name__ == "__main__":
    main()
