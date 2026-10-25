#!/usr/bin/env bash
# Chay tu dong ke hoach kiem chung ban v2 (7 buoc), tuan tu, co the dung roi chay tiep.
#
#   bash experiments/run_plan.sh                 # buoc 1-5 va 7 (buoc 6 Wi-Fi chi chay khi WIFI=1)
#   STEPS="1 2" bash experiments/run_plan.sh     # chon buoc
#   PLAN=results/fqsat_manual/plan_<stamp> bash experiments/run_plan.sh   # chay tiep ke hoach cu
#   DRY=1 bash experiments/run_plan.sh           # chi in cac lenh se chay
#   N=3 (so lan moi o cho buoc 1-5), N7=10 (buoc 7), WIFI=1 (bat buoc 6)
#
# Nen chay trong tmux hoac nohup vi tong thoi gian ~8-9 gio:
#   tmux new -s plan 'bash experiments/run_plan.sh'        (tach ra: Ctrl-b d; vao lai: tmux attach -t plan)
#   nohup bash experiments/run_plan.sh > /tmp/plan.out 2>&1 &
#
# Moi buoc goi run_sweep.sh (tu build, tu tra rwnd=1 MB va FQ_ENABLE=true khi xong). Ket qua:
#   <PLAN>/stepN.out   : toan bo output cua buoc
#   <PLAN>/stepN.list  : danh sach lan chay (dung cho summarize_runs.py / pstat.py)
#   <PLAN>/stepN.done  : danh dau da xong (xoa file nay de chay lai buoc do)
#   <PLAN>/report.txt  : bang tong hop + kiem tra tu dong cua moi buoc (tao lai moi lan ket thuc)
set -u
cd "$(dirname "$0")/.."
STEPS="${STEPS:-1 2 3 4 5 6 7}"
N="${N:-3}"; N7="${N7:-10}"; WIFI="${WIFI:-0}"; DRY="${DRY:-0}"
PLAN="${PLAN:-results/fqsat_manual/plan_$(date +%Y%m%dT%H%M%S)}"

if [[ "$DRY" != 1 ]]; then
  mkdir -p "$PLAN" 2>/dev/null || { sudo -n mkdir -p "$PLAN" && sudo -n chown "$(id -u):$(id -g)" "$PLAN"; } || { echo "khong tao duoc $PLAN"; exit 1; }
  if ! sudo -n mn --version >/dev/null 2>&1 || ! sudo -n python3 -c pass >/dev/null 2>&1; then
    echo "sudo can mat khau cho mn/python3. Chay 'sudo true' truoc roi chay lai."; exit 1
  fi
  if pgrep -f "experiments/run_sweep.sh|experiments/run_scenarios.sh|fqsat_manual_test.py" >/dev/null; then
    echo "Dang co thi nghiem khac chay (run_sweep/run_scenarios/fqsat_manual_test). Dung no truoc."; exit 1
  fi
  exec > >(tee -a "$PLAN/plan.log") 2>&1
fi
echo "### KE HOACH: $PLAN  buoc: $STEPS  N=$N N7=$N7 WIFI=$WIFI  ($(date))"

