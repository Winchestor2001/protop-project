from flask import Flask, request, jsonify, session, send_from_directory, redirect, url_for, make_response, render_template, Response
from flask_cors import CORS
from flask_swagger_ui import get_swaggerui_blueprint
import db
import pymysql
from datetime import datetime, timedelta
import os
import re
import smtplib
import requests
from email.mime.text import MIMEText
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
import base64
import json

# Firebase Admin SDK
import firebase_admin
from firebase_admin import credentials as fb_credentials, messaging

# Load .env first
load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=True,
     allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
     methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me')

# ---- Swagger UI ----
SWAGGER_URL = '/swagger'
API_URL = '/swagger/swagger.json'
SWAGGER_ADMIN_USER = os.environ.get('ADMIN_USERNAME', '')
SWAGGER_ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', '')

def check_swagger_auth():
    auth = request.authorization
    if auth and auth.username == SWAGGER_ADMIN_USER and auth.password == SWAGGER_ADMIN_PASS:
        return True
    return False

@app.before_request
def swagger_basic_auth():
    if request.path.startswith(SWAGGER_URL):
        if not check_swagger_auth():
            return Response(
                'Swagger uchun login/parol kiriting', 401,
                {'WWW-Authenticate': 'Basic realm="Swagger UI"'}
            )

swaggerui_blueprint = get_swaggerui_blueprint(
    SWAGGER_URL,
    API_URL,
    config={'app_name': "ProTop API"}
)
app.register_blueprint(swaggerui_blueprint, url_prefix=SWAGGER_URL)

@app.route('/swagger/swagger.json')
@app.route('/api/swagger.json')
def swagger_json():
    return send_from_directory(app.static_folder, 'swagger.json')

import db

# ---- Firebase Push Notifications ----
_firebase_initialized = False
FIREBASE_SERVICE_ACCOUNT_PATH = os.environ.get('FIREBASE_SERVICE_ACCOUNT_PATH', '')
FIREBASE_SERVICE_ACCOUNT_JSON = os.environ.get('FIREBASE_SERVICE_ACCOUNT_JSON', '')

if FIREBASE_SERVICE_ACCOUNT_PATH and os.path.exists(FIREBASE_SERVICE_ACCOUNT_PATH):
    cred = fb_credentials.Certificate(FIREBASE_SERVICE_ACCOUNT_PATH)
    firebase_admin.initialize_app(cred)
    _firebase_initialized = True
elif FIREBASE_SERVICE_ACCOUNT_JSON:
    try:
        cred = fb_credentials.Certificate(json.loads(FIREBASE_SERVICE_ACCOUNT_JSON))
        firebase_admin.initialize_app(cred)
        _firebase_initialized = True
    except Exception as e:
        print(f"Warning: Firebase init failed: {e}")
else:
    print("Warning: Firebase not configured. Push notifications disabled.")


def send_push(token: str, title: str, body: str, data: dict = None):
    """Bitta device ga push notification yuborish."""
    if not _firebase_initialized:
        return False
    message = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        data={k: str(v) for k, v in (data or {}).items()},
        token=token,
    )
    try:
        messaging.send(message)
        return True
    except messaging.UnregisteredError:
        # Token eskirgan — bazadan o'chirish
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("DELETE FROM device_tokens WHERE token = %s", (token,))
            conn.commit()
            conn.close()
        except Exception:
            pass
        return False
    except Exception as e:
        print(f"Push send error: {e}")
        return False


def send_push_to_user(telegram_user_id: int, title: str, body: str, data: dict = None):
    """Foydalanuvchining barcha qurilmalariga push yuborish."""
    if not _firebase_initialized:
        return 0
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT token FROM device_tokens WHERE telegram_user_id = %s", (telegram_user_id,))
    rows = cur.fetchall()
    conn.close()
    sent = 0
    for row in rows:
        if send_push(row['token'], title, body, data):
            sent += 1
    return sent


def send_push_broadcast(title: str, body: str, data: dict = None):
    """Barcha ro'yxatdan o'tgan qurilmalarga push yuborish."""
    if not _firebase_initialized:
        return 0
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT token FROM device_tokens")
    rows = cur.fetchall()
    conn.close()
    sent = 0
    for row in rows:
        if send_push(row['token'], title, body, data):
            sent += 1
    return sent


DATABASE = 'protop_db' # MySQL DB name from env
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With'
    return response

def init_db():
    db.init_db()

def get_db():
    """Получить соединение с базой данных"""
    return db.get_connection()

@app.route('/api/specialists', methods=['GET'])
def get_specialists():
    """Получить список всех специалистов или отфильтрованных по профессии.

    Логика подписки:
    - если у записи нет ни trial_expires_at, ни paid_until, ни status — считаем её "старой" и показываем всегда;
    - если есть trial_expires_at/paid_until — показываем только если сейчас до trial_expires_at или до paid_until;
      иначе помечаем как expired и не отдаём в ответ.
    """
    profession = request.args.get('profession', None)
    
    conn = get_db()
    cursor = conn.cursor()
    
    order_sql = "ORDER BY CASE WHEN top_order IS NULL THEN 1 ELSE 0 END, top_order ASC, created_at DESC"
    if profession:
        cursor.execute(f'SELECT * FROM specialists WHERE profession = %s {order_sql}', (profession,))
    else:
        cursor.execute(f'SELECT * FROM specialists {order_sql}')
    
    now = datetime.utcnow()
    specialists = []
    rows = cursor.fetchall()
    for row in rows:
        status = row['status'] if 'status' in row.keys() else None
        trial_expires_at = row['trial_expires_at'] if 'trial_expires_at' in row.keys() else None
        paid_until = row['paid_until'] if 'paid_until' in row.keys() else None

        # Проста логика определения видимости
        is_new_scheme = bool(trial_expires_at or paid_until or status)
        is_visible = True
        
        # Don't show blocked workers on public site
        if status == 'blocked':
            is_visible = False
        elif is_new_scheme:
            def _parse(ts):
                if not ts:
                    return None
                try:
                    return datetime.fromisoformat(ts)
                except Exception:
                    return None
            t_expires = _parse(trial_expires_at)
            p_until = _parse(paid_until)
            active = False
            if p_until and p_until > now:
                active = True
            if t_expires and t_expires > now:
                active = True
            if not active:
                is_visible = False
                # Обновляем статус на expired для наглядности
                try:
                    cursor.execute("UPDATE specialists SET status = %s WHERE id = %s", ('expired', row['id']))
                    conn.commit()
                except Exception:
                    pass

        if not is_visible:
            continue

        specialists.append({
            'id': row['id'],
            'profession': row['profession'],
            'full_name': row['full_name'],
            'phone': row['phone'],
            'email': row['email'],
            'experience': row['experience'],
            'price': row.get('price', 'Kelishiladi'),
            'free_time': row['free_time'],
            'city': row['city'],
            'description': row['description'],
            'photo_url': row['photo_url'],
            'top_order': row['top_order'],
            'created_at': row['created_at']
        })
    
    conn.close()
    return jsonify({'specialists': specialists})

def validate_phone(phone):
    """Валидация номера телефона"""
    # Разрешаем только цифры, +, -, (), пробелы
    phone_pattern = r'^[\+]%s[0-9][0-9\s\-\(\)]{7,20}$'
    if not re.match(phone_pattern, phone):
        return False
    return True

def validate_email(email):
    """Валидация email"""
    if not email:
        return True  # Email необязателен
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(email_pattern, email) is not None

def validate_experience(experience):
    """Валидация опыта работы"""
    try:
        exp = int(experience)
        return 0 <= exp <= 50
    except (ValueError, TypeError):
        return False

def sanitize_input(text, max_length=500):
    """Очистка текстовых полей от опасных символов"""
    if not text:
        return ''
    # Удаляем HTML теги и скрипты
    text = re.sub(r'<[^>]*>', '', str(text))
    # Удаляем потенциально опасные символы
    text = re.sub(r'[<>"\';]', '', text)
    # Обрезаем до максимальной длины
    return text[:max_length].strip()

