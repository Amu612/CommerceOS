"""
Pull the Olist + DataCo CSVs down from S3 before seeding, in cloud environments.

The datasets are gitignored (~194 MB, see docs/gap-analysis.md #3) so they never
ship in the container image. Locally, `DATASET_DIR` is usually unset and the
seed script finds the CSVs already sitting in `backend/data/raw/` on disk. In
ECS, `DATASET_DIR` is set to `s3://<bucket>/raw` (the Terraform `datasets`
module's bucket — see infra/aws/modules/datasets) and this script downloads
that prefix into the same local directory before
`scripts.seed_nexus_data` runs. A no-op everywhere else, so it's always safe
to put first in the migrate task's command.

    python -m scripts.sync_datasets
"""
from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger("sync_datasets")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_LOCAL_RAW_DIR = os.path.join(_BACKEND_DIR, "data", "raw")


def _local_dir_has_csvs(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    return any(f.lower().endswith(".csv") for f in os.listdir(path))


def sync_from_s3(s3_uri: str, dest_dir: str) -> int:
    import boto3

    assert s3_uri.startswith("s3://"), f"expected an s3:// URI, got {s3_uri!r}"
    bucket, _, prefix = s3_uri[len("s3://") :].partition("/")
    prefix = prefix.rstrip("/")

    os.makedirs(dest_dir, exist_ok=True)
    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")

    downloaded = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/" if prefix else ""):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            rel = key[len(prefix) + 1 :] if prefix else key
            local_path = os.path.join(dest_dir, rel)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            logger.info("downloading", extra={"key": key, "to": local_path})
            s3.download_file(bucket, key, local_path)
            downloaded += 1

    return downloaded


def main() -> int:
    dataset_dir = os.environ.get("DATASET_DIR", "").strip()

    if not dataset_dir.startswith("s3://"):
        logger.info("dataset_dir_not_s3_skipping", extra={"DATASET_DIR": dataset_dir or "(unset)"})
        return 0

    if _local_dir_has_csvs(_LOCAL_RAW_DIR):
        logger.info("local_csvs_already_present_skipping", extra={"dir": _LOCAL_RAW_DIR})
        return 0

    try:
        count = sync_from_s3(dataset_dir, _LOCAL_RAW_DIR)
    except Exception as exc:  # noqa: BLE001 - best-effort; seeding logs its own "no data" warning
        logger.warning("s3_sync_failed", extra={"error": str(exc)})
        return 0

    logger.info("s3_sync_complete", extra={"files": count, "dir": _LOCAL_RAW_DIR})
    return 0


if __name__ == "__main__":
    sys.exit(main())
