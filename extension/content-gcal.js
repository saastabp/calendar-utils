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
  const bubble = document.querySelector('[data-eventid]')
    || document.querySelector('[role="dialog"]');

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

  if (!title || !start) return null;

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
  // The event title is typically the most prominent text / heading in the bubble
  const candidates = [
    container.querySelector('[data-eventid]')?.getAttribute("aria-label"),
    container.querySelector('span[role="heading"]')?.textContent,
    container.querySelector('[data-eventid] span')?.textContent,
    container.querySelector('h1, h2, h3')?.textContent,
  ];

  for (const c of candidates) {
    if (c && c.trim()) {
      // aria-label often contains "Event: Title, date...", extract just the title
      const cleaned = c.replace(/^Event:\s*/i, "").split(/,\s*\d/).shift();
      return cleaned.trim();
    }
  }
  return "";
}

function extractDateTime(container) {
  const result = { start: "", end: "", timezone: guessTimezone() };

  // Look for elements with date/time info via aria-labels
  const timeEl = container.querySelector('time, [data-datestring], [aria-label*="to"]');
  if (timeEl) {
    const label = timeEl.getAttribute("aria-label") || timeEl.textContent || "";
    const parsed = parseDateTimeString(label);
    if (parsed) return { ...parsed, timezone: result.timezone };
  }

  // Fallback: scan all text nodes for date-like patterns
  const allText = container.textContent || "";
  const dateMatch = allText.match(
    /(\w+day,\s+\w+\s+\d+,\s+\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)\s*(?:[–-]\s*(\d{1,2}:\d{2}\s*[AP]M))?/i
  );

  if (dateMatch) {
    const dateStr = dateMatch[1];
    const startTime = dateMatch[2];
    const endTime = dateMatch[3] || "";

    result.start = toISODateTime(dateStr, startTime);
    if (endTime) {
      result.end = toISODateTime(dateStr, endTime);
    }
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
  const locEl = container.querySelector('[data-location], [aria-label*="Location"]');
  if (locEl) return locEl.textContent.trim();

  // Look for map links or address-like text
  const mapLink = container.querySelector('a[href*="maps.google"]');
  if (mapLink) return mapLink.textContent.trim();

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
    const combined = `${dateStr.trim()} ${timeStr.trim()}`;
    const d = new Date(combined);
    if (isNaN(d.getTime())) return "";
    // Format as local ISO without timezone offset
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