@app.route('/api/specialists', methods=['POST'])
def add_specialist():
    """Добавить нового специалиста (только админ) с валидацией"""
    if session.get('is_admin') is not True:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    
    # Проверка обязательных полей
    required_fields = ['profession', 'full_name', 'phone', 'email']
    for field in required_fields:
        if field not in data or not data[field] or not str(data[field]).strip():
            return jsonify({'error': f'Поле {field} обязательно для заполнения'}), 400
    
    # Валидация имени
    full_name = sanitize_input(data['full_name'], 100)
    if len(full_name) < 2:
        return jsonify({'error': 'Имя должно содержать минимум 2 символа'}), 400
    
    # Валидация телефона
    phone = data['phone'].strip()
    if not validate_phone(phone):
        return jsonify({'error': 'Неверный формат номера телефона. Используйте формат: +998901234567'}), 400
    
    # Валидация email (если указан)
    email = data.get('email', '').strip()
    if email and not validate_email(email):
        return jsonify({'error': 'Неверный формат email адреса'}), 400
    
    # Валидация опыта
    experience = data.get('experience', 0)
    if experience and not validate_experience(experience):
        return jsonify({'error': 'Опыт работы должен быть числом от 0 до 50'}), 400
    
    # Очистка всех текстовых полей
    profession = sanitize_input(data['profession'], 200)
    free_time = sanitize_input(data.get('free_time', ''), 200)
    city = sanitize_input(data.get('city', ''), 100)
    country = ''  # country убран из UI
    description = sanitize_input(data.get('description', ''), 1000)
    
    # Check if specialist exists
    cursor = conn.cursor()
    cursor.execute('SELECT id, status FROM specialists WHERE phone = %s', (phone,))
    existing = cursor.fetchone()
    
    if existing:
        if existing['status'] == 'rejected':
            # Allow re-application: Update basic info and reset status
            # We will use UPDATE instead of INSERT below
            try:
                cursor.execute('''
                    UPDATE specialists 
                    SET profession=%s, full_name=%s, email=%s, experience=%s, free_time=%s, city=%s, country=%s, description=%s, photo_url=%s, top_order=%s, status='trial', created_at=CURRENT_TIMESTAMP
                    WHERE id=%s
                ''', (
                    profession, full_name, email, int(experience) if experience else 0, free_time, city, country, description, 
                    sanitize_input(data.get('photo_url', ''), 300),
                    int(data.get('top_order')) if str(data.get('top_order', '')).strip().isdigit() else None,
                    existing['id']
                ))
                conn.commit()
                
                # Fetch updated
                cursor.execute('SELECT * FROM specialists WHERE id = %s', (existing['id'],))
                row = cursor.fetchone()
                specialist = {
                    'id': row['id'], 'profession': row['profession'], 'full_name': row['full_name'], 'phone': row['phone'],
                    'email': row['email'], 'experience': row['experience'], 'price': row.get('price', 'Kelishiladi'), 'free_time': row['free_time'],
                    'description': row['description'], 'photo_url': row['photo_url'], 'top_order': row['top_order'], 'created_at': row['created_at']
                }
                conn.close()
                return jsonify({'specialist': specialist}), 201
            except Exception as e:
                conn.close()
                return jsonify({'error': 'Ошибка при обновлении заявки'}), 500
        else:
            conn.close()
            return jsonify({'error': 'Специалист с таким номером телефона уже существует'}), 400

    try:
        cursor.execute('''
            INSERT INTO specialists (profession, full_name, phone, email, experience, price, free_time, city, country, description, photo_url, top_order, telegram_chat_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            profession,
            full_name,
            phone,
            email,
            int(experience) if experience else 0,
            data.get('price', 'Kelishiladi'),
            free_time,
            city,
            description,
            sanitize_input(data.get('photo_url', ''), 300),
            int(data.get('top_order')) if str(data.get('top_order', '')).strip().isdigit() else None,
            data.get('telegram_chat_id')
        ))
        
        specialist_id = cursor.lastrowid
        conn.commit()
        
        # Получаем добавленного специалиста
        cursor.execute('SELECT * FROM specialists WHERE id = %s', (specialist_id,))
        row = cursor.fetchone()
        
        specialist = {
            'id': row['id'],
            'profession': row['profession'],
            'full_name': row['full_name'],
            'phone': row['phone'],
            'email': row['email'],
            'experience': row['experience'],
            'price': row['price'],
            'free_time': row['free_time'],
            'description': row['description'],
            'photo_url': row['photo_url'],
            'top_order': row['top_order'],
            'created_at': row['created_at']
        }
        
        conn.close()
        return jsonify({'specialist': specialist}), 201
    except Exception as e:
        conn.close()
        return jsonify({'error': 'Ошибка при добавлении специалиста'}), 500

@app.route('/api/specialists/<int:specialist_id>', methods=['GET'])
def get_specialist(specialist_id):
    """Получить конкретного специалиста по ID"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM specialists WHERE id = %s', (specialist_id,))
    row = cursor.fetchone()
    
    if row is None:
        conn.close()
        return jsonify({'error': 'Специалист не найден'}), 404
    
    specialist = {
        'id': row['id'],
        'profession': row['profession'],
        'full_name': row['full_name'],
        'phone': row['phone'],
        'email': row['email'],
        'experience': row['experience'],
        'price': row['price'],
        'free_time': row['free_time'],
        'city': row['city'],
        'description': row['description'],
        'photo_url': row['photo_url'],
        'top_order': row['top_order'],
        'created_at': row['created_at']
    }
    
    conn.close()
    return jsonify({'specialist': specialist})

@app.route('/api/professions', methods=['GET'])
def get_professions():
    """Получить список уникальных профессий"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT DISTINCT profession FROM specialists')
    professions = [row['profession'] for row in cursor.fetchall()]
    
    conn.close()
    return jsonify({'professions': professions})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Получить статистику"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) as total FROM specialists')
    total = cursor.fetchone()['total']
    
    cursor.execute('SELECT COUNT(DISTINCT profession) as professions FROM specialists')
    professions_count = cursor.fetchone()['professions']
    
    conn.close()
    return jsonify({
        'total_specialists': total,
        'professions_count': professions_count,
        'completed_projects': 10000  # Статичное значение
    })

# -------- Админ-панель --------
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'adminJ')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'MavlonovZava2010')

def is_admin():
    return session.get('is_admin') is True

@app.route('/admin', methods=['GET'])
def admin_page():
    if not is_admin():
        # Простая форма логина
        return make_response('''
            <html><head><meta charset="utf-8"><title>Admin Login</title></head>
            <body style="font-family: Arial; max-width:420px; margin:60px auto;">
              <h2>Admin Login</h2>
              <form method="post" action="/admin/login">
                <div><label>Username</label><br><input name="username" style="width:100%;padding:8px"></div>
                <div style="margin-top:8px"><label>Password</label><br><input type="password" name="password" style="width:100%;padding:8px"></div>
                <button type="submit" style="margin-top:12px;padding:10px 16px">Login</button>
              </form>
            </body></html>
        ''')
    # Если залогинен — отдаем admin.html
    return render_template('admin.html')

@app.route('/robots.txt')
def robots_txt():
    return send_from_directory(app.static_folder, 'robots.txt', mimetype='text/plain')

@app.route('/sitemap.xml')
def sitemap_xml():
    return send_from_directory(app.static_folder, 'sitemap.xml', mimetype='application/xml')

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/kliyent.html')
def kliyent_page():
    return render_template('kliyent.html')

@app.route('/ischilar.html')
def ischilar_page():
    return render_template('ischilar.html')

@app.route('/specialists.html')
def specialists_page():
    return render_template('specialists.html')

@app.route('/profile.html')
def profile_page():
    return render_template('profile.html')

@app.route('/admin/login', methods=['POST'])
def admin_login():
    username = request.form.get('username', '')
    password = request.form.get('password', '')
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        session['is_admin'] = True
        return redirect(url_for('admin_page'))
    return make_response('Неверные данные', 401)

@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.clear()
    return redirect(url_for('admin_page'))

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html'), 500

# Создание специалиста (админ)
@app.route('/api/admin/specialists', methods=['POST'])
def admin_create_specialist():
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    return add_specialist()

# Обновление специалиста (админ)
@app.route('/api/specialists/<int:specialist_id>', methods=['PUT'])
def update_specialist(specialist_id):
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json() or {}
    conn = get_db()
    cursor = conn.cursor()
    # Собираем обновляемые поля
    fields = []
    values = []
    for key, col, maxlen in [
        ('profession','profession',200),
        ('full_name','full_name',100),
        ('phone','phone',30),
        ('email','email',120),
        ('free_time','free_time',200),
        ('city','city',100),
        ('country','country',100),
        ('description','description',1000),
        ('photo_url','photo_url',300)
    ]:
        if key in data and data[key] is not None:
            if key in ('experience',):
                pass
            else:
                values.append(sanitize_input(data[key], maxlen))
                fields.append(f"{col} = %s")
    if 'experience' in data and data['experience'] is not None:
        try:
            exp = int(data['experience'])
        except Exception:
            exp = 0
        values.append(max(0, min(50, exp)))
        fields.append('experience = %s')
    if 'top_order' in data:
        to = data['top_order']
        if to is None or str(to).strip()=='' or (isinstance(to,str) and not to.isdigit()):
            values.append(None)
        else:
            values.append(int(to))
        fields.append('top_order = %s')
    if not fields:
        conn.close()
        return jsonify({'error':'No fields'}), 400
    values.append(specialist_id)
    cursor.execute(f"UPDATE specialists SET {', '.join(fields)} WHERE id = %s", tuple(values))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# Удаление специалиста (только админ)
@app.route('/api/specialists/<int:specialist_id>', methods=['DELETE'])
def delete_specialist(specialist_id):
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM specialists WHERE id = %s', (specialist_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


# Admin-only endpoint to get ALL specialists (including blocked/expired)
@app.route('/api/admin/specialists/list', methods=['GET'])
def admin_get_all_specialists():
    """Get all specialists for admin panel - no filtering"""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    
    profession = request.args.get('profession', None)
    conn = get_db()
    cursor = conn.cursor()
    
    order_sql = "ORDER BY CASE WHEN top_order IS NULL THEN 1 ELSE 0 END, top_order ASC, created_at DESC"
    if profession:
        cursor.execute(f'SELECT * FROM specialists WHERE profession = %s {order_sql}', (profession,))
    else:
        cursor.execute(f'SELECT * FROM specialists {order_sql}')
    
    specialists = []
    rows = cursor.fetchall()
    for row in rows:
        specialists.append({
            'id': row['id'],
            'profession': row['profession'],
            'full_name': row['full_name'],
            'phone': row['phone'],
            'email': row['email'],
            'experience': row['experience'],
            'price': row.get('price', 'Kelishiladi'),
            'free_time': row['free_time'],
            'city': row['city'],
            'description': row['description'],
            'photo_url': row['photo_url'],
            'top_order': row['top_order'],
            'status': row['status'] if 'status' in row.keys() else None,
            'blocked_reason': row['blocked_reason'] if 'blocked_reason' in row.keys() else None,
            'blocked_at': row['blocked_at'] if 'blocked_at' in row.keys() else None,
            'telegram_chat_id': row['telegram_chat_id'] if 'telegram_chat_id' in row.keys() else None,
            'created_at': row['created_at']
        })
    
    conn.close()
    return jsonify({'specialists': specialists})


# Block a worker
@app.route('/api/admin/specialists/<int:specialist_id>/block', methods=['POST'])
def block_specialist(specialist_id):
    """Block a specialist from admin panel"""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json() or {}
    reason = sanitize_input(data.get('reason', ''), 500)
    
    if not reason:
        return jsonify({'error': 'Причина блокировки обязательна'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Get specialist info
    cursor.execute('SELECT * FROM specialists WHERE id = %s', (specialist_id,))
    specialist = cursor.fetchone()
    
    if not specialist:
        conn.close()
        return jsonify({'error': 'Специалист не найден'}), 404
    
    # Update status to blocked
    blocked_at = datetime.utcnow().isoformat()
    cursor.execute('''
        UPDATE specialists 
        SET status = 'blocked', blocked_reason = %s, blocked_at = %s
        WHERE id = %s
    ''', (reason, blocked_at, specialist_id))
    conn.commit()
    
    # Send Telegram notification if telegram_chat_id exists
    telegram_chat_id = specialist['telegram_chat_id'] if 'telegram_chat_id' in specialist.keys() else None
    if telegram_chat_id:
        bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        if bot_token:
            try:
                message = (
                    f"⛔ Sizning profilingiz bloklandi / Ваш профиль заблокирован\n\n"
                    f"Sabab / Причина: {reason}\n\n"
                    f"Agar xatolik deb o'ylasangiz — yordamga yozing:\n"
                    f"Если вы считаете, что это ошибка — обратитесь в поддержку:\n"
                    f"👉 https://t.me/Java2112"
                )
                requests.post(
                    f'https://api.telegram.org/bot{bot_token}/sendMessage',
                    json={'chat_id': telegram_chat_id, 'text': message},
                    timeout=5
                )
            except Exception as e:
                print(f"Failed to send Telegram notification: {e}")
    
    conn.close()
    return jsonify({'ok': True, 'message': 'Специалист заблокирован'})


# Unblock a worker
@app.route('/api/admin/specialists/<int:specialist_id>/unblock', methods=['POST'])
def unblock_specialist(specialist_id):
    """Unblock a specialist from admin panel"""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Check if specialist exists
    cursor.execute('SELECT * FROM specialists WHERE id = %s', (specialist_id,))
    specialist = cursor.fetchone()
    
    if not specialist:
        conn.close()
        return jsonify({'error': 'Специалист не найден'}), 404
    
    # Clear blocked status and restore to active or let date logic handle it
    cursor.execute('''
        UPDATE specialists 
        SET status = NULL, blocked_reason = NULL, blocked_at = NULL
        WHERE id = %s
    ''', (specialist_id,))
    conn.commit()
    
    # Send Telegram notification if telegram_chat_id exists
    telegram_chat_id = specialist['telegram_chat_id'] if 'telegram_chat_id' in specialist.keys() else None
    if telegram_chat_id:
        bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        if bot_token:
            try:
                message = (
                    "🎉 Tabriklaymiz! Siz blokdan chiqarildingiz!\n\n"
                    "Поздравляем! Вы разблокированы!\n\n"
                    "Endi profilingiz Premium Professionals platformasida yana faol bo'ladi."
                )
                requests.post(
                    f'https://api.telegram.org/bot{bot_token}/sendMessage',
                    json={'chat_id': telegram_chat_id, 'text': message},
                    timeout=5
                )
            except Exception as e:
                print(f"Failed to send Telegram notification: {e}")
    
    conn.close()
    
    return jsonify({'ok': True, 'message': 'Специалист разблокирован'})


# Список подписок/оплат для админ-панели
@app.route('/api/admin/subscriptions', methods=['GET'])
def admin_subscriptions():
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db(); cur = conn.cursor()
    cur.execute('''
        SELECT
            sub.id            AS subscription_id,
            sub.specialist_id AS specialist_id,
            sub.telegram_user_id,
            sub.full_name,
            sub.phone,
            sub.email,
            sub.started_at,
            sub.expires_at,
            sub.amount,
            sub.currency,
            sp.profession,
            sp.city,
            sp.created_at
        FROM subscriptions sub
        LEFT JOIN specialists sp ON sp.id = sub.specialist_id
        ORDER BY sub.created_at DESC
    ''')
    rows = cur.fetchall(); conn.close()
    subscriptions = []
    for row in rows:
        subscriptions.append({
            'subscription_id': row['subscription_id'],
            'specialist_id': row['specialist_id'],
            'telegram_user_id': row['telegram_user_id'],
            'full_name': row['full_name'],
            'phone': row['phone'],
            'email': row['email'],
            'started_at': row['started_at'],
            'expires_at': row['expires_at'],
            'amount': row['amount'],
            'currency': row['currency'],
            'profession': row['profession'],
            'city': row['city'],
            'created_at': row['created_at'],
        })
    return jsonify({'subscriptions': subscriptions})


@app.route('/api/admin/referral_stats', methods=['GET'])
def admin_referral_stats():
    """Get referral statistics for admin panel"""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Get top referrers
    cursor.execute('''
        SELECT 
            u.user_id, 
            u.username, 
            u.first_name, 
            COUNT(r.id) as referral_count,
            SUM(CASE WHEN r.status = 'activated' THEN 1 ELSE 0 END) as activated_count
        FROM bot_users u
        JOIN referrals r ON u.user_id = r.referrer_id
        GROUP BY u.user_id
        ORDER BY referral_count DESC
        LIMIT 50
    ''')
    
    stats = cursor.fetchall()
    conn.close()
    
    return jsonify({'referral_stats': stats})

# ----------------------------------------------------
#  PROFESSION MANAGEMENT (categories.json)
# ----------------------------------------------------
CATEGORIES_FILE = os.path.join(app.static_folder, 'data', 'categories.json')
SETTINGS_FILE = os.path.join(app.static_folder, 'data', 'settings.json')

def get_settings():
    import json
    if not os.path.exists(SETTINGS_FILE):
        return {}
    with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_settings(data):
    import json
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@app.route('/api/admin/set_top_specialist', methods=['POST'])
def admin_set_top_specialist():
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json or {}
    specialist_id = data.get('specialist_id')
    settings = get_settings()
    settings['top_specialist_id'] = specialist_id
    save_settings(settings)
    return jsonify({'ok': True, 'message': 'Top mutaxassis yangilandi!'})

@app.route('/api/admin/remove_top_specialist', methods=['POST'])
def admin_remove_top_specialist():
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    
    settings = get_settings()
    settings['top_specialist_id'] = None
    save_settings(settings)
    return jsonify({'ok': True, 'message': 'Top mutaxassis olib tashlandi!'})

@app.route('/api/top_specialist', methods=['GET'])
def get_top_specialist():
    settings = get_settings()
    top_id = settings.get('top_specialist_id')
    if not top_id:
        return jsonify({'specialist': None})
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM specialists WHERE id = %s', (top_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return jsonify({'specialist': None})
        
    specialist = {
        'id': row['id'],
        'profession': row['profession'],
        'full_name': row['full_name'],
        'photo_url': row['photo_url'],
        'city': row['city'],
        'top_order': row['top_order'],
        'description': row['description']
    }
    return jsonify({'specialist': specialist})

@app.route('/api/top_specialists', methods=['GET'])
def get_top_specialists():
    """Get multiple top specialists for the carousel"""
    settings = get_settings()
    top_id = settings.get('top_specialist_id')
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Get all active/trial specialists, limit 10
    cursor.execute('''
        SELECT * FROM specialists 
        WHERE (status IS NULL OR status IN ('active', 'trial'))
          AND (top_order IS NOT NULL OR id = %s)
        ORDER BY 
            CASE WHEN id = %s THEN 0 ELSE 1 END,
            top_order ASC,
            created_at DESC
        LIMIT 10
    ''', (top_id or 0, top_id or 0))
    
    rows = cursor.fetchall()
    conn.close()
    
    specialists = []
    for row in rows:
        specialists.append({
            'id': row['id'],
            'profession': row['profession'],
            'full_name': row['full_name'],
            'phone': row['phone'],
            'photo_url': row['photo_url'],
            'city': row['city'],
            'description': row['description'],
            'experience': row['experience'],
            'is_top': row['id'] == top_id if top_id else False
        })
    
    return jsonify({'specialists': specialists})

@app.route('/api/admin/professions', methods=['POST'])
def admin_create_profession():
    """Create a new profession with name and skills"""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    prof_name = data.get('prof_name')
    prof_skills = data.get('skills', '')
    category_key = data.get('category')
    
    if not prof_name or not category_key:
        return jsonify({'error': 'Missing name or category'}), 400
        
    if not os.path.exists(CATEGORIES_FILE):
         return jsonify({'error': 'Categories file not found'}), 500

    try:
        import json
        with open(CATEGORIES_FILE, 'r', encoding='utf-8') as f:
            content = json.load(f)
            
        # Find category and append profession as object
        found = False
        for cat in content.get('categories', []):
            if cat['key'] == category_key:
                # Check if profession already exists (by name)
                existing_names = [p['name'] if isinstance(p, dict) else p for p in cat['professions']]
                if prof_name in existing_names:
                    return jsonify({'error': 'Profession already exists'}), 400
                
                # Add as object with name and skills
                cat['professions'].append({
                    'name': prof_name,
                    'skills': prof_skills
                })
                found = True
                break
        
        if not found:
             return jsonify({'error': 'Category not found'}), 404
             
        with open(CATEGORIES_FILE, 'w', encoding='utf-8') as f:
            json.dump(content, f, ensure_ascii=False, indent=2)
            
        return jsonify({'success': True, 'message': 'Profession added'})
        
    except Exception as e:
        print(f"Error updating categories: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/categories', methods=['POST'])
def admin_create_category():
    """Create a new category"""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    category_title = data.get('title')
    category_key = data.get('key')
    category_icon = data.get('icon', 'work')  # Default icon if not provided
    
    if not category_title or not category_key:
        return jsonify({'error': 'Missing title or key'}), 400
    
    # Validate key format (lowercase, underscores only)
    if not re.match(r'^[a-z_]+$', category_key):
        return jsonify({'error': 'Key must be lowercase letters and underscores only'}), 400
        
    if not os.path.exists(CATEGORIES_FILE):
         return jsonify({'error': 'Categories file not found'}), 500

    try:
        import json
        with open(CATEGORIES_FILE, 'r', encoding='utf-8') as f:
            content = json.load(f)
            
        # Check if key already exists
        existing_keys = [cat['key'] for cat in content.get('categories', [])]
        if category_key in existing_keys:
            return jsonify({'error': 'Category key already exists'}), 400
        
        # Add new category AT THE BEGINNING (index 0)
        content['categories'].insert(0, {
            'key': category_key,
            'title': category_title,
            'icon': category_icon,
            'professions': []
        })
             
        with open(CATEGORIES_FILE, 'w', encoding='utf-8') as f:
            json.dump(content, f, ensure_ascii=False, indent=2)
            
        return jsonify({'success': True, 'message': 'Category created'}), 201
        
    except Exception as e:
        print(f"Error creating category: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/categories/<category_key>', methods=['DELETE'])
def admin_delete_category(category_key):
    """Delete a category and all its professions"""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
        
    if not os.path.exists(CATEGORIES_FILE):
         return jsonify({'error': 'Categories file not found'}), 500

    try:
        import json
        with open(CATEGORIES_FILE, 'r', encoding='utf-8') as f:
            content = json.load(f)
            
        # Find and remove category
        original_count = len(content.get('categories', []))
        content['categories'] = [cat for cat in content.get('categories', []) if cat['key'] != category_key]
        
        if len(content['categories']) == original_count:
            return jsonify({'error': 'Category not found'}), 404
             
        with open(CATEGORIES_FILE, 'w', encoding='utf-8') as f:
            json.dump(content, f, ensure_ascii=False, indent=2)
            
        return jsonify({'success': True, 'message': 'Category deleted'}), 200
        
    except Exception as e:
        print(f"Error deleting category: {e}")
        return jsonify({'error': str(e)}), 500


        
    except Exception as e:
        print(f"Error creating category: {e}")
        return jsonify({'error': str(e)}), 500


# ---- Advertisements endpoints ----

@app.route('/api/advertisements', methods=['GET'])
def get_advertisements():
    """Get all active advertisements"""
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT * FROM advertisements WHERE is_active = 1 ORDER BY position ASC, created_at DESC')
    rows = cur.fetchall(); conn.close()
    ads = []
    for row in rows:
        ads.append({
            'id': row['id'],
            'title': row['title'],
            'description': row['description'],
            'image_url': row['image_url'],
            'link_url': row['link_url'],
            'position': row['position'],
            'created_at': row['created_at']
        })
    return jsonify({'advertisements': ads})

@app.route('/api/admin/advertisements', methods=['GET'])
def admin_get_all_advertisements():
    """Get all advertisements (admin)"""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT * FROM advertisements ORDER BY position ASC, created_at DESC')
    rows = cur.fetchall(); conn.close()
    ads = []
    for row in rows:
        ads.append({
            'id': row['id'],
            'title': row['title'],
            'description': row['description'],
            'image_url': row['image_url'],
            'link_url': row['link_url'],
            'position': row['position'],
            'is_active': row['is_active'],
            'created_at': row['created_at']
        })
    return jsonify({'advertisements': ads})

@app.route('/api/admin/advertisements', methods=['POST'])
def admin_create_advertisement():
    """Create new advertisement (admin)"""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json() or {}
    title = sanitize_input(data.get('title', ''), 200)
    description = sanitize_input(data.get('description', ''), 500)
    image_url = sanitize_input(data.get('image_url', ''), 300)
    link_url = sanitize_input(data.get('link_url', ''), 300)
    position = int(data.get('position', 0))
    is_active = 1 if data.get('is_active', True) else 0
    
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    
    conn = get_db(); cur = conn.cursor()
    cur.execute('''
        INSERT INTO advertisements (title, description, image_url, link_url, position, is_active)
        VALUES (%s, %s, %s, %s, %s, %s)
    ''', (title, description, image_url, link_url, position, is_active))
    ad_id = cur.lastrowid
    conn.commit()
    
    cur.execute('SELECT * FROM advertisements WHERE id = %s', (ad_id,))
    row = cur.fetchone(); conn.close()
    return jsonify({'advertisement': {
        'id': row['id'],
        'title': row['title'],
        'description': row['description'],
        'image_url': row['image_url'],
        'link_url': row['link_url'],
        'position': row['position'],
        'is_active': row['is_active'],
        'created_at': row['created_at']
    }}), 201

@app.route('/api/admin/advertisements/<int:ad_id>', methods=['PUT'])
def admin_update_advertisement(ad_id):
    """Update advertisement (admin)"""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json() or {}
    fields = []
    values = []
    
    if 'title' in data:
        fields.append('title = %s')
        values.append(sanitize_input(data['title'], 200))
    if 'description' in data:
        fields.append('description = %s')
        values.append(sanitize_input(data['description'], 500))
    if 'image_url' in data:
        fields.append('image_url = %s')
        values.append(sanitize_input(data['image_url'], 300))
    if 'link_url' in data:
        fields.append('link_url = %s')
        values.append(sanitize_input(data['link_url'], 300))
    if 'position' in data:
        fields.append('position = %s')
        values.append(int(data['position']))
    if 'is_active' in data:
        fields.append('is_active = %s')
        values.append(1 if data['is_active'] else 0)
    
    if not fields:
        return jsonify({'error': 'No fields to update'}), 400
    
    values.append(ad_id)
    conn = get_db(); cur = conn.cursor()
    cur.execute(f"UPDATE advertisements SET {', '.join(fields)} WHERE id = %s", tuple(values))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/admin/advertisements/<int:ad_id>', methods=['DELETE'])
def admin_delete_advertisement(ad_id):
    """Delete advertisement (admin)"""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db(); cur = conn.cursor()
    cur.execute('DELETE FROM advertisements WHERE id = %s', (ad_id,))
    conn.commit(); conn.close()
    return jsonify({'ok': True})


# Раздача загруженных фото
@app.route('/uploads/<path:filename>')
def serve_uploads(filename):
    return send_from_directory(UPLOAD_DIR, filename)


# Вспомогательная функция отправки кода подтверждения на email
def send_verification_email(to_email: str, code: str) -> bool:
    host = os.environ.get('SMTP_HOST')
    port = int(os.environ.get('SMTP_PORT', '587'))
    user = os.environ.get('SMTP_USER')
    password = os.environ.get('SMTP_PASS')
    from_addr = os.environ.get('SMTP_FROM', user or '')
    if not host or not user or not password or not from_addr:
        print('SMTP is not configured; cannot send verification email')
        return False
    msg = MIMEText(f"Ваш код подтверждения: {code}", 'plain', 'utf-8')
    msg['Subject'] = 'Код подтверждения регистрации'
    msg['From'] = from_addr
    msg['To'] = to_email
    try:
        with smtplib.SMTP(host, port) as s:
            s.starttls()
            s.login(user, password)
            s.sendmail(from_addr, [to_email], msg.as_string())
        return True
    except Exception as e:
        print('Failed to send verification email:', e)
        return False


# Публичная точка для бота (добавление специалиста)
@app.route('/api/bot/specialists', methods=['POST'])
def bot_add_specialist():
    """Добавление специалиста из Telegram‑бота.

    Здесь же сразу стартует бесплатный пробный период (по умолчанию 1 месяц ~ 30 дней,
    можно изменить через переменную окружения TRIAL_SECONDS).
    """
    bot_key = request.headers.get('X-Bot-Token', '')
    if not bot_key or bot_key != os.environ.get('BOT_API_KEY', ''):
        return jsonify({'error': 'Forbidden'}), 403
    data = request.get_json() or {}
    # Ограниченно валидируем и санитизируем, доверяем боту базовую валидацию
    profession = sanitize_input(data.get('profession',''), 200)
    full_name = sanitize_input(data.get('full_name',''), 100)
    phone = sanitize_input(data.get('phone',''), 30)
    email = sanitize_input(data.get('email',''), 120)
    experience = int(data.get('experience') or 0)

    free_time = sanitize_input(data.get('free_time',''), 200)
    city = sanitize_input(data.get('city',''), 100)
    description = sanitize_input(data.get('description',''), 1000)
    photo_url = sanitize_input(data.get('photo_url',''), 300)
    telegram_chat_id = data.get('telegram_chat_id')  # Get Telegram chat ID
    country = ''  # для бота страна не используется

    conn = get_db(); cur = conn.cursor()
    cur.execute('''
        INSERT INTO specialists (profession, full_name, phone, email, experience, price, free_time, city, country, description, photo_url, top_order, telegram_chat_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ''', (profession, full_name, phone, email, experience, price, free_time, city, country, description, photo_url, None, telegram_chat_id))
    sid = cur.lastrowid

    # Старт бесплатного периода (по умолчанию ~1 месяц)
    trial_seconds = int(os.environ.get('TRIAL_SECONDS', str(30*24*3600)))
    now = datetime.utcnow()
    trial_started = now.isoformat()
    trial_expires = (now + timedelta(seconds=trial_seconds)).isoformat()
    try:
        cur.execute(
            "UPDATE specialists SET status = %s, trial_started_at = %s, trial_expires_at = %s WHERE id = %s",
            ('trial', trial_started, trial_expires, sid)
        )
        conn.commit()
    except Exception:
        conn.rollback()

    cur.execute('SELECT * FROM specialists WHERE id=%s', (sid,))
    row = cur.fetchone(); conn.close()
    return jsonify({'specialist': {
        'id': row['id'], 'profession': row['profession'], 'full_name': row['full_name'], 'phone': row['phone'],
        'email': row['email'], 'experience': row['experience'], 'price': row.get('price', 'Kelishiladi'), 'free_time': row['free_time'],
        'city': row['city'], 'description': row['description'], 'photo_url': row['photo_url'],
        'top_order': row['top_order'], 'created_at': row['created_at']
    }}), 201

# Продление / активация платного периода из бота
@app.route('/api/bot/specialists/<int:specialist_id>/activate', methods=['POST'])
def bot_activate_specialist(specialist_id):
    bot_key = request.headers.get('X-Bot-Token', '')
    if not bot_key or bot_key != os.environ.get('BOT_API_KEY', ''):
        return jsonify({'error': 'Forbidden'}), 403

    data = request.get_json() or {}
    telegram_user_id = data.get('telegram_user_id')

    conn = get_db(); cur = conn.cursor()
    now = datetime.utcnow()
    # 1 месяц (30 дней) платного периода
    paid_until = (now + timedelta(days=30)).isoformat()
    try:
        cur.execute(
            "UPDATE specialists SET status = %s, paid_until = %s WHERE id = %s",
            ('active', paid_until, specialist_id)
        )
        conn.commit()
        cur.execute('SELECT * FROM specialists WHERE id=%s', (specialist_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({'error': 'Not found'}), 404

        # Логируем подписку/оплату в таблицу subscriptions
        try:
            cur.execute('''
                INSERT INTO subscriptions (specialist_id, telegram_user_id, full_name, phone, email, started_at, expires_at, amount, currency)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                row['id'], telegram_user_id, row['full_name'], row['phone'], row['email'],
                now.isoformat(), paid_until, 5.0, 'USD'
            ))
            conn.commit()
        except Exception:
            conn.rollback()

        result = {
            'id': row['id'], 'profession': row['profession'], 'full_name': row['full_name'], 'phone': row['phone'],
            'email': row['email'], 'experience': row['experience'], 'price': row.get('price', 'Kelishiladi'), 'free_time': row['free_time'],
            'city': row['city'], 'description': row['description'], 'photo_url': row['photo_url'],
            'top_order': row['top_order'], 'created_at': row['created_at'],
            'status': row['status'], 'trial_started_at': row['trial_started_at'],
            'trial_expires_at': row['trial_expires_at'], 'paid_until': row['paid_until']
        }
        conn.close()
        return jsonify({'specialist': result}), 200
    except Exception:
        conn.rollback(); conn.close()
        return jsonify({'error': 'Update failed'}), 500

