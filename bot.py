import os
import db
import pymysql
import logging
import json
from urllib.parse import urlparse, parse_qs

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, ConversationHandler, CallbackQueryHandler, ContextTypes, filters
import aiohttp
from typing import Dict
from dotenv import load_dotenv

# Load .env
load_dotenv()

# ---- Config ----
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')  # REQUIRED
ADMIN_ID = int(os.environ.get('ADMIN_TELEGRAM_ID', '0'))  # REQUIRED (numeric)
API_BASE = os.environ.get('API_BASE', 'http://localhost:5000/api')
SITE_URL = os.environ.get('SITE_URL', 'http://localhost:5000')
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(PROJECT_DIR, 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)
# DB_PATH = os.path.join(PROJECT_DIR, 'specialists.db') - Removed legacy SQLite path

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

# ---- DB helpers ----

def init_db():
    db.init_db()

init_db()

# ---- Conversation states ----
PROFESSION, FULL_NAME, PHONE, REGION, CITY, EXPERIENCE, FREE_TIME, DESCRIPTION, PHOTO = range(9)

# Pending reject reasons: admin_id -> app_id
PENDING_REASONS: Dict[int, int] = {}
# Pending payment rejection reasons: admin_id -> {specialist_id, user_id}
PENDING_PAYMENT_REJECTION: Dict[int, dict] = {}
# Pending TOP payment rejection reasons: admin_id -> {position, user_id}
PENDING_TOP_PAYMENT_REJECTION: Dict[int, dict] = {}
# Pending subscription payment screenshots: user_id -> specialist_id
PENDING_PAYMENTS: Dict[int, int] = {}
# Pending TOP price messages: admin_id -> {user_id, position}
PENDING_TOP_PRICES: Dict[int, dict] = {}
# Pending TOP payment screenshots: user_id -> {position}
PENDING_TOP_PAYMENTS: Dict[int, dict] = {}

# ---- Utils ----
def get_categories_local():
    """Reads categories from static/data/categories.json locally."""
    try:
        path = os.path.join(PROJECT_DIR, 'static', 'data', 'categories.json')
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        log.error(f"Error reading categories.json: {e}")
        return {}

async def post_json(session: aiohttp.ClientSession, url: str, data: dict):
    async with session.post(url, json=data) as r:
        r.raise_for_status()
        return await r.json()


