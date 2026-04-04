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
    """
    Handles the cleanup process for expired objects in an S3 bucket by checking their metadata tags
    and deleting objects that are past their specified expiration date. The function logs the
    status of the operation and returns the count of total checked and deleted objects.

    :param event: The AWS Lambda event data received when the function is triggered. Typically,
                  this contains details about the event source and event parameters.
    :type event: dict

    :param context: The AWS Lambda context object that provides runtime information about the
                    Lambda function's execution. Includes information such as function name,
                    request ID, and memory limits.
    :type context: object

    :return: A dictionary with the counts of objects checked and deleted during the cleanup
             process.
    :rtype: dict
    """
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
