import sqlite3
import os
from datetime import datetime

DB_NAME = "slicer.db"

def get_connection():
    return sqlite3.connect(DB_NAME, check_same_thread=False)

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # 任务表：记录每一次提取的结果
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            status TEXT NOT NULL,
            video_path TEXT,
            srt_path TEXT,
            error_msg TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 检查表结构，如果原本建立过没有 video_path 的表，自动添加列
    cursor.execute("PRAGMA table_info(tasks)")
    columns = [info[1] for info in cursor.fetchall()]
    if "video_path" not in columns:
        cursor.execute("ALTER TABLE tasks ADD COLUMN video_path TEXT")
        cursor.execute("ALTER TABLE tasks ADD COLUMN srt_path TEXT")
        
    conn.commit()
    conn.close()

def create_task(url):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO tasks (url, status, created_at, updated_at) 
        VALUES (?, 'running', ?, ?)
    ''', (url, datetime.now(), datetime.now()))
    task_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return task_id

def update_task_status(task_id, status, error_msg="", video_path="", srt_path=""):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE tasks 
        SET status = ?, error_msg = ?, updated_at = ?, 
            video_path = COALESCE(NULLIF(?, ''), video_path), 
            srt_path = COALESCE(NULLIF(?, ''), srt_path)
        WHERE id = ?
    ''', (status, error_msg, datetime.now(), video_path, srt_path, task_id))
    conn.commit()
    conn.close()

def delete_task(task_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
    conn.commit()
    conn.close()

def get_successful_tasks():
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM tasks 
        WHERE status = 'success'
        ORDER BY created_at DESC
    ''')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]
