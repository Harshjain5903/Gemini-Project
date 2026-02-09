import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import Map, { Source, Layer, Marker, NavigationControl } from 'react-map-gl/mapbox';
import 'mapbox-gl/dist/mapbox-gl.css';
import { getBearing } from './navUtils';
import ChatPanel from './ChatPanel';
import NavigationPanel from './NavigationPanel';
import MobileBottomSheet from './MobileBottomSheet';
import MobileNavBar from './MobileNavBar';
import LocationSearch from './LocationSearch';
import './App.css';

export default function App() {
  // Chicago bounds for reference
  const CHICAGO_BOUNDS = {
    minLat: 41.644, maxLat: 42.023,
    minLng: -87.940, maxLng: -87.524
  };

  const [viewState, setViewState] = useState({
    longitude: -87.6298,
    latitude: 41.8781,
    zoom: 12,
    pitch: 40,
    bearing: 0
  });

  const [routeData, setRouteData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [points, setPoints] = useState({ start: null, end: null });
  const [mode, setMode] = useState('start');
  const [hour, setHour] = useState(17);
  const [beta, setBeta] = useState(5);
  const [riskData, setRiskData] = useState(null);
  const [showHeatmap, setShowHeatmap] = useState(true);
  const [activeTab, setActiveTab] = useState('manual');
  const [travelMode, setTravelMode] = useState('walking');
  const [navRoute, setNavRoute] = useState(null);
  const [navTime, setNavTime] = useState(0);
  const [navPosition, setNavPosition] = useState(null);
  const [navBearing, setNavBearing] = useState(0);
  const [showNav, setShowNav] = useState(false);
  const [navInstructions, setNavInstructions] = useState([]);
  const [navCurrentStep, setNavCurrentStep] = useState(0);
  const [isActivelyNavigating, setIsActivelyNavigating] = useState(false);
  const [navAutoStart, setNavAutoStart] = useState(null);
  const [weather, setWeather] = useState(null);
  const [userCoords, setUserCoords] = useState(null);
  const [isMobile, setIsMobile] = useState(window.innerWidth <= 768);
  const [sheetPosition, setSheetPosition] = useState('peek');
  const [mapLoaded, setMapLoaded] = useState(false);
  const mapRef = useRef(null);

  // Mobile detection
  useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth <= 768);
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // Auto-collapse sheet when active navigation starts
  useEffect(() => {
    if (isMobile && showNav && isActivelyNavigating) {
      setSheetPosition('collapsed');
    }
  }, [isMobile, showNav, isActivelyNavigating]);

  // useCallback so voice callLoop always has a stable reference (avoids stale closures)
  const handleChatRoute = useCallback((chatRouteData, startCoords, endCoords, chatHour, chatBeta, chatTravelMode) => {
    setRouteData(chatRouteData);
    setPoints({ start: startCoords, end: endCoords });
    setHour(chatHour ?? 17);
    setBeta(chatBeta ?? 5);
    if (chatTravelMode) setTravelMode(chatTravelMode);
    if (window.innerWidth <= 768) setSheetPosition('peek');
  }, []);

  // Use ref so startNavigation always reads the LATEST routeData (not a stale closure)
  const routeDataRef = useRef(null);
  useEffect(() => { routeDataRef.current = routeData; }, [routeData]);

  const startNavigation = useCallback((routeType, autoStart = 'gps') => {
    const rd = routeDataRef.current;
    if (!rd) return;
    const coords = routeType === 'fastest' ? rd.fastest_route : rd.safest_route;
    const time = routeType === 'fastest'
      ? rd.metrics.fastest.total_time
      : rd.metrics.safest.total_time;
    setNavRoute(coords);
    setNavTime(time);
    setShowNav(true);
    setNavAutoStart(autoStart);
  }, []);

  const handleNavStateUpdate = useCallback(({ instructions, currentStep, isNavigating }) => {
    setNavInstructions(instructions);
    setNavCurrentStep(currentStep);
    setIsActivelyNavigating(isNavigating);
  }, []);

  const navContext = {
    instructions: navInstructions,
    currentStep: navCurrentStep,
    currentPosition: navPosition,
    isNavigating: isActivelyNavigating,
    route: navRoute,
    travelMode: travelMode,
  };

  const closeNavigation = () => {
    setShowNav(false);
    setNavRoute(null);
    setNavPosition(null);
    setNavInstructions([]);
    setNavCurrentStep(0);
    setIsActivelyNavigating(false);
    setNavAutoStart(null);
    // Reset map to overview (exit navigation view)
    setViewState(prev => ({ ...prev, zoom: 14, pitch: 40, bearing: 0 }));
    // Re-open bottom sheet on mobile so user can see chat
    if (window.innerWidth <= 768) setSheetPosition('peek');
  };

  // Load risk heatmap data based on selected hour
  useEffect(() => {
    fetch(`/api/heatmap/${hour}`)
      .then(res => res.json())
      .then(data => setRiskData(data))
      .catch(err => {
        console.log('Dynamic heatmap failed, trying static:', err);
        fetch('/grid_risk.geojson')
          .then(res => res.json())
          .then(data => setRiskData(data))
          .catch(err2 => console.log('Risk data not loaded:', err2));
      });
  }, [hour]);

  // Fetch weather data on load and every 10 minutes
  useEffect(() => {
    const fetchWeather = () => {
      fetch('/api/weather')
        .then(res => res.json())
        .then(data => {
          if (data.status === 'success') setWeather(data.data);
        })
        .catch(() => {});
    };
    fetchWeather();
    const interval = setInterval(fetchWeather, 600000);
    return () => clearInterval(interval);
  }, []);

  // Get user's GPS position
  useEffect(() => {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      (pos) => setUserCoords([pos.coords.latitude, pos.coords.longitude]),
      () => {},
      { enableHighAccuracy: true, timeout: 30000 }
    );
    const watchId = navigator.geolocation.watchPosition(
      (pos) => setUserCoords([pos.coords.latitude, pos.coords.longitude]),
      () => {},
      { enableHighAccuracy: true, maximumAge: 5000 }
    );
    return () => navigator.geolocation.clearWatch(watchId);
  }, []);

  // Route GeoJSON for declarative rendering (survives map re-renders/style changes)
  const fastestGeoJSON = useMemo(() => {
    if (!routeData?.fastest_route || routeData.fastest_route.length < 2) return null;
    return {
      type: 'Feature',
      properties: {},
      geometry: {
        type: 'LineString',
        coordinates: routeData.fastest_route.map(p => [Number(p[1]), Number(p[0])])
      }
    };
  }, [routeData]);

  const safestGeoJSON = useMemo(() => {
    if (!routeData?.safest_route || routeData.safest_route.length < 2) return null;
    return {
      type: 'Feature',
      properties: {},
      geometry: {
        type: 'LineString',
        coordinates: routeData.safest_route.map(p => [Number(p[1]), Number(p[0])])
      }
    };
  }, [routeData]);

  // Fit map to route bounds when route changes
  useEffect(() => {
    if (!routeData || !mapRef.current) return;
    const allCoords = [...(routeData.fastest_route || []), ...(routeData.safest_route || [])];
    if (allCoords.length < 2) return;
    const map = mapRef.current.getMap ? mapRef.current.getMap() : mapRef.current;
    if (!map) return;
    const lngs = allCoords.map(p => Number(p[1]));
    const lats = allCoords.map(p => Number(p[0]));
    try {
      const isMobileNow = window.innerWidth <= 768;
      map.fitBounds(
        [[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]],
        { padding: isMobileNow ? { top: 80, left: 40, right: 40, bottom: Math.round(window.innerHeight * 0.5) } : 80, duration: 1000 }
      );
    } catch (err) {
      console.error('[Map] fitBounds error:', err);
    }
  }, [routeData]);

  // Google Maps-style camera: follow user, rotate to bearing, zoom to street level
  const navFollowRef = useRef(0);
  useEffect(() => {
    if (!isActivelyNavigating || !navPosition) return;

    // Throttle to ~2fps so map animates smoothly between updates
    const now = Date.now();
    if (now - navFollowRef.current < 500) return;
    navFollowRef.current = now;

    const map = mapRef.current?.getMap ? mapRef.current.getMap() : mapRef.current;
    if (!map) return;

    // Calculate bearing from closest route segment
    let bearing = 0;
    if (navRoute && navRoute.length > 1) {
      let closestIdx = 0;
      let closestDist = Infinity;
      for (let i = 0; i < navRoute.length; i++) {
        const dlat = navRoute[i][0] - navPosition[0];
        const dlng = navRoute[i][1] - navPosition[1];
        const d = dlat * dlat + dlng * dlng;
        if (d < closestDist) { closestDist = d; closestIdx = i; }
      }
      if (closestIdx < navRoute.length - 1) {
        bearing = getBearing(navRoute[closestIdx], navRoute[closestIdx + 1]);
      }
    }

    setNavBearing(bearing);
    map.easeTo({
      center: [navPosition[1], navPosition[0]],
      zoom: 17,
      pitch: 60,
      bearing,
      duration: 500,
    });
  }, [navPosition, isActivelyNavigating, navRoute]);

  const handleMapClick = (evt) => {
    const { lat, lng } = evt.lngLat;
    if (mode === 'start') {
      setPoints(p => ({ ...p, start: [lat, lng] }));
      setMode('end');
    } else {
      setPoints(p => ({ ...p, end: [lat, lng] }));
      // Both points set — re-open sheet so user can see results
      if (isMobile) setSheetPosition('peek');
    }
  };

  const runAnalysis = async () => {
    if (!points.start || !points.end) return;
    setLoading(true);
    try {
      const response = await fetch('/api/compare-routes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          start: points.start,
          end: points.end,
          beta: beta,
          hour: hour,
          is_weekend: false,
          travel_mode: travelMode
        })
      });
      const data = await response.json();
      if (data.status === 'success') {
        setRouteData(data.data);
      } else {
        alert('Route calculation failed: ' + data.message);
      }
    } catch {
      alert("Backend error! Make sure Flask server is running on port 5001.");
    }
    setLoading(false);
  };

  const resetPoints = () => {
    setPoints({ start: null, end: null });
    setRouteData(null);
    setMode('start');
  };

  const getTimeLabel = (h) => {
    if (h === 0) return '12 AM';
    if (h === 12) return '12 PM';
    if (h < 12) return `${h} AM`;
    return `${h - 12} PM`;
  };

  // Sidebar inner content — shared between desktop sidebar and mobile bottom sheet
  const sidebarContent = (
    <>
      {/* Header */}
      <div className="header">
        <div className="logo">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22s-8-4.5-8-11.8A8 8 0 0 1 12 2a8 8 0 0 1 8 8.2c0 7.3-8 11.8-8 11.8z"/><circle cx="12" cy="10" r="3"/></svg>
        </div>
        <div>
          <h1>SafePath</h1>
          <p>Chicago Risk-Aware Navigation</p>
        </div>
        <span className="gemini-badge">Gemini 3</span>
      </div>

      {/* Stats */}
      <div className="stats-bar">
        <div className="stat-item">
          <span className="stat-num">49K+</span>
          <span className="stat-label">crashes analyzed</span>
        </div>
        <div className="stat-sep"></div>
        <div className="stat-item">
          <span className="stat-num cyan">18K+</span>
          <span className="stat-label">crime reports</span>
        </div>
        <div className="stat-sep"></div>
        <div className="stat-item">
          <span className="stat-num green">4.8K</span>
          <span className="stat-label">risk zones</span>
        </div>
      </div>

      {/* Weather Widget */}
      {weather?.current && weather.current.temperature !== null && (
        <div className="weather-widget">
          <span className="weather-icon">{weather.current.icon}</span>
          <div className="weather-info">
            <span className="weather-temp">{Math.round(weather.current.temperature)}°F</span>
            <span className="weather-desc">{weather.current.description}</span>
          </div>
          <div className="weather-details">
            {weather.current.wind_speed > 0 && <span>💨 {weather.current.wind_speed} mph</span>}
            {weather.current.risk_multiplier > 1.1 && (
              <span className="weather-risk">⚠️ +{Math.round((weather.current.risk_multiplier - 1) * 100)}% risk</span>
            )}
          </div>
        </div>
      )}

      {/* Heatmap Toggle */}
      <div className="toggle-section">
        <label className="toggle-label">
          <input
            type="checkbox"
            checked={showHeatmap}
            onChange={(e) => setShowHeatmap(e.target.checked)}
          />
          <span className="toggle-text">Risk heatmap</span>
        </label>
      </div>

      {/* Tab Switcher */}
      <div className="tab-switcher">
        <button
          className={`tab-btn ${activeTab === 'manual' ? 'active' : ''}`}
          onClick={() => setActiveTab('manual')}
        >
          Manual
        </button>
        <button
          className={`tab-btn ${activeTab === 'chat' ? 'active' : ''}`}
          onClick={() => setActiveTab('chat')}
        >
          AI Chat
        </button>
      </div>

      {activeTab === 'manual' ? (
        <>
          {/* Route Input */}
          <div className="section">
            <label className="section-label">Route</label>
            <div className="route-input-group">
              <div className="route-search-row">
                <div className="route-dot origin"></div>
                <LocationSearch
                  placeholder="Search origin or tap map"
                  value={points.start}
                  onSelect={(coords) => { setPoints(p => ({ ...p, start: coords })); setMode('end'); }}
                  onClear={() => setPoints(p => ({ ...p, start: null }))}
                  onFocus={() => { setMode('start'); if (isMobile) setSheetPosition('expanded'); }}
                />
                {!points.start && userCoords && (
                  <button
                    className="use-location-btn"
                    onClick={() => { setPoints(p => ({ ...p, start: userCoords })); setMode('end'); }}
                    title="Use my current location"
                  >
                    📍
                  </button>
                )}
              </div>
              <div className="route-input-divider"></div>
              <div className="route-search-row">
                <div className="route-dot destination"></div>
                <LocationSearch
                  placeholder="Search destination or tap map"
                  value={points.end}
                  onSelect={(coords) => { setPoints(p => ({ ...p, end: coords })); if (isMobile) setSheetPosition('peek'); }}
                  onClear={() => setPoints(p => ({ ...p, end: null }))}
                  onFocus={() => { setMode('end'); if (isMobile) setSheetPosition('expanded'); }}
                />
                {!points.end && userCoords && (
                  <button
                    className="use-location-btn"
                    onClick={() => setPoints(p => ({ ...p, end: userCoords }))}
                    title="Use my current location"
                  >
                    📍
                  </button>
                )}
              </div>
            </div>
            {(points.start || points.end) && (
              <button onClick={resetPoints} className="reset-btn">Clear route</button>
            )}
          </div>

          {/* Travel Mode */}
          <div className="section">
            <label className="section-label">Travel mode</label>
            <div className="mode-switcher">
              <button
                className={`mode-btn ${travelMode === 'walking' ? 'active' : ''}`}
                onClick={() => setTravelMode('walking')}
              >
                Walk
              </button>
              <button
                className={`mode-btn ${travelMode === 'cycling' ? 'active' : ''}`}
                onClick={() => setTravelMode('cycling')}
              >
                Bike
              </button>
              <button
                className={`mode-btn ${travelMode === 'driving' ? 'active' : ''}`}
                onClick={() => setTravelMode('driving')}
              >
                Drive
              </button>
            </div>
            <div className="mode-hint">
              {travelMode === 'walking' && 'Crime risk weighted 70%, crashes 30%'}
              {travelMode === 'cycling' && 'Crime + crash risk weighted equally'}
              {travelMode === 'driving' && 'Crash risk weighted 90%, crime 10%'}
            </div>
          </div>

          {/* Time Slider */}
          <div className="section">
            <div className="section-header">
              <label className="section-label">Departure time</label>
              <span className="time-display">{getTimeLabel(hour)}</span>
            </div>
            <input
              type="range"
              min="0"
              max="23"
              value={hour}
              onChange={(e) => setHour(parseInt(e.target.value))}
              className="slider"
            />
            <div className="slider-labels">
              <span>12am</span>
              <span>6am</span>
              <span>12pm</span>
              <span>6pm</span>
              <span>11pm</span>
            </div>
          </div>

          {/* Safety Slider */}
          <div className="section">
            <div className="section-header">
              <label className="section-label">Safety priority</label>
              <span className={`priority-badge ${beta > 6 ? 'green' : beta < 3 ? 'orange' : 'blue'}`}>
                {beta > 6 ? 'Safer' : beta < 3 ? 'Faster' : 'Balanced'}
              </span>
            </div>
            <input
              type="range"
              min="0"
              max="10"
              step="0.5"
              value={beta}
              onChange={(e) => setBeta(parseFloat(e.target.value))}
              className="slider"
            />
            <div className="slider-labels">
              <span>Speed</span>
              <span>Safety</span>
            </div>
          </div>

          {/* Calculate Button */}
          <button
            onClick={runAnalysis}
            disabled={loading || !points.start || !points.end}
            className="calculate-btn"
          >
            {loading ? 'Analyzing routes...' : 'Calculate routes'}
          </button>

          {/* Results */}
          {routeData && (
            <div className="results">
              <div className="results-header">Route comparison</div>

              <div className="result-cards">
                <div className="result-card green">
                  <span className="result-label">Risk reduction</span>
                  <span className="result-value">{routeData.metrics.reduction_in_risk_pct}%</span>
                </div>
                <div className="result-card orange">
                  <span className="result-label">Extra time</span>
                  <span className="result-value">+{Math.round(routeData.metrics.extra_time_seconds)}s</span>
                </div>
              </div>

              <div className="comparison">
                <div className="compare-item">
                  <div className="dot amber"></div>
                  <span>Fastest: {routeData.metrics.fastest.total_risk.toFixed(0)} risk</span>
                </div>
                <div className="compare-item">
                  <div className="dot green"></div>
                  <span>Safest: {routeData.metrics.safest.total_risk.toFixed(0)} risk</span>
                </div>
              </div>

              {/* Navigate buttons */}
              {!showNav && (
                <div className="nav-buttons">
                  <button className="nav-btn safest" onClick={() => startNavigation('safest')}>
                    Navigate safest
                  </button>
                  <button className="nav-btn fastest" onClick={() => startNavigation('fastest')}>
                    Navigate fastest
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Navigation Panel */}
          {showNav && navRoute && (
            <NavigationPanel
              route={navRoute}
              totalTime={navTime}
              onPositionUpdate={setNavPosition}
              onNavStateUpdate={handleNavStateUpdate}
              onClose={closeNavigation}
              autoStart={navAutoStart}
            />
          )}
        </>
      ) : (
        <>
          <ChatPanel onRouteReceived={handleChatRoute} onStartNavigation={startNavigation} navContext={navContext} userCoords={userCoords} weather={weather} />
          {showNav && navRoute && (
            <NavigationPanel
              route={navRoute}
              totalTime={navTime}
              onPositionUpdate={setNavPosition}
              onNavStateUpdate={handleNavStateUpdate}
              onClose={closeNavigation}
              autoStart={navAutoStart}
            />
          )}
        </>
      )}

      {/* Footer */}
      <div className="footer">
        <span>Chicago Open Data + Gemini 3 AI</span>
        <span className="status">● Online</span>
      </div>
    </>
  );

  return (
    <div className="app-container">
      {/* Desktop: classic sidebar layout */}
      {!isMobile && <div className="sidebar">{sidebarContent}</div>}

      {/* Map — always visible */}
      <div className="map-container">
        <Map
          ref={mapRef}
          {...viewState}
          onMove={evt => setViewState(evt.viewState)}
          onLoad={() => setMapLoaded(true)}
          mapboxAccessToken={import.meta.env.VITE_MAPBOX_TOKEN}
          mapStyle="mapbox://styles/mapbox/dark-v11"
          onClick={handleMapClick}
          style={{ width: '100%', height: '100%' }}
        >
          <NavigationControl position="top-right" />

          {/* Risk Heatmap Layer (declarative — this works fine) */}
          {riskData && showHeatmap && (
            <Source id="risk-data" type="geojson" data={riskData}>
              <Layer
                id="risk-heatmap"
                type="fill"
                paint={{
                  'fill-color': [
                    'interpolate',
                    ['linear'],
                    ['get', 'risk_score'],
                    0, 'rgba(0, 255, 100, 0.1)',
                    20, 'rgba(50, 205, 50, 0.2)',
                    40, 'rgba(255, 255, 0, 0.3)',
                    60, 'rgba(255, 165, 0, 0.4)',
                    80, 'rgba(255, 69, 0, 0.5)',
                    100, 'rgba(255, 0, 0, 0.6)'
                  ],
                  'fill-outline-color': 'rgba(255,255,255,0.1)'
                }}
              />
            </Source>
          )}

          {/* Route lines — declarative so they survive map style/heatmap re-renders */}
          {fastestGeoJSON && (
            <Source id="fastest-route" type="geojson" data={fastestGeoJSON}>
              <Layer
                id="fastest-line-bg"
                type="line"
                layout={{ 'line-join': 'round', 'line-cap': 'round' }}
                paint={{ 'line-color': '#000000', 'line-width': 10, 'line-opacity': 0.4 }}
              />
              <Layer
                id="fastest-line"
                type="line"
                layout={{ 'line-join': 'round', 'line-cap': 'round' }}
                paint={{ 'line-color': '#f59e0b', 'line-width': 6, 'line-opacity': 0.95 }}
              />
            </Source>
          )}
          {safestGeoJSON && (
            <Source id="safest-route" type="geojson" data={safestGeoJSON}>
              <Layer
                id="safest-line-bg"
                type="line"
                layout={{ 'line-join': 'round', 'line-cap': 'round' }}
                paint={{ 'line-color': '#000000', 'line-width': 10, 'line-opacity': 0.4 }}
              />
              <Layer
                id="safest-line"
                type="line"
                layout={{ 'line-join': 'round', 'line-cap': 'round' }}
                paint={{ 'line-color': '#10b981', 'line-width': 6, 'line-opacity': 1.0 }}
              />
            </Source>
          )}

          {/* Current location (blue GPS dot) */}
          {userCoords && (
            <Marker longitude={userCoords[1]} latitude={userCoords[0]} anchor="center">
              <div className="my-location-marker">
                <div className="my-location-pulse"></div>
                <div className="my-location-dot"></div>
              </div>
            </Marker>
          )}

          {/* Origin marker (Google Maps green circle) */}
          {points.start && (
            <Marker longitude={points.start[1]} latitude={points.start[0]} anchor="center">
              <div className="gm-origin-marker">
                <div className="gm-origin-pulse"></div>
                <div className="gm-origin-outer"></div>
                <div className="gm-origin-inner"></div>
              </div>
            </Marker>
          )}

          {/* Destination marker (Google Maps red pin) */}
          {points.end && (
            <Marker longitude={points.end[1]} latitude={points.end[0]} anchor="bottom">
              <svg className="gm-dest-pin" width="28" height="40" viewBox="0 0 28 40" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M14 0C6.268 0 0 6.268 0 14c0 10.5 14 26 14 26s14-15.5 14-26C28 6.268 21.732 0 14 0z" fill="#EA4335"/>
                <circle cx="14" cy="14" r="6" fill="#B31412"/>
                <circle cx="14" cy="14" r="4.5" fill="white"/>
              </svg>
            </Marker>
          )}

          {/* Live navigation arrow (Google Maps style) */}
          {navPosition && (
            <Marker longitude={navPosition[1]} latitude={navPosition[0]} anchor="center">
              {isActivelyNavigating ? (
                <div className="nav-arrow-container">
                  <div className="nav-arrow-pulse"></div>
                  <svg
                    className="nav-arrow"
                    width="40" height="40" viewBox="0 0 40 40"
                    style={{ transform: `rotate(${navBearing - (viewState.bearing || 0)}deg)` }}
                  >
                    <defs>
                      <filter id="arrow-shadow" x="-30%" y="-30%" width="160%" height="160%">
                        <feDropShadow dx="0" dy="1" stdDeviation="2" floodOpacity="0.4"/>
                      </filter>
                    </defs>
                    <polygon
                      points="20,4 32,32 20,26 8,32"
                      fill="#4285F4"
                      stroke="white"
                      strokeWidth="2.5"
                      strokeLinejoin="round"
                      filter="url(#arrow-shadow)"
                    />
                  </svg>
                </div>
              ) : (
                <div className="nav-marker"></div>
              )}
            </Marker>
          )}
        </Map>

        {/* Map Overlay */}
        <div className={`map-overlay${isMobile && sheetPosition === 'collapsed' && (!points.start || !points.end) ? ' map-overlay-prominent' : ''}`}>
          {!points.start && <span>Tap map to set origin</span>}
          {points.start && !points.end && <span>Tap map to set destination</span>}
          {points.start && points.end && !routeData && <span>Ready — tap Calculate</span>}
          {routeData && <span>Routes displayed</span>}
        </div>

        {/* Legend */}
        <div className="map-legend">
          {showHeatmap && (
            <div className="legend-section">
              <span className="legend-title">Risk Level @ {getTimeLabel(hour)}</span>
              <div className="risk-gradient"></div>
              <div className="risk-labels">
                <span>Low</span>
                <span>High</span>
              </div>
            </div>
          )}
          {routeData && (
            <div className="legend-section">
              <span className="legend-title">Routes</span>
              <div className="legend-item">
                <div className="legend-line amber"></div>
                <span>Fastest</span>
              </div>
              <div className="legend-item">
                <div className="legend-line green"></div>
                <span>Safest</span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Mobile: compact nav bar at top during active navigation */}
      {isMobile && (
        <MobileNavBar
          show={showNav && isActivelyNavigating}
          instruction={navInstructions[navCurrentStep] || null}
          nextInstruction={navInstructions[navCurrentStep + 1] || null}
          progress={navInstructions.length > 1 ? Math.round((navCurrentStep / (navInstructions.length - 1)) * 100) : 0}
          onTap={() => setSheetPosition('expanded')}
        />
      )}

      {/* Mobile: draggable bottom sheet */}
      {isMobile && (
        <MobileBottomSheet
          position={sheetPosition}
          onPositionChange={setSheetPosition}
        >
          <div className="sidebar mobile-sheet-sidebar">
            {sidebarContent}
          </div>
        </MobileBottomSheet>
      )}
    </div>
  );
}
