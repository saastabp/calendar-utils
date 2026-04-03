const LOG_PREFIX = "[CalLinkGen:Popup]";

function log(...args) {
  console.log(LOG_PREFIX, ...args);
}

function logError(msg, err) {
  console.error(LOG_PREFIX, msg, err);
  if (err && err.stack) {
    console.error(LOG_PREFIX, "Stack trace:", err.stack);
  }
}

const statusEl = document.getElementById("status");
const previewEl = document.getElementById("event-preview");
const resultsEl = document.getElementById("results");
const generateBtn = document.getElementById("generate-btn");

let currentEventData = null;

document.addEventListener("DOMContentLoaded", init);

async function init() {
  try {
    if (!CONFIG.API_URL || !CONFIG.API_KEY) {
      showStatus("Extension not configured. Set API_URL and API_KEY in config.js.", "error");
      return;
    }

    showStatus("Reading event data...", "loading");
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

    if (!tab) {
      showStatus("No active tab found.", "error");
      return;
    }

    const isSupported =
      tab.url.includes("calendar.google.com") ||
      tab.url.includes("linkedin.com/events");

    if (!isSupported) {
      showStatus("Open a Google Calendar event or LinkedIn event page first.", "error");
      return;
    }

    chrome.tabs.sendMessage(tab.id, { action: "getEventData" }, (response) => {
      if (chrome.runtime.lastError) {
        logError("Message send failed", chrome.runtime.lastError);
        showStatus("Could not communicate with the page. Try refreshing.", "error");
        return;
      }

      if (!response || !response.success) {
        const errorMsg = response?.error || "No event data found.";
        log("Content script returned error:", errorMsg);
        showStatus(errorMsg, "error");
        return;
      }

      currentEventData = response.data;
      showEventPreview(currentEventData);
    });
  } catch (err) {
    logError("Initialization failed", err);
    showStatus(`Error: ${err.message}`, "error");
  }
}

function showEventPreview(data) {
  statusEl.classList.add("hidden");
  previewEl.classList.remove("hidden");

  document.getElementById("event-title").textContent = data.title;

  let whenText = data.start || "Unknown";
  if (data.end) whenText += ` - ${data.end}`;
  if (data.timezone) whenText += ` (${data.timezone})`;
  document.getElementById("event-when").textContent = whenText;

  const locationRow = document.getElementById("event-location-row");
  if (data.location) {
    document.getElementById("event-location").textContent = data.location;
    locationRow.classList.remove("hidden");
  } else {
    locationRow.classList.add("hidden");
  }

  const descRow = document.getElementById("event-description-row");
  if (data.description) {
    const truncated = data.description.length > 100
      ? data.description.substring(0, 100) + "..."
      : data.description;
    document.getElementById("event-description").textContent = truncated;
    descRow.classList.remove("hidden");
  } else {
    descRow.classList.add("hidden");
  }

  generateBtn.addEventListener("click", handleGenerate);
}

async function handleGenerate() {
  if (!currentEventData) return;

  generateBtn.disabled = true;
  generateBtn.textContent = "Generating...";

  try {
    log("Sending event data to API:", currentEventData);

    const response = await fetch(CONFIG.API_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": CONFIG.API_KEY,
      },
      body: JSON.stringify(currentEventData),
    });

    if (!response.ok) {
      const errorBody = await response.text();
      throw new Error(`API returned ${response.status}: ${errorBody}`);
    }

    const result = await response.json();
    log("API response:", result);
    showResults(result);
  } catch (err) {
    logError("API call failed", err);
    showStatus(`Failed to generate link: ${err.message}`, "error");
    generateBtn.disabled = false;
    generateBtn.textContent = "Generate Link";
  }
}

function showResults(result) {
  generateBtn.classList.add("hidden");
  resultsEl.classList.remove("hidden");

  document.getElementById("ics-url").value = result.ics_url;
  document.getElementById("html-snippet").value = result.html_snippet;

  document.querySelectorAll(".btn-copy").forEach((btn) => {
    btn.addEventListener("click", handleCopy);
  });
}

async function handleCopy(event) {
  const btn = event.currentTarget;
  const targetId = btn.dataset.target;
  const targetEl = document.getElementById(targetId);

  try {
    await navigator.clipboard.writeText(targetEl.value);
    btn.textContent = "Copied!";
    btn.classList.add("copied");
    log("Copied to clipboard:", targetId);

    setTimeout(() => {
      btn.textContent = "Copy";
      btn.classList.remove("copied");
    }, 2000);
  } catch (err) {
    logError("Clipboard write failed", err);
    // Fallback: select the text
    targetEl.select();
    btn.textContent = "Select All";
  }
}

function showStatus(message, type) {
  statusEl.textContent = message;
  statusEl.className = `status ${type}`;
  statusEl.classList.remove("hidden");
  previewEl.classList.add("hidden");
  resultsEl.classList.add("hidden");
}