"""
ingest.py - select our subset of the Renewvia data and save it to raw/.

What it does:
  1. For each site, read ONLY the readings inside the time window
     (Parquet filters skip the rest of the file, so we never load all 27M rows).
  2. Pick METERS_PER_SITE meters at random with a fixed seed (same meters every run).
  3. Give each meter a short readable ID (e.g. LOM-001) and keep a lookup table
     to the original pseudonymised code (needed to join payments).
  4. Save to raw/: readings, meters, sites, tariffs, payments.
  5. For the energy balance (generation.py), also save:
     - site_meter_totals: ALL meters of each site summed per 15-minute slot
       (the sample of 50 is not the whole site, so it can't be compared with
       what the site's solar system delivered);
     - generation: the site's solar/inverter monitoring (Victron VRM) power columns.

The raw/ files are a faithful subset of the source: no cleaning happens here.
Cleaning is clean.py's job, so we can always compare "before" and "after".

Run:  uv run python ingest.py --local     (writes to data/raw/)
      uv run python ingest.py             (also uploads to s3://<bucket>/raw/)
"""

import argparse
import logging

import pandas as pd
import pyarrow.parquet as pq

import config
import storage

log = logging.getLogger(__name__)

# Short prefix per site for readable meter IDs (LOM-001, LOR-001, EKA-001).
SITE_PREFIX = {"Lomekwi": "LOM", "Lorengelup": "LOR", "Ekong Anaku": "EKA"}

# Filter that keeps only rows inside the window. Parquet uses it to skip data
# on disk, before it ever reaches pandas ("predicate pushdown").
WINDOW_FILTER = [("slot_start", ">=", config.WINDOW_START), ("slot_start", "<", config.WINDOW_END)]


def load_site_readings(site: str, file_stem: str) -> pd.DataFrame:
    """Read one site's readings inside the window, with only the columns we use."""
    path = config.SOURCE_DIR / f"sparkmeterreadings_clean_{file_stem}.parquet"
    df = pd.read_parquet(
        path,
        columns=["meter_customer_code", "slot_start", "energy_kwh", "imputation_method", "tariff"],
        filters=WINDOW_FILTER,
    )
    df["site"] = site
    log.info("%s: %s rows, %d meters in window", site, f"{len(df):,}", df["meter_customer_code"].nunique())
    return df


def site_meter_totals(site_df: pd.DataFrame, site: str) -> pd.DataFrame:
    """Total energy of ALL meters at a site per 15-minute slot, before sampling.

    energy_kwh includes reconstructed values (like the revenue figures);
    energy_kwh_observed counts only what meters actually reported.
    """
    observed = site_df["imputation_method"] == config.OBSERVED_LABEL
    df = site_df.assign(energy_kwh_observed=site_df["energy_kwh"].where(observed))
    totals = df.groupby("slot_start").agg(
        energy_kwh=("energy_kwh", "sum"),
        energy_kwh_observed=("energy_kwh_observed", "sum"),
        meters=("meter_customer_code", "nunique"),
    ).reset_index()
    totals.insert(0, "site", site)
    return totals


def load_generation() -> pd.DataFrame:
    """Solar-system monitoring (Victron VRM) for our sites, power columns only.

    VRM rows are instantaneous POWER readings in watts at irregular times, and
    timestamp_local is LOCAL time (unlike meter readings, which are UTC).
    One extra day on each side is kept so generation.py can cut exact local days.
    """
    path = config.SOURCE_DIR / "vrmgeneration.parquet"
    start = config.WINDOW_START - pd.Timedelta(days=1)
    end = config.WINDOW_END + pd.Timedelta(days=1)
    # Pick the columns from the file's schema (column list only, no data read),
    # then load just those. Loading every column first used over 2 GB of RAM.
    all_columns = pq.read_schema(path).names
    keep = [c for c in all_columns
            if c.endswith("_PV_power")                              # each solar charger's PV input
            or c.startswith("System_overview_AC_Consumption")       # power delivered to the village
            or c.startswith("System_overview_Genset")               # diesel generator, if any
            or c == "System_overview_Battery_SOC"]                  # battery state of charge (%)
    df = pd.read_parquet(path, columns=["Project_Name", "timestamp_local", *keep],
                         filters=[("Project_Name", "in", list(config.SITES)),
                                  ("timestamp_local", ">=", start), ("timestamp_local", "<", end)])
    df = df.dropna(axis="columns", how="all")
    log.info("generation: %s rows, %d power columns", f"{len(df):,}", len(keep))
    return df.rename(columns={"Project_Name": "site"})


def choose_meters(codes: pd.Series, n: int, seed: int) -> list[str]:
    """Pick n meter codes at random, reproducibly.

    The codes are sorted first: the order pandas returns unique values in can
    differ between runs, and the same seed only gives the same meters if it
    starts from the same list.
    """
    unique_codes = pd.Series(sorted(codes.unique()))
    n = min(n, len(unique_codes))  # a site with fewer meters keeps all of them
    return sorted(unique_codes.sample(n=n, random_state=seed))


