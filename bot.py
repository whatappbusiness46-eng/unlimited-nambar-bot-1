import os
import sqlite3
import logging
from datetime import datetime

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@yourusername")

DB_FILE = "bot.db"

REF_BONUS = 0.50
MIN_WITHDRAW = 5.00
WITHDRAW_FEE = 0.50

# Demo services only.
# These are NOT connected to WhatsApp/Telegram/TikTok verification.
SERVICES = {
    "Myanmar": {
        "HSBC": 1.50,
        "TikTok": 1.20,
    },
    "USA": {
        "Demo SMS": 1.00,
    },
    "UK": {
        "Demo SMS": 1.00,
    },
}

DEMO_NUMBERS = [
    "+959660432713",
    "+959697711320",
    "+12025550101",
    "+447700900123",
]

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# DATABASE
# ============================================================

db = sqlite3.connect(DB_FILE, check_same_thread=False)
db.row_factory = sqlite3.Row


def db_init():
    cur = db.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            username TEXT,
            balance REAL DEFAULT 0,
            total_deposit REAL DEFAULT 0,
            total_spent REAL DEFAULT 0,
            total_withdraw REAL DEFAULT 0,
            referrals INTEGER DEFAULT 0,
            referred_by INTEGER DEFAULT NULL,
            banned INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            country TEXT,
            service TEXT,
            number TEXT,
            price REAL,
            status TEXT,
            otp TEXT DEFAULT NULL,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            method TEXT,
            account TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    db.commit()


def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def add_user(user_id, name, username="", referred_by=None):
    cur = db.cursor()

    existing = cur.execute(
        "SELECT user_id FROM users WHERE user_id=?",
        (user_id,),
    ).fetchone()

    if existing:
        cur.execute(
            "UPDATE users SET name=?, username=? WHERE user_id=?",
            (name, username, user_id),
        )
    else:
        cur.execute(
            """
            INSERT INTO users
            (user_id, name, username, referred_by, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                name,
                username,
                referred_by,
                now(),
            ),
        )

        if referred_by and referred_by != user_id:
            ref = cur.execute(
                "SELECT user_id FROM users WHERE user_id=?",
                (referred_by,),
            ).fetchone()

            if ref:
                cur.execute(
                    """
                    UPDATE users
                    SET referrals=referrals+1,
                        balance=balance+?
                    WHERE user_id=?
                    """,
                    (REF_BONUS, referred_by),
                )

    db.commit()


def get_user(user_id):
    return db.execute(
        "SELECT * FROM users WHERE user_id=?",
        (user_id,),
    ).fetchone()


def balance(user_id):
    row = get_user(user_id)
    return float(row["balance"]) if row else 0.0


def change_balance(user_id, amount):
    db.execute(
        "UPDATE users SET balance=balance+? WHERE user_id=?",
        (amount, user_id),
    )
    db.commit()


def create_order(user_id, country, service, number, price):
    cur = db.cursor()

    cur.execute(
        """
        INSERT INTO orders
        (user_id, country, service, number, price, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            country,
            service,
            number,
            price,
            "active",
            now(),
        ),
    )

    order_id = cur.lastrowid

    cur.execute(
        """
        UPDATE users
        SET balance=balance-?,
            total_spent=total_spent+?
        WHERE user_id=?
        """,
        (price, price, user_id),
    )

    db.commit()

    return order_id


def get_order(order_id):
    return db.execute(
        "SELECT * FROM orders WHERE id=?",
        (order_id,),
    ).fetchone()


def get_active_orders(user_id):
    return db.execute(
        """
        SELECT * FROM orders
        WHERE user_id=? AND status='active'
        ORDER BY id DESC
        """,
        (user_id,),
    ).fetchall()


def get_user_orders(user_id):
    return db.execute(
        """
        SELECT * FROM orders
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 20
        """,
        (user_id,),
    ).fetchall()


