# telegram_poller.py

import os
import time
import requests
from auth import get_service_account_token

# ── CONFIGURATION (via environment variables) ─────────────────────────────────
BOT_TOKEN        = os.environ['TELEGRAM_BOT_TOKEN']
PROJECT_ID       = os.environ['FIREBASE_PROJECT_ID']
ONBOARDING_GROUP = os.environ['ONBOARDING_GROUP_ID']  # e.g. "-1001234567890"
HQ_GROUP_ID      = os.environ['HQ_GROUP_ID']         # e.g. "-1009876543210"

# Firestore REST base URL and collection names
FIRESTORE_BASE   = f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}/databases/(default)/documents"
ACCESS_COLL      = "accessCodes"
ASSIGN_COLL      = "userCodes"

# This must point to your index page (not the formResponse link)
WEBAPP_URL       = "https://script.google.com/macros/s/AKfycbxX9rZc_WgPE9Lbz29eADET0Frt5Pk3064-nXl0F9iCZm6wAhopPAI3Z96giQTcc0w/exec"


# ── TELEGRAM “wrapper” ─────────────────────────────────────────────────────────
def telegram(method, payload):
    """
    Low-level helper for Telegram Bot API calls.
    """
    return requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        json=payload
    ).json()


def get_updates(offset):
    """
    Long-poll getUpdates; only fetch “message” and “chat_member” events.
    """
    resp = requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
        params={
            "timeout": 20,
            "offset": offset,
            "allowed_updates": ["message", "chat_member"]
        }
    ).json()
    return resp.get("result", [])


def get_member_status(chat_id, group_id):
    """
    Calls getChatMember to see if user is still a member of the specified group.
    Returns status string (“member”, “left”, etc.), or None on error.
    """
    resp = telegram("getChatMember", {
        "chat_id": group_id,
        "user_id": chat_id
    })
    return resp.get("result", {}).get("status")


# ── FIRESTORE HELPERS ──────────────────────────────────────────────────────────
def fetch_unused_code():
    """
    Queries Firestore for one unused access-code document (where used == false).
    Returns the code string, or None if none remain.
    """
    q = {
        "structuredQuery": {
            "from": [{"collectionId": ACCESS_COLL}],
            "where": {
                "fieldFilter": {
                    "field": {"fieldPath": "used"},
                    "op": "EQUAL",
                    "value": {"booleanValue": False}
                }
            },
            "limit": 1
        }
    }
    token = get_service_account_token()
    rows = requests.post(
        f"{FIRESTORE_BASE}:runQuery",
        headers={"Authorization": f"Bearer {token}"},
        json=q
    ).json()
    for r in rows:
        if "document" in r:
            return r["document"]["name"].split("/")[-1]
    return None


def mark_used(code):
    """
    Sets used = true on that code document in accessCodes.
    (We no longer call this here; it will be invoked by the Apps Script
    when the user actually enters the code on the Index page.)
    """
    url = (
        f"{FIRESTORE_BASE}/{ACCESS_COLL}/{requests.utils.quote(code)}"
        + "?updateMask.fieldPaths=used"
    )
    token = get_service_account_token()
    requests.patch(
        url,
        headers={"Authorization": f"Bearer {token}"},
        json={"fields": {"used": {"booleanValue": True}}}
    )


def get_assignment(chat_id):
    """
    Fetches userCodes/{chat_id}. Returns dict:
      { "code": <stringValue>, "codeSent": <booleanValue> }
    or None if no document exists.
    """
    url = f"{FIRESTORE_BASE}/{ASSIGN_COLL}/{chat_id}"
    token = get_service_account_token()
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 200:
        f = r.json()["fields"]
        return {
            "code": f["code"]["stringValue"],
            "codeSent": f.get("codeSent", {}).get("booleanValue", False)
        }
    return None


