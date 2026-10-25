#!/usr/bin/env bash
# Chay lan luot (theo khoi) cac kich ban x {TRR goc, TRR+FQSAT} x N lan.
#   bash experiments/run_scenarios.sh [N] [ten_kich_ban ...]
# Mac dinh: N=3, kich ban "S1a S1b S2". Bang kich ban: experiments/scenarios.sh. FQSAT bat o CA HAI phia
# (de chi bat o phia gui / quet rwnd: run_sweep.sh). Danh sach lan chay ghi vao /tmp/fqsat_runs_<stamp>.txt
# (cuoi cung chep sang results/fqsat_manual/ neu ghi duoc)
# (dinh dang: <kich_ban> <FQ_ENABLE> <thu_muc>), roi in tong hop bang summarize_runs.py.
set -u
cd "$(dirname "$0")/.."
N="${1:-3}"; shift || true
SCEN=("$@"); [[ ${#SCEN[@]} -gt 0 ]] || SCEN=(S1a S1b S2)

source experiments/scenarios.sh
scen_check "${SCEN[@]}" || exit 2

# sudo khong hoi mat khau giua chung (output bi chuyen huong nen se treo neu hoi): dung "sudo -n".
# May thi nghiem cho phep mn/python3 qua sudo khong mat khau; neu khong, chay "sudo true" truoc.
if ! sudo -n mn --version >/dev/null 2>&1 || ! sudo -n python3 -c pass >/dev/null 2>&1; then
  echo "sudo can mat khau cho mn/python3. Chay 'sudo true' trong terminal nay roi chay lai script."; exit 1
fi
STAMP="$(date +%Y%m%dT%H%M%S)"
LIST="/tmp/fqsat_runs_${STAMP}.txt"; : > "$LIST"   # /tmp: luon ghi duoc, ke ca khi results/ thuoc root
set_fq() { sed -i -E "s/^(\s*FQ_ENABLE\s*=\s*)(true|false)/\1$1/" mp-quic/scheduler.go; }
trap 'set_fq true; echo "FQ_ENABLE tra ve true"' EXIT

for s in "${SCEN[@]}"; do
  for mode in false true; do
    set_fq "$mode"
    if ! bash experiments/verify_fqsat.sh . > /tmp/verify_$$.txt 2>&1; then
      echo "verify FAIL (FQ_ENABLE=$mode):"; grep FAIL /tmp/verify_$$.txt; exit 1
    fi
    for i in $(seq 1 "$N"); do
      echo "== $s FQ_ENABLE=$mode lan $i/$N  ($(date +%H:%M:%S))"
      R="$PWD/results/fqsat_manual/${s}_FQ${mode}_${i}_$(date +%Y%m%dT%H%M%S)"
      sudo -n mn -c >/dev/null 2>&1
      # shellcheck disable=SC2086
      sudo -n python3 experiments/fqsat_manual_test.py ${ARGS[$s]} --out "$R" > /tmp/run_$$.log 2>&1
      echo "$s $mode $R" | tee -a "$LIST"
      [[ -f "$R/summary.txt" ]] || { echo "  !! lan chay loi (khong co summary.txt), xem /tmp/run_$$.log:"; tail -5 /tmp/run_$$.log; }
    done
  done
done
cp "$LIST" "results/fqsat_manual/runs_${STAMP}.txt" 2>/dev/null && LIST="results/fqsat_manual/runs_${STAMP}.txt"
echo; echo "Danh sach: $LIST"
python3 experiments/summarize_runs.py "$LIST"
