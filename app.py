
import os
import psycopg2
from flask import Flask, request, abort
from dotenv import load_dotenv
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from datetime import datetime, timedelta
import pytz

load_dotenv()

app = Flask(__name__)
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

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
    result = cursor.fetchone()
    cursor.close()
    conn.close()
    return result[0] if result else None

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
    text = event.message.text.strip().lower()
    group_id = event.source.group_id if event.source.type == "group" else "single"
    if text in ["kb all", "出"]:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT b.display_name, t.respawn_time, b.respawn_hours
            FROM boss_list b
            LEFT JOIN (
                SELECT DISTINCT ON (boss_id) boss_id, respawn_time
                FROM boss_tasks
                WHERE group_id = %s
                ORDER BY boss_id, respawn_time DESC
            ) t ON b.id = t.boss_id
            ORDER BY 
                CASE WHEN t.respawn_time IS NULL THEN 1 ELSE 0 END, 
                t.respawn_time ASC
        """, (group_id,))
        results = cursor.fetchall()
        cursor.close()
        conn.close()

        tz = pytz.timezone('Asia/Taipei')
        now = datetime.now(tz)
        next_24hr = now + timedelta(hours=24)
        lines = ["🕓 接下來 24 小時內重生 BOSS：\n"]

        for name, time, hours in results:
            if time:
                time = time.replace(tzinfo=tz)
                if now <= time <= next_24hr:
                    lines.append(f"{time.strftime('%H:%M:%S')} {name}\n")
                elif now > time:
                    if hours:
                        delta = now - time
                        cycles = int(delta.total_seconds() // (hours * 3600)) + 1
                        lines.append(f"{time.strftime('%H:%M:%S')} {name}【過{cycles}】\n")
                    else:
                        lines.append(f"{time.strftime('%H:%M:%S')} {name}\n")
                else:
                    lines.append(f"{time.strftime('%H:%M:%S')} {name}\n")
            else:
                lines.append(f"__:__:__ {name}\n")

        reply_text = ''.join(lines)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
