// package quic

// import (
// 	"math/rand"
// 	"sort"
// 	"time"

// 	"github.com/qdeconinck/mp-quic/ackhandler"
// 	"github.com/qdeconinck/mp-quic/congestion"
// 	"github.com/qdeconinck/mp-quic/internal/protocol"
// 	"github.com/qdeconinck/mp-quic/internal/utils"
// 	"github.com/qdeconinck/mp-quic/internal/wire"
// )

// type PacketList struct {
// 	queue    []*packedFrames // some frames are supposed to be a packet but not sealed.
// 	len      int             // how many packet
// 	toPathid protocol.PathID
// }

// type packedFrames struct {
// 	frames    []wire.Frame // a slice of a slice of frames.
// 	queueTime time.Time
// }

// type scheduler struct {
// 	// XXX Currently round-robin based, inspired from MPTCP scheduler
// 	quotas            map[protocol.PathID]uint
// 	timer             *time.Timer
// 	packetsNotSentYet map[protocol.PathID]*PacketList

// 	// ---- FQSAT learning (đánh giá chất lượng path) ----
// 	qtable      [5][5][2]float64           // Q[s_path1][s_path2][action]
// 	clvState    [4]float64                 // ngưỡng rời rạc hoá state (Eq. 5)
// 	curStateF   int8                       // state hiện tại của path thứ nhất
// 	curStateS   int8                       // state hiện tại của path thứ hai
// 	fqDelta     float64                    // learning rate do fuzzy sinh ra
// 	prevPackets map[protocol.PathID]uint64 // để tính loss theo khoảng
// 	prevLosses  map[protocol.PathID]uint64
// 	rewardEMA   map[protocol.PathID]float64
// 	penaltyEMA  map[protocol.PathID]float64 // chỉ phần phạt lRTT + loss, dùng để phục hồi
// 	badStreak   map[protocol.PathID]int
// 	goodStreak  map[protocol.PathID]int
// 	flagChanged map[protocol.PathID]time.Time
// 	forceAdjust bool // yêu cầu adjustAllocation chạy ngay

// }

// const SCHE_ALGO = "TRR" // TRR, RR, or SAPS
// const INTERVAL = 1 * time.Second

// const (
// 	FQ_ENABLE        = true
// 	FQ_USE_FUZZY     = true // false => dùng FQ_DELTA_FIXED (biến thể Q-0.3 trong §4.3)
// 	FQ_ALPHA         = 0.2  // trọng số phạt lRTT trong reward (Eq. 6)
// 	FQ_BETA          = 0.2  // trọng số phạt loss
// 	FQ_GAMMA         = 0.4  // discount factor, bài báo khuyến nghị 0.3-0.7
// 	FQ_DELTA_FIXED   = 0.3
// 	FQ_RTT_NORM_MS   = 50.0 // chuẩn hoá lRTT, giữ như FQSAT (lRTT/50)
// 	FQ_PROBE_EPS     = 0.02 // tỉ lệ packet thăm dò trên path unreliable
// 	FQ_Q_MARGIN_BAD  = 0.20 // Q thấp hơn path kia quá biên này => xấu
// 	FQ_PEN_MARGIN_OK = 0.05 // phần phạt về gần path kia => phục hồi
// 	FQ_BAD_STREAK    = 300  // số ACK liên tiếp xấu trước khi gắn cờ
// 	FQ_GOOD_STREAK   = 300
// 	FQ_MIN_DWELL     = 2 * time.Second // chống flapping (Fig. 12)
// )

// var PORTION map[*path]float32

// func (sch *scheduler) setup() {
// 	sch.quotas = make(map[protocol.PathID]uint)
// 	sch.packetsNotSentYet = make(map[protocol.PathID]*PacketList)

// 	// FQSAT
// 	sch.clvState = [4]float64{0.3, 0.4, 0.5, 0.6} // đúng bài báo; repo FQSAT dùng 0.3/0.5/0.7/0.9
// 	sch.fqDelta = FQ_DELTA_FIXED
// 	sch.prevPackets = make(map[protocol.PathID]uint64)
// 	sch.prevLosses = make(map[protocol.PathID]uint64)
// 	sch.rewardEMA = make(map[protocol.PathID]float64)
// 	sch.penaltyEMA = make(map[protocol.PathID]float64)
// 	sch.badStreak = make(map[protocol.PathID]int)
// 	sch.goodStreak = make(map[protocol.PathID]int)
// 	sch.flagChanged = make(map[protocol.PathID]time.Time)

// }

// func (sch *scheduler) getRetransmission(s *session) (hasRetransmission bool, retransmitPacket *ackhandler.Packet, pth *path) {
// 	// check for retransmissions first
// 	for {
// 		// TODO add ability to reinject on another path
// 		// XXX We need to check on ALL paths if any packet should be first retransmitted
// 		s.pathsLock.RLock()
// 	retransmitLoop:
// 		for _, pthTmp := range s.paths {
// 			retransmitPacket = pthTmp.sentPacketHandler.DequeuePacketForRetransmission()
// 			if retransmitPacket != nil {
// 				pth = pthTmp
// 				break retransmitLoop
// 			}
// 		}
// 		s.pathsLock.RUnlock()
// 		if retransmitPacket == nil {
// 			break
// 		}
// 		hasRetransmission = true

// 		if retransmitPacket.EncryptionLevel != protocol.EncryptionForwardSecure {
// 			if s.handshakeComplete {
// 				// Don't retransmit handshake packets when the handshake is complete
// 				continue
// 			}
// 			utils.Debugf("\tDequeueing handshake retransmission for packet 0x%x", retransmitPacket.PacketNumber)
// 			return
// 		}
// 		utils.Debugf("\tDequeueing retransmission of packet 0x%x from path %d", retransmitPacket.PacketNumber, pth.pathID)
// 		// resend the frames that were in the packet
// 		for _, frame := range retransmitPacket.GetFramesForRetransmission() {
// 			switch f := frame.(type) {
// 			case *wire.StreamFrame:
// 				s.streamFramer.AddFrameForRetransmission(f)
// 			case *wire.WindowUpdateFrame:
// 				// only retransmit WindowUpdates if the stream is not yet closed and the we haven't sent another WindowUpdate with a higher ByteOffset for the stream
// 				// XXX Should it be adapted to multiple paths?
// 				currentOffset, err := s.flowControlManager.GetReceiveWindow(f.StreamID)
// 				if err == nil && f.ByteOffset >= currentOffset {
// 					s.packer.QueueControlFrame(f, pth)
// 				}
// 			case *wire.PathsFrame:
// 				// Schedule a new PATHS frame to send
// 				s.schedulePathsFrame()
// 			default:
// 				s.packer.QueueControlFrame(frame, pth)
// 			}
// 		}
// 	}
// 	return
// }

// func (sch *scheduler) selectPathRoundRobin(s *session, hasRetransmission bool, hasStreamRetransmission bool, fromPth *path) *path {
// 	if sch.quotas == nil {
// 		sch.setup()
// 	}

// 	// XXX Avoid using PathID 0 if there is more than 1 path
// 	if len(s.paths) <= 1 {
// 		if !hasRetransmission && !s.paths[protocol.InitialPathID].SendingAllowed() {
// 			return nil
// 		}
// 		return s.paths[protocol.InitialPathID]
// 	}

// 	// TODO cope with decreasing number of paths (needed?)
// 	var selectedPath *path
// 	var lowerQuota, currentQuota uint
// 	var ok bool

// 	// Max possible value for lowerQuota at the beginning
// 	lowerQuota = ^uint(0)

// pathLoop:
// 	for pathID, pth := range s.paths {
// 		// Don't block path usage if we retransmit, even on another path
// 		if !hasRetransmission && !pth.SendingAllowed() {
// 			continue pathLoop
// 		}

// 		// If this path is potentially failed, do no consider it for sending
// 		if pth.potentiallyFailed.Get() {
// 			continue pathLoop
// 		}

// 		// FQSAT: bỏ path unreliable, chỉ giữ một tỉ lệ nhỏ packet để thăm dò và tiếp tục học
// 		if pth.unreliable.Get() && rand.Float64() >= FQ_PROBE_EPS {
// 			continue pathLoop
// 		}

// 		// XXX Prevent using initial pathID if multiple paths
// 		if pathID == protocol.InitialPathID {
// 			continue pathLoop
// 		}

// 		currentQuota, ok = sch.quotas[pathID]
// 		if !ok {
// 			sch.quotas[pathID] = 0
// 			currentQuota = 0
// 		}

// 		if currentQuota < lowerQuota {
// 			selectedPath = pth
// 			lowerQuota = currentQuota
// 		}
// 	}

// 	return selectedPath

// }

// func (sch *scheduler) selectPathLowLatency(s *session, hasRetransmission bool, hasStreamRetransmission bool, fromPth *path) *path {
// 	// XXX Avoid using PathID 0 if there is more than 1 path
// 	if len(s.paths) <= 1 {
// 		if !hasRetransmission && !s.paths[protocol.InitialPathID].SendingAllowed() {
// 			return nil
// 		}
// 		return s.paths[protocol.InitialPathID]
// 	}

