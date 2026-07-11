package congestion

import (
	"math"
	"time"

	"github.com/qdeconinck/mp-quic/internal/utils"
)

func isFiniteFloat64(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0)
}

const (
	initialRTTus          = 100 * 1000
	rttAlpha      float32 = 0.125
	oneMinusAlpha float32 = (1 - rttAlpha)
	rttBeta       float32 = 0.25
	oneMinusBeta  float32 = (1 - rttBeta)
	halfWindow    float32 = 0.5
	quarterWindow float32 = 0.25

	// Kalman model:
	// x(k) = [RTT level, RTT trend]^T.
	//
	kalmanDeltaT float64 = 1.0

	// Initial covariance P0.
	kalmanInitialLevelVariance float64 = 1000000.0
	kalmanInitialTrendVariance float64 = 10000.0

	// Process noise Q.
	kalmanProcessNoiseLevel float64 = 500000.0
	kalmanProcessNoiseTrend float64 = 10000.0

	// Measurement noise R.
	kalmanMeasurementNoise float64 = 5000000.0

	kalmanMinVariance float64 = 1.0
)

type RTTEstimatorMode int

const (
	RTTEstimatorEWMA RTTEstimatorMode = iota
	RTTEstimatorKalman
)

var rttEstimatorMode = RTTEstimatorEWMA

func SetRTTEstimatorMode(mode RTTEstimatorMode) bool {
	switch mode {
	case RTTEstimatorEWMA, RTTEstimatorKalman:
		rttEstimatorMode = mode
		return true

	default:
		rttEstimatorMode = RTTEstimatorEWMA
		return false
	}
}

func GetRTTEstimatorMode() RTTEstimatorMode {
	return rttEstimatorMode
}

type rttSample struct {
	rtt  time.Duration
	time time.Time
}

// RTTStats provides round-trip statistics
type RTTStats struct {
	initialRTTus int64

	recentMinRTTwindow time.Duration
	minRTT             time.Duration
	latestRTT          time.Duration
	smoothedRTT        time.Duration
	meanDeviation      time.Duration

	numMinRTTsamplesRemaining uint32

	newMinRTT        rttSample
	recentMinRTT     rttSample
	halfWindowRTT    rttSample
	quarterWindowRTT rttSample

	// Kalman state for RTT estimation.
	kalmanInitialized bool

	// State x = [level, trend]^T.
	kalmanLevelUs float64
	kalmanTrendUs float64

	// Covariance matrix P.
	kalmanP00 float64
	kalmanP01 float64
	kalmanP10 float64
	kalmanP11 float64
}

// NewRTTStats makes a properly initialized RTTStats object
func NewRTTStats() *RTTStats {
	return &RTTStats{
		initialRTTus:       initialRTTus,
		recentMinRTTwindow: utils.InfDuration,
	}
}

// InitialRTTus is the initial RTT in us
func (r *RTTStats) InitialRTTus() int64 { return r.initialRTTus }

// MinRTT Returns the minRTT for the entire connection.
// May return Zero if no valid updates have occurred.
func (r *RTTStats) MinRTT() time.Duration { return r.minRTT }

// LatestRTT returns the most recent rtt measurement.
// May return Zero if no valid updates have occurred.
func (r *RTTStats) LatestRTT() time.Duration { return r.latestRTT }

// RecentMinRTT the minRTT since SampleNewRecentMinRtt has been called, or the
// minRTT for the entire connection if SampleNewMinRtt was never called.
func (r *RTTStats) RecentMinRTT() time.Duration { return r.recentMinRTT.rtt }

// SmoothedRTT returns the RTT estimate produced by the active estimator.
// The active estimator is EWMA or Kalman, depending on RTTEstimatorMode.
// May return Zero if no valid updates have occurred.
func (r *RTTStats) SmoothedRTT() time.Duration { return r.smoothedRTT }

// GetQuarterWindowRTT gets the quarter window RTT
func (r *RTTStats) GetQuarterWindowRTT() time.Duration { return r.quarterWindowRTT.rtt }

