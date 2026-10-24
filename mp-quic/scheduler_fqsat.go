package quic

import (
	"math"
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

// learnFromAck: chuyển từ GetStateAndRewardQlearning (scheduler_dl.go:64-212 của FQSAT)
func (sch *scheduler) learnFromAck(s *session, ackPth *path) {
	// 1. Xác định hai path dữ liệu, sắp theo pathID (giống FQSAT dòng 95-105)
	first, second := protocol.PathID(255), protocol.PathID(255)
	for pid := range s.paths {
		if pid == protocol.InitialPathID {
			continue
		}
		if first == 255 {
			first = pid
		} else if pid < first {
			second, first = first, pid
		} else {
			second = pid
		}
	}
	if second == 255 || (ackPth.pathID != first && ackPth.pathID != second) {
		return // FQSAT chỉ định nghĩa cho 2 path
	}

	// 2. Thu thập thông số (FQSAT dòng 80-93)
	type pinfo struct {
		lrtt, srtt time.Duration
		cwnd       protocol.ByteCount
		cwndlevel  float64
	}
	info := map[protocol.PathID]pinfo{}
	for _, pid := range []protocol.PathID{first, second} {
		p := s.paths[pid]
		pi := pinfo{lrtt: p.rttStats.LatestRTT(), srtt: p.rttStats.SmoothedRTT(),
			cwnd: p.sentPacketHandler.GetCongestionWindow()}
		if pi.lrtt == 0 || pi.srtt == 0 || pi.cwnd == 0 {
			return // chưa đủ mẫu
		}
		pi.cwndlevel = float64(p.sentPacketHandler.GetBytesInFlight()) / float64(pi.cwnd) // Eq. 4
		info[pid] = pi
	}

	// 3. Reward cho path vừa ACK (Eq. 6, dạng gộp như FQSAT dòng 113-122)
	pid := ackPth.pathID
	pi := info[pid]
	packets, _, losses := ackPth.sentPacketHandler.GetStatistics()
	dPkt, dLoss := packets-sch.prevPackets[pid], losses-sch.prevLosses[pid]
	sch.prevPackets[pid], sch.prevLosses[pid] = packets, losses
	lossRate := 0.0
	if dPkt > 0 {
		lossRate = float64(dLoss) / float64(dPkt)
	}
	rttMs := float64(pi.lrtt) / float64(time.Millisecond)
	penalty := FQ_ALPHA*rttMs/FQ_RTT_NORM_MS + FQ_BETA*5*lossRate
	reward := (1-FQ_ALPHA-FQ_BETA)*pi.cwndlevel - penalty

	// 4. State (Eq. 5, FQSAT dòng 125-126) và rời rạc hoá
	rate := func(x pinfo) float64 { return float64(x.cwnd) / float64(x.lrtt) }
	sum := rate(info[first]) + rate(info[second])
	fLevel := sch.discretize(rate(info[first]) / sum)
	sLevel := sch.discretize(rate(info[second]) / sum)

	// 5. Learning rate bằng fuzzy (§3.2.3) – chỗ FQSAT gốc bị comment
	if FQ_USE_FUZZY {
		vrtt := math.Abs(float64(pi.lrtt-pi.srtt)) / float64(pi.srtt) // Eq. 2
		sch.fqDelta = fuzzyLearningRate(vrtt, pi.cwndlevel)
	} else {
		sch.fqDelta = FQ_DELTA_FIXED
	}

	// 6. Bellman (Eq. 1, FQSAT dòng 200-204)
	col := int8(0)
	if pid == second {
		col = 1
	}
	maxNext := math.Max(sch.qtable[fLevel][sLevel][0], sch.qtable[fLevel][sLevel][1])
	q := &sch.qtable[sch.curStateF][sch.curStateS][col]
	*q = (1-sch.fqDelta)*(*q) + sch.fqDelta*(reward+FQ_GAMMA*maxNext)
	sch.curStateF, sch.curStateS = fLevel, sLevel

	// 7. Thống kê mượt để ra quyết định cờ
	sch.rewardEMA[pid] = 0.9*sch.rewardEMA[pid] + 0.1*reward
	sch.penaltyEMA[pid] = 0.9*sch.penaltyEMA[pid] + 0.1*penalty
	sch.updateReliability(s, first, second)
	sch.ackCount[pid]++ // sửa 4
	if sch.firstAckAt.IsZero() {
		sch.firstAckAt = time.Now()
	}

}

// updateReliability: từ Q-table suy ra cờ unreliable, có hysteresis
// func (sch *scheduler) updateReliability(s *session, first, second protocol.PathID) {
// 	qF := sch.qtable[sch.curStateF][sch.curStateS][0]
// 	qS := sch.qtable[sch.curStateF][sch.curStateS][1]
// 	now := time.Now()

// 	check := func(me, other protocol.PathID, qMe, qOther float64) {
// 		p, o := s.paths[me], s.paths[other]
// 		// Gắn cờ: dựa trên Q và reward đầy đủ (hai path đều đang được MinRTT dùng)
// 		worse := qMe < qOther-FQ_Q_MARGIN_BAD && sch.rewardEMA[me] < sch.rewardEMA[other]
// 		// Gỡ cờ: chỉ nhìn phần phạt (lRTT + loss), vì path bị loại chỉ còn traffic thăm dò
// 		// nên cwndlevel của nó luôn thấp, không so reward đầy đủ được
// 		recovered := sch.penaltyEMA[me] <= sch.penaltyEMA[other]+FQ_PEN_MARGIN_OK

//			if worse {
//				sch.badStreak[me]++
//			} else {
//				sch.badStreak[me] = 0
//			}
//			if recovered {
//				sch.goodStreak[me]++
//			} else {
//				sch.goodStreak[me] = 0
//			}
//			if now.Sub(sch.flagChanged[me]) < FQ_MIN_DWELL {
//				return
//			}
//			if !p.unreliable.Get() && sch.badStreak[me] >= FQ_BAD_STREAK &&
//				!o.unreliable.Get() && !o.potentiallyFailed.Get() {
//				p.unreliable.Set(true)
//				sch.flagChanged[me], sch.forceAdjust, sch.badStreak[me] = now, true, 0
//				utils.Infof("FQSAT: path %d -> UNRELIABLE (Q=%.3f vs %.3f, rEMA=%.3f vs %.3f)",
//					me, qMe, qOther, sch.rewardEMA[me], sch.rewardEMA[other])
//			} else if p.unreliable.Get() && sch.goodStreak[me] >= FQ_GOOD_STREAK {
//				p.unreliable.Set(false)
//				sch.flagChanged[me], sch.forceAdjust, sch.goodStreak[me] = now, true, 0
//				utils.Infof("FQSAT: path %d -> reliable again (pen=%.3f vs %.3f)",
//					me, sch.penaltyEMA[me], sch.penaltyEMA[other])
//			}
//		}
//		check(first, second, qF, qS)
//		check(second, first, qS, qF)
//	}
func (sch *scheduler) updateReliability(s *session, first, second protocol.PathID) {
	qF := sch.qtable[sch.curStateF][sch.curStateS][0]
	qS := sch.qtable[sch.curStateF][sch.curStateS][1]
	now := time.Now()
	fcRecent := !sch.fcBlockedAt.IsZero() && now.Sub(sch.fcBlockedAt) <= FQ_FC_RECENT
	fcIdle := sch.fcBlockedAt.IsZero() || now.Sub(sch.fcBlockedAt) > FQ_FC_RELEASE
	warm := sch.ackCount[first] >= FQ_MIN_ACKS && sch.ackCount[second] >= FQ_MIN_ACKS &&
		now.Sub(sch.firstAckAt) >= FQ_WARMUP

	check := func(me, other protocol.PathID, qMe, qOther float64) {
		p, o := s.paths[me], s.paths[other]
		// Gắn cờ: path này kém path kia rõ rệt (sửa 3) VÀ cửa sổ nhận đang là nút thắt (sửa 1)
		// worse := warm && fcRecent && sch.rewardEMA[me] < sch.rewardEMA[other]-FQ_R_MARGIN_BAD
		worse := warm && fcRecent && sch.penaltyEMA[me] > sch.penaltyEMA[other]+FQ_PEN_MARGIN_BAD
		// Gỡ cờ: phần phạt (lRTT + loss) về gần path kia, HOẶC kết nối đã lâu không bị chặn (sửa 1)
		recovered := sch.penaltyEMA[me] <= sch.penaltyEMA[other]+FQ_PEN_MARGIN_OK || fcIdle

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
			return
		}
		if !p.unreliable.Get() && sch.badStreak[me] >= FQ_BAD_STREAK &&
			!o.unreliable.Get() && !o.potentiallyFailed.Get() {
			p.unreliable.Set(true)
			sch.flagChanged[me], sch.forceAdjust, sch.badStreak[me] = now, true, 0
			utils.Infof("FQSAT: path %d -> UNRELIABLE (rEMA=%.3f vs %.3f, pen=%.3f vs %.3f, Q=%.3f vs %.3f, fcBlocked %.0fms ago)",
				me, sch.rewardEMA[me], sch.rewardEMA[other], sch.penaltyEMA[me], sch.penaltyEMA[other],
				qMe, qOther, float64(now.Sub(sch.fcBlockedAt))/1e6)
		} else if p.unreliable.Get() && sch.goodStreak[me] >= FQ_GOOD_STREAK {
			p.unreliable.Set(false)
			sch.flagChanged[me], sch.forceAdjust, sch.goodStreak[me] = now, true, 0
			why := "penalty ok"
			if fcIdle {
				why = "rwnd not limiting"
			}
			utils.Infof("FQSAT: path %d -> reliable again (%s; pen=%.3f vs %.3f)",
				me, why, sch.penaltyEMA[me], sch.penaltyEMA[other])
		}
	}
	check(first, second, qF, qS)
	check(second, first, qS, qF)
}