# run_step <so> <mo ta> <bien moi truong...> -- <lenh...>
run_step() {
  local n="$1" title="$2"; shift 2
  local envs=()
  while [[ $# -gt 0 && "$1" != "--" ]]; do envs+=("$1"); shift; done
  shift || true
  [[ " $STEPS " == *" $n "* ]] || return 0
  if [[ -f "$PLAN/step$n.done" ]]; then echo "--- buoc $n: da xong truoc do, bo qua ($(cat "$PLAN/step$n.done"))"; return 0; fi
  echo; echo "=== BUOC $n: $title"; echo "    $(printf '%s ' "${envs[@]}")$*"; echo "    bat dau $(date)"
  [[ "$DRY" == 1 ]] && return 0
  local t0=$SECONDS
  env "${envs[@]}" "$@" 2>&1 | tee "$PLAN/step$n.out"
  local rc=${PIPESTATUS[0]}
  local list; list="$(grep -m1 '^Danh sach: ' "$PLAN/step$n.out" | sed 's/^Danh sach: //')"
  if [[ -n "$list" && -f "$list" ]]; then
    cp "$list" "$PLAN/step$n.list"
    echo "$(date +%FT%T) rc=$rc $(( (SECONDS-t0)/60 )) phut list=$list" > "$PLAN/step$n.done"
    echo "    buoc $n xong sau $(( (SECONDS-t0)/60 )) phut"
  elif [[ "$n" == 6 && $rc -eq 0 ]]; then
    echo "$(date +%FT%T) rc=$rc $(( (SECONDS-t0)/60 )) phut (runner wifi)" > "$PLAN/step$n.done"
  else
    echo "    !! buoc $n khong tao duoc danh sach lan chay (rc=$rc). Xem $PLAN/step$n.out. Tiep tuc buoc sau."
  fi
  make_report
}

make_report() {
  [[ "$DRY" == 1 ]] && return 0
  {
    echo "BAO CAO KE HOACH $PLAN  (cap nhat $(date))"
    echo "N=$N  N7=$N7  cac buoc: $STEPS"
    for f in "$PLAN"/step*.done; do [[ -f "$f" ]] && echo "  $(basename "$f" .done): $(cat "$f")"; done
    for n in 1 2 3 4 5 7; do
      [[ -f "$PLAN/step$n.list" ]] || continue
      echo; echo "################ BUOC $n ################"
      python3 experiments/summarize_runs.py -q "$PLAN/step$n.list"
      python3 experiments/plan_checks.py "$n" "$PLAN/step$n.list"
    done
    if [[ -f "$PLAN/step6.done" ]]; then
      echo; echo "################ BUOC 6 (Wi-Fi) ################"
      echo "Ket qua theo runner run_fig4_iot_v9.sh; xem $PLAN/step6.out (dong 'RESULT' / thu muc results cua runner)."
      grep -iE "RESULT_ROOT|results/" "$PLAN/step6.out" | head -5
    fi
  } > "$PLAN/report.txt"
}

# ------------------------------------------------------------------ cac buoc
run_step 1 "xac nhan v2 khong pha ket qua v1 (S3 S4 @ 1 MB va 0.16 MB)" \
  RWNDS="1048576 167772" -- bash experiments/run_sweep.sh "$N" S3 S4

run_step 2 "quet rwnd 0.16/0.25/0.4/0.6/1 MB (S3 S4)" \
  -- bash experiments/run_sweep.sh "$N" S3 S4

run_step 3 "chi co loss: S5 (2%) S6 (5%) @ 0.16 MB va 1 MB" \
  RWNDS="167772 1048576" -- bash experiments/run_sweep.sh "$N" S5 S6

run_step 4 "short flow FCT: S7 (doi chung) S1c S1a S1b @ 1 MB" \
  RWNDS="1048576" -- bash experiments/run_sweep.sh "$N" S7 S1c S1a S1b

run_step 5 "FQSAT o ca hai phia (MODE=both) S4 @ 0.16 MB" \
  MODE=both RWNDS="167772" -- bash experiments/run_sweep.sh "$N" S4

if [[ "$WIFI" == 1 ]]; then
  run_step 6 "Wi-Fi: runner run_fig4_iot_v9.sh voi TRR va TRR_FQ" \
    ALGORITHMS_STR="TRR TRR_FQ" -- bash experiments/wifi/run_fig4_iot_v9.sh
else
  [[ " $STEPS " == *" 6 "* ]] && echo "--- buoc 6 (Wi-Fi) bo qua: dat WIFI=1 de chay"
fi

run_step 7 "lap lai buoc 1 voi $N7 lan moi o" \
  RWNDS="1048576 167772" -- bash experiments/run_sweep.sh "$N7" S3 S4

make_report
echo; echo "### XONG ($(date)). Bao cao: $PLAN/report.txt"
[[ "$DRY" == 1 ]] || cat "$PLAN/report.txt"