// GetHalfWindowRTT gets the half window RTT
func (r *RTTStats) GetHalfWindowRTT() time.Duration { return r.halfWindowRTT.rtt }

// MeanDeviation gets the mean deviation
func (r *RTTStats) MeanDeviation() time.Duration { return r.meanDeviation }

// SetRecentMinRTTwindow sets how old a recent min rtt sample can be.
func (r *RTTStats) SetRecentMinRTTwindow(recentMinRTTwindow time.Duration) {
	r.recentMinRTTwindow = recentMinRTTwindow
}

// UpdateRTT updates the RTT based on a new sample.
func (r *RTTStats) UpdateRTT(sendDelta, ackDelay time.Duration, now time.Time) {
	if sendDelta == utils.InfDuration || sendDelta <= 0 {
		utils.Debugf("Ignoring measured sendDelta, because it's is either infinite, zero, or negative: %d", sendDelta/time.Microsecond)
		return
	}

	// Update r.minRTT first. r.minRTT does not use an rttSample corrected for
	// ackDelay but the raw observed sendDelta, since poor clock granularity at
	// the client may cause a high ackDelay to result in underestimation of the
	// r.minRTT.
	if r.minRTT == 0 || r.minRTT > sendDelta {
		r.minRTT = sendDelta
	}
	r.updateRecentMinRTT(sendDelta, now)

	// Correct for ackDelay if information received from the peer results in a
	// positive RTT sample. Otherwise, we use the sendDelta as a reasonable
	// measure for smoothedRTT.
	sample := sendDelta
	if sample > ackDelay {
		sample -= ackDelay
	}
	r.latestRTT = sample

	if rttEstimatorMode == RTTEstimatorKalman {
		r.updateRTTKalman(sample)
	} else {
		r.updateRTTEWMA(sample)
	}
}

func (r *RTTStats) updateRTTEWMA(sample time.Duration) {
	// First time call.
	if r.smoothedRTT == 0 {
		r.smoothedRTT = sample
		r.meanDeviation = sample / 2
		return
	}

	r.meanDeviation = time.Duration(
		oneMinusBeta*float32(r.meanDeviation/time.Microsecond)+
			rttBeta*float32(utils.AbsDuration(r.smoothedRTT-sample)/time.Microsecond),
	) * time.Microsecond

	r.smoothedRTT = time.Duration(
		(float32(r.smoothedRTT/time.Microsecond)*oneMinusAlpha)+
			(float32(sample/time.Microsecond)*rttAlpha),
	) * time.Microsecond
}