def build_meters_table(readings: pd.DataFrame) -> pd.DataFrame:
    """One row per chosen meter: short ID, original code, site and tariff class.

    Each meter has exactly one tariff class in the window (checked during
    exploration), so it belongs here rather than on every reading.
    """
    meters = (
        readings.groupby("meter_customer_code", as_index=False)
        .agg(site=("site", "first"), tariff_class=("tariff", "first"))
        .sort_values(["site", "meter_customer_code"])
    )
    # Number meters 1..n within each site: LOM-001, LOM-002, ...
    number = meters.groupby("site").cumcount() + 1
    meters["meter_id"] = meters["site"].map(SITE_PREFIX) + "-" + number.map("{:03d}".format)
    return meters[["meter_id", "meter_customer_code", "site", "tariff_class"]]


def load_sites() -> pd.DataFrame:
    """Site facts from minigridprojects.parquet (country, currency, UTC offset, PV size...)."""
    projects = pd.read_parquet(config.SOURCE_DIR / "minigridprojects.parquet")
    sites = projects[projects["projectName"].isin(config.SITES)]
    return sites[
        ["projectName", "country", "currency", "timezoneOffsetUtc", "sizePv", "sizeBattery",
         "peakLoadCapacity", "powerOnDate", "lat", "long"]
    ].rename(columns={"projectName": "site", "timezoneOffsetUtc": "utc_offset_hours",
                      "sizePv": "pv_kwp", "sizeBattery": "battery_kwh"})


def load_tariffs() -> pd.DataFrame:
    """Tariff history (all dates) for our sites; aggregate.py picks the price valid at each time."""
    tariffs = pd.read_parquet(config.SOURCE_DIR / "tariffs.parquet")
    tariffs = tariffs[tariffs["meteringBaseStation"].isin(config.SITES)]
    return tariffs[
        ["meteringBaseStation", "name", "startingDatetime", "baseRate", "vatRate", "totalRate"]
    ].rename(columns={"meteringBaseStation": "site", "name": "tariff_class"})


def load_payments(meters: pd.DataFrame) -> pd.DataFrame:
    """Payments made by our chosen meters' customers inside the window.

    Note: payment times look like LOCAL time (peak at 19:00 as stored),
    unlike readings, which are UTC. We keep the raw value here and convert in clean.py.
    """
    payments = pd.read_parquet(
        config.SOURCE_DIR / "paymentconfirmations.parquet",
        columns=["customerAccountNumber", "projectName", "transactionDatetime", "amount",
                 "currency", "paymentProcessor", "isTest", "isReversed", "isSignup", "isBalanceTransfer"],
        filters=[("transactionDatetime", ">=", config.WINDOW_START),
                 ("transactionDatetime", "<", config.WINDOW_END)],
    )
    # Keep only our 150 customers, and swap the long code for the short meter ID.
    payments = payments.merge(
        meters[["meter_id", "meter_customer_code"]],
        left_on="customerAccountNumber", right_on="meter_customer_code", how="inner",
    )
    return payments.drop(columns=["customerAccountNumber", "meter_customer_code"]).rename(
        columns={"projectName": "site"}
    )


def run(local: bool) -> None:
    """Build the raw subset and save it (locally, and to S3 unless local)."""
    chosen_parts, totals_parts = [], []
    for i, (site, file_stem) in enumerate(config.SITES.items()):
        site_df = load_site_readings(site, file_stem)
        totals_parts.append(site_meter_totals(site_df, site))
        # seed + i: each site gets its own (still fixed) random draw.
        chosen = choose_meters(site_df["meter_customer_code"], config.METERS_PER_SITE, config.RANDOM_SEED + i)
        site_df = site_df[site_df["meter_customer_code"].isin(chosen)]
        log.info("%s: kept %d meters, %s rows", site, len(chosen), f"{len(site_df):,}")
        chosen_parts.append(site_df)

    readings = pd.concat(chosen_parts, ignore_index=True)
    meters = build_meters_table(readings)

    # Readings keep only the short ID: smaller files, readable dashboards.
    readings = readings.merge(meters[["meter_id", "meter_customer_code"]], on="meter_customer_code")
    readings = readings[["meter_id", "slot_start", "energy_kwh", "imputation_method"]]

    storage.write_csv(readings, config.RAW_PREFIX, "readings.csv.gz", local)
    storage.write_csv(meters, config.RAW_PREFIX, "meters.csv", local)
    storage.write_csv(load_sites(), config.RAW_PREFIX, "sites.csv", local)
    storage.write_csv(load_tariffs(), config.RAW_PREFIX, "tariffs.csv", local)
    storage.write_csv(load_payments(meters), config.RAW_PREFIX, "payments.csv", local)
    storage.write_csv(pd.concat(totals_parts, ignore_index=True), config.RAW_PREFIX,
                      "site_meter_totals.csv.gz", local)
    storage.write_csv(load_generation(), config.RAW_PREFIX, "generation.csv.gz", local)
    log.info("ingest done: %d meters, %s readings", len(meters), f"{len(readings):,}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Select the Renewvia subset and save it to raw/.")
    parser.add_argument("--local", action="store_true", help="write to ./data only, skip S3")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(local=args.local)
