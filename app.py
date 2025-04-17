from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from dotenv import load_dotenv
import os
from db import get_boss_info_by_keyword, insert_kill_time
from datetime import datetime, timedelta
import pytz
import psycopg2

load_dotenv()
tz = pytz.timezone("Asia/Taipei")

required_vars = ["LINE_CHANNEL_ACCESS_TOKEN", "LINE_CHANNEL_SECRET"]
for var in required_vars:
    if not os.getenv(var):
        raise EnvironmentError(f"❌ 缺少必要環境變數：{var}")

line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))
app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "✅ Lineage2M BOSS Reminder Bot is running."

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)
    print("📥 收到 Webhook 訊息")
    print("📝 原始內容：", body)
    try:
        handler.handle(body, signature)
    except Exception as e:
        print("❌ Webhook 錯誤：", str(e))
        abort(400)
    return "OK"

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        dbname=os.getenv("DB_NAME")
    )

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip()
    group_id = getattr(event.source, "group_id", "single")
    print(f"💬 收到使用者輸入：[{user_msg}]")
    print(f"📦 來源群組 ID：{group_id}")

    if user_msg.lower().startswith("k "):
        parts = user_msg.split()
        if len(parts) >= 2:
            keyword = parts[1]
            boss_info = get_boss_info_by_keyword(keyword)
            if boss_info:
                now = datetime.now(tz)
                respawn = now + timedelta(hours=boss_info["respawn_hours"])
                insert_kill_time(boss_info["boss_id"], group_id, now, respawn)
                reply = f"✔️ 已記錄擊殺：{boss_info['display_name']}\n死亡：{now.strftime('%Y-%m-%d %H:%M:%S')}\n重生：{respawn.strftime('%Y-%m-%d %H:%M:%S')}"
                print("✅ 已寫入 BOSS 擊殺資料")
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            else:
                print("⚠️ 關鍵字無對應 BOSS")
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 找不到 BOSS 關鍵字"))

    elif user_msg.strip().lower() in ["kb all", "出"]:
        print("📌 成功觸發 KB ALL 查詢")
        conn = get_db_connection()
        cursor = conn.cursor()
        now = datetime.now(tz)
        next_24hr = now + timedelta(hours=24)

        query = (
            "SELECT b.display_name, MIN(t.respawn_time) "
            "FROM boss_tasks t "
            "JOIN boss_list b ON t.boss_id = b.id "
            "WHERE t.respawn_time BETWEEN %s AND %s "
            "GROUP BY b.display_name "
            "ORDER BY MIN(t.respawn_time)"
        )
        cursor.execute(query, (now, next_24hr))
        results = cursor.fetchall()
        cursor.close()
        conn.close()

        print(f"📊 查詢結果：共 {len(results)} 筆")

        if results:
            lines = ["🕓 接下來 24 小時內重生 BOSS："]
            for name, time in results:
                local_time = time.astimezone(tz)
                lines.append(f"{name}：{local_time.strftime('%Y-%m-%d %H:%M:%S')}")
            reply_text = "\n".join(lines)
        else:
            reply_text = "⚠️ 未找到 24 小時內即將重生的 BOSS"

        try:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        except Exception as e:
            print("❌ 回覆失敗：", e)

if __name__ == "__main__":
    app.run(port=5000)