func (r *RTTStats) updateRTTKalman(sample time.Duration) {
	if sample <= 0 {
		return
	}

	sampleUs := float64(sample) / float64(time.Microsecond)
	if !isFiniteFloat64(sampleUs) || sampleUs <= 0 {
		return
	}

	if !r.kalmanInitialized || r.smoothedRTT == 0 {
		r.kalmanInitialized = true

		r.kalmanLevelUs = sampleUs
		r.kalmanTrendUs = 0

		r.kalmanP00 = kalmanInitialLevelVariance
		r.kalmanP01 = 0
		r.kalmanP10 = 0
		r.kalmanP11 = kalmanInitialTrendVariance

		r.smoothedRTT = sample
		r.meanDeviation = sample / 2
		return
	}

	meanDeviationNew := time.Duration(
		oneMinusBeta*float32(
			r.meanDeviation/time.Microsecond,
		)+
			rttBeta*float32(
				utils.AbsDuration(
					r.smoothedRTT-sample,
				)/time.Microsecond,
			),
	) * time.Microsecond

	dt := kalmanDeltaT

	// ============================================================
	// 1. State prediction
	//
	// x_pred = F x
	//
	// F = [1 dt]
	//     [0  1]
	// ============================================================

	levelPredUs :=
		r.kalmanLevelUs +
			dt*r.kalmanTrendUs

	trendPredUs := r.kalmanTrendUs

	// ============================================================
	// 2. Covariance prediction
	//
	// P_pred = F P F^T + Q
	// ============================================================

	p00Pred :=
		r.kalmanP00 +
			dt*(r.kalmanP01+r.kalmanP10) +
			dt*dt*r.kalmanP11 +
			kalmanProcessNoiseLevel

	p01Pred :=
		r.kalmanP01 +
			dt*r.kalmanP11

	p10Pred :=
		r.kalmanP10 +
			dt*r.kalmanP11

	p11Pred :=
		r.kalmanP11 +
			kalmanProcessNoiseTrend

	if !isFiniteFloat64(levelPredUs) ||
		!isFiniteFloat64(trendPredUs) ||
		!isFiniteFloat64(p00Pred) ||
		!isFiniteFloat64(p01Pred) ||
		!isFiniteFloat64(p10Pred) ||
		!isFiniteFloat64(p11Pred) {

		utils.Debugf(
			"Rejecting invalid Kalman prediction",
		)
		return
	}

	if p00Pred < kalmanMinVariance {
		p00Pred = kalmanMinVariance
	}
	if p11Pred < kalmanMinVariance {
		p11Pred = kalmanMinVariance
	}

	predictedOffDiagonal :=
		0.5 * (p01Pred + p10Pred)

	p01Pred = predictedOffDiagonal
	p10Pred = predictedOffDiagonal

	predictedDeterminant :=
		p00Pred*p11Pred -
			predictedOffDiagonal*predictedOffDiagonal

	if !isFiniteFloat64(predictedDeterminant) ||
		predictedDeterminant < 0 {

		utils.Debugf(
			"Rejecting invalid predicted Kalman covariance",
		)
		return
	}

	// ============================================================
	// 3. Measurement residual
	//
	// residual = z - H x_pred
	//
	// H = [1 0]
	// ============================================================

	residualUs :=
		sampleUs - levelPredUs

	if !isFiniteFloat64(residualUs) {
		return
	}

	// ============================================================
	// 4. Innovation covariance
	//
	// S = H P_pred H^T + R
	//
	// Vì H = [1 0]:
	// S = P00_pred + R
	// ============================================================

	innovationVarianceUs2 :=
		p00Pred + kalmanMeasurementNoise

	if !isFiniteFloat64(innovationVarianceUs2) ||
		innovationVarianceUs2 <= 0 {

		utils.Debugf(
			"Rejecting invalid Kalman innovation variance",
		)
		return
	}

	// ============================================================
	// 5. Kalman gain
	//
	// K = P_pred H^T S^-1
	// ============================================================

	kLevel :=
		p00Pred / innovationVarianceUs2

	kTrend :=
		p10Pred / innovationVarianceUs2

	if !isFiniteFloat64(kLevel) ||
		!isFiniteFloat64(kTrend) {

		utils.Debugf(
			"Rejecting invalid Kalman gain",
		)
		return
	}

	// ============================================================
	// 6. State update
	//
	// x_new = x_pred + K residual
	// ============================================================

	levelNewUs :=
		levelPredUs +
			kLevel*residualUs

	trendNewUs :=
		trendPredUs +
			kTrend*residualUs

	if !isFiniteFloat64(levelNewUs) ||
		!isFiniteFloat64(trendNewUs) ||
		levelNewUs <= 0 {

		utils.Debugf(
			"Rejecting invalid Kalman state: "+
				"level=%f trend=%f",
			levelNewUs,
			trendNewUs,
		)
		return
	}

	maxDurationUs :=
		float64(
			time.Duration(1<<63-1) /
				time.Microsecond,
		)

	if levelNewUs > maxDurationUs {
		utils.Debugf(
			"Rejecting Kalman RTT exceeding time.Duration",
		)
		return
	}

	// ============================================================
	// 7. Joseph-form covariance update
	//
	// P_new =
	// (I-KH) P_pred (I-KH)^T + K R K^T
	// ============================================================

	// A = I - K H
	//
	// A = [1-kLevel    0]
	//     [-kTrend     1]
	a00 := 1.0 - kLevel
	a01 := 0.0
	a10 := -kTrend
	a11 := 1.0

	// AP = A * P_pred
	ap00 := a00*p00Pred + a01*p10Pred
	ap01 := a00*p01Pred + a01*p11Pred
	ap10 := a10*p00Pred + a11*p10Pred
	ap11 := a10*p01Pred + a11*p11Pred

	// P_new = AP*A^T + K*R*K^T
	p00New :=
		ap00*a00 +
			ap01*a01 +
			kLevel*kLevel*kalmanMeasurementNoise

	p01New :=
		ap00*a10 +
			ap01*a11 +
			kLevel*kTrend*kalmanMeasurementNoise

	p10New :=
		ap10*a00 +
			ap11*a01 +
			kTrend*kLevel*kalmanMeasurementNoise

	p11New :=
		ap10*a10 +
			ap11*a11 +
			kTrend*kTrend*kalmanMeasurementNoise

	if !isFiniteFloat64(p00New) ||
		!isFiniteFloat64(p01New) ||
		!isFiniteFloat64(p10New) ||
		!isFiniteFloat64(p11New) {

		utils.Debugf(
			"Rejecting invalid updated Kalman covariance",
		)
		return
	}

	if p00New < kalmanMinVariance {
		p00New = kalmanMinVariance
	}
	if p11New < kalmanMinVariance {
		p11New = kalmanMinVariance
	}

	offDiagonal :=
		0.5 * (p01New + p10New)

	covarianceDeterminant :=
		p00New*p11New -
			offDiagonal*offDiagonal

	if !isFiniteFloat64(offDiagonal) ||
		!isFiniteFloat64(covarianceDeterminant) ||
		covarianceDeterminant < 0 {

		utils.Debugf(
			"Rejecting non-positive-semidefinite " +
				"Kalman covariance",
		)
		return
	}

	r.kalmanLevelUs = levelNewUs
	r.kalmanTrendUs = trendNewUs

	r.kalmanP00 = p00New
	r.kalmanP01 = offDiagonal
	r.kalmanP10 = offDiagonal
	r.kalmanP11 = p11New

	r.smoothedRTT = time.Duration(
		levelNewUs * float64(time.Microsecond),
	)

	r.meanDeviation = meanDeviationNew
}

