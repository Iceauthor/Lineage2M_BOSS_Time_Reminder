from apscheduler.schedulers.background import BackgroundScheduler
import os
import json
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


# 自動清理重複 boss_aliases 並建立唯一索引
def cleanup_boss_aliases():
    try:
        def cleanup_boss_aliases():
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM boss_aliases a
                USING boss_aliases b
                WHERE a.id < b.id
                  AND a.boss_id = b.boss_id
                  AND a.keyword = b.keyword
            """)
            cursor.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_indexes WHERE indexname = 'unique_boss_keyword'
                    ) THEN
                        CREATE UNIQUE INDEX unique_boss_keyword ON boss_aliases(boss_id, keyword);
                    END IF;
                END$$;
            """)
            conn.commit()
            cursor.close()
            conn.close()
            print("✅ 已清除重複 boss_aliases\n✅ 已建立唯一索引")

    except Exception as e:
        print("❌ 清理/索引建立失敗：", e)


# 自動匯入 boss_list.json 資料
def auto_insert_boss_list():
    print("🚀 執行 BOSS 自動匯入")
    conn = get_db_connection()
    cursor = conn.cursor()

    with open("boss_list.json", "r", encoding="utf-8") as f:
        bosses = json.load(f)

    # 清空舊有資料
    cursor.execute("DELETE FROM boss_aliases")
    cursor.execute("DELETE FROM boss_list")
    print("✅ 已清除 boss_list 與 boss_aliases 資料")

    for boss in bosses:
        display_name = boss["display_name"]
        respawn_hours = boss["respawn_hours"]
        keywords = boss["keywords"]

        # 新增 boss 主資料
        cursor.execute("""
            INSERT INTO boss_list (display_name, respawn_hours) 
            VALUES (%s, %s) RETURNING id
        """, (display_name, respawn_hours))
        boss_id = cursor.fetchone()[0]

        # 新增對應 keyword
        for keyword in keywords:
            cursor.execute("""
                INSERT INTO boss_aliases (boss_id, keyword)
                VALUES (%s, %s)
            """, (boss_id, keyword.lower()))

    conn.commit()
    cursor.close()
    conn.close()
    print("✅ BOSS 資料匯入完成")



# 啟動時先執行一次清理 + 匯入
cleanup_boss_aliases()
auto_insert_boss_list()


@app.route("/", methods=["GET"])
def home():
    return "✅ Lineage2M BOSS Reminder Bot is running."

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
    text = event.message.text.strip()
    group_id = event.source.group_id if event.source.type == "group" else "single"
    
    # 處理 K 克4 170124（當日指定時間）
    if text.lower().startswith("k "):
        parts = text.split()
        if len(parts) == 3 and parts[2].isdigit() and len(parts[2]) == 6:
            _, keyword, timestr = parts
            try:
                hour = int(timestr[0:2])
                minute = int(timestr[2:4])
                second = int(timestr[4:6])
                tz = pytz.timezone("Asia/Taipei")
                kill_time = datetime.now(tz).replace(hour=hour, minute=minute, second=second, microsecond=0)

                conn = get_db_connection()
                cursor = conn.cursor()
                keyword = keyword.lower()
                cursor.execute("""
                    SELECT b.id, b.display_name, b.respawn_hours
                    FROM boss_aliases a
                    JOIN boss_list b ON a.boss_id = b.id
                    WHERE a.keyword = %s
                """, (keyword,))
                row = cursor.fetchone()
                if row:
                    boss_id, display_name, respawn_hours = row
                    respawn_time = kill_time + timedelta(hours=respawn_hours)
                    cursor.execute(
                        "INSERT INTO boss_tasks (boss_id, group_id, kill_time, respawn_time) VALUES (%s, %s, %s, %s)",
                        (boss_id, group_id, kill_time, respawn_time)
                    )
                    conn.commit()
                    reply_text = f"\n\n🔴 擊殺：{display_name}\n🕓 死亡：{kill_time.strftime('%Y-%m-%d %H:%M:%S')}\n🟢 重生：{respawn_time.strftime('%Y-%m-%d %H:%M:%S')}"
                else:
                    reply_text = "❌ 找不到該 BOSS 關鍵字。"
                cursor.close()
                conn.close()
            except:
                reply_text = "❌ 時間格式錯誤，請使用 K 克4 170124 的格式。"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return

    # 處理 /clear all 指令：清除該群組所有 BOSS 紀錄
    if text.lower().strip() == "/clear all":
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM boss_tasks WHERE group_id = %s", (group_id,))
        conn.commit()
        cursor.close()
        conn.close()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 已清除本群組所有 BOSS 紀錄"))
        return

