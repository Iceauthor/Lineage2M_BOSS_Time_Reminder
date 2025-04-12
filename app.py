from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from dotenv import load_dotenv
import os
from db import get_boss_info_by_keyword, insert_kill_time
from datetime import datetime, timedelta

app = Flask(__name__)
load_dotenv()

line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except Exception as e:
        print("❌ Webhook 錯誤：", str(e))
        abort(400)
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip()
    group_id = event.source.group_id if hasattr(event.source, "group_id") else "single"

    if user_msg.lower().startswith("k "):
        parts = user_msg.split()
        if len(parts) >= 2:
            keyword = parts[1]
            boss_info = get_boss_info_by_keyword(keyword)
            if boss_info:
                now = datetime.now()
                respawn = now + timedelta(hours=boss_info["respawn_hours"])
                insert_kill_time(boss_info["boss_id"], group_id, now, respawn)
                reply = f"✔️ 已記錄擊殺：{boss_info['display_name']}\n死亡：{now}\n重生：{respawn}"
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
                return

if __name__ == "__main__":
    app.run(port=5000)
