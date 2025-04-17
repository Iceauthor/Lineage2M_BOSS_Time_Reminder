
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
import os
from db import get_boss_info_by_keyword, insert_kill_time
from datetime import datetime, timedelta
import pytz
import psycopg2
import json

load_dotenv()
tz = pytz.timezone("Asia/Taipei")


def get_respawn_hours_by_name(name):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT respawn_hours FROM boss_list WHERE display_name = %s", (name,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row[0] if row else None


def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        dbname=os.getenv("DB_NAME")
    )


# 自動清理重複 boss_aliases 並建立唯一索引
def cleanup_boss_aliases():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM boss_aliases a
            USING boss_aliases b
            WHERE
                a.ctid < b.ctid
                AND a.boss_id = b.boss_id
                AND a.keyword = b.keyword;
        """)
        print("✅ 已清除重複 boss_aliases")

        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_boss_keyword_unique
            ON boss_aliases (boss_id, keyword);
        """)
        print("✅ 已建立唯一索引")

        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print("❌ 清理/索引建立失敗：", e)


# 自動匯入 boss_list.json 資料
def auto_insert_boss_list():
    print("🚀 執行 BOSS 自動匯入")
    try:
        with open("boss_list.json", "r", encoding="utf-8") as f:
            bosses = json.load(f)

        conn = get_db_connection()
        cursor = conn.cursor()

        for boss in bosses:
            display_name = boss["display_name"]
            respawn_hours = boss["respawn_hours"]
            keywords = boss["keywords"]

            cursor.execute("SELECT id FROM boss_list WHERE display_name = %s", (display_name,))
            row = cursor.fetchone()
            if row:
                boss_id = row[0]
            else:
                cursor.execute("INSERT INTO boss_list (display_name, respawn_hours) VALUES (%s, %s) RETURNING id",
                               (display_name, respawn_hours))
                boss_id = cursor.fetchone()[0]

            for keyword in keywords:
                cursor.execute("SELECT 1 FROM boss_aliases WHERE boss_id = %s AND keyword = %s",
                               (boss_id, keyword.lower()))
                if not cursor.fetchone():
                    cursor.execute("INSERT INTO boss_aliases (boss_id, keyword) VALUES (%s, %s)",
                                   (boss_id, keyword.lower()))

        conn.commit()
        cursor.close()
        conn.close()
        print("✅ BOSS 資料匯入完成")
    except Exception as e:
        print("❌ 匯入錯誤：", e)


# 啟動時先執行一次清理 + 匯入
cleanup_boss_aliases()
auto_insert_boss_list()

