# bot.py
import os
import time
import requests
from auth import get_service_account_token

# ─── CONFIG ─────────────────────────────────────────
BOT_TOKEN         = os.environ['TELEGRAM_BOT_TOKEN']
PROJECT_ID        = os.environ['FIREBASE_PROJECT_ID']
FIRESTORE_BASE    = (
    f"https://firestore.googleapis.com/v1/projects/"
    f"{PROJECT_ID}/databases/(default)/documents"
)
ACCESS_COLL       = "accessCodes"
ASSIGN_COLL       = "userCodes"
FORM_PREFILL_BASE = (
    "https://docs.google.com/forms/d/e/"
    "1FAIpQLSctMoFicVK8PFP2KzFo-mByt3GR2bhxhiGY40m14BrUa1tPCQ/"
    "viewform?usp=pp_url&entry.1575034971="
)
ONBOARDING_GROUP  = os.environ['ONBOARDING_GROUP_ID']

# ─── HELPERS ────────────────────────────────────────
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
            # document.name: projects/.../documents/accessCodes/<code>
            return r["document"]["name"].split("/")[-1]
    return None

def mark_used(code):
    url = (
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
    url = f"{FIRESTORE_BASE}/{ASSIGN_COLL}/{chat_id}"
    token = get_service_account_token()
    r = requests.get(
        url, headers={"Authorization": f"Bearer {token}"}
    )
    if r.status_code == 200:
        f = r.json()["fields"]
        return {
            "code":     f["code"]["stringValue"],
            "codeSent": f.get("codeSent", {}).get("booleanValue", False)
        }
    return None

def upsert_assignment(chat_id, code, sent):
    url = f"{FIRESTORE_BASE}/{ASSIGN_COLL}/{chat_id}"
    body = {
        "fields": {
            "code":     {"stringValue": code},
            "codeSent": {"booleanValue": sent}
        }
    }
    token = get_service_account_token()
    requests.patch(
        url,
        headers={"Authorization": f"Bearer {token}"},
        json=body
    )

# ─── MAIN LOOP ──────────────────────────────────────
def poll():
    offset = 0
    while True:
        print("🟢 Polling Telegram…")
        for upd in get_updates(offset):
            offset = upd["update_id"] + 1
            msg  = upd.get("message", {})
            text = msg.get("text", "")
            chat = msg.get("chat", {})
            cid  = chat.get("id")

            # Only respond to /getcode in a private chat
            if text == "/getcode" and chat.get("type") == "private":
                # 1) Verify still in onboarding group
                if get_member_status(cid) not in ("member", "administrator", "creator"):
                    upsert_assignment(cid, "", False)
                    continue

                rec = get_assignment(cid)
                # New user → fetch + mark code
                if not rec:
                    code = fetch_unused_code()
                    if not code:
                        telegram("sendMessage", {"chat_id": cid, "text": "❌ No codes left"})
                        continue
                    mark_used(code)
                    upsert_assignment(cid, code, True)
                    rec = {"code": code}

                # First-time DM?
                if not rec.get("codeSent"):
                    upsert_assignment(cid, rec["code"], True)

                # Send them the code
                reply = (
                    f"✅ Verification complete!\n\n🔑 *{rec['code']}*\n\n"
                    f"Fill the form to finish signing up: "
                    f"[Click here]({FORM_PREFILL_BASE}{rec['code']})"
                )
                telegram("sendMessage", {
                    "chat_id": cid,
                    "text": reply,
                    "parse_mode": "Markdown"
                })

        time.sleep(1)


if __name__ == "__main__":
    poll()
