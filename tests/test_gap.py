"""GAP: 6 h (24 slots) or more without an observed reading."""

import pandas as pd

from anomalies import detect_gaps

NAN = float("nan")


def test_exactly_6_hours_is_flagged(make_meter):
    meter = make_meter([0.1] * 4 + [NAN] * 24 + [0.1] * 4)
    result = detect_gaps(meter)
    assert len(result) == 1
    assert result.iloc[0]["severity"] == "low"
    assert result.iloc[0]["end"] - result.iloc[0]["start"] == pd.Timedelta(hours=6)


def test_just_under_6_hours_is_not_flagged(make_meter):
    meter = make_meter([0.1] * 4 + [NAN] * 23 + [0.1] * 4)
    assert detect_gaps(meter).empty


def test_severity_bands(make_meter):
    # 24 h exactly -> low; 24 h 15 min -> medium; 72 h 15 min -> high
    for n_slots, expected in [(96, "low"), (97, "medium"), (289, "high")]:
        result = detect_gaps(make_meter([0.1] + [NAN] * n_slots + [0.1]))
        assert result.iloc[0]["severity"] == expected, n_slots


def test_zero_is_a_reading_not_a_gap(make_meter):
    # 0 kWh is a real reading: it must never be counted as missing.
    assert detect_gaps(make_meter([0.0] * 40)).empty


def test_two_separate_gaps(make_meter):
    meter = make_meter([NAN] * 30 + [0.1] + [NAN] * 30)
    assert len(detect_gaps(meter)) == 2


def test_meter_with_no_observed_data_is_one_big_gap(make_meter):
    result = detect_gaps(make_meter([NAN] * 96))
    assert len(result) == 1
    assert result.iloc[0]["severity"] == "low"  # exactly 24 h


def test_empty_meter_returns_empty_table(make_meter):
    result = detect_gaps(make_meter([]))
    assert result.empty
    assert list(result.columns) == ["rule", "start", "end", "severity", "details"]


def test_gap_across_dst_change_uses_real_elapsed_time(make_meter):
    # UK clocks jumped from 01:00 to 02:00 on 2024-03-31. A gap of 24 slots
    # around that moment is 6 real hours, even though the wall clock moves 7 h.
    meter = make_meter([0.1] * 4 + [NAN] * 24 + [0.1] * 4, start="2024-03-30 23:00", tz="Europe/London")
    result = detect_gaps(meter)
    assert len(result) == 1
    assert result.iloc[0]["end"] - result.iloc[0]["start"] == pd.Timedelta(hours=6)
    assert result.iloc[0]["details"].startswith("6.0 h")
