"""
generation.py - energy balance per site: solar generated vs delivered vs metered.

Three numbers per site and day:
  pv_kwh       energy produced by the solar panels (sum of all solar chargers)
  load_kwh     energy the power system delivered to the village network (AC output)
  metered_kwh  energy recorded by ALL customer meters at the site

From them:
  metered_share_of_load_pct  = metered / delivered. The rest is lost in the village
                               network or never reaches a meter (line losses,
                               unmetered loads, theft, faulty meters).
  pv_kwh_per_kwp_day         = solar energy per kW of installed panels per day
                               (how hard the panels are actually working).

The monitoring data (Victron VRM) gives POWER in watts at irregular moments, not
energy. To turn power into energy we average the power inside each 15-minute
slot and multiply by the slot length: 2,000 W for 0.25 h = 0.5 kWh.

Fair comparison: a slot counts only when the monitoring reported BOTH solar and
delivered power in it, and the meter total is summed over those same slots.
Days with less than GENERATION_MIN_COVERAGE monitored slots are left out.

Run:  uv run python generation.py --local
"""

import argparse
import logging

import pandas as pd

import config
import storage

log = logging.getLogger(__name__)

SLOT_HOURS = config.INTERVAL_MINUTES / 60   # 0.25 h


# ---------------------------------------------------------------------------
# Monitoring data: power (W) -> energy (kWh) per 15-minute slot
# ---------------------------------------------------------------------------
def total_power(gen: pd.DataFrame) -> pd.DataFrame:
    """Add up the device columns into three site-level power series (watts).

    - pv_w: all solar chargers' PV input added together.
    - load_w: AC output on the three phases (L1-L3) added together.
    - genset_w: diesel generator. It is only recorded while running, so empty = 0 W.
    """
    def cols(prefix_or_suffix: str) -> list[str]:
        return [c for c in gen.columns if prefix_or_suffix in c]

    numbers = gen.drop(columns=["site", "timestamp_local"]).apply(pd.to_numeric, errors="coerce")
    return pd.DataFrame({
        "site": gen["site"],
        "timestamp_local": pd.to_datetime(gen["timestamp_local"]),
        "pv_w": numbers[cols("_PV_power")].sum(axis=1, min_count=1),     # NaN if no charger reported
        "load_w": numbers[cols("AC_Consumption")].sum(axis=1, min_count=1),
        "genset_w": numbers[cols("Genset")].sum(axis=1).fillna(0),
        "battery_soc_pct": numbers.get("System_overview_Battery_SOC"),
    })


def power_to_slot_energy(power: pd.DataFrame) -> pd.DataFrame:
    """Average power in each 15-minute slot, then convert to kWh.

    Returns one row per site and slot (local time); slots with no monitoring
    rows are present with empty values, so gaps stay visible.
    """
    parts = []
    for site, g in power.groupby("site"):
        slots = g.set_index("timestamp_local")[["pv_w", "load_w", "genset_w", "battery_soc_pct"]] \
                 .resample(f"{config.INTERVAL_MINUTES}min").mean()
        energy = pd.DataFrame({
            "site": site,
            "slot_local": slots.index,
            "pv_kwh": slots["pv_w"].to_numpy() / 1000 * SLOT_HOURS,
            "load_kwh": slots["load_w"].to_numpy() / 1000 * SLOT_HOURS,
            "genset_kwh": slots["genset_w"].to_numpy() / 1000 * SLOT_HOURS,
            "battery_soc_pct": slots["battery_soc_pct"].to_numpy(),
        })
        parts.append(energy)
    return pd.concat(parts, ignore_index=True)


# ---------------------------------------------------------------------------
# Customer meters: all meters of a site, moved to local time
# ---------------------------------------------------------------------------
def meter_totals_local(totals: pd.DataFrame, sites: pd.DataFrame) -> pd.DataFrame:
    """Site meter totals with the UTC slot converted to local time (no DST at our sites)."""
    offset_h = totals["site"].map(sites.set_index("site")["utc_offset_hours"])
    slot_utc = pd.to_datetime(totals["slot_start"], utc=True).dt.tz_localize(None)
    return totals.assign(slot_local=slot_utc + pd.to_timedelta(offset_h, unit="h")) \
                 .drop(columns="slot_start") \
                 .rename(columns={"energy_kwh": "metered_kwh", "energy_kwh_observed": "metered_kwh_observed"})


