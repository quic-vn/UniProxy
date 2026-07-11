package quic

import (
	"errors"
	"fmt"
	"sync"
	"time"

	//"github.com/qdeconinck/mp-quic/internal/utils"
	"github.com/qdeconinck/mp-quic/internal/handshake"
	"github.com/qdeconinck/mp-quic/internal/protocol"
	"github.com/qdeconinck/mp-quic/qerr"
)

type streamsMap struct {
	mutex   sync.RWMutex
	session *session

	perspective          protocol.Perspective
	connectionParameters handshake.ConnectionParametersManager

	streams map[protocol.StreamID]*stream
	// needed for round-robin scheduling
	openStreams     []protocol.StreamID
	roundRobinIndex uint32

	nextStream                protocol.StreamID // StreamID of the next Stream that will be returned by OpenStream()
	highestStreamOpenedByPeer protocol.StreamID
	nextStreamOrErrCond       sync.Cond
	openStreamOrErrCond       sync.Cond

	closeErr           error
	nextStreamToAccept protocol.StreamID

	newStream newStreamLambda

	numOutgoingStreams uint32
	numIncomingStreams uint32
}

type streamLambda func(*stream) (bool, error)
type newStreamLambda func(protocol.StreamID) *stream

var (
	errMapAccess = errors.New("streamsMap: Error accessing the streams map")
)

func newStreamsMap(newStream newStreamLambda, pers protocol.Perspective, connectionParameters handshake.ConnectionParametersManager, sess *session) *streamsMap {
	sm := streamsMap{
		session:              sess,
		perspective:          pers,
		streams:              map[protocol.StreamID]*stream{},
		openStreams:          make([]protocol.StreamID, 0),
		newStream:            newStream,
		connectionParameters: connectionParameters,
	}
	sm.nextStreamOrErrCond.L = &sm.mutex
	sm.openStreamOrErrCond.L = &sm.mutex

	if pers == protocol.PerspectiveClient {
		sm.nextStream = 1
		sm.nextStreamToAccept = 2
	} else {
		sm.nextStream = 2
		sm.nextStreamToAccept = 1
	}

	return &sm
}

// GetOrOpenStream either returns an existing stream, a newly opened stream, or nil if a stream with the provided ID is already closed.
// Newly opened streams should only originate from the client. To open a stream from the server, OpenStream should be used.
func (m *streamsMap) GetOrOpenStream(id protocol.StreamID) (*stream, error) {
	m.mutex.RLock()
	s, ok := m.streams[id]
	m.mutex.RUnlock()
	if ok {
		return s, nil // s may be nil
	}

	// ... we don't have an existing stream
	m.mutex.Lock()
	defer m.mutex.Unlock()
	// We need to check whether another invocation has already created a stream (between RUnlock() and Lock()).
	s, ok = m.streams[id]
	if ok {
		return s, nil
	}

	if m.perspective == protocol.PerspectiveServer {
		if id%2 == 0 {
			if id <= m.nextStream { // this is a server-side stream that we already opened. Must have been closed already
				return nil, nil
			}
			return nil, qerr.Error(qerr.InvalidStreamID, fmt.Sprintf("attempted to open stream %d from client-side", id))
		}
		if id <= m.highestStreamOpenedByPeer { // this is a client-side stream that doesn't exist anymore. Must have been closed already
			return nil, nil
		}
	}
	if m.perspective == protocol.PerspectiveClient {
		if id%2 == 1 {
			if id <= m.nextStream { // this is a client-side stream that we already opened.
				return nil, nil
			}
			return nil, qerr.Error(qerr.InvalidStreamID, fmt.Sprintf("attempted to open stream %d from server-side", id))
		}
		if id <= m.highestStreamOpenedByPeer { // this is a server-side stream that doesn't exist anymore. Must have been closed already
			return nil, nil
		}
	}

	// sid is the next stream that will be opened
	sid := m.highestStreamOpenedByPeer + 2
	// if there is no stream opened yet, and this is the server, stream 1 should be openend
	if sid == 2 && m.perspective == protocol.PerspectiveServer {
		sid = 1
	}

	for ; sid <= id; sid += 2 {
		_, err := m.openRemoteStream(sid)
		if err != nil {
			return nil, err
		}
	}

	m.nextStreamOrErrCond.Broadcast()
	return m.streams[id], nil
}

func (m *streamsMap) openRemoteStream(id protocol.StreamID) (*stream, error) {
	if m.numIncomingStreams >= m.connectionParameters.GetMaxIncomingStreams() {
		return nil, qerr.TooManyOpenStreams
	}
	if id+protocol.MaxNewStreamIDDelta < m.highestStreamOpenedByPeer {
		return nil, qerr.Error(qerr.InvalidStreamID, fmt.Sprintf("attempted to open stream %d, which is a lot smaller than the highest opened stream, %d", id, m.highestStreamOpenedByPeer))
	}

	if m.perspective == protocol.PerspectiveServer {
		m.numIncomingStreams++
	} else {
		m.numOutgoingStreams++
	}

	if id > m.highestStreamOpenedByPeer {
		m.highestStreamOpenedByPeer = id
	}

	s := m.newStream(id)
	m.putStream(s)
	return s, nil
}

