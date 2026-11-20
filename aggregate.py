"""
aggregate.py - turn cleaned 15-minute readings into the tables the dashboard uses.

Outputs (all in processed/):
  daily_meter.csv       kWh per meter per LOCAL day (+ how much of it was observed)
  monthly_site.csv      kWh, revenue, customers, ARPU and billed vs collected per site and month
  accounting.csv        revenue per site, month and customer class: local currency
                        (with and without VAT) and USD
  load_profile.csv      average consumption per meter for each local hour of the day

Key choices (see docs/decisions.md):
  - Days and months are LOCAL time. The partial local days at the window edges
    (1 Jan and 1 Jul) are dropped, so the period is 2 Jan - 30 Jun 2024.
  - Energy totals and revenue use energy_kwh (observed + reconstructed): the
    energy was really delivered even if the meter couldn't report it in time.
    The observed share is reported next to every total, so readers can judge it.
  - Revenue = kWh x the tariff in force that day (totalRate, VAT included).
  - USD = local currency / official monthly average rate (reference/ file).

Run:  uv run python aggregate.py --local
"""

import argparse
import logging

import pandas as pd

import config
import storage

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Daily per meter
# ---------------------------------------------------------------------------
def daily_per_meter(readings: pd.DataFrame) -> pd.DataFrame:
    """Sum 15-minute readings into one row per meter and local day.

    Days without all 96 slots (the window edges in local time) are dropped,
    so every remaining day is comparable.
    """
    df = readings.copy()
    df["date_local"] = df["slot_start_local"].dt.normalize()   # midnight of the local day
    daily = df.groupby(["meter_id", "site", "date_local"]).agg(
        kwh=("energy_kwh", lambda s: s.sum(min_count=1)),               # NaN if no value at all
        kwh_observed=("energy_kwh_observed", lambda s: s.sum(min_count=1)),
        slots=("energy_kwh", "size"),
        slots_observed=("energy_kwh_observed", "count"),                # count() skips NaN
    ).reset_index()
    full_day = daily["slots"] == config.SLOTS_PER_DAY
    return daily[full_day].drop(columns="slots").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------
def add_tariff(daily: pd.DataFrame, meters: pd.DataFrame, tariffs: pd.DataFrame) -> pd.DataFrame:
    """Attach the tariff in force on each day for the meter's site and customer class.

    merge_asof picks, for each day, the LATEST tariff row that started on or
    before that day (like looking up the price list valid at that date).
    """
    df = daily.merge(meters[["meter_id", "tariff_class"]], on="meter_id", how="left")
    prices = tariffs[["site", "tariff_class", "startingDatetime", "totalRate", "vatRate"]].copy()
    prices["startingDatetime"] = pd.to_datetime(prices["startingDatetime"])
    df = pd.merge_asof(df.sort_values("date_local"), prices.sort_values("startingDatetime"),
                       left_on="date_local", right_on="startingDatetime",
                       by=["site", "tariff_class"], direction="backward")
    df = df.rename(columns={"totalRate": "price_per_kwh", "vatRate": "vat_rate"})
    return df.drop(columns="startingDatetime").sort_values(["meter_id", "date_local"]).reset_index(drop=True)


def add_revenue(daily: pd.DataFrame) -> pd.DataFrame:
    """Estimated billed revenue per meter-day, in local currency.

    price_per_kwh already includes VAT, so the net amount is price / (1 + VAT).
    """
    df = daily.copy()
    df["revenue_lcy"] = df["kwh"] * df["price_per_kwh"]
    df["revenue_excl_vat_lcy"] = df["revenue_lcy"] / (1 + df["vat_rate"])
    return df