# ---- Auth endpoints for site ----

@app.route('/api/auth/signup', methods=['POST'])
def auth_signup():
    """Старт регистрации: создаём запись в email_verifications и отправляем код на почту.

    Финальное создание пользователя происходит в /api/auth/signup/confirm.
    """
    data = request.get_json() or {}
    name = sanitize_input(data.get('name', ''), 100)
    email = (data.get('email', '') or '').strip().lower()
    phone = (data.get('phone', '') or '').strip()
    role = (data.get('role', '') or 'worker').strip()
    password = data.get('password', '') or ''

    if role not in ('client', 'worker'):
        return jsonify({'error': 'Invalid role'}), 400
    if not email or not validate_email(email):
        return jsonify({'error': 'Неверный email'}), 400
    if phone and not validate_phone(phone):
        return jsonify({'error': 'Неверный формат номера телефона. Используйте формат: +998901234567'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Пароль должен содержать минимум 6 символов'}), 400
    if len(name) < 2:
        return jsonify({'error': 'Имя должно содержать минимум 2 символа'}), 400

    conn = get_db(); cur = conn.cursor()
    try:
        # Проверяем, что такого пользователя ещё нет
        cur.execute('SELECT id FROM users WHERE email = %s', (email,))
        if cur.fetchone():
            conn.close()
            return jsonify({'error': 'Email уже зарегистрирован'}), 400
        if phone:
            cur.execute('SELECT id FROM users WHERE phone = %s', (phone,))
            if cur.fetchone():
                conn.close()
                return jsonify({'error': 'Телефон уже зарегистрирован'}), 400

        # Генерируем 6-значный код
        import random
        code = f"{random.randint(0, 999999):06d}"
        expires_at = (datetime.utcnow() + timedelta(minutes=15)).isoformat()

        # Сохраняем/перезаписываем запись в email_verifications
        cur.execute('DELETE FROM email_verifications WHERE email = %s', (email,))
        cur.execute('''
            INSERT INTO email_verifications (email, code, name, phone, role, password_hash, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        ''', (
            email, code, name, phone or None, role, generate_password_hash(password), expires_at
        ))
        conn.commit()
    except Exception:
        conn.rollback(); conn.close()
        return jsonify({'error': 'Ошибка подготовки регистрации'}), 500

    # Пытаемся отправить код по email
    if not send_verification_email(email, code):
        return jsonify({'error': 'Не удалось отправить код на email. Проверьте SMTP настройки.'}), 500

    return jsonify({'ok': True}), 200


@app.route('/api/auth/signup/confirm', methods=['POST'])
def auth_signup_confirm():
    """Подтверждение регистрации кодом из email: создаём аккаунт в таблице users."""
    data = request.get_json() or {}
    email = (data.get('email', '') or '').strip().lower()
    code = (data.get('code', '') or '').strip()
    if not email or not code:
        return jsonify({'error': 'Укажите email и код подтверждения'}), 400

    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT * FROM email_verifications WHERE email = %s AND code = %s ORDER BY created_at DESC LIMIT 1', (email, code))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Неверный код'}), 400

    # Проверяем срок действия кода
    expires_at = row['expires_at']
    if expires_at:
        try:
            if datetime.fromisoformat(expires_at) < datetime.utcnow():
                conn.close()
                return jsonify({'error': 'Срок действия кода истёк'}), 400
        except Exception:
            pass

    # Дополнительно проверим, что пользователь ещё не создан
    cur.execute('SELECT id FROM users WHERE email = %s', (email,))
    if cur.fetchone():
        conn.close()
        return jsonify({'error': 'Email уже зарегистрирован'}), 400
    if row['phone']:
        cur.execute('SELECT id FROM users WHERE phone = %s', (row['phone'],))
        if cur.fetchone():
            conn.close()
            return jsonify({'error': 'Телефон уже зарегистрирован'}), 400

    try:
        cur.execute(
            'INSERT INTO users (name, email, phone, role, password_hash) VALUES (%s, %s, %s, %s, %s)',
            (row['name'], row['email'], row['phone'], row['role'] or 'worker', row['password_hash'])
        )
        uid = cur.lastrowid
        cur.execute('DELETE FROM email_verifications WHERE email = %s', (email,))
        conn.commit()
        cur.execute('SELECT id, name, email, phone, role FROM users WHERE id = %s', (uid,))
        u = cur.fetchone()
        conn.close()
    except Exception:
        conn.rollback(); conn.close()
        return jsonify({'error': 'Ошибка создания пользователя'}), 500

    session['user_id'] = u['id']
    session['user_role'] = u['role']
    return jsonify({'user': {
        'id': u['id'], 'name': u['name'], 'email': u['email'],
        'phone': u['phone'], 'role': u['role']
    }})

