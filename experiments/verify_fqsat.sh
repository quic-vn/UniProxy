#!/usr/bin/env bash
# Kiem tra ban ghep UniProxy + FQSAT (co unreliable) truoc khi chay thi nghiem.
# Dung:  bash verify_fqsat.sh [duong-dan-repo]     (mac dinh: thu muc hien tai)
# Khong sua file nao trong repo (chi tao/xoa mot thu muc tam _fqcheck trong mp-quic).

REPO="$(cd "${1:-.}" && pwd)"
MQ="$REPO/mp-quic"
QT="$REPO/quic-tun"
PASS=0; FAIL=0; WARN=0
ok()   { echo "  [OK]   $*"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $*"; FAIL=$((FAIL+1)); }
warn() { echo "  [WARN] $*"; WARN=$((WARN+1)); }
has()  { grep -qF -- "$2" "$1" 2>/dev/null; }                 # has FILE "chuoi"
cnt()  { grep -cF -- "$2" "$1" 2>/dev/null || true; }         # cnt FILE "chuoi"
infunc() { # infunc FILE "func ten(" "chuoi": chuoi nam trong than ham do
  awk -v f="$2" -v s="$3" 'index($0,f)==1{on=1;next} on&&/^func /{on=0} on&&index($0,s){found=1} END{exit !found}' "$1"
}
infunc_code() { # nhu infunc nhung bo phan comment (//...) truoc khi so khop
  awk -v f="$2" -v s="$3" 'index($0,f)==1{on=1;next} on&&/^func /{on=0} on{l=$0; sub(/\/\/.*/,"",l); if(index(l,s)) found=1} END{exit !found}' "$1"
}
showfunc() { # showfunc FILE "func ten(" "chuoi": in so dong + noi dung cac dong chua chuoi
  awk -v f="$2" -v s="$3" 'index($0,f)==1{on=1;next} on&&/^func /{on=0} on&&index($0,s){printf "           %s:%d: %s\n", FILENAME, NR, $0}' "$1"
}
[[ -d "$MQ" && -d "$QT" ]] || { echo "Khong thay mp-quic/ va quic-tun/ trong $REPO"; exit 2; }
echo "REPO = $REPO"

echo "== 1. Moi truong Go"
GOV="$(go env GOVERSION 2>/dev/null)"; echo "  go = $GOV"
GOMINOR="$(echo "$GOV" | sed -E 's/^go1\.([0-9]+).*/\1/')"
[[ "$GOMINOR" =~ ^[0-9]+$ && "$GOMINOR" -ge 19 ]] && ok "Go >= 1.19" || bad "Can Go >= 1.19"
for m in "$MQ" "$QT"; do
  echo "  $(basename "$m")/go.mod: $(grep -E '^(go|toolchain) ' "$m/go.mod" | tr '\n' ' ')"
  if (cd "$m" && go list -m >/dev/null 2>"/tmp/fqv_gomod.err"); then ok "$(basename "$m")/go.mod nap duoc"
  else bad "$(basename "$m")/go.mod loi: $(head -1 /tmp/fqv_gomod.err)  -> sed -i -e 's/^go 1\.22\$/go 1.19/' -e '/^toolchain /d' $m/go.mod"; fi
done
has "$QT/go.mod" "github.com/qdeconinck/mp-quic => ../mp-quic" && ok "quic-tun replace -> ../mp-quic" || bad "quic-tun/go.mod thieu replace => ../mp-quic"

echo "== 2. fuzzy/fuzzy.go (Fig.6, Table 2, Table 7)"
F="$MQ/fuzzy/fuzzy.go"
[[ -f "$F" ]] && ok "ton tai" || bad "thieu $F"
has "$F" "package fuzzy" && ok "package fuzzy" || bad "sai package"
for kv in "MustBeLowIncome 0.1" "MustNotBeLowIncome 0.3" "MustBeMiddleHighIncome 0.4" "MustBeHighIncome 0.6" \
          "MustBeLowDebt 0.2" "MustNotBeLowDebt 0.4" "MustBeMiddleHighDebt 0.6" "MustBeHighDebt 0.9" \
          "RejectedValue 0.3" "ConsideredValue 0.5" "AcceptedValue 0.7"; do
  set -- $kv   # dong khong comment: <ten> = <gia tri>
  grep -qE "^[[:space:]]*$1[[:space:]]*=[[:space:]]*${2//./\\.}([[:space:]]|$)" "$F" && ok "$1 = $2" || bad "fuzzy.go: $1 phai = $2"
done
has "$F" "func (b *BLT) Inference" && ok "func (b *BLT) Inference" || bad "thieu Inference"

echo "== 3. ackhandler: GetBytesInFlight"
has "$MQ/ackhandler/interfaces.go" "GetBytesInFlight() protocol.ByteCount" && ok "interface" || bad "interfaces.go thieu GetBytesInFlight"
grep -q "func (h \*sentPacketHandler) GetBytesInFlight() protocol.ByteCount" "$MQ/ackhandler/sent_packet_handler.go" \
  && ok "cai dat trong sent_packet_handler.go" || bad "sent_packet_handler.go thieu ham GetBytesInFlight"
infunc "$MQ/ackhandler/sent_packet_handler.go" "func (h *sentPacketHandler) GetBytesInFlight(" "return h.bytesInFlight" \
  && ok "tra ve h.bytesInFlight" || bad "GetBytesInFlight khong tra ve h.bytesInFlight"

echo "== 4. path.go: co unreliable"
grep -qE "unreliable[[:space:]]+utils.AtomicBool" "$MQ/path.go" && ok "truong unreliable" || bad "path.go thieu 'unreliable utils.AtomicBool'"
infunc "$MQ/path.go" "func (p *path) setup(" "p.unreliable.Set(false)" && ok "setup() Set(false)" || bad "setup() thieu p.unreliable.Set(false)"

echo "== 5. scheduler_fqsat.go (phan hoc + quy tac co)"
S="$MQ/scheduler_fqsat.go"
[[ -f "$S" ]] && ok "ton tai" || bad "thieu $S"
[[ "$(grep -c '^package ' "$S")" == 1 && "$(grep -m1 '^package ' "$S")" == "package quic" ]] && ok "dung 1 dong 'package quic'" || bad "dong package sai/thua"
for fn in "func (sch *scheduler) discretize(" "func fuzzyLearningRate(" "func (sch *scheduler) learnFromAck(" \
          "func (sch *scheduler) updateReliability(" "func pathUsable("; do
  has "$S" "$fn" && ok "$fn" || bad "thieu $fn"
done
grep -qE "Family.Income[[:space:]]*=[[:space:]]*vrtt" "$S" && ok "Income = vRTT" || bad "fuzzy Income phai = vrtt"
grep -qE "Family.Debt[[:space:]]*=[[:space:]]*cwndlevel" "$S" && ok "Debt = CWNDlevel" || bad "fuzzy Debt phai = cwndlevel"
infunc "$S" "func (sch *scheduler) learnFromAck(" "fuzzyLearningRate(" && ok "learnFromAck goi fuzzy" || bad "learnFromAck khong goi fuzzyLearningRate"
infunc "$S" "func (sch *scheduler) learnFromAck(" "GetBytesInFlight()" && ok "CWNDlevel = InP/CWND" || bad "learnFromAck khong dung GetBytesInFlight"
infunc "$S" "func (sch *scheduler) learnFromAck(" "FQ_GAMMA*maxNext" && ok "cap nhat Bellman" || bad "khong thay Bellman (FQ_GAMMA*maxNext)"
infunc "$S" "func (sch *scheduler) learnFromAck(" "updateReliability(" && ok "learnFromAck goi updateReliability" || bad "learnFromAck khong goi updateReliability"
infunc "$S" "func (sch *scheduler) updateReliability(" "unreliable.Set(true)" && ok "co gan co" || bad "khong gan co unreliable"
infunc "$S" "func (sch *scheduler) updateReliability(" "unreliable.Set(false)" && ok "co go co" || bad "khong go co unreliable"
infunc "$S" "func (sch *scheduler) updateReliability(" "FQ_MIN_DWELL" && ok "hysteresis FQ_MIN_DWELL" || bad "thieu FQ_MIN_DWELL"
infunc "$S" "func (sch *scheduler) updateReliability(" "forceAdjust" && ok "bat forceAdjust" || bad "khong bat forceAdjust"
infunc "$S" "func pathUsable(" "unreliable.Get()" && ok "pathUsable xet unreliable" || bad "pathUsable khong xet unreliable"

echo "== 6. scheduler.go"
C="$MQ/scheduler.go"
grep -qE '^const SCHE_ALGO[[:space:]]*=[[:space:]]*"TRR"' "$C" && ok 'SCHE_ALGO = "TRR"' || bad 'SCHE_ALGO khong phai "TRR"'
FQE="$(grep -E '^[[:space:]]*FQ_ENABLE[[:space:]]*=' "$C" | head -1 | sed -E 's/.*=[[:space:]]*([a-z]+).*/\1/')"
[[ "$FQE" == "true" ]] && ok "FQ_ENABLE = true" || warn "FQ_ENABLE = '$FQE' (false = chay baseline TRR goc)"
for k in FQ_USE_FUZZY FQ_ALPHA FQ_BETA FQ_GAMMA FQ_DELTA_FIXED FQ_RTT_NORM_MS FQ_PROBE_EPS FQ_Q_MARGIN_BAD \
         FQ_PEN_MARGIN_OK FQ_BAD_STREAK FQ_GOOD_STREAK FQ_MIN_DWELL; do
  v="$(grep -E "^[[:space:]]*$k[[:space:]]*=" "$C" | head -1 | sed -E 's/^[[:space:]]*[A-Z_]+[[:space:]]*=[[:space:]]*([^/]*).*/\1/; s/[[:space:]]+$//')"
  [[ -n "$v" ]] && echo "         $k = $v" || bad "thieu hang $k"
done
if has "$C" '"math/rand/v2"'; then
  [[ "$GOMINOR" -ge 22 ]] && warn "dung math/rand/v2 (duoc voi $GOV)" || bad "math/rand/v2 can Go 1.22, may nay $GOV -> doi thanh \"math/rand\""
else has "$C" '"math/rand"' && ok 'import "math/rand"' || bad 'thieu import "math/rand"'; fi
has "$C" "qtable" && has "$C" "forceAdjust bool" && ok "truong FQSAT trong struct" || bad "struct scheduler thieu truong FQSAT"
grep -qE "clvState[[:space:]]*=[[:space:]]*\[4\]float64\{0\.3, 0\.4, 0\.5, 0\.6\}" "$C" && ok "clvState = 0.3/0.4/0.5/0.6" || warn "clvState khac 0.3/0.4/0.5/0.6"
infunc "$C" "func (sch *scheduler) setup(" "make(map[protocol.PathID]float64)" && ok "setup() khoi tao map" || bad "setup() chua khoi tao map FQSAT (se panic)"
infunc "$C" "func (sch *scheduler) selectPathLowLatency(" "pth.unreliable.Get() && rand.Float64() >= FQ_PROBE_EPS" \
  && ok "MinRTT (selectPathLowLatency) bo path unreliable" || bad "selectPathLowLatency chua bo path unreliable (doan FQ_PROBE_EPS phai nam trong ham nay)"
infunc "$C" "func (sch *scheduler) selectPathRoundRobin(" "FQ_PROBE_EPS" \
  && bad "doan FQ_PROBE_EPS dang nam nham trong selectPathRoundRobin (TRR khong goi ham nay)" || ok "khong dat nham vao selectPathRoundRobin"
infunc "$C" "func (sch *scheduler) selectPath(" "selectPathLowLatency" && ok "selectPath van la MinRTT" || bad "selectPath khong goi selectPathLowLatency"
# chi xet file Go thuc su duoc bien dich (bo thu muc '.xxx', '_xxx', 'testdata') va bo dong comment
GOGREP=(grep -r --include='*.go' --exclude='*_test.go' --exclude-dir='.*' --exclude-dir='_*' --exclude-dir=testdata)
FQS="$("${GOGREP[@]}" -n "selectPathFQSAT" "$MQ" | grep -vE '^[^:]+:[0-9]+:[[:space:]]*//')"
[[ -n "$FQS" ]] && bad "co selectPathFQSAT trong code (thiet ke khong dung): $(echo "$FQS" | head -2 | tr '\n' ' ')" || ok "khong dung selectPathFQSAT"
infunc "$C" "func (sch *scheduler) streamAllocation(" "pathUsable(" && ok "streamAllocation dung pathUsable" || bad "streamAllocation chua dung pathUsable"
infunc "$C" "func (sch *scheduler) adjustAllocation(" "pathUsable(st.path)" && ok "adjustAllocation di doi stream" || bad "adjustAllocation thieu buoc di doi stream"
[[ "$(awk 'index($0,"func (sch *scheduler) adjustAllocation(")==1{on=1;next} on&&/^func /{on=0} on' "$C" | grep -c 'pathUsable(pth)')" -ge 3 ]] \
  && ok "adjustAllocation: >=3 cho loc pathUsable(pth)" || bad "adjustAllocation chua thay du 3 cho loc pathUsable(pth)"
infunc "$C" "func (sch *scheduler) adjustAllocation(" "packetShSent[stream.path.pathID] > 0" && ok "guard chia 0 PORTION" || bad "thieu guard packetShSent > 0"
[[ "$(cnt "$C" 'pth.pathID == protocol.InitialPathID || pth.potentiallyFailed.Get()')" == 0 ]] && ok "khong con dieu kien loc cu" || bad "con dieu kien 'InitialPathID || potentiallyFailed' chua doi"
infunc "$C" "func (sch *scheduler) sendPacket(" "sch.forceAdjust" && ok "sendPacket xu ly forceAdjust" || bad "sendPacket chua xu ly forceAdjust"

echo "== 7. streams_map.go (TRR)"
M="$MQ/streams_map.go"
infunc "$M" "func (m *streamsMap) getStreamForPath(" "pathUsable(path)" && ok "threshold bo path unreliable" || bad "getStreamForPath chua dung pathUsable"
infunc "$M" "func (m *streamsMap) getStreamForPath(" "!stream.path.unreliable.Get() && curRTT < pthRTT" && ok "dieu kien muon path" || bad "thieu '!stream.path.unreliable.Get() &&'"
infunc "$M" "func (m *streamsMap) getStreamForPath(" "now.Sub(pth.congestionlimited) > pthRTT" && ok "logic underutilized giu nguyen" || bad "logic idlePath bi doi"

echo "== 8. session.go: hook ACK"
infunc "$MQ/session.go" "func (s *session) handleAckFrame(" "s.scheduler.learnFromAck(s, pth)" && ok "handleAckFrame goi learnFromAck" || bad "handleAckFrame chua goi learnFromAck"

echo "== 8b. Bon sua doi (co co dieu kien, PING tham do, quyet dinh theo EMA, chot khoi dong)"
for k in FQ_PEN_MARGIN_BAD FQ_PEN_MARGIN_OK FQ_FC_RECENT FQ_FC_RELEASE FQ_PING_INTERVAL FQ_QUIET_PROBE FQ_MIN_ACKS FQ_WARMUP FQ_FILL_ON FQ_FILL_OFF; do
  v="$(grep -E "^[[:space:]]*$k[[:space:]]*=" "$C" | head -1 | sed -E 's/^[[:space:]]*[A-Z_]+[[:space:]]*=[[:space:]]*([^/]*).*/\1/; s/[[:space:]]+$//')"
  [[ -n "$v" ]] && echo "         $k = $v" || bad "thieu hang $k"
done
grep -qE "fcBlockedAt[[:space:]]+time.Time" "$C" && ok "struct: fcBlockedAt" || bad "struct scheduler thieu fcBlockedAt time.Time"
grep -qE "ackCount[[:space:]]+map\[protocol.PathID\]uint64" "$C" && ok "struct: ackCount" || bad "struct scheduler thieu ackCount"
grep -qE "firstAckAt[[:space:]]+time.Time" "$C" && ok "struct: firstAckAt" || bad "struct scheduler thieu firstAckAt"
infunc "$C" "func (sch *scheduler) setup(" "sch.ackCount = make" && ok "setup() khoi tao ackCount" || bad "setup() chua khoi tao ackCount (se panic)"
infunc "$C" "func (sch *scheduler) sendPacket(" "sch.probePaths(s)" && ok "sendPacket goi probePaths (sua 2)" || bad "sendPacket chua goi probePaths"
# sua 1: stream_framer ghi fcBlockedAt
SF="$MQ/stream_framer.go"
has "$SF" "func (f *streamFramer) noteFCBlocked()" && ok "stream_framer: ham noteFCBlocked" || bad "stream_framer.go thieu noteFCBlocked"
infunc "$SF" "func (f *streamFramer) noteFCBlocked(" "scheduler.fcBlockedAt = time.Now()" && ok "noteFCBlocked ghi fcBlockedAt" || bad "noteFCBlocked khong ghi fcBlockedAt"
NFC="$(awk 'index($0,"func (f *streamFramer) maybePopNormalFrames(")==1{on=1;next} on&&/^func /{on=0} on&&index($0,"f.noteFCBlocked()"){c++} END{print c+0}' "$SF")"
[[ "$NFC" -ge 2 ]] && ok "maybePopNormalFrames goi noteFCBlocked o $NFC cho" || bad "maybePopNormalFrames phai goi noteFCBlocked o 2 cho (dang: $NFC)"
# sua 2: path.go + session.go + probePaths
grep -qE "lastProbe[[:space:]]+time.Time" "$MQ/path.go" && ok "path.go: lastProbe" || bad "path.go thieu lastProbe"
grep -qE "lastAck[[:space:]]+time.Time" "$MQ/path.go" && ok "path.go: lastAck" || bad "path.go thieu lastAck"
infunc "$MQ/session.go" "func (s *session) handleAckFrame(" "pth.lastAck = time.Now()" && ok "handleAckFrame ghi lastAck" || bad "handleAckFrame chua ghi pth.lastAck"
HA="func (s *session) handleAckFrame("
infunc_code "$MQ/session.go" "$HA" "sRTTBefore, lRTTBefore := pth.rttStats.SmoothedRTT(), pth.rttStats.LatestRTT()" \
  && infunc_code "$MQ/session.go" "$HA" "lossAfter != lossBefore" && infunc_code "$MQ/session.go" "$HA" "&& newInfo &&" \
  && ok "handleAckFrame chi hoc khi co mau RTT moi hoac loss moi (sua 5 ban 2)" \
  || bad "handleAckFrame phai xac dinh newInfo bang mau RTT moi / loss moi (sua 5 ban 2)"
if infunc_code "$MQ/session.go" "$HA" "GetLeastUnacked() != luBefore"; then
  bad "handleAckFrame van dung GetLeastUnacked() lam newInfo (ACK cua goi chi-co-ACK lam no thay doi):"; showfunc "$MQ/session.go" "$HA" "luBefore"
else ok "handleAckFrame khong dung GetLeastUnacked() lam newInfo"; fi
has "$S" "func (sch *scheduler) probePaths(" && ok "probePaths ton tai" || bad "thieu func probePaths"
infunc "$S" "func (sch *scheduler) probePaths(" "s.sendProbePing(pth)" && ok "probePaths gui PING thuan (sendProbePing)" || bad "probePaths phai goi s.sendProbePing (KHONG dung sendPing: PackPing xoa ACK dang cho)"
if infunc_code "$S" "func (sch *scheduler) probePaths(" "s.sendPing("; then
  bad "probePaths van goi s.sendPing (PackPing xoa ACK dang cho -> ket noi treo):"; showfunc "$S" "func (sch *scheduler) probePaths(" "sendPing("
else ok "probePaths khong dung sendPing"; fi
infunc "$S" "func (sch *scheduler) probePaths(" "FQ_PING_INTERVAL" && infunc "$S" "func (sch *scheduler) probePaths(" "FQ_QUIET_PROBE" \
  && ok "probePaths: path gan co moi FQ_PING_INTERVAL, path im lang moi FQ_QUIET_PROBE" || bad "probePaths chua phan biet FQ_PING_INTERVAL / FQ_QUIET_PROBE"
if infunc_code "$S" "func (sch *scheduler) probePaths(" "InitialPathID || pth.potentiallyFailed.Get()"; then
  bad "probePaths van bo qua path potentiallyFailed (path pf khong bao gio duoc danh thuc):"; showfunc "$S" "func (sch *scheduler) probePaths(" "potentiallyFailed"
else ok "probePaths khong bo qua path potentiallyFailed"; fi
infunc_code "$S" "func (sch *scheduler) probePaths(" "pth.unreliable.Get() || pth.potentiallyFailed.Get()" \
  && ok "probePaths: path pf duoc PING moi FQ_PING_INTERVAL" || bad "probePaths: path pf phai dung FQ_PING_INTERVAL (pth.unreliable.Get() || pth.potentiallyFailed.Get())"
has "$MQ/packet_packer.go" "func (p *packetPacker) PackPurePing(" && ok "packet_packer: PackPurePing" || bad "packet_packer.go thieu PackPurePing"
infunc "$MQ/packet_packer.go" "func (p *packetPacker) PackPurePing(" "frames = append(frames, p.ackFrame[pth.pathID])" && ok "PackPurePing giu ACK dang cho" || bad "PackPurePing phai kem p.ackFrame dang cho"
infunc "$MQ/session.go" "func (s *session) sendProbePing(" "PackPurePing(pth)" && ok "session: sendProbePing dung PackPurePing" || bad "session.go thieu sendProbePing/PackPurePing"
infunc "$MQ/session.go" "func (s *session) sendProbePing(" "pth.open.Get()" && ok "sendProbePing bo qua path da dong" || bad "sendProbePing phai kiem tra pth.open.Get()"
infunc "$C" "func (sch *scheduler) getRetransmission(" "case *wire.PingFrame:" && ok "getRetransmission khong truyen lai PING" || bad "getRetransmission thieu 'case *wire.PingFrame:' (PING mat se vao controlFrames chung)"
# sua 1 + 3 + 4 trong updateReliability
UR="func (sch *scheduler) updateReliability("
infunc "$S" "$UR" "fcRecent" && infunc "$S" "$UR" "FQ_FC_RECENT" && ok "updateReliability: gan co can fcRecent (sua 1)" || bad "updateReliability chua xet fcRecent/FQ_FC_RECENT"
infunc "$S" "$UR" "fcIdle" && infunc "$S" "$UR" "FQ_FC_RELEASE" && ok "updateReliability: go co khi fcIdle (sua 1)" || bad "updateReliability chua xet fcIdle/FQ_FC_RELEASE"
infunc "$S" "$UR" "sch.penaltyEMA[me] > sch.penaltyEMA[other]+FQ_PEN_MARGIN_BAD" && ok "updateReliability: gan co theo penaltyEMA (sua 3)" || bad "updateReliability phai gan co theo penaltyEMA voi FQ_PEN_MARGIN_BAD (rewardEMA gay flapping)"
if infunc_code "$S" "$UR" "rewardEMA[me] < sch.rewardEMA[other]"; then
  bad "updateReliability van gan co theo rewardEMA (gay bat/tat co lien tuc):"; showfunc "$S" "$UR" "rewardEMA[me] <"
else ok "updateReliability khong con gan co theo rewardEMA"; fi
infunc_code "$S" "$UR" "s.connectionParameters.GetSendConnectionFlowControlWindow()" && infunc_code "$S" "$UR" "fillOther >= FQ_FILL_ON" \
  && infunc_code "$S" "$UR" "fillOther < FQ_FILL_OFF" && ok "updateReliability: chi gan co khi path con lai lap day rwnd (sua 6)" \
  || bad "updateReliability chua xet fillOther/FQ_FILL_ON/FQ_FILL_OFF theo GetSendConnectionFlowControlWindow (sua 6)"
infunc "$S" "$UR" "FQ_MIN_ACKS" && infunc "$S" "$UR" "FQ_WARMUP" && ok "updateReliability: chot khoi dong (sua 4)" || bad "updateReliability chua xet FQ_MIN_ACKS/FQ_WARMUP"
infunc "$S" "func (sch *scheduler) learnFromAck(" "sch.ackCount[pid]++" && ok "learnFromAck dem ackCount" || bad "learnFromAck chua tang ackCount"
infunc "$S" "func (sch *scheduler) learnFromAck(" "sch.firstAckAt = time.Now()" && ok "learnFromAck ghi firstAckAt" || bad "learnFromAck chua ghi firstAckAt"

echo "== 9. Code la ngoai thiet ke"
[[ -f "$MQ/path_chooser.go" ]] && bad "co mp-quic/path_chooser.go (khong thuoc ban ghep; import internal/qsat)" || ok "khong co path_chooser.go"
MISSING=0
while read -r imp; do d="$MQ/${imp#github.com/qdeconinck/mp-quic/}"
  [[ -d "$d" ]] || { bad "import khong ton tai: $imp"; MISSING=1; }
done < <("${GOGREP[@]}" -hoE '"github.com/qdeconinck/mp-quic/[^"]+"' "$MQ" | tr -d '"' | sort -u)
HIDDEN="$(find "$MQ" -mindepth 1 -maxdepth 2 -type d \( -name '.*' -o -name '_*' \) 2>/dev/null)"
[[ -n "$HIDDEN" ]] && echo "  (bo qua, Go khong bien dich): $(echo "$HIDDEN" | sed "s#$MQ/##" | tr '\n' ' ')"
[[ "$MISSING" == 0 ]] && ok "moi import noi bo deu ton tai"
if git -C "$REPO" rev-parse >/dev/null 2>&1; then
  echo "  git: nhanh $(git -C "$REPO" rev-parse --abbrev-ref HEAD); file .go khac HEAD (khong ke _test.go):"
  git -C "$REPO" status --short -- mp-quic quic-tun | grep -E '\.go$|go\.(mod|sum)$' | grep -v '_test.go' | sed 's/^/         /'
fi

echo "== 10. Chay thu fuzzy (vi du trong bai FQ-SAT: vRTT=0.5, CWNDlevel=0.9 -> 0.7)"
TMP="$MQ/_fqcheck"; rm -rf "$TMP"; mkdir -p "$TMP"
cat > "$TMP/main.go" <<'EOF'
package main

import (
	"fmt"

	"github.com/qdeconinck/mp-quic/fuzzy"
)

func lr(v, c float64) float64 {
	var d fuzzy.FuzzyNumber
	d.Family.Income, d.Family.Debt = v, c
	b := fuzzy.BLT{}
	b.Fuzzification(&d)
	b.Inference(&d)
	b.Defuzzification(&d)
	return d.CrispValue
}

func main() {
	for _, t := range [][3]float64{{0.5, 0.9, 0.7}, {0.05, 0.1, 0.3}, {0.35, 0.5, 0.5}, {0.05, 0.95, 0.5}, {0.7, 0.1, 0.5}} {
		fmt.Printf("%.2f %.2f %.4f %.1f\n", t[0], t[1], lr(t[0], t[1]), t[2])
	}
}
EOF
if OUT="$(cd "$MQ" && go run ./_fqcheck 2>&1)"; then
  while read -r v c got exp; do
    awk -v g="$got" -v e="$exp" 'BEGIN{exit !((g-e)<1e-9 && (e-g)<1e-9)}' \
      && ok "vRTT=$v CWNDlevel=$c -> delta=$got" || bad "vRTT=$v CWNDlevel=$c -> $got, ky vong $exp"
  done <<< "$OUT"
else bad "khong chay duoc fuzzy: $(echo "$OUT" | head -3)"; fi
rm -rf "$TMP"

echo "== 11. Build"
(cd "$MQ" && go build . 2>/tmp/fqv_b0.err) && ok "go build mp-quic" || bad "mp-quic: $(head -3 /tmp/fqv_b0.err)"
(cd "$QT/server/cmd" && go build 2>/tmp/fqv_b1.err) && ok "build server -> $QT/server/cmd/cmd" || bad "server: $(head -3 /tmp/fqv_b1.err)"
(cd "$QT/client/cmd" && go build 2>/tmp/fqv_b2.err) && ok "build client -> $QT/client/cmd/cmd" || bad "client: $(head -3 /tmp/fqv_b2.err)"
for b in "$QT/server/cmd/cmd" "$QT/client/cmd/cmd"; do
  [[ -x "$b" ]] || continue
  side="$(basename "$(dirname "$(dirname "$b")")")"
  if strings "$b" 2>/dev/null | grep -q "FQSAT: path"; then
    [[ "$FQE" == "true" ]] && ok "$side binary chua code FQSAT" || bad "$side binary van chua code FQSAT du FQ_ENABLE=$FQE (build cu?)"
  else
    [[ "$FQE" == "true" ]] && bad "$side binary khong chua 'FQSAT: path' (build tu code cu?)" \
      || ok "$side binary: FQ_ENABLE=false, compiler da loai bo code FQSAT => TRR goc"
  fi
done

echo "== 12. Dieu kien chay thi nghiem"
for f in experiments/fqsat_manual_test.py example/client_long.py example/server.py; do
  [[ -f "$REPO/$f" ]] && ok "$f" || bad "thieu $REPO/$f"
done
command -v mn >/dev/null && ok "mininet (mn)" || bad "khong co mn"
python3 -c "import mininet" 2>/dev/null && ok "python3 import mininet" || bad "python3 khong import duoc mininet"
for mod in sch_cake sch_netem xt_TPROXY; do modinfo "$mod" >/dev/null 2>&1 && ok "kernel module $mod" || warn "khong thay module $mod (co the built-in)"; done
command -v tc >/dev/null && ok "tc" || bad "khong co tc"
command -v iptables >/dev/null && ok "iptables" || bad "khong co iptables"

echo
echo "TONG KET: OK=$PASS  WARN=$WARN  FAIL=$FAIL"
[[ "$FAIL" == 0 ]] && echo "=> San sang: sudo mn -c && sudo python3 experiments/fqsat_manual_test.py" || echo "=> Sua cac muc [FAIL] truoc khi chay."
exit $(( FAIL > 0 ))