# ---- Handlers ----
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help information with support contact."""
    help_text = (
        "📋 *Yordam / Помощь*\n\n"
        "🇺🇿 *Botdan foydalanish:*\n"
        "🔹 /start - Yangi profil yaratish\n"
        "🔹 /top - TOP o'rinni olish\n"
        "🔹 /help - Yo'riqnoma\n\n"
        "🇷🇺 *Использование бота:*\n"
        "🔹 /start - Создать новый профиль\n"
        "🔹 /top - Поднять анкету в ТОП\n"
        "🔹 /help - Инструкция\n\n"
        "📝 *Jarayon / Процесс:*\n"
        "1️⃣ /start -> Kasb tanlash / Выбрать профессию\n"
        "2️⃣ Ma'lumotlarni to'ldirish / Заполнить данные\n"
        "3️⃣ Rasm yuborish / Отправить фото\n"
        "4️⃣ Admin tasdiqlashi / Ожидание проверки\n\n"
        "❓ Support: @Java2112"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def post_init(application: Application):
    """Set bot commands on startup."""
    await application.bot.set_my_commands([
        ("start", "Boshlash / Начать"),
        ("top", "TOP xizmati / TOP услуга"),
        ("subscription", "Obunani tekshirish / Проверить подписку"),
        ("help", "Yordam / Помощь"),
    ])
    
    # Set bot description (shown when user opens bot for the first time / shares the link)
    try:
        await application.bot.set_my_description(
            description=(
                "🌟 ProTop — O'zbekistondagi eng katta mutaxassislar platformasi!\n\n"
                "✅ 100+ kasb bo'yicha tekshirilgan professional ustalar\n"
                "✅ Tez va qulay qidiruv\n"
                "✅ Bepul ro'yxatdan o'tish\n"
                "✅ Do'stingizni taklif qiling — 1 oy bepul!\n\n"
                "📸 Instagram: instagram.com/protop.uz\n"
                "🌐 Sayt: protop.uz"
            )
        )
        await application.bot.set_my_short_description(
            short_description="ProTop — mutaxassislar va mijozlar platformasi. Do'stingizni taklif qiling — 1 oy bepul! 🌟"
        )
    except Exception as e:
        log.warning(f"Could not set bot description: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Deep-linking: /start ref_USERID or pro_SOMETHING
    args = context.args
    pro = None
    referrer_id = None
    if args:
        joined = ' '.join(args)
        if joined.startswith('pro_'):
            pro = joined[4:].replace('_', ' ')
        elif joined.startswith('ref_'):
            try:
                referrer_id = int(joined[4:])
            except ValueError:
                pass
    
    context.user_data['profession'] = pro

    # Save to bot_users
    user = update.effective_user
    try:
        conn = db.get_connection()
        cur = conn.cursor()
        
        # Check if user already exists
        cur.execute("SELECT user_id FROM bot_users WHERE user_id = %s", (user.id,))
        if not cur.fetchone():
            # New user - process referral
            cur.execute(
                "INSERT IGNORE INTO bot_users (user_id, username, first_name, last_name, referred_by) VALUES (%s, %s, %s, %s, %s)",
                (user.id, user.username, user.first_name, user.last_name, referrer_id)
            )
            if referrer_id and referrer_id != user.id:
                # Log referral
                cur.execute(
                    "INSERT IGNORE INTO referrals (referrer_id, referred_user_id, status) VALUES (%s, %s, %s)",
                    (referrer_id, user.id, 'pending')
                )
                # Notify referrer
                try:
                    await context.bot.send_message(
                        chat_id=referrer_id,
                        text=f"🎁 Do'stingiz joined! @{user.username or user.first_name} taklifingiz bilan qo'shildi.\nU ro'yxatdan o'tsa, sizga +30 kunlik bonus beriladi!"
                    )
                except:
                    pass
                
            # GIFT FOR INVITEE: Show welcome message
            welcome_gift_text = (
                "👋 *Xush kelibsiz!* Sizni do'stingiz taklif qildi.\n\n"
                "ProTop - bu eng yaxshi ustalarni topish va o'z xizmatlaringizni taklif qilish platformasi.\n"
                "🎁 Sizga ham sovg'amiz bor: Ro'yxatdan o'tsangiz, *1 oylik Premium* bonusga ega bo'lasiz!\n\n"
                "🔗 Bizni Instagramda kuzating: [ProTop Instagram](https://www.instagram.com/protop.uz?utm_source=ig_web_button_share_sheet&igsh=ZDNlZDc0MzIxNw==)\n\n"
                "Boshlash uchun ro'yxatdan o'ting 👇"
            )
            await update.message.reply_text(welcome_gift_text, parse_mode='Markdown', disable_web_page_preview=False)
        else:
            # Existing user - just update basic info
            cur.execute(
                "UPDATE bot_users SET username=%s, first_name=%s, last_name=%s WHERE user_id=%s",
                (user.username, user.first_name, user.last_name, user.id)
            )
            
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"Error saving bot user: {e}")

    # DEEP LINKING PRIORITY: If pro is set, skip generic intro
    if pro:
        await update.message.reply_text(f"Tanlangan kasb / Выбранная профессия: {pro}")
        await update.message.reply_text("Ism familiyangiz? / Ваше имя и фамилия?\n(Masalan: Ivanov Ivan)")
        return FULL_NAME

    intro = (
        "🇺🇿 Assalomu alaykum! Professional mutaxassislar bazasiga xush kelibsiz.\n"
        "Ro'yxatdan o'tish uchun toifani tanlang:\n\n"
        "🇷🇺 Здравствуйте! Добро пожаловать в базу профессионалов.\n"
        "Для регистрации выберите категорию:"
    )
    
    # 1) Load categories locally
    cat_data = get_categories_local()
    cats = cat_data.get('categories', [])
    
    if cats:
        rows, row = [], []
        for c in cats:
            key = c.get('key')
            title = (c.get('title') or key or '')[:30] # cut to fit
            if not key: continue
            row.append(InlineKeyboardButton(title, callback_data=f"cat:{key}"))
            if len(row) == 2:
                rows.append(row); row = []
        if row:
            rows.append(row)
        
        # Add "Barcha kasblar" button
        rows.append([InlineKeyboardButton("🗂 Barcha kasblar / Все профессии", callback_data="cat:all")])
        # Add "Kasbingiz yo'qmi?" button
        rows.append([InlineKeyboardButton("Kasbingiz yo'qmi? / Нет профессии?", url="https://t.me/Java2112")])
        
        await update.message.reply_text(intro, reply_markup=InlineKeyboardMarkup(rows))
        return PROFESSION

    # Fallback if no categories found
    await update.message.reply_text(
        "Kasbingizni yozing / Напишите вашу профессию (Masalan: Frontend Dasturchi):"
    )
    return PROFESSION

async def pro_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pro = query.data[4:].replace('_', ' ')
    context.user_data['profession'] = pro
    await query.message.reply_text(f"Tanlangan kasb / Выбранная профессия: {pro}\nIsm familiyangiz? / Ваше имя и фамилия?\n(Masalan: Ivanov Ivan)")
    return FULL_NAME

async def cat_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Check if user wants to go back to categories
    if query.data == "cat_back":
        # Call start to show categories again
        # We need to construct a fake message or just reuse logic? 
        # Easier to just re-send categories logic.
        cat_data = get_categories_local()
        cats = cat_data.get('categories', [])
        rows, row = [], []
        for c in cats:
            key = c.get('key')
            title = (c.get('title') or key or '')[:30]
            if not key: continue
            row.append(InlineKeyboardButton(title, callback_data=f"cat:{key}"))
            if len(row) == 2:
                rows.append(row); row = []
        if row: rows.append(row)
        rows.append([InlineKeyboardButton("🗂 Barcha kasblar / Все профессии", callback_data="cat:all")])
        rows.append([InlineKeyboardButton("Kasbingiz yo'qmi? / Нет профессии?", url="https://t.me/Java2112")])
        await query.message.edit_text(
            "Kategoriya tanlang / Выберите категорию:",
            reply_markup=InlineKeyboardMarkup(rows)
        )
        return PROFESSION

    key = query.data.split(':',1)[1]
    profs = []
    
    if key == 'all':
        # Get all unique professions from local json
        cat_data = get_categories_local()
        all_profs = set()
        for c in cat_data.get('categories', []):
            for p in c.get('professions', []):
                # Handle both string and object formats
                if isinstance(p, dict):
                    all_profs.add(p.get('name', ''))
                else:
                    all_profs.add(p)
        profs = sorted(list(all_profs))
    else:
        cat_data = get_categories_local()
        cats = cat_data.get('categories', [])
        for c in cats:
            if c.get('key') == key:
                profs = c.get('professions', [])
                break

    if not profs:
        await query.message.reply_text("Kasblar topilmadi. / Профессии не найдены.")
        return PROFESSION

    buttons, row = [], []
    for p in profs:  # Show all, pagination if needed but usually fits
        # Handle both string and object formats
        if isinstance(p, dict):
            prof_name = p.get('name', '')
        else:
            prof_name = p
        
        # Cut text to fit button limits
        label = prof_name[:30]
        row.append(InlineKeyboardButton(label, callback_data=f"pro:{prof_name.replace(' ', '_')[:40]}"))
        if len(row) == 2: # 2 cols looks better
            buttons.append(row); row = []
    if row: buttons.append(row)
    
    # Back button
    buttons.append([InlineKeyboardButton("🔙 Ortga / Назад", callback_data="cat_back")])
    
    await query.message.edit_text(
        "Kasb tanlang / Выберите профессию:", 
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return PROFESSION

async def ask_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['profession'] = update.message.text.strip()[:200]
    await update.message.reply_text("Ism familiyangiz? / Ваше имя и фамилия?\n(Masalan: Ivanov Ivan)")
    return FULL_NAME

async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['full_name'] = update.message.text.strip()[:100]
    kb = ReplyKeyboardMarkup([[KeyboardButton(text="📱 Raqamimni yuborish / Отправить номер", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("Telefon raqam (+998901234567) yuboring yoki pastdagi tugmani bosing:\nОтправьте номер телефона или нажмите кнопку ниже:", reply_markup=kb)
    return PHONE

def _region_keyboard():
    regions = [
        ("toshkent", "Toshkent / Ташкент"),
        ("qoraqalpogiston", "Qoraqalpog'iston / Каракалпакстан"),
        ("andijon", "Andijon / Андижан"),
        ("buxoro", "Buxoro / Бухара"),
        ("jizzax", "Jizzax / Джизак"),
        ("qashqadaryo", "Qashqadaryo / Кашкадарья"),
        ("navoiy", "Navoiy / Навои"),
        ("namangan", "Namangan / Наманган"),
        ("samarqand", "Samarqand / Самарканд"),
        ("surxondaryo", "Surxondaryo / Сурхандарья"),
        ("sirdaryo", "Sirdaryo / Сырдарья"),
        ("fargona", "Farg'ona / Фергана"),
        ("xorazm", "Xorazm / Хорезм"),
    ]
    rows = []
    for key, title in regions:
        rows.append([InlineKeyboardButton(f"📍 {title}", callback_data=f"region:{key}")])
    return InlineKeyboardMarkup(rows)

_REGION_CITIES = {
    "toshkent": ["Toshkent", "Chirchiq", "Angren", "Olmaliq", "Bekobod", "G'azalkent", "Yangiyo'l"],
    "qoraqalpogiston": ["Nukus", "Qo'ng'irot", "Xo'jayli", "Beruniy", "To'rtko'l"],
    "andijon": ["Andijon", "Jalolquduq", "Qorasuv", "Marhamat", "Paxtaobod", "Shahrixon"],
    "buxoro": ["Buxoro", "Kogon", "G'ijduvon", "Qorako'l", "G'alaosiyo", "Romitan"],
    "jizzax": ["Jizzax", "G'allaorol", "Do'stlik", "Paxtakor"],
    "qashqadaryo": ["Qarshi", "Shahrisabz", "Kitob", "Qamashi", "G'uzor"],
    "navoiy": ["Navoiy", "Zarafshon", "Uchquduq", "Nurota", "Qiziltepa"],
    "namangan": ["Namangan", "Chust", "Kosonsoy", "Pop", "To'raqo'rg'on", "Uchqo'rg'on"],
    "samarqand": ["Samarqand", "Urgut", "Jomboy", "Kattaqo'rg'on", "Oqtosh"],
    "surxondaryo": ["Termiz", "Denov", "Boysun", "Sho'rchi", "Qumqo'rg'on"],
    "sirdaryo": ["Guliston", "Shirin", "Sirdaryo", "Yangiyer"],
    "fargona": ["Farg'ona", "Qo'qon", "Marg'ilon", "Quvasoy", "Quva", "Rishton"],
    "xorazm": ["Urganch", "Xiva", "Shovot", "Xonqa", "Yangiariq"],
}

def _city_keyboard_for(region_key: str):
    cities = _REGION_CITIES.get(region_key, [])
    rows = []
    for c in cities:
        rows.append([InlineKeyboardButton(f"🏙️ {c}", callback_data=f"city:{c}")])
    return InlineKeyboardMarkup(rows)

async def phone_text_to_region(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['full_name'] = context.user_data.get('full_name','')
    context.user_data['phone'] = update.message.text.strip()[:20]
    
    # ONLINE CHECK
    prof = (context.user_data.get('profession') or '').lower()
    if 'online' in prof:
        context.user_data['region'] = 'Online'
        context.user_data['city'] = 'Online'
        options = ['1-3', '3-6', '6-10', '10+']
        rows = [[InlineKeyboardButton(o, callback_data=f"exp:{o}") for o in options[:2]],
                [InlineKeyboardButton(o, callback_data=f"exp:{o}") for o in options[2:]]]
        await update.message.reply_text("Tajriba oralig'ini tanlang / Выберите опыт работы:", reply_markup=InlineKeyboardMarkup(rows))
        return EXPERIENCE

    await update.message.reply_text("Viloyat/Respublika tanlang / Выберите регион:", reply_markup=ReplyKeyboardRemove())
    await update.message.reply_text("👇", reply_markup=_region_keyboard())
    return REGION

async def phone_contact_to_region(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    context.user_data['phone'] = (contact.phone_number or '').strip()[:20]
    
    # ONLINE CHECK
    prof = (context.user_data.get('profession') or '').lower()
    if 'online' in prof:
        context.user_data['region'] = 'Online'
        context.user_data['city'] = 'Online'
        options = ['1-3', '3-6', '6-10', '10+']
        rows = [[InlineKeyboardButton(o, callback_data=f"exp:{o}") for o in options[:2]],
                [InlineKeyboardButton(o, callback_data=f"exp:{o}") for o in options[2:]]]
        await update.message.reply_text("Tajriba oralig'ini tanlang / Выберите опыт работы:", reply_markup=InlineKeyboardMarkup(rows))
        return EXPERIENCE

    await update.message.reply_text("Viloyat/Respublika tanlang / Выберите регион:", reply_markup=ReplyKeyboardRemove())
    await update.message.reply_text("👇", reply_markup=_region_keyboard())
    return REGION

async def region_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    region_key = q.data.split(':',1)[1]
    context.user_data['region'] = region_key
    await q.message.reply_text("Shaharni tanlang (yoki yozib yuboring) / Выберите город (или напишите):", reply_markup=_city_keyboard_for(region_key))
    return CITY

async def city_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    city = q.data.split(':',1)[1]
    context.user_data['city'] = city
    options = ['1-3', '3-6', '6-10', '10+']
    rows = [[InlineKeyboardButton(o, callback_data=f"exp:{o}") for o in options[:2]],
            [InlineKeyboardButton(o, callback_data=f"exp:{o}") for o in options[2:]]]
    await q.message.reply_text("Tajriba oralig'ini tanlang / Выберите опыт работы:", reply_markup=InlineKeyboardMarkup(rows))
    return EXPERIENCE

async def city_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['city'] = update.message.text.strip()[:100]
    options = ['1-3', '3-6', '6-10', '10+']
    rows = [[InlineKeyboardButton(o, callback_data=f"exp:{o}") for o in options[:2]],
            [InlineKeyboardButton(o, callback_data=f"exp:{o}") for o in options[2:]]]
    await update.message.reply_text("Tajriba oralig'ini tanlang / Выберите опыт работы:", reply_markup=InlineKeyboardMarkup(rows))
    return EXPERIENCE

async def ask_experience(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        exp = int(update.message.text.strip())
    except Exception:
        exp = 0
    context.user_data['experience'] = max(0, min(50, exp))
    await update.message.reply_text("Ish vaqti / Рабочее время (Masalan: 09:00 - 18:00):")
    return FREE_TIME

def _exp_to_int(v: str) -> int:
    m = {'1-3':2, '3-6':4, '6-10':8, '10+':10}
    return m.get(v, 0)

async def exp_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    rng = q.data.split(':',1)[1]
    context.user_data['experience'] = _exp_to_int(rng)
    await q.message.reply_text("Ish vaqti / Рабочее время (Masalan: 09:00 - 18:00):")
    return FREE_TIME



async def ask_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['free_time'] = update.message.text.strip()[:200]
    await update.message.reply_text("O'zingiz haqingizda qisqacha / Кратко о себе (Ko'nikmalar, portfolio / Навыки, портфолио...):")
    return DESCRIPTION

async def ask_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['description'] = update.message.text.strip()[:1000]
    await update.message.reply_text("Profil uchun rasm yuboring / Отправьте фото для профиля:")
    return PHOTO

async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    filename = f"app_{update.effective_user.id}_{photo.file_unique_id}.jpg"
    local_path = os.path.join(UPLOAD_DIR, filename)
    await file.download_to_drive(local_path)

    # Подготовим данные анкеты
    data = context.user_data.copy()
    data['photo_path'] = local_path
    data['user_id'] = update.effective_user.id
    data['username'] = update.effective_user.username or ''

    # Проверка дублей по ФИО / телефону (applications + specialists)
    full_name = (data.get('full_name') or '').strip()
    phone = (data.get('phone') or '').strip()

    conn = db.get_connection()
    conn.row_factory = None # pymysql dist cursor is default
    cur = conn.cursor()
    try:
        # Ищем дубли среди заявок
        cur.execute(
            "SELECT 1 FROM applications WHERE (phone = %s AND phone != '') OR (full_name = %s AND full_name != '')",
            (phone, full_name)
        )
        if cur.fetchone():
            support_text = (
                "⚠️ Bunday ism-familiya yoki telefon bilan ariza allaqachon mavjud.\n"
                "⚠️ Заявка с таким именем или телефоном уже существует.\n\n"
                "Qo'llab-quvvatlash / Поддержка: 👉 https://t.me/Java2112\n"
            )
            await update.message.reply_text(support_text)
            conn.close()
            return ConversationHandler.END

        # Ищем дубли среди уже опубликованных специалистов
        cur.execute(
            "SELECT 1 FROM specialists WHERE (phone = %s AND phone != '') OR (full_name = %s AND full_name != '')",
            (phone, full_name)
        )
        if cur.fetchone():
            support_text = (
                "⚠️ Bunday ism-familiya yoki telefon bilan profil saytda allaqachon mavjud.\n\n"
                "Agar siz boshqa kasbda ro'yxatdan o'tmoqchi bo'lsangiz, "
                "qo'llab-quvvatlash xizmatiga murojaat qiling:\n"
                "👉 https://t.me/Java2112\n\n"
                "Iltimos, o'zingizning haqiqiy ma'lumotlaringizni kiriting."
            )
            await update.message.reply_text(support_text)
            conn.close()
            return ConversationHandler.END

        # Сохраняем заявку в БД
        cur.execute(
            """
            INSERT INTO applications (user_id, username, profession, full_name, phone, city, experience, price, free_time, description, photo_path)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                data['user_id'], data['username'], data.get('profession',''), data.get('full_name',''), data.get('phone',''),
                data.get('city',''), int(data.get('experience') or 0), "Kelishiladi", data.get('free_time',''), data.get('description',''),
                local_path
            )
        )
        app_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    # Notify admin
    if ADMIN_ID:
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"approve:{app_id}"),
                InlineKeyboardButton("❌ Rad etish", callback_data=f"reject:{app_id}")
            ]
        ])
        caption = (
            f"Yangi ariza #{app_id}\n\n"
            f"Kasb: {data.get('profession')}\n"
            f"F.I.Sh.: {data.get('full_name')}\n"
            f"Tel: {data.get('phone')}\n"
            f"Shahar: {data.get('city') or 'Online'}\n"
            f"Tajriba: {data.get('experience')} yil\n"
            f"Vaqt: {data.get('free_time') or '-'}\n\n"
            f"{data.get('description') or ''}"
        )
        try:
            with open(local_path, 'rb') as f:
                await context.bot.send_photo(chat_id=ADMIN_ID, photo=f, caption=caption, reply_markup=kb)
        except Exception as e:
            log.exception("Failed to send photo to admin: %s", e)
            await context.bot.send_message(chat_id=ADMIN_ID, text=caption, reply_markup=kb)

    await update.message.reply_text(
        "✅ Rahmat! Arizangiz yuborildi. Admin tasdiqlaganidan so'ng sizga xabar beramiz.\n"
        "✅ Спасибо! Ваша заявка отправлена. Мы сообщим вам, когда администратор одобрит её."
    )
    
    # Referral Invite Message
    ref_link = f"https://t.me/{(await context.bot.get_me()).username}?start=ref_{update.effective_user.id}"
    invite_text = (
        "🚀 *Tabriklaymiz!* Siz endi ProTop jamoasining bir qismisiz!\n\n"
        "🌟 *ProTop* - mutaxassislar va mijozlar uchun eng qulay platforma.\n\n"
        "🎁 *Do'stlaringizni taklif qiling va bepul obuna oling!*\n"
        "Har bir taklif etilgan do'stingiz uchun sizga *30 kunlik Premium* qo'shiladi.\n\n"
        "🔗 *Sizning referral havolangiz:*\n"
        f"`{ref_link}`\n\n"
        "📸 Instagramimiz: [protop.uz](https://www.instagram.com/protop.uz?utm_source=ig_web_button_share_sheet&igsh=ZDNlZDc0MzIxNw==)\n"
        "Do'stlaringizga yuboring va bonuslarni yig'ing!"
    )
    # Use image if available, otherwise text
    try:
        # User specified "with image"
        image_url = "https://protop.uz/static/images/referral_promo.jpg" # Placeholder or existing
        await update.message.reply_photo(photo=image_url, caption=invite_text, parse_mode='Markdown')
    except:
        await update.message.reply_text(invite_text, parse_mode='Markdown')

    return ConversationHandler.END

