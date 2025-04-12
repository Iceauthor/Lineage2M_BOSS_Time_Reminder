import os
import mysql.connector
from dotenv import load_dotenv
from datetime import datetime, timedelta

# 載入 .env 環境變數
load_dotenv()

def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        port=int(os.getenv("DB_PORT"))
    )

def test_connection():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES;")
        tables = cursor.fetchall()
        print("✅ 資料庫連線成功，以下是目前的資料表：")
        for table in tables:
            print("-", table[0])
        cursor.close()
        conn.close()
    except Exception as e:
        print("❌ 資料庫連線失敗:", str(e))

def get_boss_info_by_keyword(keyword):
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT b.id AS boss_id, b.display_name, b.respawn_hours
            FROM boss_aliases a
            JOIN boss_list b ON a.boss_id = b.id
            WHERE a.keyword = %s
        """, (keyword,))
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

def insert_kill_time(boss_id, group_id, kill_time, respawn_time):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO boss_tasks (boss_id, group_id, kill_time, respawn_time)
            VALUES (%s, %s, %s, %s)
        """, (boss_id, group_id, kill_time, respawn_time))
        conn.commit()
    finally:
        cursor.close()
        conn.close()

def get_next_respawns_within_24h(group_id):
    now = datetime.now()
    later = now + timedelta(hours=24)
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT b.display_name, MIN(t.respawn_time) AS next_respawn
            FROM boss_tasks t
            JOIN boss_list b ON t.boss_id = b.id
            WHERE t.group_id = %s AND t.respawn_time BETWEEN %s AND %s
            GROUP BY b.id, b.display_name
            ORDER BY next_respawn ASC
        """, (group_id, now, later))
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

def get_group_stats(group_id, boss_id):
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT kill_time FROM boss_tasks
            WHERE group_id = %s AND boss_id = %s
            ORDER BY kill_time ASC
        """, (group_id, boss_id))
        kills = cursor.fetchall()
        return [row["kill_time"] for row in kills]
    finally:
        cursor.close()
        conn.close()

def clear_boss_kill_data(group_id, boss_id):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM boss_tasks
            WHERE group_id = %s AND boss_id = %s
        """, (group_id, boss_id))
        conn.commit()
    finally:
        cursor.close()
        conn.close()