@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    data = request.get_json() or {}
    email = (data.get('email', '') or '').strip().lower()
    password = data.get('password', '') or ''
    if not email or not password:
        return jsonify({'error': 'Укажите email и пароль'}), 400

    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE email = %s', (email,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'Аккаунт не найден. Зарегистрируйтесь.'}), 400
    if not check_password_hash(row['password_hash'], password):
        return jsonify({'error': 'Неверный email или пароль'}), 400

    session['user_id'] = row['id']
    session['user_role'] = row['role']
    return jsonify({'user': {
        'id': row['id'], 'name': row['name'], 'email': row['email'],
        'phone': row['phone'], 'role': row['role']
    }})

@app.route('/api/auth/me', methods=['GET'])
def auth_me():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'user': None})
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT id, name, email, phone, role FROM users WHERE id = %s', (uid,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({'user': None})
    return jsonify({'user': {
        'id': row['id'], 'name': row['name'], 'email': row['email'],
        'phone': row['phone'], 'role': row['role']
    }})

@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    session.pop('user_id', None)
    session.pop('user_role', None)
    return jsonify({'ok': True})


# Broadcast message to all Telegram users
@app.route('/api/admin/broadcast', methods=['POST'])
def admin_broadcast():
    """Send a broadcast message to all Telegram bot users"""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json() or {}
    message = sanitize_input(data.get('message', ''), 2000)
    image_url = sanitize_input(data.get('image_url', ''), 500)
    
    if not message:
        return jsonify({'error': 'Message is required'}), 400
    
    # Get all Telegram chat IDs from specialists who have them
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT telegram_chat_id FROM specialists WHERE telegram_chat_id IS NOT NULL')
    rows = cursor.fetchall()
    conn.close()
    
    chat_ids = [row['telegram_chat_id'] for row in rows if row['telegram_chat_id']]
    
    if not chat_ids:
        return jsonify({'error': 'No Telegram users found'}), 404
    
    # Send messages
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    if not bot_token:
        return jsonify({'error': 'Telegram bot token not configured'}), 500
    
    success_count = 0
    error_count = 0
    
    for chat_id in chat_ids:
        try:
            if image_url:
                # Send photo with caption
                payload = {
                    'chat_id': chat_id,
                    'photo': image_url,
                    'caption': message
                }
                response = requests.post(
                    f'https://api.telegram.org/bot{bot_token}/sendPhoto',
                    json=payload,
                    timeout=10
                )
            else:
                # Send text message
                payload = {
                    'chat_id': chat_id,
                    'text': message
                }
                response = requests.post(
                    f'https://api.telegram.org/bot{bot_token}/sendMessage',
                    json=payload,
                    timeout=10
                )
            
            if response.status_code == 200:
                success_count += 1
            else:
                error_count += 1
                print(f"Failed to send message to chat {chat_id}: {response.text}")
                
        except Exception as e:
            error_count += 1
            print(f"Exception sending message to chat {chat_id}: {e}")
    
    return jsonify({
        'ok': True,
        'message': f'Messages sent: {success_count}, Errors: {error_count}'
    })


