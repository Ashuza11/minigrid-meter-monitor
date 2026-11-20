"""
anomalies.py - rule-based anomaly detection on the cleaned 15-minute readings.

Each rule is a small PURE function: it takes ONE meter's readings and returns a
table of anomalies. Pure means no files, no globals changed, same input -> same
output, which is what makes every rule easy to unit-test (see tests/).

Input expected by every rule: a DataFrame for one meter, sorted by time, with
  slot_start           timezone-aware timestamp of each 15-minute slot
  energy_kwh_observed  the meter's real reading, or empty (NaN) if not observed
Rules only look at OBSERVED values: estimates made by the dataset are ignored.

Output of every rule: columns rule, start, end, severity, details
(start = beginning of the first slot, end = end of the last slot).

Run:  uv run python anomalies.py --local
"""

import argparse
import logging

import pandas as pd

import config
import storage

log = logging.getLogger(__name__)

COLUMNS = ["rule", "start", "end", "severity", "details"]
SLOT = pd.Timedelta(minutes=config.INTERVAL_MINUTES)


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------
def find_runs(mask: pd.Series) -> list[tuple[int, int]]:
    """Return (first_position, last_position) of every stretch of consecutive True values.

    Example: [F, T, T, F, T] -> [(1, 2), (4, 4)]
    Trick: a new stretch starts wherever the value changes, so a running count
    of changes gives every stretch its own number.
    """
    mask = mask.reset_index(drop=True).astype(bool)
    if not mask.any():
        return []
    stretch_id = (mask != mask.shift()).cumsum()
    positions = mask[mask].index.to_series().groupby(stretch_id[mask])
    return list(zip(positions.min(), positions.max()))


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS)


# ---------------------------------------------------------------------------
# The five rules
# ---------------------------------------------------------------------------
def detect_gaps(meter: pd.DataFrame) -> pd.DataFrame:
    """GAP: the meter sent no real reading for 6 hours or more in a row.

    Why it matters: while a meter is silent, the company can't see usage,
    enforce prepaid credit, or spot faults. 6 hours is longer than the normal
    short communication hiccups, so it points to a real problem (meter offline,
    network down, customer disconnected).
    Severity by length: up to 24 h low, up to 72 h medium, longer high.
    """
    missing = meter["energy_kwh_observed"].isna()
    rows = []
    for first, last in find_runs(missing):
        n_slots = last - first + 1
        if n_slots < config.hours_to_slots(config.GAP_MIN_HOURS):
            continue
        hours = n_slots / config.SLOTS_PER_HOUR
        rows.append({
            "rule": "GAP",
            "start": meter["slot_start"].iloc[first],
            "end": meter["slot_start"].iloc[last] + SLOT,
            "severity": config.severity_for(hours, config.GAP_SEVERITY_HOURS),
            "details": f"{hours:.1f} h without an observed reading",
        })
    return pd.DataFrame(rows, columns=COLUMNS) if rows else _empty()


def detect_zero_runs(meter: pd.DataFrame) -> pd.DataFrame:
    """ZERO_RUN: the meter reported exactly 0 kWh for 3 days or more in a row.

    Why it matters: on a prepaid mini-grid this usually means the customer has
    run out of credit or stopped using power (lost revenue), less often a
    broken meter or a disconnected supply. Only readings the meter actually
    reported as 0 count; a missing reading breaks the run (it is not a proven zero).
    Severity: up to 7 days medium, longer high.
    """
    is_zero = meter["energy_kwh_observed"].eq(0)  # NaN -> False
    rows = []
    for first, last in find_runs(is_zero):
        n_slots = last - first + 1
        if n_slots < config.ZERO_RUN_MIN_DAYS * config.SLOTS_PER_DAY:
            continue
        days = n_slots / config.SLOTS_PER_DAY
        rows.append({
            "rule": "ZERO_RUN",
            "start": meter["slot_start"].iloc[first],
            "end": meter["slot_start"].iloc[last] + SLOT,
            "severity": config.severity_for(days, config.ZERO_RUN_SEVERITY_DAYS),
            "details": f"{days:.1f} days at 0 kWh",
        })
    return pd.DataFrame(rows, columns=COLUMNS) if rows else _empty()


