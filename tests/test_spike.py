"""SPIKE: reading > 5x the median of the previous 7 days' non-zero readings."""

import pandas as pd

from anomalies import detect_spikes, spike_baseline

NAN = float("nan")
HISTORY = [0.25] * 30  # 30 non-zero readings (>= 24 needed), typical level 0.25 kWh


def test_just_above_5x_is_flagged(make_meter):
    result = detect_spikes(make_meter(HISTORY + [1.26]))
    assert len(result) == 1
    assert result.iloc[0]["severity"] == "low"


def test_exactly_5x_is_not_flagged(make_meter):
    # The rule is "more than 5x": 0.25 * 5 = 1.25 exactly is not a spike.
    assert detect_spikes(make_meter(HISTORY + [1.25])).empty


def test_severity_bands(make_meter):
    for value, expected in [(2.5, "low"), (5.0, "medium"), (5.25, "high")]:
        result = detect_spikes(make_meter(HISTORY + [value]))
        assert result.iloc[0]["severity"] == expected, value


def test_zeros_do_not_drag_the_baseline_down(make_meter):
    # Mostly zeros plus 30 readings of 0.25: the plain median would be 0,
    # but the in-use median is 0.25, so 1.0 (4x) is NOT a spike.
    meter = make_meter([0.0] * 200 + HISTORY + [1.0])
    assert detect_spikes(meter).empty


def test_not_enough_history_means_no_spike(make_meter):
    # Only 10 non-zero readings before: baseline unknown, so we don't guess.
    assert detect_spikes(make_meter([0.25] * 10 + [100.0])).empty


def test_minimum_baseline_floor(make_meter):
    # Typical level 0.0001 kWh is below the 0.001 floor, so the floor is used:
    # 0.004 kWh is 40x the raw median but only 4x the floor -> not a spike.
    assert detect_spikes(make_meter([0.0001] * 30 + [0.004])).empty


def test_baseline_excludes_the_current_slot(make_meter):
    meter = make_meter(HISTORY + [100.0])
    assert spike_baseline(meter).iloc[-1] == 0.25


def test_consecutive_spikes_are_one_event(make_meter):
    result = detect_spikes(make_meter(HISTORY + [2.0, 3.0, 2.0] + [0.25]))
    assert len(result) == 1
    assert result.iloc[0]["end"] - result.iloc[0]["start"] == pd.Timedelta(minutes=45)


def test_baseline_only_looks_back_7_days(make_meter):
    # 30 readings at 0.25, then 8 days of silence: the old readings fall out
    # of the 7-day window, so there is no baseline and no spike.
    meter = make_meter(HISTORY + [NAN] * (8 * 96) + [5.0])
    assert detect_spikes(meter).empty


def test_meter_with_no_data(make_meter):
    assert detect_spikes(make_meter([NAN] * 50)).empty
    assert detect_spikes(make_meter([])).empty


def test_spike_after_dst_change(make_meter):
    meter = make_meter(HISTORY + [2.0], start="2024-03-31 00:00", tz="Europe/London")
    assert len(detect_spikes(meter)) == 1
