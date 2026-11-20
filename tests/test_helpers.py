"""Tests for shared helpers: run finding and severity mapping."""

import pandas as pd

import config
from anomalies import find_runs


def test_find_runs_basic():
    assert find_runs(pd.Series([False, True, True, False, True])) == [(1, 2), (4, 4)]


def test_find_runs_none_and_all():
    assert find_runs(pd.Series([False, False])) == []
    assert find_runs(pd.Series([True, True, True])) == [(0, 2)]


def test_find_runs_empty_series():
    assert find_runs(pd.Series([], dtype=bool)) == []


def test_severity_limits_are_inclusive():
    assert config.severity_for(24, config.GAP_SEVERITY_HOURS) == "low"
    assert config.severity_for(24.25, config.GAP_SEVERITY_HOURS) == "medium"
    assert config.severity_for(72, config.GAP_SEVERITY_HOURS) == "medium"
    assert config.severity_for(72.25, config.GAP_SEVERITY_HOURS) == "high"


def test_hours_to_slots():
    assert config.hours_to_slots(6) == 24
    assert config.hours_to_slots(12) == 48
