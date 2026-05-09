import json
import logging
import os
import re
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
    """
    Handles AWS Lambda function execution, processes the input event to generate calendar invites,
    and returns the corresponding URLs and HTML snippet.

    This function parses the incoming request to extract the necessary data for generating an
    ICS calendar file, validates the data, uploads the file to an S3 bucket, and constructs URLs for
    different platforms (ICS, Google Calendar, and Outlook). It also creates an HTML snippet for embedding
    these links. In case of errors, it returns appropriate HTTP response codes and error messages.

    :param event: The AWS Lambda function event object containing input data
        for processing.
    :type event: dict
    :param context: The AWS Lambda context object containing runtime
        information.
    :type context: object
    :return: A response dictionary containing the ICS file URL, Google Calendar link,
        Outlook link, and HTML snippet, or an error message in case of failure.
    :rtype: dict
    """
    try:
        body = _parse_body(event)
        _validate(body)

        ics_bytes = _generate_ics(body)
        ics_url = _upload_to_s3(ics_bytes, body)
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
    """
    Parses the body of an event, decoding it if it is base64 encoded, and attempts to parse it
    as JSON if it is a string. Raises an exception if the string body is not valid JSON.

    :param event: The event dictionary containing the information about the request. Typically,
        it includes the keys ``"body"`` (the raw body of the request as a string) and
        ``"isBase64Encoded"`` (a boolean indicating whether the body is base64 encoded).
    :type event: dict
    :raises ValueError: If the body is a string and it contains invalid JSON.
    :return: The parsed body of the event, which may be a Python dictionary (if the body is valid JSON),
        or the original raw body if it is not a string.
    :rtype: Any
    """
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
    """
    Validates the provided data dictionary to ensure all required fields are present and
    correctly formatted. If validation fails, an appropriate exception is raised.

    :param body: Dictionary containing the data to validate.
    :type body: dict
    :raises ValueError: If any required fields are missing in the `body`.
    :raises ValueError: If the `timezone` field is invalid or not found.
    :raises ValueError: If the `start` or `end` fields are not properly formatted ISO 8601
        datetime strings.
    """
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
    """
    Generates an iCalendar (ICS) file content based on the provided event details. The event data should
    include essential information such as title, start time, timezone, and optionally end time, location,
    and description. If the end time is not provided, the event will be treated as an all-day event.

    :param body: A dictionary containing event details.
        - timezone (str): The timezone string in which the event occurs.
        - title (str): The title of the event.
        - start (str): The start datetime of the event in ISO 8601 format.
        - end (str, optional): The end datetime of the event in ISO 8601 format. Defaults to None.
        - location (str, optional): The location of the event. Defaults to None.
        - description (str, optional): A textual description of the event. Defaults to None.
    :return: The iCalendar (ICS) file content as a byte string.
    :rtype: bytes
    """
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


def _upload_to_s3(ics_bytes, body):
    """
    Uploads an iCalendar (.ics) file to an Amazon S3 bucket and tags the object
    with an expiration date based on the event's end time.

    The expiration date is calculated to be 7 days after the event ends. If the
    `end` attribute is not provided in the `body`, the `start` attribute is used
    instead. The method ensures that the expiration timestamp is timezone-aware.
    After the file is uploaded to S3, the URL of the uploaded file is returned.

    :param ics_bytes: The iCalendar file content as bytes to be uploaded to the
        S3 bucket.
    :type ics_bytes: bytes
    :param body: A dictionary containing event metadata, including the `start`
        and optionally an `end` timestamp in ISO 8601 format.
    :type body: dict
    :return: The URL of the uploaded .ics file in the specified S3 bucket.
    :rtype: str
    """
    from datetime import timedelta

    bucket = os.environ["ICS_BUCKET_NAME"]
    base_url = os.environ["ICS_BASE_URL"]
    key = f"events/{uuid.uuid4()}.ics"

    # Expire 7 days after the event ends
    end_str = body.get("end") or body["start"]
    event_end = datetime.fromisoformat(end_str)
    expires = event_end + timedelta(days=7)
    # Ensure timezone-aware for S3
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)

    expires_iso = expires.strftime("%Y-%m-%dT%H:%M:%SZ")

    download_name = _sanitize_filename(body.get("title")) + ".ics"

    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=ics_bytes,
        ContentType="text/calendar",
        ContentDisposition=f'attachment; filename="{download_name}"',
        Tagging=f"expires={expires_iso}",
    )
    logger.info("Uploaded to s3://%s/%s (expires %s)", bucket, key, expires_iso)
    return f"{base_url}/{key}"