# Targeted broadcast message to specific Telegram users
@app.route('/api/admin/targeted-broadcast', methods=['POST'])
def admin_targeted_broadcast():
    """Send a broadcast message to specific Telegram bot users"""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json() or {}
    message = sanitize_input(data.get('message', ''), 2000)
    image_url = sanitize_input(data.get('image_url', ''), 500)
    chat_ids = data.get('chat_ids', [])
    
    if not message:
        return jsonify({'error': 'Message is required'}), 400
    
    if not chat_ids or not isinstance(chat_ids, list):
        return jsonify({'error': 'Chat IDs are required'}), 400
    
    # Filter out invalid chat IDs
    valid_chat_ids = [str(chat_id) for chat_id in chat_ids if chat_id]
    
    if not valid_chat_ids:
        return jsonify({'error': 'No valid chat IDs provided'}), 400
    
    # Send messages
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    if not bot_token:
        return jsonify({'error': 'Telegram bot token not configured'}), 500
    
    success_count = 0
    error_count = 0
    
    for chat_id in valid_chat_ids:
        try:
            if image_url:
                # Send photo with caption
                payload = {
                    'chat_id': chat_id,
                    'photo': image_url,
                    'caption': message
                }
                response = requests.post(
                    f'https://api.telegram.org/bot{bot_token}/sendPhoto',
                    json=payload,
                    timeout=10
                )
            else:
                # Send text message
                payload = {
                    'chat_id': chat_id,
                    'text': message
                }
                response = requests.post(
                    f'https://api.telegram.org/bot{bot_token}/sendMessage',
                    json=payload,
                    timeout=10
                )
            
            if response.status_code == 200:
                success_count += 1
            else:
                error_count += 1
                print(f"Failed to send message to chat {chat_id}: {response.text}")
                
        except Exception as e:
            error_count += 1
            print(f"Exception sending message to chat {chat_id}: {e}")
    
    return jsonify({
        'ok': True,
        'message': f'Messages sent: {success_count}, Errors: {error_count}'
    })

