"""
Tests for the risk blending logic used in routing.
Tests the _get_blended_risk calculation without loading the full road network.
"""
import pytest


# --- Replicate the blending logic for unit testing ---
# (Avoids loading OSMnx graph which takes minutes)

MODE_WEIGHTS = {
    "walking":  {"crash": 0.3, "crime": 0.7},
    "driving":  {"crash": 0.9, "crime": 0.1},
    "cycling":  {"crash": 0.5, "crime": 0.5},
}


def get_blended_risk(cell_info, time_key, travel_mode="walking", has_crime_data=True):
    """Mirror of RoutingEngine._get_blended_risk for testing"""
    weights = MODE_WEIGHTS.get(travel_mode, MODE_WEIGHTS["walking"])

    crash_base = cell_info.get("base_risk", 0)
    crash_mod = cell_info.get("time_modifiers", {}).get(time_key, 1.0)
    crash_risk = crash_base * crash_mod

    crime_base = cell_info.get("crime_risk", 0)
    crime_mod = cell_info.get("crime_time_modifiers", {}).get(time_key, 1.0)
    crime_risk = crime_base * crime_mod

    if crime_base == 0 and not has_crime_data:
        return crash_risk

    return (weights["crash"] * crash_risk) + (weights["crime"] * crime_risk)


# --- Test Data ---

SAMPLE_CELL = {
    "base_risk": 60.0,       # crash risk
    "crime_risk": 40.0,      # crime risk
    "time_modifiers": {
        "night_weekday": 1.5,
        "midday_weekday": 0.8,
    },
    "crime_time_modifiers": {
        "night_weekday": 2.0,
        "midday_weekday": 0.5,
    },
}

CRASH_ONLY_CELL = {
    "base_risk": 50.0,
    "crime_risk": 0,
    "time_modifiers": {"night_weekday": 1.3},
    "crime_time_modifiers": {},
}


class TestRiskBlending:
    """Test travel-mode-aware risk blending"""

    def test_walking_weights_crime_higher(self):
        """Walking should weight crime 70% and crash 30%"""
        risk = get_blended_risk(SAMPLE_CELL, "midday_weekday", "walking")
        # crash: 60 * 0.8 = 48, crime: 40 * 0.5 = 20
        # blended: 0.3 * 48 + 0.7 * 20 = 14.4 + 14.0 = 28.4
        assert risk == pytest.approx(28.4, abs=0.1)

    def test_driving_weights_crash_higher(self):
        """Driving should weight crash 90% and crime 10%"""
        risk = get_blended_risk(SAMPLE_CELL, "midday_weekday", "driving")
        # crash: 60 * 0.8 = 48, crime: 40 * 0.5 = 20
        # blended: 0.9 * 48 + 0.1 * 20 = 43.2 + 2.0 = 45.2
        assert risk == pytest.approx(45.2, abs=0.1)

    def test_cycling_weights_equal(self):
        """Cycling should weight crash and crime equally (50/50)"""
        risk = get_blended_risk(SAMPLE_CELL, "midday_weekday", "cycling")
        # crash: 48, crime: 20 → 0.5*48 + 0.5*20 = 34
        assert risk == pytest.approx(34.0, abs=0.1)

    def test_night_multiplier_increases_risk(self):
        """Night time should increase both crash and crime risk"""
        day_risk = get_blended_risk(SAMPLE_CELL, "midday_weekday", "walking")
        night_risk = get_blended_risk(SAMPLE_CELL, "night_weekday", "walking")
        assert night_risk > day_risk

    def test_night_walking_risk(self):
        """Verify exact night walking calculation"""
        risk = get_blended_risk(SAMPLE_CELL, "night_weekday", "walking")
        # crash: 60 * 1.5 = 90, crime: 40 * 2.0 = 80
        # blended: 0.3 * 90 + 0.7 * 80 = 27 + 56 = 83
        assert risk == pytest.approx(83.0, abs=0.1)

    def test_driving_night_vs_walking_night(self):
        """At night, walking risk should differ from driving risk"""
        walk = get_blended_risk(SAMPLE_CELL, "night_weekday", "walking")
        drive = get_blended_risk(SAMPLE_CELL, "night_weekday", "driving")
        # Walking: 0.3*90 + 0.7*80 = 83
        # Driving: 0.9*90 + 0.1*80 = 81 + 8 = 89
        assert walk != drive
        assert drive == pytest.approx(89.0, abs=0.1)

    def test_unknown_time_uses_modifier_1(self):
        """Missing time key should default to modifier 1.0"""
        risk = get_blended_risk(SAMPLE_CELL, "unknown_period", "walking")
        # crash: 60 * 1.0 = 60, crime: 40 * 1.0 = 40
        # blended: 0.3 * 60 + 0.7 * 40 = 18 + 28 = 46
        assert risk == pytest.approx(46.0, abs=0.1)

    def test_unknown_travel_mode_defaults_to_walking(self):
        """Unknown travel mode should fall back to walking weights"""
        walking_risk = get_blended_risk(SAMPLE_CELL, "midday_weekday", "walking")
        unknown_risk = get_blended_risk(SAMPLE_CELL, "midday_weekday", "skateboarding")
        assert walking_risk == unknown_risk

    def test_zero_risk_cell(self):
        """Zero risk cell should return 0"""
        zero_cell = {"base_risk": 0, "crime_risk": 0}
        risk = get_blended_risk(zero_cell, "midday_weekday", "walking")
        assert risk == 0

    def test_crash_only_fallback_without_crime_data(self):
        """When has_crime_data=False and crime_risk=0, use crash-only"""
        risk = get_blended_risk(CRASH_ONLY_CELL, "night_weekday", "walking", has_crime_data=False)
        # Should return crash-only: 50 * 1.3 = 65
        assert risk == pytest.approx(65.0, abs=0.1)

    def test_crash_only_blended_with_crime_data(self):
        """When has_crime_data=True, still blend even if crime_risk=0"""
        risk = get_blended_risk(CRASH_ONLY_CELL, "night_weekday", "walking", has_crime_data=True)
        # Should blend: 0.3 * (50*1.3) + 0.7 * 0 = 19.5
        assert risk == pytest.approx(19.5, abs=0.1)


