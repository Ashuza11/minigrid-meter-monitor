"""
run_pipeline.py - run every step in order, with one log for the whole run.

Steps (each one reads what the previous one wrote):
  ingest      source Parquet files   -> raw/
  clean       raw/                   -> processed/ readings + data-quality report
  anomalies   processed/ readings    -> processed/anomalies.csv
  aggregate   processed/ readings    -> daily, monthly and accounting tables
  generation  raw/ monitoring data   -> energy balance tables
  manifest    processed/ tables      -> QuickSight manifests (one JSON per table)

The log goes to the screen AND to logs/pipeline_<date-time>.log, so every
run leaves a record of what happened and what the numbers were.

Run:  uv run python run_pipeline.py --local                  (everything, local files only)
      uv run python run_pipeline.py --local --skip-ingest    (reuse raw/, e.g. without the source files)
      uv run python run_pipeline.py                          (also read/write S3)
"""

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import aggregate
import anomalies
import clean
import generation
import ingest
import manifest

log = logging.getLogger("pipeline")

# Order matters: each step needs the output of the steps before it.
STEPS = [
    ("ingest", ingest.run),
    ("clean", clean.run),
    ("anomalies", anomalies.run),
    ("aggregate", aggregate.run),
    ("generation", generation.run),
    ("manifest", manifest.run),
]

LOG_DIR = Path("logs")


def setup_logging() -> Path:
    """Send log lines to the console and to a new file for this run."""
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"pipeline_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_file)],
    )
    return log_file


def run(local: bool, skip_ingest: bool) -> bool:
    """Run the steps in order. Stop at the first failure (later steps would use stale data)."""
    started = time.perf_counter()
    for name, step in STEPS:
        if name == "ingest" and skip_ingest:
            log.info("step %s: skipped (--skip-ingest), using existing raw/ files", name)
            continue
        log.info("step %s: start", name)
        t0 = time.perf_counter()
        try:
            step(local=local)
        except Exception:
            log.exception("step %s: FAILED, pipeline stopped", name)   # logs the full traceback
            return False
        log.info("step %s: done in %.1f s", name, time.perf_counter() - t0)
    log.info("pipeline finished in %.1f s", time.perf_counter() - started)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the whole MiniGrid Meter Monitor pipeline.")
    parser.add_argument("--local", action="store_true", help="read/write ./data only, skip S3")
    parser.add_argument("--skip-ingest", action="store_true", help="reuse the existing raw/ files")
    args = parser.parse_args()
    log_file = setup_logging()
    log.info("run started (local=%s), log file: %s", args.local, log_file)
    ok = run(local=args.local, skip_ingest=args.skip_ingest)
    sys.exit(0 if ok else 1)   # non-zero exit code tells schedulers/CI that the run failed
