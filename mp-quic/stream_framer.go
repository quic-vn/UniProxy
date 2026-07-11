package quic

import (
	"net"
	"time"

	"github.com/qdeconinck/mp-quic/internal/flowcontrol"
	"github.com/qdeconinck/mp-quic/internal/protocol"
	"github.com/qdeconinck/mp-quic/internal/utils"
	"github.com/qdeconinck/mp-quic/internal/wire"
)

var nodata int = 1

type streamFramer struct {
	streamsMap *streamsMap

	flowControlManager flowcontrol.FlowControlManager

	retransmissionQueue  []*wire.StreamFrame
	blockedFrameQueue    []*wire.BlockedFrame
	addAddressFrameQueue []*wire.AddAddressFrame
	closePathFrameQueue  []*wire.ClosePathFrame
	pathsFrame           *wire.PathsFrame
}

func newStreamFramer(streamsMap *streamsMap, flowControlManager flowcontrol.FlowControlManager) *streamFramer {
	return &streamFramer{
		streamsMap:         streamsMap,
		flowControlManager: flowControlManager,
	}
}

func (f *streamFramer) AddFrameForRetransmission(frame *wire.StreamFrame) {
	f.retransmissionQueue = append(f.retransmissionQueue, frame)
}

func (f *streamFramer) PopStreamFrames(maxLen protocol.ByteCount, pth *path) []*wire.StreamFrame {
	fs, currentLen := f.maybePopFramesForRetransmission(maxLen)
	return append(fs, f.maybePopNormalFrames(maxLen-currentLen, pth)...)
}

func (f *streamFramer) PopBlockedFrame() *wire.BlockedFrame {
	if len(f.blockedFrameQueue) == 0 {
		return nil
	}
	frame := f.blockedFrameQueue[0]
	f.blockedFrameQueue = f.blockedFrameQueue[1:]
	return frame
}

func (f *streamFramer) AddAddressForTransmission(ipVersion uint8, addr net.UDPAddr) {
	f.addAddressFrameQueue = append(f.addAddressFrameQueue, &wire.AddAddressFrame{IPVersion: ipVersion, Addr: addr})
}

func (f *streamFramer) PopAddAddressFrame() *wire.AddAddressFrame {
	if len(f.addAddressFrameQueue) == 0 {
		return nil
	}
	frame := f.addAddressFrameQueue[0]
	f.addAddressFrameQueue = f.addAddressFrameQueue[1:]
	return frame
}

func (f *streamFramer) AddPathsFrameForTransmission(s *session) {
	s.pathsLock.RLock()
	defer s.pathsLock.RUnlock()
	paths := make([]protocol.PathID, len(s.paths))
	remoteRTTs := make([]time.Duration, len(s.paths))
	i := 0
	for pathID := range s.paths {
		paths[i] = pathID
		if s.paths[pathID].potentiallyFailed.Get() {
			remoteRTTs[i] = time.Hour
		} else {
			remoteRTTs[i] = s.paths[pathID].rttStats.SmoothedRTT()
		}
		i++
	}
	f.pathsFrame = &wire.PathsFrame{MaxNumPaths: 255, NumPaths: uint8(len(paths)), PathIDs: paths, RemoteRTTs: remoteRTTs}
}

func (f *streamFramer) PopPathsFrame() *wire.PathsFrame {
	if f.pathsFrame == nil {
		return nil
	}
	frame := f.pathsFrame
	f.pathsFrame = nil
	return frame
}

func (f *streamFramer) AddClosePathFrameForTransmission(closePathFrame *wire.ClosePathFrame) {
	f.closePathFrameQueue = append(f.closePathFrameQueue, closePathFrame)
}

func (f *streamFramer) PopClosePathFrame() *wire.ClosePathFrame {
	if len(f.closePathFrameQueue) == 0 {
		return nil
	}
	frame := f.closePathFrameQueue[0]
	f.closePathFrameQueue = f.closePathFrameQueue[1:]
	return frame
}

