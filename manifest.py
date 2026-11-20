"""
manifest.py - write the QuickSight manifest files that point at the processed tables.

What a manifest is: a small JSON file that tells QuickSight WHERE a table lives
in S3 and HOW to read it (CSV, comma, header row). In QuickSight you create a
dataset "from S3", give it the manifest's URL, and it loads the CSV.

AWS rule (QuickSight docs, "Supported formats for Amazon S3 manifest files"):
all files in ONE manifest must have the same columns. Our tables all have
different columns, so each table gets its own manifest -> its own dataset.

Output: processed/manifests/<table>.json (and uploaded to S3 unless --local).

Run:  uv run python manifest.py --local
"""

import argparse
import json
import logging

import config
import storage

log = logging.getLogger(__name__)

PLACEHOLDER_BUCKET = "YOUR-BUCKET-NAME"

# Table -> dashboard sheet(s) that use it. Only plain .csv files: the QuickSight
# manifest docs list .csv/.tsv/.json/log files and don't mention .csv.gz.
DASHBOARD_TABLES = {
    "monthly_site.csv": "Overview, Trends",
    "energy_balance_monthly.csv": "Overview",
    "daily_meter.csv": "Trends",
    "load_profile.csv": "Trends",
    "anomalies.csv": "Anomalies",
    "data_quality_report.csv": "Anomalies",
    "accounting.csv": "Accounting",
}


def build_manifest(bucket: str, key: str) -> dict:
    """The QuickSight manifest for one CSV file at s3://bucket/key."""
    return {
        "fileLocations": [{"URIs": [f"s3://{bucket}/{key}"]}],
        "globalUploadSettings": {
            "format": "CSV",
            "delimiter": ",",
            "textqualifier": "\"",     # pandas quotes text containing commas with "
            "containsHeader": "true",  # first row = column names
        },
    }


def run(local: bool) -> None:
    bucket = config.S3_BUCKET
    if not bucket:
        bucket = PLACEHOLDER_BUCKET
        log.warning("S3_BUCKET is not set: manifests use '%s'; rerun with .env filled before using them",
                    bucket)
    for filename in DASHBOARD_TABLES:
        key = f"{config.PROCESSED_PREFIX}/{filename}"
        if not storage.local_path(config.PROCESSED_PREFIX, filename).exists():
            raise FileNotFoundError(f"{key} missing: run the earlier pipeline steps first")
        text = json.dumps(build_manifest(bucket, key), indent=2)
        storage.write_text(text, f"{config.PROCESSED_PREFIX}/manifests",
                           filename.replace(".csv", ".json"), local)
    log.info("wrote %d manifests for bucket %s", len(DASHBOARD_TABLES), bucket)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Write QuickSight manifests for the processed tables.")
    parser.add_argument("--local", action="store_true", help="write ./data only, skip S3")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(local=args.local)
