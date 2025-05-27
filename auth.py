# auth.py
import os, json
from google.oauth2 import service_account
from google.auth.transport.requests import Request

# The Firestore REST scope:
SCOPES = ["https://www.googleapis.com/auth/datastore"]

# 1) Load your JSON key from the Railway env-var
service_account_info = json.loads(os.environ["SERVICE_ACCOUNT_JSON"])

# 2) Build credentials object once
credentials = service_account.Credentials.from_service_account_info(
    service_account_info,
    scopes=SCOPES
)
request = Request()

def get_service_account_token():
    """
    Returns a fresh OAuth2 Bearer token (auto-refreshing under the hood).
    """
    credentials.refresh(request)
    return credentials.token