async def on_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not ADMIN_ID or update.effective_user.id != ADMIN_ID:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Sizda ruxsat yo'q")
        return

    action, app_id_s = query.data.split(':', 1)
    app_id = int(app_id_s)

    conn = db.get_connection()
    # conn.row_factory = sqlite3.Row # removed
    cur = conn.cursor()
    cur.execute("SELECT * FROM applications WHERE id = %s", (app_id,))
    row = cur.fetchone()

    if not row:
        await query.message.reply_text("Ariza topilmadi")
        conn.close()
        return

    if action == 'approve':
        # Mark approved
        cur.execute("UPDATE applications SET status='approved' WHERE id=%s", (app_id,))
        conn.commit()
        # Push to site automatically
        push_ok = False
        specialist_id = None
        filename = os.path.basename(row['photo_path']) if row['photo_path'] else ''
        filename = os.path.basename(row['photo_path']) if row['photo_path'] else ''
        photo_url = f"/uploads/{filename}" if filename else ''
        payload = {
            'profession': row['profession'],
            'full_name': row['full_name'],
            'phone': row['phone'],
            'city': row['city'],
            'experience': row['experience'],
            'free_time': row['free_time'],
            'description': row['description'],
            'photo_url': photo_url,
            'telegram_chat_id': row['user_id']  # Send Telegram chat ID for notifications
        }
        try:
            async with aiohttp.ClientSession() as session:
                headers = {'X-Bot-Token': os.environ.get('BOT_API_KEY','')}
                async with session.post(f"{API_BASE.rstrip('/')}/bot/specialists", json=payload, headers=headers) as r:
                    r.raise_for_status()
                    data = await r.json()
                    specialist_id = (data.get('specialist') or {}).get('id')
                    push_ok = True
        except Exception as e:
            log.exception("Failed to push to site via API: %s", e)
        if not push_ok:
            # Fallback: direct insert into local DB
            try:
                conn2 = db.get_connection()
                cu2 = conn2.cursor()
                cu2.execute('''
                    INSERT INTO specialists (profession, full_name, phone, experience, price, free_time, city, description, photo_url, top_order, telegram_chat_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (
                    payload['profession'], payload['full_name'], payload['phone'], int(payload.get('experience') or 0),
                    "Kelishiladi", payload.get('free_time',''), payload.get('city',''), payload.get('description',''), payload.get('photo_url',''), None,
                    payload.get('telegram_chat_id')
                ))
                specialist_id = cu2.lastrowid
                conn2.commit(); conn2.close()
                push_ok = True
            except Exception as e:
                log.exception("Fallback DB insert failed: %s", e)
        # Update caption and notify applicant
        try:
            await query.edit_message_caption(caption=(query.message.caption or '') + "\n\n✅ Tasdiqlandi va saytga qo'shildi")
        except Exception:
            pass
        try:
            # 5 MONTHS FREE LOGIC
            from datetime import datetime, timedelta
            trial_expires = datetime.utcnow() + timedelta(days=150) # Approx 5 months
            trial_iso = trial_expires.isoformat()

            # Update DB with expiry
            conn3 = db.get_connection()
            c3 = conn3.cursor()
            c3.execute("UPDATE specialists SET trial_expires_at = %s WHERE id = %s", (trial_iso, specialist_id))
            conn3.commit()
            conn3.close()

            # Build direct link to specialist's profession page
            import urllib.parse
            prof_encoded = urllib.parse.quote(row['profession'])
            profile_link = f"{SITE_URL.rstrip('/')}/specialists.html?profession={prof_encoded}&mode=worker"
            
            txt = (
                f"🎉 Tabriklaymiz! Profilingiz saytga qo'shildi!\n\n"
                f"🔗 Sizning sahifangiz: {profile_link}\n\n"
                f"Hurmatli mijoz, bot hozir rivojlanish jarayonida va yaqin 5 oy davomida bepul taqdim etiladi.\n\n"
                f"🇷🇺 Уважаемый клиент, бот находится в процессе разработки и будет предоставляться бесплатно в течение ближайших 5 месяцев."
            )
            await context.bot.send_message(chat_id=row['user_id'], text=txt)

            APPROVED_STICKER = os.environ.get('STICKER_APPROVED', '')
            if APPROVED_STICKER:
                await context.bot.send_sticker(chat_id=row['user_id'], sticker=APPROVED_STICKER)
            
            # Check for referral bonus
            try:
                conn_ref = db.get_connection()
                cur_ref = conn_ref.cursor()
                cur_ref.execute("SELECT referrer_id FROM referrals WHERE referred_user_id = %s AND status = 'pending'", (row['user_id'],))
                ref = cur_ref.fetchone()
                if ref:
                    referrer_id = ref['referrer_id']
                    # Mark as activated
                    cur_ref.execute("UPDATE referrals SET status = 'completed', activated_at = %s WHERE referred_user_id = %s", (datetime.utcnow().isoformat(), row['user_id']))
                    conn_ref.commit()
                    
                    # Notify referrer with gift button
                    kb_gift = InlineKeyboardMarkup([[InlineKeyboardButton("🎁 Sovgani faollashtirish", callback_data=f"gift_activate:{row['user_id']}") ]])
                    await context.bot.send_message(
                        chat_id=referrer_id,
                        text=f"🔥 Xushxabar! Siz taklif qilgan @{row['username'] or 'do`stingiz'} ro'yxatdan o'tdi.\nSizga 1 oylik (30 kun) bepul Premium sovg'a berildi!",
                        reply_markup=kb_gift
                    )
                    
                    # GIFT FOR INVITEE: Also give the referred user 30 days bonus
                    try:
                        from datetime import datetime, timedelta
                        # Get the invitee's specialist record (just created above)
                        cur_ref.execute("SELECT id, paid_until, trial_expires_at FROM specialists WHERE telegram_chat_id = %s", (row['user_id'],))
                        invitee_spec = cur_ref.fetchone()
                        if invitee_spec:
                            base = datetime.utcnow()
                            ce = invitee_spec['paid_until'] or invitee_spec['trial_expires_at']
                            try:
                                if ce:
                                    ced = datetime.fromisoformat(ce)
                                    if ced > base:
                                        base = ced
                            except:
                                pass
                            new_exp = (base + timedelta(days=30)).isoformat()
                            cur_ref.execute("UPDATE specialists SET paid_until = %s WHERE id = %s", (new_exp, invitee_spec['id']))
                            conn_ref.commit()
                        
                        # Notify the invitee about their gift
                        await context.bot.send_message(
                            chat_id=row['user_id'],
                            text=(
                                "🎁 *Sizga ham sovg'a!*\n\n"
                                "Sizni do'stingiz taklif qilgani uchun sizga ham *30 kunlik Premium* bonus berildi!\n\n"
                                "📸 Bizni Instagramda kuzating:\nhttps://www.instagram.com/protop.uz\n\n"
                                "🌐 Saytimiz: https://protop.uz"
                            ),
                            parse_mode='Markdown'
                        )
                    except Exception as e2:
                        log.error(f"Error giving invitee bonus: {e2}")
                    
                conn_ref.close()
            except Exception as e:
                log.error(f"Error processing referral bonus notification: {e}")

        except Exception as e:
            log.exception(f"Error in approval notification: {e}")
    elif action == 'reject':
        # Ask admin for reason
        PENDING_REASONS[update.effective_user.id] = app_id
        await query.message.reply_text("Rad etish sababi? Ushbu xabarga javob sifatida yozing.")
    conn.close()


async def on_payment_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пользователь нажал кнопку "chekni yuboraman" после оплаты.

    Запоминаем, к какому специалисту относится чек и просим отправить скрин.
    """
    q = update.callback_query
    await q.answer()
    if not q.data.startswith('paystart:'):
        return
    try:
        specialist_id = int(q.data.split(':', 1)[1])
    except Exception:
        await q.message.reply_text("Xatolik. Iltimos, qayta urinib ko'ring.")
        return
    user_id = update.effective_user.id
    PENDING_PAYMENTS[user_id] = specialist_id
    await q.message.reply_text(
        "Endi to'lov chekingizni skrinshot qilib shu yerga yuboring. \n"
        "Admin tekshiradi va 5$ to'lovi tasdiqlansa, profil 1 oy davomida aktiv bo'ladi."
    )


async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /top — запросить продвижение в TOP."""
    text = (
        "🔝 TOP ga chiqish\n\n"
        "Qaysi TOP o'rinni olishni xohlaysiz? 1 dan 10 gacha TOP o'rinlardan birini tanlang."
    )
    buttons = [
        [
            InlineKeyboardButton("TOP-1", callback_data="top_req:1"),
            InlineKeyboardButton("TOP-2", callback_data="top_req:2"),
            InlineKeyboardButton("TOP-3", callback_data="top_req:3"),
            InlineKeyboardButton("TOP-4", callback_data="top_req:4"),
            InlineKeyboardButton("TOP-5", callback_data="top_req:5"),
        ],
        [
            InlineKeyboardButton("TOP-6", callback_data="top_req:6"),
            InlineKeyboardButton("TOP-7", callback_data="top_req:7"),
            InlineKeyboardButton("TOP-8", callback_data="top_req:8"),
            InlineKeyboardButton("TOP-9", callback_data="top_req:9"),
            InlineKeyboardButton("TOP-10", callback_data="top_req:10"),
        ],
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def on_top_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пользователь выбрал желаемый TOP-номер (top_req:N)."""
    q = update.callback_query
    await q.answer()
    try:
        pos = int(q.data.split(':', 1)[1])
    except Exception:
        await q.message.reply_text("Xatolik. Iltimos, qayta urinib ko'ring.")
        return

    user_id = update.effective_user.id

    # Пользователь может выбрать ЛЮБОЙ TOP (1-10), дальше решает админ
    await q.message.reply_text(
        f"Siz TOP-{pos} o'rnini tanladingiz. Admin joy bandligini tekshiradi va sizga narx bilan javob beradi."
    )

    if ADMIN_ID:
        username = update.effective_user.username or '-'
        text_admin = (
            "Yangi TOP so'rovi\n\n"
            f"Foydalanuvchi ID: {user_id}\n"
            f"Username: @{username}\n"
            f"So'ralgan TOP o'rni: TOP-{pos}\n\n"
            "Iltimos, joy bandmi yo'qligini tekshirib, narxni yuboring yoki rad eting."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"TOP-{pos} narxini yuborish", callback_data=f"topprice:{pos}:{user_id}")]
        ])
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=text_admin, reply_markup=kb)
        except Exception:
            pass


async def on_top_price_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ нажал кнопку 'topprice:pos:user_id' — запрашиваем у него цену для TOP."""
    q = update.callback_query
    await q.answer()
    if not ADMIN_ID or update.effective_user.id != ADMIN_ID:
        await q.message.reply_text("Sizda ruxsat yo'q")
        return
    parts = q.data.split(':')
    if len(parts) != 3:
        return
    _, pos_s, user_id_s = parts
    try:
        pos = int(pos_s)
        user_id = int(user_id_s)
    except Exception:
        return

    PENDING_TOP_PRICES[update.effective_user.id] = {"user_id": user_id, "position": pos}
    await q.message.reply_text(
        f"TOP-{pos} uchun narxni yozing (masalan: '30$ / 2 hafta'). Bu matn foydalanuvchiga yuboriladi."
    )


async def on_top_paystart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пользователь согласился на цену за TOP и нажал 'toppaystart:pos'."""
    q = update.callback_query
    await q.answer()
    if not q.data.startswith('toppaystart:'):
        return
    try:
        pos = int(q.data.split(':', 1)[1])
    except Exception:
        await q.message.reply_text("Xatolik. Iltimos, qayta urinib ko'ring.")
        return
    user_id = update.effective_user.id
    PENDING_TOP_PAYMENTS[user_id] = {"position": pos}
    await q.message.reply_text(
        f"Endi TOP-{pos} uchun to'lov chekingizni skrinshot qilib shu yerga yuboring. \n"
        "Admin tekshiradi va tasdiqlansa, profil TOP ro'yxatida yuqoriga ko'tariladi."
    )






async def receive_payment_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi to'lov chekini (rasm) yuborganda.
    
    При получении скриншота оплаты:
    1. Проверяем, есть ли ожидание оплаты (PENDING_PAYMENTS).
    2. Пересылаем админу фото с кнопками "Принять" / "Отклонить".
    """
    user_id = update.effective_user.id
    # Check if this user is pending payment (subscription or top)
    specialist_id = PENDING_PAYMENTS.get(user_id)
    
    if not specialist_id:
        # Check top payments
        if user_id in PENDING_TOP_PAYMENTS:
            return await receive_top_payment_screenshot(update, context)
        # Maybe it's just a random photo?
        # await update.message.reply_text("Tushunarsiz rasm. / Непонятное фото.")
        return

    # Send to admin
    photo = update.message.photo[-1].file_id
    caption = (
        f"💰 #Payment #To'lov\n"
        f"Specialist ID: {specialist_id}\n"
        f"User ID: {user_id}\n"
        f"User: {update.effective_user.mention_html()}\n\n"
        f"5$ oylik to'lov.\n"
        f"Tasdiqlaysizmi?"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Tasdiqlash / Принять", callback_data=f"pay:ok:{specialist_id}:{user_id}"),
            InlineKeyboardButton("❌ Rad etish / Отклонить", callback_data=f"pay:no:{specialist_id}:{user_id}")
        ]
    ])
    
    await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo, caption=caption, reply_markup=kb, parse_mode='HTML')
    
    await update.message.reply_text(
        "✅ Chek qabul qilindi! Admin tasdiqlashini kuting.\n"
        "✅ Чек принят! Ожидайте подтверждения администратора."
    )
    # Clear wait
    del PENDING_PAYMENTS[user_id]


async def receive_top_payment_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Specific handler for TOP payment screenshots"""
    user_id = update.effective_user.id
    top_ctx = PENDING_TOP_PAYMENTS.get(user_id)
    if not top_ctx:
        return

    position = top_ctx.get('position')
    
    photo = update.message.photo[-1].file_id
    caption = (
        f"💰 #TopPayment\n"
        f"User ID: {user_id}\n"
        f"User: {update.effective_user.mention_html()}\n"
        f"TOP Position: {position}\n\n"
        f"Tasdiqlaysizmi%s"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ TOP Tasdiq / Принять", callback_data=f"toppayok:{position}:{user_id}"),
            InlineKeyboardButton("❌ Rad etish / Отклонить", callback_data=f"toppayno:{position}:{user_id}")
        ]
    ])

    await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo, caption=caption, reply_markup=kb, parse_mode='HTML')
    
    await update.message.reply_text(
        f"✅ TOP-{position} uchun chek qabul qilindi! Admin tasdiqlashini kuting.\n"
    )
    # Clear wait
    del PENDING_TOP_PAYMENTS[user_id]


async def on_payment_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ подтверждает или отклоняет оплату (обычная подписка: payok/payno)."""
    q = update.callback_query
    await q.answer()
    if not ADMIN_ID or update.effective_user.id != ADMIN_ID:
        await q.message.reply_text("Sizda ruxsat yo'q")
        return

    data = q.data.split(':')
    # Expected: pay:ok:spec_id:user_id OR pay:no:spec_id:user_id
    if len(data) == 4 and data[0] == 'pay':
        _, sub_action, specialist_id_s, user_id_s = data
        action = 'pay' + sub_action # payok or payno
    elif len(data) == 3: # Legacy support
        action, specialist_id_s, user_id_s = data
    else:
        return

    try:
        specialist_id = int(specialist_id_s)
        user_id = int(user_id_s)
    except Exception:
        return

    if action == 'payok':
        # Активируем 2-недельный период через API сайта (or 1 month as text says)
        # Using a direct DB update is safer if API is flaky, but let's try API first as existing code did
        try:
            # First, update via DB to be sure
            from datetime import datetime, timedelta
            conn = db.get_connection()
            cur = conn.cursor()
            # Add 30 days to paid_until (or now + 30 days)
            # We logic: reset status to active, clear trial_expires_at?
            # User wants: "Active for 1 month"
            
            # Check current expiry
            cur.execute("SELECT paid_until, trial_expires_at FROM specialists WHERE id=%s", (specialist_id,))
            row = cur.fetchone()
            current_exp = None
            if row:
                if row['paid_until']: current_exp = row['paid_until']
                elif row['trial_expires_at']: current_exp = row['trial_expires_at']
            
            base_date = datetime.utcnow()
            try:
                if current_exp:
                    ce_date = datetime.fromisoformat(current_exp)
                    if ce_date > base_date:
                        base_date = ce_date
            except:
                pass
            
            new_exp = (base_date + timedelta(days=30)).isoformat()
            
            cur.execute("UPDATE specialists SET paid_until=%s, status='active' WHERE id=%s", (new_exp, specialist_id))
            conn.commit()
            conn.close()

        except Exception as e:
            log.error(f"DB update failed for payok: {e}")
            await q.message.reply_text("Xatolik: Baza yangilanmadi.")
            return

        # Сообщаем пользователю
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="✅ To'lov tasdiqlandi! Profilingiz 1 oyga uzaytirildi. Rahmat."
            )
        except Exception:
            pass
        try:
            await q.edit_message_caption(caption=(q.message.caption or '') + "\n\n✅ To'lov tasdiqlandi, +1 oy.")
        except Exception:
            pass
    elif action == 'payno':
        # Ask admin for rejection reason
        PENDING_PAYMENT_REJECTION[update.effective_user.id] = {"specialist_id": specialist_id, "user_id": user_id}
        await q.message.reply_text("To'lov rad etish sababi? Ushbu xabarga javob sifatida yozing.")
        try:
            await q.edit_message_caption(caption=(q.message.caption or '') + "\n\n⏳ Sabab kutilmoqda...")
        except Exception:
            pass

    # Очищаем ожидание чека для этого пользователя (подписка)
    PENDING_PAYMENTS.pop(user_id, None)


async def on_top_payment_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ подтверждает или отклоняет оплату за TOP (toppayok/toppayno)."""
    q = update.callback_query
    await q.answer()
    if not ADMIN_ID or update.effective_user.id != ADMIN_ID:
        await q.message.reply_text("Sizda ruxsat yo'q")
        return

    data = q.data.split(':')
    if len(data) != 3:
        return
    action, pos_s, user_id_s = data
    try:
        position = int(pos_s)
        user_id = int(user_id_s)
    except Exception:
        return

    if action == 'toppayok':
        # Успешная оплата за TOP
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"TOP-{position} uchun to'lov tasdiqlandi!\n\n"
                    "Yaqin orada profilingiz saytning TOP ro'yxatida yuqoriga ko'tariladi. "
                    "Admin sizni TOP o'rniga qo'shadi."
                ),
            )
        except Exception:
            pass
        try:
            await q.edit_message_caption(caption=(q.message.caption or '') + "\n\n✅ TOP to'lovi tasdiqlandi.")
        except Exception:
            pass
    elif action == 'toppayno':
        # Ask admin for rejection reason
        PENDING_TOP_PAYMENT_REJECTION[update.effective_user.id] = {"position": position, "user_id": user_id}
        await q.message.reply_text("TOP to'lov rad etish sababi? Ushbu xabarga javob sifatida yozing.")
        try:
            await q.edit_message_caption(caption=(q.message.caption or '') + "\n\n⏳ Sabab kutilmoqda...")
        except Exception:
            pass

    # Очищаем ожидание TOP-чека для этого пользователя
    PENDING_TOP_PAYMENTS.pop(user_id, None)


async def on_gift_activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi 'Sovgani faollashtirish' tugmasini bosganda."""
    query = update.callback_query
    await query.answer()
    
    # gift_activate:referred_user_id
    _, referred_id_s = query.data.split(':')
    referred_id = int(referred_id_s)
    user_id = update.effective_user.id
    
    try:
        conn = db.get_connection()
        cur = conn.cursor()
        
        # Check if this referral is already activated for this referrer
        cur.execute("SELECT status FROM referrals WHERE referrer_id = %s AND referred_user_id = %s", (user_id, referred_id))
        ref = cur.fetchone()
        
        if ref and ref['status'] == 'completed':
            # Add 30 days to specialist's paid_until
            # Find specialist associated with this user_id
            cur.execute("SELECT id, paid_until, trial_expires_at FROM specialists WHERE telegram_chat_id = %s", (user_id,))
            spec = cur.fetchone()
            
            if spec:
                from datetime import datetime, timedelta
                base_date = datetime.utcnow()
                current_exp = spec['paid_until'] or spec['trial_expires_at']
                
                try:
                    if current_exp:
                        ce_date = datetime.fromisoformat(current_exp)
                        if ce_date > base_date:
                            base_date = ce_date
                except:
                    pass
                
                new_exp = (base_date + timedelta(days=30)).isoformat()
                cur.execute("UPDATE specialists SET paid_until = %s, status = 'active' WHERE id = %s", (new_exp, spec['id']))
                # Mark referral as 'activated'
                cur.execute("UPDATE referrals SET status = 'activated' WHERE referred_user_id = %s", (referred_id,))
                conn.commit()
                
                await query.edit_message_text(text=f"✅ Tabriklaymiz! 30 kunlik Premium bonus faollashtirildi.\nYangi muddat: {new_exp[:10]}")
            else:
                await query.edit_message_text(text="⚠️ Profilingiz topilmadi. Bonusni faollashtirish uchun avval ro'yxatdan o'ting.")
        else:
            await query.edit_message_text(text="⚠️ Bu bonus allaqachon ishlatilgan yoki xatolik yuz berdi.")
        
        conn.close()
    except Exception as e:
        log.error(f"Error activating gift: {e}")
        await query.message.reply_text("Xatolik yuz berdi.")


async def subscription_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command /subscription to check remaining days."""
    user_id = update.effective_user.id
    try:
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT paid_until, trial_expires_at, status FROM specialists WHERE telegram_chat_id = %s", (user_id,))
        row = cur.fetchone()
        conn.close()
        
        if row:
            expiry = row['paid_until'] or row['trial_expires_at']
            if expiry:
                from datetime import datetime
                exp_date = datetime.fromisoformat(expiry)
                now = datetime.utcnow()
                diff = exp_date - now
                days = diff.days
                
                if diff.total_seconds() > 0:
                    status_text = f"✅ Faol\n📅 Tugash muddati: {expiry[:10]} ({days} kun qoldi)"
                else:
                    status_text = f"❌ Muddati tugagan\n📅 Tugagan sana: {expiry[:10]}"
            else:
                status_text = "ℹ️ Cheksiz"
            
            await update.message.reply_text(f"📊 *Sizning obunangiz:*\n\n{status_text}", parse_mode='Markdown')
        else:
            await update.message.reply_text("❓ Siz hali ro'yxatdan o'tmagansiz. /start buyrug'ini bosing.")
    except Exception as e:
        log.error(f"Error checking subscription: {e}")
        await update.message.reply_text("Xatolik yuz berdi.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bekor qilindi.")
    return ConversationHandler.END


async def region_text_blocker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Iltimos, viloyatni quyidagi tugmalardan tanlang.\n"
        "Пожалуйста, выберите регион из списка ниже.",
        reply_markup=_region_keyboard()
    )


async def region_text_blocker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Iltimos, viloyatni quyidagi tugmalardan tanlang.\n"
        "Пожалуйста, выберите регион из списка ниже.",
        reply_markup=_region_keyboard()
    )


def main():
    if not BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN env var")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            PROFESSION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_full_name),
                CallbackQueryHandler(cat_chosen, pattern=r'^cat'), # Regex changed to match cat: and cat_back
                CallbackQueryHandler(pro_chosen, pattern=r'^pro:')
            ],
            FULL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone)],
            PHONE: [
                MessageHandler(filters.CONTACT, phone_contact_to_region),
                MessageHandler(filters.TEXT & ~filters.COMMAND, phone_text_to_region)
            ],
            REGION: [
                CallbackQueryHandler(region_chosen, pattern=r'^region:'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, region_text_blocker)
            ],
            CITY: [
                CallbackQueryHandler(city_chosen, pattern=r'^city:'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, city_received)
            ],
            EXPERIENCE: [
                CallbackQueryHandler(exp_chosen, pattern=r'^exp:'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_experience)
            ],
            FREE_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_description)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_photo)],
            PHOTO: [MessageHandler(filters.PHOTO, receive_photo)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CommandHandler('subscription', subscription_command))
    app.add_handler(CallbackQueryHandler(on_decision, pattern=r'^(approve|reject):'))
    app.add_handler(CallbackQueryHandler(on_payment_start, pattern=r'^paystart:'))
    app.add_handler(CallbackQueryHandler(on_payment_decision, pattern=r'^(payok|payno|pay):'))
    app.add_handler(CallbackQueryHandler(on_gift_activate, pattern=r'^gift_activate:'))
    # TOP promotion flow handlers
    app.add_handler(CommandHandler('top', top_command))
    app.add_handler(CallbackQueryHandler(on_top_request, pattern=r'^top_req:'))
    app.add_handler(CallbackQueryHandler(on_top_price_request, pattern=r'^topprice:'))
    app.add_handler(CallbackQueryHandler(on_top_paystart, pattern=r'^toppaystart:'))
    app.add_handler(CallbackQueryHandler(on_top_payment_decision, pattern=r'^(toppayok|toppayno):'))

    # Admin reason / TOP price collector
    async def admin_reason_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        admin_id = update.effective_user.id
        if admin_id != ADMIN_ID:
            return
        app_id = PENDING_REASONS.get(admin_id)
        top_ctx = PENDING_TOP_PRICES.get(admin_id)
        pay_reject = PENDING_PAYMENT_REJECTION.get(admin_id)
        toppay_reject = PENDING_TOP_PAYMENT_REJECTION.get(admin_id)
        
        if not app_id and not top_ctx and not pay_reject and not toppay_reject:
            return
        text = update.message.text.strip()

        # 1) Причина отклонения заявки
        if app_id:
            PENDING_REASONS.pop(admin_id, None)
            conn = db.get_connection()
            # conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM applications WHERE id=%s", (app_id,))
            row = cur.fetchone()
            if row:
                # Inkor qilingan arizani DB dan o'chirish
                cur.execute("DELETE FROM applications WHERE id=%s", (app_id,))
                conn.commit()
                try:
                    await context.bot.send_message(chat_id=row['user_id'], text=f"Kechirasiz, arizangiz rad etildi. Sabab: {text}")
                    REJECTED_STICKER = os.environ.get('STICKER_REJECTED','')
                    if REJECTED_STICKER:
                        await context.bot.send_sticker(chat_id=row['user_id'], sticker=REJECTED_STICKER)
                except Exception:
                    pass
                await update.message.reply_text("Rad etish sababi yuborildi va ariza o'chirildi ✔️")
            conn.close()
            return

        # 2) Цена за TOP-продвижение, которую нужно отправить пользователю
        if top_ctx:
            PENDING_TOP_PRICES.pop(admin_id, None)
            user_id = top_ctx.get('user_id')
            position = top_ctx.get('position')
            try:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"✅ TOP-{position} uchun chek yuborish", callback_data=f"toppaystart:{position}")]
                ])
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"Admin siz uchun TOP-{position} taklif qildi. Narx: {text}.\n\n"
                        "Agar rozimisiz, to'lovni amalga oshiring va pastdagi tugma orqali chekingizni yuboring."
                    ),
                    reply_markup=kb,
                )
                await update.message.reply_text("TOP narxi foydalanuvchiga yuborildi ✔️")
            except Exception:
                pass
            return

        # 3) Причина отклонения обычного платежа (подписка)
        if pay_reject:
            PENDING_PAYMENT_REJECTION.pop(admin_id, None)
            user_id = pay_reject.get('user_id')
            specialist_id = pay_reject.get('specialist_id')
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"Kechirasiz, to'lov cheki tasdiqlanmadi. Sabab: {text}\n\nMa'lumotlarni qayta tekshirib, qayta yuboring."
                )
                await update.message.reply_text("To'lov rad etish sababi yuborildi ✔️")
            except Exception:
                pass
            # Очищаем ожидание для этого пользователя
            PENDING_PAYMENTS.pop(user_id, None)
            return

        # 4) Причина отклонения TOP платежа
        if toppay_reject:
            PENDING_TOP_PAYMENT_REJECTION.pop(admin_id, None)
            user_id = toppay_reject.get('user_id')
            position = toppay_reject.get('position')
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"Kechirasiz, TOP uchun to'lov cheki tasdiqlanmadi. Sabab: {text}\n\n"
                        "Ma'lumotlarni qayta tekshirib, qayta urinib ko'rishingiz mumkin (buyruq /top)."
                    )
                )
                await update.message.reply_text("TOP to'lov rad etish sababi yuborildi ✔️")
            except Exception:
                pass
            # Очищаем ожидание для этого пользователя
            PENDING_TOP_PAYMENTS.pop(user_id, None)
            return

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reason_handler))
    # Приём скриншотов чеков после paystart yoki TOP
    app.add_handler(MessageHandler(filters.PHOTO, receive_payment_screenshot))

    log.info("Bot started")
    app.run_polling()


if __name__ == '__main__':
    main()
