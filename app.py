from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FlexSendMessage
from dotenv import load_dotenv
import os
from db import (
    get_boss_info_by_keyword, insert_kill_time, get_next_respawns_within_24h,
    get_group_stats, clear_boss_kill_data
)
from datetime import datetime, timedelta
import json

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
    
    # 判斷是否為 kill 指令
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

    # KB ALL 或預測指令
    if user_msg.lower() in ["kb all", "出"]:
        boss_list = get_next_respawns_within_24h(group_id)
        contents = []
        with open("static/flex_boss_color_map.json", encoding="utf-8") as f:
            color_map = json.load(f)

        for boss in boss_list:
            name = boss["display_name"]
            time_str = boss["next_respawn"].strftime("%H:%M")
            color = "#FFFFFF"
            if name in color_map["yellow"]:
                color = "#FFFACD"
            elif name in color_map["purple"]:
                color = "#F5E6FF"
            box = {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "paddingAll": "10px",
                "backgroundColor": color,
                "contents": [
                    {"type": "text", "text": name, "weight": "bold", "color": "#111111", "size": "md"},
                    {"type": "text", "text": f"預計：{time_str}", "color": "#666666", "size": "sm"}
                ]
            }
            contents.append(box)

        flex_msg = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": contents
            }
        }

        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="24小時內BOSS預測", contents=flex_msg))
        return

    # 清除指令
    if user_msg.lower().startswith("clear "):
        parts = user_msg.split()
        if len(parts) == 2:
            keyword = parts[1]
            boss_info = get_boss_info_by_keyword(keyword)
            if boss_info:
                clear_boss_kill_data(group_id, boss_info["boss_id"])
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🧹 已清除 {boss_info['display_name']} 的紀錄"))
                return

if __name__ == "__main__":
    app.run(port=5000)
