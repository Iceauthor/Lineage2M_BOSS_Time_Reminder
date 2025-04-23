from apscheduler.schedulers.background import BackgroundScheduler
import os
import json
import psycopg2
from flask import Flask, request, abort
from dotenv import load_dotenv
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FlexSendMessage
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
    print("✅ 已清除 boss_aliases 資料")

    for boss in bosses:
        display_name = boss["display_name"]
        respawn_hours = boss["respawn_hours"]
        keywords = boss["keywords"]

        # 新增 boss 主資料
        cursor.execute("""
            INSERT INTO boss_list (display_name, respawn_hours)
            VALUES (%s, %s)
            ON CONFLICT (display_name)
            DO UPDATE SET respawn_hours = EXCLUDED.respawn_hours
            RETURNING id
        """, (display_name, respawn_hours))
        boss_id = cursor.fetchone()[0]

        # 新增對應 keyword
        for keyword in keywords:
            cursor.execute("""
                INSERT INTO boss_aliases (boss_id, keyword)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
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


# ✅ 新增 /ping route（避免平台睡眠）
@app.route("/ping", methods=["GET"])
def ping():
    tz = pytz.timezone("Asia/Taipei")
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    return f"pong - {now}", 200


@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except Exception as e:
        print("Error:", e)
    return "OK", 200  # ✅ 立即給 LINE 回應


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
                    # 先刪除同一群組同一 BOSS 的舊資料
                    cursor.execute("DELETE FROM boss_tasks WHERE boss_id = %s AND group_id = %s", (boss_id, group_id))
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

    # 處理 clear all 指令：清除該群組所有 BOSS 紀錄
    if text.lower().strip() == "clear all":
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
                    # 先刪除同一群組同一 BOSS 的舊資料
                    cursor.execute("DELETE FROM boss_tasks WHERE boss_id = %s AND group_id = %s", (boss_id, group_id))
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
            # 先刪除同一群組同一 BOSS 的舊資料
            cursor.execute("DELETE FROM boss_tasks WHERE boss_id = %s AND group_id = %s", (boss_id, group_id))

            # 插入新紀錄
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
            SELECT
                b.display_name,
                t.id,  -- boss_tasks id
                t.kill_time,
                b.respawn_hours
            FROM boss_list b
            LEFT JOIN LATERAL (
                SELECT id, kill_time
                FROM boss_tasks
                WHERE boss_id = b.id AND group_id = %s
                ORDER BY kill_time DESC, id DESC
                LIMIT 1
            ) t ON true
            ORDER BY 
              CASE WHEN t.kill_time IS NULL THEN 1 ELSE 0 END,
              (t.kill_time + (b.respawn_hours || ' hours')::interval)
        """, (group_id,))
        results = cursor.fetchall()
        cursor.close()
        conn.close()

        tz = pytz.timezone('Asia/Taipei')
        now = datetime.now(tz)
        soon = now + timedelta(minutes=30)
        next_24hr = now + timedelta(hours=24)

        lines = ["🕓 即將重生 BOSS：\n"]

        yellow_list = [
            "被汙染的克魯瑪", "司穆艾爾", "提米特利斯", "突變克魯瑪", "黑色蕾爾莉",
            "寇倫", "提米妮爾", "卡坦", "蘭多勒", "貝希莫斯", "薩班", "史坦",
            "忘卻之鏡", "大地祭壇", "水之祭壇", "風之祭壇", "黑闇祭壇", "克拉奇",
            "梅杜莎", "沙勒卡", "塔拉金"
        ]

        purple_list = [
            "黑卡頓", "塔那透斯", "巴倫", "摩德烏斯", "歐克斯", "薩拉克斯", "哈普", "霸拉克",
            "安德拉斯", "納伊阿斯", "核心基座", "巨蟻女王", "卡布里歐", "鳳凰", "猛龍獸",
            "奧爾芬", "弗林特", "拉何"
        ]

        def next_respawn_time(r):
            if r[2]:  # r[2] 是 kill_time
                respawn_time = r[2].astimezone(tz) + timedelta(hours=r[3])
                delta = (respawn_time - now).total_seconds()
                return delta if delta >= 0 else float('inf')
            else:
                return float('inf')

        # ✅ 按照最近即將重生的排最前
        sorted_results = sorted(results, key=next_respawn_time)

        flex_contents = []

        for name, task_id, kill_time, hours in sorted_results:

            box = {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "md",
                "margin": "sm",
                "contents": [],
            }

            # 判斷有無紀錄
            if kill_time:
                respawn_time = kill_time.astimezone(tz) + timedelta(hours=hours)
                if now < respawn_time <= soon:
                    color = "#D60000"
                    note = "（快重生）"
                    emoji = "🔥 "
                    weight = "bold"
                    text_block = {
                        "type": "text",
                        "text": f"{emoji}{respawn_time.strftime('%H:%M:%S')} {name}{note}",
                        "color": color,
                        "weight": weight,
                        "size": "sm",
                        "wrap": True
                    }
                    box = {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [text_block]
                    }
                    if name in yellow_list:
                        box["backgroundColor"] = "#FFF9DC"  # 淡鵝黃色
                    elif name in purple_list:
                        box["backgroundColor"] = "#F5F0FF"  # 淡粉紫色
                    flex_contents.append(box)
                elif now > respawn_time:
                    diff = (now - respawn_time).total_seconds()
                    passed = int(diff // (hours * 3600))
                    if passed >= 1:
                        respawn_time += timedelta(hours=passed * hours)
                    note = f"（過{passed}）" if passed >= 1 else ""
                    color = "#999999"
                    emoji = ""
                    weight = "regular"
                    text_block = {
                        "type": "text",
                        "text": f"{emoji}{respawn_time.strftime('%H:%M:%S')} {name}{note}",
                        "color": color,
                        "weight": weight,
                        "size": "sm",
                        "wrap": True
                    }
                    box = {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [text_block]
                    }
                    if name in yellow_list:
                        box["backgroundColor"] = "#FFF9DC"  # 淡鵝黃色
                    elif name in purple_list:
                        box["backgroundColor"] = "#F5F0FF"  # 淡粉紫色
                    flex_contents.append(box)
                else:
                    color = "#000000"
                    note = ""
                    emoji = ""
                    weight = "regular"
                    text_block = {
                        "type": "text",
                        "text": f"{emoji}{respawn_time.strftime('%H:%M:%S')} {name}{note}",
                        "color": color,
                        "weight": weight,
                        "size": "sm",
                        "wrap": True
                    }
                    box = {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [text_block]
                    }
                    if name in yellow_list:
                        box["backgroundColor"] = "#FFF9DC"  # 淡鵝黃色
                    elif name in purple_list:
                        box["backgroundColor"] = "#F5F0FF"  # 淡粉紫色
                    flex_contents.append(box)
                time_str = respawn_time.strftime("%H:%M:%S")
            else:
                text_block = {
                    "type": "text",
                    "text": f"__:__:__ {name}",
                    "color": "#CCCCCC",
                    "size": "sm",
                    "wrap": True
                }
                box = {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [text_block]
                }
                flex_contents.append(box)

        bubble = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": "🕓 即將重生 BOSS",
                        "weight": "bold",
                        "size": "md",
                        "margin": "md"
                    },
                    {
                        "type": "separator",
                        "margin": "md"
                    },
                    *flex_contents
                ]
            }
        }
        for name, task_id, kill_time, respawn_hours in results:
            if kill_time:
                respawn_time = kill_time.replace(tzinfo=pytz.timezone('Asia/Taipei')) + timedelta(hours=respawn_hours)
                if now <= respawn_time <= next_24hr:
                    lines.append(f"{respawn_time.strftime('%H:%M:%S')} {name}\n")
                elif now > respawn_time:
                    if respawn_hours:
                        diff = (now - respawn_time).total_seconds()
                        passed_cycles = int(diff // (respawn_hours * 3600))  # 向下取整，避免誤差提前進位
                        lines.append(f"{respawn_time.strftime('%H:%M:%S')} {name}（過{passed_cycles}）\n")
                    else:
                        lines.append(f"{respawn_time.strftime('%H:%M:%S')} {name}\n")
                else:
                    lines.append(f"{respawn_time.strftime('%H:%M:%S')} {name}\n")
            else:
                lines.append(f"__:__:__ {name}\n")

        reply_text = ''.join(lines)
        line_bot_api.reply_message(
            event.reply_token,
            messages=[
                FlexSendMessage(alt_text="BOSS 重生預測表", contents=bubble)
                # TextSendMessage(text=reply_text)
            ]
        )
    # ✅ ALIAS 指令管理區段
    if text.startswith("alias ") or text.startswith("add "):
        parts = text.split()
        if len(parts) < 2:
            line_bot_api.reply_message(event.reply_token,
                                       TextSendMessage(text="⚠️ 格式錯誤，請使用：alias 別名 正式名稱"))
            return

        subcommand = parts[1].lower()

        # alias del keyword
        if subcommand == "del" and len(parts) == 3:
            keyword = parts[2].lower()
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM boss_aliases WHERE keyword = %s", (keyword,))
            conn.commit()
            cursor.close()
            conn.close()
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🗑️ 已刪除別名「{keyword}」"))
            return

        # alias check keyword
        if subcommand == "check" and len(parts) == 3:
            keyword = parts[2].lower()
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT b.display_name FROM boss_aliases a
                JOIN boss_list b ON a.boss_id = b.id
                WHERE a.keyword = %s
            """, (keyword,))
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            if row:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🔍 「{keyword}」 對應 BOSS：{row[0]}"))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 找不到「{keyword}」的對應 BOSS"))
            return

        # ✅ alias list（只顯示本群使用過的 BOSS）
        if subcommand == "list":
            group_id = event.source.group_id if event.source.type == "group" else "single"
            if group_id == "single":
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 此功能僅限群組使用"))
                return

            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT a.keyword, b.display_name
                FROM boss_aliases a
                JOIN boss_list b ON a.boss_id = b.id
                JOIN boss_tasks t ON b.id = t.boss_id
                WHERE t.group_id = %s
                ORDER BY b.display_name
            """, (group_id,))
            rows = cursor.fetchall()
            cursor.close()
            conn.close()

            if not rows:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📭 本群組尚未使用過任何別名。"))
                return

            # 建立 Flex Message 卡片內容
            alias_contents = [
                {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {"type": "text", "text": k, "size": "sm", "flex": 2, "weight": "bold"},
                        {"type": "text", "text": "→", "size": "sm", "flex": 1},
                        {"type": "text", "text": n, "size": "sm", "flex": 5}
                    ]
                } for k, n in rows
            ]

            bubble = {
                "type": "bubble",
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {"type": "text", "text": "📘 本群組別名清單", "weight": "bold", "size": "md", "margin": "md"},
                        {"type": "separator", "margin": "md"},
                        *alias_contents
                    ]
                }
            }

            line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="本群別名清單", contents=bubble))
            return

        # alias 新增 keyword → display_name
        if len(parts) >= 3:
            keyword = parts[1].lower()
            target_name = parts[2]
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM boss_list WHERE display_name = %s", (target_name,))
            row = cursor.fetchone()
            if row:
                boss_id = row[0]
                cursor.execute(
                    "INSERT INTO boss_aliases (boss_id, keyword) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (boss_id, keyword)
                )
                conn.commit()
                reply_text = f"✅ 已將「{keyword}」設定為「{target_name}」的別名！"
            else:
                reply_text = f"❌ 找不到名稱為「{target_name}」的 BOSS。"
            cursor.close()
            conn.close()
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return


