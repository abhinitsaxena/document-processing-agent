# Document Processing Agent

A Google Cloud Function that automatically processes PDF attachments from Gmail emails using Gemini AI. It classifies documents as purchase orders, invoices, or unsupported types and extracts structured data from them.

## How It Works

1. A Gmail Pub/Sub push notification triggers the Cloud Function
2. The function authenticates with Gmail using OAuth2 and fetches the latest email matching the target sender
3. It extracts the PDF attachment from the email
4. Gemini 2.5 Pro analyzes the PDF and returns structured data (document type, confidence score, extracted fields)
5. Results are logged to Cloud Run logs

## Prerequisites

- Python 3.10+
- A Google Cloud project with the following APIs enabled:
  - Gmail API
  - Vertex AI API
  - Cloud Functions / Cloud Run
- Gmail OAuth2 credentials (client ID, client secret, and refresh token)
- A Gmail Pub/Sub push subscription pointed at the Cloud Function URL

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GCP_PROJECT_ID` | Yes | Your Google Cloud project ID |
| `GMAIL_CLIENT_ID` | Yes | OAuth2 client ID for Gmail access |
| `GMAIL_CLIENT_SECRET` | Yes | OAuth2 client secret for Gmail access |
| `GMAIL_REFRESH_TOKEN` | Yes | OAuth2 refresh token for the Gmail account |
| `TARGET_SENDER` | No | Email address to filter incoming mail (default: `vendor-billing@trusted-partner.com`) |

## Local Development

```bash
# Install dependencies
pip install -r requirement.txt

# Set required environment variables
export GCP_PROJECT_ID="your-gcp-project"
export GMAIL_CLIENT_ID="your-client-id"
export GMAIL_CLIENT_SECRET="your-client-secret"
export GMAIL_REFRESH_TOKEN="your-refresh-token"

# Run locally with functions-framework
functions-framework --target=process_pdf_agent --port=8080
```

## Deployment

Deploy to Google Cloud Functions (2nd gen):

```bash
gcloud functions deploy process-pdf-agent \
  --gen2 \
  --runtime=python310 \
  --trigger-http \
  --entry-point=process_pdf_agent \
  --region=us-east1 \
  --set-env-vars="GCP_PROJECT_ID=your-gcp-project,GMAIL_CLIENT_ID=...,GMAIL_CLIENT_SECRET=...,GMAIL_REFRESH_TOKEN=..." \
  --allow-unauthenticated
```

> **Note:** For production, use Secret Manager for credentials instead of plain environment variables, and remove `--allow-unauthenticated` in favor of proper IAM authentication on the Pub/Sub push subscription.

## Setting Up Gmail Pub/Sub Notifications

1. Create a Pub/Sub topic (e.g., `gmail-notifications`)
2. Grant `gmail-api-push@system.gserviceaccount.com` publish permissions on the topic
3. Create a push subscription pointing to your deployed Cloud Function URL
4. Call the Gmail API to watch the mailbox:

```bash
curl -X POST \
  "https://gmail.googleapis.com/gmail/v1/users/me/watch" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -d '{
    "topicName": "projects/YOUR_PROJECT/topics/gmail-notifications",
    "labelIds": ["INBOX"]
  }'
```

> The watch expires after 7 days. Set up a Cloud Scheduler job to renew it automatically.

## Request Format

The function expects a Pub/Sub push message:

```json
{
  "message": {
    "data": "<base64-encoded JSON with emailAddress field>"
  }
}
```

## Response Schema

Gemini returns structured output matching this schema:

```json
{
  "document_type": "PURCHASE_ORDER | INVOICE | UNSUPPORTED",
  "confidence_score": 0-100,
  "extracted_data": {
    "vendor_name": "...",
    "invoice_number": "...",
    "line_items": [],
    "total_amount": "..."
  }
}
```

## Project Structure

```
document-processing-agent/
├── main.py           # Cloud Function entry point and all processing logic
├── requirement.txt   # Python dependencies
└── README.md
```
