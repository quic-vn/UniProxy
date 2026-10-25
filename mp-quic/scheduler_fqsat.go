package quic

import (
	"math"
	"sort"
	"time"

	"github.com/qdeconinck/mp-quic/fuzzy"
	"github.com/qdeconinck/mp-quic/internal/protocol"
	"github.com/qdeconinck/mp-quic/internal/utils"
)

// discretize: Q-table (§3.2.2) – 5 mức [0,0.3) [0.3,0.4) [0.4,0.5) [0.5,0.6) [0.6,1]
func (sch *scheduler) discretize(v float64) int8 {
	for i, th := range sch.clvState {
		if v < th {
			return int8(i)
		}
	}
	return 4
}

// fuzzyLearningRate: Fig. 6, Table 2, Table 7, Eq. 7
func fuzzyLearningRate(vrtt, cwndlevel float64) float64 {
	var data fuzzy.FuzzyNumber
	data.Family.Income = vrtt    // ngưỡng Income trong fuzzy.go = vRTT (0.1/0.3/0.4/0.6)
	data.Family.Debt = cwndlevel // ngưỡng Debt = CWNDlevel (0.2/0.4/0.6/0.9)
	blt := fuzzy.BLT{}
	blt.Fuzzification(&data)
	blt.Inference(&data)
	blt.Defuzzification(&data)
	if math.IsNaN(data.CrispValue) || data.CrispValue <= 0 {
		return FQ_DELTA_FIXED
	}
	return data.CrispValue // 0.3 / 0.5 / 0.7 hoặc trung bình có trọng số
}

// dataPaths: các path dữ liệu đang mở (bỏ path 0), sắp theo pathID. Dùng cho N ≥ 2 path.
// Lock s.paths phải do caller giữ hoặc không cần (handleAckFrame chạy trong goroutine của session).
func dataPaths(s *session) []*path {
	ps := make([]*path, 0, len(s.paths))
	for pid, p := range s.paths {
		if pid == protocol.InitialPathID || !p.open.Get() {
			continue
		}
		ps = append(ps, p)
	}
	sort.Slice(ps, func(i, j int) bool { return ps[i].pathID < ps[j].pathID })
	return ps
}

// rwndEstimate (mục 7): cửa sổ nhận cấp connection mà phía nhận thực sự cấp, tính bằng byte.
// Phía nhận công bố offset = bytesRead + rwnd; ta biết remaining = offset − bytesSent và tổng
// bytes in flight ≈ bytesSent − bytesAcked. Khi phía nhận đã đọc hết những gì đã nhận thì
// remaining + inflight = rwnd, còn lại thì nhỏ hơn => theo dõi giá trị LỚN NHẤT quan sát được,
// không bao giờ thấp hơn giá trị công bố lúc bắt tay.
//
// Sửa rwnd: phép đo bị đội lên khi ACK của một path về chậm hơn WINDOW_UPDATE. Dữ liệu đi trên
// path chậm được phía nhận đọc và báo bằng WINDOW_UPDATE qua path nhanh, nhưng vẫn nằm trong
// bytes in flight của path chậm cho đến khi ACK của nó về => remaining + inflight > rwnd thật.
// Ở S3 0,16 MB (path 2: RTT 10 -> 90 ms) ước lượng nhảy từ ~173 KB lên 231-276 KB ngay sau IMPAIR,
// fill tụt xuống ~0,6 và cờ không bật. Vì vậy:
//   - chặn trên bằng cửa sổ tối đa mà phía nhận được tự nới tới (hai proxy UniProxy chạy cùng bản
//     mp-quic nên phía gửi biết hằng số này) × FQ_RWND_HDR;
//   - phần vượt giá trị bắt tay giảm dần (hằng số FQ_RWND_DECAY) nếu không được đo lại, để một mẫu
//     sai không giữ nguyên đến hết kết nối.
func (sch *scheduler) rwndEstimate(s *session, ps []*path) float64 {
	base := float64(s.connectionParameters.GetSendConnectionFlowControlWindow())
	if !FQ_RWND_EST || s.flowControlManager == nil {
		return base
	}
	peerMax := float64(protocol.DefaultMaxReceiveConnectionFlowControlWindowClient)
	if s.perspective == protocol.PerspectiveClient {
		peerMax = float64(protocol.DefaultMaxReceiveConnectionFlowControlWindowServer)
	}
	limit := math.Max(base, peerMax) * FQ_RWND_HDR

	now := time.Now()
	if !sch.rwndEstAt.IsZero() && sch.rwndEst > base {
		sch.rwndEst = base + (sch.rwndEst-base)*math.Exp(-float64(now.Sub(sch.rwndEstAt))/float64(FQ_RWND_DECAY))
	}
	sch.rwndEstAt = now

	var inflight protocol.ByteCount
	for _, p := range ps {
		inflight += p.sentPacketHandler.GetBytesInFlight()
	}
	observed := math.Min(float64(s.flowControlManager.RemainingConnectionWindowSize()+inflight), limit)
	if observed > sch.rwndEst {
		sch.rwndEst = observed
	}
	if sch.rwndEst < base {
		sch.rwndEst = base
	}
	return sch.rwndEst
}

