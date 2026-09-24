"""Ishchilar davomat boti — barcha kod shu bitta faylda.
Sozlamalar config.py faylida turadi.

Fayl tuzilishi (bo'limlar):
  1. MA'LUMOTLAR BAZASI  — SQLite bilan ishlash
  2. YORDAMCHI FUNKSIYALAR — masofa, vaqt formati, admin tekshiruvi
  3. KLAVIATURALAR — tugmalar
  4. /START — hamma uchun kirish nuqtasi
  5. ISHCHI QISMI — keldim va ketyapman (jonli joylashuv bilan), statistika, bonus/jazolarim
  6. ADMIN PANELI — tugmalar orqali boshqarish (qadam-baqadam)
  7. PDF HISOBOT — davomat + bonus/jazo sabablari bilan
  8. ADMIN BUYRUQLARI — matnli buyruqlar (ixtiyoriy)
  9. ISHGA TUSHIRISH
"""

import asyncio
import logging
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from math import atan2, ceil, cos, radians, sin, sqrt
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    User,
)
from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace

from config import (
    ADMIN_IDS,
    BOT_TOKEN,
    CENTER_LATITUDE,
    CENTER_LONGITUDE,
    CHECKOUT_REQUIRES_CENTER,
    DB_PATH,
    EARLY_REQUIRED_MINUTES,
    FINE_EARLY_PER_MINUTE,
    FINE_LATE_PER_MINUTE,
    GPS_ACCURACY_TOLERANCE_METERS,
    GROUP_CHAT_ID,
    LATE_FINE_CAP_MINUTES,
    RADIUS_METERS,
    TIMEZONE,
    WARNING_STRIKE_LIMIT,
)

logger = logging.getLogger(__name__)
TZ = ZoneInfo(TIMEZONE)

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ---------- Tugma yozuvlari (klaviatura ham, handlerlar ham shu yerdan oladi) ----------
BTN_ARRIVE = "✅ Keldim"
BTN_LEAVE = "🏠 Ketyapman"
BTN_BACK = "↩️ Orqaga"
BTN_STATS = "📊 Statistikam"
BTN_MARKS = "🏅 Bonus va jazolarim"
BTN_FINES = "💰 Jarima sozlamalari"

# Xabarlardagi chiroyli ajratuvchi chiziq
LINE = "━━━━━━━━━━━━━━"

ALREADY_CHECKED = "✅ Siz bugun allaqachon kelganingizni belgilagansiz."
ALREADY_LEFT = "🏠 Siz bugun allaqachon ketganingizni belgilagansiz."
NOT_CHECKED_IN = (
    "🙈 Siz bugun hali kelganingizni belgilamagansiz.\n"
    f"Avval <b>{BTN_ARRIVE}</b> tugmasini bosing."
)

# Hafta kunlari — Python'ning weekday() tartibida: 0 = dushanba ... 6 = yakshanba
WEEKDAYS = [
    "Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba",
]

# Yangi ishchi qo'shilganda beriladigan standart ketish vaqti
DEFAULT_DEPARTURE = "18:00"

# Jazo uchun tayyor sabablar — admin ro'yxatdan tanlaydi
JAZO_REASONS = [
    "Rangli ichimlik yoki xidli mahsulot iste'mol qilish",
    "Uniforma kiymaganligi",
    "Ish vaqtida mobil qurilmalardan foydalanish",
]


# ==================== 1. MA'LUMOTLAR BAZASI ====================

def db(query: str, params=(), fetch: str | None = None):
    """Barcha SQL so'rovlar uchun bitta yordamchi funksiya.
    fetch="one" — bitta qator, fetch="all" — hamma qatorlar,
    fetch="id" — yangi qo'shilgan qatorning id raqami, aks holda rowcount.

    Diqqat: har chaqiruvda yangi ulanish ochiladi, shuning uchun keyingi
    chaqiruvda "SELECT last_insert_rowid()" 0 qaytaradi — id kerak bo'lsa
    shu yerdagi fetch="id" dan foydalaning."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute(query, params)
        if fetch == "one":
            result = cur.fetchone()
        elif fetch == "all":
            result = cur.fetchall()
        elif fetch == "id":
            result = cur.lastrowid
        else:
            result = cur.rowcount
        conn.commit()
        return result


def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                scheduled_time TEXT NOT NULL,  -- 'HH:MM' standart kelish vaqti
                departure_time TEXT NOT NULL DEFAULT '{DEFAULT_DEPARTURE}'  -- 'HH:MM' standart ketish vaqti
            );
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                attendance_date TEXT NOT NULL,
                arrived_time TEXT NOT NULL,
                is_late INTEGER NOT NULL,
                late_minutes INTEGER NOT NULL,          -- belgilangan vaqtdan keyingi daqiqalar
                early_minutes INTEGER NOT NULL DEFAULT 0,-- deadline bilan belgilangan vaqt orasidagi daqiqalar
                fine_amount INTEGER NOT NULL DEFAULT 0,  -- jami jarima (so'mda)
                scheduled_time TEXT,                     -- o'sha kunga amal qilgan belgilangan vaqt
                left_time TEXT,                          -- ketgan vaqt 'HH:MM:SS' yoki NULL
                left_lat REAL,                           -- ketayotganda yuborilgan joylashuv
                left_lon REAL,
                FOREIGN KEY (employee_id) REFERENCES employees (id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_attendance_employee_date
                ON attendance (employee_id, attendance_date);

            -- Haftalik jadval: har bir kun uchun alohida kelish/ketish vaqti.
            -- Qator yo'q  -> o'sha kunga ishchining standart vaqti ishlatiladi.
            -- arrive_time NULL -> o'sha kun dam olish kuni deb belgilangan.
            CREATE TABLE IF NOT EXISTS schedules (
                employee_id INTEGER NOT NULL,
                weekday INTEGER NOT NULL,     -- 0 = dushanba ... 6 = yakshanba
                arrive_time TEXT,             -- 'HH:MM' yoki NULL (dam olish kuni)
                leave_time TEXT,              -- 'HH:MM' yoki NULL
                PRIMARY KEY (employee_id, weekday),
                FOREIGN KEY (employee_id) REFERENCES employees (id)
            );

            -- Bonus va jazolar
            CREATE TABLE IF NOT EXISTS marks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                mark_type TEXT NOT NULL,      -- 'bonus', 'jazo' yoki 'ogohlantirish'
                reason TEXT NOT NULL,
                mark_date TEXT NOT NULL,      -- 'YYYY-MM-DD'
                created_at TEXT NOT NULL,     -- 'HH:MM:SS'
                admin_id INTEGER NOT NULL,
                FOREIGN KEY (employee_id) REFERENCES employees (id)
            );
            CREATE INDEX IF NOT EXISTS idx_marks_employee_date
                ON marks (employee_id, mark_date);

            -- Botdan turib o'zgartiriladigan sozlamalar (ish joyi koordinatasi, radius, jarimalar).
            -- Qator bo'lmasa — config.py dagi qiymat ishlatiladi.
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        conn.commit()


def today() -> str:
    """Joriy sana — config'dagi vaqt zonasi bo'yicha (server UTC bo'lsa ham to'g'ri)."""
    return datetime.now(TZ).date().isoformat()


# ---------- Sozlamalar (ish joyi koordinatasi, radius) ----------

def get_setting(key: str) -> str | None:
    row = db("SELECT value FROM settings WHERE key = ?", (key,), fetch="one")
    return row[0] if row else None


def set_setting(key: str, value) -> None:
    db(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def get_center() -> tuple[float, float]:
    """Ish joyining joriy koordinatasi. Admin botdan o'zgartirgan bo'lsa —
    bazadagi qiymat, aks holda config.py dagi boshlang'ich qiymat."""
    lat, lon = get_setting("center_lat"), get_setting("center_lon")
    if lat is None or lon is None:
        return CENTER_LATITUDE, CENTER_LONGITUDE
    return float(lat), float(lon)


def get_radius() -> int:
    """Ruxsat etilgan radius (metr) — admin o'zgartirgan bo'lsa, o'sha."""
    value = get_setting("radius_meters")
    return int(value) if value else RADIUS_METERS


# ---------- Jarima sozlamalari (admin botdan istalgan vaqtda o'zgartiradi) ----------
# config.py dagi qiymatlar — BOSHLANG'ICH. Admin "💰 Jarima sozlamalari" orqali
# o'zgartirsa, yangi qiymat bazaga yoziladi va config.py dagisidan ustun turadi.
# O'zgarish faqat KEYINGI "Keldim"larga ta'sir qiladi: eski davomat yozuvlarida
# jarima o'sha paytdagi summa bilan saqlangan va o'zgarmaydi.
FINE_SETTINGS = {
    "early_required": {
        "key": "fine_early_required_minutes", "default": EARLY_REQUIRED_MINUTES,
        "min": 0, "max": 120, "unit": "daqiqa", "icon": "⏰",
        "title": "Oldindan kelish vaqti",
        "ask": "Ishchi belgilangan vaqtdan necha daqiqa <b>OLDIN</b> kelishi kerak?",
        "example": "5",
    },
    "early_rate": {
        "key": "fine_early_per_minute", "default": FINE_EARLY_PER_MINUTE,
        "min": 0, "max": 10_000_000, "unit": "so'm", "icon": "🟡",
        "title": "Erta oyna jarimasi (har daqiqa)",
        "ask": (
            "Erta kelish oynasida (masalan 08:55–09:00) kechiktirilgan "
            "<b>har bir daqiqa</b> uchun jarima summasi qancha bo'lsin?"
        ),
        "example": "5000",
    },
    "late_rate": {
        "key": "fine_late_per_minute", "default": FINE_LATE_PER_MINUTE,
        "min": 0, "max": 10_000_000, "unit": "so'm", "icon": "🔴",
        "title": "Kechikish jarimasi (har daqiqa)",
        "ask": (
            "Belgilangan vaqtdan keyin kechikkan <b>har bir daqiqa</b> "
            "uchun jarima summasi qancha bo'lsin?"
        ),
        "example": "7000",
    },
    "late_cap": {
        "key": "fine_late_cap_minutes", "default": LATE_FINE_CAP_MINUTES,
        "min": 1, "max": 240, "unit": "daqiqa", "icon": "🛑",
        "title": "Jarima hisoblash chegarasi",
        "ask": (
            "Belgilangan vaqtdan keyin eng ko'pi bilan necha daqiqagacha jarima "
            "hisoblansin? Undan ortiq kechiksa — jarima to'xtaydi va ishchiga "
            "ogohlantirish beriladi."
        ),
        "example": "10",
    },
    "strikes": {
        "key": "fine_warning_strike_limit", "default": WARNING_STRIKE_LIMIT,
        "min": 1, "max": 20, "unit": "marta", "icon": "🔔",
        "title": "Ogohlantirish limiti",
        "ask": "Necha marta ogohlantirishdan keyin qattiq chora ko'rilsin?",
        "example": "3",
    },
}


def get_fine_rates() -> dict[str, int]:
    """Joriy jarima sozlamalari: {'early_required': 5, 'early_rate': 5000, ...}.
    Bazada qiymat bo'lsa — o'sha, bo'lmasa config.py dagi boshlang'ich."""
    rows = dict(db("SELECT key, value FROM settings WHERE key LIKE 'fine_%'", fetch="all"))
    rates: dict[str, int] = {}
    for name, spec in FINE_SETTINGS.items():
        try:
            rates[name] = int(rows[spec["key"]])
        except (KeyError, ValueError):
            rates[name] = spec["default"]
    return rates


def get_employee(telegram_id: int):
    """(id, telegram_id, ism, familiya, kelish_vaqti, ketish_vaqti) yoki None."""
    return db(
        "SELECT id, telegram_id, first_name, last_name, scheduled_time, departure_time "
        "FROM employees WHERE telegram_id = ?",
        (telegram_id,), fetch="one",
    )


def add_employee(
    telegram_id: int, first_name: str, last_name: str,
    sched_time: str, departure: str = DEFAULT_DEPARTURE,
) -> bool:
    try:
        db(
            "INSERT INTO employees "
            "(telegram_id, first_name, last_name, scheduled_time, departure_time) "
            "VALUES (?, ?, ?, ?, ?)",
            (telegram_id, first_name, last_name, sched_time, departure),
        )
        return True
    except sqlite3.IntegrityError:  # bu telegram_id allaqachon mavjud
        return False


# ---------- Haftalik jadval ----------

def get_day_schedule(employee_id: int, weekday: int):
    """Shu hafta kuni uchun (kelish, ketish) juftligi.
    None — bu kunga alohida jadval yo'q (standart vaqt ishlatiladi).
    Kelish None bo'lsa — kun dam olish kuni deb belgilangan."""
    return db(
        "SELECT arrive_time, leave_time FROM schedules WHERE employee_id = ? AND weekday = ?",
        (employee_id, weekday), fetch="one",
    )


def get_week_schedule(employee_id: int) -> dict[int, tuple]:
    """{hafta_kuni: (kelish, ketish)} — faqat belgilangan kunlar."""
    rows = db(
        "SELECT weekday, arrive_time, leave_time FROM schedules WHERE employee_id = ?",
        (employee_id,), fetch="all",
    )
    return {weekday: (arrive, leave) for weekday, arrive, leave in rows}


def set_day_schedule(employee_id: int, weekday: int, arrive: str | None, leave: str | None):
    db(
        "INSERT INTO schedules (employee_id, weekday, arrive_time, leave_time) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(employee_id, weekday) DO UPDATE SET arrive_time = ?, leave_time = ?",
        (employee_id, weekday, arrive, leave, arrive, leave),
    )


def clear_week_schedule(employee_id: int) -> int:
    return db("DELETE FROM schedules WHERE employee_id = ?", (employee_id,))


def times_for_day(employee, when: datetime) -> tuple[str | None, str | None]:
    """O'sha kunga amal qiladigan (kelish, ketish) vaqtlari.
    Haftalik jadvalda qator bo'lsa — o'sha, aks holda standart vaqtlar."""
    day = get_day_schedule(employee[0], when.weekday())
    if day is not None:
        return day[0], day[1]
    return employee[4], employee[5]


# ---------- Bonus va jazolar ----------

def add_mark(employee_id: int, mark_type: str, reason: str, admin_id: int):
    now = datetime.now(TZ)
    db(
        "INSERT INTO marks (employee_id, mark_type, reason, mark_date, created_at, admin_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (employee_id, mark_type, reason, now.date().isoformat(),
         now.strftime("%H:%M:%S"), admin_id),
    )


def count_marks(employee_id: int, mark_type: str) -> int:
    """Ishchining shu turdagi (bonus/jazo/ogohlantirish) yozuvlari soni."""
    return db(
        "SELECT COUNT(*) FROM marks WHERE employee_id = ? AND mark_type = ?",
        (employee_id, mark_type), fetch="one",
    )[0]


def get_marks(employee_id: int, date_from=None, date_to=None):
    """(turi, sabab, sana) ro'yxati — eng yangisi birinchi."""
    query = "SELECT mark_type, reason, mark_date FROM marks WHERE employee_id = ?"
    params: list = [employee_id]
    if date_from and date_to:
        query += " AND mark_date BETWEEN ? AND ?"
        params += [date_from.isoformat(), date_to.isoformat()]
    return db(query + " ORDER BY mark_date DESC, id DESC", tuple(params), fetch="all")


def record_attendance(
    employee_id: int, arrived: str, is_late: bool, late_min: int,
    early_min: int, fine_amount: int, scheduled: str | None,
) -> bool:
    """Davomatni yozadi; bugun allaqachon yozuv bo'lsa False qaytaradi."""
    return db(
        "INSERT OR IGNORE INTO attendance "
        "(employee_id, attendance_date, arrived_time, is_late, late_minutes, "
        "early_minutes, fine_amount, scheduled_time) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (employee_id, today(), arrived, int(is_late), late_min,
         early_min, fine_amount, scheduled),
    ) > 0