if __name__ == '__main__':
    # Если БД отсутствует — создать и инициализировать, иначе убедиться, что миграции применены
    if not os.path.exists(DATABASE) or os.path.getsize(DATABASE) == 0:
        init_db()
        print('База данных инициализирована!')
    else:
        # Запустить легкую миграцию для добавления новых столбцов при старой БД
        try:
            conn = sqlite3.connect(DATABASE)
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(specialists)")
            cols = [row[1] for row in cursor.fetchall()]
            if 'photo_url' not in cols:
                cursor.execute("ALTER TABLE specialists ADD COLUMN photo_url TEXT")
            if 'top_order' not in cols:
                cursor.execute("ALTER TABLE specialists ADD COLUMN top_order INTEGER")
            if 'city' not in cols:
                cursor.execute("ALTER TABLE specialists ADD COLUMN city TEXT")
            if 'country' not in cols:
                cursor.execute("ALTER TABLE specialists ADD COLUMN country TEXT")
            if 'status' not in cols:
                cursor.execute("ALTER TABLE specialists ADD COLUMN status TEXT")
            if 'trial_started_at' not in cols:
                cursor.execute("ALTER TABLE specialists ADD COLUMN trial_started_at TEXT")
            if 'trial_expires_at' not in cols:
                cursor.execute("ALTER TABLE specialists ADD COLUMN trial_expires_at TEXT")
            if 'paid_until' not in cols:
                cursor.execute("ALTER TABLE specialists ADD COLUMN paid_until TEXT")
            # Убедимся, что таблицы users/email_verifications/subscriptions тоже есть
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    email TEXT UNIQUE NOT NULL,
                    phone TEXT UNIQUE,
                    role TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS email_verifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT,
                    phone TEXT,
                    role TEXT,
                    password_hash TEXT NOT NULL,
                    expires_at TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    specialist_id INTEGER NOT NULL,
                    telegram_user_id INTEGER,
                    full_name TEXT,
                    phone TEXT,
                    email TEXT,
                    started_at TEXT,
                    expires_at TEXT,
                    amount REAL,
                    currency TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS advertisements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT,
                    image_url TEXT,
                    link_url TEXT,
                    position INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            conn.close()
        except Exception as _:
            pass
    

# ---- Broadcast & Subscription Management ----

@app.route('/api/admin/broadcast', methods=['POST'])
def send_broadcast_all():
    """Send broadcast to ALL users (bot_users + specialists)"""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    message = data.get('message')
    image_url = data.get('image_url')
    
    if not message:
        return jsonify({'error': 'Message required'}), 400

    conn = get_db()
    cursor = conn.cursor()
    
    # 1. Get all from bot_users
    try:
        cursor.execute("SELECT user_id FROM bot_users")
        bot_users = {row[0] for row in cursor.fetchall() if row[0]}
    except:
        bot_users = set()
    
    # 2. Get all from specialists (legacy fallback)
    cursor.execute("SELECT telegram_chat_id FROM specialists WHERE telegram_chat_id IS NOT NULL")
    spec_users = {row[0] for row in cursor.fetchall() if row[0]}
    
    # Union all unique IDs
    all_users = bot_users.union(spec_users)
    conn.close()
    
    if not all_users:
        return jsonify({'error': 'No users found'}), 404

    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        # Fallback to verify with print if no token (dev mode)
        print(f"Broadcast simulated to {len(all_users)} users: {message}")
        return jsonify({'ok': True, 'count': len(all_users), 'simulated': True})

    success_count = 0
    
    def _send(chat_id):
        try:
            payload = {'chat_id': chat_id, 'text': message}
            if image_url:
                payload['caption'] = message
                del payload['text']
                payload['photo'] = image_url
                requests.post(f'https://api.telegram.org/bot{bot_token}/sendPhoto', json=payload, timeout=2)
            else:
                requests.post(f'https://api.telegram.org/bot{bot_token}/sendMessage', json=payload, timeout=2)
            return True
        except:
            return False

    # In a real production app, do this asynchronously (Celery/RQ)
    # For now, we do a simple loop with short timeout
    for uid in all_users:
        if _send(uid):
            success_count += 1
            
    return jsonify({'ok': True, 'count': success_count, 'total': len(all_users)})


@app.route('/api/admin/specialists/<int:sid>/subscription', methods=['POST'])
def update_subscription(sid):
    """Update subscription expiry manually"""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    action = data.get('action') # 'add_month', 'reset_trial', 'set_expired'
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM specialists WHERE id = %s", (sid,))
    spec = cursor.fetchone()
    if not spec:
        conn.close()
        return jsonify({'error': 'Specialist not found'}), 404

    new_date = None
    
    if action == 'add_month':
        # Add 30 days to current expiry or now
        current_exp = spec['trial_expires_at'] or spec['paid_until']
        if current_exp:
            try:
                base = datetime.fromisoformat(current_exp)
                # if base < datetime.utcnow(): base = datetime.utcnow() # Allow extending from past date if wanted, but usually extend from NOW if expired
                if base < datetime.utcnow(): base = datetime.utcnow()
            except:
                base = datetime.utcnow()
        else:
            base = datetime.utcnow()
        new_date = (base + timedelta(days=30)).isoformat()
        cursor.execute("UPDATE specialists SET paid_until = %s WHERE id = %s", (new_date, sid))
        
    elif action == 'reset_trial':
        # Set to 5 months from now
        new_date = (datetime.utcnow() + timedelta(days=150)).isoformat()
        cursor.execute("UPDATE specialists SET trial_expires_at = %s, paid_until = NULL, status = 'active' WHERE id = %s", (new_date, sid))
        
    elif action == 'set_expired':
        # Set to yesterday
        new_date = (datetime.utcnow() - timedelta(days=1)).isoformat()
        cursor.execute("UPDATE specialists SET trial_expires_at = %s, paid_until = NULL, status = 'expired' WHERE id = %s", (new_date, sid))
        
        # Notify user about expiry and ask for payment
        if spec['telegram_chat_id']:
            bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
            if bot_token:
                try:
                    txt = (
                        "Hurmatli mijoz, sizning bepul sinov davringiz tugadi.\n"
                        "Profilni faollashtirish uchun to'lov qilishingiz kerak.\n\n"
                        "Уважаемый клиент, ваш пробный период истек.\n"
                        "Для активации профиля необходимо произвести оплату."
                    )
                    # Inline keyboard with "Send Receipt" button
                    kb = {
                        "inline_keyboard": [[
                            {"text": "✅ Chekni yuborish / Отправить чек", "callback_data": f"paystart:{sid}"}
                        ]]
                    }
                    requests.post(
                        f'https://api.telegram.org/bot{bot_token}/sendMessage',
                        json={'chat_id': spec['telegram_chat_id'], 'text': txt, 'reply_markup': kb},
                        timeout=2
                    )
                except Exception as e:
                    print(f"Failed to notify expiry: {e}")

    conn.commit()
    conn.close()
    
    return jsonify({'ok': True, 'new_date': new_date})

@app.route('/api/admin/targeted-broadcast', methods=['POST'])
def send_targeted_broadcast():
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
        
    data = request.get_json()
    message = data.get('message')
    image_url = data.get('image_url')
    chat_ids = data.get('chat_ids', [])
    
    if not message:
        return jsonify({'error': 'Message required'}), 400
    
    if not chat_ids:
        return jsonify({'error': 'No recipients provided'}), 400
        
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        # Fallback
        print(f"Targeted broadcast simulated to {len(chat_ids)} users")
        return jsonify({'ok': True, 'count': len(chat_ids), 'simulated': True})
        
    success_count = 0
    
    for chat_id in chat_ids:
        try:
            payload = {'chat_id': chat_id, 'text': message}
            if image_url:
                payload['caption'] = message
                del payload['text']
                payload['photo'] = image_url
                requests.post(f'https://api.telegram.org/bot{bot_token}/sendPhoto', json=payload, timeout=5)
            else:
                requests.post(f'https://api.telegram.org/bot{bot_token}/sendMessage', json=payload, timeout=5)
            success_count += 1
        except Exception as e:
            print(f"Failed to send to {chat_id}: {e}")
            
    return jsonify({'ok': True, 'count': success_count})
@app.route('/api/admin/professions', methods=['DELETE'])
def admin_delete_profession():
    """Delete a profession from a category"""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    
    prof_name = request.args.get('prof_name')
    category_key = request.args.get('category_key')
    
    if not prof_name or not category_key:
        return jsonify({'error': 'Missing prof_name or category_key'}), 400
        
    if not os.path.exists(CATEGORIES_FILE):
         return jsonify({'error': 'Categories file not found'}), 500

    try:
        import json
        with open(CATEGORIES_FILE, 'r', encoding='utf-8') as f:
            content = json.load(f)
            
        # Find category
        category = next((c for c in content.get('categories', []) if c['key'] == category_key), None)
        if not category:
            return jsonify({'error': 'Category not found'}), 404
            
        # Filter out the profession
        original_len = len(category['professions'])
        category['professions'] = [
            p for p in category['professions'] 
            if (isinstance(p, dict) and p['name'] != prof_name) or (isinstance(p, str) and p != prof_name)
        ]
        
        if len(category['professions']) == original_len:
             return jsonify({'error': 'Profession not found'}), 404

        with open(CATEGORIES_FILE, 'w', encoding='utf-8') as f:
            json.dump(content, f, ensure_ascii=False, indent=2)
            
        return jsonify({'success': True, 'message': 'Profession deleted'})
        
    except Exception as e:
        print(f"Error deleting profession: {e}")
        return jsonify({'error': str(e)}), 500


# ---- Mobile API endpoints ----

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
ADMIN_TELEGRAM_ID = os.environ.get('ADMIN_TELEGRAM_ID', '')
BOT_USERNAME = os.environ.get('BOT_USERNAME', '')  # Bot username (without @)


