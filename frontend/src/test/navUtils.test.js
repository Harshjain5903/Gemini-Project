import { describe, it, expect } from 'vitest';
import { getBearing, getDistance, getTurnDirection, generateInstructions, interpolate } from '../navUtils';

describe('getDistance (Haversine)', () => {
  it('returns 0 for the same point', () => {
    const p = [40.7580, -73.9855];
    expect(getDistance(p, p)).toBe(0);
  });

  it('calculates Times Square to Penn Station (~900m)', () => {
    const timesSquare = [40.7580, -73.9855];
    const pennStation = [40.7505, -73.9934];
    const dist = getDistance(timesSquare, pennStation);
    expect(dist).toBeGreaterThan(800);
    expect(dist).toBeLessThan(1100);
  });

  it('calculates Central Park to Wall Street (~8km)', () => {
    const centralPark = [40.7829, -73.9654];
    const wallStreet = [40.7074, -74.0113];
    const dist = getDistance(centralPark, wallStreet);
    expect(dist).toBeGreaterThan(7000);
    expect(dist).toBeLessThan(10000);
  });

  it('is symmetric (A→B = B→A)', () => {
    const a = [40.7580, -73.9855];
    const b = [40.7505, -73.9934];
    expect(getDistance(a, b)).toBeCloseTo(getDistance(b, a), 5);
  });
});

describe('getBearing', () => {
  it('returns ~0° (north) when going due north', () => {
    const from = [40.750, -73.990];
    const to = [40.760, -73.990];
    const bearing = getBearing(from, to);
    expect(bearing).toBeCloseTo(0, 0);
  });

  it('returns ~90° (east) when going due east', () => {
    const from = [40.750, -73.990];
    const to = [40.750, -73.980];
    const bearing = getBearing(from, to);
    expect(bearing).toBeGreaterThan(80);
    expect(bearing).toBeLessThan(100);
  });

  it('returns ~180° (south) when going due south', () => {
    const from = [40.760, -73.990];
    const to = [40.750, -73.990];
    const bearing = getBearing(from, to);
    expect(bearing).toBeCloseTo(180, 0);
  });

  it('returns ~270° (west) when going due west', () => {
    const from = [40.750, -73.980];
    const to = [40.750, -73.990];
    const bearing = getBearing(from, to);
    expect(bearing).toBeGreaterThan(260);
    expect(bearing).toBeLessThan(280);
  });
});

describe('getTurnDirection', () => {
  it('detects straight (small angle change)', () => {
    expect(getTurnDirection(0, 5).type).toBe('straight');
    expect(getTurnDirection(350, 355).type).toBe('straight');
  });

  it('detects right turn (90°)', () => {
    expect(getTurnDirection(0, 90).type).toBe('right');
  });

  it('detects left turn (-90°)', () => {
    expect(getTurnDirection(90, 0).type).toBe('left');
  });

  it('detects slight right (30°)', () => {
    expect(getTurnDirection(0, 30).type).toBe('slight-right');
  });

  it('detects slight left (-30°)', () => {
    expect(getTurnDirection(30, 0).type).toBe('slight-left');
  });

  it('detects sharp right (150°)', () => {
    expect(getTurnDirection(0, 150).type).toBe('sharp-right');
  });

  it('handles wrap-around at 360°', () => {
    const result = getTurnDirection(355, 5);
    expect(result.type).toBe('straight');
  });
});

describe('generateInstructions', () => {
  it('returns empty array for null/empty coords', () => {
    expect(generateInstructions(null)).toEqual([]);
    expect(generateInstructions([])).toEqual([]);
    expect(generateInstructions([[40.75, -73.99]])).toEqual([]);
  });

  it('generates start and end instructions for 2-point route', () => {
    const coords = [[40.758, -73.985], [40.750, -73.993]];
    const instrs = generateInstructions(coords);
    expect(instrs.length).toBe(2);
    expect(instrs[0].label).toBe('Start navigation');
    expect(instrs[instrs.length - 1].label).toBe('Arrive at destination');
  });

  it('generates turn instructions for a route with turns', () => {
    // Go north, then east (right turn)
    const coords = [
      [40.750, -73.990],
      [40.755, -73.990],
      [40.755, -73.980],
      [40.755, -73.970]
    ];
    const instrs = generateInstructions(coords);
    expect(instrs.length).toBeGreaterThanOrEqual(3); // start + at least 1 turn + end
    expect(instrs[0].icon).toBe('🏁');
    expect(instrs[instrs.length - 1].icon).toBe('📍');
  });

  it('includes distance on each instruction', () => {
    const coords = [[40.758, -73.985], [40.750, -73.993]];
    const instrs = generateInstructions(coords);
    const lastInstr = instrs[instrs.length - 1];
    expect(lastInstr.distance).toBeGreaterThan(0);
  });
});

describe('interpolate', () => {
  it('returns start point at fraction=0', () => {
    const from = [40.750, -73.990];
    const to = [40.760, -73.980];
    const result = interpolate(from, to, 0);
    expect(result[0]).toBe(40.750);
    expect(result[1]).toBe(-73.990);
  });

  it('returns end point at fraction=1', () => {
    const from = [40.750, -73.990];
    const to = [40.760, -73.980];
    const result = interpolate(from, to, 1);
    expect(result[0]).toBe(40.760);
    expect(result[1]).toBe(-73.980);
  });

  it('returns midpoint at fraction=0.5', () => {
    const from = [40.750, -73.990];
    const to = [40.760, -73.980];
    const result = interpolate(from, to, 0.5);
    expect(result[0]).toBeCloseTo(40.755, 5);
    expect(result[1]).toBeCloseTo(-73.985, 5);
  });
});