// 	// FIXME Only works at the beginning... Cope with new paths during the connection
// 	if hasRetransmission && hasStreamRetransmission && fromPth.rttStats.SmoothedRTT() == 0 {
// 		// Is there any other path with a lower number of packet sent?
// 		currentQuota := sch.quotas[fromPth.pathID]
// 		for pathID, pth := range s.paths {
// 			if pathID == protocol.InitialPathID || pathID == fromPth.pathID {
// 				continue
// 			}
// 			// The congestion window was checked when duplicating the packet
// 			if sch.quotas[pathID] < currentQuota {
// 				return pth
// 			}
// 		}
// 	}

// 	var selectedPath *path
// 	var lowerRTT time.Duration
// 	var currentRTT time.Duration
// 	selectedPathID := protocol.PathID(255)

// pathLoop:
// 	for pathID, pth := range s.paths {
// 		// Don't block path usage if we retransmit, even on another path
// 		if !hasRetransmission && !pth.SendingAllowed() {
// 			if SCHE_ALGO == "TRR" {
// 				pth.congestionlimited = time.Now()
// 			}
// 			continue pathLoop
// 		}

// 		// If this path is potentially failed, do not consider it for sending
// 		if pth.potentiallyFailed.Get() {
// 			continue pathLoop
// 		}

// 		// XXX Prevent using initial pathID if multiple paths
// 		if pathID == protocol.InitialPathID {
// 			continue pathLoop
// 		}

// 		currentRTT = pth.rttStats.SmoothedRTT()

// 		// Prefer staying single-path if not blocked by current path
// 		// Don't consider this sample if the smoothed RTT is 0
// 		if lowerRTT != 0 && currentRTT == 0 {
// 			continue pathLoop
// 		}

// 		// Case if we have multiple paths unprobed
// 		if currentRTT == 0 {
// 			currentQuota, ok := sch.quotas[pathID]
// 			if !ok {
// 				sch.quotas[pathID] = 0
// 				currentQuota = 0
// 			}
// 			lowerQuota, _ := sch.quotas[selectedPathID]
// 			if selectedPath != nil && currentQuota > lowerQuota {
// 				continue pathLoop
// 			}
// 		}

// 		if currentRTT != 0 && lowerRTT != 0 && selectedPath != nil && currentRTT >= lowerRTT {
// 			continue pathLoop
// 		}

// 		// Update
// 		lowerRTT = currentRTT
// 		selectedPath = pth
// 		selectedPathID = pathID
// 	}

// 	return selectedPath
// }

// func (sch *scheduler) sendingQueueEmpty(pth *path) bool {
// 	if sch.packetsNotSentYet[pth.pathID] == nil {
// 		sch.packetsNotSentYet[pth.pathID] = &PacketList{
// 			queue:    make([]*packedFrames, 0),
// 			len:      0,
// 			toPathid: pth.pathID,
// 		}
// 	}
// 	return len(sch.packetsNotSentYet[pth.pathID].queue) == 0
// }

// func (sch *scheduler) calculateArrivalTime(s *session, pth *path, addMeanDeviation bool) (time.Duration, bool) {

// 	packetSize := protocol.MaxPacketSize * 8 //bit uint64
// 	var pthBwd protocol.ByteCount
// 	if pth.rttStats.SmoothedRTT() > 0 {
// 		pthBwd = (pth.sentPacketHandler.GetCongestionWindow() * protocol.ByteCount(time.Second) * protocol.ByteCount(congestion.BytesPerSecond)) / protocol.ByteCount(pth.rttStats.SmoothedRTT())
// 	}
// 	inSecond := uint64(time.Second)
// 	var rtt time.Duration
// 	if addMeanDeviation {
// 		rtt = pth.rttStats.SmoothedRTT() + pth.rttStats.MeanDeviation()
// 	} else {
// 		rtt = pth.rttStats.SmoothedRTT()

// 	}
// 	if pthBwd == 0 {
// 		return rtt / 2, false
// 	}
// 	if rtt == 0 {
// 		return 0, true
// 	}
// 	writeQueue, ok := sch.packetsNotSentYet[pth.pathID]
// 	var writeQueueSize protocol.ByteCount
// 	if !ok {
// 		writeQueueSize = 0
// 	} else {
// 		writeQueueSize = protocol.ByteCount(writeQueue.len) * protocol.DefaultTCPMSS * 8 //in bit
// 		//protocol.DefaultTCPMSS MaxPacketSize
// 	}

// 	arrivalTime := (uint64(packetSize+writeQueueSize)*inSecond)/uint64(pthBwd) + uint64(rtt)/2 //in nanosecond
// 	return time.Duration(arrivalTime), true
// }

// func (sch *scheduler) queueFrames(frames []wire.Frame, pth *path) {
// 	if sch.packetsNotSentYet[pth.pathID] == nil {
// 		sch.packetsNotSentYet[pth.pathID] = &PacketList{
// 			queue:    make([]*packedFrames, 0),
// 			len:      0,
// 			toPathid: pth.pathID,
// 		}
// 	}
// 	packetList := sch.packetsNotSentYet[pth.pathID]
// 	packetList.queue = append(packetList.queue, &packedFrames{frames, time.Now()})
// 	packetList.len += 1
// }

// func (sch *scheduler) dequeueStoredFrames(pth *path) []wire.Frame {

// 	packetList := sch.packetsNotSentYet[pth.pathID]
// 	if len(packetList.queue) == 0 {
// 		return nil
// 	}
// 	packet := packetList.queue[0]
// 	// Shift the slice and don't retain anything that isn't needed.
// 	copy(packetList.queue, packetList.queue[1:])
// 	packetList.queue[len(packetList.queue)-1] = nil
// 	packetList.queue = packetList.queue[:len(packetList.queue)-1]
// 	// Update statistics
// 	packetList.len -= 1

// 	return packet.frames
// }

// func (sch *scheduler) selectPathByArrivalTime(s *session, hasRetransmission bool, hasStreamRetransmission bool, fromPth *path) (selectedPath *path) {
// 	if s.perspective == protocol.PerspectiveClient {
// 		return sch.selectPathLowLatency(s, hasRetransmission, hasStreamRetransmission, fromPth)
// 	}
// 	// XXX Avoid using PathID 0 if there is more than 1 path
// 	if len(s.paths) <= 1 {
// 		if !s.paths[protocol.InitialPathID].SendingAllowed() {
// 			return nil
// 		}
// 		selectedPath = s.paths[protocol.InitialPathID]
// 		return selectedPath
// 	}
// 	// FIXME Only works at the beginning... Cope with new paths during the connection
// 	if hasRetransmission && hasStreamRetransmission && fromPth.rttStats.SmoothedRTT() == 0 {
// 		// Is there any other path with a lower number of packet sent?
// 		currentQuota := sch.quotas[fromPth.pathID]
// 		for pathID, pth := range s.paths {
// 			if pathID == protocol.InitialPathID || pathID == fromPth.pathID {
// 				continue
// 			}
// 			// The congestion window was checked when duplicating the packet
// 			if sch.quotas[pathID] < currentQuota {
// 				return pth
// 			}
// 		}
// 	}

// 	for _, pth := range s.paths {
// 		if pth != nil && !sch.sendingQueueEmpty(pth) {
// 			if pth.SendingAllowed() {
// 				return pth
// 			}
// 		}
// 	}
// 	// var currentRTT time.Duration
// 	var currentArrivalTime time.Duration
// 	var lowerArrivalTime time.Duration
// 	selectedPathID := protocol.PathID(255)
// 	var allCwndLimited bool = true

// 	//find the best path, including that is limited by SendingAllowed()
// pathLoop:
// 	for pathID, pth := range s.paths {

// 		// If this path is potentially failed, do not consider it for sending
// 		if pth.potentiallyFailed.Get() {
// 			continue pathLoop
// 		}

// 		// XXX Prevent using initial pathID if multiple paths
// 		if pathID == protocol.InitialPathID {
// 			continue pathLoop
// 		}

// 		// return nil if all paths are limited by cwnd
// 		allCwndLimited = allCwndLimited && (!hasRetransmission && !pth.SendingAllowed())

// 		// currentRTT = pth.rttStats.SmoothedRTT() // if SmoothedRTT == 0, send on it. Because it will be duplicated to other paths. TODO maybe not?
// 		// currentArrivalTime, _ = sch.calculateArrivalTime(s, pth, false)
// 		currentArrivalTime, _ = sch.calculateArrivalTime(s, pth, false)
// 		// currentArrivalTime = pth.rttStats.SmoothedRTT()

// 		// Prefer staying single-path if not blocked by current path
// 		// Don't consider this sample if the smoothed RTT is 0
// 		if lowerArrivalTime != 0 && currentArrivalTime == 0 {
// 			continue pathLoop
// 		}

// 		// Case if we have multiple paths unprobed
// 		// currentArrivalTime == 0 means rtt == 0
// 		if currentArrivalTime == 0 {
// 			currentQuota, ok := sch.quotas[pathID]
// 			if !ok {
// 				sch.quotas[pathID] = 0
// 				currentQuota = 0
// 			}
// 			lowerQuota, _ := sch.quotas[selectedPathID]
// 			if selectedPath != nil && currentQuota > lowerQuota {
// 				continue pathLoop
// 			}
// 		}

