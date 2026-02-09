/**
 * Calculate bearing between two [lat, lng] points in degrees (0-360)
 */
export function getBearing(from, to) {
  const toRad = (d) => (d * Math.PI) / 180;
  const toDeg = (r) => (r * 180) / Math.PI;
  const dLng = toRad(to[1] - from[1]);
  const y = Math.sin(dLng) * Math.cos(toRad(to[0]));
  const x =
    Math.cos(toRad(from[0])) * Math.sin(toRad(to[0])) -
    Math.sin(toRad(from[0])) * Math.cos(toRad(to[0])) * Math.cos(dLng);
  return ((toDeg(Math.atan2(y, x)) + 360) % 360);
}

/**
 * Distance between two [lat, lng] points in meters (Haversine)
 */
export function getDistance(from, to) {
  const R = 6371000;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(to[0] - from[0]);
  const dLng = toRad(to[1] - from[1]);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(from[0])) * Math.cos(toRad(to[0])) * Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

/**
 * Get turn direction from bearing change
 */
export function getTurnDirection(prevBearing, nextBearing) {
  let diff = ((nextBearing - prevBearing) + 360) % 360;
  if (diff > 180) diff -= 360;
  if (Math.abs(diff) < 20) return { type: 'straight', icon: '⬆️', label: 'Continue straight' };
  if (diff > 0 && diff < 70) return { type: 'slight-right', icon: '↗️', label: 'Slight right' };
  if (diff >= 70 && diff < 130) return { type: 'right', icon: '➡️', label: 'Turn right' };
  if (diff >= 130) return { type: 'sharp-right', icon: '↪️', label: 'Sharp right' };
  if (diff < 0 && diff > -70) return { type: 'slight-left', icon: '↖️', label: 'Slight left' };
  if (diff <= -70 && diff > -130) return { type: 'left', icon: '⬅️', label: 'Turn left' };
  return { type: 'sharp-left', icon: '↩️', label: 'Sharp left' };
}

/**
 * Generate turn-by-turn instructions from route coordinates
 */
export function generateInstructions(coords) {
  if (!coords || coords.length < 2) return [];
  const instructions = [];

  instructions.push({
    index: 0,
    icon: '🏁',
    label: 'Start navigation',
    distance: 0,
    coord: coords[0]
  });

  let prevBearing = getBearing(coords[0], coords[1]);

  for (let i = 1; i < coords.length - 1; i++) {
    const nextBearing = getBearing(coords[i], coords[i + 1]);
    const turn = getTurnDirection(prevBearing, nextBearing);
    const dist = getDistance(coords[i - 1], coords[i]);

    if (turn.type !== 'straight' || i === 1) {
      instructions.push({
        index: i,
        icon: turn.icon,
        label: turn.label,
        distance: Math.round(dist),
        coord: coords[i]
      });
    }
    prevBearing = nextBearing;
  }

  const lastDist = getDistance(coords[coords.length - 2], coords[coords.length - 1]);
  instructions.push({
    index: coords.length - 1,
    icon: '📍',
    label: 'Arrive at destination',
    distance: Math.round(lastDist),
    coord: coords[coords.length - 1]
  });

  return instructions;
}

/**
 * Interpolate position between two coordinates (0-1 fraction)
 */
export function interpolate(from, to, fraction) {
  return [
    from[0] + (to[0] - from[0]) * fraction,
    from[1] + (to[1] - from[1]) * fraction
  ];
}
