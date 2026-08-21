import base64
import json
import os
from typing import Any, Dict, Literal

import functions_framework
import vertexai
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from pydantic import BaseModel, Field
from vertexai.generative_models import GenerationConfig, GenerativeModel, Part

TARGET_SENDER = os.environ.get("TARGET_SENDER", "vendor-billing@trusted-partner.com")

vertexai.init(
    project=os.environ.get("GCP_PROJECT_ID", "your-gcp-project"),
    location="us-east1",
)


class DocumentAnalysis(BaseModel):
    document_type: Literal["PURCHASE_ORDER", "INVOICE", "UNSUPPORTED"] = Field(
        description="The classified category of the document."
    )
    confidence_score: int = Field(
        ge=0,
        le=100,
        description="Confidence percentage of the extraction and classification accuracy.",
    )
    extracted_data: Dict[str, Any] = Field(
        description="Key-value pairs extracted from the document layout."
    )


def get_gmail_user_credentials():
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN")
    client_id = os.environ.get("GMAIL_CLIENT_ID")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET")

    if not (refresh_token and client_id and client_secret):
        raise ValueError("Missing Gmail User OAuth environment variables.")

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    )
    creds.refresh(Request())
    return creds


def extract_pdf_from_gmail_message(service, user_id, msg) -> bytes | None:
    parts = msg.get("payload", {}).get("parts", [])
    for part in parts:
        if part.get("filename", "").endswith(".pdf"):
            attachment_id = part["body"].get("attachmentId")
            if attachment_id:
                attachment = (
                    service.users()
                    .messages()
                    .attachments()
                    .get(userId=user_id, messageId=msg["id"], id=attachment_id)
                    .execute()
                )
                return base64.urlsafe_b64decode(attachment["data"].encode("UTF-8"))
    return None


def analyze_document_with_gemini(pdf_bytes: bytes) -> dict:
    model = GenerativeModel("gemini-2.5-pro")
    pdf_part = Part.from_data(data=pdf_bytes, mime_type="application/pdf")

    prompt = """
    Analyze this PDF document carefully:
    1. Identify the document type strictly as: 'PURCHASE_ORDER', 'INVOICE', or 'UNSUPPORTED'.
    2. Extract all structural fields (e.g., line items, totals, dates, vendor info) into a clean JSON dictionary under 'extracted_data'.
    3. Provide an overall confidence score from 0 to 100 for the extraction.
    """

    config = GenerationConfig(
        response_mime_type="application/json",
        response_schema=DocumentAnalysis.model_json_schema(),
    )

    response = model.generate_content([pdf_part, prompt], generation_config=config)
    return json.loads(response.text)


@functions_framework.http
def process_pdf_agent(request):
    request_json = request.get_json()
    if not request_json:
        return ("Invalid Request Payload", 400)

    if "message" not in request_json:
        return ("Unsupported trigger structure", 400)

    pubsub_message = request_json["message"]
    encoded_data = pubsub_message.get("data")

    if not encoded_data:
        return ("Ignored: Empty Pub/Sub data payload", 200)

    data_str = base64.b64decode(encoded_data).decode("utf-8")
    if not data_str.strip():
        return ("Ignored: Blank decoded Pub/Sub payload", 200)

    try:
        notification_data = json.loads(data_str)
    except json.JSONDecodeError:
        print(f"Warning: Received non-JSON payload: {data_str}")
        return ("Ignored: Payload is not valid JSON", 200)

    email_address = notification_data.get("emailAddress")
    if not email_address:
        return ("Ignored: Notification missing emailAddress", 200)

    credentials = get_gmail_user_credentials()
    service = build("gmail", "v1", credentials=credentials)

    query = f"from:{TARGET_SENDER} has:attachment filename:pdf"
    results = (
        service.users()
        .messages()
        .list(userId=email_address, q=query, maxResults=1)
        .execute()
    )
    messages = results.get("messages", [])

    if not messages:
        return ("No messages found in inbox.", 200)

    msg_id = messages[0]["id"]
    msg = (
        service.users()
        .messages()
        .get(userId=email_address, id=msg_id, format="full")
        .execute()
    )

    headers = msg.get("payload", {}).get("headers", [])
    sender = next((h["value"] for h in headers if h["name"] == "From"), "Unknown Sender")
    subject = next((h["value"] for h in headers if h["name"] == "Subject"), "No Subject")
    snippet = msg.get("snippet", "No snippet available")

    pdf_bytes = extract_pdf_from_gmail_message(service, email_address, msg)
    if not pdf_bytes:
        return ("No valid PDF attachment bytes found.", 400)

    analysis_result = analyze_document_with_gemini(pdf_bytes)

    doc_type = analysis_result.get("document_type")
    confidence = analysis_result.get("confidence_score", 0)
    extracted_data = analysis_result.get("extracted_data", {})


    return ("Email processed and printed to logs successfully.", 200)