// 		if currentArrivalTime != 0 && lowerArrivalTime != 0 && selectedPath != nil && currentArrivalTime >= lowerArrivalTime {
// 			continue pathLoop
// 		}

// 		// Update
// 		lowerArrivalTime = currentArrivalTime
// 		selectedPath = pth
// 		selectedPathID = pathID
// 	}
// 	if allCwndLimited {
// 		return nil
// 	}
// 	return selectedPath
// }

// // Lock of s.paths must be held
// func (sch *scheduler) selectPath(s *session, hasRetransmission bool, hasStreamRetransmission bool, fromPth *path) *path {
// 	// XXX Currently round-robin
// 	// TODO select the right scheduler dynamically
// 	if SCHE_ALGO == "SAPS" {
// 		return sch.selectPathByArrivalTime(s, hasRetransmission, hasStreamRetransmission, fromPth)
// 	}
// 	return sch.selectPathLowLatency(s, hasRetransmission, hasStreamRetransmission, fromPth)
// 	// return sch.selectPathRoundRobin(s, hasRetransmission, hasStreamRetransmission, fromPth)
// }

// // Lock of s.paths must be free (in case of log print)
// func (sch *scheduler) performPacketSending(s *session, windowUpdateFrames []*wire.WindowUpdateFrame, pth *path) (*ackhandler.Packet, bool, error) {
// 	// add a retransmittable frame
// 	if pth.sentPacketHandler.ShouldSendRetransmittablePacket() {
// 		s.packer.QueueControlFrame(&wire.PingFrame{}, pth)
// 	}
// 	var err error
// 	var packet *packedPacket

// 	if SCHE_ALGO == "SAPS" {
// 		if pth.SendingAllowed() && sch.sendingQueueEmpty(pth) { //normally
// 			packet, err = s.packer.PackPacket(pth)
// 			if err != nil || packet == nil {
// 				return nil, false, err
// 			}
// 		} else if !pth.SendingAllowed() {
// 			stored, err := s.packer.StoreFrames(s, pth)
// 			if stored {
// 				return nil, true, err // here the "sent" bool is set to true, then the loop outside will not break
// 			} else {
// 				return nil, false, err // here the "sent" bool is set to true, then the loop outside will not break
// 			}
// 		} else {
// 			packet, err = s.packer.PackPacketWithStoreFrames(pth)
// 			if err != nil || packet == nil {
// 				return nil, false, err
// 			}
// 		}
// 	} else {
// 		packet, err = s.packer.PackPacket(pth)
// 		if err != nil || packet == nil {
// 			return nil, false, err
// 		}
// 	}

// 	if err = s.sendPackedPacket(packet, pth); err != nil {
// 		return nil, false, err
// 	}

// 	// send every window update twice
// 	for _, f := range windowUpdateFrames {
// 		s.packer.QueueControlFrame(f, pth)
// 	}

// 	// Packet sent, so update its quota
// 	sch.quotas[pth.pathID]++

// 	// Provide some logging if it is the last packet
// 	for _, frame := range packet.frames {
// 		switch frame := frame.(type) {
// 		case *wire.StreamFrame:
// 			if frame.FinBit {
// 				// Last packet to send on the stream, print stats
// 				// s.pathsLock.RLock()
// 				// utils.Infof("Info for stream %x of %x", frame.StreamID, s.connectionID)
// 				// for pathID, pth := range s.paths {
// 				// 	sntPkts, sntRetrans, sntLost := pth.sentPacketHandler.GetStatistics()
// 				// 	rcvPkts := pth.receivedPacketHandler.GetStatistics()
// 				// 	utils.Infof("Path %x: sent %d retrans %d lost %d; rcv %d rtt %v", pathID, sntPkts, sntRetrans, sntLost, rcvPkts, pth.rttStats.SmoothedRTT())
// 				// }
// 				// s.pathsLock.RUnlock()
// 			}
// 		default:
// 		}
// 	}

// 	pkt := &ackhandler.Packet{
// 		PacketNumber:    packet.number,
// 		Frames:          packet.frames,
// 		Length:          protocol.ByteCount(len(packet.raw)),
// 		EncryptionLevel: packet.encryptionLevel,
// 	}

// 	return pkt, true, nil
// }

// // Lock of s.paths must be free
// func (sch *scheduler) ackRemainingPaths(s *session, totalWindowUpdateFrames []*wire.WindowUpdateFrame) error {
// 	// Either we run out of data, or CWIN of usable paths are full
// 	// Send ACKs on paths not yet used, if needed. Either we have no data to send and
// 	// it will be a pure ACK, or we will have data in it, but the CWIN should then
// 	// not be an issue.
// 	s.pathsLock.RLock()
// 	defer s.pathsLock.RUnlock()
// 	// get WindowUpdate frames
// 	// this call triggers the flow controller to increase the flow control windows, if necessary
// 	windowUpdateFrames := totalWindowUpdateFrames
// 	if len(windowUpdateFrames) == 0 {
// 		windowUpdateFrames = s.getWindowUpdateFrames(s.peerBlocked)
// 	}
// 	for _, pthTmp := range s.paths {
// 		ackTmp := pthTmp.GetAckFrame()
// 		for _, wuf := range windowUpdateFrames {
// 			s.packer.QueueControlFrame(wuf, pthTmp)
// 		}
// 		if ackTmp != nil || len(windowUpdateFrames) > 0 {
// 			if (pthTmp.pathID == protocol.InitialPathID || pthTmp.potentiallyFailed.Get()) && ackTmp == nil {
// 				continue
// 			}
// 			swf := pthTmp.GetStopWaitingFrame(false)
// 			if swf != nil {
// 				s.packer.QueueControlFrame(swf, pthTmp)
// 			}
// 			s.packer.QueueControlFrame(ackTmp, pthTmp)
// 			// XXX (QDC) should we instead call PackPacket to provides WUFs?
// 			var packet *packedPacket
// 			var err error
// 			if ackTmp != nil {
// 				// Avoid internal error bug
// 				packet, err = s.packer.PackAckPacket(pthTmp)
// 			} else {
// 				packet, err = s.packer.PackPacket(pthTmp)
// 			}
// 			if err != nil {
// 				return err
// 			}
// 			err = s.sendPackedPacket(packet, pthTmp)
// 			if err != nil {
// 				return err
// 			}
// 		}
// 	}
// 	s.peerBlocked = false
// 	return nil
// }

// func (sch *scheduler) streamAllocation(strm *stream, s *session) {
// 	if strm.streamID == 1 {
// 		return
// 	}
// 	var bestpath *path
// 	for _, pth := range s.paths {
// 		if !pathUsable(pth) {
// 			continue
// 		}

// 		if bestpath == nil {
// 			bestpath = pth
// 			continue
// 		}
// 		if pth.rttStats.SmoothedRTT() != 0 && pth.rttStats.SmoothedRTT() < bestpath.rttStats.SmoothedRTT() {
// 			bestpath = pth
// 		}
// 	}
// 	strm.path = bestpath
// }

// func (sch *scheduler) adjustAllocation(s *session) {
// 	// FQSAT: chuyển mọi stream đang gán vào path unreliable/failed sang path tốt nhất còn lại
// 	var bestPath *path
// 	for _, pth := range s.paths {
// 		if !pathUsable(pth) {
// 			continue
// 		}
// 		if bestPath == nil || (pth.rttStats.SmoothedRTT() != 0 &&
// 			pth.rttStats.SmoothedRTT() < bestPath.rttStats.SmoothedRTT()) {
// 			bestPath = pth
// 		}
// 	}
// 	if bestPath != nil {
// 		for _, streamID := range s.streamsMap.openStreams {
// 			if streamID == 1 {
// 				continue
// 			}
// 			st := s.streamsMap.streams[streamID]
// 			if st.path != nil && !pathUsable(st.path) {
// 				st.path = bestPath
// 			}
// 		}
// 	}

// 	// compute the related statistics from packetSent of every stream
// 	packetAcSent := make(map[protocol.PathID]int)
// 	packetShSent := make(map[protocol.PathID]int)
// 	packetSent := make(map[protocol.StreamID]int)
// 	for _, streamID := range s.streamsMap.openStreams {
// 		if streamID == 1 {
// 			continue
// 		}
// 		stream := s.streamsMap.streams[streamID]
// 		// utils.Infof("stream %v path %v token %v", stream.streamID, stream.path.pathID, stream.token)
// 		for pathID, pkt := range stream.packetSent {
// 			// utils.Infof("stream %v sent %v packets on path %v", stream.streamID, pkt, pathID)
// 			packetAcSent[pathID] += pkt
// 			packetShSent[stream.path.pathID] += pkt
// 			packetSent[stream.streamID] += pkt
// 		}
// 	}
// 	for _, pth := range s.paths {
// 		if !pathUsable(pth) {
// 			continue
// 		}
// 		// utils.Infof("path %v cwnd %v rtt %dms", pth.pathID, pth.sentPacketHandler.GetCongestionWindow(),pth.rttStats.SmoothedRTT()/time.Millisecond)
// 	}
// 	// utils.Infof("RWND %v", s.streamFramer.flowControlManager.RemainingConnectionWindowSize())