# ---------------------------------------------------------------------------
# Energy balance
# ---------------------------------------------------------------------------
def daily_balance(slot_energy: pd.DataFrame, meters_local: pd.DataFrame) -> pd.DataFrame:
    """One row per site and local day, using only slots where solar AND load were monitored."""
    df = slot_energy.merge(meters_local, on=["site", "slot_local"], how="inner")   # inner: both sides exist
    df["date_local"] = df["slot_local"].dt.normalize()
    df["monitored"] = df["pv_kwh"].notna() & df["load_kwh"].notna()

    all_slots = df.groupby(["site", "date_local"]).size().rename("slots")
    m = df[df["monitored"]]
    daily = m.groupby(["site", "date_local"]).agg(
        monitored_slots=("monitored", "size"),
        pv_kwh=("pv_kwh", "sum"),
        load_kwh=("load_kwh", "sum"),
        genset_kwh=("genset_kwh", "sum"),
        metered_kwh=("metered_kwh", "sum"),
        metered_kwh_observed=("metered_kwh_observed", "sum"),
        meters=("meters", "max"),
        battery_soc_pct=("battery_soc_pct", "mean"),
    )
    daily = daily.join(all_slots, how="right").reset_index()
    daily["monitored_slots"] = daily["monitored_slots"].fillna(0).astype(int)
    # Only full local days (96 meter slots), like aggregate.py.
    daily = daily[daily["slots"] == config.SLOTS_PER_DAY].drop(columns="slots")
    daily["coverage_pct"] = 100 * daily["monitored_slots"] / config.SLOTS_PER_DAY
    daily["included"] = daily["coverage_pct"] >= 100 * config.GENERATION_MIN_COVERAGE
    return daily.reset_index(drop=True)


def monthly_balance(daily: pd.DataFrame, sites: pd.DataFrame) -> pd.DataFrame:
    """Monthly totals over included days, with the two key ratios."""
    d = daily[daily["included"]].assign(month=lambda x: x["date_local"].dt.strftime("%Y-%m"))
    out = d.groupby(["site", "month"]).agg(
        days_included=("date_local", "nunique"),
        monitored_slots=("monitored_slots", "sum"),
        pv_kwh=("pv_kwh", "sum"),
        load_kwh=("load_kwh", "sum"),
        genset_kwh=("genset_kwh", "sum"),
        metered_kwh=("metered_kwh", "sum"),
        metered_kwh_observed=("metered_kwh_observed", "sum"),
        meters=("meters", "max"),
        battery_soc_pct=("battery_soc_pct", "mean"),
    ).reset_index()
    out = out.merge(sites[["site", "pv_kwp"]], on="site", how="left")
    monitored_days = out["monitored_slots"] / config.SLOTS_PER_DAY      # e.g. 30 days at 95% = 28.5
    out["metered_share_of_load_pct"] = 100 * out["metered_kwh"] / out["load_kwh"]
    out["pv_kwh_per_kwp_day"] = out["pv_kwh"] / out["pv_kwp"] / monitored_days
    out["load_kwh_per_day"] = out["load_kwh"] / monitored_days
    return out


# ---------------------------------------------------------------------------
# Pipeline step
# ---------------------------------------------------------------------------
def run(local: bool) -> None:
    gen = storage.read_csv(config.RAW_PREFIX, "generation.csv.gz", local)
    totals = storage.read_csv(config.RAW_PREFIX, "site_meter_totals.csv.gz", local)
    sites = storage.read_csv(config.PROCESSED_PREFIX, "sites.csv", local)

    slot_energy = power_to_slot_energy(total_power(gen))
    daily = daily_balance(slot_energy, meter_totals_local(totals, sites))
    monthly = monthly_balance(daily, sites)

    for site, g in daily.groupby("site"):
        log.info("%s: %d of %d days have >= %d%% monitoring coverage", site, int(g["included"].sum()),
                 len(g), int(100 * config.GENERATION_MIN_COVERAGE))

    storage.write_csv(daily.round({c: 4 for c in daily.select_dtypes("number")}), config.PROCESSED_PREFIX, "energy_balance_daily.csv", local)
    storage.write_csv(monthly.round(4), config.PROCESSED_PREFIX, "energy_balance_monthly.csv", local)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Solar generated vs delivered vs metered, per site.")
    parser.add_argument("--local", action="store_true", help="read/write ./data only, skip S3")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(local=args.local)