def send_telegram_message(chat_id, text, reply_markup=None):
    """Telegram Bot API orqali xabar yuborish."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML',
    }
    if reply_markup:
        import json
        payload['reply_markup'] = json.dumps(reply_markup)
    try:
        resp = requests.post(url, json=payload, timeout=10)
        return resp.json()
    except Exception as e:
        print(f"Telegram xabar yuborishda xatolik: {e}")
        return None


def send_telegram_photo(chat_id, photo_path, caption, reply_markup=None):
    """Telegram Bot API orqali rasm yuborish."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    data = {
        'chat_id': chat_id,
        'caption': caption,
        'parse_mode': 'HTML',
    }
    if reply_markup:
        import json
        data['reply_markup'] = json.dumps(reply_markup)
    try:
        with open(photo_path, 'rb') as f:
            resp = requests.post(url, data=data, files={'photo': f}, timeout=15)
        return resp.json()
    except Exception as e:
        print(f"Telegram rasm yuborishda xatolik: {e}")
        return None


@app.route('/api/mobile/application', methods=['POST'])
def mobile_submit_application():
    """Mobile ilovadan mutaxassis arizasi qabul qilish.

    Form data (multipart/form-data):
        - telegram_user_id (required): Telegram user ID
        - username: Telegram username
        - profession (required): Kasbi
        - full_name (required): To'liq ismi
        - phone (required): Telefon raqami
        - city: Shahar
        - experience: Tajriba (yil)
        - price: Narxi
        - free_time: Bo'sh vaqti
        - description: Tavsif
        - photo: Rasm fayli (optional)
    """
    # Form data olish (multipart/form-data yoki JSON)
    if request.content_type and 'multipart/form-data' in request.content_type:
        data = request.form.to_dict()
        photo = request.files.get('photo')
    elif request.is_json:
        data = request.get_json() or {}
        photo = None
    else:
        return jsonify({'error': 'Content-Type multipart/form-data yoki application/json bo\'lishi kerak'}), 400

    # Majburiy maydonlar tekshirish
    telegram_user_id = data.get('telegram_user_id')
    profession = data.get('profession', '').strip()
    full_name = data.get('full_name', '').strip()
    phone = data.get('phone', '').strip()

    if not telegram_user_id:
        return jsonify({'error': 'telegram_user_id majburiy'}), 400
    if not profession:
        return jsonify({'error': 'profession majburiy'}), 400
    if not full_name:
        return jsonify({'error': 'full_name majburiy'}), 400
    if not phone:
        return jsonify({'error': 'phone majburiy'}), 400

    try:
        telegram_user_id = int(telegram_user_id)
    except (ValueError, TypeError):
        return jsonify({'error': 'telegram_user_id raqam bo\'lishi kerak'}), 400

    # Sanitize
    profession = sanitize_input(profession, 200)
    full_name = sanitize_input(full_name, 100)
    phone = sanitize_input(phone, 30)
    username = sanitize_input(data.get('username', ''), 100)
    city = sanitize_input(data.get('city', ''), 100)
    experience = int(data.get('experience') or 0)
    price = sanitize_input(data.get('price', 'Kelishiladi'), 100)
    free_time = sanitize_input(data.get('free_time', ''), 200)
    description = sanitize_input(data.get('description', ''), 1000)

    # Avval shu user uchun pending ariza borligini tekshirish
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM applications WHERE user_id = %s AND status = 'pending'",
        (telegram_user_id,)
    )
    existing = cur.fetchone()
    if existing:
        conn.close()
        return jsonify({'error': 'Sizda allaqachon ko\'rib chiqilayotgan ariza mavjud', 'application_id': existing['id']}), 409

    # Rasm saqlash
    photo_path = ''
    if photo and photo.filename:
        import uuid
        ext = os.path.splitext(photo.filename)[1] or '.jpg'
        filename = f"app_{telegram_user_id}_{uuid.uuid4().hex[:8]}{ext}"
        photo_path = os.path.join(UPLOAD_DIR, filename)
        photo.save(photo_path)
    elif data.get('photo_base64'):
        # Base64 formatda rasm qabul qilish (mobile uchun qulay)
        import uuid
        try:
            img_data = base64.b64decode(data['photo_base64'])
            filename = f"app_{telegram_user_id}_{uuid.uuid4().hex[:8]}.jpg"
            photo_path = os.path.join(UPLOAD_DIR, filename)
            with open(photo_path, 'wb') as f:
                f.write(img_data)
        except Exception:
            pass

    # DB ga saqlash
    try:
        cur.execute('''
            INSERT INTO applications (user_id, username, profession, full_name, phone, city, experience, price, free_time, description, photo_path, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
        ''', (
            telegram_user_id, username, profession, full_name, phone,
            city, experience, price, free_time, description, photo_path
        ))
        app_id = cur.lastrowid
        conn.commit()
    except Exception as e:
        conn.close()
        print(f"DB xatolik: {e}")
        return jsonify({'error': 'Arizani saqlashda xatolik'}), 500
    finally:
        conn.close()

    # Adminga Telegram orqali xabar yuborish
    if ADMIN_TELEGRAM_ID:
        caption = (
            f"<b>Yangi ariza #{app_id}</b> (Mobile)\n\n"
            f"<b>Kasb:</b> {profession}\n"
            f"<b>F.I.Sh.:</b> {full_name}\n"
            f"<b>Tel:</b> {phone}\n"
            f"<b>Shahar:</b> {city or 'Online'}\n"
            f"<b>Tajriba:</b> {experience} yil\n"
            f"<b>Narx:</b> {price}\n"
            f"<b>Vaqt:</b> {free_time or '-'}\n\n"
            f"{description or ''}"
        )
        inline_keyboard = {
            'inline_keyboard': [[
                {'text': '\u2705 Qabul qilish', 'callback_data': f'approve:{app_id}'},
                {'text': '\u274c Inkor qilish', 'callback_data': f'reject:{app_id}'}
            ]]
        }
        if photo_path and os.path.exists(photo_path):
            send_telegram_photo(ADMIN_TELEGRAM_ID, photo_path, caption, inline_keyboard)
        else:
            send_telegram_message(ADMIN_TELEGRAM_ID, caption, inline_keyboard)

    return jsonify({
        'success': True,
        'message': 'Arizangiz qabul qilindi. Admin ko\'rib chiqadi.',
        'application_id': app_id
    }), 201


@app.route('/api/mobile/application/<int:app_id>', methods=['GET'])
def mobile_get_application_status(app_id):
    """Ariza holatini tekshirish."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, status, created_at FROM applications WHERE id = %s", (app_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'Ariza topilmadi'}), 404
    return jsonify({
        'application_id': row['id'],
        'status': row['status'],
        'created_at': str(row['created_at'])
    })


@app.route('/api/mobile/specialist/status', methods=['GET'])
def mobile_get_specialist_status():
    """Telegram user ID bo'yicha mutaxassis holatini tekshirish."""
    telegram_user_id = request.args.get('telegram_user_id')
    if not telegram_user_id:
        return jsonify({'error': 'telegram_user_id parametri majburiy'}), 400
    try:
        telegram_user_id = int(telegram_user_id)
    except (ValueError, TypeError):
        return jsonify({'error': 'telegram_user_id raqam bo\'lishi kerak'}), 400

    conn = get_db()
    cur = conn.cursor()

    # Avval specialist jadvalida bor-yo'qligini tekshirish
    cur.execute("SELECT * FROM specialists WHERE telegram_chat_id = %s", (telegram_user_id,))
    specialist = cur.fetchone()
    if specialist:
        conn.close()
        return jsonify({
            'registered': True,
            'specialist': {
                'id': specialist['id'],
                'profession': specialist['profession'],
                'full_name': specialist['full_name'],
                'phone': specialist['phone'],
                'status': specialist['status'],
                'city': specialist['city'],
                'photo_url': specialist['photo_url'],
                'trial_expires_at': specialist['trial_expires_at'],
                'paid_until': specialist['paid_until'],
                'created_at': str(specialist['created_at'])
            }
        })

    # Arizasi borligini tekshirish
    cur.execute(
        "SELECT id, status, created_at FROM applications WHERE user_id = %s ORDER BY created_at DESC LIMIT 1",
        (telegram_user_id,)
    )
    application = cur.fetchone()
    conn.close()

    if application:
        return jsonify({
            'registered': False,
            'application': {
                'id': application['id'],
                'status': application['status'],
                'created_at': str(application['created_at'])
            }
        })

    return jsonify({'registered': False, 'application': None})


@app.route('/api/mobile/me', methods=['GET'])
def mobile_get_me():
    """Mobile foydalanuvchining to'liq profilini olish."""
    telegram_user_id = request.args.get('telegram_user_id')
    if not telegram_user_id:
        return jsonify({'error': 'telegram_user_id parametri majburiy'}), 400
    try:
        telegram_user_id = int(telegram_user_id)
    except (ValueError, TypeError):
        return jsonify({'error': "telegram_user_id raqam bo'lishi kerak"}), 400

    conn = get_db()
    cur = conn.cursor()

    # 1. Bot user info
    cur.execute("SELECT * FROM bot_users WHERE user_id = %s", (telegram_user_id,))
    bot_user = cur.fetchone()

    # 2. Specialist info
    cur.execute("SELECT * FROM specialists WHERE telegram_chat_id = %s", (telegram_user_id,))
    specialist = cur.fetchone()

    # 3. Oxirgi ariza
    cur.execute(
        "SELECT id, status, profession, full_name, created_at FROM applications WHERE user_id = %s ORDER BY created_at DESC LIMIT 1",
        (telegram_user_id,)
    )
    application = cur.fetchone()

    # 4. Obuna holati
    subscription = None
    if specialist:
        cur.execute(
            "SELECT * FROM subscriptions WHERE specialist_id = %s ORDER BY created_at DESC LIMIT 1",
            (specialist['id'],)
        )
        subscription = cur.fetchone()

    # 5. Referral stats
    cur.execute("SELECT COUNT(*) as count FROM referrals WHERE referrer_id = %s", (telegram_user_id,))
    referral_count = cur.fetchone()['count']

    cur.execute("SELECT COUNT(*) as count FROM referrals WHERE referrer_id = %s AND status = 'activated'", (telegram_user_id,))
    activated_referrals = cur.fetchone()['count']

    conn.close()

    result = {
        'telegram_user_id': telegram_user_id,
        'user': {
            'username': bot_user['username'] if bot_user else None,
            'first_name': bot_user['first_name'] if bot_user else None,
            'last_name': bot_user['last_name'] if bot_user else None,
            'joined_at': str(bot_user['joined_at']) if bot_user else None,
        } if bot_user else None,
        'specialist': {
            'id': specialist['id'],
            'profession': specialist['profession'],
            'full_name': specialist['full_name'],
            'phone': specialist['phone'],
            'email': specialist['email'],
            'experience': specialist['experience'],
            'price': specialist['price'],
            'city': specialist['city'],
            'country': specialist['country'],
            'description': specialist['description'],
            'photo_url': specialist['photo_url'],
            'status': specialist['status'],
            'trial_expires_at': specialist['trial_expires_at'],
            'paid_until': specialist['paid_until'],
            'created_at': str(specialist['created_at']),
        } if specialist else None,
        'application': {
            'id': application['id'],
            'status': application['status'],
            'profession': application['profession'],
            'full_name': application['full_name'],
            'created_at': str(application['created_at']),
        } if application else None,
        'subscription': {
            'started_at': subscription['started_at'],
            'expires_at': subscription['expires_at'],
            'amount': subscription['amount'],
            'currency': subscription['currency'],
        } if subscription else None,
        'referrals': {
            'total': referral_count,
            'activated': activated_referrals,
        }
    }

    return jsonify(result)