class TestCrimeWeights:
    """Test that crime severity weights make sense"""

    CRIME_WEIGHTS = {
        "MURDER & NON-NEGL. MANSLAUGHTER": 10,
        "RAPE": 8,
        "ROBBERY": 5,
        "FELONY ASSAULT": 4,
        "ASSAULT 3 & RELATED OFFENSES": 2,
        "DANGEROUS WEAPONS": 3,
        "SEX CRIMES": 3,
        "GRAND LARCENY": 1,
        "PETIT LARCENY": 1,
        "GRAND LARCENY OF MOTOR VEHICLE": 1,
    }

    def test_murder_is_highest_weight(self):
        """Murder should have the highest severity weight"""
        max_crime = max(self.CRIME_WEIGHTS, key=self.CRIME_WEIGHTS.get)
        assert max_crime == "MURDER & NON-NEGL. MANSLAUGHTER"

    def test_larceny_is_lowest_weight(self):
        """Larceny should have the lowest severity weight"""
        min_weight = min(self.CRIME_WEIGHTS.values())
        assert min_weight == 1

    def test_violent_crimes_heavier_than_property(self):
        """Violent crimes should be weighted higher than property crimes"""
        assert self.CRIME_WEIGHTS["ROBBERY"] > self.CRIME_WEIGHTS["GRAND LARCENY"]
        assert self.CRIME_WEIGHTS["FELONY ASSAULT"] > self.CRIME_WEIGHTS["PETIT LARCENY"]

    def test_felony_boost(self):
        """Felony assault with 1.5x boost should exceed base weight"""
        base = self.CRIME_WEIGHTS["FELONY ASSAULT"]
        boosted = base * 1.5
        assert boosted == 6.0


class TestModeWeights:
    """Test that mode weight configurations are valid"""

    def test_all_modes_sum_to_one(self):
        """Crash + crime weights should sum to 1.0 for each mode"""
        for mode, weights in MODE_WEIGHTS.items():
            total = weights["crash"] + weights["crime"]
            assert total == pytest.approx(1.0), f"{mode} weights sum to {total}"

    def test_walking_prioritizes_crime(self):
        assert MODE_WEIGHTS["walking"]["crime"] > MODE_WEIGHTS["walking"]["crash"]

    def test_driving_prioritizes_crash(self):
        assert MODE_WEIGHTS["driving"]["crash"] > MODE_WEIGHTS["driving"]["crime"]

    def test_cycling_is_balanced(self):
        assert MODE_WEIGHTS["cycling"]["crash"] == MODE_WEIGHTS["cycling"]["crime"]
