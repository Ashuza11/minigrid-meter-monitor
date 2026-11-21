"""
storage.py - save and load pipeline files, locally or on S3.

Design (kept deliberately simple):
  - Every file is ALWAYS written to the local data/ folder first.
  - Without --local, that file is then uploaded to s3://<bucket>/<same path>.
  - Reading works the other way: without --local, the file is downloaded from S3
    into data/ first, then read from disk.
So the code that does the real work only ever deals with pandas DataFrames, and
the "where does it live" question is answered in this one file.
"""

import logging
from pathlib import Path

import pandas as pd

import config

log = logging.getLogger(__name__)


def _s3_client():
    """Create an S3 client using the AWS profile and region from .env.

    boto3 is imported here (not at the top) so --local runs work even
    without any AWS setup.
    """
    import boto3
    from botocore.config import Config

    if not config.S3_BUCKET:
        raise RuntimeError("S3_BUCKET is not set. Create .env from .env.example, or use --local.")
    session = boto3.Session(profile_name=config.AWS_PROFILE, region_name=config.AWS_REGION)
    # Settings for a slow or unstable connection: wait up to 5 minutes for an
    # answer (default 60 s) and retry a failed request up to 10 times.
    slow_network = Config(connect_timeout=30, read_timeout=300,
                          retries={"max_attempts": 10, "mode": "standard"})
    return session.client("s3", config=slow_network)


def _transfer_config():
    """Upload big files in 8 MB parts, 2 at a time (default is 10 at a time).

    With 10 parallel parts a slow upload link is shared 10 ways, so each part
    is too slow and times out. 2 at a time gives each part enough bandwidth.
    """
    from boto3.s3.transfer import TransferConfig

    return TransferConfig(multipart_chunksize=8 * 1024 * 1024, max_concurrency=2)


def local_path(folder: str, filename: str) -> Path:
    """Where a pipeline file lives on disk, e.g. data/raw/readings.csv.gz."""
    return config.LOCAL_ROOT / folder / filename


def upload(path: Path, key: str) -> None:
    """Upload one local file to s3://<bucket>/<key>."""
    _s3_client().upload_file(str(path), config.S3_BUCKET, key, Config=_transfer_config())
    log.info("uploaded s3://%s/%s", config.S3_BUCKET, key)


def write_csv(df: pd.DataFrame, folder: str, filename: str, local: bool) -> Path:
    """Write a DataFrame as CSV to data/<folder>/<filename>, then upload it unless local.

    A filename ending in .gz is gzip-compressed automatically by pandas
    (about 5-10x smaller, which matters on a slow connection).
    """
    path = local_path(folder, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    log.info("wrote %s (%s rows)", path, f"{len(df):,}")
    if not local:
        upload(path, f"{folder}/{filename}")
    return path


def write_text(text: str, folder: str, filename: str, local: bool) -> Path:
    """Write a text file (e.g. a markdown summary) the same way as write_csv."""
    path = local_path(folder, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    log.info("wrote %s", path)
    if not local:
        upload(path, f"{folder}/{filename}")
    return path


def read_csv(folder: str, filename: str, local: bool, **kwargs) -> pd.DataFrame:
    """Read data/<folder>/<filename>; without --local, download it from S3 first.

    Extra keyword arguments go straight to pandas.read_csv (e.g. parse_dates=[...]).
    """
    path = local_path(folder, filename)
    if not local:
        path.parent.mkdir(parents=True, exist_ok=True)
        _s3_client().download_file(config.S3_BUCKET, f"{folder}/{filename}", str(path))
        log.info("downloaded s3://%s/%s/%s", config.S3_BUCKET, folder, filename)
    return pd.read_csv(path, **kwargs)