// learnFromAck: chuyển từ GetStateAndRewardQlearning (scheduler_dl.go:64-212 của FQSAT).
// Chạy cho mọi số path ≥ 2: reward/phạt/loss của path vừa ACK luôn được cập nhật; riêng
// Q-table (state = cặp tỉ lệ cwnd/lRTT) chỉ định nghĩa cho đúng 2 path như FQSAT gốc.
func (sch *scheduler) learnFromAck(s *session, ackPth *path) {
	ps := dataPaths(s)
	if len(ps) < 2 || ackPth.pathID == protocol.InitialPathID {
		return
	}
	pid := ackPth.pathID
	lrtt, srtt := ackPth.rttStats.LatestRTT(), ackPth.rttStats.SmoothedRTT()
	cwnd := ackPth.sentPacketHandler.GetCongestionWindow()
	if lrtt == 0 || srtt == 0 || cwnd == 0 {
		return // chưa đủ mẫu
	}
	cwndlevel := float64(ackPth.sentPacketHandler.GetBytesInFlight()) / float64(cwnd) // Eq. 4

	// Reward cho path vừa ACK (Eq. 6, dạng gộp như FQSAT dòng 113-122); loss tính theo khoảng
	packets, _, losses := ackPth.sentPacketHandler.GetStatistics()
	dPkt, dLoss := packets-sch.prevPackets[pid], losses-sch.prevLosses[pid]
	sch.prevPackets[pid], sch.prevLosses[pid] = packets, losses
	lossRate := 0.0
	if dPkt > 0 {
		lossRate = float64(dLoss) / float64(dPkt)
	}
	rttMs := float64(lrtt) / float64(time.Millisecond)
	penalty := FQ_ALPHA*rttMs/FQ_RTT_NORM_MS + FQ_BETA*5*lossRate
	reward := (1-FQ_ALPHA-FQ_BETA)*cwndlevel - penalty

	// Learning rate bằng fuzzy (§3.2.3) – chỗ FQSAT gốc bị comment
	if FQ_USE_FUZZY {
		vrtt := math.Abs(float64(lrtt-srtt)) / float64(srtt) // Eq. 2
		sch.fqDelta = fuzzyLearningRate(vrtt, cwndlevel)
	} else {
		sch.fqDelta = FQ_DELTA_FIXED
	}

	// Q-table (Eq. 5 + Eq. 1), chỉ khi đúng 2 path – giữ để đối chiếu với FQSAT gốc, không
	// dùng để ra quyết định cờ (xem updateReliability)
	if len(ps) == 2 {
		first, second := ps[0], ps[1]
		rate := func(p *path) float64 {
			l := p.rttStats.LatestRTT()
			if l == 0 {
				return 0
			}
			return float64(p.sentPacketHandler.GetCongestionWindow()) / float64(l)
		}
		if sum := rate(first) + rate(second); sum > 0 {
			fLevel := sch.discretize(rate(first) / sum)
			sLevel := sch.discretize(rate(second) / sum)
			col := int8(0)
			if pid == second.pathID {
				col = 1
			}
			maxNext := math.Max(sch.qtable[fLevel][sLevel][0], sch.qtable[fLevel][sLevel][1])
			q := &sch.qtable[sch.curStateF][sch.curStateS][col]
			*q = (1-sch.fqDelta)*(*q) + sch.fqDelta*(reward+FQ_GAMMA*maxNext)
			sch.curStateF, sch.curStateS = fLevel, sLevel
		}
	}

	// Thống kê mượt để ra quyết định cờ
	sch.rewardEMA[pid] = 0.9*sch.rewardEMA[pid] + 0.1*reward
	sch.penaltyEMA[pid] = 0.9*sch.penaltyEMA[pid] + 0.1*penalty
	sch.lossEMA[pid] = 0.9*sch.lossEMA[pid] + 0.1*lossRate // mục 6
	sch.ackCount[pid]++                                    // sửa 4
	if sch.firstAckAt.IsZero() {
		sch.firstAckAt = time.Now()
	}
	sch.updateReliability(s, ps)
}

