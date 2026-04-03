import json
import logging
import os
import traceback
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

import boto3
from icalendar import Calendar, Event, vText

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

s3 = boto3.client("s3")

REQUIRED_FIELDS = ("title", "start", "timezone")


def lambda_handler(event, context):
    try:
        body = _parse_body(event)
        _validate(body)

        ics_bytes = _generate_ics(body)
        ics_url = _upload_to_s3(ics_bytes)
        google_url = _build_google_url(body)
        outlook_url = _build_outlook_url(body)
        html_snippet = _build_html_snippet(ics_url, google_url, outlook_url)

        logger.info("Generated ICS: %s", ics_url)
        return _response(200, {
            "ics_url": ics_url,
            "google_url": google_url,
            "outlook_url": outlook_url,
            "html_snippet": html_snippet,
        })

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
        if not body.get(field):
            continue
        try:
            datetime.fromisoformat(body[field])
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid datetime for '{field}': {body[field]}") from exc


def _generate_ics(body):
    import zoneinfo
    from datetime import timedelta

    tz = zoneinfo.ZoneInfo(body["timezone"])

    cal = Calendar()
    cal.add("prodid", "-//360BalancedLiving//CalendarUtils//EN")
    cal.add("version", "2.0")
    cal.add("method", "PUBLISH")

    evt = Event()
    evt.add("summary", body["title"])

    start_dt = datetime.fromisoformat(body["start"]).replace(tzinfo=tz)
    evt.add("dtstart", start_dt)

    if body.get("end"):
        evt.add("dtend", datetime.fromisoformat(body["end"]).replace(tzinfo=tz))
    else:
        # All-day event: end is next day
        evt.add("dtend", start_dt + timedelta(days=1))

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


def _to_utc_compact(dt_str, tz_str):
    """Convert local ISO datetime + timezone to compact UTC format (YYYYMMDDTHHmmSSZ)."""
    import zoneinfo
    tz = zoneinfo.ZoneInfo(tz_str)
    dt = datetime.fromisoformat(dt_str).replace(tzinfo=tz)
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.strftime("%Y%m%dT%H%M%SZ")


def _default_end(body):
    """Return end datetime string, defaulting to start + 1 day if missing."""
    if body.get("end"):
        return body["end"]
    from datetime import timedelta
    start_dt = datetime.fromisoformat(body["start"])
    return (start_dt + timedelta(days=1)).isoformat()


def _build_google_url(body):
    start = _to_utc_compact(body["start"], body["timezone"])
    end = _to_utc_compact(_default_end(body), body["timezone"])
    params = {
        "action": "TEMPLATE",
        "text": body["title"],
        "dates": f"{start}/{end}",
    }
    if body.get("location"):
        params["location"] = body["location"]
    if body.get("description"):
        params["details"] = body["description"]

    qs = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    return f"https://calendar.google.com/calendar/render?{qs}"


def _build_outlook_url(body):
    start = _to_utc_compact(body["start"], body["timezone"])
    end = _to_utc_compact(_default_end(body), body["timezone"])
    # Outlook web uses ISO 8601 with Z suffix
    fmt_start = f"{start[:4]}-{start[4:6]}-{start[6:11]}:{start[11:13]}:{start[13:]}"
    fmt_end = f"{end[:4]}-{end[4:6]}-{end[6:11]}:{end[11:13]}:{end[13:]}"
    params = {
        "rru": "addevent",
        "subject": body["title"],
        "startdt": fmt_start,
        "enddt": fmt_end,
    }
    if body.get("location"):
        params["location"] = body["location"]
    if body.get("body"):
        params["body"] = body["description"]

    qs = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    return f"https://outlook.live.com/calendar/0/action/compose?{qs}"


def _build_html_snippet(ics_url, google_url, outlook_url):
    return (
        f'<b>Add to Calendar:</b> '
        f'<a href="{google_url}">Google</a> | '
        f'<a href="{outlook_url}">Outlook</a> | '
        f'<a href="{ics_url}">Download .ics</a>'
    )


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