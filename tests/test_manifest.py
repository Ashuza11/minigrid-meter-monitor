"""QuickSight manifest: one file per manifest, CSV settings QuickSight expects."""

import json

from manifest import DASHBOARD_TABLES, build_manifest


def test_manifest_points_at_the_s3_file():
    m = build_manifest("my-bucket", "processed/monthly_site.csv")
    assert m["fileLocations"] == [{"URIs": ["s3://my-bucket/processed/monthly_site.csv"]}]


def test_manifest_csv_settings():
    s = build_manifest("b", "k.csv")["globalUploadSettings"]
    assert s == {"format": "CSV", "delimiter": ",", "textqualifier": "\"", "containsHeader": "true"}


def test_manifest_is_valid_json():
    assert json.loads(json.dumps(build_manifest("b", "k.csv")))["fileLocations"]


def test_only_plain_csv_tables():
    # QuickSight docs don't list .csv.gz for manifests
    assert all(name.endswith(".csv") for name in DASHBOARD_TABLES)
