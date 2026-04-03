import json
import logging
import os
import traceback
import uuid
from datetime import datetime, timezone

import boto3
from icalendar import Calendar, Event, vText

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

s3 = boto3.client("s3")

REQUIRED_FIELDS = ("title", "start", "end", "timezone")


def lambda_handler(event, context):
    try:
        body = _parse_body(event)
        _validate(body)

        ics_bytes = _generate_ics(body)
        ics_url = _upload_to_s3(ics_bytes)
        html_snippet = f'<a href="{ics_url}">Add to Calendar</a>'

        logger.info("Generated ICS: %s", ics_url)
        return _response(200, {"ics_url": ics_url, "html_snippet": html_snippet})

    except ValueError as exc:
        logger.warning("Validation error: %s", exc)
        return _response(400, {"error": str(exc)})
    except Exception:
        logger.error("Unhandled exception:\n%s", traceback.format_exc())
        return _response(500, {"error": "Internal server error"})


def _parse_body(event):
    raw = event.get("body", "")
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode("utf-8")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc
    return raw


def _validate(body):
    missing = [f for f in REQUIRED_FIELDS if not body.get(f)]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    try:
        import zoneinfo
        zoneinfo.ZoneInfo(body["timezone"])
    except (KeyError, zoneinfo.ZoneInfoNotFoundError) as exc:
        raise ValueError(f"Invalid timezone: {body['timezone']}") from exc

    for field in ("start", "end"):
        try:
            datetime.fromisoformat(body[field])
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid datetime for '{field}': {body[field]}") from exc


def _generate_ics(body):
    import zoneinfo

    tz = zoneinfo.ZoneInfo(body["timezone"])

    cal = Calendar()
    cal.add("prodid", "-//360BalancedLiving//CalendarUtils//EN")
    cal.add("version", "2.0")
    cal.add("method", "PUBLISH")

    evt = Event()
    evt.add("summary", body["title"])
    evt.add("dtstart", datetime.fromisoformat(body["start"]).replace(tzinfo=tz))
    evt.add("dtend", datetime.fromisoformat(body["end"]).replace(tzinfo=tz))
    evt.add("dtstamp", datetime.now(timezone.utc))
    evt["uid"] = str(uuid.uuid4())

    if body.get("location"):
        evt["location"] = vText(body["location"])
    if body.get("description"):
        evt["description"] = vText(body["description"])

    cal.add_component(evt)
    return cal.to_ical()


def _upload_to_s3(ics_bytes):
    bucket = os.environ["ICS_BUCKET_NAME"]
    base_url = os.environ["ICS_BASE_URL"]
    key = f"events/{uuid.uuid4()}.ics"

    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=ics_bytes,
        ContentType="text/calendar",
    )
    logger.info("Uploaded to s3://%s/%s", bucket, key)
    return f"{base_url}/{key}"


def _response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,x-api-key",
        },
        "body": json.dumps(body),
    }