# LINE 機器人主體
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


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip()
    group_id = getattr(event.source, "group_id", "single")
    print(f"💬 收到使用者輸入：[{user_msg}]")
    print(f"📦 來源群組 ID：{group_id}")
    
    if user_msg.lower().startswith("add "):
        try:
            _, keyword, display_name = user_msg.split()
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM boss_list WHERE display_name = %s", (display_name,))
            row = cursor.fetchone()
            if not row:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 無此 BOSS 名稱"))
                return
            boss_id = row[0]
            cursor.execute("SELECT 1 FROM boss_aliases WHERE boss_id = %s AND keyword = %s", (boss_id, keyword.lower()))
            if not cursor.fetchone():
                cursor.execute("INSERT INTO boss_aliases (boss_id, keyword) VALUES (%s, %s)", (boss_id, keyword.lower()))
                conn.commit()
                msg = f"✅ 已新增 {display_name} 的關鍵字：{keyword}"
            else:
                msg = "⚠️ 該關鍵字已存在"
            cursor.close()
            conn.close()
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        except:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 指令錯誤：add 關鍵字 名稱"))
            msg = f"❌ 指令錯誤：add 關鍵字 名稱"

    elif user_msg.lower() == "reset all":
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM boss_tasks WHERE group_id = %s", (group_id,))
        conn.commit()
        cursor.execute("SELECT display_name FROM boss_list")
        bosses = cursor.fetchall()
        cursor.close()
        conn.close()
        reply = "\n".join([f"{b[0]}：__:__:__" for b in bosses])
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已重設時間：\n{reply}"))

    elif user_msg.lower().startswith("k "):
            parts = user_msg.split()
            if len(parts) >= 2:
                keyword = parts[1]
                boss_info = get_boss_info_by_keyword(keyword)
                if boss_info:
                    now = datetime.now(tz)
                    respawn = now + timedelta(hours=boss_info["respawn_hours"])

                    # ✅ 先刪除該群組該 BOSS 舊資料
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM boss_tasks WHERE boss_id = %s AND group_id = %s",
                                   (boss_info["boss_id"], group_id))
                    conn.commit()
                    cursor.close()
                    conn.close()

                    insert_kill_time(boss_info["boss_id"], group_id, now, respawn)

                    reply = f"✔️ 已記錄擊殺：{boss_info['display_name']}\n死亡：{now.strftime('%Y-%m-%d %H:%M:%S')}\n重生：{respawn.strftime('%Y-%m-%d %H:%M:%S')}"
                    print("✅ 已寫入 BOSS 擊殺資料")
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            else:
                print("⚠️ 關鍵字無對應 BOSS")
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 找不到 BOSS 關鍵字"))

    elif user_msg.strip().lower() in ["kb all", "出"]:
        conn = get_db_connection()
        cursor = conn.cursor()
        now = datetime.now(tz)
        next_24hr = now + timedelta(hours=24)

        cursor.execute("""
                    SELECT b.display_name, t.timestamp, b.respawn_hours
                    FROM boss_list b
                    LEFT JOIN (
                        SELECT DISTINCT ON (boss_id) *
                        FROM boss_tasks
                        WHERE group_id = %s
                        ORDER BY boss_id, id DESC
                    ) t ON t.boss_id = b.id
                    ORDER BY t.timestamp NULLS LAST
                """, (group_id,))
        results = cursor.fetchall()
        cursor.close()
        conn.close()

        print(f"📊 查詢結果：共 {len(results)} 筆")
        lines = ["🕓 接下來 24 小時內重生 BOSS：\\n"]

        for name, timestamp, respawn_hours in results:
            if timestamp:
                death_time = timestamp.replace(tzinfo=tz)
                respawn_time = death_time + timedelta(hours=respawn_hours)

                if now <= respawn_time <= next_24hr:
                    lines.append(f"{respawn_time.strftime('%H:%M:%S')} {name}\\n")
                elif now > respawn_time:
                    delta = now - respawn_time
                    cycles = int(delta.total_seconds() // (respawn_hours * 3600)) + 1
                    lines.append(f"{respawn_time.strftime('%H:%M:%S')} {name}【過{cycles}】\\n")
                else:
                    lines.append(f"{respawn_time.strftime('%H:%M:%S')} {name}\\n")
            else:
                lines.append(f"__ : __ : __ {name}\\n")

        reply_text = "".join(lines)
        try:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        except Exception as e:
            print("❌ 回覆失敗：", e)



# 自動推播：重生時間倒數兩分鐘提醒
def reminder_job():
    try:
        now = datetime.now(tz)
        soon = now + timedelta(minutes=2)
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT b.display_name, t.group_id, t.respawn_time
            FROM boss_tasks t
            JOIN boss_list b ON b.id = t.boss_id
            WHERE t.respawn_time BETWEEN %s AND %s
        """, (now, soon))
        results = cursor.fetchall()
        for name, group_id, respawn in results:
            try:
                msg = f"*{name}* 即將出現"
                line_bot_api.push_message(group_id, TextSendMessage(text=msg))
            except Exception as e:
                print(f"❌ 提醒失敗：{e}")
        cursor.close()
        conn.close()
    except Exception as e:
        print("❌ 排程提醒錯誤：", e)


if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    scheduler.add_job(reminder_job, "interval", minutes=1)
    scheduler.start()
    app.run(port=5000)