def to_usd(table: pd.DataFrame, fx: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Add a *_usd column for each local-currency column, using the month's average rate.

    `table` needs 'month' (YYYY-MM) and 'currency'; `fx` has month, currency, local_per_usd.
    """
    df = table.merge(fx[["month", "currency", "local_per_usd"]], on=["month", "currency"], how="left")
    for col in columns:
        df[col.replace("_lcy", "_usd")] = df[col] / df["local_per_usd"]
    return df


# ---------------------------------------------------------------------------
# Monthly tables
# ---------------------------------------------------------------------------
def monthly_accounting(daily: pd.DataFrame, sites: pd.DataFrame, fx: pd.DataFrame) -> pd.DataFrame:
    """Revenue per site, month and customer class (local currency and USD)."""
    df = daily.assign(month=daily["date_local"].dt.strftime("%Y-%m"))
    acc = df.groupby(["site", "month", "tariff_class"]).agg(
        kwh=("kwh", "sum"),
        revenue_lcy=("revenue_lcy", "sum"),
        revenue_excl_vat_lcy=("revenue_excl_vat_lcy", "sum"),
        price_per_kwh=("price_per_kwh", "max"),
    ).reset_index()
    # A "paying customer" in a month = a meter that used some energy that month.
    users = (df[df["kwh"] > 0].groupby(["site", "month", "tariff_class"])["meter_id"]
             .nunique().rename("customers_with_use"))
    acc = acc.merge(users, on=["site", "month", "tariff_class"], how="left")
    acc["customers_with_use"] = acc["customers_with_use"].fillna(0).astype(int)
    acc["vat_lcy"] = acc["revenue_lcy"] - acc["revenue_excl_vat_lcy"]
    acc = acc.merge(sites[["site", "currency"]], on="site", how="left")
    return to_usd(acc, fx, ["revenue_lcy", "revenue_excl_vat_lcy", "vat_lcy"])


def monthly_site(daily: pd.DataFrame, payments: pd.DataFrame, sites: pd.DataFrame,
                 fx: pd.DataFrame) -> pd.DataFrame:
    """One row per site and month: energy, data quality, revenue, ARPU, billed vs collected."""
    df = daily.assign(month=daily["date_local"].dt.strftime("%Y-%m"))
    out = df.groupby(["site", "month"]).agg(
        meters=("meter_id", "nunique"),
        kwh=("kwh", "sum"),
        kwh_observed=("kwh_observed", "sum"),
        billed_lcy=("revenue_lcy", "sum"),
    ).reset_index()
    users = df[df["kwh"] > 0].groupby(["site", "month"])["meter_id"].nunique().rename("customers_with_use")
    out = out.merge(users, on=["site", "month"], how="left")
    out["customers_with_use"] = out["customers_with_use"].fillna(0).astype(int)

    # Payments actually received (mobile money), counted on the same local days.
    first_day, last_day = df["date_local"].min(), df["date_local"].max()
    pay = payments.assign(date_local=payments["time_local"].dt.normalize())
    pay = pay[pay["date_local"].between(first_day, last_day)]
    collected = (pay.assign(month=pay["date_local"].dt.strftime("%Y-%m"))
                 .groupby(["site", "month"])
                 .agg(collected_lcy=("amount", "sum"), payers=("meter_id", "nunique")))
    out = out.merge(collected, on=["site", "month"], how="left")
    out[["collected_lcy", "payers"]] = out[["collected_lcy", "payers"]].fillna(0)

    # Ratios. Each one is "per something", so sites of different size compare fairly.
    out["observed_share_pct"] = 100 * out["kwh_observed"] / out["kwh"]
    out["kwh_per_customer"] = out["kwh"] / out["customers_with_use"]
    out["arpu_lcy"] = out["billed_lcy"] / out["customers_with_use"]        # average revenue per user
    out["collected_vs_billed_pct"] = 100 * out["collected_lcy"] / out["billed_lcy"]

    out = out.merge(sites[["site", "currency"]], on="site", how="left")
    return to_usd(out, fx, ["billed_lcy", "collected_lcy", "arpu_lcy"])


def load_profile(readings: pd.DataFrame) -> pd.DataFrame:
    """Average energy one meter uses in each local hour (observed readings only).

    mean of 15-minute observed values x 4 slots = average kWh in that hour.
    """
    df = readings.dropna(subset=["energy_kwh_observed"])
    df = df.assign(hour_local=df["slot_start_local"].dt.hour)
    profile = df.groupby(["site", "hour_local"])["energy_kwh_observed"].agg(["mean", "count"]).reset_index()
    profile["avg_kwh_per_meter_hour"] = profile["mean"] * config.SLOTS_PER_HOUR
    return profile.rename(columns={"count": "readings"}).drop(columns="mean")


# ---------------------------------------------------------------------------
# Pipeline step
# ---------------------------------------------------------------------------
def run(local: bool) -> None:
    readings = storage.read_csv(config.PROCESSED_PREFIX, "readings_clean.csv.gz", local,
                                usecols=["meter_id", "site", "slot_start_local", "energy_kwh", "energy_kwh_observed"],
                                parse_dates=["slot_start_local"])
    meters = storage.read_csv(config.PROCESSED_PREFIX, "meters.csv", local)
    sites = storage.read_csv(config.PROCESSED_PREFIX, "sites.csv", local)
    tariffs = storage.read_csv(config.RAW_PREFIX, "tariffs.csv", local)
    payments = storage.read_csv(config.PROCESSED_PREFIX, "payments_clean.csv", local, parse_dates=["time_local"])
    fx = pd.read_csv(config.FX_RATES_FILE)

    daily = add_revenue(add_tariff(daily_per_meter(readings), meters, tariffs))
    missing_price = daily["price_per_kwh"].isna().sum()
    if missing_price:
        log.warning("%d meter-days have no tariff and get no revenue", missing_price)

    accounting = monthly_accounting(daily, sites, fx)
    site_month = monthly_site(daily, payments, sites, fx)
    profile = load_profile(readings)

    log.info("days per meter: %d (%s to %s)", daily["date_local"].nunique(),
             daily["date_local"].min().date(), daily["date_local"].max().date())
    if site_month["local_per_usd"].isna().any():
        log.warning("some months have no exchange rate; their USD values are empty")

    storage.write_csv(daily.round({c: 6 for c in daily.select_dtypes("number")}), config.PROCESSED_PREFIX, "daily_meter.csv", local)
    storage.write_csv(site_month.round(4), config.PROCESSED_PREFIX, "monthly_site.csv", local)
    storage.write_csv(accounting.round(4), config.PROCESSED_PREFIX, "accounting.csv", local)
    storage.write_csv(profile.round(6), config.PROCESSED_PREFIX, "load_profile.csv", local)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Aggregate readings into daily, monthly and accounting tables.")
    parser.add_argument("--local", action="store_true", help="read/write ./data only, skip S3")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(local=args.local)
