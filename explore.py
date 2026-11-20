"""
explore.py - first look at the Renewvia mini-grid data.

Goal: learn the facts we need BEFORE choosing a time window and meters:
  - date range and number of meters per site
  - what values `imputation_method` takes (observed vs reconstructed)
  - whether timestamps look like UTC or local time
  - site details (country, currency, timezone) and tariff layout

It only prints; it does not change any file.
Run:  uv run python explore.py
"""

from pathlib import Path

import pandas as pd

# Folder where the downloaded Zenodo files live (gitignored).
SOURCE = Path("data/source/renewvia")

# The three sites we downloaded: file-name part -> label used in prints.
SITES = ["Lomekwi", "Lorengelup", "Ekong_Anaku"]


def section(title: str) -> None:
    """Print a visible heading so the output is easy to read."""
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# ---------------------------------------------------------------------------
# 1. Site metadata: country, currency, timezone
# ---------------------------------------------------------------------------
section("1. Projects (sites)")
projects = pd.read_parquet(SOURCE / "minigridprojects.parquet")
# Site names in the table may use spaces instead of underscores.
wanted = [s.replace("_", " ") for s in SITES]
mask = projects["projectName"].str.replace("_", " ").isin(wanted)
print(
    projects.loc[
        mask,
        ["projectName", "country", "currency", "timezoneOffsetUtc", "powerOnDate", "sizePv", "sizeBattery"],
    ].to_string(index=False)
)

section("2. Metering base stations for these sites")
stations = pd.read_parquet(SOURCE / "meteringbasestations.parquet")
st_mask = stations["projectName"].str.replace("_", " ").isin(wanted)
print(stations.loc[st_mask].to_string(index=False))

# ---------------------------------------------------------------------------
# 2. Meter readings, one site at a time (each file is millions of rows)
# ---------------------------------------------------------------------------
for site in SITES:
    section(f"3. Readings - {site}")
    # Read only the columns we need: less memory, faster.
    df = pd.read_parquet(
        SOURCE / f"sparkmeterreadings_clean_{site}.parquet",
        columns=["meter_customer_code", "meter_type", "slot_start", "energy_kwh", "imputation_method", "tariff"],
    )

    print(f"rows: {len(df):,}")
    print(f"meters: {df['meter_customer_code'].nunique():,}")
    print(f"first slot: {df['slot_start'].min()}   last slot: {df['slot_start'].max()}")
    print(f"timezone attached to slot_start: {df['slot_start'].dt.tz}")

    # Are timestamps on a clean 15-minute grid? (minutes should be 0/15/30/45)
    print("\nminute values in slot_start:")
    print(df["slot_start"].dt.minute.value_counts().sort_index().to_string())

    print("\nimputation_method (share of rows):")
    print(df["imputation_method"].value_counts(dropna=False, normalize=True).round(4).to_string())

    print("\nmeter_type:")
    print(df["meter_type"].value_counts(dropna=False).to_string())

    print("\ntariff names:")
    print(df["tariff"].value_counts(dropna=False).head(10).to_string())

    print("\nenergy_kwh summary:")
    print(df["energy_kwh"].describe().to_string())
    print(f"negative values: {(df['energy_kwh'] < 0).sum():,}")
    print(f"zero values:     {(df['energy_kwh'] == 0).sum():,}")
    print(f"missing (NaN):   {df['energy_kwh'].isna().sum():,}")

    # Average consumption by hour of day. Solar mini-grid households usually
    # peak in the evening (about 19:00-21:00 LOCAL time). If the peak shows up
    # 1-3 hours earlier, the timestamps are probably UTC.
    print("\nmean kWh per 15-min slot by hour of day (as stored):")
    hourly = df.groupby(df["slot_start"].dt.hour)["energy_kwh"].mean().round(4)
    print(hourly.to_string())

    # Per month: how many meters report, and what share of rows are observed.
    # This tells us which months are well covered, to pick our window.
    df["month"] = df["slot_start"].dt.to_period("M")
    df["is_observed"] = df["imputation_method"].eq("observed")
    monthly = df.groupby("month").agg(
        meters=("meter_customer_code", "nunique"),
        rows=("energy_kwh", "size"),
        observed_share=("is_observed", "mean"),
    )
    monthly["observed_share"] = monthly["observed_share"].round(3)
    print("\nper month (observed_share assumes the label 'observed'; check list above):")
    print(monthly.to_string())

    del df  # free memory before the next site

# ---------------------------------------------------------------------------
# 3. Tariffs: what a price row looks like
# ---------------------------------------------------------------------------
section("4. Tariffs (first rows) and platform tariffs")
tariffs = pd.read_parquet(SOURCE / "tariffs.parquet")
print(tariffs.head(10).to_string(index=False))
print("\nmeteringBaseStation values in tariffs:", tariffs["meteringBaseStation"].nunique())

platform = pd.read_parquet(SOURCE / "meteringplatformtariffs.parquet")
print(platform.head(10).to_string(index=False))