def spike_baseline(meter: pd.DataFrame) -> pd.Series:
    """Typical level of this meter just before each slot (aligned with `meter` rows).

    = median of the meter's NON-ZERO observed readings in the previous 7 days
    (the current slot is excluded, so a spike can't raise its own baseline).
    Returns NaN where there are fewer than SPIKE_MIN_HISTORY_READINGS such readings.
    Zeros are ignored because 75% of readings are 0: a plain median would be 0
    and every non-zero reading would look like a spike.
    """
    in_use = meter["energy_kwh_observed"].where(meter["energy_kwh_observed"] > 0)
    series = pd.Series(in_use.to_numpy(), index=pd.DatetimeIndex(meter["slot_start"]))
    window = f"{config.SPIKE_BASELINE_DAYS}D"
    # closed="left": the window covers the 7 days BEFORE the slot, not the slot itself.
    median = series.rolling(window, closed="left").median()
    history = series.notna().astype(float).rolling(window, closed="left").sum()
    median[history < config.SPIKE_MIN_HISTORY_READINGS] = float("nan")
    return pd.Series(median.to_numpy(), index=meter.index)


def detect_spikes(meter: pd.DataFrame) -> pd.DataFrame:
    """SPIKE: a reading more than 5x the meter's typical level of the past 7 days.

    Why it matters: a sudden jump can mean a new heavy appliance, a fault or
    short circuit, or meter tampering; it can also overload a small solar system.
    "Typical level" = median of the previous 7 days' non-zero readings, never
    below 0.001 kWh per 15 min (4 W), so tiny baselines don't create false alarms.
    Consecutive spiking slots are reported as ONE event.
    Severity by the highest ratio in the event: up to 10x low, up to 20x medium, above high.
    """
    baseline = spike_baseline(meter).clip(lower=config.SPIKE_MIN_BASELINE_KWH)
    value = meter["energy_kwh_observed"]
    is_spike = (value > config.SPIKE_FACTOR * baseline).fillna(False)  # no baseline -> no spike
    rows = []
    for first, last in find_runs(is_spike):
        ratios = (value / baseline).iloc[first:last + 1]
        peak = ratios.idxmax()
        rows.append({
            "rule": "SPIKE",
            "start": meter["slot_start"].iloc[first],
            "end": meter["slot_start"].iloc[last] + SLOT,
            "severity": config.severity_for(ratios.max(), config.SPIKE_SEVERITY_RATIO),
            "details": f"peak {value[peak]:.4f} kWh vs typical {baseline[peak]:.4f} kWh ({ratios.max():.1f}x)",
        })
    return pd.DataFrame(rows, columns=COLUMNS) if rows else _empty()


def detect_negatives(meter: pd.DataFrame) -> pd.DataFrame:
    """NEGATIVE: a reading below 0 kWh.

    Why it matters: these customer meters only measure consumption, so energy
    can't flow backwards. A negative value means a meter, firmware or data
    processing error, and would distort billing. Always high severity.
    Consecutive negative slots are reported as one event.
    """
    value = meter["energy_kwh_observed"]
    rows = []
    for first, last in find_runs(value.lt(0)):
        rows.append({
            "rule": "NEGATIVE",
            "start": meter["slot_start"].iloc[first],
            "end": meter["slot_start"].iloc[last] + SLOT,
            "severity": config.NEGATIVE_SEVERITY,
            "details": f"lowest value {value.iloc[first:last + 1].min():.4f} kWh",
        })
    return pd.DataFrame(rows, columns=COLUMNS) if rows else _empty()