// updateReliability: suy ra cờ unreliable cho từng path, có hysteresis. Tổng quát cho N path:
// mỗi path được so với path TỐT NHẤT trong các path còn lại đang dùng được ("others").
//
//	sửa 1: chỉ gắn cờ khi kết nối đang bị cửa sổ nhận chặn (fcBlockedAt gần đây); nếu không bị
//	       chặn suốt FQ_FC_RELEASE thì gỡ cờ, vì lúc đó path chậm vẫn cộng thêm băng thông.
//	sửa 3: quyết định theo penaltyEMA (lRTT + loss), không theo Q-table / rewardEMA (chứa cwndlevel
//	       nên path ít dùng bị gắn cờ oan => flapping).
//	sửa 4: không xét gắn cờ khi chưa đủ FQ_MIN_ACKS ACK / chưa qua FQ_WARMUP (pha khởi động).
//	sửa 6: chỉ gắn cờ khi tổng cwnd của các path còn lại ≥ FQ_FILL_ON × cửa sổ nhận (bỏ path này
//	       không mất băng thông); gỡ khi < FQ_FILL_OFF. Cửa sổ nhận lấy từ rwndEstimate (mục 7).
//	Không bao giờ gắn cờ nếu không còn path nào khác dùng được.
func (sch *scheduler) updateReliability(s *session, ps []*path) {
	now := time.Now()
	fcRecent := !sch.fcBlockedAt.IsZero() && now.Sub(sch.fcBlockedAt) <= FQ_FC_RECENT
	fcIdle := sch.fcBlockedAt.IsZero() || now.Sub(sch.fcBlockedAt) > FQ_FC_RELEASE
	rwnd := sch.rwndEstimate(s, ps)

	for _, p := range ps {
		me := p.pathID
		bestPen, bestID := math.Inf(1), protocol.PathID(255)
		var cwndOthers protocol.ByteCount
		othersWarm := false
		for _, o := range ps {
			if o == p || o.unreliable.Get() || o.potentiallyFailed.Get() {
				continue
			}
			if pen := sch.penaltyEMA[o.pathID]; pen < bestPen {
				bestPen, bestID = pen, o.pathID
			}
			cwndOthers += o.sentPacketHandler.GetCongestionWindow()
			if sch.ackCount[o.pathID] >= FQ_MIN_ACKS {
				othersWarm = true
			}
		}
		if bestID == 255 {
			continue // không còn path nào khác dùng được => không bao giờ loại path này
		}
		fillOthers := 0.0
		if rwnd > 0 {
			fillOthers = float64(cwndOthers) / rwnd
		}
		warm := sch.ackCount[me] >= FQ_MIN_ACKS && othersWarm && now.Sub(sch.firstAckAt) >= FQ_WARMUP

		worse := warm && fcRecent && fillOthers >= FQ_FILL_ON &&
			sch.penaltyEMA[me] > bestPen+FQ_PEN_MARGIN_BAD
		recovered := sch.penaltyEMA[me] <= bestPen+FQ_PEN_MARGIN_OK || fcIdle || fillOthers < FQ_FILL_OFF

		if worse {
			sch.badStreak[me]++
		} else {
			sch.badStreak[me] = 0
		}
		if recovered {
			sch.goodStreak[me]++
		} else {
			sch.goodStreak[me] = 0
		}
		if now.Sub(sch.flagChanged[me]) < FQ_MIN_DWELL {
			continue
		}
		if !p.unreliable.Get() && sch.badStreak[me] >= FQ_BAD_STREAK {
			p.unreliable.Set(true)
			sch.flagChanged[me], sch.forceAdjust, sch.badStreak[me] = now, true, 0
			utils.Infof("FQSAT: path %d -> UNRELIABLE (pen=%.3f vs best %.3f on path %d, loss=%.3f, rEMA=%.3f, fcBlocked %.0fms ago, fill_others=%.2f, rwnd=%.0f)",
				me, sch.penaltyEMA[me], bestPen, bestID, sch.lossEMA[me], sch.rewardEMA[me],
				float64(now.Sub(sch.fcBlockedAt))/1e6, fillOthers, rwnd)
		} else if p.unreliable.Get() && sch.goodStreak[me] >= FQ_GOOD_STREAK {
			p.unreliable.Set(false)
			sch.flagChanged[me], sch.forceAdjust, sch.goodStreak[me] = now, true, 0
			why := "penalty ok"
			if fcIdle {
				why = "rwnd not limiting"
			} else if fillOthers < FQ_FILL_OFF {
				why = "other paths cannot fill rwnd"
			}
			utils.Infof("FQSAT: path %d -> reliable again (%s; pen=%.3f vs best %.3f, fill_others=%.2f)",
				me, why, sch.penaltyEMA[me], bestPen, fillOthers)
		}
	}
}