// 	// adjust the stream allocation on each path
// 	paths_RTT := make([]*path, 0)
// 	for _, pth := range s.paths {
// 		if !pathUsable(pth) {
// 			continue
// 		}
// 		paths_RTT = append(paths_RTT, pth)
// 	}
// 	sort.SliceStable(paths_RTT, func(i, j int) bool {
// 		return paths_RTT[i].rttStats.SmoothedRTT() < paths_RTT[j].rttStats.SmoothedRTT()
// 	})

// 	var delta int
// 	for i, pth := range paths_RTT {
// 		if i == len(paths_RTT)-1 {
// 			break
// 		}

// 		delta = packetShSent[pth.pathID] - packetAcSent[pth.pathID]

// 		for delta > 0 {
// 			var stream_tbd *stream
// 			var stream_max *stream
// 			for _, streamID := range s.streamsMap.openStreams {
// 				if streamID == 1 {
// 					continue
// 				}
// 				stream := s.streamsMap.streams[streamID]
// 				if stream.path == pth && packetSent[streamID] >= delta {
// 					if stream_tbd == nil || packetSent[streamID] < packetSent[stream_tbd.streamID] {
// 						stream_tbd = stream
// 					}
// 				}
// 				if stream.path == pth && (stream_max == nil || packetSent[streamID] > packetSent[stream_max.streamID]) {
// 					stream_max = stream
// 				}
// 			}
// 			if stream_tbd == nil {
// 				stream_tbd = stream_max
// 			}
// 			if stream_tbd != nil {
// 				//utils.Infof("remove: stream %v is removed from path %v to path %v", stream_tbd.streamID, pth.pathID, paths_RTT[len(paths_RTT)-1].pathID)
// 				stream_tbd.path = paths_RTT[len(paths_RTT)-1]
// 				delta -= packetSent[stream_tbd.streamID]
// 				packetShSent[pth.pathID] -= packetSent[stream_tbd.streamID]
// 				packetShSent[paths_RTT[len(paths_RTT)-1].pathID] += packetSent[stream_tbd.streamID]
// 			}
// 		}

// 		for {
// 			var stream_tbd *stream
// 			for _, streamID := range s.streamsMap.openStreams {
// 				if streamID == 1 {
// 					continue
// 				}
// 				stream := s.streamsMap.streams[streamID]
// 				if stream.path.rttStats.SmoothedRTT() <= pth.rttStats.SmoothedRTT() {
// 					continue
// 				}
// 				if stream.path != pth && packetSent[streamID]+delta <= 0 {
// 					if stream_tbd == nil || packetSent[streamID] > packetSent[stream_tbd.streamID] {
// 						stream_tbd = stream
// 					}
// 				}
// 			}
// 			if stream_tbd == nil {
// 				break
// 			}
// 			//utils.Infof("add: stream %v is allocated from path %v to path %v", stream_tbd.streamID, stream_tbd.path.pathID, pth.pathID)
// 			delta += packetSent[stream_tbd.streamID]
// 			packetShSent[pth.pathID] += packetSent[stream_tbd.streamID]
// 			packetShSent[stream_tbd.path.pathID] -= packetSent[stream_tbd.streamID]
// 			stream_tbd.path = pth
// 		}
// 	}

// 	// record the maximum portion of single allocated stream on each path for the later computation of threshold
// 	if PORTION == nil {
// 		PORTION = make(map[*path]float32)
// 	}
// 	for _, pth := range s.paths {
// 		if !pathUsable(pth) {
// 			continue
// 		}
// 		PORTION[pth] = 0
// 	}
// 	for _, streamID := range s.streamsMap.openStreams {
// 		if streamID == 1 {
// 			continue
// 		}
// 		stream := s.streamsMap.streams[streamID]
// 		if packetShSent[stream.path.pathID] > 0 &&
// 			float32(packetSent[streamID])/float32(packetShSent[stream.path.pathID]) > PORTION[stream.path] {

// 			PORTION[stream.path] = float32(packetSent[streamID]) / float32(packetShSent[stream.path.pathID])
// 		}
// 	}
// }

// func (sch *scheduler) resetStatistics(s *session) {
// 	if sch.timer == nil {
// 		sch.timer = time.NewTimer(INTERVAL)
// 	} else {
// 		sch.timer.Reset(INTERVAL)
// 	}
// 	for _, streamID := range s.streamsMap.openStreams {
// 		if streamID == 1 {
// 			continue
// 		}
// 		stream := s.streamsMap.streams[streamID]
// 		stream.packetSent = make(map[protocol.PathID]int)
// 	}
// }

// func (sch *scheduler) sendPacket(s *session) error {
// 	var pth *path

// 	// Update leastUnacked value of paths
// 	s.pathsLock.RLock()
// 	for _, pthTmp := range s.paths {
// 		pthTmp.SetLeastUnacked(pthTmp.sentPacketHandler.GetLeastUnacked())
// 	}
// 	s.pathsLock.RUnlock()

// 	// get WindowUpdate frames
// 	// this call triggers the flow controller to increase the flow control windows, if necessary
// 	windowUpdateFrames := s.getWindowUpdateFrames(false)
// 	for _, wuf := range windowUpdateFrames {
// 		s.packer.QueueControlFrame(wuf, pth)
// 	}

// 	// Repeatedly try sending until we don't have any more data, or run out of the congestion window

// 	for {
// 		// Allocate path bandwidth to streams periodcally
// 		if SCHE_ALGO == "TRR" {
// 			if sch.timer == nil {
// 				sch.timer = time.NewTimer(INTERVAL)
// 			}
// 			select {
// 			case <-sch.timer.C:
// 				sch.adjustAllocation(s)
// 				sch.resetStatistics(s)
// 			default:
// 				if sch.forceAdjust {
// 					sch.forceAdjust = false
// 					sch.adjustAllocation(s)
// 					sch.resetStatistics(s)
// 				}
// 			}

// 		}

// 		// We first check for retransmissions
// 		hasRetransmission, retransmitHandshakePacket, fromPth := sch.getRetransmission(s)
// 		// XXX There might still be some stream frames to be retransmitted
// 		hasStreamRetransmission := s.streamFramer.HasFramesForRetransmission()

// 		// Select the path here
// 		s.pathsLock.RLock()
// 		pth = sch.selectPath(s, hasRetransmission, hasStreamRetransmission, fromPth)
// 		s.pathsLock.RUnlock()

// 		// XXX No more path available, should we have a new QUIC error message?
// 		if pth == nil {
// 			windowUpdateFrames := s.getWindowUpdateFrames(false)
// 			return sch.ackRemainingPaths(s, windowUpdateFrames)
// 		}

// 		// If we have an handshake packet retransmission, do it directly
// 		if hasRetransmission && retransmitHandshakePacket != nil {
// 			s.packer.QueueControlFrame(pth.sentPacketHandler.GetStopWaitingFrame(true), pth)
// 			packet, err := s.packer.PackHandshakeRetransmission(retransmitHandshakePacket, pth)
// 			if err != nil {
// 				return err
// 			}
// 			if err = s.sendPackedPacket(packet, pth); err != nil {
// 				return err
// 			}
// 			continue
// 		}

// 		// XXX Some automatic ACK generation should be done someway
// 		var ack *wire.AckFrame

// 		ack = pth.GetAckFrame()
// 		if ack != nil {
// 			s.packer.QueueControlFrame(ack, pth)
// 		}
// 		if ack != nil || hasStreamRetransmission {
// 			swf := pth.sentPacketHandler.GetStopWaitingFrame(hasStreamRetransmission)
// 			if swf != nil {
// 				s.packer.QueueControlFrame(swf, pth)
// 			}
// 		}

// 		// Also add CLOSE_PATH frames, if any
// 		for cpf := s.streamFramer.PopClosePathFrame(); cpf != nil; cpf = s.streamFramer.PopClosePathFrame() {
// 			s.packer.QueueControlFrame(cpf, pth)
// 		}

// 		// Also add ADD ADDRESS frames, if any
// 		for aaf := s.streamFramer.PopAddAddressFrame(); aaf != nil; aaf = s.streamFramer.PopAddAddressFrame() {
// 			s.packer.QueueControlFrame(aaf, pth)
// 		}

// 		// Also add PATHS frames, if any
// 		for pf := s.streamFramer.PopPathsFrame(); pf != nil; pf = s.streamFramer.PopPathsFrame() {
// 			s.packer.QueueControlFrame(pf, pth)
// 		}

// 		pkt, sent, err := sch.performPacketSending(s, windowUpdateFrames, pth)
// 		if err != nil {
// 			return err
// 		}
// 		windowUpdateFrames = nil
// 		if !sent {
// 			// Prevent sending empty packets
// 			return sch.ackRemainingPaths(s, windowUpdateFrames)
// 		}

