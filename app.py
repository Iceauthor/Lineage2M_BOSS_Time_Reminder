
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import os
from dotenv import load_dotenv
import psycopg2
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import pytz

load_dotenv()
app = Flask(__name__)

line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

tz = pytz.timezone("Asia/Taipei")

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        dbname=os.getenv("DB_NAME")
    )

def get_respawn_hours_by_name(name):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT respawn_hours FROM boss_list WHERE display_name = %s", (name,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row[0] if row else None

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip()
    group_id = getattr(event.source, 'group_id', 'single')

    if user_msg.lower() in ["kb all", "出"]:
        conn = get_db_connection()
        cursor = conn.cursor()
        now = datetime.now(tz)
        next_24hr = now + timedelta(hours=24)

        cursor.execute("""
            SELECT b.display_name, t.death_time, b.respawn_hours
            FROM boss_list b
            LEFT JOIN (
                SELECT DISTINCT ON (boss_id) *
                FROM boss_tasks
                WHERE group_id = %s
                ORDER BY boss_id, id DESC
            ) t ON t.boss_id = b.id
            ORDER BY t.death_time NULLS LAST
        """, (group_id,))
        results = cursor.fetchall()
        cursor.close()
        conn.close()

        lines = ["🕓 接下來 24 小時內重生 BOSS：\n"]
        for name, death_time, respawn_hours in results:
            if death_time:
                death_time = death_time.replace(tzinfo=tz)
                respawn_time = death_time + timedelta(hours=respawn_hours)
                if now <= respawn_time <= next_24hr:
                    lines.append(f"{respawn_time.strftime('%H:%M:%S')} {name}\n")
                elif now > respawn_time:
                    delta = now - respawn_time
                    cycles = int(delta.total_seconds() // (respawn_hours * 3600)) + 1
                    lines.append(f"{respawn_time.strftime('%H:%M:%S')} {name}【過{cycles}】\n")
                else:
                    lines.append(f"{respawn_time.strftime('%H:%M:%S')} {name}\n")
            else:
                lines.append(f"__ : __ : __ {name}\n")

        reply_text = "".join(lines)
        try:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        except Exception as e:
            print("❌ 回覆失敗：", e)

# 啟動 Flask
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