// probePaths (sửa 2 + mục 3, 5): gửi PING thuần để path luôn có mẫu RTT/loss.
//   - Path bị gắn cờ, path potentiallyFailed, và path vừa được gỡ cờ (< FQ_POST_CLEAR): thăm dò
//     dày mỗi FQ_PING_INTERVAL, để học, gỡ cờ, xoá pf, và để sRTT hội tụ sau khi gỡ cờ (mục 3).
//   - Path chưa gắn cờ nhưng im lặng: mỗi FQ_QUIET_PROBE, và CHỈ khi phía này đang gửi dữ liệu
//     stream (mục 5). Phía client của proxy hầu như không gửi dữ liệu; thăm dò từ phía đó làm
//     thay đổi slow start của phía server (thấy trong thí nghiệm chạy lẫn binary).
//
// PING là frame retransmittable nên PING mất được tính vào loss; mất thì không truyền lại
// (getRetransmission bỏ PingFrame) để không rơi vào controlFrames dùng chung.
func (sch *scheduler) probePaths(s *session) error {
	now := time.Now()
	senderActive := !sch.lastDataSentAt.IsZero() && now.Sub(sch.lastDataSentAt) <= FQ_SENDER_IDLE
	var toProbe []*path
	s.pathsLock.RLock()
	for _, pth := range s.paths {
		if pth.pathID == protocol.InitialPathID || !pth.open.Get() {
			continue
		}
		if pth.rttStats.SmoothedRTT() == 0 { // chưa có mẫu RTT, để cơ chế duplicate của MPQUIC lo
			continue
		}
		fc := sch.flagChanged[pth.pathID]
		dense := pth.unreliable.Get() || pth.potentiallyFailed.Get() ||
			(!fc.IsZero() && now.Sub(fc) < FQ_POST_CLEAR)
		interval := FQ_PING_INTERVAL
		if !dense {
			if !senderActive {
				continue
			}
			interval = FQ_QUIET_PROBE
		}
		if now.Sub(pth.lastAck) > interval && now.Sub(pth.lastProbe) > interval {
			toProbe = append(toProbe, pth)
		}
	}
	s.pathsLock.RUnlock()
	for _, pth := range toProbe {
		pth.lastProbe = now
		if err := s.sendProbePing(pth); err != nil { // PING thuần, không dùng sendPing/PackPing
			return err
		}
	}
	return nil
}

