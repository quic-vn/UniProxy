# Bang kich ban dung chung cho run_scenarios.sh va run_sweep.sh (source file nay, khong chay truc tiep).
# Moi gia tri la tham so cho experiments/fqsat_manual_test.py.
# Topo: path 1 (r2) 100 Mbit, path 2 (r3) 50 Mbit, RTT goc moi path = 2 x base-delay (mac dinh 10 ms).
declare -A ARGS=(
  # ---- Short flow (FCT), suy giam o PATH NHANH, giu suot lan chay (muc 6: xep hang stream moi) ----
  # S1a: path 1 bi loss 2%, khong them delay; path 2 sach nhung RTT goc 40 ms. 2 luong bulk nen.
  [S1a]="--workload fct --impair-path 1 --base-delay2 20ms --delay 5ms --jitter 0ms --loss 2% --bg-conns 2"
  # S1b: chenh RTT nho hon (10 vs 16 ms), loss lon hon (5%).
  [S1b]="--workload fct --impair-path 1 --base-delay2 8ms --delay 5ms --jitter 0ms --loss 5% --bg-conns 2"
  # S1c: path 1 loss 2%, path 2 RTT goc 16 ms (vung ma +40 ms phat loss lam doi lua chon).
  [S1c]="--workload fct --impair-path 1 --base-delay2 8ms --delay 5ms --jitter 0ms --loss 2% --bg-conns 2"
  # S7 : FCT doi chung, khong suy giam (delay=base, khong jitter/loss), path 2 RTT goc 16 ms.
  [S7]="--workload fct --impair-path 1 --base-delay2 8ms --delay 5ms --jitter 0ms --loss 0% --bg-conns 2"

  # ---- Bulk, suy giam o PATH CHAM (path 2) tu giay 20 den 60 ----
  # S2: chi jitter (delay variability), khong loss.
  [S2]="--workload long --impair-path 2 --delay 20ms --jitter 30ms --loss 0%"
  # S3: chi tang delay (RTT 10 -> 90 ms), khong jitter, khong loss.
  [S3]="--workload long --impair-path 2 --delay 45ms --jitter 0ms --loss 0%"
  # S4: delay 45 ms + jitter 20 ms + loss 2%.
  [S4]="--workload long --impair-path 2 --delay 45ms --jitter 20ms --loss 2%"
  # S5: CHI loss 2%, khong them delay (muc 4: co can tang trong so loss khong?).
  [S5]="--workload long --impair-path 2 --delay 5ms --jitter 0ms --loss 2%"
  # S6: CHI loss 5%.
  [S6]="--workload long --impair-path 2 --delay 5ms --jitter 0ms --loss 5%"
)

# Cac muc cua so nhan (byte) cho quet rwnd (muc 2): 0.16 / 0.25 / 0.4 / 0.6 / 1 MB
RWND_SWEEP="167772 262144 419430 629145 1048576"

scen_check() {
  local s
  for s in "$@"; do
    [[ -n "${ARGS[$s]:-}" ]] || { echo "Khong co kich ban '$s' (co: ${!ARGS[*]})" >&2; return 2; }
  done
}
