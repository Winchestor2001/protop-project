import os
import pymysql
import pymysql.cursors
from dotenv import load_dotenv

load_dotenv()

# Configuration
DB_HOST = os.environ.get('DB_HOST', 'localhost')
DB_USER = os.environ.get('DB_USER', 'root')
DB_PASSWORD = os.environ.get('DB_PASSWORD', '')
DB_NAME = os.environ.get('DB_NAME', 'protop_db')

def get_connection():
    """Returns a new MySQL connection."""
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

def init_db():
    """Initializes the database tables (MySQL)."""
    # Create DB if not exists (needs root/high priv usually, user needs to create DB manually or we try)
    try:
        conn_root = pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD)
        try:
            with conn_root.cursor() as cur:
                cur.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            conn_root.commit()
        finally:
            conn_root.close()
    except Exception as e:
        print(f"Warning: Could not check/create database: {e}. Assuming it exists.")

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            # 1. SPECIALISTS
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS specialists (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    profession TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    email TEXT,
                    experience INT,
                    price TEXT,
                    free_time TEXT,
                    city TEXT,
                    country TEXT,
                    description TEXT,
                    photo_url TEXT,
                    top_order INT,
                    status TEXT,
                    trial_started_at TEXT,
                    trial_expires_at TEXT,
                    paid_until TEXT,
                    telegram_chat_id BIGINT,
                    blocked_reason TEXT,
                    blocked_at TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 2. APPLICATIONS
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS applications (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    username TEXT,
                    profession TEXT,
                    full_name TEXT,
                    phone TEXT,
                    city TEXT,
                    experience INT,
                    price TEXT,
                    free_time TEXT,
                    description TEXT,
                    photo_path TEXT,
                    status VARCHAR(50) DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 3. BOT USERS
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bot_users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    referred_by BIGINT,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 3.1 REFERRALS (Detailed tracking)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS referrals (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    referrer_id BIGINT NOT NULL,
                    referred_user_id BIGINT NOT NULL,
                    status VARCHAR(50) DEFAULT 'pending',
                    activated_at TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY (referred_user_id)
                )
            """)

            # 4. USERS (Admin/Site)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name TEXT,
                    email VARCHAR(255) UNIQUE NOT NULL,
                    phone VARCHAR(255) UNIQUE,
                    role TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 5. EMAIL VERIFICATIONS
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS email_verifications (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    email TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT,
                    phone TEXT,
                    role TEXT,
                    password_hash TEXT NOT NULL,
                    expires_at TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 6. SUBSCRIPTIONS
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    specialist_id INT NOT NULL,
                    telegram_user_id BIGINT,
                    full_name TEXT,
                    phone TEXT,
                    email TEXT,
                    started_at TEXT,
                    expires_at TEXT,
                    amount REAL,
                    currency TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 7. ADVERTISEMENTS
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS advertisements (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT,
                    image_url TEXT,
                    link_url TEXT,
                    position INT DEFAULT 0,
                    is_active INT DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        conn.commit()
    finally:
        conn.close()

if __name__ == "__main__":
    init_db()
    print("MySQL Database Initialized!")