// 		// Duplicate traffic when it was sent on an unknown performing path
// 		// FIXME adapt for new paths coming during the connection
// 		if pth.rttStats.SmoothedRTT() == 0 {
// 			currentQuota := sch.quotas[pth.pathID]
// 			// Was the packet duplicated on all potential paths?
// 		duplicateLoop:
// 			for pathID, tmpPth := range s.paths {
// 				if pathID == protocol.InitialPathID || pathID == pth.pathID {
// 					continue
// 				}
// 				if sch.quotas[pathID] < currentQuota && tmpPth.sentPacketHandler.SendingAllowed() {
// 					// Duplicate it
// 					pth.sentPacketHandler.DuplicatePacket(pkt)
// 					break duplicateLoop
// 				}
// 			}
// 		}

// 		// And try pinging on potentially failed paths
// 		if fromPth != nil && fromPth.potentiallyFailed.Get() {
// 			err = s.sendPing(fromPth)
// 			if err != nil {
// 				return err
// 			}
// 		}
// 	}
// }

package quic

import (
	"math/rand"
	"sort"
	"time"

	"github.com/qdeconinck/mp-quic/ackhandler"
	"github.com/qdeconinck/mp-quic/congestion"
	"github.com/qdeconinck/mp-quic/internal/protocol"
	"github.com/qdeconinck/mp-quic/internal/utils"
	"github.com/qdeconinck/mp-quic/internal/wire"
)

type PacketList struct {
	queue    []*packedFrames // some frames are supposed to be a packet but not sealed.
	len      int             // how many packet
	toPathid protocol.PathID
}

type packedFrames struct {
	frames    []wire.Frame // a slice of a slice of frames.
	queueTime time.Time
}

type scheduler struct {
	// XXX Currently round-robin based, inspired from MPTCP scheduler
	quotas            map[protocol.PathID]uint
	timer             *time.Timer
	packetsNotSentYet map[protocol.PathID]*PacketList

	// ---- FQSAT learning (đánh giá chất lượng path) ----
	qtable      [5][5][2]float64           // Q[s_path1][s_path2][action]
	clvState    [4]float64                 // ngưỡng rời rạc hoá state (Eq. 5)
	curStateF   int8                       // state hiện tại của path thứ nhất
	curStateS   int8                       // state hiện tại của path thứ hai
	fqDelta     float64                    // learning rate do fuzzy sinh ra
	prevPackets map[protocol.PathID]uint64 // để tính loss theo khoảng
	prevLosses  map[protocol.PathID]uint64
	rewardEMA   map[protocol.PathID]float64
	penaltyEMA  map[protocol.PathID]float64 // chỉ phần phạt lRTT + loss, dùng để phục hồi
	badStreak   map[protocol.PathID]int
	goodStreak  map[protocol.PathID]int
	flagChanged map[protocol.PathID]time.Time
	forceAdjust bool // yêu cầu adjustAllocation chạy ngay
	// sửa 1: lần gần nhất kết nối bị flow control (cửa sổ nhận) chặn, do stream_framer ghi
	fcBlockedAt time.Time
	// sửa 4: số ACK đã nhận trên mỗi path và thời điểm ACK đầu tiên, để không gắn cờ khi chưa
	// đủ mẫu hoặc còn trong pha khởi động (slow start)
	ackCount   map[protocol.PathID]uint64
	firstAckAt time.Time
}

const SCHE_ALGO = "TRR" // TRR, RR, or SAPS
const INTERVAL = 1 * time.Second

const (
	FQ_ENABLE      = true
	FQ_USE_FUZZY   = true // false => dùng FQ_DELTA_FIXED (biến thể Q-0.3 trong §4.3)
	FQ_ALPHA       = 0.2  // trọng số phạt lRTT trong reward (Eq. 6)
	FQ_BETA        = 0.2  // trọng số phạt loss
	FQ_GAMMA       = 0.4  // discount factor, bài báo khuyến nghị 0.3-0.7
	FQ_DELTA_FIXED = 0.3
	FQ_RTT_NORM_MS = 50.0 // chuẩn hoá lRTT, giữ như FQSAT (lRTT/50)
	// FQ_PROBE_EPS     = 0.02 // tỉ lệ packet thăm dò trên path unreliable
	FQ_PROBE_EPS      = 0.0
	FQ_Q_MARGIN_BAD   = 0.20 // Q thấp hơn path kia quá biên này => xấu
	FQ_PEN_MARGIN_BAD = 0.15 // gắn cờ khi phạt (lRTT+loss) cao hơn path kia quá biên này (≈ +37 ms RTT)
	FQ_PEN_MARGIN_OK  = 0.05 // phần phạt về gần path kia => phục hồi
	FQ_BAD_STREAK     = 300  // số ACK liên tiếp xấu trước khi gắn cờ
	FQ_GOOD_STREAK    = 300
	FQ_MIN_DWELL      = 2 * time.Second // chống flapping (Fig. 12)
	FQ_R_MARGIN_BAD   = 0.15            // sửa 3: rewardEMA thấp hơn path kia quá biên này => xấu
	// sửa 1: chỉ gắn cờ khi kết nối bị cửa sổ nhận chặn trong FQ_FC_RECENT gần nhất;
	//        gỡ cờ khi không bị chặn suốt FQ_FC_RELEASE (path chậm lúc đó lại có ích)
	FQ_FC_RECENT  = 1 * time.Second
	FQ_FC_RELEASE = 5 * time.Second
	// sửa 2: PING thăm dò path nào không có ACK trong khoảng này
	FQ_PING_INTERVAL = 100 * time.Millisecond
	FQ_QUIET_PROBE   = 1 * time.Second // path chưa gắn cờ nhưng im lặng
	// sửa 4: mỗi path phải có ít nhất FQ_MIN_ACKS ACK, và kết nối đã chạy ít nhất FQ_WARMUP
	//        kể từ ACK đầu tiên, mới được xét gắn cờ
	FQ_MIN_ACKS = 500
	FQ_WARMUP   = 3 * time.Second
)

var PORTION map[*path]float32

func (sch *scheduler) setup() {
	sch.quotas = make(map[protocol.PathID]uint)
	sch.packetsNotSentYet = make(map[protocol.PathID]*PacketList)

	// FQSAT
	sch.clvState = [4]float64{0.3, 0.4, 0.5, 0.6} // đúng bài báo; repo FQSAT dùng 0.3/0.5/0.7/0.9
	sch.fqDelta = FQ_DELTA_FIXED
	sch.prevPackets = make(map[protocol.PathID]uint64)
	sch.prevLosses = make(map[protocol.PathID]uint64)
	sch.rewardEMA = make(map[protocol.PathID]float64)
	sch.penaltyEMA = make(map[protocol.PathID]float64)
	sch.badStreak = make(map[protocol.PathID]int)
	sch.goodStreak = make(map[protocol.PathID]int)
	sch.flagChanged = make(map[protocol.PathID]time.Time)
	sch.ackCount = make(map[protocol.PathID]uint64)

}

func (sch *scheduler) getRetransmission(s *session) (hasRetransmission bool, retransmitPacket *ackhandler.Packet, pth *path) {
	// check for retransmissions first
	for {
		// TODO add ability to reinject on another path
		// XXX We need to check on ALL paths if any packet should be first retransmitted
		s.pathsLock.RLock()
	retransmitLoop:
		for _, pthTmp := range s.paths {
			retransmitPacket = pthTmp.sentPacketHandler.DequeuePacketForRetransmission()
			if retransmitPacket != nil {
				pth = pthTmp
				break retransmitLoop
			}
		}
		s.pathsLock.RUnlock()
		if retransmitPacket == nil {
			break
		}
		hasRetransmission = true

		if retransmitPacket.EncryptionLevel != protocol.EncryptionForwardSecure {
			if s.handshakeComplete {
				// Don't retransmit handshake packets when the handshake is complete
				continue
			}
			utils.Debugf("\tDequeueing handshake retransmission for packet 0x%x", retransmitPacket.PacketNumber)
			return
		}
		utils.Debugf("\tDequeueing retransmission of packet 0x%x from path %d", retransmitPacket.PacketNumber, pth.pathID)
		// resend the frames that were in the packet
		for _, frame := range retransmitPacket.GetFramesForRetransmission() {
			switch f := frame.(type) {
			case *wire.StreamFrame:
				s.streamFramer.AddFrameForRetransmission(f)
			case *wire.WindowUpdateFrame:
				// only retransmit WindowUpdates if the stream is not yet closed and the we haven't sent another WindowUpdate with a higher ByteOffset for the stream
				// XXX Should it be adapted to multiple paths?
				currentOffset, err := s.flowControlManager.GetReceiveWindow(f.StreamID)
				if err == nil && f.ByteOffset >= currentOffset {
					s.packer.QueueControlFrame(f, pth)
				}
			case *wire.PathsFrame:
				// Schedule a new PATHS frame to send
				s.schedulePathsFrame()
			case *wire.PingFrame:
				// FQSAT sửa 2: PING chỉ để thăm dò/keep-alive, mất thì không cần truyền lại.
				// Nếu xếp lại vào controlFrames (dùng chung mọi path), PackPacket kế tiếp trên
				// bất kỳ path nào sẽ gửi gói chỉ có PING và xoá mất ACK đang chờ của path đó.
			default:
				s.packer.QueueControlFrame(frame, pth)
			}
		}
	}
	return
}