func (r *RTTStats) updateRecentMinRTT(sample time.Duration, now time.Time) { // Recent minRTT update.
	if r.numMinRTTsamplesRemaining > 0 {
		r.numMinRTTsamplesRemaining--
		if r.newMinRTT.rtt == 0 || sample <= r.newMinRTT.rtt {
			r.newMinRTT = rttSample{rtt: sample, time: now}
		}
		if r.numMinRTTsamplesRemaining == 0 {
			r.recentMinRTT = r.newMinRTT
			r.halfWindowRTT = r.newMinRTT
			r.quarterWindowRTT = r.newMinRTT
		}
	}

	// Update the three recent rtt samples.
	if r.recentMinRTT.rtt == 0 || sample <= r.recentMinRTT.rtt {
		r.recentMinRTT = rttSample{rtt: sample, time: now}
		r.halfWindowRTT = r.recentMinRTT
		r.quarterWindowRTT = r.recentMinRTT
	} else if sample <= r.halfWindowRTT.rtt {
		r.halfWindowRTT = rttSample{rtt: sample, time: now}
		r.quarterWindowRTT = r.halfWindowRTT
	} else if sample <= r.quarterWindowRTT.rtt {
		r.quarterWindowRTT = rttSample{rtt: sample, time: now}
	}

	// Expire old min rtt samples.
	if r.recentMinRTT.time.Before(now.Add(-r.recentMinRTTwindow)) {
		r.recentMinRTT = r.halfWindowRTT
		r.halfWindowRTT = r.quarterWindowRTT
		r.quarterWindowRTT = rttSample{rtt: sample, time: now}
	} else if r.halfWindowRTT.time.Before(now.Add(-time.Duration(float32(r.recentMinRTTwindow/time.Microsecond)*halfWindow) * time.Microsecond)) {
		r.halfWindowRTT = r.quarterWindowRTT
		r.quarterWindowRTT = rttSample{rtt: sample, time: now}
	} else if r.quarterWindowRTT.time.Before(now.Add(-time.Duration(float32(r.recentMinRTTwindow/time.Microsecond)*quarterWindow) * time.Microsecond)) {
		r.quarterWindowRTT = rttSample{rtt: sample, time: now}
	}
}

