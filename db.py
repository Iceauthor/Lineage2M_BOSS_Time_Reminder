import os
import psycopg2
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

def get_db_connection():
    required_vars = ["DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME"]
    for var in required_vars:
        if not os.getenv(var):
            raise EnvironmentError(f"❌ 缺少資料庫設定變數：{var}")
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        dbname=os.getenv("DB_NAME")
    )

def get_boss_info_by_keyword(keyword):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT b.id, b.display_name, b.respawn_hours
            FROM boss_aliases a
            JOIN boss_list b ON a.boss_id = b.id
            WHERE a.keyword = %s
        """, (keyword,))
        result = cursor.fetchone()
        if result:
            return {
                "boss_id": result[0],
                "display_name": result[1],
                "respawn_hours": result[2]
            }
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