func (sch *scheduler) selectPathRoundRobin(s *session, hasRetransmission bool, hasStreamRetransmission bool, fromPth *path) *path {
	if sch.quotas == nil {
		sch.setup()
	}

	// XXX Avoid using PathID 0 if there is more than 1 path
	if len(s.paths) <= 1 {
		if !hasRetransmission && !s.paths[protocol.InitialPathID].SendingAllowed() {
			return nil
		}
		return s.paths[protocol.InitialPathID]
	}

	// TODO cope with decreasing number of paths (needed?)
	var selectedPath *path
	var lowerQuota, currentQuota uint
	var ok bool

	// Max possible value for lowerQuota at the beginning
	lowerQuota = ^uint(0)

pathLoop:
	for pathID, pth := range s.paths {
		// Don't block path usage if we retransmit, even on another path
		if !hasRetransmission && !pth.SendingAllowed() {
			continue pathLoop
		}

		// If this path is potentially failed, do no consider it for sending
		if pth.potentiallyFailed.Get() {
			continue pathLoop
		}

		// XXX Prevent using initial pathID if multiple paths
		if pathID == protocol.InitialPathID {
			continue pathLoop
		}

		currentQuota, ok = sch.quotas[pathID]
		if !ok {
			sch.quotas[pathID] = 0
			currentQuota = 0
		}

		if currentQuota < lowerQuota {
			selectedPath = pth
			lowerQuota = currentQuota
		}
	}

	return selectedPath

}

func (sch *scheduler) selectPathLowLatency(s *session, hasRetransmission bool, hasStreamRetransmission bool, fromPth *path) *path {
	// XXX Avoid using PathID 0 if there is more than 1 path
	if len(s.paths) <= 1 {
		if !hasRetransmission && !s.paths[protocol.InitialPathID].SendingAllowed() {
			return nil
		}
		return s.paths[protocol.InitialPathID]
	}

	// FIXME Only works at the beginning... Cope with new paths during the connection
	if hasRetransmission && hasStreamRetransmission && fromPth.rttStats.SmoothedRTT() == 0 {
		// Is there any other path with a lower number of packet sent?
		currentQuota := sch.quotas[fromPth.pathID]
		for pathID, pth := range s.paths {
			if pathID == protocol.InitialPathID || pathID == fromPth.pathID {
				continue
			}
			// The congestion window was checked when duplicating the packet
			if sch.quotas[pathID] < currentQuota {
				return pth
			}
		}
	}

	var selectedPath *path
	var lowerRTT time.Duration
	var currentRTT time.Duration
	selectedPathID := protocol.PathID(255)

pathLoop:
	for pathID, pth := range s.paths {
		// Don't block path usage if we retransmit, even on another path
		if !hasRetransmission && !pth.SendingAllowed() {
			if SCHE_ALGO == "TRR" {
				pth.congestionlimited = time.Now()
			}
			continue pathLoop
		}

		// If this path is potentially failed, do not consider it for sending
		if pth.potentiallyFailed.Get() {
			continue pathLoop
		}

		// FQSAT: bỏ path unreliable, chỉ giữ một tỉ lệ nhỏ packet để thăm dò và tiếp tục học
		if pth.unreliable.Get() && rand.Float64() >= FQ_PROBE_EPS {
			continue pathLoop
		}

		// XXX Prevent using initial pathID if multiple paths
		if pathID == protocol.InitialPathID {
			continue pathLoop
		}

		currentRTT = pth.rttStats.SmoothedRTT()

		// Prefer staying single-path if not blocked by current path
		// Don't consider this sample if the smoothed RTT is 0
		if lowerRTT != 0 && currentRTT == 0 {
			continue pathLoop
		}

		// Case if we have multiple paths unprobed
		if currentRTT == 0 {
			currentQuota, ok := sch.quotas[pathID]
			if !ok {
				sch.quotas[pathID] = 0
				currentQuota = 0
			}
			lowerQuota, _ := sch.quotas[selectedPathID]
			if selectedPath != nil && currentQuota > lowerQuota {
				continue pathLoop
			}
		}

		if currentRTT != 0 && lowerRTT != 0 && selectedPath != nil && currentRTT >= lowerRTT {
			continue pathLoop
		}

		// Update
		lowerRTT = currentRTT
		selectedPath = pth
		selectedPathID = pathID
	}

	return selectedPath
}

func (sch *scheduler) sendingQueueEmpty(pth *path) bool {
	if sch.packetsNotSentYet[pth.pathID] == nil {
		sch.packetsNotSentYet[pth.pathID] = &PacketList{
			queue:    make([]*packedFrames, 0),
			len:      0,
			toPathid: pth.pathID,
		}
	}
	return len(sch.packetsNotSentYet[pth.pathID].queue) == 0
}

func (sch *scheduler) calculateArrivalTime(s *session, pth *path, addMeanDeviation bool) (time.Duration, bool) {

	packetSize := protocol.MaxPacketSize * 8 //bit uint64
	var pthBwd protocol.ByteCount
	if pth.rttStats.SmoothedRTT() > 0 {
		pthBwd = (pth.sentPacketHandler.GetCongestionWindow() * protocol.ByteCount(time.Second) * protocol.ByteCount(congestion.BytesPerSecond)) / protocol.ByteCount(pth.rttStats.SmoothedRTT())
	}
	inSecond := uint64(time.Second)
	var rtt time.Duration
	if addMeanDeviation {
		rtt = pth.rttStats.SmoothedRTT() + pth.rttStats.MeanDeviation()
	} else {
		rtt = pth.rttStats.SmoothedRTT()

	}
	if pthBwd == 0 {
		return rtt / 2, false
	}
	if rtt == 0 {
		return 0, true
	}
	writeQueue, ok := sch.packetsNotSentYet[pth.pathID]
	var writeQueueSize protocol.ByteCount
	if !ok {
		writeQueueSize = 0
	} else {
		writeQueueSize = protocol.ByteCount(writeQueue.len) * protocol.DefaultTCPMSS * 8 //in bit
		//protocol.DefaultTCPMSS MaxPacketSize
	}

	arrivalTime := (uint64(packetSize+writeQueueSize)*inSecond)/uint64(pthBwd) + uint64(rtt)/2 //in nanosecond
	return time.Duration(arrivalTime), true
}

func (sch *scheduler) queueFrames(frames []wire.Frame, pth *path) {
	if sch.packetsNotSentYet[pth.pathID] == nil {
		sch.packetsNotSentYet[pth.pathID] = &PacketList{
			queue:    make([]*packedFrames, 0),
			len:      0,
			toPathid: pth.pathID,
		}
	}
	packetList := sch.packetsNotSentYet[pth.pathID]
	packetList.queue = append(packetList.queue, &packedFrames{frames, time.Now()})
	packetList.len += 1
}

func (sch *scheduler) dequeueStoredFrames(pth *path) []wire.Frame {

	packetList := sch.packetsNotSentYet[pth.pathID]
	if len(packetList.queue) == 0 {
		return nil
	}
	packet := packetList.queue[0]
	// Shift the slice and don't retain anything that isn't needed.
	copy(packetList.queue, packetList.queue[1:])
	packetList.queue[len(packetList.queue)-1] = nil
	packetList.queue = packetList.queue[:len(packetList.queue)-1]
	// Update statistics
	packetList.len -= 1

	return packet.frames
}

func (sch *scheduler) selectPathByArrivalTime(s *session, hasRetransmission bool, hasStreamRetransmission bool, fromPth *path) (selectedPath *path) {
	if s.perspective == protocol.PerspectiveClient {
		return sch.selectPathLowLatency(s, hasRetransmission, hasStreamRetransmission, fromPth)
	}
	// XXX Avoid using PathID 0 if there is more than 1 path
	if len(s.paths) <= 1 {
		if !s.paths[protocol.InitialPathID].SendingAllowed() {
			return nil
		}
		selectedPath = s.paths[protocol.InitialPathID]
		return selectedPath
	}
	// FIXME Only works at the beginning... Cope with new paths during the connection
	if hasRetransmission && hasStreamRetransmission && fromPth.rttStats.SmoothedRTT() == 0 {
		// Is there any other path with a lower number of packet sent?
		currentQuota := sch.quotas[fromPth.pathID]
		for pathID, pth := range s.paths {
			if pathID == protocol.InitialPathID || pathID == fromPth.pathID {
				continue
			}
			// The congestion window was checked when duplicating the packet
			if sch.quotas[pathID] < currentQuota {
				return pth
			}
		}
	}

	for _, pth := range s.paths {
		if pth != nil && !sch.sendingQueueEmpty(pth) {
			if pth.SendingAllowed() {
				return pth
			}
		}
	}
	// var currentRTT time.Duration
	var currentArrivalTime time.Duration
	var lowerArrivalTime time.Duration
	selectedPathID := protocol.PathID(255)
	var allCwndLimited bool = true

	//find the best path, including that is limited by SendingAllowed()
pathLoop:
	for pathID, pth := range s.paths {

		// If this path is potentially failed, do not consider it for sending
		if pth.potentiallyFailed.Get() {
			continue pathLoop
		}

		// XXX Prevent using initial pathID if multiple paths
		if pathID == protocol.InitialPathID {
			continue pathLoop
		}

		// return nil if all paths are limited by cwnd
		allCwndLimited = allCwndLimited && (!hasRetransmission && !pth.SendingAllowed())

		// currentRTT = pth.rttStats.SmoothedRTT() // if SmoothedRTT == 0, send on it. Because it will be duplicated to other paths. TODO maybe not?
		// currentArrivalTime, _ = sch.calculateArrivalTime(s, pth, false)
		currentArrivalTime, _ = sch.calculateArrivalTime(s, pth, false)
		// currentArrivalTime = pth.rttStats.SmoothedRTT()

		// Prefer staying single-path if not blocked by current path
		// Don't consider this sample if the smoothed RTT is 0
		if lowerArrivalTime != 0 && currentArrivalTime == 0 {
			continue pathLoop
		}

		// Case if we have multiple paths unprobed
		// currentArrivalTime == 0 means rtt == 0
		if currentArrivalTime == 0 {
			currentQuota, ok := sch.quotas[pathID]
			if !ok {
				sch.quotas[pathID] = 0
				currentQuota = 0
			}
			lowerQuota, _ := sch.quotas[selectedPathID]
			if selectedPath != nil && currentQuota > lowerQuota {
				continue pathLoop
			}
		}

		if currentArrivalTime != 0 && lowerArrivalTime != 0 && selectedPath != nil && currentArrivalTime >= lowerArrivalTime {
			continue pathLoop
		}

		// Update
		lowerArrivalTime = currentArrivalTime
		selectedPath = pth
		selectedPathID = pathID
	}
	if allCwndLimited {
		return nil
	}
	return selectedPath
}