// SampleNewRecentMinRTT forces RttStats to sample a new recent min rtt within the next
// |numSamples| UpdateRTT calls.
func (r *RTTStats) SampleNewRecentMinRTT(numSamples uint32) {
	r.numMinRTTsamplesRemaining = numSamples
	r.newMinRTT = rttSample{}
}

// OnConnectionMigration is called when connection migrates and rtt measurement needs to be reset.
func (r *RTTStats) OnConnectionMigration() {
	r.latestRTT = 0
	r.minRTT = 0
	r.smoothedRTT = 0
	r.meanDeviation = 0

	r.kalmanInitialized = false
	r.kalmanLevelUs = 0
	r.kalmanTrendUs = 0

	r.kalmanP00 = 0
	r.kalmanP01 = 0
	r.kalmanP10 = 0
	r.kalmanP11 = 0

	r.initialRTTus = initialRTTus
	r.numMinRTTsamplesRemaining = 0
	r.recentMinRTTwindow = utils.InfDuration

	r.recentMinRTT = rttSample{}
	r.halfWindowRTT = rttSample{}
	r.quarterWindowRTT = rttSample{}
}

// ExpireSmoothedMetrics causes the smoothed_rtt to be increased to the latest_rtt if the latest_rtt
// is larger. The mean deviation is increased to the most recent deviation if
// it's larger.
func (r *RTTStats) ExpireSmoothedMetrics() {
	r.meanDeviation = utils.MaxDuration(
		r.meanDeviation,
		utils.AbsDuration(
			r.smoothedRTT-r.latestRTT,
		),
	)

	r.smoothedRTT = utils.MaxDuration(
		r.smoothedRTT,
		r.latestRTT,
	)

	if rttEstimatorMode != RTTEstimatorKalman ||
		r.smoothedRTT <= 0 {
		return
	}

	r.kalmanInitialized = true
	r.kalmanLevelUs =
		float64(r.smoothedRTT) /
			float64(time.Microsecond)

	r.kalmanTrendUs = 0

	if !isFiniteFloat64(r.kalmanP00) ||
		r.kalmanP00 < kalmanMinVariance {

		r.kalmanP00 =
			kalmanInitialLevelVariance
	}

	if !isFiniteFloat64(r.kalmanP11) ||
		r.kalmanP11 < kalmanMinVariance {

		r.kalmanP11 =
			kalmanInitialTrendVariance
	}

	r.kalmanP01 = 0
	r.kalmanP10 = 0
}

// XXX (QDC): This is subject to improvement
// Update the smoothed RTT to the given value
func (r *RTTStats) UpdateSessionRTT(
	smoothedRTT time.Duration,
) {
	r.smoothedRTT = smoothedRTT

	if rttEstimatorMode != RTTEstimatorKalman ||
		smoothedRTT <= 0 {
		return
	}

	levelUs :=
		float64(smoothedRTT) /
			float64(time.Microsecond)

	if !isFiniteFloat64(levelUs) ||
		levelUs <= 0 {

		utils.Debugf(
			"Ignoring invalid session RTT: %v",
			smoothedRTT,
		)
		return
	}

	r.kalmanLevelUs = levelUs

	r.kalmanTrendUs = 0

	if !r.kalmanInitialized {
		r.kalmanInitialized = true

		r.kalmanP00 =
			kalmanInitialLevelVariance
		r.kalmanP01 = 0
		r.kalmanP10 = 0
		r.kalmanP11 =
			kalmanInitialTrendVariance
	}
}