def has_checked_in_today(employee_id: int) -> bool:
    return db(
        "SELECT 1 FROM attendance WHERE employee_id = ? AND attendance_date = ?",
        (employee_id, today()), fetch="one",
    ) is not None


def has_checked_out_today(employee_id: int) -> bool:
    """Bugun ish tugatilgani (ketgan vaqt yozilgani) belgilanganmi."""
    row = db(
        "SELECT left_time FROM attendance WHERE employee_id = ? AND attendance_date = ?",
        (employee_id, today()), fetch="one",
    )
    return row is not None and row[0] is not None


def record_checkout(
    employee_id: int, left_time: str,
    lat: float | None = None, lon: float | None = None,
) -> bool:
    """Bugungi davomat yozuviga ketish vaqti va (bo'lsa) joylashuvini yozadi.
    Yozuv topilmasa yoki allaqachon ketgan bo'lsa False qaytaradi."""
    return db(
        "UPDATE attendance SET left_time = ?, left_lat = ?, left_lon = ? "
        "WHERE employee_id = ? AND attendance_date = ? AND left_time IS NULL",
        (left_time, lat, lon, employee_id, today()),
    ) > 0


# ==================== 2. YORDAMCHI FUNKSIYALAR ====================

def distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Ikki geografik nuqta orasidagi masofa, metrlarda (Haversine formulasi)."""
    p1, p2 = radians(lat1), radians(lat2)
    a = (
        sin(radians(lat2 - lat1) / 2) ** 2
        + cos(p1) * cos(p2) * sin(radians(lon2 - lon1) / 2) ** 2
    )
    return 6371000 * 2 * atan2(sqrt(a), sqrt(1 - a))


def location_check(location) -> tuple[float, float, float]:
    """Yuborilgan joylashuvni ish joyi bilan solishtiradi.

    Qaytaradi: (haqiqiy masofa, GPS xatoligi uchun berilgan yon berish,
                hisobga olinadigan masofa).

    Nega yon berish kerak: bino ichida telefon sun'iy yo'ldoshni ko'rmaydi va
    joylashuvni Wi-Fi/uyali tarmoq bo'yicha taxminlaydi. Telegram bunday
    joylashuv bilan birga "horizontal_accuracy" (xatolik radiusi) ni yuboradi —
    u 100-500 metr bo'lishi mumkin. Shu xatolikni hisobga olmasak, ish joyida
    o'tirgan ishchi ham "uzoqdasiz" degan javob oladi.
    """
    center_lat, center_lon = get_center()
    real = distance_meters(
        location.latitude, location.longitude, center_lat, center_lon
    )
    accuracy = location.horizontal_accuracy or 0
    tolerance = min(accuracy, GPS_ACCURACY_TOLERANCE_METERS)
    return real, tolerance, max(real - tolerance, 0)


def format_minutes(total_minutes: int) -> str:
    """123 -> '2 soat 3 daqiqa', 45 -> '45 daqiqa'."""
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours} soat {minutes} daqiqa" if hours else f"{minutes} daqiqa"


def format_money(amount: int) -> str:
    """15000 -> '15 000 so'm'."""
    return f"{amount:,}".replace(",", " ") + " so'm"


def compute_fine(
    now: datetime, scheduled_dt: datetime, rates: dict[str, int] | None = None,
) -> tuple[int, int, int, bool]:
    """Kelgan vaqtga qarab jarimani hisoblaydi.
    Qaytaradi: (erta_oyna_daqiqasi, kechikkan_daqiqa, jami_jarima, ortiqcha_kechikish).

    Summalar admin botdan o'zgartira oladigan sozlamalardan olinadi (get_fine_rates).
    Ishchi belgilangan vaqtdan `early_required` daqiqa oldin kelishi kerak:
      • deadline (masalan 08:55) gacha kelsa — jarima yo'q;
      • deadline bilan belgilangan vaqt (08:55–09:00) orasidagi har daqiqa — early_rate;
      • belgilangan vaqtdan (09:00) keyingi har daqiqa — late_rate.
    Belgilangan vaqtdan `late_cap` daqiqadan ortiq kech qolsa, jarima shu chegarada
    TO'XTAYDI va ortiqcha_kechikish=True qaytadi (ogohlantirish uchun).
    30 soniya ham 1 daqiqa deb hisoblanadi (yaxlitlash yuqoriga)."""
    rates = rates or get_fine_rates()
    deadline = scheduled_dt - timedelta(minutes=rates["early_required"])
    if now <= deadline:
        return 0, 0, 0, False

    # Erta kelish oynasi: deadline dan belgilangan vaqtgacha (yoki kelgan vaqtgacha) bo'lgan qism
    early_seconds = (min(now, scheduled_dt) - deadline).total_seconds()
    early_min = ceil(early_seconds / 60) if early_seconds > 0 else 0

    # Belgilangan vaqtdan keyingi qism (haqiqiy kechikish)
    late_seconds = (now - scheduled_dt).total_seconds()
    late_min = ceil(late_seconds / 60) if late_seconds > 0 else 0

    # Jarima hisoblashda kech qism chegarada to'xtaydi
    excessive = late_min > rates["late_cap"]
    charged_late_min = min(late_min, rates["late_cap"])

    total = early_min * rates["early_rate"] + charged_late_min * rates["late_rate"]
    return early_min, late_min, total, excessive


def is_admin(user: User | None) -> bool:
    return user is not None and user.id in ADMIN_IDS


def format_week_schedule(employee) -> str:
    """Ishchining haftalik jadvalini o'qiladigan matnga aylantiradi."""
    week = get_week_schedule(employee[0])
    if not week:
        return (
            f"Haftalik jadval belgilanmagan — har kuni standart vaqt:\n"
            f"🕘 Kelish: {employee[4]}   🕕 Ketish: {employee[5]}"
        )

    lines = []
    for weekday, name in enumerate(WEEKDAYS):
        if weekday in week:
            arrive, leave = week[weekday]
            if arrive is None:
                lines.append(f"• {name}: 🌙 dam olish kuni")
            else:
                lines.append(f"• {name}: 🕘 {arrive} — 🕕 {leave or '—'}")
        else:
            lines.append(f"• {name}: {employee[4]} — {employee[5]} (standart)")
    return "\n".join(lines)


def not_registered_text(user_id: int) -> str:
    """Ro'yxatda yo'q foydalanuvchiga o'z IDsini ko'rsatamiz —
    shu ID orqali admin uni osongina qo'shadi."""
    return (
        "👋 <b>Salom!</b>\n"
        f"{LINE}\n"
        "Siz hali ro'yxatda yo'qsiz 🙈\n\n"
        f"🆔 Sizning ID raqamingiz: <code>{user_id}</code>\n"
        "Shu raqamni administratorga yuboring — u sizni qo'shadi 😊"
    )


# ==================== 3. KLAVIATURALAR ====================

def menu_kb(user: User) -> ReplyKeyboardMarkup | None:
    """Foydalanuvchi kimligiga qarab asosiy menyu tugmalari:
    ishchiga — Keldim/Statistika, adminga — boshqaruv tugmalari."""
    rows = []
    employee = get_employee(user.id)
    if employee:
        rows.append([
            KeyboardButton(text=BTN_ARRIVE),
            KeyboardButton(text=BTN_LEAVE),
        ])
        rows.append([
            KeyboardButton(text=BTN_STATS),
            KeyboardButton(text=BTN_MARKS),
        ])
    if is_admin(user):
        rows.append([
            KeyboardButton(text="➕ Ishchi qo'shish"),
            KeyboardButton(text="📋 Ishchilar ro'yxati"),
        ])
        rows.append([
            KeyboardButton(text="🏅 Bonus berish"),
            KeyboardButton(text="⚠️ Jazo berish"),
        ])
        rows.append([
            KeyboardButton(text="📄 PDF hisobot"),
            KeyboardButton(text="📍 Ish joyi lokatsiyasi"),
        ])
        rows.append([KeyboardButton(text=BTN_FINES)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True) if rows else None


def employees_pick_kb(prefix: str) -> InlineKeyboardMarkup | None:
    """Barcha ishchilar ro'yxatidan bittasini tanlash uchun tugmalar."""
    employees = db(
        "SELECT first_name, last_name, telegram_id FROM employees ORDER BY first_name, last_name",
        fetch="all",
    )
    if not employees:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{first} {last}", callback_data=f"{prefix}:{tg_id}")]
        for first, last, tg_id in employees
    ])


CANCEL_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
    resize_keyboard=True,
)

# Ketayotganda joylashuv kutilayotganda ko'rinadigan bitta tugma
LEAVE_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_BACK)]],
    resize_keyboard=True,
)

STATS_KB = InlineKeyboardMarkup(
    inline_keyboard=[[
        InlineKeyboardButton(text="📅 Bugun", callback_data="stats_day"),
        InlineKeyboardButton(text="🗓 Bu hafta", callback_data="stats_week"),
        InlineKeyboardButton(text="📆 Bu oy", callback_data="stats_month"),
    ]]
)

REPORT_KB = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="📆 Shu oy", callback_data="rep_this"),
            InlineKeyboardButton(text="🗓 O'tgan oy", callback_data="rep_prev"),
        ],
        [InlineKeyboardButton(text="✏️ Boshqa davr", callback_data="rep_custom")],
    ]
)

MARKS_KB = InlineKeyboardMarkup(
    inline_keyboard=[[
        InlineKeyboardButton(text="🏅 Bonuslar", callback_data="my_bonus"),
        InlineKeyboardButton(text="⚠️ Jazolar", callback_data="my_jazo"),
        InlineKeyboardButton(text="🔔 Ogohlantirishlar", callback_data="my_ogoh"),
    ]]
)