// Lock of s.paths must be held
func (sch *scheduler) selectPath(s *session, hasRetransmission bool, hasStreamRetransmission bool, fromPth *path) *path {
	// XXX Currently round-robin
	// TODO select the right scheduler dynamically
	if SCHE_ALGO == "SAPS" {
		return sch.selectPathByArrivalTime(s, hasRetransmission, hasStreamRetransmission, fromPth)
	}
	return sch.selectPathLowLatency(s, hasRetransmission, hasStreamRetransmission, fromPth)
	// return sch.selectPathRoundRobin(s, hasRetransmission, hasStreamRetransmission, fromPth)
}

// Lock of s.paths must be free (in case of log print)
func (sch *scheduler) performPacketSending(s *session, windowUpdateFrames []*wire.WindowUpdateFrame, pth *path) (*ackhandler.Packet, bool, error) {
	// add a retransmittable frame
	if pth.sentPacketHandler.ShouldSendRetransmittablePacket() {
		s.packer.QueueControlFrame(&wire.PingFrame{}, pth)
	}
	var err error
	var packet *packedPacket

	if SCHE_ALGO == "SAPS" {
		if pth.SendingAllowed() && sch.sendingQueueEmpty(pth) { //normally
			packet, err = s.packer.PackPacket(pth)
			if err != nil || packet == nil {
				return nil, false, err
			}
		} else if !pth.SendingAllowed() {
			stored, err := s.packer.StoreFrames(s, pth)
			if stored {
				return nil, true, err // here the "sent" bool is set to true, then the loop outside will not break
			} else {
				return nil, false, err // here the "sent" bool is set to true, then the loop outside will not break
			}
		} else {
			packet, err = s.packer.PackPacketWithStoreFrames(pth)
			if err != nil || packet == nil {
				return nil, false, err
			}
		}
	} else {
		packet, err = s.packer.PackPacket(pth)
		if err != nil || packet == nil {
			return nil, false, err
		}
	}

	if err = s.sendPackedPacket(packet, pth); err != nil {
		return nil, false, err
	}

	// send every window update twice
	for _, f := range windowUpdateFrames {
		s.packer.QueueControlFrame(f, pth)
	}

	// Packet sent, so update its quota
	sch.quotas[pth.pathID]++

	// Provide some logging if it is the last packet
	for _, frame := range packet.frames {
		switch frame := frame.(type) {
		case *wire.StreamFrame:
			if frame.FinBit {
				// Last packet to send on the stream, print stats
				// s.pathsLock.RLock()
				// utils.Infof("Info for stream %x of %x", frame.StreamID, s.connectionID)
				// for pathID, pth := range s.paths {
				// 	sntPkts, sntRetrans, sntLost := pth.sentPacketHandler.GetStatistics()
				// 	rcvPkts := pth.receivedPacketHandler.GetStatistics()
				// 	utils.Infof("Path %x: sent %d retrans %d lost %d; rcv %d rtt %v", pathID, sntPkts, sntRetrans, sntLost, rcvPkts, pth.rttStats.SmoothedRTT())
				// }
				// s.pathsLock.RUnlock()
			}
		default:
		}
	}

	pkt := &ackhandler.Packet{
		PacketNumber:    packet.number,
		Frames:          packet.frames,
		Length:          protocol.ByteCount(len(packet.raw)),
		EncryptionLevel: packet.encryptionLevel,
	}

	return pkt, true, nil
}

// Lock of s.paths must be free
func (sch *scheduler) ackRemainingPaths(s *session, totalWindowUpdateFrames []*wire.WindowUpdateFrame) error {
	// Either we run out of data, or CWIN of usable paths are full
	// Send ACKs on paths not yet used, if needed. Either we have no data to send and
	// it will be a pure ACK, or we will have data in it, but the CWIN should then
	// not be an issue.
	s.pathsLock.RLock()
	defer s.pathsLock.RUnlock()
	// get WindowUpdate frames
	// this call triggers the flow controller to increase the flow control windows, if necessary
	windowUpdateFrames := totalWindowUpdateFrames
	if len(windowUpdateFrames) == 0 {
		windowUpdateFrames = s.getWindowUpdateFrames(s.peerBlocked)
	}
	for _, pthTmp := range s.paths {
		ackTmp := pthTmp.GetAckFrame()
		for _, wuf := range windowUpdateFrames {
			s.packer.QueueControlFrame(wuf, pthTmp)
		}
		if ackTmp != nil || len(windowUpdateFrames) > 0 {
			if (pthTmp.pathID == protocol.InitialPathID || pthTmp.potentiallyFailed.Get()) && ackTmp == nil {
				continue
			}
			swf := pthTmp.GetStopWaitingFrame(false)
			if swf != nil {
				s.packer.QueueControlFrame(swf, pthTmp)
			}
			s.packer.QueueControlFrame(ackTmp, pthTmp)
			// XXX (QDC) should we instead call PackPacket to provides WUFs?
			var packet *packedPacket
			var err error
			if ackTmp != nil {
				// Avoid internal error bug
				packet, err = s.packer.PackAckPacket(pthTmp)
			} else {
				packet, err = s.packer.PackPacket(pthTmp)
			}
			if err != nil {
				return err
			}
			err = s.sendPackedPacket(packet, pthTmp)
			if err != nil {
				return err
			}
		}
	}
	s.peerBlocked = false
	return nil
}

func (sch *scheduler) streamAllocation(strm *stream, s *session) {
	if strm.streamID == 1 {
		return
	}
	var bestpath *path
	for _, pth := range s.paths {
		if !pathUsable(pth) {
			continue
		}

		if bestpath == nil {
			bestpath = pth
			continue
		}
		if pth.rttStats.SmoothedRTT() != 0 && pth.rttStats.SmoothedRTT() < bestpath.rttStats.SmoothedRTT() {
			bestpath = pth
		}
	}
	strm.path = bestpath
}