// pathUsable: điều kiện chung thay cho "InitialPathID || potentiallyFailed" ở các tầng
func pathUsable(p *path) bool {
	return p.pathID != protocol.InitialPathID && !p.potentiallyFailed.Get() && !p.unreliable.Get()
}

// probePaths (sửa 2): gửi PING lên path dữ liệu nào không có ACK trong FQ_PING_INTERVAL gần nhất.
// PING là frame retransmittable, nên PING mất được tính vào loss như gói dữ liệu.
func (sch *scheduler) probePaths(s *session) error {
	now := time.Now()
	var toProbe []*path
	s.pathsLock.RLock()
	for _, pth := range s.paths {
		if pth.pathID == protocol.InitialPathID || pth.potentiallyFailed.Get() {
			continue
		}
		if pth.rttStats.SmoothedRTT() == 0 { // chưa có mẫu RTT, để cơ chế duplicate của MPQUIC lo
			continue
		}
		// 		if now.Sub(pth.lastAck) > FQ_PING_INTERVAL && now.Sub(pth.lastProbe) > FQ_PING_INTERVAL {
		// 			toProbe = append(toProbe, pth)
		// 		}
		// 	}
		// 	s.pathsLock.RUnlock()
		// 	for _, pth := range toProbe {
		// 		pth.lastProbe = now
		// 		if err := s.sendPing(pth); err != nil {
		// 			return err
		// 		}
		// 	}
		// 	return nil
		// }

		interval := FQ_QUIET_PROBE // path chưa gắn cờ: chỉ giữ một mẫu/giây khi nó im lặng
		if pth.unreliable.Get() {
			interval = FQ_PING_INTERVAL // path bị gắn cờ: thăm dò dày để học và gỡ cờ
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