def schedule_kb(selected: set[int]) -> InlineKeyboardMarkup:
    """Haftalik jadval muharriri: kunlarni belgilash + amallar."""
    rows = []
    for start in range(0, 7, 2):
        rows.append([
            InlineKeyboardButton(
                text=f"{'☑️' if weekday in selected else '☐'} {WEEKDAYS[weekday]}",
                callback_data=f"sday:{weekday}",
            )
            for weekday in range(start, min(start + 2, 7))
        ])
    rows.append([InlineKeyboardButton(text="⏰ Tanlangan kunlarga vaqt belgilash", callback_data="sset")])
    rows.append([InlineKeyboardButton(text="🌙 Tanlangan kunlar — dam olish", callback_data="soff")])
    rows.append([InlineKeyboardButton(text="🗑 Jadvalni tozalash", callback_data="sclear")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Routerlar: admin buyruqlari birinchi, keyin admin paneli, keyin ishchi qismi
admin_router = Router()

panel_router = Router()
panel_router.message.filter(F.chat.type == "private", F.from_user.id.in_(ADMIN_IDS))
panel_router.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))

employee_router = Router()
# Guruhdagi "Keldim" yoki lokatsiya xabarlariga javob bermasligi uchun faqat shaxsiy chat
employee_router.message.filter(F.chat.type == "private")


# ==================== 4. /START ====================

def greeting() -> str:
    """Kun vaqtiga qarab salom."""
    hour = datetime.now(TZ).hour
    if 5 <= hour < 12:
        return "🌅 Xayrli tong"
    if 12 <= hour < 18:
        return "☀️ Xayrli kun"
    return "🌙 Xayrli kech"


# Jonli joylashuv qanday yuboriladi — kelish ham, ketish ham shu yo'l bilan
LIVE_LOCATION_GUIDE = (
    "1️⃣ Chatdagi <b>📎</b> (skrepka) tugmasini bosing\n"
    "2️⃣ <b>Location</b> (Joylashuv) ni tanlang\n"
    "3️⃣ <b>Share My Live Location</b> (Jonli joylashuvni ulashish) ni bosing\n"
    "4️⃣ Vaqtni tanlang (masalan, 15 daqiqa) va yuboring ✅"
)


class LeaveFlow(StatesGroup):
    location = State()  # "Ketyapman" bosildi — jonli joylashuv kutilmoqda


@employee_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()  # /start har doim jarayonni boshidan boshlaydi
    user = message.from_user
    employee = get_employee(user.id)
    kb = menu_kb(user)

    if is_admin(user):
        text = (
            f"{greeting()}, admin! 👋\n"
            f"{LINE}\n"
            "Hamma narsa pastdagi tugmalar orqali — hech narsani yodlash shart emas 😊\n\n"
            "➕ <b>Ishchi qo'shish</b> — bot hammasini qadam-baqadam so'raydi\n"
            "📋 <b>Ishchilar ro'yxati</b> — vaqt, jadval, o'chirish\n"
            "🏅 <b>Bonus berish</b> — yaxshi ish uchun rahmat\n"
            "⚠️ <b>Jazo berish</b> — sababni ro'yxatdan tanlaysiz\n"
            "📄 <b>PDF hisobot</b> — davomat, bonus va jazolar\n"
            "📍 <b>Ish joyi lokatsiyasi</b> — ish joyi nuqtasi va radius\n"
            f"{BTN_FINES} — kechikish jarimasi summalarini o'zgartirish"
        )
        if employee:
            text += (
                f"\n\nSiz ishchi sifatida ham ro'yxatdasiz — "
                f"<b>{BTN_ARRIVE}</b> va <b>{BTN_LEAVE}</b> tugmalari ishlaydi."
            )
        await message.answer(text, reply_markup=kb)
        return

    if not employee:
        await message.answer(not_registered_text(user.id))
        return

    first_name = employee[2]
    text = (
        f"{greeting()}, <b>{first_name}</b>! 👋\n"
        f"{LINE}\n"
        f"🟢 Ishga kelganda — <b>{BTN_ARRIVE}</b>\n"
        f"🏠 Ketayotganda — <b>{BTN_LEAVE}</b>\n"
        "📍 Ikkalasida ham <b>jonli joylashuv</b> yuborasiz.\n"
    )
    text += f"\n🗓 <b>Ish jadvalingiz</b>\n{format_week_schedule(employee)}"
    await message.answer(text, reply_markup=kb)


# ==================== 5. ISHCHI QISMI ====================

@employee_router.message(F.text == BTN_ARRIVE)
async def handle_keldim(message: Message):
    employee = get_employee(message.from_user.id)
    if not employee:
        await message.answer(not_registered_text(message.from_user.id))
        return

    if has_checked_in_today(employee[0]):
        await message.answer(ALREADY_CHECKED)
        return

    await message.answer(
        "🟢 <b>Keldim — joylashuvni yuboring</b>\n"
        f"{LINE}\n"
        f"{LIVE_LOCATION_GUIDE}\n\n"
        "⚠️ Xaritadan belgilangan oddiy nuqta emas — faqat <b>jonli</b> "
        "joylashuv qabul qilinadi."
    )


def location_problem(message: Message) -> str | None:
    """Yuborilgan joylashuv qabul qilinmasa — sababi (matn), aks holda None.
    Kelish ham, ketish ham bir xil qoida bilan tekshiriladi.

    Nega faqat JONLI joylashuv: oddiy joylashuvda foydalanuvchi xaritada istalgan
    nuqtani (masalan, ish joyini) qo'lda belgilab yuborishi mumkin — bu firibgarlikka
    yo'l ochadi. Jonli joylashuv esa qurilmaning haqiqiy GPS'idan olinadi."""
    if message.forward_origin is not None:
        return (
            "❌ <b>Forward qilingan</b> joylashuv qabul qilinmaydi.\n\n"
            "Iltimos, o'zingiz <b>jonli joylashuv</b> yuboring:\n\n"
            f"{LIVE_LOCATION_GUIDE}"
        )
    if message.location.live_period is None:
        return (
            "❌ Xaritadan belgilangan <b>oddiy nuqta</b> qabul qilinmaydi.\n\n"
            "Menga <b>jonli</b> joylashuv kerak:\n\n"
            f"{LIVE_LOCATION_GUIDE}"
        )
    return None


def too_far_text(head: str, real_dist: float, tolerance: float, radius: int) -> str:
    """Ish joyidan uzoqda bo'lganda ko'rsatiladigan javob (kelish va ketish uchun umumiy)."""
    text = (
        f"{head}\n"
        f"{LINE}\n"
        f"📏 Ish joyigacha: taxminan <b>{int(real_dist)} metr</b>\n"
        f"✅ Ruxsat etilgan: {radius} metr\n"
    )
    if tolerance:
        text += f"📡 GPS xatoligi hisobga olindi: −{int(tolerance)} metr\n"
    text += (
        "\n💡 <b>Agar siz ish joyidasiz:</b>\n"
        "• Telefonda joylashuvni <b>High accuracy</b> (Aniq joylashuv) ga qo'ying\n"
        "• Wi-Fi'ni yoqib qo'ying — bino ichida aniqlik oshadi\n"
        "• Deraza yoniga yoki tashqariga chiqib, 10–20 soniya kutib, qayta yuboring\n"
        "• Baribir bo'lmasa — adminga ayting, u ish joyi nuqtasini to'g'rilaydi"
    )
    return text