# 處理 kr1、kr2 克4 170124 格式，指定前日或前兩日死亡時間
    if text.lower().startswith("kr1 ") or text.lower().startswith("kr2 "):
        parts = text.split()
        if len(parts) == 3:
            prefix, keyword, timestr = parts
            try:
                hour = int(timestr[0:2])
                minute = int(timestr[2:4])
                second = int(timestr[4:6])
                offset_days = 1 if prefix.lower() == "kr1" else 2
                kill_time = datetime.now(pytz.timezone("Asia/Taipei")) - timedelta(days=offset_days)
                kill_time = kill_time.replace(hour=hour, minute=minute, second=second, microsecond=0)

                conn = get_db_connection()
                cursor = conn.cursor()
                keyword = keyword.lower()
                cursor.execute("""
                    SELECT b.id, b.display_name, b.respawn_hours
                    FROM boss_aliases a
                    JOIN boss_list b ON a.boss_id = b.id
                    WHERE a.keyword = %s
                """, (keyword,))
                row = cursor.fetchone()
                if row:
                    boss_id, display_name, respawn_hours = row
                    respawn_time = kill_time + timedelta(hours=respawn_hours)
                    cursor.execute(
                        "INSERT INTO boss_tasks (boss_id, group_id, kill_time, respawn_time) VALUES (%s, %s, %s, %s)",
                        (boss_id, group_id, kill_time, respawn_time)
                    )
                    conn.commit()
                    reply_text = f"\n\n🔴 擊殺：{display_name}\n🕓 死亡：{kill_time.strftime('%Y-%m-%d %H:%M:%S')}\n🟢 重生：{respawn_time.strftime('%Y-%m-%d %H:%M:%S')}"
                else:
                    reply_text = "❌ 找不到該 BOSS 關鍵字。"
                cursor.close()
                conn.close()
            except:
                reply_text = "❌ 時間格式錯誤，請使用 kr1 克4 170124 的格式。"
        else:
            reply_text = "❌ 指令格式錯誤，請使用 kr1 克4 170124 的格式。"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return
    # 處理 K、k 指令作為擊殺紀錄
    if text.lower().startswith("k "):
        keyword = text[2:].strip()
        conn = get_db_connection()
        cursor = conn.cursor()
        keyword = keyword.lower()
        cursor.execute("""
            SELECT b.id, b.display_name, b.respawn_hours
            FROM boss_aliases a
            JOIN boss_list b ON a.boss_id = b.id
            WHERE a.keyword = %s
        """, (keyword,))
        row = cursor.fetchone()
        if row:
            boss_id, display_name, respawn_hours = row
            now = datetime.now(pytz.timezone('Asia/Taipei'))
            respawn_time = now + timedelta(hours=respawn_hours)
            cursor.execute(
                "INSERT INTO boss_tasks (boss_id, group_id, kill_time, respawn_time) VALUES (%s, %s, %s, %s)",
                (boss_id, group_id, now, respawn_time)
            )
            conn.commit()
            reply_text = f"\n\n🔴 擊殺：{display_name}\n🕓 死亡：{now.strftime('%Y-%m-%d %H:%M:%S')}\n🟢 重生：{respawn_time.strftime('%Y-%m-%d %H:%M:%S')}"
        else:
            reply_text = "❌ 無法辨識的關鍵字，請先使用 add 指令新增。"
        cursor.close()
        conn.close()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    text = event.message.text.strip().lower()
    group_id = event.source.group_id if event.source.type == "group" else "single"
    if text in ["kb all", "出"]:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT b.display_name, t.latest_respawn_time, b.respawn_hours
            FROM boss_list b
            LEFT JOIN (
                SELECT DISTINCT ON (boss_id)
                    boss_id, respawn_time AS latest_respawn_time
                FROM boss_tasks
                WHERE group_id = %s
                ORDER BY boss_id, respawn_time DESC
            ) t ON b.id = t.boss_id
            ORDER BY 
                CASE WHEN t.latest_respawn_time IS NULL THEN 1 ELSE 0 END, 
                t.latest_respawn_time ASC
        """, (group_id,))
        results = cursor.fetchall()
        print(f"📊 查詢結果：{results}")
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
    