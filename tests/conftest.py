"""
Shared helpers for the tests.

pytest loads conftest.py automatically, so every test file can use
`make_meter` without importing it.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

# Let tests `import anomalies` / `import config` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _make_meter(values, start="2024-03-01 00:00", tz="UTC"):
    """Build one meter's readings: one 15-minute slot per value.

    `values` can contain None / float("nan") for "no observed reading".
    """
    slots = pd.date_range(start, periods=len(values), freq="15min", tz=tz)
    return pd.DataFrame({"slot_start": slots, "energy_kwh_observed": pd.Series(values, dtype="float64")})


@pytest.fixture
def make_meter():
    """Fixture that hands the builder to a test: def test_x(make_meter): ..."""
    return _make_meter