@employee_router.message(StateFilter(LeaveFlow.location), F.text == BTN_BACK)
async def leave_back(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("👌 Yaxshi, bekor qilindi.", reply_markup=menu_kb(message.from_user))


@employee_router.message(F.text == BTN_LEAVE)
async def leave_start(message: Message, state: FSMContext):
    """\"Ketyapman\" bosildi — endi kelishdagi kabi JONLI joylashuv so'raladi.
    Ketish vaqti shu joylashuv kelgan paytda yoziladi."""
    employee = get_employee(message.from_user.id)
    if not employee:
        await message.answer(not_registered_text(message.from_user.id))
        return

    # Avval kelgan bo'lishi kerak — aks holda yangilanadigan yozuv yo'q
    if not has_checked_in_today(employee[0]):
        await message.answer(NOT_CHECKED_IN)
        return

    if has_checked_out_today(employee[0]):
        await message.answer(ALREADY_LEFT)
        return

    await state.set_state(LeaveFlow.location)
    await message.answer(
        "🏠 <b>Ketyapman — joylashuvni yuboring</b>\n"
        f"{LINE}\n"
        f"{LIVE_LOCATION_GUIDE}\n\n"
        "📌 Ish joyidan chiqayotganda, hali <b>ish joyida turib</b> yuboring.\n"
        f"Fikringiz o'zgarsa — <b>{BTN_BACK}</b> ni bosing.",
        reply_markup=LEAVE_KB,
    )


@employee_router.message(StateFilter(LeaveFlow.location), F.location)
async def handle_leave_location(message: Message, state: FSMContext, is_update: bool = False):
    """is_update haqida — handle_location'dagi izohga qarang: bu ham jonli
    joylashuvning keyingi yangilanishi bo'lishi mumkin, shunda jim tekshiramiz."""
    employee = get_employee(message.from_user.id)
    if not employee:
        if is_update:
            return
        await state.clear()
        await message.answer(not_registered_text(message.from_user.id))
        return

    employee_id, first_name, last_name = employee[0], employee[2], employee[3]
    menu = menu_kb(message.from_user)

    if not has_checked_in_today(employee_id):
        if is_update:
            return
        # "Ketyapman" faqat kelgan ishchiga ochiladi, demak bu — kechadan qolib
        # ketgan eski holat. Hozirgi lokatsiya esa aslida bugungi KELISH lokatsiyasi.
        await state.clear()
        await handle_location(message)
        return
    if has_checked_out_today(employee_id):
        if is_update:
            return
        await state.clear()
        await message.answer(ALREADY_LEFT, reply_markup=menu)
        return

    # Forward yoki oddiy nuqta bo'lsa — holat saqlanadi, employee qayta yuboradi
    problem = location_problem(message)
    if problem:
        if is_update:
            return
        await message.answer(problem, reply_markup=LEAVE_KB)
        return

    real_dist, tolerance, dist = location_check(message.location)
    radius = get_radius()
    if CHECKOUT_REQUIRES_CENTER and dist > radius:
        logger.info(
            "Ketish lokatsiyasi rad etildi (%s): employee_id=%s, masofa=%.0fm, xatolik=%.0fm",
            "yangilanish" if is_update else "birinchi", employee_id, real_dist,
            message.location.horizontal_accuracy or 0,
        )
        if is_update:
            return
        await message.answer(
            too_far_text(
                "🙈 <b>Siz ish joyidan uzoqdasiz.</b>\nKetayotganda ish joyida turib yuboring.",
                real_dist, tolerance, radius,
            ),
            reply_markup=LEAVE_KB,
        )
        return

    now = datetime.now(TZ)
    left = now.strftime("%H:%M:%S")

    # Shu kunga belgilangan ketish vaqti (haftalik jadval bo'lsa — o'sha)
    _arrive_time, leave_time = times_for_day(employee, now)

    # Belgilangan vaqtdan oldin ketdimi?
    left_early = False
    early_minutes = 0
    if leave_time:
        leave_hour, leave_minute = map(int, leave_time.split(":"))
        leave_dt = now.replace(hour=leave_hour, minute=leave_minute, second=0, microsecond=0)
        if now < leave_dt:
            left_early = True
            early_minutes = ceil((leave_dt - now).total_seconds() / 60)

    if not record_checkout(
        employee_id, left, message.location.latitude, message.location.longitude
    ):
        await state.clear()
        await message.answer(ALREADY_LEFT, reply_markup=menu)
        return
    await state.clear()

    # Ishchiga javob
    reply = (
        f"👋 <b>Xayr, {first_name}!</b>\n"
        f"{LINE}\n"
        "✅ Ketishingiz yozildi\n"
        f"🕒 Ketgan vaqt: <b>{left}</b>\n"
    )
    if left_early:
        reply += (
            f"⚠️ Belgilangan ketish vaqti ({leave_time}) dan "
            f"{format_minutes(early_minutes)} oldin ketdingiz.\n"
        )
    reply += (
        "\n🔒 Xohlasangiz, joylashuvni ulashishni endi to'xtatishingiz mumkin — "
        "bizga kerak bo'lgani shu bir lahzadagi joy edi.\n"
        "Yaxshi dam oling! 🌙"
    )
    await message.answer(reply, reply_markup=menu)

    # Guruh va adminlarga xabar
    if leave_time is None:
        status = "🌙 Bugun dam olish kuni sifatida belgilangan"
    elif left_early:
        status = (
            f"🕕 Belgilangan ketish vaqti: {leave_time}\n"
            f"🟡 Belgilangan vaqtdan {format_minutes(early_minutes)} oldin ketdi"
        )
    else:
        status = f"🕕 Belgilangan ketish vaqti: {leave_time}\n🟢 O'z vaqtida ketdi"

    notice = (
        "🏠 <b>Ishdan ketdi</b>\n"
        f"👤 {first_name} {last_name}\n"
        f"🕒 Ketgan vaqti: {left}\n"
        f"{status}"
    )
    try:
        await message.bot.send_message(GROUP_CHAT_ID, notice)
    except TelegramAPIError:
        logger.exception("Guruhga (%s) ketish xabari yuborilmadi", GROUP_CHAT_ID)

    # Adminlarga — aniq lokatsiya (xarita nuqtasi) + ish joyidan masofa
    admin_notice = notice + f"\n📏 Ish joyidan: taxminan {int(real_dist)} metr"
    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_location(
                admin_id, message.location.latitude, message.location.longitude
            )
            await message.bot.send_message(admin_id, admin_notice)
        except TelegramAPIError:
            logger.exception("Adminga (%s) ketish xabari yuborilmadi", admin_id)


@employee_router.message(F.location)
async def handle_location(message: Message, is_update: bool = False):
    """Kelish lokatsiyasi. (Ketish lokatsiyasi — yuqoridagi LeaveFlow holatida.)

    is_update=True bo'lsa — bu birinchi joylashuv emas, balki JONLI joylashuvning
    keyingi yangilanishi (Telegram'ning edited_message'i). GPS ilk lahzada
    noaniq bo'lishi mumkin (ayniqsa bino ichida) — vaqt o'tib aniqlik oshadi va
    keyingi yangilanish ish joyi radiusiga tushishi mumkin. Shu sabab ishchi
    qayta tugma bosmasdan, faqat joylashuvni ulashib turishning o'zi kifoya:
    har bir yangilanishda qayta tekshiramiz. Lekin hali ham uzoq bo'lsa,
    ishchini har necha soniyada "uzoqdasiz" xabari bilan bezovta qilmaymiz —
    faqat birinchi urinishda va muvaffaqiyatli bo'lganda javob yozamiz."""
    employee = get_employee(message.from_user.id)
    if not employee:
        if is_update:
            return
        await message.answer(not_registered_text(message.from_user.id))
        return

    employee_id, first_name, last_name = employee[0], employee[2], employee[3]

    if has_checked_in_today(employee_id):
        if is_update:
            return
        if has_checked_out_today(employee_id):
            await message.answer(ALREADY_LEFT)
        else:
            await message.answer(
                f"{ALREADY_CHECKED}\n\n"
                f"Ketmoqchi bo'lsangiz — avval <b>{BTN_LEAVE}</b> tugmasini bosing, "
                "keyin joylashuvni yuboring."
            )
        return

    problem = location_problem(message)
    if problem:
        if is_update:
            return
        await message.answer(problem, reply_markup=menu_kb(message.from_user))
        return

    real_dist, tolerance, dist = location_check(message.location)
    radius = get_radius()
    if dist > radius:
        logger.info(
            "Lokatsiya rad etildi (%s): employee_id=%s, masofa=%.0fm, xatolik=%.0fm, "
            "koordinata=%s,%s",
            "yangilanish" if is_update else "birinchi", employee_id, real_dist,
            message.location.horizontal_accuracy or 0,
            message.location.latitude, message.location.longitude,
        )
        if is_update:
            # Jonli joylashuv hali ham keladi — GPS to'g'rilanishi mumkin,
            # keyingi yangilanishda qaytadan tekshiramiz. Hozircha jim turamiz.
            return
        await message.answer(
            too_far_text(
                "🙈 <b>Siz hali ish joyiga yetib kelmagansiz.</b>",
                real_dist, tolerance, radius,
            ),
            reply_markup=menu_kb(message.from_user),
        )
        return

    now = datetime.now(TZ)
    arrived = now.strftime("%H:%M:%S")
    rates = get_fine_rates()  # admin o'zgartirgan joriy jarima sozlamalari

    # Shu hafta kuniga belgilangan vaqt (haftalik jadval bo'lsa — o'sha, aks holda standart)
    scheduled_time, leave_time = times_for_day(employee, now)

    excessive = False
    if scheduled_time is None:
        # Bu kun dam olish kuni deb belgilangan — kechikish/jarima hisoblanmaydi
        early_minutes = late_minutes = fine_amount = 0
    else:
        sched_hour, sched_minute = map(int, scheduled_time.split(":"))
        scheduled_dt = now.replace(hour=sched_hour, minute=sched_minute, second=0, microsecond=0)
        early_minutes, late_minutes, fine_amount, excessive = compute_fine(
            now, scheduled_dt, rates
        )

    is_late = late_minutes > 0
    # Jarima hisoblangan kech daqiqalar (chegarada to'xtaydi)
    charged_late = min(late_minutes, rates["late_cap"])

    if not record_attendance(employee_id, arrived, is_late, late_minutes,
                             early_minutes, fine_amount, scheduled_time):
        await message.answer(ALREADY_CHECKED)
        return

    # Ortiqcha kechikish bo'lsa — ogohlantirishni yozib qo'yamiz (admin_id=0: tizim)
    warning_text = None
    if excessive:
        reason = (
            f"Belgilangan vaqtdan {format_minutes(late_minutes)} kech keldi "
            f"({rates['late_cap']} daqiqadan ortiq)"
        )
        add_mark(employee_id, "ogohlantirish", reason, 0)
        strikes = count_marks(employee_id, "ogohlantirish")  # shu ogohlantirish ham hisobga olindi
        remaining = rates["strikes"] - strikes
        warning_text = (
            "⚠️ <b>Ogohlantirish!</b>\n"
            f"Siz keragidan ortiq ({rates['late_cap']} daqiqadan ko'p) kech qoldingiz.\n"
        )
        if remaining > 0:
            warning_text += f"Yana {remaining} marta shunday bo'lsa, qattiq chora ko'riladi."
        else:
            warning_text += (
                f"Bu — {strikes}-ogohlantirish. Chegara ({rates['strikes']}) dan oshdi, "
                "qattiq chora ko'riladi!"
            )

    # Guruhga yuboriladigan xabar
    group_text = f"🟢 <b>Ishga keldi</b>\n👤 {first_name} {last_name}\n🕒 Kelgan vaqti: {arrived}\n"
    if scheduled_time is None:
        group_text += "🌙 Bugun dam olish kuni sifatida belgilangan"
    elif fine_amount == 0:
        group_text += f"⏰ Belgilangan vaqt: {scheduled_time}\n🟢 Vaqtida keldi"
    else:
        group_text += f"⏰ Belgilangan vaqt: {scheduled_time}\n"
        if early_minutes:
            group_text += (
                f"🟡 Erta kelish oynasida {early_minutes} daqiqa kechikdi "
                f"→ {format_money(early_minutes * rates['early_rate'])}\n"
            )
        if late_minutes:
            cap_note = f" ({rates['late_cap']} daqiqada to'xtatildi)" if excessive else ""
            group_text += (
                f"🔴 Belgilangan vaqtdan {format_minutes(late_minutes)} kech qoldi{cap_note} "
                f"→ {format_money(charged_late * rates['late_rate'])}\n"
            )
        group_text += f"💰 Jami jarima: <b>{format_money(fine_amount)}</b>"
        if excessive:
            group_text += "\n⚠️ Keragidan ortiq kech qoldi — ogohlantirish berildi."

    try:
        await message.bot.send_message(GROUP_CHAT_ID, group_text)
    except TelegramAPIError:
        logger.exception("Guruhga (%s) xabar yuborib bo'lmadi", GROUP_CHAT_ID)

    # Har bir adminga alohida — aniq lokatsiya (xarita nuqtasi) + qisqacha xabar:
    # ism-familiya, kelgan vaqt va (agar kech qolgan bo'lsa) necha daqiqa kechikkani
    if scheduled_time is None:
        admin_status = "🌙 Bugun dam olish kuni sifatida belgilangan"
    elif is_late:
        admin_status = f"🔴 Kech qoldi: {format_minutes(late_minutes)}"
    elif fine_amount:
        admin_status = f"🟡 Erta kelish oynasida {early_minutes} daqiqa kechikdi"
    else:
        admin_status = "🟢 Vaqtida keldi"
    admin_text = (
        "📍 <b>Yangi kelish</b>\n"
        f"👤 {first_name} {last_name}\n"
        f"🕒 Kelgan vaqti: {arrived}\n"
        f"{admin_status}"
    )
    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_location(
                admin_id, message.location.latitude, message.location.longitude
            )
            await message.bot.send_message(admin_id, admin_text)
        except TelegramAPIError:
            logger.exception("Adminga (%s) xabar yuborib bo'lmadi", admin_id)

    # Jarima bo'lmasa quvontiramiz, bo'lsa — xotirjam, neytral sarlavha
    headline = (
        f"✅ <b>Qabul qilindi, {first_name}</b>" if fine_amount
        else f"🎉 <b>Ajoyib, {first_name}!</b>"
    )
    reply = (
        f"{headline}\n"
        f"{LINE}\n"
        "📍 Kelganingiz yozildi\n"
        f"🕒 Vaqt: <b>{arrived}</b>\n"
    )
    if fine_amount:
        reply += f"💰 Bugungi jarima: <b>{format_money(fine_amount)}</b>\n"
    else:
        reply += "🟢 Vaqtida keldingiz — barakalla!\n"
    if leave_time:
        reply += f"🕕 Ketish vaqtingiz: <b>{leave_time}</b>\n"
    reply += f"\n🏠 Ketayotganda <b>{BTN_LEAVE}</b> tugmasini bosib, joylashuv yuborasiz."
    await message.answer(reply, reply_markup=menu_kb(message.from_user))

    # Ogohlantirishni alohida xabar qilib yuboramiz (ishchi e'tibor bersin)
    if warning_text:
        await message.answer(warning_text)


@employee_router.edited_message(StateFilter(LeaveFlow.location), F.location)
async def handle_leave_location_update(message: Message, state: FSMContext):
    """Jonli joylashuv Telegram'da har necha soniyada yangilanadi va bot bu
    yangilanishlarni "edited_message" sifatida oladi (yangi "message" sifatida
    emas). Ilgari bot faqat birinchi joylashuvni ko'rar edi — agar o'sha lahzada
    GPS hali aniq bo'lmasa (masalan bino ichida), ishchi qayta "🏠 Ketyapman"ni
    bosib joylashuvni qaytadan boshlashi kerak edi. Endi har bir yangilanishda
    qayta tekshiriladi — GPS to'g'rilanishi bilanoq avtomatik qabul qilinadi."""
    await handle_leave_location(message, state, is_update=True)


@employee_router.edited_message(F.location)
async def handle_location_update(message: Message):
    """Kelish uchun ham xuddi shunday — izoh yuqorida."""
    await handle_location(message, is_update=True)


@employee_router.message(F.text == BTN_STATS)
async def handle_stats_menu(message: Message):
    if not get_employee(message.from_user.id):
        await message.answer(not_registered_text(message.from_user.id))
        return

    await message.answer(
        f"📊 <b>Mening natijalarim</b>\n{LINE}\nQaysi davrni ko'ramiz? 👇",
        reply_markup=STATS_KB,
    )


@employee_router.callback_query(F.data.in_({"stats_day", "stats_week", "stats_month"}))
async def handle_stats_callback(callback: CallbackQuery):
    employee = get_employee(callback.from_user.id)
    if not employee:
        await callback.answer("Siz ro'yxatdan o'tmagansiz.", show_alert=True)
        return

    today_date = datetime.now(TZ).date()
    if callback.data == "stats_day":
        date_from, period_label = today_date, "Bugungi"
    elif callback.data == "stats_week":
        date_from = today_date - timedelta(days=today_date.weekday())  # shu haftaning dushanbasi
        period_label = "Shu haftadagi"
    else:  # stats_month
        date_from, period_label = today_date.replace(day=1), "Shu oydagi"

    records = db(
        "SELECT is_late, late_minutes, fine_amount FROM attendance "
        "WHERE employee_id = ? AND attendance_date BETWEEN ? AND ?",
        (employee[0], date_from.isoformat(), today_date.isoformat()), fetch="all",
    )

    total_days = len(records)
    late_count = sum(1 for is_late, _, _ in records if is_late)
    total_late_minutes = sum(m for is_late, m, _ in records if is_late)
    fined_days = sum(1 for _, _, fine in records if fine)
    total_fine = sum(fine for _, _, fine in records)

    if total_days == 0:
        text = f"📊 <b>{period_label} natijalar</b>\n{LINE}\nBu davrda hali yozuv yo'q 🙈"
    else:
        text = (
            f"📊 <b>{period_label} natijalar</b>\n{LINE}\n"
            f"✅ Kelgan kunlar: <b>{total_days}</b>\n"
            f"🔴 Kech qolgan kunlar: <b>{late_count}</b>\n"
        )
        if late_count:
            text += f"⏰ Jami kechikish: <b>{format_minutes(total_late_minutes)}</b>\n"
        if total_fine:
            text += (
                f"💰 Jarimali kunlar: <b>{fined_days}</b>\n"
                f"💰 Jami jarima: <b>{format_money(total_fine)}</b>"
            )
        else:
            text += "🎉 Jarima yo'q — barakalla!"

    try:
        await callback.message.edit_text(text)
    except TelegramBadRequest:
        pass  # bir xil tugma ikki marta bosilsa "message is not modified" xatosi chiqadi
    await callback.answer()


# ---------- Ishchining bonus va jazolari ----------

@employee_router.message(F.text == BTN_MARKS)
async def handle_my_marks(message: Message):
    employee = get_employee(message.from_user.id)
    if not employee:
        await message.answer(not_registered_text(message.from_user.id))
        return

    marks = get_marks(employee[0])
    bonus_count = sum(1 for mark_type, _, _ in marks if mark_type == "bonus")
    jazo_count = sum(1 for mark_type, _, _ in marks if mark_type == "jazo")
    ogoh_count = sum(1 for mark_type, _, _ in marks if mark_type == "ogohlantirish")

    await message.answer(
        f"🏅 <b>Bonus va jazolarim</b>\n{LINE}\n"
        f"🏅 Bonuslar: <b>{bonus_count}</b> ta\n"
        f"⚠️ Jazolar: <b>{jazo_count}</b> ta\n"
        f"🔔 Ogohlantirishlar: <b>{ogoh_count}</b> ta\n\n"
        "Batafsil ko'rish uchun tugmani bosing 👇",
        reply_markup=MARKS_KB,
    )


@employee_router.callback_query(F.data.in_({"my_bonus", "my_jazo", "my_ogoh"}))
async def handle_my_marks_detail(callback: CallbackQuery):
    employee = get_employee(callback.from_user.id)
    if not employee:
        await callback.answer("Siz ro'yxatdan o'tmagansiz.", show_alert=True)
        return

    wanted = {"my_bonus": "bonus", "my_jazo": "jazo", "my_ogoh": "ogohlantirish"}[callback.data]
    title = {
        "bonus": "🏅 Bonuslaringiz",
        "jazo": "⚠️ Jazolaringiz",
        "ogohlantirish": "🔔 Ogohlantirishlaringiz",
    }[wanted]
    rows = [(reason, date) for mark_type, reason, date in get_marks(employee[0])
            if mark_type == wanted]

    if not rows:
        text = f"{title}\n{LINE}\nHozircha bunday yozuv yo'q."
        if wanted != "bonus":
            text += " Shunday davom eting! 🎉"
    else:
        text = f"{title} — jami {len(rows)} ta\n{LINE}\n" + "\n".join(
            f"{i}. <b>{date}</b>\n   {reason}" for i, (reason, date) in enumerate(rows, 1)
        )

    try:
        await callback.message.edit_text(text, reply_markup=MARKS_KB)
    except TelegramBadRequest:
        pass  # bir xil tugma ikki marta bosilsa "message is not modified" xatosi chiqadi
    await callback.answer()


@employee_router.message(StateFilter(LeaveFlow.location))
async def leave_wrong_input(message: Message):
    """Ketish holatida joylashuv o'rniga boshqa narsa yuborilsa — nima qilish kerakligini eslatamiz."""
    await message.answer(
        "🙈 Menga <b>jonli joylashuv</b> kerak, xabar emas.\n\n"
        f"{LIVE_LOCATION_GUIDE}\n\n"
        f"Fikringiz o'zgargan bo'lsa — <b>{BTN_BACK}</b> ni bosing.",
        reply_markup=LEAVE_KB,
    )


# ==================== 6. ADMIN PANELI (tugmalar orqali) ====================
# Admin buyruq yodlamaydi: tugmani bosadi, bot kerakli ma'lumotni
# qadam-baqadam so'raydi. Har qadamda "❌ Bekor qilish" tugmasi bor.

class AddEmployee(StatesGroup):
    tg_id = State()
    first_name = State()
    last_name = State()
    sched_time = State()
    departure = State()


class ChangeTime(StatesGroup):
    sched_time = State()
    departure = State()


class CustomReport(StatesGroup):
    dates = State()


class GiveBonus(StatesGroup):
    reason = State()


class GiveJazo(StatesGroup):
    reason = State()


class EditSchedule(StatesGroup):
    picking = State()   # kunlarni belgilash
    arrive = State()    # kelish vaqtini kiritish
    leave = State()     # ketish vaqtini kiritish


class SetCenter(StatesGroup):
    location = State()  # ish joyining yangi nuqtasi
    radius = State()    # ruxsat etilgan radius


@panel_router.message(F.text == "❌ Bekor qilish")
async def cancel_action(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Bekor qilindi.", reply_markup=menu_kb(message.from_user))


# ---------- Ishchi qo'shish (4 qadam) ----------

@panel_router.message(F.text == "➕ Ishchi qo'shish")
async def add_step_start(message: Message, state: FSMContext):
    await state.set_state(AddEmployee.tg_id)
    await message.answer(
        "<b>1/5-qadam:</b> Ishchining Telegram ID raqamini yuboring.\n\n"
        "💡 IDni bilish oson: ishchi botga /start yozsa, bot unga ID raqamini "
        "ko'rsatadi — o'sha raqamni sizga yuborsin.\n"
        "Yoki ishchidan kelgan istalgan xabarni shu yerga forward qiling.",
        reply_markup=CANCEL_KB,
    )


@panel_router.message(AddEmployee.tg_id)
async def add_step_id(message: Message, state: FSMContext):
    # Forward qilingan xabardan IDni avtomatik olamiz
    sender = getattr(message.forward_origin, "sender_user", None)
    if sender:
        tg_id = sender.id
    elif message.text and message.text.strip().isdigit():
        tg_id = int(message.text.strip())
    else:
        await message.answer(
            "ID butun son bo'lishi kerak, masalan: 123456789.\n"
            "Qaytadan yuboring yoki ishchining xabarini forward qiling."
        )
        return

    existing = get_employee(tg_id)
    if existing:
        await state.clear()
        await message.answer(
            f"⚠️ Bu ID allaqachon ro'yxatda: {existing[2]} {existing[3]}.",
            reply_markup=menu_kb(message.from_user),
        )
        return

    await state.update_data(tg_id=tg_id)
    await state.set_state(AddEmployee.first_name)
    await message.answer(f"ID qabul qilindi: <code>{tg_id}</code>\n\n<b>2/5-qadam:</b> Ismini yozing (masalan: Ali).")


@panel_router.message(AddEmployee.first_name, F.text)
async def add_step_first_name(message: Message, state: FSMContext):
    await state.update_data(first_name=message.text.strip())
    await state.set_state(AddEmployee.last_name)
    await message.answer("<b>3/5-qadam:</b> Familiyasini yozing (masalan: Valiyev).")


@panel_router.message(AddEmployee.last_name, F.text)
async def add_step_last_name(message: Message, state: FSMContext):
    await state.update_data(last_name=message.text.strip())
    await state.set_state(AddEmployee.sched_time)
    await message.answer("<b>4/5-qadam:</b> Ishga kelish vaqtini yozing (masalan: 09:00).")


@panel_router.message(AddEmployee.sched_time, F.text)
async def add_step_time(message: Message, state: FSMContext):
    sched_time = message.text.strip()
    if not TIME_RE.match(sched_time):
        await message.answer("Vaqt HH:MM formatida bo'lishi kerak, masalan: 09:00. Qaytadan yozing.")
        return

    await state.update_data(sched_time=sched_time)
    await state.set_state(AddEmployee.departure)
    await message.answer(
        "<b>5/5-qadam:</b> Ish joyidan ketish vaqtini yozing (masalan: 18:00).\n\n"
        f"💡 O'tkazib yuborish uchun <code>-</code> yuboring — standart {DEFAULT_DEPARTURE} qo'yiladi."
    )


@panel_router.message(AddEmployee.departure, F.text)
async def add_step_departure(message: Message, state: FSMContext):
    departure = message.text.strip()
    if departure == "-":
        departure = DEFAULT_DEPARTURE
    elif not TIME_RE.match(departure):
        await message.answer("Vaqt HH:MM formatida bo'lishi kerak, masalan: 18:00. Qaytadan yozing.")
        return

    data = await state.get_data()
    await state.clear()

    if add_employee(data["tg_id"], data["first_name"], data["last_name"],
                   data["sched_time"], departure):
        await message.answer(
            f"✅ <b>{data['first_name']} {data['last_name']}</b> ro'yxatga qo'shildi!\n"
            f"🕘 Kelish vaqti: {data['sched_time']}\n"
            f"🕕 Ketish vaqti: {departure}\n\n"
            "Endi u botga /start yozib, \"✅ Keldim\" tugmasidan foydalana oladi.\n"
            "💡 Hafta kunlariga alohida vaqt kerak bo'lsa — \"📋 Ishchilar ro'yxati\" "
            "dan 🗓 tugmasini bosing.",
            reply_markup=menu_kb(message.from_user),
        )
    else:
        await message.answer(
            "⚠️ Bu ID bilan ishchi allaqachon mavjud.",
            reply_markup=menu_kb(message.from_user),
        )


# ---------- Ishchilar ro'yxati (vaqt o'zgartirish / o'chirish) ----------

@panel_router.message(F.text == "📋 Ishchilar ro'yxati")
async def show_employees_list(message: Message):
    employees = db(
        "SELECT first_name, last_name, scheduled_time, departure_time, telegram_id "
        "FROM employees ORDER BY first_name, last_name",
        fetch="all",
    )
    if not employees:
        await message.answer(
            "Hozircha ishchilar ro'yxati bo'sh.\n"
            "\"➕ Ishchi qo'shish\" tugmasi orqali birinchi ishchini qo'shing."
        )
        return

    # Har bir ishchi uchun: ⏰ — vaqt, 🗓 — haftalik jadval, 🗑 — o'chirish
    rows = []
    for first, last, sched, departure, tg_id in employees:
        rows.append([InlineKeyboardButton(
            text=f"👤 {first} {last} — {sched}/{departure}", callback_data=f"time:{tg_id}"
        )])
        rows.append([
            InlineKeyboardButton(text="⏰ Vaqt", callback_data=f"time:{tg_id}"),
            InlineKeyboardButton(text="🗓 Jadval", callback_data=f"sched:{tg_id}"),
            InlineKeyboardButton(text="🗑", callback_data=f"del:{tg_id}"),
        ])

    await message.answer(
        f"📋 Ishchilar ro'yxati ({len(employees)} ta):\n"
        "Nom yonidagi raqamlar — kelish/ketish vaqti.\n\n"
        "⏰ — standart kelish va ketish vaqtini o'zgartirish\n"
        "🗓 — hafta kunlariga alohida vaqt belgilash\n"
        "🗑 — ro'yxatdan o'chirish",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@panel_router.callback_query(F.data.startswith("time:"))
async def change_time_start(callback: CallbackQuery, state: FSMContext):
    tg_id = int(callback.data.split(":")[1])
    employee = get_employee(tg_id)
    if not employee:
        await callback.answer("Bu ishchi topilmadi.", show_alert=True)
        return

    await state.update_data(tg_id=tg_id)
    await state.set_state(ChangeTime.sched_time)
    await callback.message.answer(
        f"<b>{employee[2]} {employee[3]}</b> uchun yangi <b>kelish</b> vaqtini yozing "
        f"(hozirgisi: {employee[4]}).\nMasalan: 09:30",
        reply_markup=CANCEL_KB,
    )
    await callback.answer()


@panel_router.message(ChangeTime.sched_time, F.text)
async def change_time_arrive(message: Message, state: FSMContext):
    sched_time = message.text.strip()
    if not TIME_RE.match(sched_time):
        await message.answer("Vaqt HH:MM formatida bo'lishi kerak, masalan: 09:30. Qaytadan yozing.")
        return

    data = await state.get_data()
    employee = get_employee(data["tg_id"])
    await state.update_data(sched_time=sched_time)
    await state.set_state(ChangeTime.departure)
    await message.answer(
        f"Endi <b>ketish</b> vaqtini yozing "
        f"(hozirgisi: {employee[5] if employee else DEFAULT_DEPARTURE}).\nMasalan: 18:00"
    )


@panel_router.message(ChangeTime.departure, F.text)
async def change_time_save(message: Message, state: FSMContext):
    departure = message.text.strip()
    if not TIME_RE.match(departure):
        await message.answer("Vaqt HH:MM formatida bo'lishi kerak, masalan: 18:00. Qaytadan yozing.")
        return

    data = await state.get_data()
    await state.clear()

    ok = db(
        "UPDATE employees SET scheduled_time = ?, departure_time = ? WHERE telegram_id = ?",
        (data["sched_time"], departure, data["tg_id"]),
    ) > 0
    await message.answer(
        f"✅ Saqlandi.\n🕘 Kelish: {data['sched_time']}\n🕕 Ketish: {departure}"
        if ok else "⚠️ Bunday ishchi topilmadi.",
        reply_markup=menu_kb(message.from_user),
    )


@panel_router.callback_query(F.data.startswith("del:"))
async def delete_confirm(callback: CallbackQuery):
    tg_id = int(callback.data.split(":")[1])
    employee = get_employee(tg_id)
    if not employee:
        await callback.answer("Bu ishchi topilmadi.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Ha, o'chirilsin", callback_data=f"delok:{tg_id}"),
        InlineKeyboardButton(text="❌ Yo'q", callback_data="delno"),
    ]])
    await callback.message.answer(
        f"<b>{employee[2]} {employee[3]}</b> ro'yxatdan o'chirilsinmi?", reply_markup=kb
    )
    await callback.answer()


