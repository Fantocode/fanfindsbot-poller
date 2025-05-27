# telegram_poller.py
import os, time, requests
from auth import get_service_account_token

BOT_TOKEN        = os.environ['TELEGRAM_BOT_TOKEN']
PROJECT_ID       = os.environ['FIREBASE_PROJECT_ID']
FIRESTORE_BASE   = f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}/databases/(default)/documents"
ACCESS_COLL      = "accessCodes"
ASSIGN_COLL      = "userCodes"
FORM_PREFILL_BASE= (
    "https://docs.google.com/forms/d/e/"
    "1FAIpQLSctMoFicVK8PFP2KzFo-mByt3GR2bhxhiGY40m14BrUa1tPCQ/"
    "viewform?usp=pp_url&entry.1575034971="
)
ONBOARDING_GROUP = os.environ['ONBOARDING_GROUP_ID']

def telegram(method, payload):
    return requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        json=payload
    ).json()

def get_updates(offset):
    return requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
        params={"timeout":20,"offset":offset,"allowed_updates":["message"]}
    ).json().get("result", [])

def get_member_status(chat_id):
    resp = telegram("getChatMember", {"chat_id":ONBOARDING_GROUP,"user_id":chat_id})
    print("DEBUG getChatMember →", resp)
    return resp.get("result",{}).get("status")

def fetch_unused_code():
    q = {"structuredQuery":{
          "from":[{"collectionId":ACCESS_COLL}],
          "where":{"fieldFilter":{
            "field":{"fieldPath":"used"},
            "op":"EQUAL","value":{"booleanValue":False}
          }},
          "limit":1
        }}
    token = get_service_account_token()
    rows = requests.post(f"{FIRESTORE_BASE}:runQuery",
                         headers={"Authorization":f"Bearer {token}"},
                         json=q).json()
    for r in rows:
        if "document" in r:
            return r["document"]["name"].split("/")[-1]
    return None

def mark_used(code):
    url = f"{FIRESTORE_BASE}/{ACCESS_COLL}/{requests.utils.quote(code)}?updateMask.fieldPaths=used"
    token = get_service_account_token()
    requests.patch(url,
                   headers={"Authorization":f"Bearer {token}"},
                   json={"fields":{"used":{"booleanValue":True}}})

def get_assignment(chat_id):
    url = f"{FIRESTORE_BASE}/{ASSIGN_COLL}/{chat_id}"
    token = get_service_account_token()
    r = requests.get(url, headers={"Authorization":f"Bearer {token}"})
    if r.status_code==200:
        f = r.json()["fields"]
        return {"code":f["code"]["stringValue"],
                "codeSent":f.get("codeSent",{}).get("booleanValue",False)}
    return None

def upsert_assignment(chat_id, code, sent):
    url  = f"{FIRESTORE_BASE}/{ASSIGN_COLL}/{chat_id}"
    body = {"fields":{
              "code":{"stringValue":code},
              "codeSent":{"booleanValue":sent}
            }}
    token = get_service_account_token()
    requests.patch(url,
                   headers={"Authorization":f"Bearer {token}"},
                   json=body)

def delete_assignment(chat_id):
    url = f"{FIRESTORE_BASE}/{ASSIGN_COLL}/{chat_id}"
    token = get_service_account_token()
    requests.delete(url, headers={"Authorization":f"Bearer {token}"})

def poll():
    offset = 0
    while True:
        print("🟢 Polling Telegram…")
        for upd in get_updates(offset):
            print("DEBUG update →", upd)
            offset = upd["update_id"]+1
            msg    = upd.get("message",{})
            text   = msg.get("text","")
            chat   = msg.get("chat",{})
            cid    = chat.get("id")
            print(f"DEBUG got text={text!r} in chat_type={chat.get('type')!r}")

            # only /getcode in private chat
            if text=="/getcode" and chat.get("type")=="private":
                status = get_member_status(cid)
                if status not in ("member","administrator","creator"):
                    print("  ⛔ not in group, deleting any old assignment")
                    delete_assignment(cid)
                    continue

                rec = get_assignment(cid)
                print("  🔍 existing assignment:", rec)
                if not rec or not rec.get("code"):
                    code = fetch_unused_code()
                    print("  ➕ fetched code:", code)
                    if not code:
                        telegram("sendMessage",{"chat_id":cid,"text":"❌ No codes left"})
                        continue

                    mark_used(code)
                    upsert_assignment(cid, code, True)

                        # … after upsert_assignment() …
    dm_text = (
        "✅ <b>Verification complete!</b>\n\n"
        f"🔑 <b>{code}</b>\n\n"
        "Fill the form to finish signing up:\n"
        f"<a href=\"{FORM_PREFILL_BASE}{code}\">Click here to open the form</a>"
    )
    print("  📨 sending DM (HTML)…", dm_text)
    dm_resp = telegram("sendMessage", {
        "chat_id": cid,
        "text": dm_text,
        "parse_mode": "HTML"
    })
    print("  📨 sendMessage response:", dm_resp)

                    })
                    print("  📨 sendMessage response:", dm_resp)
                else:
                    print("  ℹ️ already had a code, skipping DM")

        time.sleep(1)

if __name__=="__main__":
    poll()
