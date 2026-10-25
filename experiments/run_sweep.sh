#!/usr/bin/env bash
# Chay cac kich ban x cac muc cua so nhan (rwnd) x {TRR goc, TRR+FQSAT} x N lan, theo khoi.
# FQSAT chi bat o PHIA GUI DU LIEU (server proxy r5); client proxy r1 luon la TRR goc
# (MODE=both de bat o ca hai phia).
#
#   bash experiments/run_sweep.sh [N] [kich_ban ...]
#   RWNDS="167772 1048576" bash experiments/run_sweep.sh 3 S3 S4      # chon muc rwnd
#   MODE=both bash experiments/run_sweep.sh 3 S4                      # FQSAT o ca hai phia
#
# Mac dinh: N=3, kich ban "S3 S4", RWNDS = RWND_SWEEP trong scenarios.sh.
# Nhan kich ban trong bang tong hop: <kich_ban>_rw<rwnd>. Ket qua: results/fqsat_manual/sweep_*.
# Khi ket thuc (ke ca Ctrl-C): rwnd ve 1 MB, FQ_ENABLE=true, build lai.
set -u
cd "$(dirname "$0")/.."
source experiments/scenarios.sh
N="${1:-3}"; shift || true
SCEN=("$@"); [[ ${#SCEN[@]} -gt 0 ]] || SCEN=(S3 S4)
scen_check "${SCEN[@]}" || exit 2
RWNDS="${RWNDS:-$RWND_SWEEP}"
MODE="${MODE:-server}"

if ! sudo -n mn --version >/dev/null 2>&1 || ! sudo -n python3 -c pass >/dev/null 2>&1; then
  echo "sudo can mat khau cho mn/python3. Chay 'sudo true' truoc roi chay lai."; exit 1
fi

setfq() { sed -i -E "s/^(\s*FQ_ENABLE\s*=\s*)(true|false)/\1$1/" mp-quic/scheduler.go; }
set_rwnd() {
  sed -i -E "s/^(const (InitialConnectionFlowControlWindow ByteCount|ReceiveConnectionFlowControlWindow|DefaultMaxReceiveConnectionFlowControlWindow(Server|Client)) = )[0-9]+/\1$1/" \
    mp-quic/internal/protocol/protocol.go mp-quic/internal/protocol/server_parameters.go
}
build() { bash experiments/verify_fqsat.sh . > /tmp/verify_sweep_$$.txt 2>&1 || { echo "verify FAIL:"; grep FAIL /tmp/verify_sweep_$$.txt; exit 1; }; }
restore() { set_rwnd 1048576; setfq true; build; echo "da tra rwnd 1 MB, FQ_ENABLE=true, build lai"; }
trap restore EXIT

STAMP="$(date +%Y%m%dT%H%M%S)"
LIST="/tmp/fqsat_sweep_${STAMP}.txt"; : > "$LIST"
B="/tmp/fqbins_sweep_$$"; mkdir -p "$B"

for rw in $RWNDS; do
  set_rwnd "$rw"
  setfq false; build; cp quic-tun/server/cmd/cmd "$B/server_off"; cp quic-tun/client/cmd/cmd "$B/client_off"
  setfq true;  build; cp quic-tun/server/cmd/cmd "$B/server_on";  cp quic-tun/client/cmd/cmd "$B/client_on"
  for s in "${SCEN[@]}"; do
    for mode in off on; do
      cp "$B/server_$mode" quic-tun/server/cmd/cmd
      if [[ "$MODE" == both ]]; then cp "$B/client_$mode" quic-tun/client/cmd/cmd; else cp "$B/client_off" quic-tun/client/cmd/cmd; fi
      fq=$([ "$mode" = on ] && echo true || echo false)
      for i in $(seq 1 "$N"); do
        R="$PWD/results/fqsat_manual/sweep_${s}_rw${rw}_srv${mode}_${i}_$(date +%Y%m%dT%H%M%S)"
        echo "== $s rwnd=$rw FQ(server)=$fq lan $i/$N  ($(date +%H:%M:%S))"
        sudo -n mn -c >/dev/null 2>&1
        # shellcheck disable=SC2086
        sudo -n python3 experiments/fqsat_manual_test.py ${ARGS[$s]} --out "$R" > /tmp/run_sweep_$$.log 2>&1
        if [[ -f "$R/summary.txt" ]]; then echo "${s}_rw$rw $fq $R" | tee -a "$LIST"
        else echo "  !! lan chay loi, xem /tmp/run_sweep_$$.log:"; tail -5 /tmp/run_sweep_$$.log; fi
      done
    done
  done
done
cp "$LIST" "results/fqsat_manual/sweep_${STAMP}.txt" 2>/dev/null && LIST="results/fqsat_manual/sweep_${STAMP}.txt"
echo; echo "Danh sach: $LIST"
python3 experiments/summarize_runs.py "$LIST"