func (m *streamsMap) openStreamImpl() (*stream, error) {
	id := m.nextStream
	if m.numOutgoingStreams >= m.connectionParameters.GetMaxOutgoingStreams() {
		return nil, qerr.TooManyOpenStreams
	}

	if m.perspective == protocol.PerspectiveServer {
		m.numOutgoingStreams++
	} else {
		m.numIncomingStreams++
	}

	m.nextStream += 2
	s := m.newStream(id)
	m.putStream(s)
	return s, nil
}

// OpenStream opens the next available stream
func (m *streamsMap) OpenStream() (*stream, error) {
	m.mutex.Lock()
	defer m.mutex.Unlock()

	if m.closeErr != nil {
		return nil, m.closeErr
	}
	return m.openStreamImpl()
}

func (m *streamsMap) OpenStreamSync() (*stream, error) {
	m.mutex.Lock()
	defer m.mutex.Unlock()

	for {
		if m.closeErr != nil {
			return nil, m.closeErr
		}
		str, err := m.openStreamImpl()
		if err == nil {
			return str, err
		}
		if err != nil && err != qerr.TooManyOpenStreams {
			return nil, err
		}
		m.openStreamOrErrCond.Wait()
	}
}

// AcceptStream returns the next stream opened by the peer
// it blocks until a new stream is opened
func (m *streamsMap) AcceptStream() (*stream, error) {
	m.mutex.Lock()
	defer m.mutex.Unlock()
	var str *stream
	for {
		var ok bool
		if m.closeErr != nil {
			return nil, m.closeErr
		}
		str, ok = m.streams[m.nextStreamToAccept]
		if ok {
			break
		}
		m.nextStreamOrErrCond.Wait()
	}
	m.nextStreamToAccept += 2
	return str, nil
}

func (m *streamsMap) Iterate(fn streamLambda) error {
	m.mutex.Lock()
	defer m.mutex.Unlock()

	openStreams := append([]protocol.StreamID{}, m.openStreams...)

	for _, streamID := range openStreams {
		cont, err := m.iterateFunc(streamID, fn)
		if err != nil {
			return err
		}
		if !cont {
			break
		}
	}
	return nil
}

// RoundRobinIterate executes the streamLambda for every open stream, until the streamLambda returns false
// It uses a round-robin-like scheduling to ensure that every stream is considered fairly
func (m *streamsMap) RoundRobinIterate(fn streamLambda) error {
	m.mutex.Lock()
	defer m.mutex.Unlock()

	numStreams := uint32(len(m.openStreams))

	for i := uint32(0); i < numStreams; i++ {
		streamID := m.openStreams[(m.roundRobinIndex)%numStreams]
		m.roundRobinIndex = (m.roundRobinIndex + 1) % numStreams

		if streamID == 1 {
			continue
		}

		cont, err := m.iterateFunc(streamID, fn)
		if err != nil {
			return err
		}
		if !cont {
			break
		}
	}
	return nil
}

func (m *streamsMap) iterateFunc(streamID protocol.StreamID, fn streamLambda) (bool, error) {
	str, ok := m.streams[streamID]
	if !ok {
		return true, errMapAccess
	}
	return fn(str)
}

// Token-based Round-Robin Scheduling
func (m *streamsMap) TokenRoundRobinIterate(fn streamLambda, pth *path) error {
	m.mutex.Lock()
	defer m.mutex.Unlock()

	for {
		stream := m.getStreamForPath(pth)
		if stream == nil {
			return nil
		}
		//utils.Infof("get stream %v", stream.streamID)
		cont, err := fn(stream)
		if err != nil {
			return err
		}
		if !cont {
			return nil
		}
	}
	return nil
}