# # 自動推播：重生時間倒數兩分鐘提醒
# def reminder_job():
#     try:
#         tz = pytz.timezone("Asia/Taipei")
#         now = datetime.now(tz)
#         soon = now + timedelta(minutes=2)
#         conn = get_db_connection()
#         cursor = conn.cursor()
#         cursor.execute("""
#             SELECT b.display_name, t.group_id, t.respawn_time
#             FROM boss_tasks t
#             JOIN boss_list b ON b.id = t.boss_id
#             WHERE t.respawn_time BETWEEN %s AND %s
#         """, (now, soon))
#         results = cursor.fetchall()
#         for name, group_id, respawn in results:
#             if not group_id or not group_id.startswith("C"):
#                 print(f"⚠️ 無效 group_id：{group_id}，跳過")
#                 continue
#             try:
#                 msg = f"*{name}* 即將出現"
#                 line_bot_api.push_message(group_id, TextSendMessage(text=msg))
#             except Exception as e:
#                 print(f"❌ 提醒失敗：{e}")
#         cursor.close()
#         conn.close()
#     except Exception as e:
#         print("❌ 排程提醒錯誤：", e)

# ✅ 自動推播 BOSS 重生提醒（倒數兩分鐘 + 過期仍持續提醒）
# def reminder_job():
#     try:
#         tz = pytz.timezone("Asia/Taipei")
#         now = datetime.now(tz)
#         soon = now + timedelta(minutes=2)
#
#         conn = get_db_connection()
#         cursor = conn.cursor()
#
#         # 1. 查出兩分鐘內即將出現的 BOSS
#         cursor.execute("""
#             SELECT b.display_name, t.group_id, t.respawn_time
#             FROM boss_tasks t
#             JOIN boss_list b ON b.id = t.boss_id
#             WHERE t.respawn_time BETWEEN %s AND %s
#         """, (now, soon))
#         results = cursor.fetchall()
#
#         # 2. 查出所有已經過期的 BOSS（不管過多久）
#         cursor.execute("""
#             SELECT b.display_name, t.group_id, t.respawn_time, b.respawn_hours
#             FROM boss_tasks t
#             JOIN boss_list b ON b.id = t.boss_id
#             WHERE t.respawn_time < %s
#         """, (now,))
#         expired = cursor.fetchall()
#
#         # 3. 推播快出現者
#         for name, group_id, respawn in results:
#             if not group_id or not group_id.startswith("C"):
#                 print(f"⚠️ 無效 group_id：{group_id}，跳過")
#                 continue
#             try:
#                 msg = f"*{name}* 即將出現"
#                 line_bot_api.push_message(group_id, TextSendMessage(text=msg))
#             except Exception as e:
#                 print(f"❌ 提醒失敗：{e}")
#
#         # 4. 推播已過期但還沒再輸入的 BOSS（持續提醒）
#         for name, group_id, respawn, hours in expired:
#             if not group_id or not group_id.startswith("C"):
#                 continue
#             try:
#                 # 計算已過幾次 respawn cycle
#                 delta = (now - respawn).total_seconds()
#                 passed = int(delta // (hours * 3600))
#                 if passed >= 1:
#                     msg = f"*{name}* 即將出現（過{passed}）"
#                     line_bot_api.push_message(group_id, TextSendMessage(text=msg))
#             except Exception as e:
#                 print(f"❌ 過期提醒失敗：{e}")
#
#         cursor.close()
#         conn.close()
#     except Exception as e:
#         print("❌ 排程提醒錯誤：", e)

