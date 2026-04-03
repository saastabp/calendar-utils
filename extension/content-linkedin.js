/* Content script for LinkedIn event pages (linkedin.com/events/*).
 *
 * LinkedIn event pages have somewhat more stable DOM structure than
 * Google Calendar, but still use obfuscated class names. We rely
 * on semantic elements and known page structure.
 */

const LOG_PREFIX = "[CalLinkGen:LinkedIn]";

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
      log("No event data found on this page");
      sendResponse({ success: false, error: "Could not find event data on this LinkedIn page." });
    }
  } catch (err) {
    logError("Failed to scrape event data", err);
    sendResponse({ success: false, error: `Scraping failed: ${err.message}` });
  }

  return false;
});

function scrapeEventData() {
  const title = extractTitle();
  if (!title) return null;

  const { start, end, timezone } = extractDateTime();
  const location = extractLocation();
  const description = extractDescription();

  return { title, start, end, timezone, location, description };
}

function extractTitle() {
  // LinkedIn event pages use h1 for the event title
  const h1 = document.querySelector("h1");
  if (h1) return h1.textContent.trim();

  // Fallback: og:title meta tag
  const ogTitle = document.querySelector('meta[property="og:title"]');
  if (ogTitle) return ogTitle.getAttribute("content") || "";

  return "";
}

function extractDateTime() {
  const result = { start: "", end: "", timezone: guessTimezone() };

  // LinkedIn shows date/time in a details section, often with an icon
  // Look for time elements first
  const timeEls = document.querySelectorAll("time");
  if (timeEls.length >= 1) {
    const startAttr = timeEls[0].getAttribute("datetime");
    if (startAttr) {
      result.start = normalizeDateTime(startAttr);
    }
    if (timeEls.length >= 2) {
      const endAttr = timeEls[1].getAttribute("datetime");
      if (endAttr) {
        result.end = normalizeDateTime(endAttr);
      }
    }
    return result;
  }

  // Fallback: look for date strings in the event details section
  const detailsSection = document.querySelector('[class*="event-details"], [class*="event-date"]')
    || document.querySelector("main");

  if (detailsSection) {
    const text = detailsSection.textContent || "";
    const dateMatch = text.match(
      /(\w+,\s+\w+\s+\d+,\s+\d{4})\s+(?:at\s+)?(\d{1,2}:\d{2}\s*[AP]M)\s*(?:[–-]\s*(\d{1,2}:\d{2}\s*[AP]M))?/i
    );

    if (dateMatch) {
      result.start = toISODateTime(dateMatch[1], dateMatch[2]);
      if (dateMatch[3]) {
        result.end = toISODateTime(dateMatch[1], dateMatch[3]);
      }
    }
  }

  return result;
}

function extractLocation() {
  // LinkedIn events show location in the details area
  // Look for elements with location-related attributes
  const locationEl = document.querySelector('[class*="location"], [data-test-id*="location"]');
  if (locationEl) return locationEl.textContent.trim();

  // Look for "Online event" or a Zoom/Teams link
  const main = document.querySelector("main");
  if (main) {
    const links = main.querySelectorAll('a[href*="zoom.us"], a[href*="teams.microsoft"], a[href*="meet.google"]');
    if (links.length > 0) return links[0].href;

    // Check for "Online event" text
    const text = main.textContent;
    if (text.includes("Online event")) return "Online";
  }

  return "";
}

function extractDescription() {
  // LinkedIn event description/about section
  const aboutSection = document.querySelector('[class*="about-section"], [class*="description"]');
  if (aboutSection) return aboutSection.textContent.trim();

  // Fallback: og:description
  const ogDesc = document.querySelector('meta[property="og:description"]');
  if (ogDesc) return ogDesc.getAttribute("content") || "";

  return "";
}

function normalizeDateTime(isoStr) {
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return "";
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:00`;
  } catch (err) {
    logError("Failed to normalize datetime", err);
    return "";
  }
}

function toISODateTime(dateStr, timeStr) {
  try {
    const combined = `${dateStr.trim()} ${timeStr.trim()}`;
    const d = new Date(combined);
    if (isNaN(d.getTime())) return "";
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