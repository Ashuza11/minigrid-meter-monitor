"""FLATLINE: the same non-zero value for 12 h (48 slots) or more."""

import pandas as pd

from anomalies import detect_flatlines

NAN = float("nan")


def test_exactly_12_hours_is_flagged(make_meter):
    result = detect_flatlines(make_meter([0.1] + [0.3] * 48 + [0.1]))
    assert len(result) == 1
    assert result.iloc[0]["severity"] == "low"
    assert result.iloc[0]["end"] - result.iloc[0]["start"] == pd.Timedelta(hours=12)


def test_just_under_12_hours_is_not_flagged(make_meter):
    assert detect_flatlines(make_meter([0.1] + [0.3] * 47 + [0.1])).empty


def test_severity_bands(make_meter):
    for n_slots, expected in [(96, "low"), (97, "medium"), (193, "high")]:
        result = detect_flatlines(make_meter([0.3] * n_slots))
        assert result.iloc[0]["severity"] == expected, n_slots


def test_zeros_are_not_a_flatline(make_meter):
    # A long run of 0 is ZERO_RUN's job, not FLATLINE's.
    assert detect_flatlines(make_meter([0.0] * 100)).empty


def test_missing_reading_breaks_the_run(make_meter):
    assert detect_flatlines(make_meter([0.3] * 30 + [NAN] + [0.3] * 30)).empty


def test_meter_with_no_data(make_meter):
    assert detect_flatlines(make_meter([NAN] * 60)).empty
    assert detect_flatlines(make_meter([])).empty


def test_flatline_across_dst_change(make_meter):
    # Starts before the UK clock change on 2024-10-27 (clocks go back at 02:00).
    meter = make_meter([0.3] * 48, start="2024-10-26 20:00", tz="Europe/London")
    result = detect_flatlines(meter)
    assert len(result) == 1
    assert result.iloc[0]["end"] - result.iloc[0]["start"] == pd.Timedelta(hours=12)