func (f *streamFramer) HasFramesForRetransmission() bool {
	return len(f.retransmissionQueue) > 0
}

func (f *streamFramer) HasCryptoStreamFrame() bool {
	// TODO(#657): Flow control
	cs, _ := f.streamsMap.GetOrOpenStream(1)
	return cs.lenOfDataForWriting() > 0
}

// TODO(lclemente): This is somewhat duplicate with the normal path for generating frames.
// TODO(#657): Flow control
func (f *streamFramer) PopCryptoStreamFrame(maxLen protocol.ByteCount) *wire.StreamFrame {
	if !f.HasCryptoStreamFrame() {
		return nil
	}
	cs, _ := f.streamsMap.GetOrOpenStream(1)
	frame := &wire.StreamFrame{
		StreamID: 1,
		Offset:   cs.writeOffset,
	}
	frameHeaderBytes, _ := frame.MinLength(protocol.VersionWhatever) // can never error
	frame.Data = cs.getDataForWriting(maxLen - frameHeaderBytes)
	return frame
}

func (f *streamFramer) maybePopFramesForRetransmission(maxLen protocol.ByteCount) (res []*wire.StreamFrame, currentLen protocol.ByteCount) {
	for len(f.retransmissionQueue) > 0 {
		frame := f.retransmissionQueue[0]
		frame.DataLenPresent = true

		frameHeaderLen, _ := frame.MinLength(protocol.VersionWhatever) // can never error
		if currentLen+frameHeaderLen >= maxLen {
			break
		}

		currentLen += frameHeaderLen

		splitFrame := maybeSplitOffFrame(frame, maxLen-currentLen)
		if splitFrame != nil { // StreamFrame was split
			res = append(res, splitFrame)
			frameLen := splitFrame.DataLen()
			currentLen += frameLen
			// XXX (QDC): to avoid rewriting a lot of tests...
			if f.flowControlManager != nil {
				f.flowControlManager.AddBytesRetrans(splitFrame.StreamID, frameLen)
			}
			break
		}

		f.retransmissionQueue = f.retransmissionQueue[1:]
		res = append(res, frame)
		frameLen := frame.DataLen()
		currentLen += frameLen
		// XXX (QDC): to avoid rewriting a lot of tests...
		if f.flowControlManager != nil {
			f.flowControlManager.AddBytesRetrans(frame.StreamID, frameLen)
		}
	}
	return
}