@panel_router.callback_query(F.data.startswith("delok:"))
async def delete_do(callback: CallbackQuery):
    tg_id = int(callback.data.split(":")[1])
    ok = db("DELETE FROM employees WHERE telegram_id = ?", (tg_id,)) > 0
    await callback.message.edit_text(
        "✅ Ishchi ro'yxatdan o'chirildi." if ok else "⚠️ Bunday ishchi topilmadi."
    )
    await callback.answer()


@panel_router.callback_query(F.data == "delno")
async def delete_cancel(callback: CallbackQuery):
    await callback.message.edit_text("Bekor qilindi.")
    await callback.answer()


# ---------- Haftalik jadval (kunlarni belgilab, vaqt qo'yish) ----------
# Masalan: dushanba/chorshanba/juma — 12:00, seshanba/payshanba/shanba — 13:00.
# Admin avval kunlarni belgilaydi, keyin o'sha kunlarga vaqt kiritadi.

def schedule_text(employee, selected: set[int]) -> str:
    chosen = ", ".join(WEEKDAYS[weekday] for weekday in sorted(selected)) or "hech qaysi"
    return (
        f"🗓 <b>{employee[2]} {employee[3]}</b> — haftalik jadval\n\n"
        f"{format_week_schedule(employee)}\n\n"
        f"<b>Belgilangan kunlar:</b> {chosen}\n\n"
        "Kunlarni bosib belgilang, so'ng pastdagi amallardan birini tanlang."
    )


