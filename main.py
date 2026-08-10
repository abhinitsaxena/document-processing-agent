import base64
import json
import os
import functions_framework
from typing import Literal, Dict, Any
from pydantic import BaseModel, Field
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
#from google.auth import default
import vertexai
from vertexai.generative_models import GenerativeModel, Part, GenerationConfig
def extract_pdf_from_gmail_message(service, user_id, msg) -> bytes:
    # Helper to parse multipart MIME and extract PDF attachment bytes
    parts = msg.get("payload", {}).get("parts", [])
    for part in parts:
        if part.get("filename", "").endswith(".pdf"):
            attachment_id = part["body"].get("attachmentId")
            if attachment_id:
                attachment = service.users().messages().attachments().get(
                    userId=user_id, messageId=msg["id"], id=attachment_id
                ).execute()
                return base64.urlsafe_b64decode(attachment["data"].encode("UTF-8"))
    return None
def get_gmail_user_credentials():
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN")
    client_id = os.environ.get("GMAIL_CLIENT_ID")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET")

    if refresh_token and client_id and client_secret:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=["https://www.googleapis.com/auth/gmail.readonly"]
        )
        creds.refresh(Request())
        return creds
    else:
        raise ValueError("Missing Gmail User OAuth environment variables.")

TARGET_SENDER = os.environ.get("TARGET_SENDER", "vendor-billing@trusted-partner.com")

@functions_framework.http
def process_pdf_agent(request):
    request_json = request.get_json()
    if not request_json:
        return ("Invalid Request Payload", 400)

    # 1. Handle Gmail Pub/Sub Push Trigger
    if "message" in request_json:
        pubsub_message = request_json["message"]
        encoded_data = pubsub_message.get("data")
        
        if not encoded_data:
            return ("Ignored: Empty Pub/Sub data payload", 200)

        data_str = base64.b64decode(encoded_data).decode("utf-8")
        if not data_str.strip():
            return ("Ignored: Blank decoded Pub/Sub payload", 200)

        #notification_data = json.loads(data_str)
        # SAFELY PARSE JSON: Catch any invalid or plain-text messages without crashing
        try:
            notification_data = json.loads(data_str)
        except json.JSONDecodeError:
            print(f"Warning: Received non-JSON payload: {data_str}")
            return ("Ignored: Payload is not valid JSON", 200)        
        email_address = notification_data.get("emailAddress")
        if not email_address:
            return ("Ignored: Notification missing emailAddress", 200)

        # Authenticate with User OAuth2 credentials
        credentials = get_gmail_user_credentials()
        service = build("gmail", "v1", credentials=credentials)

        # Cost-saving filter: Check for emails strictly from target sender with PDF attachments
        query = f"from:{TARGET_SENDER} has:attachment filename:pdf"
        results = service.users().messages().list(userId=email_address, q=query, maxResults=1).execute()
#        results = service.users().messages().list(userId=email_address, maxResults=1).execute()
        messages = results.get("messages", [])

        if not messages:
            return ("No messages found in inbox.", 200)

        # Fetch the latest email details
        msg_id = messages[0]["id"]
        msg = service.users().messages().get(userId=email_address, id=msg_id, format="full").execute()
        
        # Extract headers (Sender, Subject, Date)
        headers = msg.get("payload", {}).get("headers", [])
        sender = next((h["value"] for h in headers if h["name"] == "From"), "Unknown Sender")
        subject = next((h["value"] for h in headers if h["name"] == "Subject"), "No Subject")
        snippet = msg.get("snippet", "No snippet available")
       #Extract PDF attachment payload bytes from Gmail message parts
        pdf_bytes = extract_pdf_from_gmail_message(service, email_address, msg)
        if not pdf_bytes:
             return ("No valid PDF attachment bytes found.", 400)
        #else:
        #return ("Unsupported trigger structure", 400)

        # 3. Process the PDF through Gemini AI with Structured Output Schemas
        analysis_result = analyze_document_with_gemini(pdf_bytes)

        doc_type = analysis_result.get("document_type")
        confidence = analysis_result.get("confidence_score", 0)
        extracted_data = analysis_result.get("extracted_data", {})

        # SIMPLE PRINT: Output the core email content directly to Cloud Run logs
        print(f"--- NEW EMAIL RECEIVED ---")
        print(f"From: {sender}")
        print(f"Subject: {subject}")
        print(f"Snippet: {snippet}")
        print(f"Attachment file name: {extracted_data}")
        print(f"Document Type: {doc_type}")
        print(f"Confidence Score: {confidence}")
        print(f"--------------------------")

        return ("Email processed and printed to logs successfully.", 200)

    return ("Unsupported trigger structure", 400)


# Initialize Vertex AI globally (or inside the function to prevent cold-start failures)

vertexai.init(project=os.environ.get("GCP_PROJECT_ID", "your-gcp-project"), location="us-east1")



# Define strict structured output schema using Pydantic
class DocumentAnalysis(BaseModel):
    document_type: Literal["PURCHASE_ORDER", "INVOICE", "UNSUPPORTED"] = Field(
        description="The classified category of the document."
    )
    confidence_score: int = Field(
        ge=0, le=100, 
        description="Confidence percentage of the extraction and classification accuracy."
    )
    extracted_data: Dict[str, Any] = Field(
        description="Key-value pairs extracted from the document layout."
    )

# TARGET_SENDER = os.environ.get("TARGET_SENDER", "vendor-billing@trusted-partner.com")


# @functions_framework.http
# def process_pdf_agent(request):
#     request_json = request.get_json()
#     if not request_json:
#         return ("Invalid Request Payload", 400)