def upsert_assignment(chat_id, code, sent_flag):
    """
    Creates or updates userCodes/{chat_id} with fields:
      code (string), codeSent (boolean).
    """
    url = f"{FIRESTORE_BASE}/{ASSIGN_COLL}/{chat_id}"
    token = get_service_account_token()
    body = {
        "fields": {
            "code": {"stringValue": code},
            "codeSent": {"booleanValue": sent_flag}
        }
    }
    requests.patch(
        url,
        headers={"Authorization": f"Bearer {token}"},
        json=body
    )


def delete_assignment(chat_id):
    """
    Deletes the document userCodes/{chat_id}, if it exists.
    """
    url = f"{FIRESTORE_BASE}/{ASSIGN_COLL}/{chat_id}"
    token = get_service_account_token()
    requests.delete(url, headers={"Authorization": f"Bearer {token}"})


# ── MAIN POLLING LOOP ──────────────────────────────────────────────────────────
def poll():
    """
    Continuously polls for Telegram updates. Handles:
      • A user sending /start or /getcode in private DM
        → checks Onboarding-group membership, fetches or re-uses a code,
          stores it in userCodes with codeSent=True, then DMs:
          “✅ Verification complete! 🔑 <code>
           Finish signing up here: <Link>”
        → does NOT mark the code used yet—they are only “reserved” until
          they actually input it on Index.html.

      • If user leaves or (re)joins either ONBOARDING_GROUP or HQ_GROUP,
        → clears any existing assignment so they can re‐apply later.
    """
    offset = 0
    while True:
        updates = get_updates(offset)
        for upd in updates:
            offset = upd["update_id"] + 1

            if upd.get("message"):
                msg = upd["message"]
                lc = msg.get("left_chat_member")
                nc = msg.get("new_chat_member")
                chat_info = msg.get("chat", {})
                group_id = str(chat_info.get("id"))

                # 1) If someone left the ONBOARDING_GROUP, delete assignment
                if lc and group_id == ONBOARDING_GROUP:
                    delete_assignment(str(lc["id"]))

                # 2) If someone (re)joined the ONBOARDING_GROUP, delete old assignment
                if nc and group_id == ONBOARDING_GROUP:
                    delete_assignment(str(nc["id"]))

                # 3) If someone left the HQ_GROUP, delete assignment
                if lc and group_id == HQ_GROUP_ID:
                    delete_assignment(str(lc["id"]))

                # 4) (Optional) If someone joined the HQ_GROUP, you could note that here
                # if nc and group_id == HQ_GROUP_ID:
                #     pass

                # 5) Now handle a DM in private: /start or /getcode
                text = msg.get("text", "").strip().lower()
                chat = msg.get("chat", {})
                cid  = chat.get("id")

                if text in ("/start", "/getcode") and chat.get("type") == "private":
                    # Only allow if user is still in ONBOARDING_GROUP
                    status_onboard = get_member_status(cid, ONBOARDING_GROUP)
                    if status_onboard not in ("member", "administrator", "creator"):
                        delete_assignment(str(cid))
                        continue

                    # Fetch existing assignment or create a new one
                    rec = get_assignment(str(cid))
                    if rec is None or not rec.get("codeSent", False):
                        code = fetch_unused_code()
                        if not code:
                            telegram("sendMessage", {
                                "chat_id": cid,
                                "text": "❌ Sorry, we have run out of access codes right now."
                            })
                            continue

                        # **Do NOT call mark_used(code) here.**
                        # Instead, just reserve it in userCodes until they enter it on Index.html.
                        upsert_assignment(str(cid), code, True)

                        dm_text = (
                            "✅ *Verification complete!*  \n\n"
                            f"🔑 *{code}*  \n\n"
                            "Finish signing up here: [Link](%s)" % WEBAPP_URL
                        )
                        telegram("sendMessage", {
                            "chat_id": cid,
                            "text": dm_text,
                            "parse_mode": "Markdown"
                        })
                    else:
                        # They already have codeSent=true; do nothing
                        continue

        time.sleep(1)


if __name__ == "__main__":
    poll()