async def show_schedule_editor(callback: CallbackQuery, state: FSMContext, edit: bool = True):
    data = await state.get_data()
    employee = get_employee(data["tg_id"])
    if not employee:
        await callback.answer("Bu ishchi topilmadi.", show_alert=True)
        return

    selected = set(data.get("days", []))
    text, kb = schedule_text(employee, selected), schedule_kb(selected)
    if edit:
        try:
            await callback.message.edit_text(text, reply_markup=kb)
        except TelegramBadRequest:
            pass  # matn o'zgarmagan bo'lsa Telegram xato qaytaradi
    else:
        await callback.message.answer(text, reply_markup=kb)


@panel_router.callback_query(F.data.startswith("sched:"))
async def schedule_start(callback: CallbackQuery, state: FSMContext):
    tg_id = int(callback.data.split(":")[1])
    if not get_employee(tg_id):
        await callback.answer("Bu ishchi topilmadi.", show_alert=True)
        return

    await state.set_state(EditSchedule.picking)
    await state.update_data(tg_id=tg_id, days=[])
    await show_schedule_editor(callback, state, edit=False)
    await callback.answer()


@panel_router.callback_query(EditSchedule.picking, F.data.startswith("sday:"))
async def schedule_toggle_day(callback: CallbackQuery, state: FSMContext):
    weekday = int(callback.data.split(":")[1])
    data = await state.get_data()
    days = set(data.get("days", []))
    days.symmetric_difference_update({weekday})  # bosilgan kunni yoqadi/o'chiradi
    await state.update_data(days=sorted(days))
    await show_schedule_editor(callback, state)
    await callback.answer()


@panel_router.callback_query(EditSchedule.picking, F.data == "sset")
async def schedule_ask_arrive(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("days"):
        await callback.answer("Avval kamida bitta kunni belgilang.", show_alert=True)
        return

    days_text = ", ".join(WEEKDAYS[weekday] for weekday in data["days"])
    await state.set_state(EditSchedule.arrive)
    await callback.message.answer(
        f"<b>{days_text}</b> kunlari uchun <b>kelish</b> vaqtini yozing.\nMasalan: 12:00",
        reply_markup=CANCEL_KB,
    )
    await callback.answer()


@panel_router.message(EditSchedule.arrive, F.text)
async def schedule_save_arrive(message: Message, state: FSMContext):
    arrive = message.text.strip()
    if not TIME_RE.match(arrive):
        await message.answer("Vaqt HH:MM formatida bo'lishi kerak, masalan: 12:00. Qaytadan yozing.")
        return

    await state.update_data(arrive=arrive)
    await state.set_state(EditSchedule.leave)
    await message.answer(
        "Endi shu kunlar uchun <b>ketish</b> vaqtini yozing.\nMasalan: 18:00\n\n"
        "💡 O'tkazib yuborish uchun <code>-</code> yuboring."
    )


@panel_router.message(EditSchedule.leave, F.text)
async def schedule_save_leave(message: Message, state: FSMContext):
    leave = message.text.strip()
    if leave == "-":
        leave = None
    elif not TIME_RE.match(leave):
        await message.answer("Vaqt HH:MM formatida bo'lishi kerak, masalan: 18:00. Qaytadan yozing.")
        return

    data = await state.get_data()
    await state.clear()

    employee = get_employee(data["tg_id"])
    if not employee:
        await message.answer("⚠️ Bu ishchi topilmadi.", reply_markup=menu_kb(message.from_user))
        return

    for weekday in data["days"]:
        set_day_schedule(employee[0], weekday, data["arrive"], leave)

    days_text = ", ".join(WEEKDAYS[weekday] for weekday in data["days"])
    await message.answer(
        f"✅ <b>{employee[2]} {employee[3]}</b> uchun saqlandi:\n"
        f"📅 {days_text}\n"
        f"🕘 Kelish: {data['arrive']}   🕕 Ketish: {leave or '—'}\n\n"
        f"<b>Yangi jadval:</b>\n{format_week_schedule(get_employee(data['tg_id']))}",
        reply_markup=menu_kb(message.from_user),
    )


@panel_router.callback_query(EditSchedule.picking, F.data == "soff")
async def schedule_set_dayoff(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("days"):
        await callback.answer("Avval kamida bitta kunni belgilang.", show_alert=True)
        return

    employee = get_employee(data["tg_id"])
    if not employee:
        await callback.answer("Bu ishchi topilmadi.", show_alert=True)
        return

    for weekday in data["days"]:
        set_day_schedule(employee[0], weekday, None, None)

    await state.update_data(days=[])
    await show_schedule_editor(callback, state)
    await callback.answer("Dam olish kuni qilib belgilandi.")


@panel_router.callback_query(EditSchedule.picking, F.data == "sclear")
async def schedule_clear(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    employee = get_employee(data["tg_id"])
    if not employee:
        await callback.answer("Bu ishchi topilmadi.", show_alert=True)
        return

    clear_week_schedule(employee[0])
    await state.update_data(days=[])
    await show_schedule_editor(callback, state)
    await callback.answer("Jadval tozalandi — standart vaqt ishlatiladi.")


# ---------- Bonus va jazo berish ----------

async def notify_employee(bot, tg_id: int, text: str) -> bool:
    """Ishchiga shaxsiy xabar yuboradi. U botni bloklagan bo'lsa False."""
    try:
        await bot.send_message(tg_id, text)
        return True
    except TelegramAPIError:
        logger.exception("Ishchiga (%s) xabar yuborib bo'lmadi", tg_id)
        return False


async def save_and_notify_mark(
    message: Message, admin: User, tg_id: int, mark_type: str, reason: str
):
    """Bonus/jazoni bazaga yozadi, ishchini xabardor qiladi, adminga tasdiq beradi.
    `admin` alohida uzatiladi: callback ichidagi xabarning muallifi — botning o'zi."""
    employee = get_employee(tg_id)
    if not employee:
        await message.answer("⚠️ Bu ishchi topilmadi.", reply_markup=menu_kb(admin))
        return

    add_mark(employee[0], mark_type, reason, admin.id)

    if mark_type == "bonus":
        note = (
            "🏅 <b>Sizga bonus berildi!</b>\n"
            f"{LINE}\n"
            f"📝 Sabab: {reason}\n"
            f"📅 Sana: {today()}\n\n"
            "Ajoyib ish, shunday davom eting! 🎉"
        )
    else:
        note = (
            "⚠️ <b>Sizga jazo berildi.</b>\n"
            f"{LINE}\n"
            f"📝 Sabab: {reason}\n"
            f"📅 Sana: {today()}\n\n"
            "Iltimos, bunday holat qaytarilmasligiga e'tibor bering."
        )

    delivered = await notify_employee(message.bot, tg_id, note)
    label = "🏅 Bonus" if mark_type == "bonus" else "⚠️ Jazo"
    await message.answer(
        f"✅ {label} yozib qo'yildi.\n"
        f"👤 {employee[2]} {employee[3]}\n"
        f"📝 Sabab: {reason}\n\n"
        + ("📨 Ishchiga xabar yuborildi."
           if delivered else
           "⚠️ Ishchiga xabar yetkazilmadi (u botni bloklagan yoki /start bosmagan). "
           "Yozuv baribir saqlandi."),
        reply_markup=menu_kb(admin),
    )


@panel_router.message(F.text == "🏅 Bonus berish")
async def bonus_pick_employee(message: Message, state: FSMContext):
    await state.clear()
    kb = employees_pick_kb("bon")
    if kb is None:
        await message.answer("Avval ishchi qo'shing.")
        return
    await message.answer("🏅 Kimga bonus bermoqchisiz?", reply_markup=kb)


@panel_router.callback_query(F.data.startswith("bon:"))
async def bonus_ask_reason(callback: CallbackQuery, state: FSMContext):
    tg_id = int(callback.data.split(":")[1])
    employee = get_employee(tg_id)
    if not employee:
        await callback.answer("Bu ishchi topilmadi.", show_alert=True)
        return

    await state.set_state(GiveBonus.reason)
    await state.update_data(tg_id=tg_id)
    await callback.message.answer(
        f"🏅 <b>{employee[2]} {employee[3]}</b> uchun bonus sababini yozing.\n\n"
        "Masalan: <i>Oylik reja 120% bajarildi</i>",
        reply_markup=CANCEL_KB,
    )
    await callback.answer()


@panel_router.message(GiveBonus.reason, F.text)
async def bonus_save(message: Message, state: FSMContext):
    reason = message.text.strip()
    if len(reason) < 3:
        await message.answer("Sabab juda qisqa. Iltimos, batafsilroq yozing.")
        return

    data = await state.get_data()
    await state.clear()
    await save_and_notify_mark(message, message.from_user, data["tg_id"], "bonus", reason)


@panel_router.message(F.text == "⚠️ Jazo berish")
async def jazo_pick_employee(message: Message, state: FSMContext):
    await state.clear()
    kb = employees_pick_kb("jaz")
    if kb is None:
        await message.answer("Avval ishchi qo'shing.")
        return
    await message.answer("⚠️ Kimga jazo bermoqchisiz?", reply_markup=kb)


@panel_router.callback_query(F.data.startswith("jaz:"))
async def jazo_pick_reason(callback: CallbackQuery, state: FSMContext):
    tg_id = int(callback.data.split(":")[1])
    employee = get_employee(tg_id)
    if not employee:
        await callback.answer("Bu ishchi topilmadi.", show_alert=True)
        return

    await state.update_data(tg_id=tg_id)
    rows = [
        [InlineKeyboardButton(text=reason, callback_data=f"jr:{tg_id}:{index}")]
        for index, reason in enumerate(JAZO_REASONS)
    ]
    rows.append([InlineKeyboardButton(text="✏️ Boshqa sabab", callback_data=f"jr:{tg_id}:x")])
    await callback.message.answer(
        f"⚠️ <b>{employee[2]} {employee[3]}</b> uchun jazo sababini tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@panel_router.callback_query(F.data.startswith("jr:"))
async def jazo_save(callback: CallbackQuery, state: FSMContext):
    _, tg_id_text, choice = callback.data.split(":")
    tg_id = int(tg_id_text)

    if choice == "x":
        await state.set_state(GiveJazo.reason)
        await state.update_data(tg_id=tg_id)
        await callback.message.answer("Jazo sababini yozing:", reply_markup=CANCEL_KB)
        await callback.answer()
        return

    await state.clear()
    await callback.answer()
    await save_and_notify_mark(
        callback.message, callback.from_user, tg_id, "jazo", JAZO_REASONS[int(choice)]
    )


@panel_router.message(GiveJazo.reason, F.text)
async def jazo_save_custom(message: Message, state: FSMContext):
    reason = message.text.strip()
    if len(reason) < 3:
        await message.answer("Sabab juda qisqa. Iltimos, batafsilroq yozing.")
        return

    data = await state.get_data()
    await state.clear()
    await save_and_notify_mark(message, message.from_user, data["tg_id"], "jazo", reason)


# ---------- Ish joyi lokatsiyasi va radius ----------
# Ishchilar "juda uzoqdasiz" degan javob olayotgan bo'lsa, ko'pincha sabab —
# config.py dagi koordinata bino ustiga aniq tushmagan. Admin ish joyi ichida
# turib jonli joylashuv yuborsa, nuqta shu yerga ko'chadi.

CENTER_KB = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Yangi ish joyini belgilash", callback_data="center_set")],
        [InlineKeyboardButton(text="📏 Radiusni o'zgartirish", callback_data="center_radius")],
    ]
)


@panel_router.message(F.text == "📍 Ish joyi lokatsiyasi")
async def center_menu(message: Message):
    lat, lon = get_center()
    is_custom = get_setting("center_lat") is not None
    await message.bot.send_location(message.chat.id, lat, lon)
    await message.answer(
        "📍 <b>Ish joyining hozirgi nuqtasi</b>\n"
        f"Koordinata: <code>{lat}, {lon}</code>\n"
        f"Manba: {'botdan belgilangan' if is_custom else 'config.py (boshlang`ich)'}\n"
        f"Ruxsat etilgan radius: <b>{get_radius()} metr</b>\n"
        f"GPS xatoligiga yon berish: {GPS_ACCURACY_TOLERANCE_METERS} metrgacha\n\n"
        "Yuqoridagi xarita nuqtasi ish joyi binosiga to'g'ri kelmasa — "
        "uni qaytadan belgilang.",
        reply_markup=CENTER_KB,
    )


@panel_router.callback_query(F.data == "center_set")
async def center_set_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SetCenter.location)
    await callback.message.answer(
        "🎯 <b>Ish joyi nuqtasini belgilash</b>\n\n"
        "Ish joyi <b>ichida yoki hovlisida turib</b> jonli joylashuvingizni yuboring:\n"
        "📎 → <b>Location</b> → <b>Share My Live Location</b>\n\n"
        "⚠️ Aniqroq bo'lishi uchun: Wi-Fi yoqilgan bo'lsin va imkon bo'lsa "
        "deraza yonida yoki tashqarida turing.",
        reply_markup=CANCEL_KB,
    )
    await callback.answer()


@panel_router.message(SetCenter.location, F.location)
async def center_set_save(message: Message, state: FSMContext):
    lat, lon = message.location.latitude, message.location.longitude
    accuracy = message.location.horizontal_accuracy or 0

    old_lat, old_lon = get_center()
    moved = distance_meters(lat, lon, old_lat, old_lon)

    set_setting("center_lat", lat)
    set_setting("center_lon", lon)
    await state.clear()

    text = (
        "✅ <b>Ish joyi nuqtasi yangilandi.</b>\n"
        f"Yangi koordinata: <code>{lat}, {lon}</code>\n"
        f"Eski nuqtadan farqi: {int(moved)} metr\n"
        f"Ruxsat etilgan radius: {get_radius()} metr"
    )
    if accuracy:
        text += f"\n📡 Yuborilgan joylashuv aniqligi: ±{int(accuracy)} metr"
        if accuracy > 50:
            text += (
                "\n\n⚠️ Aniqlik pastroq. Nuqta biroz siljigan bo'lishi mumkin — "
                "tashqariga chiqib qayta yuborsangiz aniqroq bo'ladi."
            )
    await message.answer(text, reply_markup=menu_kb(message.from_user))


@panel_router.message(SetCenter.location)
async def center_set_wrong_input(message: Message):
    await message.answer(
        "Bu joylashuv emas. Iltimos, 📎 → <b>Location</b> orqali joylashuvingizni "
        "yuboring yoki \"❌ Bekor qilish\" tugmasini bosing.",
        reply_markup=CANCEL_KB,
    )


@panel_router.callback_query(F.data == "center_radius")
async def center_radius_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SetCenter.radius)
    await callback.message.answer(
        "📏 <b>Radiusni o'zgartirish</b>\n\n"
        f"Hozirgi radius: <b>{get_radius()} metr</b>\n"
        "Yangi qiymatni faqat son bilan yozing (masalan: <code>100</code>).\n\n"
        "💡 Tavsiya: 80-150 metr. Juda kichik radius (50 dan past) bino ichidagi "
        "GPS xatoligi tufayli ishchilarni noto'g'ri rad etadi.",
        reply_markup=CANCEL_KB,
    )
    await callback.answer()