def detect_flatlines(meter: pd.DataFrame) -> pd.DataFrame:
    """FLATLINE: exactly the same non-zero reading repeated for 12 hours or more.

    Why it matters: real household use goes up and down (day vs night), so an
    identical value for 12 h suggests a stuck meter, or the system repeating
    the last value it received. Zeros are excluded (that is ZERO_RUN's job).
    Severity by length: up to 24 h low, up to 48 h medium, longer high.
    """
    value = meter["energy_kwh_observed"].reset_index(drop=True)
    # Same value as the slot before? (NaN is never equal, so a gap breaks the run.)
    # A run of k repeats means k+1 slots share the value, so we include the slot before.
    repeats = value.eq(value.shift()) & value.gt(0)
    rows = []
    for first, last in find_runs(repeats):
        first -= 1  # the slot that started the repeated value
        n_slots = last - first + 1
        if n_slots < config.hours_to_slots(config.FLATLINE_MIN_HOURS):
            continue
        hours = n_slots / config.SLOTS_PER_HOUR
        rows.append({
            "rule": "FLATLINE",
            "start": meter["slot_start"].iloc[first],
            "end": meter["slot_start"].iloc[last] + SLOT,
            "severity": config.severity_for(hours, config.FLATLINE_SEVERITY_HOURS),
            "details": f"{value.iloc[first]:.4f} kWh repeated for {hours:.1f} h",
        })
    return pd.DataFrame(rows, columns=COLUMNS) if rows else _empty()


RULES = [detect_gaps, detect_zero_runs, detect_spikes, detect_negatives, detect_flatlines]


def run_all_rules(meter: pd.DataFrame) -> pd.DataFrame:
    """Apply every rule to one meter and stack the results."""
    meter = meter.sort_values("slot_start").reset_index(drop=True)
    found = [rule(meter) for rule in RULES]
    found = [f for f in found if not f.empty]
    return pd.concat(found, ignore_index=True) if found else _empty()


# ---------------------------------------------------------------------------
# Context from payments (not a rule: adds business meaning to ZERO_RUN)
# ---------------------------------------------------------------------------
def add_payment_context(anomalies: pd.DataFrame, payments: pd.DataFrame) -> pd.DataFrame:
    """For each ZERO_RUN, note whether the customer paid within 24 h after it ended.

    A run that ends right after a top-up is very likely "out of credit", not a fault.
    """
    anomalies = anomalies.copy()
    anomalies["paid_within_24h_after"] = pd.NA
    zero = anomalies["rule"] == "ZERO_RUN"
    for idx, row in anomalies[zero].iterrows():
        mine = payments[payments["meter_id"] == row["meter_id"]]
        window = mine["time_utc"].between(row["end"] - pd.Timedelta(hours=1), row["end"] + pd.Timedelta(hours=24))
        anomalies.loc[idx, "paid_within_24h_after"] = bool(window.any())
    return anomalies


def run(local: bool) -> None:
    grid = storage.read_csv(config.PROCESSED_PREFIX, "readings_clean.csv.gz", local,
                            usecols=["meter_id", "site", "slot_start", "energy_kwh_observed"])
    grid["slot_start"] = pd.to_datetime(grid["slot_start"], utc=True)
    payments = storage.read_csv(config.PROCESSED_PREFIX, "payments_clean.csv", local)
    payments["time_utc"] = pd.to_datetime(payments["time_utc"], utc=True)
    sites = storage.read_csv(config.PROCESSED_PREFIX, "sites.csv", local)
    offset_h = sites.set_index("site")["utc_offset_hours"]

    results = []
    for (meter_id, site), meter in grid.groupby(["meter_id", "site"]):
        found = run_all_rules(meter)
        if not found.empty:
            found.insert(0, "meter_id", meter_id)
            found.insert(1, "site", site)
            results.append(found)
    anomalies = pd.concat(results, ignore_index=True)
    anomalies = add_payment_context(anomalies, payments)

    # Extra columns for the dashboard: duration and local start time.
    anomalies["duration_hours"] = ((anomalies["end"] - anomalies["start"]) / pd.Timedelta(hours=1)).round(2)
    local_shift = pd.to_timedelta(anomalies["site"].map(offset_h), unit="h")
    anomalies["start_local"] = anomalies["start"].dt.tz_localize(None) + local_shift

    summary = anomalies.pivot_table(index="rule", columns="severity", values="meter_id",
                                    aggfunc="size", fill_value=0)
    log.info("anomalies by rule and severity:\n%s", summary.to_string())
    storage.write_csv(anomalies, config.PROCESSED_PREFIX, "anomalies.csv", local)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the anomaly rules on cleaned readings.")
    parser.add_argument("--local", action="store_true", help="read/write ./data only, skip S3")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(local=args.local)