func (m *streamsMap) getStreamForPath(pth *path) *stream {
	var max_bandwidth float32 = 0
	var maxRTT time.Duration = 0
	for pathID, path := range pth.sess.paths {
		if pathID == protocol.InitialPathID || path.potentiallyFailed.Get() {
			continue
		}
		currentRTT := path.rttStats.SmoothedRTT()
		if currentRTT > maxRTT {
			maxRTT = currentRTT
		}
		if currentRTT > 0 {
			bandwidth := path.sentPacketHandler.GetCongestionWindow() / protocol.ByteCount(currentRTT/time.Millisecond)
			if float32(bandwidth)*PORTION[path] > max_bandwidth {
				max_bandwidth = float32(bandwidth) * PORTION[path]
			}
		}
	}
	threshold := int(max_bandwidth * float32(maxRTT/time.Millisecond) / float32(protocol.MaxPacketSize))

	var stream1 *stream
	var stream2 *stream
	index1 := -1
	index2 := -1
	waitData := false
	idlePath := false
	now := time.Now()
	pthRTT := pth.rttStats.SmoothedRTT()
	if now.Sub(pth.congestionlimited) > pthRTT || now.Sub(pth.bandwidthshared) <= pthRTT {
		idlePath = true
	}
	for index, streamID := range m.openStreams {
		if streamID == 1 {
			continue
		}
		stream := m.streams[streamID]
		if stream.lenOfDataForWriting() > 0 && stream1 == nil {
			if stream.path == pth {
				if stream.token > 0 {
					stream1 = stream
				} else {
					if index1 == -1 {
						index1 = index
					} else if index1 >= int(m.roundRobinIndex) {
						if index >= int(m.roundRobinIndex) && index < index1 {
							index1 = index
						}
					} else {
						if index >= int(m.roundRobinIndex) || index < index1 {
							index1 = index
						}
					}
				}
			} else {
				if stream.token > threshold {
					stream1 = stream
				} else if idlePath && stream2 == nil {
					curRTT := stream.path.rttStats.SmoothedRTT()
					if curRTT < pthRTT && now.Sub(stream.noDataTime) <= curRTT {
						continue
					}
					if stream.token > 0 {
						stream2 = stream
					} else {
						if index2 == -1 {
							index2 = index
						} else if index2 >= int(m.roundRobinIndex) {
							if index >= int(m.roundRobinIndex) && index < index2 {
								index2 = index
							}
						} else {
							if index >= int(m.roundRobinIndex) || index < index2 {
								index2 = index
							}
						}
					}
				}
			}
		} else if stream.lenOfDataForWriting() == 0 {
			if stream.shouldSendFin() {
				stream1 = stream
			}
			if stream.hasUnsentData() {
				if stream.path == pth {
					waitData = true
				}
			} else {
				stream.noDataTime = now
			}
		}
	}

	if stream1 != nil {
		return stream1
	}
	if index1 == -1 && waitData {
		return nil
	}
	if index1 == -1 && stream2 != nil {
		pth.bandwidthshared = now
		return stream2
	}

	if index1 == -1 {
		if index2 == -1 {
			return nil
		} else {
			m.distributeToken(uint32(index2))
			pth.bandwidthshared = now
			return m.streams[m.openStreams[index2]]
		}
	} else {
		m.distributeToken(uint32(index1))
		return m.streams[m.openStreams[index1]]
	}
}

func (m *streamsMap) distributeToken(index uint32) {
	numStreams := uint32(len(m.openStreams))
	for {
		streamID := m.openStreams[m.roundRobinIndex]
		if streamID == 1 {
			m.roundRobinIndex = (m.roundRobinIndex + 1) % numStreams
			continue
		}
		stream := m.streams[streamID]
		if stream.hasUnsentData() {
			stream.token += 1
			if m.roundRobinIndex == index {
				m.roundRobinIndex = (m.roundRobinIndex + 1) % numStreams
				return
			}
		}
		m.roundRobinIndex = (m.roundRobinIndex + 1) % numStreams
	}
}

func (m *streamsMap) putStream(s *stream) error {
	id := s.StreamID()
	if _, ok := m.streams[id]; ok {
		return fmt.Errorf("a stream with ID %d already exists", id)
	}

	// Original UniProxy behavior:
	// Only TRR streams were assigned to a path using streamAllocation().
	//
	// if SCHE_ALGO == "TRR" {
	// 	m.session.scheduler.streamAllocation(s, m.session)
	// }

	// Updated behavior for algorithm X:
	// TRR and X use the same stream-allocation logic.
	// X differs from TRR only in the RTT estimator:
	// TRR uses EWMA, while X uses Kalman.
	if isTRRLikeScheduler() {
		m.session.scheduler.streamAllocation(s, m.session)
	}

	m.streams[id] = s
	m.openStreams = append(m.openStreams, id)
	return nil
}

// Attention: this function must only be called if a mutex has been acquired previously
func (m *streamsMap) RemoveStream(id protocol.StreamID) error {
	s, ok := m.streams[id]
	if !ok || s == nil {
		return fmt.Errorf("attempted to remove non-existing stream: %d", id)
	}

	if id%2 == 0 {
		m.numOutgoingStreams--
	} else {
		m.numIncomingStreams--
	}

	for i, s := range m.openStreams {
		if s == id {
			// delete the streamID from the openStreams slice
			m.openStreams = m.openStreams[:i+copy(m.openStreams[i:], m.openStreams[i+1:])]
			// adjust round-robin index, if necessary
			if uint32(i) < m.roundRobinIndex {
				m.roundRobinIndex--
			} else if m.roundRobinIndex == uint32(len(m.openStreams)) {
				m.roundRobinIndex = 0
			}
			break
		}
	}

	delete(m.streams, id)
	m.openStreamOrErrCond.Signal()
	return nil
}

func (m *streamsMap) CloseWithError(err error) {
	m.mutex.Lock()
	defer m.mutex.Unlock()
	m.closeErr = err
	m.nextStreamOrErrCond.Broadcast()
	m.openStreamOrErrCond.Broadcast()
	for _, s := range m.openStreams {
		m.streams[s].Cancel(err)
	}
}