def create_withdraw(user_id, amount, method, account):
    cur = db.cursor()

    cur.execute(
        """
        INSERT INTO withdrawals
        (user_id, amount, method, account, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            amount,
            method,
            account,
            "pending",
            now(),
        ),
    )

    withdraw_id = cur.lastrowid

    cur.execute(
        "UPDATE users SET balance=balance-? WHERE user_id=?",
        (amount, user_id),
    )

    db.commit()

    return withdraw_id


# ============================================================
# KEYBOARDS
# ============================================================

def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["📱 Get Number", "📊 Status"],
            ["📋 Active Number", "💰 Wallet"],
            ["🔗 Refer", "👤 Profile"],
            ["💸 Withdraw", "🆘 Support"],
        ],
        resize_keyboard=True,
    )


def admin_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["👥 Users", "📊 Statistics"],
            ["➕ Add Balance", "➖ Remove Balance"],
            ["📦 Services", "📱 Demo Inventory"],
            ["💸 Withdrawals", "📢 Broadcast"],
            ["🚫 Ban User", "✅ Unban User"],
            ["🏠 Main Menu"],
        ],
        resize_keyboard=True,
    )


def country_keyboard():
    buttons = []

    for country in SERVICES:
        buttons.append(
            [InlineKeyboardButton(
                f"🌍 {country}",
                callback_data=f"country:{country}"
            )]
        )

    buttons.append(
        [InlineKeyboardButton("🔙 Back", callback_data="back:main")]
    )

    return InlineKeyboardMarkup(buttons)


def service_keyboard(country):
    buttons = []

    for service, price in SERVICES[country].items():
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{service} — ${price:.2f}",
                    callback_data=f"service:{country}:{service}",
                )
            ]
        )

    buttons.append(
        [InlineKeyboardButton("🔙 Back", callback_data="getnumber")]
    )

    return InlineKeyboardMarkup(buttons)


# ============================================================
# HELPERS
# ============================================================

def banned(user_id):
    row = get_user(user_id)
    return bool(row and row["banned"])


async def ensure_user(update: Update):
    user = update.effective_user

    if not get_user(user.id):
        add_user(
            user.id,
            user.first_name or "User",
            user.username or "",
        )


async def safe_send(context, chat_id, text):
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
        )
        return True
    except Exception:
        return False


# ============================================================
# START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    referred_by = None

    if context.args:
        try:
            referred_by = int(context.args[0])
        except Exception:
            referred_by = None

    add_user(
        user.id,
        user.first_name or "User",
        user.username or "",
        referred_by,
    )

    if banned(user.id):
        await update.message.reply_text(
            "🚫 Your account is banned."
        )
        return

    await update.message.reply_text(
        f"""
👋 Welcome {user.first_name}!

🤖 Virtual Number Marketplace

📱 Get Number
📊 Status
📋 Active Number
💰 Wallet
🔗 Referral
💸 Withdraw

Choose an option below 👇
""",
        reply_markup=main_keyboard(),
    )


# ============================================================
# GET NUMBER
# ============================================================

async def get_number(update, context):
    await update.message.reply_text(
        """
📱 GET NUMBER

🌍 Select Country:
""",
        reply_markup=country_keyboard(),
    )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if banned(user_id):
        await query.edit_message_text(
            "🚫 Your account is banned."
        )
        return

    data = query.data

    # -------------------------
    # MAIN
    # -------------------------

    if data == "back:main":
        await query.message.reply_text(
            "🏠 Main Menu",
            reply_markup=main_keyboard(),
        )
        return

    # -------------------------
    # COUNTRY
    # -------------------------

    if data.startswith("country:"):
        country = data.split(":", 1)[1]

        await query.edit_message_text(
            f"""
🌍 Country: {country}

🏦 Select Service:
""",
            reply_markup=service_keyboard(country),
        )
        return

    # -------------------------
    # SERVICE
    # -------------------------

    if data.startswith("service:"):
        parts = data.split(":", 2)

        country = parts[1]
        service = parts[2]

        price = SERVICES[country][service]

        if balance(user_id) < price:
            await query.edit_message_text(
                f"""
❌ Insufficient Balance

💰 Required: ${price:.2f}
💳 Your Balance: ${balance(user_id):.2f}

Please add balance first.
"""
            )
            return

        # Demo inventory
        number = DEMO_NUMBERS[
            user_id % len(DEMO_NUMBERS)
        ]

        order_id = create_order(
            user_id,
            country,
            service,
            number,
            price,
        )

        await query.edit_message_text(
            f"""
✅ NUMBER ACTIVATED

🆔 Order ID: #{order_id}

🌍 Country: {country}
🏦 Service: {service}

📱 Number:
{number}

💰 Price: ${price:.2f}

⏳ Status: Waiting

⚠️ DEMO MODE

This number is test/demo inventory and is
NOT connected to real WhatsApp/TikTok/
Telegram verification services.
""",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔐 Demo OTP",
                            callback_data=f"demootp:{order_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🔄 Change",
                            callback_data=f"change:{order_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "❌ Cancel",
                            callback_data=f"cancel:{order_id}",
                        )
                    ],
                ]
            ),
        )

        return

    # -------------------------
    # DEMO OTP
    # -------------------------

    if data.startswith("demootp:"):
        order_id = int(data.split(":")[1])

        order = get_order(order_id)

        if not order or order["user_id"] != user_id:
            await query.answer(
                "Order not found.",
                show_alert=True,
            )
            return

        if order["status"] != "active":
            await query.answer(
                "Order is no longer active.",
                show_alert=True,
            )
            return

        demo_code = "123456"

        db.execute(
            """
            UPDATE orders
            SET otp=?, status='completed'
            WHERE id=?
            """,
            (demo_code, order_id),
        )

        db.commit()

        await query.edit_message_text(
            f"""
✅ DEMO OTP RECEIVED

🆔 Order: #{order_id}

📱 Number:
{order["number"]}

🔐 Demo OTP:
{demo_code}

📊 Status: Completed

⚠️ This is a test OTP only.
"""
        )

        return

    # -------------------------
    # CANCEL
    # -------------------------

    if data.startswith("cancel:"):
        order_id = int(data.split(":")[1])

        order = get_order(order_id)

        if not order or order["user_id"] != user_id:
            return

        if order["status"] != "active":
            await query.answer(
                "Order already closed.",
                show_alert=True,
            )
            return

        db.execute(
            """
            UPDATE orders
            SET status='cancelled'
            WHERE id=?
            """,
            (order_id,),
        )

        change_balance(
            user_id,
            float(order["price"]),
        )

        db.commit()

        await query.edit_message_text(
            f"""
❌ Order Cancelled

🆔 Order: #{order_id}

💰 Refunded:
${order["price"]:.2f}
"""
        )

        return

    # -------------------------
    # CHANGE
    # -------------------------

    if data.startswith("change:"):
        order_id = int(data.split(":")[1])

        order = get_order(order_id)

        if not order or order["user_id"] != user_id:
            return

        if order["status"] != "active":
            await query.answer(
                "Order is no longer active.",
                show_alert=True,
            )
            return

        number = DEMO_NUMBERS[
            (user_id + order_id + 1)
            % len(DEMO_NUMBERS)
        ]

        db.execute(
            """
            UPDATE orders
            SET number=?
            WHERE id=?
            """,
            (number, order_id),
        )

        db.commit()

        await query.edit_message_text(
            f"""
🔄 NUMBER CHANGED

🆔 Order: #{order_id}

📱 New Number:
{number}

⏳ Status: Waiting
"""
        )

        return


# ============================================================
# WALLET
# ============================================================

async def wallet(update, context):
    user_id = update.effective_user.id
    bal = balance(user_id)

    await update.message.reply_text(
        f"""
💰 WALLET

💵 Balance: ${bal:.2f}

💳 Total Spent:
${get_user(user_id)["total_spent"]:.2f}

💸 Total Withdrawn:
${get_user(user_id)["total_withdraw"]:.2f}

Use Withdraw to request payout.
"""
    )


# ============================================================
# PROFILE
# ============================================================

async def profile(update, context):
    user_id = update.effective_user.id
    user = get_user(user_id)

    await update.message.reply_text(
        f"""
👤 PROFILE

🆔 ID: {user_id}

👤 Name:
{user["name"]}

💰 Balance:
${user["balance"]:.2f}

🔗 Referrals:
{user["referrals"]}

💵 Total Spent:
${user["total_spent"]:.2f}

💸 Total Withdrawn:
${user["total_withdraw"]:.2f}
"""
    )


# ============================================================
# STATUS
# ============================================================

async def status(update, context):
    orders = get_active_orders(
        update.effective_user.id
    )

    await update.message.reply_text(
        f"""
📊 BOT STATUS

🤖 Bot: Online
📱 Active Orders: {len(orders)}
💾 Database: Online

✅ System operational.
"""
    )


# ============================================================
# ACTIVE NUMBERS
# ============================================================

async def active_numbers(update, context):
    orders = get_active_orders(
        update.effective_user.id
    )

    if not orders:
        await update.message.reply_text(
            "📋 No Active Numbers."
        )
        return

    text = "📋 ACTIVE NUMBERS\n\n"

    for order in orders:
        text += (
            f"🆔 #{order['id']}\n"
            f"🌍 {order['country']}\n"
            f"🏦 {order['service']}\n"
            f"📱 {order['number']}\n"
            f"📊 {order['status']}\n\n"
        )

    await update.message.reply_text(text)


# ============================================================
# ORDERS
# ============================================================

async def orders(update, context):
    rows = get_user_orders(
        update.effective_user.id
    )

    if not rows:
        await update.message.reply_text(
            "📭 No orders found."
        )
        return

    text = "📋 YOUR ORDERS\n\n"

    for row in rows:
        text += (
            f"#{row['id']} — "
            f"{row['country']} / {row['service']}\n"
            f"📱 {row['number']}\n"
            f"💰 ${row['price']:.2f}\n"
            f"📊 {row['status']}\n\n"
        )

    await update.message.reply_text(text)


# ============================================================
# REFERRAL
# ============================================================

async def refer(update, context):
    user_id = update.effective_user.id

    me = await context.bot.get_me()

    link = (
        f"https://t.me/{me.username}"
        f"?start={user_id}"
    )

    await update.message.reply_text(
        f"""
🔗 REFER & EARN

Invite your friends and earn:

💰 ${REF_BONUS:.2f} per referral

👥 Your Referrals:
{get_user(user_id)["referrals"]}

📎 Your Referral Link:

{link}
"""
    )


# ============================================================
# WITHDRAW
# ============================================================

async def withdraw(update, context):
    user_id = update.effective_user.id
    bal = balance(user_id)

    if bal < MIN_WITHDRAW:
        await update.message.reply_text(
            f"""
❌ Minimum Withdrawal

Minimum: ${MIN_WITHDRAW:.2f}
Your Balance: ${bal:.2f}
"""
        )
  
