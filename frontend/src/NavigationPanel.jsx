import React, { useState, useEffect, useRef, useCallback } from 'react';
import { getBearing, getDistance, getTurnDirection, generateInstructions, interpolate } from './navUtils';
import './NavigationPanel.css';

export default function NavigationPanel({ route, totalTime, onPositionUpdate, onNavStateUpdate, onClose, autoStart }) {
  const [isNavigating, setIsNavigating] = useState(false);
  const [navMode, setNavMode] = useState(null); // 'sim' | 'gps'
  const [currentStep, setCurrentStep] = useState(0);
  const [distanceRemaining, setDistanceRemaining] = useState(0);
  const [timeRemaining, setTimeRemaining] = useState(0);
  const [progress, setProgress] = useState(0);
  const [instructions, setInstructions] = useState([]);

  const simRef = useRef(null);
  const gpsRef = useRef(null);
  const stepListRef = useRef(null);
  const totalDistRef = useRef(0);
  const instructionsRef = useRef([]);
  const currentStepRef = useRef(0);

  // Generate instructions when route changes
  useEffect(() => {
    if (route && route.length > 1) {
      const instrs = generateInstructions(route);
      setInstructions(instrs);
      instructionsRef.current = instrs;

      // Calculate total distance
      let total = 0;
      for (let i = 0; i < route.length - 1; i++) {
        total += getDistance(route[i], route[i + 1]);
      }
      totalDistRef.current = total;
      setDistanceRemaining(Math.round(total));
      setTimeRemaining(Math.round(totalTime || total / 1.4)); // 1.4 m/s walking speed
    }
  }, [route, totalTime]);

  // Scroll active step into view
  useEffect(() => {
    if (stepListRef.current) {
      const activeEl = stepListRef.current.querySelector('.nav-step.active');
      if (activeEl) {
        activeEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }
  }, [currentStep]);

  // Report navigation state to parent (for voice nav integration)
  useEffect(() => {
    if (onNavStateUpdate) {
      onNavStateUpdate({ instructions, currentStep, isNavigating });
    }
  }, [instructions, currentStep, isNavigating, onNavStateUpdate]);

  // Keep currentStepRef in sync
  useEffect(() => {
    currentStepRef.current = currentStep;
  }, [currentStep]);

  // Find which step we're at (forward-only — never regress to earlier steps)
  // Uses refs to avoid stale closures in GPS watchPosition callbacks
  const updateStep = useCallback((pos) => {
    const instrs = instructionsRef.current;
    if (!instrs.length) return;
    const step = currentStepRef.current;
    const safeCurrentStep = Math.min(step, instrs.length - 1);
    let newStep = safeCurrentStep;
    for (let i = safeCurrentStep + 1; i < instrs.length; i++) {
      const d = getDistance(pos, instrs[i].coord);
      if (d < 35) {
        newStep = i;
        break;
      }
    }
    if (newStep > safeCurrentStep) {
      setCurrentStep(newStep);
      currentStepRef.current = newStep;
    }
  }, []);

  // Simulation: animate along the route (10fps — smooth on mobile without killing perf)
  const startSimulation = () => {
    if (!route || route.length < 2) return;
    setIsNavigating(true);
    setNavMode('sim');
    setCurrentStep(0);
    setProgress(0);

    let segIndex = 0;
    let fraction = 0;
    const totalSegs = route.length - 1;
    const speed = 0.05; // fraction per tick at 10fps (same visual pace as old 0.008 at 60fps)

    const tick = () => {
      fraction += speed;

      if (fraction >= 1) {
        fraction = 0;
        segIndex++;
        if (segIndex >= totalSegs) {
          // Arrived
          clearInterval(simRef.current);
          simRef.current = null;
          const endPos = route[route.length - 1];
          onPositionUpdate(endPos);
          setProgress(100);
          setDistanceRemaining(0);
          setTimeRemaining(0);
          const lastStep = Math.max(0, instructionsRef.current.length - 1);
          setCurrentStep(lastStep);
          currentStepRef.current = lastStep;
          setNavMode(null);
          return;
        }
      }

      const pos = interpolate(route[segIndex], route[segIndex + 1], fraction);
      onPositionUpdate(pos);

      // Update progress
      const progressPct = ((segIndex + fraction) / totalSegs) * 100;
      setProgress(progressPct);

      // Update remaining distance
      let remaining = 0;
      remaining += getDistance(pos, route[segIndex + 1]) * (1 - fraction);
      for (let i = segIndex + 1; i < totalSegs; i++) {
        remaining += getDistance(route[i], route[i + 1]);
      }
      setDistanceRemaining(Math.round(remaining));
      setTimeRemaining(Math.round(remaining / 1.4));

      updateStep(pos);
    };

    simRef.current = setInterval(tick, 100); // 10fps — 6x fewer re-renders than rAF
  };

  const [gpsError, setGpsError] = useState(null);

  // GPS: track real position
  const startGPS = () => {
    if (!navigator.geolocation) {
      alert('Geolocation is not supported by your browser');
      return;
    }
    setIsNavigating(true);
    setNavMode('gps');
    setCurrentStep(0);
    setProgress(0);
    setGpsError(null);

    gpsRef.current = navigator.geolocation.watchPosition(
      (position) => {
        setGpsError(null);
        const pos = [position.coords.latitude, position.coords.longitude];
        onPositionUpdate(pos);
        updateStep(pos);

        // Calculate remaining distance along route (not straight-line)
        let closestIdx = 0;
        let closestDist = Infinity;
        for (let i = 0; i < route.length; i++) {
          const d = getDistance(pos, route[i]);
          if (d < closestDist) {
            closestDist = d;
            closestIdx = i;
          }
        }
        let remaining = 0;
        for (let i = closestIdx; i < route.length - 1; i++) {
          remaining += getDistance(route[i], route[i + 1]);
        }
        setDistanceRemaining(Math.round(remaining));
        setTimeRemaining(Math.round(remaining / 1.4));

        // Update progress
        const total = totalDistRef.current;
        if (total > 0) {
          setProgress(Math.max(0, Math.min(100, ((total - remaining) / total) * 100)));
        }

        // Check for arrival
        const destDist = getDistance(pos, route[route.length - 1]);
        if (destDist < 20) {
          setProgress(100);
          setDistanceRemaining(0);
          setTimeRemaining(0);
          const lastStep = Math.max(0, instructionsRef.current.length - 1);
          setCurrentStep(lastStep);
          currentStepRef.current = lastStep;
        }
      },
      (error) => {
        console.error('GPS error:', error);
        if (error.code === 1) {
          setGpsError('Location access denied. Please enable location permissions in your browser settings.');
        } else if (error.code === 2) {
          setGpsError('Unable to determine your location. Please check your GPS settings.');
        } else if (error.code === 3) {
          setGpsError('Location request timed out. Trying again...');
        }
      },
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 30000 }
    );
  };

  // Auto-start GPS navigation by default (like Google Maps)
  // Wait for instructions to be generated before starting
  useEffect(() => {
    if (!route || route.length < 2 || isNavigating) return;
    if (!instructions.length) return; // Wait for instructions to be ready
    if (autoStart === 'sim') {
      startSimulation();
    } else {
      startGPS();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoStart, route, instructions]);

  const stopNavigation = () => {
    if (simRef.current) clearInterval(simRef.current);
    if (gpsRef.current) navigator.geolocation.clearWatch(gpsRef.current);
    setIsNavigating(false);
    setNavMode(null);
    setProgress(0);
    setCurrentStep(0);
    onPositionUpdate(null);
  };

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (simRef.current) clearInterval(simRef.current);
      if (gpsRef.current) navigator.geolocation.clearWatch(gpsRef.current);
    };
  }, []);

  const formatDist = (m) => {
    if (m >= 1000) return `${(m / 1000).toFixed(1)} km`;
    return `${m} m`;
  };

  const formatTime = (s) => {
    const mins = Math.floor(s / 60);
    const secs = Math.round(s % 60);
    if (mins > 0) return `${mins} min`;
    return `${secs} sec`;
  };

  // Current instruction to display prominently
  const currentInstruction = instructions[currentStep] || null;
  const nextInstruction = instructions[currentStep + 1] || null;

  return (
    <div className="nav-panel">
      <div className="nav-header">
        <span className="nav-title">Navigation</span>
        <button className="nav-close" onClick={() => { stopNavigation(); onClose(); }}>
          ✕
        </button>
      </div>

      {!isNavigating ? (
        // Pre-navigation: starting GPS...
        <div className="nav-start-section">
          <div className="nav-route-summary">
            <div className="nav-summary-item">
              <span className="nav-summary-value">{formatDist(distanceRemaining)}</span>
              <span className="nav-summary-label">Distance</span>
            </div>
            <div className="nav-summary-item">
              <span className="nav-summary-value">{formatTime(timeRemaining)}</span>
              <span className="nav-summary-label">Est. Time</span>
            </div>
            <div className="nav-summary-item">
              <span className="nav-summary-value">{instructions.length}</span>
              <span className="nav-summary-label">Steps</span>
            </div>
          </div>
          <button className="nav-start-btn gps" onClick={startGPS}>
            Start navigation
          </button>
          <button className="nav-demo-link" onClick={startSimulation}>
            Demo mode
          </button>
        </div>
      ) : (
        // Active navigation
        <div className="nav-active">
          {/* GPS error banner */}
          {gpsError && (
            <div className="nav-gps-error">
              <span>{gpsError}</span>
              <button onClick={() => { stopNavigation(); startSimulation(); }}>
                Use Demo Mode
              </button>
            </div>
          )}
          {/* Mode indicator + Progress bar */}
          <div className="nav-mode-badge">
            {navMode === 'sim' ? 'Demo mode' : 'Navigating'}
          </div>
          <div className="nav-progress-bar">
            <div className="nav-progress-fill" style={{ width: `${progress}%` }} />
          </div>

          {/* Current instruction - big display */}
          {currentInstruction && (
            <div className="nav-current">
              <span className="nav-current-icon">{currentInstruction.icon}</span>
              <div className="nav-current-text">
                <span className="nav-current-label">{currentInstruction.label}</span>
                {nextInstruction && (
                  <span className="nav-current-next">
                    Then: {nextInstruction.icon} {nextInstruction.label} in {formatDist(nextInstruction.distance)}
                  </span>
                )}
              </div>
            </div>
          )}

          {/* Stats row */}
          <div className="nav-stats">
            <div className="nav-stat">
              <span className="nav-stat-value">{formatDist(distanceRemaining)}</span>
              <span className="nav-stat-label">Remaining</span>
            </div>
            <div className="nav-stat">
              <span className="nav-stat-value">{formatTime(timeRemaining)}</span>
              <span className="nav-stat-label">ETA</span>
            </div>
            <div className="nav-stat">
              <span className="nav-stat-value">{Math.round(progress)}%</span>
              <span className="nav-stat-label">Done</span>
            </div>
          </div>

          {/* Step list */}
          <div className="nav-steps" ref={stepListRef}>
            {instructions.map((instr, i) => (
              <div
                key={i}
                className={`nav-step ${i === currentStep ? 'active' : ''} ${i < currentStep ? 'done' : ''}`}
              >
                <span className="nav-step-icon">{i < currentStep ? '✓' : instr.icon}</span>
                <span className="nav-step-label">{instr.label}</span>
                {instr.distance > 0 && (
                  <span className="nav-step-dist">{formatDist(instr.distance)}</span>
                )}
              </div>
            ))}
          </div>

          {/* Stop button */}
          <button className="nav-stop-btn" onClick={stopNavigation}>
            ■ Stop Navigation
          </button>
        </div>
      )}
    </div>
  );
}
