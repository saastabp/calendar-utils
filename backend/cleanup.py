import logging
import os
import traceback
from datetime import datetime, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

s3 = boto3.client("s3")

BUCKET = os.environ["ICS_BUCKET_NAME"]
PREFIX = "events/"


def lambda_handler(event, context):
    try:
        now = datetime.now(timezone.utc)
        deleted = 0
        checked = 0

        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=BUCKET, Prefix=PREFIX):
            for obj in page.get("Contents", []):
                checked += 1
                key = obj["Key"]

                try:
                    tagging = s3.get_object_tagging(Bucket=BUCKET, Key=key)
                    tags = {t["Key"]: t["Value"] for t in tagging.get("TagSet", [])}
                    expires_str = tags.get("expires")

                    if not expires_str:
                        continue

                    expires = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
                    if expires < now:
                        s3.delete_object(Bucket=BUCKET, Key=key)
                        deleted += 1
                        logger.info("Deleted expired object: %s (expired %s)", key, expires_str)
                except Exception:
                    logger.error("Error processing %s:\n%s", key, traceback.format_exc())

        logger.info("Cleanup complete: checked %d objects, deleted %d", checked, deleted)
        return {"checked": checked, "deleted": deleted}

    except Exception:
        logger.error("Cleanup failed:\n%s", traceback.format_exc())
        raise
