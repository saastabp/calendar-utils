/* Content script for Google Calendar event detail pages.
 *
 * Google Calendar is a SPA with heavily obfuscated class names.
 * We rely on aria-labels, data attributes, and semantic structure
 * which are more stable than class names. This will still need
 * maintenance as Google updates their UI.
 */

const LOG_PREFIX = "[CalLinkGen:GCal]";

function log(...args) {
  console.log(LOG_PREFIX, ...args);
}

function logError(msg, err) {
  console.error(LOG_PREFIX, msg, err);
  if (err && err.stack) {
    console.error(LOG_PREFIX, "Stack trace:", err.stack);
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.action !== "getEventData") return false;

  try {
    const data = scrapeEventData();
    if (data) {
      log("Scraped event data:", data);
      sendResponse({ success: true, data });
    } else {
      log("No event detail panel found");
      sendResponse({ success: false, error: "No event detail panel found. Open an event first." });
    }
  } catch (err) {
    logError("Failed to scrape event data", err);
    sendResponse({ success: false, error: `Scraping failed: ${err.message}` });
  }

  return false;
});

function scrapeEventData() {
  // Strategy 1: Event detail bubble/popup (clicking an event on the calendar)
  // Use the stable dialog with data-chips-dialog attribute
  const bubble = document.querySelector('[role="dialog"][data-chips-dialog="true"]');

  if (bubble) {
    return scrapeFromBubble(bubble);
  }

  // Strategy 2: Full event detail page (/eventedit/ or /event/ URL)
  if (window.location.pathname.includes("/eventedit/") || window.location.pathname.includes("/event/")) {
    return scrapeFromEventPage();
  }

  return null;
}

function scrapeFromBubble(bubble) {
  const title = extractTitle(bubble);
  const { start, end, timezone } = extractDateTime(bubble);
  const location = extractLocation(bubble);
  const description = extractDescription(bubble);

  if (!title) {
    log("Could not extract title from bubble");
    return null;
  }
  if (!start) {
    log("Could not extract start date/time from bubble");
    return null;
  }

  return { title, start, end, timezone, location, description };
}

function scrapeFromEventPage() {
  const title = document.querySelector('[data-key="title"] input')?.value
    || document.querySelector('input[aria-label="Title"]')?.value
    || document.querySelector('[aria-label*="Title"]')?.textContent?.trim();

  const dateInfo = extractDateTimeFromPage();
  const location = document.querySelector('[data-key="location"] input')?.value
    || document.querySelector('input[aria-label="Location"]')?.value
    || "";
  const description = document.querySelector('[data-key="description"] textarea')?.value
    || document.querySelector('textarea[aria-label="Description"]')?.value
    || "";

  if (!title || !dateInfo.start) return null;

  return { title, ...dateInfo, location, description };
}

function extractTitle(container) {
  // Google Calendar uses a heading with id="rAECCd" inside the bubble
  const candidates = [
    container.querySelector('#rAECCd')?.textContent,
    container.querySelector('span[role="heading"]')?.textContent,
    container.querySelector('[data-eventid]')?.getAttribute("aria-label"),
  ];

  for (const c of candidates) {
    if (c && c.trim()) {
      // aria-label may contain "Event: Title, date...", extract just the title
      const cleaned = c.replace(/^Event:\s*/i, "").split(/,\s*\d/).shift();
      return cleaned.trim();
    }
  }
  return "";
}

