# Calendar Utils

Chrome extension + AWS SAM backend for generating Add-to-Calendar links from Google Calendar and LinkedIn events.

Click an event, generate shareable links for Google Calendar, Outlook, and .ics download. Copy as rich text to paste directly into Gmail or other email clients.

## Architecture

- **Extension**: Manifest V3 Chrome extension that scrapes event data from Google Calendar and LinkedIn event pages
- **Backend**: AWS Lambda (Python 3.12) behind API Gateway with API key auth
- **Storage**: S3 bucket (`cal.360balancedliving.com`) for hosting .ics files

## Setup

### Prerequisites

- AWS CLI configured with appropriate credentials
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
- Google Chrome

### Deploy the backend

```bash
sam build
sam deploy
```

Note the `ApiUrl` and `ApiKeyId` from the stack outputs. Retrieve the API key value:

```bash
aws apigateway get-api-key --api-key <ApiKeyId> --include-value
```

### Install the extension

1. Clone this repository:
   ```bash
   git clone https://github.com/saastabp/calendar-utils.git
   ```

2. Create `extension/config.js` with your API credentials:
   ```js
   const CONFIG = {
     API_URL: "<ApiUrl from stack output>",
     API_KEY: "<API key value>",
   };
   ```

3. Open Chrome and go to `chrome://extensions`
4. Enable **Developer mode** (toggle in top right)
5. Click **Load unpacked** and select the `extension/` folder
6. Pin the extension by clicking the puzzle piece icon in the toolbar

## Usage

1. Open an event in Google Calendar (click on it to open the detail popup) or navigate to a LinkedIn event page
2. Click the Calendar Link Generator extension icon
3. Verify the scraped event details
4. Click **Generate Link**
5. Use the Add to Google / Add to Outlook buttons, or copy the links for email:
   - **Copy for Email** pastes as rich text with clickable links (works in Gmail)
   - Toggle **Raw HTML** for the raw markup
   - Toggle **Include .ics** to add a download link

## Project Structure

```
.
├── backend/
│   ├── app.py              # Lambda handler - generates .ics, Google/Outlook URLs
│   └── requirements.txt
├── extension/
│   ├── manifest.json
│   ├── config.js            # API credentials (gitignored)
│   ├── popup.html/js/css    # Extension popup UI
│   ├── content-gcal.js      # Google Calendar scraper
│   ├── content-linkedin.js  # LinkedIn event scraper
│   └── icons/
├── template.yaml            # SAM template
└── samconfig.toml           # SAM deploy config
```