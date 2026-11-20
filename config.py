"""
config.py - every setting of the pipeline in one place.

Why one file: if a threshold or the time window changes, you edit it here and
every script picks it up. Each value has a comment explaining WHY it has that value.
The reasoning behind the bigger choices is in docs/decisions.md.
"""

import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

# Read .env (bucket name, AWS profile, region) into environment variables.
# .env is gitignored; .env.example shows which keys are expected.
load_dotenv()

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
S3_BUCKET = os.getenv("S3_BUCKET")          # e.g. "minigrid-meter-monitor-data"
AWS_PROFILE = os.getenv("AWS_PROFILE")      # named profile in ~/.aws/credentials
AWS_REGION = os.getenv("AWS_REGION", "eu-west-1")

SOURCE_DIR = Path("data/source/renewvia")   # Zenodo files as downloaded (never modified)
LOCAL_ROOT = Path("data")                   # with --local, raw/ and processed/ live here
RAW_PREFIX = "raw"                          # same folder names locally and on S3
PROCESSED_PREFIX = "processed"

# ---------------------------------------------------------------------------
# Which data we use
# ---------------------------------------------------------------------------
# Site name as written in the metadata tables -> part of the readings file name.
SITES = {
    "Lomekwi": "Lomekwi",            # Kenya
    "Lorengelup": "Lorengelup",      # Kenya
    "Ekong Anaku": "Ekong_Anaku",    # Nigeria
}

# 6 months with stable tariffs and mostly observed data at the Kenyan sites.
# End is EXCLUSIVE: readings with slot_start < WINDOW_END are kept.
WINDOW_START = pd.Timestamp("2024-01-01")   # UTC, like the readings
WINDOW_END = pd.Timestamp("2024-07-01")

METERS_PER_SITE = 50   # 3 sites x 50 = 150 meters, as in the original brief
RANDOM_SEED = 42       # fixed seed: the same 150 meters are picked on every run

# ---------------------------------------------------------------------------
# Data shape
# ---------------------------------------------------------------------------
INTERVAL_MINUTES = 15          # the dataset's reading interval
SLOTS_PER_HOUR = 60 // INTERVAL_MINUTES
SLOTS_PER_DAY = 24 * SLOTS_PER_HOUR

# Only this imputation label counts as real data. "profile" and "uniform" are
# estimates the dataset authors used to fill communication gaps.
OBSERVED_LABEL = "observed"

# A meter below this share of observed intervals is reported as low quality.
COMPLETENESS_TARGET = 0.90

# ---------------------------------------------------------------------------
# Anomaly rule thresholds (from the original brief)
# ---------------------------------------------------------------------------
GAP_MIN_HOURS = 6              # 6 h without observed data: longer than normal network hiccups
ZERO_RUN_MIN_DAYS = 3          # 3 full days at zero: household not using power / no credit
SPIKE_FACTOR = 5               # reading > 5x its recent typical level
SPIKE_BASELINE_DAYS = 7        # "recent" = the previous 7 days (covers a full weekly cycle)
SPIKE_MIN_BASELINE_KWH = None  # set in the anomalies step after looking at real values
FLATLINE_MIN_HOURS = 12        # a real household's use changes within 12 h; a stuck value doesn't

# ---------------------------------------------------------------------------
# Severity cut-offs (approved 2026-11-20). Each list is
# [(upper limit, severity), ...]; the first limit the value is AT OR BELOW wins,
# so limits are inclusive: a 72 h gap is "medium", a 72.25 h gap is "high".
# float("inf") means "no upper limit".
# ---------------------------------------------------------------------------
GAP_SEVERITY_HOURS = [(24, "low"), (72, "medium"), (float("inf"), "high")]
ZERO_RUN_SEVERITY_DAYS = [(7, "medium"), (float("inf"), "high")]
SPIKE_SEVERITY_RATIO = [(10, "low"), (20, "medium"), (float("inf"), "high")]
FLATLINE_SEVERITY_HOURS = [(24, "low"), (48, "medium"), (float("inf"), "high")]
NEGATIVE_SEVERITY = "high"     # a consumption meter should never go below zero


def hours_to_slots(hours: float) -> int:
    """Convert a duration in hours to a number of 15-minute slots (6 h -> 24)."""
    return int(hours * SLOTS_PER_HOUR)


def severity_for(value: float, cutoffs: list[tuple[float, str]]) -> str:
    """Return the severity label for `value` using a cut-off list like GAP_SEVERITY_HOURS.

    Limits are inclusive. Example: severity_for(30, GAP_SEVERITY_HOURS) -> "medium"
    (30 h is above 24 and at or below 72).
    """
    for upper_limit, label in cutoffs:
        if value <= upper_limit:
            return label
    return cutoffs[-1][1]