function extractDateTime(container) {
  const result = { start: "", end: "", timezone: guessTimezone() };

  // Google Calendar puts date/time in the #xDetDlgWhen container
  // Format examples:
  //   "Saturday, April 4⋅6:00 – 9:00pm"
  //   "Saturday, April 4⋅6:00am – 9:00pm"
  //   "Friday, April 3, 2026"  (all-day event)
  //   "April 4 – 5, 2026" (multi-day)
  const whenEl = container.querySelector('#xDetDlgWhen');
  const whenText = whenEl ? whenEl.textContent.trim() : "";
  log("When text:", whenText);

  if (!whenText) return result;

  // Try: "DayName, Month Day⋅StartTime – EndTime" (same day, with times)
  // The ⋅ or · separator may appear, or just whitespace
  const sameDay = whenText.match(
    /(?:\w+day,\s+)?(\w+\s+\d+)(?:,\s*(\d{4}))?[⋅·,\s]+(\d{1,2}(?::\d{2})?\s*(?:[ap]m)?)\s*[–\-]\s*(\d{1,2}(?::\d{2})?\s*(?:[ap]m)?)/i
  );

  if (sameDay) {
    const dateStr = sameDay[1]; // "April 4"
    const year = sameDay[2] || new Date().getFullYear();
    let startTime = sameDay[3].trim(); // "6:00" or "6:00am"
    let endTime = sameDay[4].trim();   // "9:00pm"

    // If start time has no am/pm, infer from end time
    if (!/[ap]m/i.test(startTime) && /[ap]m/i.test(endTime)) {
      const endSuffix = endTime.match(/[ap]m/i)[0];
      startTime += endSuffix;
    }

    result.start = toISODateTime(`${dateStr}, ${year}`, startTime);
    result.end = toISODateTime(`${dateStr}, ${year}`, endTime);
    return result;
  }

  // Try: all-day event "DayName, Month Day" or "DayName, Month Day, Year"
  const allDay = whenText.match(/(?:\w+day,\s+)?(\w+\s+\d+)(?:,\s*(\d{4}))?/);
  if (allDay) {
    const dateStr = allDay[1];
    const year = allDay[2] || new Date().getFullYear();
    result.start = toISODateTime(`${dateStr}, ${year}`, "12:00am");
    return result;
  }

  return result;
}

function extractDateTimeFromPage() {
  const result = { start: "", end: "", timezone: guessTimezone() };

  const dateInput = document.querySelector('input[aria-label*="date" i]')
    || document.querySelector('[data-key="startDate"] input');
  const startTimeInput = document.querySelector('input[aria-label*="start time" i]')
    || document.querySelector('[data-key="startTime"] input');
  const endTimeInput = document.querySelector('input[aria-label*="end time" i]')
    || document.querySelector('[data-key="endTime"] input');

  if (dateInput && startTimeInput) {
    result.start = toISODateTime(dateInput.value, startTimeInput.value);
    if (endTimeInput) {
      result.end = toISODateTime(dateInput.value, endTimeInput.value);
    }
  }

  return result;
}

function extractLocation(container) {
  // Google Calendar uses #xDetDlgLoc for location
  const locEl = container.querySelector('#xDetDlgLoc');
  if (locEl) {
    // Get venue name and address separately, join them
    const parts = [];
    locEl.querySelectorAll('.UfeRlc, .AzuXid').forEach((el) => {
      const text = el.textContent.trim();
      // Skip the "Location:" label
      if (text && !text.startsWith("Location:")) {
        parts.push(text);
      }
    });
    if (parts.length > 0) return parts.join(", ");
  }

  // Fallback
  const fallback = container.querySelector('[aria-label*="Location"], [data-location]');
  if (fallback) return fallback.textContent.trim();

  return "";
}

function extractDescription(container) {
  const descEl = container.querySelector('[data-description], [class*="description"]');
  if (descEl) return descEl.textContent.trim();
  return "";
}

function parseDateTimeString(str) {
  // Handle strings like "Wednesday, April 10, 2026, 9:00 AM to 10:00 AM"
  const match = str.match(
    /(\w+\s+\d+,?\s*\d{4}),?\s+(\d{1,2}:\d{2}\s*[AP]M)\s*(?:to|[–-])\s*(\d{1,2}:\d{2}\s*[AP]M)/i
  );
  if (!match) return null;

  return {
    start: toISODateTime(match[1], match[2]),
    end: toISODateTime(match[1], match[3]),
  };
}

function toISODateTime(dateStr, timeStr) {
  try {
    // Normalize time: ensure space before am/pm for Date.parse compatibility
    const normalizedTime = timeStr.trim().replace(/(\d)(am|pm)/i, "$1 $2");
    const combined = `${dateStr.trim()} ${normalizedTime}`;
    log("Parsing date/time:", combined);
    const d = new Date(combined);
    if (isNaN(d.getTime())) {
      log("Failed to parse date/time string:", combined);
      return "";
    }
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:00`;
  } catch (err) {
    logError("Failed to parse date/time", err);
    return "";
  }
}

function guessTimezone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "America/New_York";
}