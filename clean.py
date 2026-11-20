"""
clean.py - turn the raw subset into analysis-ready tables and measure data quality.

Steps for the readings:
  1. Parse types: timestamps as UTC, energy as numbers.
  2. Drop rows that can't be used (unparseable time, outside the window,
     not on the 15-minute grid) and remove duplicate (meter, slot) rows.
  3. Build the COMPLETE 15-minute grid for every meter, so missing intervals
     become visible rows instead of silently absent ones.
  4. Label every slot: observed / reconstructed / no_data.

Then: standardise site currencies, convert payment times to UTC, and write a
data-quality report (CSV per meter + a short markdown summary).

Two energy columns are kept on purpose:
  energy_kwh           dataset value, including reconstructed estimates
                       -> used for kWh totals and revenue (the energy was really sold)
  energy_kwh_observed  only values the meter actually reported, else empty
                       -> used for completeness and anomaly rules

Run:  uv run python clean.py --local
"""

import argparse
import logging

import pandas as pd

import config
import storage

log = logging.getLogger(__name__)

# Messy currency labels found in minigridprojects -> ISO 4217 codes.
CURRENCY_CODES = {"Kshs": "KES", "N": "NGN"}


# ---------------------------------------------------------------------------
# Readings
# ---------------------------------------------------------------------------
def parse_readings(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Parse types and drop rows that can't be placed on the grid.

    Returns the parsed rows and a dict counting what was dropped and why.
    """
    df = raw.copy()
    # errors="coerce" turns anything unparseable into NaT/NaN instead of crashing,
    # so we can COUNT bad values rather than lose the whole run.
    df["slot_start"] = pd.to_datetime(df["slot_start"], errors="coerce", utc=True)
    df["energy_kwh"] = pd.to_numeric(df["energy_kwh"], errors="coerce")

    counts = {"bad_timestamp": int(df["slot_start"].isna().sum())}
    df = df.dropna(subset=["slot_start"])

    start = config.WINDOW_START.tz_localize("UTC")
    end = config.WINDOW_END.tz_localize("UTC")
    outside = (df["slot_start"] < start) | (df["slot_start"] >= end)
    counts["outside_window"] = int(outside.sum())
    df = df[~outside]

    # A valid slot starts exactly on :00, :15, :30 or :45.
    off_grid = df["slot_start"] != df["slot_start"].dt.floor(f"{config.INTERVAL_MINUTES}min")
    counts["off_grid"] = int(off_grid.sum())
    df = df[~off_grid]

    counts["energy_not_numeric"] = int(df["energy_kwh"].isna().sum())  # kept, treated as missing
    return df, counts


def remove_duplicates(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Keep one row per (meter, slot). Returns the cleaned rows and how many were removed.

    If a slot appears twice we keep the first occurrence; the count tells us
    whether this ever happens (it is reported, never silently ignored).
    """
    before = len(df)
    df = df.drop_duplicates(subset=["meter_id", "slot_start"], keep="first")
    return df, before - len(df)


def build_full_grid(readings: pd.DataFrame, meter_ids: list[str],
                    start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Return one row for EVERY meter and EVERY 15-minute slot in [start, end).

    Slots without a source row get status "no_data". A meter with no readings at
    all still gets its full grid (all "no_data"), so it shows up as 0% complete
    instead of disappearing from the report.
    """
    slots = pd.date_range(start, end, freq=f"{config.INTERVAL_MINUTES}min", inclusive="left")
    grid = pd.MultiIndex.from_product([meter_ids, slots], names=["meter_id", "slot_start"]).to_frame(index=False)
    grid = grid.merge(readings, on=["meter_id", "slot_start"], how="left")

    is_observed = grid["imputation_method"].eq(config.OBSERVED_LABEL) & grid["energy_kwh"].notna()
    has_value = grid["energy_kwh"].notna()
    grid["status"] = "no_data"
    grid.loc[has_value, "status"] = "reconstructed"
    grid.loc[is_observed, "status"] = "observed"
    # Observed-only energy: empty unless the meter really reported it.
    grid["energy_kwh_observed"] = grid["energy_kwh"].where(is_observed)
    return grid.drop(columns=["imputation_method"])


def add_local_time(grid: pd.DataFrame, meters: pd.DataFrame, sites: pd.DataFrame) -> pd.DataFrame:
    """Add site and local time. Days and months are counted in LOCAL time,
    because that is how customers and the company experience them.

    Kenya (+3) and Nigeria (+1) have no daylight saving time, so a fixed offset is exact.
    """
    grid = grid.merge(meters[["meter_id", "site"]], on="meter_id", how="left")
    offsets = grid["site"].map(sites.set_index("site")["utc_offset_hours"])
    grid["slot_start_local"] = grid["slot_start"].dt.tz_localize(None) + pd.to_timedelta(offsets, unit="h")
    return grid


# ---------------------------------------------------------------------------
# Sites and payments
# ---------------------------------------------------------------------------
def standardise_sites(sites: pd.DataFrame) -> pd.DataFrame:
    """Trim stray spaces ("Kshs " -> "Kshs") and map currencies to ISO codes."""
    sites = sites.copy()
    sites["currency"] = sites["currency"].str.strip().replace(CURRENCY_CODES)
    return sites


def clean_payments(payments: pd.DataFrame, sites: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Drop test/reversed payments and convert local payment time to UTC.

    Payment times look like local time (they peak at 19:00 as stored), so
    UTC = local - site offset.
    """
    p = payments.copy()
    p["time_local"] = pd.to_datetime(p["transactionDatetime"])
    offsets = p["site"].map(sites.set_index("site")["utc_offset_hours"])
    p["time_utc"] = (p["time_local"] - pd.to_timedelta(offsets, unit="h")).dt.tz_localize("UTC")

    is_test = p["isTest"].fillna(0).astype(bool)
    is_reversed = p["isReversed"].fillna(0).astype(bool)
    counts = {"test_payments": int(is_test.sum()), "reversed_payments": int(is_reversed.sum())}
    p = p[~is_test & ~is_reversed]
    p = p.rename(columns={"isSignup": "is_signup", "isBalanceTransfer": "is_balance_transfer"})
    cols = ["meter_id", "site", "time_local", "time_utc", "amount", "currency",
            "paymentProcessor", "is_signup", "is_balance_transfer"]
    return p[cols], counts


# ---------------------------------------------------------------------------
# Data-quality report
# ---------------------------------------------------------------------------
def quality_per_meter(grid: pd.DataFrame, meters: pd.DataFrame) -> pd.DataFrame:
    """Completeness per meter = observed slots / expected slots."""
    status_counts = pd.crosstab(grid["meter_id"], grid["status"])
    for col in ["observed", "reconstructed", "no_data"]:
        if col not in status_counts:  # e.g. a perfect dataset has no "no_data" column
            status_counts[col] = 0
    report = meters[["meter_id", "site", "tariff_class"]].merge(
        status_counts[["observed", "reconstructed", "no_data"]].reset_index(), on="meter_id", how="left"
    ).fillna({"observed": 0, "reconstructed": 0, "no_data": 0})
    report["slots_expected"] = report[["observed", "reconstructed", "no_data"]].sum(axis=1)
    report["completeness_pct"] = (100 * report["observed"] / report["slots_expected"]).round(2)
    report["reconstructed_pct"] = (100 * report["reconstructed"] / report["slots_expected"]).round(2)
    report["no_data_pct"] = (100 * report["no_data"] / report["slots_expected"]).round(2)
    report["below_target"] = report["completeness_pct"] < 100 * config.COMPLETENESS_TARGET

    # Completeness WHILE ACTIVE: only count slots between the meter's first and
    # last reading. A customer connected in April isn't penalised for January.
    # This separates "meter lifecycle" (joined late / left early) from
    # "unreliable reporting" (gaps while the meter was in service).
    has_row = grid[grid["status"] != "no_data"]
    span = has_row.groupby("meter_id")["slot_start"].agg(first_reading="min", last_reading="max")
    step = pd.Timedelta(minutes=config.INTERVAL_MINUTES)
    span["slots_active"] = (span["last_reading"] - span["first_reading"]) // step + 1
    report = report.merge(span.reset_index(), on="meter_id", how="left")
    # A meter with no readings at all has no active period -> stays empty (NaN).
    report["completeness_active_pct"] = (100 * report["observed"] / report["slots_active"]).round(2)
    return report.rename(columns={"observed": "slots_observed", "reconstructed": "slots_reconstructed",
                                  "no_data": "slots_no_data"})


def to_markdown_table(df: pd.DataFrame) -> str:
    """Render a small DataFrame (index included) as a markdown table, without extra libraries."""
    df = df.reset_index()
    header = "| " + " | ".join(df.columns) + " |"
    divider = "|" + "---|" * len(df.columns)
    rows = ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join([header, divider, *rows])


def quality_summary_markdown(stats: dict, report: pd.DataFrame) -> str:
    """A short human-readable summary of the cleaning run (every number comes from `stats`/`report`)."""
    by_site = report.groupby("site").agg(
        meters=("meter_id", "size"),
        median_completeness_pct=("completeness_pct", "median"),
        median_completeness_active_pct=("completeness_active_pct", "median"),
        mean_reconstructed_pct=("reconstructed_pct", "mean"),
        mean_no_data_pct=("no_data_pct", "mean"),
        meters_below_target=("below_target", "sum"),
    ).round(1)
    target = int(100 * config.COMPLETENESS_TARGET)
    lines = [
        "# Data-quality summary",
        "",
        f"Window: {config.WINDOW_START.date()} to {config.WINDOW_END.date()} (UTC, end exclusive), "
        f"{config.INTERVAL_MINUTES}-minute slots. Completeness = observed slots / expected slots.",
        "",
        "## Readings",
        "",
        "| Step | Rows |",
        "|---|---|",
        f"| Rows in (raw) | {stats['rows_in']:,} |",
        f"| Unparseable timestamp | {stats['bad_timestamp']:,} |",
        f"| Outside window | {stats['outside_window']:,} |",
        f"| Not on the 15-min grid | {stats['off_grid']:,} |",
        f"| Duplicates removed | {stats['duplicates_removed']:,} |",
        f"| Energy not numeric (kept as missing) | {stats['energy_not_numeric']:,} |",
        f"| Rows out (full grid: meters x slots) | {stats['rows_out']:,} |",
        f"| of which observed | {stats['observed']:,} |",
        f"| of which reconstructed by the dataset | {stats['reconstructed']:,} |",
        f"| of which no data | {stats['no_data']:,} |",
        "",
        "## Completeness by site",
        "",
        to_markdown_table(by_site),
        "",
        f"Meters below {target}% completeness: **{int(report['below_target'].sum())} of {len(report)}**. "
        "Per-meter detail: `data_quality_report.csv`.",
        "",
        f"Completeness while active (only between a meter's first and last reading): "
        f"**{int((report['completeness_active_pct'].fillna(0) < target).sum())} of {len(report)}** meters below {target}%. "
        "The difference between the two figures is meter lifecycle (customers connected late or "
        "disconnected early), not unreliable reporting.",
        "",
        "## Other fixes",
        "",
        f"- Site currencies standardised to ISO codes: {stats['currency_map']}.",
        f"- Payments: {stats['payments_in']:,} in, {stats['test_payments']} test and "
        f"{stats['reversed_payments']} reversed removed, {stats['payments_out']:,} out. "
        "Payment times converted from local time to UTC.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pipeline step
# ---------------------------------------------------------------------------
def run(local: bool) -> None:
    raw = storage.read_csv(config.RAW_PREFIX, "readings.csv.gz", local)
    meters = storage.read_csv(config.RAW_PREFIX, "meters.csv", local)
    sites = standardise_sites(storage.read_csv(config.RAW_PREFIX, "sites.csv", local))
    payments_raw = storage.read_csv(config.RAW_PREFIX, "payments.csv", local)

    readings, stats = parse_readings(raw)
    readings, stats["duplicates_removed"] = remove_duplicates(readings)
    grid = build_full_grid(readings, sorted(meters["meter_id"]),
                           config.WINDOW_START.tz_localize("UTC"), config.WINDOW_END.tz_localize("UTC"))
    grid = add_local_time(grid, meters, sites)

    status = grid["status"].value_counts()
    stats.update(rows_in=len(raw), rows_out=len(grid),
                 observed=int(status.get("observed", 0)),
                 reconstructed=int(status.get("reconstructed", 0)),
                 no_data=int(status.get("no_data", 0)),
                 currency_map=CURRENCY_CODES)

    payments, pay_counts = clean_payments(payments_raw, sites)
    stats.update(pay_counts, payments_in=len(payments_raw), payments_out=len(payments))

    report = quality_per_meter(grid, meters)
    for key in ["rows_in", "duplicates_removed", "rows_out", "observed", "reconstructed", "no_data"]:
        log.info("%s: %s", key, f"{stats[key]:,}")
    log.info("meters below %d%% completeness: %d of %d",
             int(100 * config.COMPLETENESS_TARGET), int(report["below_target"].sum()), len(report))

    cols = ["meter_id", "site", "slot_start", "slot_start_local", "energy_kwh", "energy_kwh_observed", "status"]
    storage.write_csv(grid[cols], config.PROCESSED_PREFIX, "readings_clean.csv.gz", local)
    storage.write_csv(meters, config.PROCESSED_PREFIX, "meters.csv", local)
    storage.write_csv(sites, config.PROCESSED_PREFIX, "sites.csv", local)
    storage.write_csv(payments, config.PROCESSED_PREFIX, "payments_clean.csv", local)
    storage.write_csv(report, config.PROCESSED_PREFIX, "data_quality_report.csv", local)
    storage.write_text(quality_summary_markdown(stats, report), config.PROCESSED_PREFIX,
                       "data_quality_summary.md", local)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clean readings and write the data-quality report.")
    parser.add_argument("--local", action="store_true", help="read/write ./data only, skip S3")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(local=args.local)