func (f *streamFramer) maybePopNormalFrames(maxBytes protocol.ByteCount, pth *path) (res []*wire.StreamFrame) {
	frame := &wire.StreamFrame{DataLenPresent: true}
	var currentLen protocol.ByteCount

	fn := func(s *stream) (bool, error) {
		if s == nil || s.streamID == 1 /* crypto stream is handled separately */ {
			return true, nil
		}

		frame.StreamID = s.streamID
		// not perfect, but thread-safe since writeOffset is only written when getting data
		frame.Offset = s.writeOffset
		frameHeaderBytes, _ := frame.MinLength(protocol.VersionWhatever) // can never error
		if currentLen+frameHeaderBytes >= maxBytes {
			return false, nil // theoretically, we could find another stream that fits, but this is quite unlikely, so we stop here
		}
		maxLen := maxBytes - currentLen - frameHeaderBytes

		var sendWindowSize protocol.ByteCount
		if flowcontrol.FLOW_CONTROL == "Connection" {
			sendWindowSize = f.flowControlManager.RemainingConnectionWindowSize()
			if sendWindowSize == 0 {
				return false, nil
			}
		} else if flowcontrol.FLOW_CONTROL == "Stream" {
			sendWindowSize, _ = f.flowControlManager.SendWindowSize(s.streamID)
			if sendWindowSize == 0 {
				return true, nil
			}
		}
		maxLen = utils.MinByteCount(maxLen, sendWindowSize)
		data := s.getDataForWriting(maxLen)
		// This is unlikely, but check it nonetheless, the scheduler might have jumped in. Seems to happen in ~20% of cases in the tests.
		shouldSendFin := s.shouldSendFin()
		if data == nil && !shouldSendFin {
			return true, nil
		}

		if shouldSendFin {
			frame.FinBit = true
			s.sentFin()
		}

		frame.Data = data
		f.flowControlManager.AddBytesSent(s.streamID, protocol.ByteCount(len(data)))
		// s.byteSent[pth.pathID] += protocol.ByteCount(len(data))
		// if !s.startStat && time.Since(s.openTime) >= 10*time.Second {
		// 	s.byteSent = make(map[protocol.PathID]protocol.ByteCount)
		// 	s.startStat = true
		// }
		// if !s.endStat && time.Since(s.openTime) >= 70*time.Second {
		// 	for pth, bytes := range s.byteSent {
		// 		utils.Infof("stream %v sent %v bytes on path %v", s.streamID, bytes, pth)
		// 	}
		// 	s.endStat = true
		// }
		// if SCHE_ALGO == "TRR" {
		// 	if !s.hasUnsentData() && time.Since(s.noDataTime) <= pth.rttStats.SmoothedRTT() {
		// 		s.token = 0
		// 		s.noDataTime = time.Now()
		// 	} else {
		// 		s.token -= 1
		// 	}
		// 	s.packetSent[pth.pathID] += 1
		// }

		// TRR and X share the same token-based stream scheduling logic.
		// X differs only by using Kalman-smoothed RTT instead of EWMA.
		if isTRRLikeScheduler() {
			if !s.hasUnsentData() && time.Since(s.noDataTime) <= pth.rttStats.SmoothedRTT() {
				s.token = 0
				s.noDataTime = time.Now()
			} else {
				s.token -= 1
			}
			s.packetSent[pth.pathID] += 1
		}

		// Finally, check if we are now FC blocked and should queue a BLOCKED frame
		if f.flowControlManager.RemainingConnectionWindowSize() == 0 && flowcontrol.FLOW_CONTROL == "Connection" {
			// We are now connection-level FC blocked
			f.blockedFrameQueue = append(f.blockedFrameQueue, &wire.BlockedFrame{StreamID: 0})
		} else if !frame.FinBit && sendWindowSize-frame.DataLen() == 0 && flowcontrol.FLOW_CONTROL == "Stream" {
			// We are now stream-level FC blocked
			f.blockedFrameQueue = append(f.blockedFrameQueue, &wire.BlockedFrame{StreamID: s.StreamID()})
		}

		res = append(res, frame)
		currentLen += frameHeaderBytes + frame.DataLen()

		if currentLen == maxBytes {
			return false, nil
		}

		frame = &wire.StreamFrame{DataLenPresent: true}
		return true, nil
	}

	// if SCHE_ALGO == "TRR" {
	// 	f.streamsMap.TokenRoundRobinIterate(fn, pth)
	// } else {
	// 	f.streamsMap.RoundRobinIterate(fn)
	// }

	// TRR and X share the same token-based stream scheduling logic.
	if isTRRLikeScheduler() {
		f.streamsMap.TokenRoundRobinIterate(fn, pth)
	} else {
		f.streamsMap.RoundRobinIterate(fn)
	}

	return
}

// maybeSplitOffFrame removes the first n bytes and returns them as a separate frame. If n >= len(frame), nil is returned and nothing is modified.
func maybeSplitOffFrame(frame *wire.StreamFrame, n protocol.ByteCount) *wire.StreamFrame {
	if n >= frame.DataLen() {
		return nil
	}

	defer func() {
		frame.Data = frame.Data[n:]
		frame.Offset += n
	}()

	return &wire.StreamFrame{
		FinBit:         false,
		StreamID:       frame.StreamID,
		Offset:         frame.Offset,
		Data:           frame.Data[:n],
		DataLenPresent: frame.DataLenPresent,
	}
}
