"""ZERO_RUN: 3 days (288 slots) or more of observed 0 kWh."""

from anomalies import detect_zero_runs

NAN = float("nan")
DAY = 96  # 15-minute slots per day


def test_exactly_3_days_is_flagged(make_meter):
    result = detect_zero_runs(make_meter([0.1] + [0.0] * (3 * DAY) + [0.1]))
    assert len(result) == 1
    assert result.iloc[0]["severity"] == "medium"


def test_just_under_3_days_is_not_flagged(make_meter):
    assert detect_zero_runs(make_meter([0.0] * (3 * DAY - 1))).empty


def test_severity_7_days_medium_above_high(make_meter):
    assert detect_zero_runs(make_meter([0.0] * (7 * DAY))).iloc[0]["severity"] == "medium"
    assert detect_zero_runs(make_meter([0.0] * (7 * DAY + 1))).iloc[0]["severity"] == "high"


def test_missing_reading_breaks_the_run(make_meter):
    # 2 days of zeros, one missing slot, 2 more days: not a proven 3-day run.
    meter = make_meter([0.0] * (2 * DAY) + [NAN] + [0.0] * (2 * DAY))
    assert detect_zero_runs(meter).empty


def test_small_usage_breaks_the_run(make_meter):
    meter = make_meter([0.0] * (2 * DAY) + [0.001] + [0.0] * (2 * DAY))
    assert detect_zero_runs(meter).empty


def test_meter_with_no_data(make_meter):
    assert detect_zero_runs(make_meter([NAN] * (4 * DAY))).empty
    assert detect_zero_runs(make_meter([])).empty


def test_zero_run_across_dst_change(make_meter):
    meter = make_meter([0.0] * (3 * DAY), start="2024-03-30 00:00", tz="Europe/London")
    result = detect_zero_runs(meter)
    assert len(result) == 1
    assert (result.iloc[0]["end"] - result.iloc[0]["start"]).total_seconds() == 3 * 24 * 3600
