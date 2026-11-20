"""NEGATIVE: any observed reading below 0 kWh."""

from anomalies import detect_negatives

NAN = float("nan")


def test_negative_value_is_flagged_high(make_meter):
    result = detect_negatives(make_meter([0.1, -0.01, 0.1]))
    assert len(result) == 1
    assert result.iloc[0]["severity"] == "high"
    assert "-0.0100" in result.iloc[0]["details"]


def test_zero_is_not_negative(make_meter):
    assert detect_negatives(make_meter([0.0, 0.0, 0.1])).empty


def test_consecutive_negatives_are_one_event(make_meter):
    assert len(detect_negatives(make_meter([-0.1, -0.2, 0.1, -0.1]))) == 2


def test_missing_values_are_ignored(make_meter):
    assert detect_negatives(make_meter([NAN, NAN])).empty
    assert detect_negatives(make_meter([])).empty


def test_negative_across_dst_change(make_meter):
    meter = make_meter([0.1, -0.5, 0.1], start="2024-10-27 00:45", tz="Europe/London")
    assert len(detect_negatives(meter)) == 1