def _sanitize_filename(title):
    """Sanitize an event title for use in a Content-Disposition filename."""
    if not title:
        return "event"
    cleaned = re.sub(r'[\x00-\x1f"\\/:*?<>|]+', "_", title).strip(" ._")
    cleaned = re.sub(r"\s+", " ", cleaned)[:80]
    return cleaned or "event"


def _to_utc_compact(dt_str, tz_str):
    """
    Converts a given datetime string and time zone to a compact UTC datetime format.

    This function takes an ISO 8601 datetime string and a time zone string, converts
    the datetime to the specified time zone, and then adjusts it to UTC. The result
    is formatted as a compact UTC datetime string in the format "YYYYMMDDTHHMMSSZ".

    :param dt_str: The ISO 8601 datetime string to be converted.
    :type dt_str: str
    :param tz_str: The time zone string corresponding to the input datetime.
    :type tz_str: str
    :return: A compact UTC datetime string in the format "YYYYMMDDTHHMMSSZ".
    :rtype: str
    """
    import zoneinfo
    tz = zoneinfo.ZoneInfo(tz_str)
    dt = datetime.fromisoformat(dt_str).replace(tzinfo=tz)
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.strftime("%Y%m%dT%H%M%SZ")


def _default_end(body):
    """
    Calculates the default end datetime based on the given start datetime
    if an explicit end datetime is not provided in the input.

    :param body: Dictionary containing details of the datetime. It should
                 include a 'start' key with an ISO 8601-formatted datetime
                 string. Optionally, it may include an 'end' key with an
                 ISO 8601-formatted datetime string.
    :type body: dict
    :return: If 'end' is not specified in the body dictionary, this method
             computes the end datetime as 1 day from the given start datetime
             (in ISO 8601 format). If 'end' is specified, it returns the
             value of 'end' unchanged.
    :rtype: str
    """
    if body.get("end"):
        return body["end"]
    from datetime import timedelta
    start_dt = datetime.fromisoformat(body["start"])
    return (start_dt + timedelta(days=1)).isoformat()


def _build_google_url(body):
    """
    Constructs a Google Calendar URL based on the provided event details.

    This method generates a URL for creating a new event on Google Calendar.
    The event details such as title, start time, end time, location, and
    description are extracted from the input `body` dictionary.

    :param body: A dictionary containing event details for constructing the
                 Google Calendar URL. It must include the following keys:
                 - "start": The start time of the event.
                 - "timezone": The timezone of the event.
                 - "title": The title of the event.
                 Optionally, it may include:
                 - "location": The location of the event.
                 - "description": A description of the event.
    :type body: dict

    :return: A string containing the generated Google Calendar event creation
             URL query string.
    :rtype: str
    """
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
    """
    Constructs a URL for creating a new event in the Outlook web calendar.

    This function compiles all required event details, such as start time, end time,
    title, and optional fields like location or description, into a query string
    that conforms to the Outlook web calendar URL format. Start and end times are
    converted to ISO 8601 format with the 'Z' suffix (UTC timezone) before being
    embedded into the generated URL.

    :param body: A dictionary containing event details. Must include the "start"
        field for the event's start date/time in UTC format, "timezone" for the
        event's time zone, and "title" for the event's name. Optionally, may
        include "location" for the event's location and "description" for
        additional event details.
    :type body: dict
    :return: A fully constructed URL string suitable for creating a calendar
        event in Outlook web.
    :rtype: str
    """
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
    """
    Generates an HTML snippet for adding an event to a calendar. The snippet
    includes links to Google Calendar, Outlook Calendar, and a downloadable .ics
    file.

    :param ics_url: URL to download the .ics file.
    :type ics_url: str
    :param google_url: URL to add the event to Google Calendar.
    :type google_url: str
    :param outlook_url: URL to add the event to Outlook Calendar.
    :type outlook_url: str
    :return: A string containing the HTML snippet with calendar links.
    :rtype: str
    """
    return (
        f'<b>Add to Calendar:</b> '
        f'<a href="{google_url}">Google</a> | '
        f'<a href="{outlook_url}">Outlook</a> | '
        f'<a href="{ics_url}">Download .ics</a>'
    )


def _response(status_code, body):
    """
    Constructs a standardized HTTP response with appropriate headers and body.

    :param status_code: HTTP status code for the response.
    :type status_code: int
    :param body: The content to be included in the response body.
    :type body: dict
    :return: A dictionary representing the HTTP response with `statusCode`,
        `headers`, and `body` keys.
    :rtype: dict
    """
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,x-api-key",
        },
        "body": json.dumps(body),
    }