# ✅ 自動推播 BOSS 重生提醒（兩分鐘內 + 過期持續提醒 + 正確時間更新）
def reminder_job():
    try:
        tz = pytz.timezone("Asia/Taipei")
        now = datetime.now(tz)
        soon = now + timedelta(minutes=2)

        conn = get_db_connection()
        cursor = conn.cursor()

        # 查詢所有 boss 的最新資訊（含週期）
        cursor.execute("""
            SELECT b.display_name, t.group_id, t.respawn_time, b.respawn_hours
            FROM boss_tasks t
            JOIN boss_list b ON b.id = t.boss_id
        """)
        results = cursor.fetchall()

        for name, group_id, respawn, hours in results:
            if not group_id or not group_id.startswith("C"):
                continue

            # 確保 respawn_time 是 timezone-aware
            if respawn.tzinfo is None:
                respawn = tz.localize(respawn)

            # 計算實際下一次應重生的時間（若已過期則加上週期直到未來）
            next_respawn = respawn
            passed = 0
            while next_respawn < now:
                next_respawn += timedelta(hours=hours)
                passed += 1

            # ✅ 寫回資料庫，更新為最新的下一次時間點
            if passed > 0:
                cursor.execute("""
                    UPDATE boss_tasks
                    SET respawn_time = %s
                    WHERE boss_id = (
                        SELECT id FROM boss_list WHERE display_name = %s LIMIT 1
                    ) AND group_id = %s
                """, (next_respawn, name, group_id))
                conn.commit()

            # 判斷是否即將重生（2分鐘內）
            # if 0 <= (next_respawn - now).total_seconds() <= 120:
            #     try:
            #         passed = int((now - respawn).total_seconds() // (hours * 3600))
            #         suffix = f"（過{passed}）" if passed > 0 else ""
            #         msg = f"*{name}* 即將出現{suffix}"
            #         line_bot_api.push_message(group_id, TextSendMessage(text=msg))
            if 0 <= (next_respawn - now).total_seconds() <= 120:
                try:
                    suffix = f"（過{passed}）" if passed > 0 else ""
                    msg = f"*{name}* 即將出現{suffix}"
                    line_bot_api.push_message(group_id, TextSendMessage(text=msg))
                except Exception as e:
                    print(f"❌ 提醒失敗：{e}")

        cursor.close()
        conn.close()
    except Exception as e:
        print("❌ 排程提醒錯誤：", e)


@app.route("/debug-respawn", methods=["GET"])
def debug_respawn_route():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            b.display_name AS boss_name,
            t.kill_time,
            t.respawn_time,
            b.respawn_hours,
            EXTRACT(EPOCH FROM (t.respawn_time - t.kill_time)) / 3600 AS actual_hours,
            (EXTRACT(EPOCH FROM (t.respawn_time - t.kill_time)) / 3600) - b.respawn_hours AS hour_difference
        FROM boss_tasks t
        JOIN boss_list b ON t.boss_id = b.id
        ORDER BY t.respawn_time DESC
        LIMIT 20
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    output = "<h2>重生時間誤差檢查</h2><ul>"
    for row in rows:
        boss, kill, respawn, expected, actual, diff = row
        output += f"<li><b>{boss}</b>：預期 {expected} 小時，實際 {actual:.2f} 小時，誤差 {diff:.2f} 小時</li>"
    output += "</ul>"
    return output


if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    scheduler.add_job(reminder_job, "interval", minutes=1)
    scheduler.start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

    