#     pdf_bytes = None
#     sender_info = None
#     trigger_source = "email" # default or dynamic

#     # 1. Handle Chat Trigger or direct payload execution
#     if "file_bytes" in request_json:
#         trigger_source = "chat"
#         pdf_bytes = base64.b64decode(request_json["file_bytes"])
#         sender_info = request_json.get("sender_info", "chat_user@company.com")

#     # 2. Handle Gmail Pub/Sub Push Trigger
#     elif "message" in request_json:
#         trigger_source = "email"
#         pubsub_message = request_json["message"]

#                 # Retrieve the raw data, defaulting to an empty string if missing
#         raw_data = pubsub_message.get("data", "")
#         if not raw_data:
#             return ("Ignored: Pub/Sub message contains no data payload.", 200)

#         try:
#             # Pad the base64 string to ensure it decodes correctly
#             padded_raw_data = raw_data + "=" * ((4 - len(raw_data) % 4) % 4)
#             # Use urlsafe_b64decode to handle Gmail's base64url encoding
#             data_str = base64.b64decode(pubsub_message["data"]).decode("utf-8")
#         except Exception as decode_err:
#             print(f"[ERROR] Failed to base64-decode data field: {decode_err}")
#             return ("Invalid base64 payload structure", 400)
#  # Check if the decoded payload is empty
#         if not data_str.strip():
#             return ("Ignored: Decoded Pub/Sub data payload is empty.", 200)

#         try:
#             notification_data = json.loads(data_str)
#         except json.JSONDecodeError as json_err:
#             print(f"[ERROR] JSON decode failed for payload '{data_str}': {json_err}")
#             return ("Invalid JSON format inside decoded payload", 400)

#         email_address = notification_data.get("emailAddress")
#         if not email_address:
#             return ("Ignored: No email address found in notification payload.", 200)
        
#         # Authenticate and build Gmail service
#         #credentials, _ = default(scopes=["https://www.googleapis.com/auth/gmail.readonly"])
#         #service = build("gmail", "v1", credentials=credentials)
#         credentials = get_gmail_user_credentials()
#         service = build("gmail", "v1", credentials=credentials)
#         # Cost-saving filter: Check for emails strictly from target sender with PDF attachments
#         query = f"from:{TARGET_SENDER} has:attachment filename:pdf"
#         results = service.users().messages().list(userId=email_address, q=query, maxResults=1).execute()
#         messages = results.get("messages", [])

#         if not messages:
#             return ("Ignored: Email does not match target sender or lacks a PDF attachment.", 200)

#         # Fetch the matching email details
#         msg_id = messages[0]["id"]
#         msg = service.users().messages().get(userId=email_address, id=msg_id).execute()
        
#         # Extract sender details
#         headers = msg.get("payload", {}).get("headers", [])
#         sender_info = next((h["value"] for h in headers if h["name"] == "From"), TARGET_SENDER)

#         # Extract PDF attachment payload bytes from Gmail message parts
#         pdf_bytes = extract_pdf_from_gmail_message(service, email_address, msg)
#         if not pdf_bytes:
#             return ("No valid PDF attachment bytes found.", 400)
#     else:
#         return ("Unsupported trigger structure", 400)

#     # 3. Process the PDF through Gemini AI with Structured Output Schemas
#     analysis_result = analyze_document_with_gemini(pdf_bytes)

#     doc_type = analysis_result.get("document_type")
#     confidence = analysis_result.get("confidence_score", 0)
#     extracted_data = analysis_result.get("extracted_data", {})

#     # 4. Implement Core Conditional Routing Logic
    
#     # Condition A: Unsupported Document Type
#     if doc_type not in ["PURCHASE_ORDER", "INVOICE"]:
#         send_reply(
#             trigger_source, 
#             sender_info, 
#             "Your document is not supported. A support person will reach out to you shortly."
#         )
#         create_support_ticket(
#             title="Unsupported Document Received", 
#             draft_definition=extracted_data, 
#             pdf_bytes=pdf_bytes
#         )
#         return ("Processed: Unsupported Document", 200)

#     # Condition B: High Confidence (>= 90%)
#     if confidence >= 90:
#         erp_record_id = call_erp_system_api(doc_type, extracted_data, pdf_bytes)
#         send_reply(
#             trigger_source, 
#             sender_info, 
#             f"Successfully processed your {doc_type}. System Record ID: {erp_record_id}"
#         )
    
#     # Condition C: Low Confidence (< 90%)
#     else:
#         create_support_ticket(
#             title=f"Review Required: Low Confidence {doc_type} ({confidence}%)", 
#             draft_definition=extracted_data, 
#             pdf_bytes=pdf_bytes
#         )
#         send_reply(
#             trigger_source, 
#             sender_info, 
#             f"We received your {doc_type}, but it requires manual validation. Our support team is reviewing the draft details."
#         )

#     return ("Agent workflow executed successfully", 200)


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





# def call_erp_system_api(doc_type: str, data: dict, pdf_bytes: bytes) -> str:
#     # Placeholder: Call your company's ERP API (SAP, NetSuite, etc.)
#     print(f"[ERP TOOL] Creating {doc_type} in backend system with data: {data}")
#     return "ERP-REC-98765"


# def create_support_ticket(title: str, draft_definition: dict, pdf_bytes: bytes):
#     # Placeholder: Call your ticketing system API (Jira, Zendesk, etc.)
#     print(f"[TICKET TOOL] Created support ticket [{title}] with draft payload.")


# def send_reply(source: str, target: str, message: str):
#     # Placeholder: Send reply via Gmail API or Google Chat Webhook depending on trigger source
#     print(f"[NOTIFY] Replying via [{source}] to [{target}]: {message}")