func (sch *scheduler) adjustAllocation(s *session) {
	// FQSAT: chuyển mọi stream đang gán vào path unreliable/failed sang path tốt nhất còn lại
	var bestPath *path
	for _, pth := range s.paths {
		if !pathUsable(pth) {
			continue
		}
		if bestPath == nil || (pth.rttStats.SmoothedRTT() != 0 &&
			pth.rttStats.SmoothedRTT() < bestPath.rttStats.SmoothedRTT()) {
			bestPath = pth
		}
	}
	// if bestPath != nil {
	if FQ_ENABLE && bestPath != nil { // FQ_ENABLE=false => hành vi y hệt TRR gốc
		for _, streamID := range s.streamsMap.openStreams {
			if streamID == 1 {
				continue
			}
			st := s.streamsMap.streams[streamID]
			if st.path != nil && !pathUsable(st.path) {
				st.path = bestPath
			}
		}
	}

	// compute the related statistics from packetSent of every stream
	packetAcSent := make(map[protocol.PathID]int)
	packetShSent := make(map[protocol.PathID]int)
	packetSent := make(map[protocol.StreamID]int)
	for _, streamID := range s.streamsMap.openStreams {
		if streamID == 1 {
			continue
		}
		stream := s.streamsMap.streams[streamID]
		// utils.Infof("stream %v path %v token %v", stream.streamID, stream.path.pathID, stream.token)
		for pathID, pkt := range stream.packetSent {
			// utils.Infof("stream %v sent %v packets on path %v", stream.streamID, pkt, pathID)
			packetAcSent[pathID] += pkt
			packetShSent[stream.path.pathID] += pkt
			packetSent[stream.streamID] += pkt
		}
	}
	for _, pth := range s.paths {
		if !pathUsable(pth) {
			continue
		}
		// utils.Infof("path %v cwnd %v rtt %dms", pth.pathID, pth.sentPacketHandler.GetCongestionWindow(),pth.rttStats.SmoothedRTT()/time.Millisecond)
	}
	// utils.Infof("RWND %v", s.streamFramer.flowControlManager.RemainingConnectionWindowSize())

	// adjust the stream allocation on each path
	paths_RTT := make([]*path, 0)
	for _, pth := range s.paths {
		if !pathUsable(pth) {
			continue
		}
		paths_RTT = append(paths_RTT, pth)
	}
	sort.SliceStable(paths_RTT, func(i, j int) bool {
		return paths_RTT[i].rttStats.SmoothedRTT() < paths_RTT[j].rttStats.SmoothedRTT()
	})

	var delta int
	for i, pth := range paths_RTT {
		if i == len(paths_RTT)-1 {
			break
		}

		delta = packetShSent[pth.pathID] - packetAcSent[pth.pathID]

		for delta > 0 {
			var stream_tbd *stream
			var stream_max *stream
			for _, streamID := range s.streamsMap.openStreams {
				if streamID == 1 {
					continue
				}
				stream := s.streamsMap.streams[streamID]
				if stream.path == pth && packetSent[streamID] >= delta {
					if stream_tbd == nil || packetSent[streamID] < packetSent[stream_tbd.streamID] {
						stream_tbd = stream
					}
				}
				if stream.path == pth && (stream_max == nil || packetSent[streamID] > packetSent[stream_max.streamID]) {
					stream_max = stream
				}
			}
			if stream_tbd == nil {
				stream_tbd = stream_max
			}
			if stream_tbd != nil {
				//utils.Infof("remove: stream %v is removed from path %v to path %v", stream_tbd.streamID, pth.pathID, paths_RTT[len(paths_RTT)-1].pathID)
				stream_tbd.path = paths_RTT[len(paths_RTT)-1]
				delta -= packetSent[stream_tbd.streamID]
				packetShSent[pth.pathID] -= packetSent[stream_tbd.streamID]
				packetShSent[paths_RTT[len(paths_RTT)-1].pathID] += packetSent[stream_tbd.streamID]
			}
		}

		for {
			var stream_tbd *stream
			for _, streamID := range s.streamsMap.openStreams {
				if streamID == 1 {
					continue
				}
				stream := s.streamsMap.streams[streamID]
				if stream.path.rttStats.SmoothedRTT() <= pth.rttStats.SmoothedRTT() {
					continue
				}
				if stream.path != pth && packetSent[streamID]+delta <= 0 {
					if stream_tbd == nil || packetSent[streamID] > packetSent[stream_tbd.streamID] {
						stream_tbd = stream
					}
				}
			}
			if stream_tbd == nil {
				break
			}
			//utils.Infof("add: stream %v is allocated from path %v to path %v", stream_tbd.streamID, stream_tbd.path.pathID, pth.pathID)
			delta += packetSent[stream_tbd.streamID]
			packetShSent[pth.pathID] += packetSent[stream_tbd.streamID]
			packetShSent[stream_tbd.path.pathID] -= packetSent[stream_tbd.streamID]
			stream_tbd.path = pth
		}
	}

	// record the maximum portion of single allocated stream on each path for the later computation of threshold
	if PORTION == nil {
		PORTION = make(map[*path]float32)
	}
	for _, pth := range s.paths {
		if !pathUsable(pth) {
			continue
		}
		PORTION[pth] = 0
	}
	for _, streamID := range s.streamsMap.openStreams {
		if streamID == 1 {
			continue
		}
		stream := s.streamsMap.streams[streamID]
		if packetShSent[stream.path.pathID] > 0 &&
			float32(packetSent[streamID])/float32(packetShSent[stream.path.pathID]) > PORTION[stream.path] {

			PORTION[stream.path] = float32(packetSent[streamID]) / float32(packetShSent[stream.path.pathID])
		}
	}
}

func (sch *scheduler) resetStatistics(s *session) {
	if sch.timer == nil {
		sch.timer = time.NewTimer(INTERVAL)
	} else {
		sch.timer.Reset(INTERVAL)
	}
	for _, streamID := range s.streamsMap.openStreams {
		if streamID == 1 {
			continue
		}
		stream := s.streamsMap.streams[streamID]
		stream.packetSent = make(map[protocol.PathID]int)
	}
}

func (sch *scheduler) sendPacket(s *session) error {
	var pth *path

	// Update leastUnacked value of paths
	s.pathsLock.RLock()
	for _, pthTmp := range s.paths {
		pthTmp.SetLeastUnacked(pthTmp.sentPacketHandler.GetLeastUnacked())
	}
	s.pathsLock.RUnlock()

	// get WindowUpdate frames
	// this call triggers the flow controller to increase the flow control windows, if necessary
	windowUpdateFrames := s.getWindowUpdateFrames(false)
	for _, wuf := range windowUpdateFrames {
		s.packer.QueueControlFrame(wuf, pth)
	}

	// Repeatedly try sending until we don't have any more data, or run out of the congestion window

	for {
		// Allocate path bandwidth to streams periodcally
		if SCHE_ALGO == "TRR" {
			if sch.timer == nil {
				sch.timer = time.NewTimer(INTERVAL)
			}
			select {
			case <-sch.timer.C:
				sch.adjustAllocation(s)
				sch.resetStatistics(s)
			default:
				if sch.forceAdjust {
					sch.forceAdjust = false
					sch.adjustAllocation(s)
					sch.resetStatistics(s)
				}
			}
			// FQSAT sửa 2: PING thăm dò path không có ACK gần đây (path bị gắn cờ không còn
			// dữ liệu, nên không có ACK để học và gỡ cờ)
			if FQ_ENABLE {
				if err := sch.probePaths(s); err != nil {
					return err
				}
			}

		}

		// We first check for retransmissions
		hasRetransmission, retransmitHandshakePacket, fromPth := sch.getRetransmission(s)
		// XXX There might still be some stream frames to be retransmitted
		hasStreamRetransmission := s.streamFramer.HasFramesForRetransmission()

		// Select the path here
		s.pathsLock.RLock()
		pth = sch.selectPath(s, hasRetransmission, hasStreamRetransmission, fromPth)
		s.pathsLock.RUnlock()

		// XXX No more path available, should we have a new QUIC error message?
		if pth == nil {
			windowUpdateFrames := s.getWindowUpdateFrames(false)
			return sch.ackRemainingPaths(s, windowUpdateFrames)
		}

		// If we have an handshake packet retransmission, do it directly
		if hasRetransmission && retransmitHandshakePacket != nil {
			s.packer.QueueControlFrame(pth.sentPacketHandler.GetStopWaitingFrame(true), pth)
			packet, err := s.packer.PackHandshakeRetransmission(retransmitHandshakePacket, pth)
			if err != nil {
				return err
			}
			if err = s.sendPackedPacket(packet, pth); err != nil {
				return err
			}
			continue
		}

		// XXX Some automatic ACK generation should be done someway
		var ack *wire.AckFrame

		ack = pth.GetAckFrame()
		if ack != nil {
			s.packer.QueueControlFrame(ack, pth)
		}
		if ack != nil || hasStreamRetransmission {
			swf := pth.sentPacketHandler.GetStopWaitingFrame(hasStreamRetransmission)
			if swf != nil {
				s.packer.QueueControlFrame(swf, pth)
			}
		}

		// Also add CLOSE_PATH frames, if any
		for cpf := s.streamFramer.PopClosePathFrame(); cpf != nil; cpf = s.streamFramer.PopClosePathFrame() {
			s.packer.QueueControlFrame(cpf, pth)
		}

		// Also add ADD ADDRESS frames, if any
		for aaf := s.streamFramer.PopAddAddressFrame(); aaf != nil; aaf = s.streamFramer.PopAddAddressFrame() {
			s.packer.QueueControlFrame(aaf, pth)
		}

		// Also add PATHS frames, if any
		for pf := s.streamFramer.PopPathsFrame(); pf != nil; pf = s.streamFramer.PopPathsFrame() {
			s.packer.QueueControlFrame(pf, pth)
		}

		pkt, sent, err := sch.performPacketSending(s, windowUpdateFrames, pth)
		if err != nil {
			return err
		}
		windowUpdateFrames = nil
		if !sent {
			// Prevent sending empty packets
			return sch.ackRemainingPaths(s, windowUpdateFrames)
		}

		// Duplicate traffic when it was sent on an unknown performing path
		// FIXME adapt for new paths coming during the connection
		if pth.rttStats.SmoothedRTT() == 0 {
			currentQuota := sch.quotas[pth.pathID]
			// Was the packet duplicated on all potential paths?
		duplicateLoop:
			for pathID, tmpPth := range s.paths {
				if pathID == protocol.InitialPathID || pathID == pth.pathID {
					continue
				}
				if sch.quotas[pathID] < currentQuota && tmpPth.sentPacketHandler.SendingAllowed() {
					// Duplicate it
					pth.sentPacketHandler.DuplicatePacket(pkt)
					break duplicateLoop
				}
			}
		}

		// And try pinging on potentially failed paths
		if fromPth != nil && fromPth.potentiallyFailed.Get() {
			err = s.sendPing(fromPth)
			if err != nil {
				return err
			}
		}
	}
}
