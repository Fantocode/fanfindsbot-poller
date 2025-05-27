# telegram_poller.py
import os
import time
import requests
from auth import get_service_account_token

# telegram_poller.py
# …other imports…
from auth import get_service_account_token

BOT_TOKEN        = os.environ['TELEGRAM_BOT_TOKEN']
PROJECT_ID       = os.environ['FIREBASE_PROJECT_ID']
FIRESTORE_BASE   = f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}/databases/(default)/documents"
ACCESS_COLL      = "accessCodes"
ASSIGN_COLL      = "userCodes"

# ← NEW: point at your Apps Script /exec URL
WEBAPP_URL       = "https://script.google.com/macros/s/AKfycbxtZezPKizkiTtuce1wVWlNA7psEaxmoCjNuHzyRXFyGODy0hY9nnN9BNqwrOZshjf0vQ/exec"
ONBOARDING_GROUP = os.environ['ONBOARDING_GROUP_ID']


def telegram(method, payload):
    return requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        json=payload
    ).json()

def get_updates(offset):
    return requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
        params={"timeout": 20, "offset": offset, "allowed_updates": ["message"]}
    ).json().get("result", [])

def get_member_status(chat_id):
    resp = telegram("getChatMember", {
        "chat_id": ONBOARDING_GROUP, "user_id": chat_id
    })
    return resp.get("result", {}).get("status")

def fetch_unused_code():
    q = {
      "structuredQuery": {
        "from": [{"collectionId": ACCESS_COLL}],
        "where": {
          "fieldFilter": {
            "field": {"fieldPath": "used"},
            "op": "EQUAL",
            "value": {"booleanValue": False}
        }},
        "limit": 1
      }
    }
    token = get_service_account_token()
    rows  = requests.post(
      f"{FIRESTORE_BASE}:runQuery",
      headers={"Authorization": f"Bearer {token}"},
      json=q
    ).json()
    for r in rows:
        if "document" in r:
            return r["document"]["name"].split("/")[-1]
    return None

def mark_used(code):
    url   = (
      f"{FIRESTORE_BASE}/{ACCESS_COLL}/"
      f"{requests.utils.quote(code)}?updateMask.fieldPaths=used"
    )
    token = get_service_account_token()
    requests.patch(
      url,
      headers={"Authorization": f"Bearer {token}"},
      json={"fields": {"used": {"booleanValue": True}}}
    )

def get_assignment(chat_id):
    url   = f"{FIRESTORE_BASE}/{ASSIGN_COLL}/{chat_id}"
    token = get_service_account_token()
    r     = requests.get(url, headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 200:
        f = r.json()["fields"]
        return {
            "code":     f["code"]["stringValue"],
            "codeSent": f.get("codeSent", {}).get("booleanValue", False)
        }
    return None

def upsert_assignment(chat_id, code, sent):
    url   = f"{FIRESTORE_BASE}/{ASSIGN_COLL}/{chat_id}"
    token = get_service_account_token()
    body  = {
        "fields": {
            "code":     {"stringValue": code},
            "codeSent": {"booleanValue": sent}
        }
    }
    requests.patch(
      url,
      headers={"Authorization": f"Bearer {token}"},
      json=body
    )

def delete_assignment(chat_id):
    """ Remove their assignment so re-joins or leaves reset them """
    url   = f"{FIRESTORE_BASE}/{ASSIGN_COLL}/{chat_id}"
    token = get_service_account_token()
    requests.delete(url, headers={"Authorization": f"Bearer {token}"})

def poll():
    offset = 0
    while True:
        for upd in get_updates(offset):
            offset = upd["update_id"] + 1
            msg    = upd.get("message", {})
            text   = msg.get("text", "")
            chat   = msg.get("chat", {})
            cid    = chat.get("id")

            # 1) Handle service messages: left or new member
            if msg.get("left_chat_member"):
                uid = msg["left_chat_member"]["id"]
                delete_assignment(uid)
            if msg.get("new_chat_member"):
                uid = msg["new_chat_member"]["id"]
                delete_assignment(uid)

            # 2) Only respond to /getcode in a private chat
            if text == "/getcode" and chat.get("type") == "private":
                status = get_member_status(cid)
                if status not in ("member", "administrator", "creator"):
                    # They’ve DM'd when not in the group → reset them
                    delete_assignment(cid)
                    continue

                rec = get_assignment(cid)
                if not rec or not rec.get("code"):
                    # New or re-joined → issue a fresh code
                    code = fetch_unused_code()
                    if not code:
                        telegram("sendMessage", {
                            "chat_id": cid,
                            "text": "❌ No codes left"
                        })
                        continue

                    mark_used(code)
                    upsert_assignment(cid, code, True)

    # Send them the DM with HTML formatting
    dm_text = (
        "✅ <b>Verification complete!</b>\n\n"
        f"🔑 <b>{code}</b>\n\n"
        "Finish signing up here:\n"
        f"<a href=\"{WEBAPP_URL}?code={code}\">Use your unique access code above to complete the sign-up form</a>"
    )
    telegram("sendMessage", {
        "chat_id": cid,
        "text": dm_text,
        "parse_mode": "HTML"
    })


        time.sleep(1)

if __name__ == "__main__":
    poll()
