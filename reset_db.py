import db
import os
import shutil

UPLOAD_DIR = 'uploads'

def reset_db_mysql():
    print("Connecting to MySQL...")
    conn = db.get_connection()
    cur = conn.cursor()
    
    tables = [
        'specialists',
        'applications',
        'bot_users',
        'subscriptions',
        'advertisements',
        'email_verifications',
        'users'
    ]
    
    print("Deleting data from tables...")
    cur.execute("SET FOREIGN_KEY_CHECKS = 0")
    for table in tables:
        try:
            cur.execute(f"TRUNCATE TABLE {table}")
            print(f"  - Truncated {table}")
        except Exception as e:
            print(f"  - Error truncating {table}: {e}")
            
    cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    conn.commit()
    conn.close()
    print("Database cleared (MySQL).")

def clear_uploads():
    print(f"Clearing uploads directory: {UPLOAD_DIR}...")
    if os.path.exists(UPLOAD_DIR):
        for filename in os.listdir(UPLOAD_DIR):
            file_path = os.path.join(UPLOAD_DIR, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            except Exception as e:
                print(f'Failed to delete {file_path}. Reason: {e}')
    print("Uploads cleared.")

if __name__ == '__main__':
    reset_db_mysql()
    clear_uploads()
    print("Done. Site is new.")