@panel_router.message(SetCenter.radius, F.text)
async def center_radius_save(message: Message, state: FSMContext):
    value = message.text.strip()
    if not value.isdigit() or not 10 <= int(value) <= 5000:
        await message.answer(
            "Noto'g'ri qiymat. 10 dan 5000 gacha bo'lgan sonni yuboring (metrlarda)."
        )
        return

    set_setting("radius_meters", int(value))
    await state.clear()
    await message.answer(
        f"✅ Radius <b>{value} metr</b> qilib belgilandi.",
        reply_markup=menu_kb(message.from_user),
    )


# ==================== 7. PDF HISOBOT ====================
# Hisobotda uchta jadval bo'ladi: umumiy yakun, kunlik davomat,
# hamda bonus/jazolar — har birining sababi bilan.

# Unicode shrift topilsa o'shani ishlatamiz (kirill harflar ham chiqadi),
# topilmasa PDF'ning o'zida bor Helvetica ishlatiladi.
FONT_CANDIDATES = [
    ("DejaVu",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("Arial",
     r"C:\Windows\Fonts\arial.ttf",
     r"C:\Windows\Fonts\arialbd.ttf"),
]

# Helvetica faqat latin-1 belgilarni biladi — tipografik belgilarni oddiysiga almashtiramiz
ASCII_MAP = str.maketrans({
    "ʻ": "'", "ʼ": "'", "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...",
})


def setup_pdf_font(pdf: FPDF) -> tuple[str, bool]:
    """(shrift nomi, unicode_mi) qaytaradi."""
    for name, regular, bold in FONT_CANDIDATES:
        if os.path.exists(regular) and os.path.exists(bold):
            pdf.add_font(name, "", regular)
            pdf.add_font(name, "B", bold)
            return name, True
    return "Helvetica", False


def pdf_text(value, unicode_font: bool) -> str:
    """Matnni PDF shrifti qabul qiladigan ko'rinishga keltiradi."""
    text = str(value).translate(ASCII_MAP)
    if unicode_font:
        return text
    return text.encode("latin-1", "replace").decode("latin-1")


def build_report_pdf(records, marks, date_from, date_to) -> bytes:
    """Davomat va bonus/jazolardan PDF yasaydi."""
    pdf = FPDF(orientation="P", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    font, unicode_font = setup_pdf_font(pdf)
    pdf.add_page()

    def safe(value):
        return pdf_text(value, unicode_font)

    def table(headers, rows, widths, aligns):
        with pdf.table(
            col_widths=widths,
            text_align=aligns,
            headings_style=FontFace(emphasis="BOLD", color=(255, 255, 255),
                                    fill_color=(68, 114, 196)),
            line_height=6,
            padding=1.5,
        ) as tbl:
            head = tbl.row()
            for header in headers:
                head.cell(safe(header))
            for data_row in rows:
                row = tbl.row()
                for value in data_row:
                    row.cell(safe(value))

    # ---- Sarlavha ----
    pdf.set_font(font, "B", 16)
    pdf.cell(0, 10, safe("Davomat hisoboti"),
             new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.set_font(font, "", 10)
    pdf.cell(0, 6, safe(f"Davr: {date_from.isoformat()} - {date_to.isoformat()}"),
             new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.cell(0, 6, safe(f"Tayyorlandi: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}"),
             new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.ln(4)

    # ---- 1-jadval: har bir ishchi bo'yicha yakun ----
    def new_item():
        return {"days": 0, "late": 0, "minutes": 0, "fine": 0,
                "bonus": 0, "jazo": 0, "ogohlantirish": 0}

    summary: dict[str, dict] = {}
    for first, last, _date, _arrived, _sched, is_late, late_min, _early, fine, _left in records:
        item = summary.setdefault(f"{first} {last}", new_item())
        item["days"] += 1
        item["fine"] += fine
        if is_late:
            item["late"] += 1
            item["minutes"] += late_min
    for first, last, mark_type, _reason, _date in marks:
        item = summary.setdefault(f"{first} {last}", new_item())
        item[mark_type] += 1
    total_fine_all = sum(item["fine"] for item in summary.values())

    pdf.set_font(font, "B", 12)
    pdf.cell(0, 8, safe("1. Umumiy yakun"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font(font, "", 9)
    table(
        ["Ishchi", "Kelgan", "Kech kun", "Jami jarima", "Bonus", "Jazo", "Ogoh."],
        [
            [name, item["days"], item["late"],
             format_money(item["fine"]) if item["fine"] else "-",
             item["bonus"], item["jazo"], item["ogohlantirish"]]
            for name, item in sorted(summary.items())
        ],
        widths=(50, 20, 22, 42, 16, 16, 20),
        aligns=("LEFT", "CENTER", "CENTER", "RIGHT", "CENTER", "CENTER", "CENTER"),
    )
    pdf.ln(2)
    pdf.set_font(font, "B", 10)
    pdf.cell(0, 7, safe(f"Barcha ishchilar bo'yicha jami jarima: {format_money(total_fine_all)}"),
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    # ---- 2-jadval: kunlik davomat ----
    pdf.set_font(font, "B", 12)
    pdf.cell(0, 8, safe("2. Kunlik davomat"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font(font, "", 8)
    rates = get_fine_rates()
    pdf.cell(0, 5, safe(
        f"Hozirgi tarif — erta kelish oynasi: har daqiqa {format_money(rates['early_rate'])}  |  "
        f"belgilangan vaqtdan keyin: har daqiqa {format_money(rates['late_rate'])} "
        f"(eng ko'pi {rates['late_cap']} daqiqagacha, undan ortig'iga ogohlantirish)"),
        new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font(font, "", 9)
    if records:
        table(
            ["Ishchi", "Sana", "Kelgan", "Ketgan", "Belg.", "Kech daq.", "Jarima"],
            [
                [f"{first} {last}", date, arrived, left or "-", sched or "-",
                 str(late_min) if late_min else "-",
                 format_money(fine) if fine else "-"]
                for first, last, date, arrived, sched, _is_late, late_min, _early, fine, left
                in records
            ],
            widths=(40, 22, 22, 22, 16, 20, 34),
            aligns=("LEFT", "CENTER", "CENTER", "CENTER", "CENTER", "CENTER", "RIGHT"),
        )
    else:
        pdf.cell(0, 6, safe("Bu davrda davomat yozuvi yo'q."),
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(6)

    # ---- 3-jadval: bonus, jazo va ogohlantirishlar, sabablari bilan ----
    type_label = {"bonus": "BONUS", "jazo": "JAZO", "ogohlantirish": "OGOHLANTIRISH"}
    pdf.set_font(font, "B", 12)
    pdf.cell(0, 8, safe("3. Bonus, jazo va ogohlantirishlar"),
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font(font, "", 9)
    if marks:
        table(
            ["Ishchi", "Sana", "Turi", "Sababi"],
            [
                [f"{first} {last}", date, type_label.get(mark_type, mark_type.upper()), reason]
                for first, last, mark_type, reason, date in marks
            ],
            widths=(40, 22, 30, 88),
            aligns=("LEFT", "CENTER", "CENTER", "LEFT"),
        )
    else:
        pdf.cell(0, 6, safe("Bu davrda bonus, jazo yoki ogohlantirish berilmagan."),
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    return bytes(pdf.output())


async def send_pdf_report(message: Message, date_from, date_to) -> None:
    """Berilgan davr uchun PDF hisobot tayyorlab yuboradi."""
    records = db(
        """
        SELECT t.first_name, t.last_name, a.attendance_date, a.arrived_time,
               COALESCE(a.scheduled_time, t.scheduled_time), a.is_late, a.late_minutes,
               a.early_minutes, a.fine_amount, a.left_time
        FROM attendance a
        JOIN employees t ON t.id = a.employee_id
        WHERE a.attendance_date BETWEEN ? AND ?
        ORDER BY a.attendance_date, t.first_name, t.last_name
        """,
        (date_from.isoformat(), date_to.isoformat()), fetch="all",
    )
    marks = db(
        """
        SELECT t.first_name, t.last_name, m.mark_type, m.reason, m.mark_date
        FROM marks m
        JOIN employees t ON t.id = m.employee_id
        WHERE m.mark_date BETWEEN ? AND ?
        ORDER BY m.mark_date, t.first_name, t.last_name
        """,
        (date_from.isoformat(), date_to.isoformat()), fetch="all",
    )

    if not records and not marks:
        await message.answer(
            f"{date_from.isoformat()} — {date_to.isoformat()} oralig'ida "
            "davomat, bonus yoki jazo yozuvi topilmadi."
        )
        return

    pdf_bytes = build_report_pdf(records, marks, date_from, date_to)
    bonus_count = sum(1 for _, _, mark_type, _, _ in marks if mark_type == "bonus")
    jazo_count = sum(1 for _, _, mark_type, _, _ in marks if mark_type == "jazo")
    ogoh_count = sum(1 for _, _, mark_type, _, _ in marks if mark_type == "ogohlantirish")
    total_fine = sum(row[8] for row in records)

    caption = (
        f"📄 Davomat hisoboti\n"
        f"Davr: {date_from.isoformat()} — {date_to.isoformat()}\n"
        f"📊 Davomat yozuvlari: {len(records)}\n"
        f"💰 Jami jarima: {format_money(total_fine)}\n"
        f"🏅 Bonuslar: {bonus_count}   ⚠️ Jazolar: {jazo_count}   🔔 Ogohlantirishlar: {ogoh_count}"
    )
    await message.answer_document(
        BufferedInputFile(
            pdf_bytes,
            filename=f"davomat_{date_from.isoformat()}_{date_to.isoformat()}.pdf",
        ),
        caption=caption,
    )


@panel_router.message(F.text == "📄 PDF hisobot")
async def report_menu(message: Message):
    await message.answer("Qaysi davr uchun hisobot kerak?", reply_markup=REPORT_KB)


@panel_router.callback_query(F.data.in_({"rep_this", "rep_prev"}))
async def report_quick(callback: CallbackQuery):
    today_date = datetime.now(TZ).date()
    if callback.data == "rep_this":
        date_from, date_to = today_date.replace(day=1), today_date
    else:  # o'tgan oy
        date_to = today_date.replace(day=1) - timedelta(days=1)  # o'tgan oyning oxiri
        date_from = date_to.replace(day=1)

    await callback.answer()
    await send_pdf_report(callback.message, date_from, date_to)


@panel_router.callback_query(F.data == "rep_custom")
async def report_custom_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CustomReport.dates)
    await callback.message.answer(
        "Boshlanish va tugash sanalarini bitta xabarda yuboring.\n"
        "Namuna: <code>2026-07-01 2026-07-16</code>",
        reply_markup=CANCEL_KB,
    )
    await callback.answer()


@panel_router.message(CustomReport.dates, F.text)
async def report_custom_dates(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) != 2 or not (DATE_RE.match(parts[0]) and DATE_RE.match(parts[1])):
        await message.answer(
            "Ikkita sanani YYYY-MM-DD formatida yuboring.\n"
            "Namuna: <code>2026-07-01 2026-07-16</code>"
        )
        return

    try:
        date_from = datetime.strptime(parts[0], "%Y-%m-%d").date()
        date_to = datetime.strptime(parts[1], "%Y-%m-%d").date()
    except ValueError:
        await message.answer("Sana formati noto'g'ri. Namuna: 2026-07-01")
        return

    if date_from > date_to:
        await message.answer("Boshlanish sanasi tugash sanasidan katta bo'lishi mumkin emas.")
        return

    await state.clear()
    await message.answer("⏳ Hisobot tayyorlanmoqda...", reply_markup=menu_kb(message.from_user))
    await send_pdf_report(message, date_from, date_to)


# ==================== JARIMA SOZLAMALARI (admin) ====================
# Kechikish uchun jarima summalarini admin botdan istalgan vaqtda o'zgartiradi.
# O'zgarish keyingi "Keldim"lardan boshlab kuchga kiradi.

class SetFine(StatesGroup):
    value = State()  # tanlangan sozlamaning yangi qiymati


def format_fine_value(name: str, value: int) -> str:
    unit = FINE_SETTINGS[name]["unit"]
    return format_money(value) if unit == "so'm" else f"{value} {unit}"


def spaced(number: int) -> str:
    """1000000 -> '1 000 000'."""
    return f"{number:,}".replace(",", " ")


def parse_number(text: str) -> int | None:
    """'7000', '7 000', '7,000' -> 7000. Butun son bo'lmasa None."""
    cleaned = re.sub(r"[\s,']", "", text)
    return int(cleaned) if cleaned.isdigit() else None


def fines_text() -> str:
    rates = get_fine_rates()
    lines = [
        f"{spec['icon']} {spec['title']}: <b>{format_fine_value(name, rates[name])}</b>"
        for name, spec in FINE_SETTINGS.items()
    ]

    # Jonli misol: o'zgarish natijasi darrov ko'rinsin
    scheduled = datetime(2000, 1, 1, 9, 0)
    deadline = scheduled - timedelta(minutes=rates["early_required"])
    _early, _late, example_fine, _excessive = compute_fine(
        scheduled + timedelta(minutes=3), scheduled, rates
    )
    return (
        "💰 <b>Jarima sozlamalari</b>\n"
        f"{LINE}\n"
        + "\n".join(lines)
        + "\n\n📌 <b>Misol:</b> ish 09:00 da boshlansa, ishchi "
        f"{deadline:%H:%M} gacha kelishi kerak. 09:03 da kelsa jarima: "
        f"<b>{format_money(example_fine)}</b>\n\n"
        "✏️ O'zgartirmoqchi bo'lgan sozlamani bosing 👇\n"
        "ℹ️ Yangi qiymat faqat keyingi \"Keldim\"larga ta'sir qiladi — "
        "oldingi yozuvlar o'zgarmaydi."
    )


def fines_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{spec['icon']} {spec['title']}", callback_data=f"fine:{name}"
        )]
        for name, spec in FINE_SETTINGS.items()
    ])


@panel_router.message(F.text == BTN_FINES)
async def fines_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(fines_text(), reply_markup=fines_kb())


@panel_router.callback_query(F.data.startswith("fine:"))
async def fine_edit_start(callback: CallbackQuery, state: FSMContext):
    name = callback.data.split(":", 1)[1]
    spec = FINE_SETTINGS.get(name)
    if spec is None:
        await callback.answer("Bunday sozlama topilmadi.", show_alert=True)
        return

    current = format_fine_value(name, get_fine_rates()[name])
    await state.set_state(SetFine.value)
    await state.update_data(fine_name=name)
    await callback.message.answer(
        f"{spec['icon']} <b>{spec['title']}</b>\n"
        f"{LINE}\n"
        f"{spec['ask']}\n\n"
        f"Hozirgi qiymat: <b>{current}</b>\n"
        f"Yangisini faqat son bilan yozing (masalan: <code>{spec['example']}</code>).\n"
        f"📏 Oraliq: {spec['min']} dan {spaced(spec['max'])} gacha",
        reply_markup=CANCEL_KB,
    )
    await callback.answer()


@panel_router.message(SetFine.value, F.text)
async def fine_edit_save(message: Message, state: FSMContext):
    data = await state.get_data()
    name = data.get("fine_name")
    spec = FINE_SETTINGS.get(name)
    if spec is None:
        await state.clear()
        await message.answer("⚠️ Xatolik: qaytadan urinib ko'ring.", reply_markup=menu_kb(message.from_user))
        return

    value = parse_number(message.text)
    if value is None or not spec["min"] <= value <= spec["max"]:
        await message.answer(
            f"🙈 Noto'g'ri qiymat. {spec['min']} dan {spaced(spec['max'])} gacha bo'lgan "
            f"butun son yuboring (masalan: <code>{spec['example']}</code>).\n"
            "Yoki \"❌ Bekor qilish\" ni bosing."
        )
        return

    set_setting(spec["key"], value)
    await state.clear()
    await message.answer(
        f"✅ <b>Saqlandi!</b>\n{spec['icon']} {spec['title']}: "
        f"<b>{format_fine_value(name, value)}</b>",
        reply_markup=menu_kb(message.from_user),
    )
    await message.answer(fines_text(), reply_markup=fines_kb())


# ==================== 8. ADMIN BUYRUQLARI (eski, ixtiyoriy) ====================
# Tugmalar o'rniga matnli buyruqlarni yoqtirganlar uchun saqlab qolingan.

@admin_router.message(Command("add_employee"))
async def cmd_add_employee(message: Message):
    if not is_admin(message.from_user):
        return

    # Format: /add_employee <telegram_id> <Ism> <Familiya> <HH:MM>
    parts = message.text.split(maxsplit=4)
    if len(parts) != 5:
        await message.answer(
            "Foydalanish: /add_employee <telegram_id> <Ism> <Familiya> <HH:MM>\n"
            "Masalan: /add_employee 123456789 Ali Valiyev 09:00\n\n"
            "💡 Osonroq yo'l: \"➕ Ishchi qo'shish\" tugmasini bosing."
        )
        return

    _, tg_id, first_name, last_name, sched_time = parts
    if not tg_id.isdigit():
        await message.answer("telegram_id butun son bo'lishi kerak.")
        return
    if not TIME_RE.match(sched_time):
        await message.answer("Vaqt HH:MM formatida bo'lishi kerak, masalan: 09:00")
        return

    if add_employee(int(tg_id), first_name, last_name, sched_time):
        await message.answer(
            f"✅ Qo'shildi: {first_name} {last_name} (belgilangan vaqt: {sched_time})"
        )
    else:
        await message.answer("⚠️ Bu telegram_id bilan ishchi allaqachon mavjud.")


@admin_router.message(Command("remove_employee"))
async def cmd_remove_employee(message: Message):
    if not is_admin(message.from_user):
        return

    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Foydalanish: /remove_employee <telegram_id>")
        return

    ok = db("DELETE FROM employees WHERE telegram_id = ?", (int(parts[1]),)) > 0
    await message.answer("✅ O'chirildi." if ok else "⚠️ Bunday ishchi topilmadi.")


@admin_router.message(Command("set_time"))
async def cmd_set_time(message: Message):
    if not is_admin(message.from_user):
        return

    parts = message.text.split()
    if len(parts) != 3 or not parts[1].isdigit() or not TIME_RE.match(parts[2]):
        await message.answer(
            "Foydalanish: /set_time <telegram_id> <HH:MM>\n"
            "Masalan: /set_time 123456789 09:30"
        )
        return

    ok = db(
        "UPDATE employees SET scheduled_time = ? WHERE telegram_id = ?",
        (parts[2], int(parts[1])),
    ) > 0
    await message.answer(f"✅ Yangilandi: {parts[2]}" if ok else "⚠️ Bunday ishchi topilmadi.")


@admin_router.message(Command("list_employees"))
async def cmd_list_employees(message: Message):
    if not is_admin(message.from_user):
        return

    employees = db(
        "SELECT first_name, last_name, scheduled_time, departure_time, telegram_id "
        "FROM employees", fetch="all",
    )
    if not employees:
        await message.answer("Hozircha ishchilar ro'yxati bo'sh.")
        return

    lines = ["📋 Ishchilar ro'yxati:\n"] + [
        f"• {first} {last} — {sched} dan {departure} gacha (ID: {tg_id})"
        for first, last, sched, departure, tg_id in employees
    ]
    await message.answer("\n".join(lines))


@admin_router.message(Command("pdf_hisobot"))
async def cmd_pdf_report(message: Message):
    if not is_admin(message.from_user):
        return

    parts = message.text.split()
    if len(parts) == 1:
        # Argument berilmasa — shu oy uchun hisobot
        today_date = datetime.now(TZ).date()
        date_from, date_to = today_date.replace(day=1), today_date
    elif len(parts) == 3 and DATE_RE.match(parts[1]) and DATE_RE.match(parts[2]):
        try:
            date_from = datetime.strptime(parts[1], "%Y-%m-%d").date()
            date_to = datetime.strptime(parts[2], "%Y-%m-%d").date()
        except ValueError:
            await message.answer("Sana formati noto'g'ri. Namuna: 2026-07-01")
            return
    else:
        await message.answer(
            "Foydalanish:\n"
            "/pdf_hisobot — shu oy uchun hisobot\n"
            "/pdf_hisobot 2026-07-01 2026-07-16 — belgilangan sanalar oralig'i uchun\n\n"
            "💡 Osonroq yo'l: \"📄 PDF hisobot\" tugmasini bosing."
        )
        return

    if date_from > date_to:
        await message.answer("Boshlanish sanasi tugash sanasidan katta bo'lishi mumkin emas.")
        return

    await send_pdf_report(message, date_from, date_to)


# ==================== 9. ISHGA TUSHIRISH ====================

async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_routers(admin_router, panel_router, employee_router)

    await bot.delete_webhook(drop_pending_updates=True)

    # Botning chiroyli ko'rinishi: buyruq menyusi va tavsif (xato bo'lsa bot to'xtamaydi)
    try:
        await bot.set_my_commands([BotCommand(command="start", description="🏠 Bosh menyu")])
        await bot.set_my_short_description(
            "📍 Ishchilar davomat boti — keldim va ketyapman, jonli joylashuv bilan."
        )
        await bot.set_my_description(
            "👋 Salom! Men ishchilar davomat botiman.\n\n"
            "🟢 Ishga kelganda — «Keldim»\n"
            "🏠 Ketayotganda — «Ketyapman»\n"
            "📍 Ikkalasida ham jonli joylashuv yuborasiz.\n\n"
            "Boshlash uchun /start ni bosing."
        )
    except TelegramAPIError:
        logger.warning("Bot tavsifini o'rnatib bo'lmadi", exc_info=True)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())