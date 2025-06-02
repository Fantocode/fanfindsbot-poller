# telegram_poller.py

import os
import time
import requests
from auth import get_service_account_token

# ── CONFIGURATION (via env vars) ──────────────────────────────────────────────
BOT_TOKEN       = os.environ['TELEGRAM_BOT_TOKEN']
PROJECT_ID      = os.environ['FIREBASE_PROJECT_ID']
ONBOARDING_GROUP = os.environ['ONBOARDING_GROUP_ID']   # e.g. "-1001234567890"
# Firestore REST base:
FIRESTORE_BASE  = f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}/databases/(default)/documents"
ACCESS_COLL     = "accessCodes"    # name of collection holding codes
ASSIGN_COLL     = "userCodes"      # name of collection for user→code assignments

# This must point to your index page (not the formResponse link)
WEBAPP_URL      = "https://script.google.com/macros/s/AKfycby-latzetN2RNKnIC3OSBq_bKdzTJ9GNTOK-dDe_4kQOnivsnAAvcsaVJcqOA1L9ZngwA/exec"  

# ── TELEGRAM “wrapper” ──────────────────────────────────────────────────────
def telegram(method, payload):
    return requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        json=payload
    ).json()

def get_updates(offset):
    resp = requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
        params={
            "timeout": 20,
            "offset": offset,
            "allowed_updates": ["message", "chat_member"]
        }
    ).json().get("result", [])
    return resp

def get_member_status(chat_id):
    """Calls getChatMember to see if user is still in your onboarding group."""
    resp = telegram("getChatMember", {
        "chat_id": ONBOARDING_GROUP,
        "user_id": chat_id
    })
    return resp.get("result", {}).get("status")

# ── FIRESTORE HELPERS ────────────────────────────────────────────────────────
def fetch_unused_code():
    """Queries Firestore for one unused (used==false) access code."""
    q = {"structuredQuery": {
        "from": [{"collectionId": ACCESS_COLL}],
        "where": {
            "fieldFilter": {
                "field": {"fieldPath": "used"},
                "op": "EQUAL",
                "value": {"booleanValue": False}
            }
        },
        "limit": 1
    }}
    token = get_service_account_token()
    rows = requests.post(
        f"{FIRESTORE_BASE}:runQuery",
        headers={"Authorization": f"Bearer {token}"},
        json=q
    ).json()
    for r in rows:
        if "document" in r:
            # Extract the document ID (access‐code string)
            return r["document"]["name"].split("/")[-1]
    return None

def mark_used(code):
    """Sets used=true on that code document in accessCodes."""
    url = f"{FIRESTORE_BASE}/{ACCESS_COLL}/{requests.utils.quote(code)}?updateMask.fieldPaths=used"
    token = get_service_account_token()
    requests.patch(
        url,
        headers={"Authorization": f"Bearer {token}"},
        json={"fields": {"used": {"booleanValue": True}}}
    )

def get_assignment(chat_id):
    """Fetches user→code record for this chat_id, if any."""
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
    """Creates or updates userCodes/{chat_id} with code & codeSent flag."""
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
    """Deletes userCodes/{chat_id}."""
    url = f"{FIRESTORE_BASE}/{ASSIGN_COLL}/{chat_id}"
    token = get_service_account_token()
    requests.delete(url, headers={"Authorization": f"Bearer {token}"})

# ── MAIN POLLING LOOP ────────────────────────────────────────────────────────
def poll():
    offset = 0
    while True:
        updates = get_updates(offset)
        for upd in updates:
            offset = upd["update_id"] + 1

            # If someone left or joined the group, we clear any old assignment
            if upd.get("message"):
                msg = upd["message"]
                # “left_chat_member” fires when a user leaves ANY chat (we check if it’s your group)
                lc = msg.get("left_chat_member")
                if lc and msg.get("chat", {}).get("id") == int(ONBOARDING_GROUP):
                    delete_assignment(lc["id"])

                # “new_chat_member” when a user (re)joins the group
                nc = msg.get("new_chat_member")
                if nc and msg.get("chat", {}).get("id") == int(ONBOARDING_GROUP):
                    delete_assignment(nc["id"])

                # Now handle a direct DM in private
                text = msg.get("text", "").strip()
                chat = msg.get("chat", {})
                cid = chat.get("id")

                # Only respond to /start (or /getcode) in a private chat
                if text.lower() in ("/start", "/getcode") and chat.get("type") == "private":
                    # 1) Verify they are still in the ONBOARDING_GROUP
                    status = get_member_status(cid)
                    if status not in ("member", "administrator", "creator"):
                        # If they’re not a group member, delete any stale assignment and skip
                        delete_assignment(cid)
                        continue

                    # 2) If they have no assignment yet, or their codeSent=false, assign a new code
                    rec = get_assignment(str(cid))
                    if rec is None or not rec.get("code"):
                        code = fetch_unused_code()
                        if not code:
                            telegram("sendMessage", {
                                "chat_id": cid,
                                "text": "❌ Sorry, we have run out of access codes right now."
                            })
                            continue

                        # Mark code as used in accessCodes
                        #mark_used(code)
                        # Insert into userCodes: codeSent = true
                        upsert_assignment(str(cid), code, True)

                        # 3) DM them the link to YOUR INDEX PAGE (no ?code=…)
                        dm_text = (
                            "✅ <b>Verification complete!</b>\n\n"
                            f"🔑 <b>{code}</b>\n\n"
                            "Finish signing up here: "
                            f"<a href=\"{WEBAPP_URL}\">Link</a>"
                        )
                        telegram("sendMessage", {
                            "chat_id": cid,
                            "text": dm_text,
                            "parse_mode": "HTML"
                        })
                    else:
                        # They already have a codeSent=true record. Don’t send again.
                        continue

        time.sleep(1)


if __name__ == "__main__":
    poll()
