import React, { useState, useRef, useEffect, useCallback } from 'react';
import './LocationSearch.css';

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN;
// Chicago center for proximity bias
const PROXIMITY = '-87.6298,41.8781';

export default function LocationSearch({ placeholder, value, onSelect, onClear, onFocus }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef(null);
  const dropdownRef = useRef(null);
  const debounceRef = useRef(null);

  // Sync display value when coords set externally (e.g. map tap or GPS)
  useEffect(() => {
    if (value) {
      setQuery(`${value[0].toFixed(4)}, ${value[1].toFixed(4)}`);
    } else {
      setQuery('');
    }
  }, [value]);

  // Reverse geocode when coords set externally to show place name
  useEffect(() => {
    if (!value || !MAPBOX_TOKEN) return;
    const [lat, lng] = value;
    fetch(`https://api.mapbox.com/geocoding/v5/mapbox.places/${lng},${lat}.json?access_token=${MAPBOX_TOKEN}&limit=1`)
      .then(r => r.json())
      .then(data => {
        if (data.features?.length) {
          setQuery(data.features[0].place_name.replace(/, United States$/, ''));
        }
      })
      .catch(() => {});
  }, [value]);

  const searchPlaces = useCallback((text) => {
    if (!text || text.length < 2 || !MAPBOX_TOKEN) {
      setResults([]);
      return;
    }
    setLoading(true);
    fetch(
      `https://api.mapbox.com/geocoding/v5/mapbox.places/${encodeURIComponent(text)}.json?access_token=${MAPBOX_TOKEN}&proximity=${PROXIMITY}&limit=5&country=us&types=address,poi,neighborhood,place`
    )
      .then(r => r.json())
      .then(data => {
        setResults(data.features || []);
        setShowDropdown(true);
        setLoading(false);
      })
      .catch(() => {
        setLoading(false);
      });
  }, []);

  const handleInput = (e) => {
    const text = e.target.value;
    setQuery(text);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => searchPlaces(text), 300);
  };

  const handleSelect = (feature) => {
    const [lng, lat] = feature.center;
    setQuery(feature.place_name.replace(/, United States$/, ''));
    setResults([]);
    setShowDropdown(false);
    onSelect([lat, lng]);
  };

  const handleClear = () => {
    setQuery('');
    setResults([]);
    setShowDropdown(false);
    onClear();
    inputRef.current?.focus();
  };

  const handleFocus = () => {
    if (results.length) setShowDropdown(true);
    onFocus?.();
  };

  // Close dropdown on outside click
  useEffect(() => {
    const handleClick = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target) &&
          inputRef.current && !inputRef.current.contains(e.target)) {
        setShowDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClick);
    document.addEventListener('touchstart', handleClick);
    return () => {
      document.removeEventListener('mousedown', handleClick);
      document.removeEventListener('touchstart', handleClick);
    };
  }, []);

  return (
    <div className="location-search">
      <input
        ref={inputRef}
        className="location-search-input"
        type="text"
        placeholder={placeholder}
        value={query}
        onChange={handleInput}
        onFocus={handleFocus}
        autoComplete="off"
      />
      {query && (
        <button className="location-search-clear" onClick={handleClear} type="button">
          &times;
        </button>
      )}
      {loading && <div className="location-search-spinner" />}
      {showDropdown && results.length > 0 && (
        <div className="location-search-dropdown" ref={dropdownRef}>
          {results.map((f) => (
            <button
              key={f.id}
              className="location-search-result"
              onClick={() => handleSelect(f)}
              type="button"
            >
              <span className="lsr-icon">📍</span>
              <div className="lsr-text">
                <span className="lsr-name">{f.text}</span>
                <span className="lsr-address">{f.place_name.replace(/, United States$/, '')}</span>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