// pathUsable: điều kiện chung thay cho "InitialPathID || potentiallyFailed" ở các tầng
func pathUsable(p *path) bool {
	return p.pathID != protocol.InitialPathID && !p.potentiallyFailed.Get() && !p.unreliable.Get()
}

// logPathStats (chẩn đoán): một dòng "PSTAT" cho mỗi path dữ liệu, gọi mỗi INTERVAL trước
// adjustAllocation (lúc stream.packetSent còn chứa số frame của khoảng vừa qua).
//
//	dfr   : số frame dữ liệu stream gửi trên path trong khoảng vừa qua (theo TRR)
//	nstr  : số stream đang được gán vào path
//	pkts/losses: tích luỹ từ đầu kết nối (script phân tích tự lấy hiệu)
//	fcage : ms kể từ lần cuối kết nối bị cửa sổ nhận chặn (-1: chưa bao giờ)
//	rwnd  : cửa sổ nhận công bố lúc bắt tay; rwndest: ước lượng (mục 7); sender: phía này đang gửi
//
// Các trường FQSAT (ack, pen, rew, loss, unrel) chỉ có ý nghĩa khi FQ_ENABLE = true.
func (sch *scheduler) logPathStats(s *session) {
	now := time.Now()
	fcage := int64(-1)
	if !sch.fcBlockedAt.IsZero() {
		fcage = int64(now.Sub(sch.fcBlockedAt) / time.Millisecond)
	}
	sender := !sch.lastDataSentAt.IsZero() && now.Sub(sch.lastDataSentAt) <= FQ_SENDER_IDLE
	dfr := make(map[protocol.PathID]int)
	nstr := make(map[protocol.PathID]int)
	for _, streamID := range s.streamsMap.openStreams {
		if streamID == 1 {
			continue
		}
		st := s.streamsMap.streams[streamID]
		if st == nil {
			continue
		}
		for pid, n := range st.packetSent {
			dfr[pid] += n
		}
		if st.path != nil {
			nstr[st.path.pathID]++
		}
	}
	rwndBase := s.connectionParameters.GetSendConnectionFlowControlWindow()
	for pid, pth := range s.paths {
		if pid == protocol.InitialPathID {
			continue
		}
		pkts, _, losses := pth.sentPacketHandler.GetStatistics()
		utils.Infof("PSTAT p=%d dfr=%d nstr=%d srtt=%.1f lrtt=%.1f cwnd=%d inf=%d pkts=%d losses=%d pf=%t unrel=%t ack=%d pen=%.3f rew=%.3f loss=%.4f fcage=%d rwnd=%d rwndest=%.0f sender=%t",
			pid, dfr[pid], nstr[pid],
			float64(pth.rttStats.SmoothedRTT())/1e6, float64(pth.rttStats.LatestRTT())/1e6,
			pth.sentPacketHandler.GetCongestionWindow(), pth.sentPacketHandler.GetBytesInFlight(),
			pkts, losses, pth.potentiallyFailed.Get(), pth.unreliable.Get(),
			sch.ackCount[pid], sch.penaltyEMA[pid], sch.rewardEMA[pid], sch.lossEMA[pid], fcage,
			rwndBase, sch.rwndEst, sender)
	}
}
