import os
import time
import requests

BOT_TOKEN     = os.environ['TELEGRAM_BOT_TOKEN']
WEBHOOK_URL   = os.environ['APPS_SCRIPT_WEBHOOK_URL']
OFFSET_FILE   = 'offset.txt'

def load_offset():
    try:
        return int(open(OFFSET_FILE).read().strip())
    except:
        return 0

def save_offset(offset):
    open(OFFSET_FILE, 'w').write(str(offset))

def poll():
    offset = load_offset()
    url    = f'https://api.telegram.org/bot{BOT_TOKEN}/getUpdates'
    params = {'timeout':14, 'offset': offset, 'allowed_updates': ['message']}
    resp   = requests.get(url, params=params, timeout=20).json()
    for upd in resp.get('result', []):
        offset = max(offset, upd['update_id'] + 1)
        msg    = upd.get('message', {})
        text   = msg.get('text', '')
        chat   = msg.get('chat', {})
        # only forward "/getcode" from private chats:
        if text == '/getcode' and chat.get('type') == 'private':
            # forward to your Apps Script webhook:
            requests.post(WEBHOOK_URL, json=upd, timeout=10)
    save_offset(offset)

def main():
    while True:
        try:
            poll()
        except Exception as e:
            print('Error polling:', e)
            time.sleep(5)

if __name__ == '__main__':
    main()