# ---- Mobile Device Registration & Push Notifications ----

@app.route('/api/mobile/device/register', methods=['POST'])
def mobile_device_register():
    """Mobile qurilma FCM tokenini ro'yxatdan o'tkazish."""
    data = request.get_json() or {}
    token = data.get('token', '').strip()
    platform = data.get('platform', '').strip().lower()
    telegram_user_id = data.get('telegram_user_id')

    if not token or not platform or not telegram_user_id:
        return jsonify({'error': 'token, platform va telegram_user_id majburiy'}), 400

    if platform not in ('android', 'ios'):
        return jsonify({'error': "platform faqat 'android' yoki 'ios' bo'lishi kerak"}), 400

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, telegram_user_id FROM device_tokens WHERE token = %s", (token,))
        existing = cur.fetchone()
        if existing:
            if existing['telegram_user_id'] == telegram_user_id:
                return jsonify({'success': True, 'message': 'Token allaqachon mavjud'}), 200
            # Token boshqa userda — yangilash
            cur.execute(
                "UPDATE device_tokens SET telegram_user_id = %s, platform = %s WHERE token = %s",
                (telegram_user_id, platform, token)
            )
        else:
            cur.execute(
                "INSERT INTO device_tokens (telegram_user_id, token, platform) VALUES (%s, %s, %s)",
                (telegram_user_id, token, platform)
            )
        conn.commit()
    finally:
        conn.close()

    return jsonify({'success': True})


@app.route('/api/mobile/device/unregister', methods=['DELETE'])
def mobile_device_unregister():
    """Logout paytida FCM tokenni o'chirish."""
    data = request.get_json() or {}
    token = data.get('token', '').strip()

    if not token:
        return jsonify({'error': 'token majburiy'}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM device_tokens WHERE token = %s", (token,))
    affected = cur.rowcount
    conn.commit()
    conn.close()

    if affected == 0:
        return jsonify({'error': 'Token topilmadi'}), 404

    return jsonify({'success': True})


@app.route('/api/admin/devices', methods=['GET'])
def admin_devices():
    """Admin panelda ro'yxatdan o'tgan mobile qurilmalar ro'yxati."""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT dt.id, dt.telegram_user_id, dt.token, dt.platform, dt.created_at, dt.updated_at,
               bu.first_name, bu.username
        FROM device_tokens dt
        LEFT JOIN bot_users bu ON dt.telegram_user_id = bu.user_id
        ORDER BY dt.created_at DESC
    """)
    devices = cur.fetchall()
    conn.close()

    for d in devices:
        if d.get('created_at'):
            d['created_at'] = str(d['created_at'])
        if d.get('updated_at'):
            d['updated_at'] = str(d['updated_at'])

    return jsonify({'devices': devices, 'total': len(devices), 'firebase_initialized': _firebase_initialized})


@app.route('/api/admin/devices/<int:device_id>', methods=['DELETE'])
def admin_delete_device(device_id):
    """Admin paneldan qurilma tokenini o'chirish."""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401

    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM device_tokens WHERE id = %s", (device_id,))
    affected = cur.rowcount
    conn.commit()
    conn.close()

    if affected == 0:
        return jsonify({'error': 'Qurilma topilmadi'}), 404

    return jsonify({'success': True})


@app.route('/api/admin/push-broadcast', methods=['POST'])
def admin_push_broadcast():
    """Admin paneldan barcha mobile qurilmalarga push notification yuborish."""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401

    if not _firebase_initialized:
        return jsonify({'error': 'Firebase sozlanmagan. Push notification yuborib bo\'lmaydi.'}), 500

    data = request.get_json() or {}
    title = sanitize_input(data.get('title', ''), 200)
    body = sanitize_input(data.get('body', ''), 1000)
    push_data = data.get('data', {})

    if not title or not body:
        return jsonify({'error': 'title va body majburiy'}), 400

    sent = send_push_broadcast(title, body, push_data)

    return jsonify({
        'ok': True,
        'sent_count': sent
    })


@app.route('/api/admin/push-targeted', methods=['POST'])
def admin_push_targeted():
    """Tanlangan foydalanuvchilarga push notification yuborish."""
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 401

    if not _firebase_initialized:
        return jsonify({'error': 'Firebase sozlanmagan'}), 500

    data = request.get_json() or {}
    title = sanitize_input(data.get('title', ''), 200)
    body = sanitize_input(data.get('body', ''), 1000)
    telegram_user_ids = data.get('telegram_user_ids', [])
    push_data = data.get('data', {})

    if not title or not body:
        return jsonify({'error': 'title va body majburiy'}), 400

    if not telegram_user_ids or not isinstance(telegram_user_ids, list):
        return jsonify({'error': 'telegram_user_ids majburiy (list)'}), 400

    total_sent = 0
    for uid in telegram_user_ids:
        total_sent += send_push_to_user(int(uid), title, body, push_data)

    return jsonify({
        'ok': True,
        'sent_count': total_sent
    })


# ---- Mobile Telegram Auth ----

@app.route('/api/mobile/auth/init', methods=['POST'])
def mobile_auth_init():
    """Telegram orqali auth sessiyasini boshlash.

    Mobile ilova bu endpointni chaqiradi va qaytgan deep_link ni ochadi.
    User Telegram botda "Start" bosganidan keyin auth tasdiqlangan bo'ladi.

    Returns:
        token: Auth sessiya tokeni
        deep_link: Telegram deep link (foydalanuvchi uchun)
    """
    import secrets
    token = secrets.token_hex(29)  # 58 belgi; "auth_" + 58 = 63 ≤ Telegram 64 belgi limiti

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO mobile_auth_sessions (token, status) VALUES (%s, 'pending')",
            (token,)
        )
        conn.commit()
    finally:
        conn.close()

    # Bot username ni aniqlash
    bot_username = BOT_USERNAME
    if not bot_username and TELEGRAM_BOT_TOKEN:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe",
                timeout=5
            )
            data = resp.json()
            if data.get('ok'):
                bot_username = data['result']['username']
        except Exception:
            pass

    deep_link = f"https://t.me/{bot_username}?start=auth_{token}" if bot_username else None

    return jsonify({
        'token': token,
        'deep_link': deep_link
    })


@app.route('/api/mobile/auth/check', methods=['GET'])
def mobile_auth_check():
    """Auth sessiya holatini tekshirish (mobile ilova polling qiladi).

    Query params:
        token (required): Auth sessiya tokeni

    Returns:
        status: 'pending' | 'confirmed'
        user: Telegram user ma'lumotlari (agar confirmed bo'lsa)
    """
    token = request.args.get('token')
    if not token:
        return jsonify({'error': 'token parametri majburiy'}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM mobile_auth_sessions WHERE token = %s", (token,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return jsonify({'error': 'Token topilmadi'}), 404

    # Eskirgan tokenlarni tekshirish (10 daqiqadan oshsa)
    created = row['created_at']
    if isinstance(created, str):
        created = datetime.fromisoformat(created)
    if datetime.utcnow() - created > timedelta(minutes=10):
        return jsonify({'status': 'expired'}), 410

    if row['status'] == 'confirmed':
        return jsonify({
            'status': 'confirmed',
            'user': {
                'telegram_user_id': row['telegram_user_id'],
                'username': row['username'],
                'first_name': row['first_name'],
                'last_name': row['last_name'],
            }
        })

    return jsonify({'status': 'pending'})


@app.route('/api/mobile/auth/confirm', methods=['POST'])
def mobile_auth_confirm():
    """Bot tomonidan auth sessiyani tasdiqlash (ichki endpoint).

    Bot /start auth_TOKEN qabul qilganda bu endpointni chaqiradi.
    """
    bot_key = request.headers.get('X-Bot-Token', '')
    if not bot_key or bot_key != os.environ.get('BOT_API_KEY', ''):
        return jsonify({'error': 'Forbidden'}), 403

    data = request.get_json() or {}
    token = data.get('token')
    telegram_user_id = data.get('telegram_user_id')

    if not token or not telegram_user_id:
        return jsonify({'error': 'token va telegram_user_id majburiy'}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM mobile_auth_sessions WHERE token = %s AND status = 'pending'", (token,))
    row = cur.fetchone()

    if not row:
        conn.close()
        return jsonify({'error': 'Token topilmadi yoki allaqachon tasdiqlangan'}), 404

    try:
        cur.execute('''
            UPDATE mobile_auth_sessions
            SET status = 'confirmed', telegram_user_id = %s, username = %s,
                first_name = %s, last_name = %s, confirmed_at = %s
            WHERE token = %s
        ''', (
            telegram_user_id,
            data.get('username', ''),
            data.get('first_name', ''),
            data.get('last_name', ''),
            datetime.utcnow().isoformat(),
            token
        ))
        conn.commit()
    finally:
        conn.close()

    return jsonify({'success': True})


if __name__ == '__main__':
    # Initialize DB if needed
    if not os.path.exists(DATABASE) or os.path.getsize(DATABASE) == 0:
        init_db()

    # Production-ready run
    # For actual production, use gunicorn/uwsgi: gunicorn app:app
    port = int(os.environ.get('PORT', 5002))
    app.run(host='0.0.0.0', port=port, debug=False)