import os
import sqlite3
import logging
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@yourusername")
DB_FILE = "bot.db"

REF_BONUS = 0.50
MIN_WITHDRAW = 5.00
WITHDRAW_FEE = 0.50

# Demo/test catalogue only. No real verification-OTP integration.
SERVICES = {
    "Myanmar": {"HSBC": 1.50, "TikTok": 1.20},
    "USA": {"Demo SMS": 1.00},
    "UK": {"Demo SMS": 1.00},
}
DEMO_NUMBERS = ["+959660432713", "+959697711320", "+12025550101", "+447700900123"]

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

db = sqlite3.connect(DB_FILE, check_same_thread=False)
db.row_factory = sqlite3.Row

def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def db_init():
    cur = db.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, name TEXT, username TEXT, balance REAL DEFAULT 0,
        total_deposit REAL DEFAULT 0, total_spent REAL DEFAULT 0, total_withdraw REAL DEFAULT 0,
        referrals INTEGER DEFAULT 0, referred_by INTEGER DEFAULT NULL, banned INTEGER DEFAULT 0,
        created_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, country TEXT, service TEXT,
        number TEXT, price REAL, status TEXT, otp TEXT DEFAULT NULL, created_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS withdrawals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL, method TEXT,
        account TEXT, status TEXT DEFAULT 'pending', created_at TEXT)""")
    db.commit()

def add_user(user_id, name, username="", referred_by=None):
    cur = db.cursor()
    existing = cur.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
    if existing:
        cur.execute("UPDATE users SET name=?, username=? WHERE user_id=?", (name, username, user_id))
    else:
        cur.execute("""INSERT INTO users
            (user_id,name,username,referred_by,created_at) VALUES (?,?,?,?,?)""",
            (user_id, name, username, referred_by, now()))
        if referred_by and referred_by != user_id:
            ref = cur.execute("SELECT user_id FROM users WHERE user_id=?", (referred_by,)).fetchone()
            if ref:
                cur.execute("""UPDATE users SET referrals=referrals+1,balance=balance+?
                               WHERE user_id=?""", (REF_BONUS, referred_by))
    db.commit()

def get_user(user_id):
    return db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

def balance(user_id):
    row = get_user(user_id)
    return float(row["balance"]) if row else 0.0

def change_balance(user_id, amount):
    db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, user_id))
    db.commit()

def create_order(user_id, country, service, number, price):
    cur = db.cursor()
    cur.execute("""INSERT INTO orders
        (user_id,country,service,number,price,status,created_at)
        VALUES (?,?,?,?,?,?,?)""", (user_id,country,service,number,price,"active",now()))
    order_id = cur.lastrowid
    cur.execute("""UPDATE users SET balance=balance-?,total_spent=total_spent+?
                   WHERE user_id=?""", (price,price,user_id))
    db.commit()
    return order_id

def get_order(order_id):
    return db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()

def get_active_orders(user_id):
    return db.execute("""SELECT * FROM orders WHERE user_id=? AND status='active'
                         ORDER BY id DESC""", (user_id,)).fetchall()

def get_user_orders(user_id):
    return db.execute("""SELECT * FROM orders WHERE user_id=?
                         ORDER BY id DESC LIMIT 20""", (user_id,)).fetchall()

def create_withdraw(user_id, amount, method, account):
    cur = db.cursor()
    cur.execute("""INSERT INTO withdrawals
        (user_id,amount,method,account,status,created_at)
        VALUES (?,?,?,?,?,?)""", (user_id,amount,method,account,"pending",now()))
    wid = cur.lastrowid
    cur.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amount,user_id))
    db.commit()
    return wid

def main_keyboard():
    return ReplyKeyboardMarkup([
        ["📱 Get Number","📊 Status"],
        ["📋 Active Number","💰 Wallet"],
        ["🔗 Refer","👤 Profile"],
        ["💸 Withdraw","🆘 Support"],
    ], resize_keyboard=True)

def admin_keyboard():
    return ReplyKeyboardMarkup([
        ["👥 Users","📊 Statistics"],
        ["➕ Add Balance","➖ Remove Balance"],
        ["📦 Services","📱 Demo Inventory"],
        ["💸 Withdrawals","📢 Broadcast"],
        ["🚫 Ban User","✅ Unban User"],
        ["🏠 Main Menu"],
    ], resize_keyboard=True)

def country_keyboard():
    buttons = [[InlineKeyboardButton(f"🌍 {c}", callback_data=f"country:{c}")] for c in SERVICES]
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back:main")])
    return InlineKeyboardMarkup(buttons)

def service_keyboard(country):
    buttons = [[InlineKeyboardButton(f"{s} — ${p:.2f}", callback_data=f"service:{country}:{s}")]
               for s,p in SERVICES[country].items()]
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="getnumber")])
    return InlineKeyboardMarkup(buttons)

def banned(user_id):
    row = get_user(user_id)
    return bool(row and row["banned"])

async def ensure_user(update):
    user = update.effective_user
    if not get_user(user.id):
        add_user(user.id, user.first_name or "User", user.username or "")

async def safe_send(context, chat_id, text):
    try:
        await context.bot.send_message(chat_id=chat_id, text=text)
        return True
    except Exception:
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    referred_by = None
    if context.args:
        try:
            referred_by = int(context.args[0])
        except Exception:
            pass
    add_user(user.id, user.first_name or "User", user.username or "", referred_by)
    if banned(user.id):
        await update.message.reply_text("🚫 Your account is banned.")
        return
    await update.message.reply_text(
        f"""👋 Welcome {user.first_name}!

🤖 Virtual Number Marketplace

📱 Get Number
📊 Status
📋 Active Number
💰 Wallet
🔗 Referral
💸 Withdraw

Choose an option below 👇""", reply_markup=main_keyboard())

async def get_number(update, context):
    await update.message.reply_text("📱 GET NUMBER

🌍 Select Country:", reply_markup=country_keyboard())

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if banned(user_id):
        await query.edit_message_text("🚫 Your account is banned.")
        return
    data = query.data

    if data == "back:main":
        await query.message.reply_text("🏠 Main Menu", reply_markup=main_keyboard())
        return
    if data == "getnumber":
        await query.edit_message_text("📱 GET NUMBER

🌍 Select Country:", reply_markup=country_keyboard())
        return
    if data.startswith("country:"):
        country = data.split(":",1)[1]
        await query.edit_message_text(f"🌍 Country: {country}

🏦 Select Service:",
                                      reply_markup=service_keyboard(country))
        return
    if data.startswith("service:"):
        parts = data.split(":",2)
        country, service = parts[1], parts[2]
        price = SERVICES[country][service]
        if balance(user_id) < price:
            await query.edit_message_text(
                f"❌ Insufficient Balance

💰 Required: ${price:.2f}
"
                f"💳 Your Balance: ${balance(user_id):.2f}

Please add balance first.")
            return
        number = DEMO_NUMBERS[user_id % len(DEMO_NUMBERS)]
        order_id = create_order(user_id,country,service,number,price)
        await query.edit_message_text(
            f"""✅ NUMBER ACTIVATED

🆔 Order ID: #{order_id}
🌍 Country: {country}
🏦 Service: {service}

📱 Number:
{number}

💰 Price: ${price:.2f}
⏳ Status: Waiting

⚠️ DEMO MODE
This is test/demo inventory and is NOT connected
to real WhatsApp/TikTok/Telegram verification services.""",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔐 Demo OTP", callback_data=f"demootp:{order_id}")],
                [InlineKeyboardButton("🔄 Change", callback_data=f"change:{order_id}"),
                 InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{order_id}")]
            ]))
        return
    if data.startswith("demootp:"):
        order_id = int(data.split(":")[1])
        order = get_order(order_id)
        if not order or order["user_id"] != user_id:
            await query.answer("Order not found.", show_alert=True)
            return
        if order["status"] != "active":
            await query.answer("Order is no longer active.", show_alert=True)
            return
        demo_code = "123456"
        db.execute("UPDATE orders SET otp=?,status='completed' WHERE id=?", (demo_code,order_id))
        db.commit()
        await query.edit_message_text(
            f"""✅ DEMO OTP RECEIVED

🆔 Order: #{order_id}
📱 Number: {order["number"]}
🔐 Demo OTP: {demo_code}
📊 Status: Completed

⚠️ This is a test OTP only.""")
        return
    if data.startswith("cancel:"):
        order_id = int(data.split(":")[1])
        order = get_order(order_id)
        if not order or order["user_id"] != user_id:
            return
        if order["status"] != "active":
            await query.answer("Order already closed.", show_alert=True)
            return
        db.execute("UPDATE orders SET status='cancelled' WHERE id=?", (order_id,))
        change_balance(user_id,float(order["price"]))
        await query.edit_message_text(
            f"❌ Order Cancelled

🆔 Order: #{order_id}
💰 Refunded: ${order['price']:.2f}")
        return
    if data.startswith("change:"):
        order_id = int(data.split(":")[1])
        order = get_order(order_id)
        if not order or order["user_id"] != user_id:
            return
        if order["status"] != "active":
            await query.answer("Order is no longer active.", show_alert=True)
            return
        number = DEMO_NUMBERS[(user_id + order_id + 1) % len(DEMO_NUMBERS)]
        db.execute("UPDATE orders SET number=? WHERE id=?", (number,order_id))
        db.commit()
        await query.edit_message_text(
            f"🔄 NUMBER CHANGED

🆔 Order: #{order_id}
📱 New Number: {number}
⏳ Status: Waiting")

async def wallet(update, context):
    user = get_user(update.effective_user.id)
    await update.message.reply_text(
        f"""💰 WALLET

💵 Balance: ${user['balance']:.2f}
💳 Total Spent: ${user['total_spent']:.2f}
💸 Total Withdrawn: ${user['total_withdraw']:.2f}""")

async def profile(update, context):
    user = get_user(update.effective_user.id)
    await update.message.reply_text(
        f"""👤 PROFILE

🆔 ID: {user['user_id']}
👤 Name: {user['name']}
💰 Balance: ${user['balance']:.2f}
🔗 Referrals: {user['referrals']}
💵 Total Spent: ${user['total_spent']:.2f}
💸 Total Withdrawn: ${user['total_withdraw']:.2f}""")

async def status(update, context):
    orders = get_active_orders(update.effective_user.id)
    await update.message.reply_text(
        f"📊 BOT STATUS

🤖 Bot: Online
📱 Active Orders: {len(orders)}
💾 Database: Online

✅ System operational.")

async def active_numbers(update, context):
    orders = get_active_orders(update.effective_user.id)
    if not orders:
        await update.message.reply_text("📋 No Active Numbers.")
        return
    text = "📋 ACTIVE NUMBERS

"
    for o in orders:
        text += f"🆔 #{o['id']}
🌍 {o['country']}
🏦 {o['service']}
📱 {o['number']}
📊 {o['status']}

"
    await update.message.reply_text(text)

async def orders(update, context):
    rows = get_user_orders(update.effective_user.id)
    if not rows:
        await update.message.reply_text("📭 No orders found.")
        return
    text = "📋 YOUR ORDERS

"
    for r in rows:
        text += f"#{r['id']} — {r['country']} / {r['service']}
📱 {r['number']}
💰 ${r['price']:.2f}
📊 {r['status']}

"
    await update.message.reply_text(text)

async def refer(update, context):
    user_id = update.effective_user.id
    me = await context.bot.get_me()
    link = f"https://t.me/{me.username}?start={user_id}"
    await update.message.reply_text(
        f"""🔗 REFER & EARN

💰 Bonus: ${REF_BONUS:.2f} per referral
👥 Your Referrals: {get_user(user_id)['referrals']}

📎 Your Referral Link:
{link}""")

async def withdraw(update, context):
    user_id = update.effective_user.id
    bal = balance(user_id)
    if bal < MIN_WITHDRAW:
        await update.message.reply_text(f"❌ Minimum Withdrawal

Minimum: ${MIN_WITHDRAW:.2f}
Your Balance: ${bal:.2f}")
        return
    context.user_data["action"] = "withdraw_amount"
    await update.message.reply_text(
        f"💸 WITHDRAW

Available: ${bal:.2f}
Minimum: ${MIN_WITHDRAW:.2f}
Fee: ${WITHDRAW_FEE:.2f}

Send withdrawal amount.
Example: 10")

async def support(update, context):
    await update.message.reply_text(f"🆘 SUPPORT

Contact:
{SUPPORT_USERNAME}")

async def admin(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("🔐 ADMIN PANEL", reply_markup=admin_keyboard())

async def admin_users(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    count = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    await update.message.reply_text(f"👥 Total Users: {count}")

async def statistics(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    users = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    orders_count = db.execute("SELECT COUNT(*) AS c FROM orders").fetchone()["c"]
    active = db.execute("SELECT COUNT(*) AS c FROM orders WHERE status='active'").fetchone()["c"]
    withdrawn = db.execute("SELECT COALESCE(SUM(amount),0) AS s FROM withdrawals WHERE status='approved'").fetchone()["s"]
    await update.message.reply_text(
        f"📊 STATISTICS

👥 Users: {users}
📦 Orders: {orders_count}
📱 Active Orders: {active}
💸 Approved Withdraw: ${withdrawn:.2f}")

async def admin_add_balance(update, context):
    if update.effective_user.id != ADMIN_ID: return
    context.user_data["action"] = "admin_add"
    await update.message.reply_text("➕ ADD BALANCE

Format:
USER_ID AMOUNT

Example:
123456789 10")

async def admin_remove_balance(update, context):
    if update.effective_user.id != ADMIN_ID: return
    context.user_data["action"] = "admin_remove"
    await update.message.reply_text("➖ REMOVE BALANCE

Format:
USER_ID AMOUNT")

async def admin_ban(update, context):
    if update.effective_user.id != ADMIN_ID: return
    context.user_data["action"] = "ban"
    await update.message.reply_text("Send User ID to ban.")

async def admin_unban(update, context):
    if update.effective_user.id != ADMIN_ID: return
    context.user_data["action"] = "unban"
    await update.message.reply_text("Send User ID to unban.")

async def admin_broadcast(update, context):
    if update.effective_user.id != ADMIN_ID: return
    context.user_data["action"] = "broadcast"
    await update.message.reply_text("📢 Send the broadcast message.")

async def do_broadcast(update, context):
    users = db.execute("SELECT user_id FROM users WHERE banned=0").fetchall()
    success = 0
    for row in users:
        if await safe_send(context,row["user_id"],update.message.text):
            success += 1
    await update.message.reply_text(f"📢 BROADCAST COMPLETE

👥 Sent: {success}/{len(users)}")

async def admin_withdrawals(update, context):
    if update.effective_user.id != ADMIN_ID: return
    rows = db.execute("SELECT * FROM withdrawals WHERE status='pending' ORDER BY id DESC LIMIT 20").fetchall()
    if not rows:
        await update.message.reply_text("💸 No pending withdrawals.")
        return
    for r in rows:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approve", callback_data=f"wdapprove:{r['id']}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"wdreject:{r['id']}")
        ]])
        await update.message.reply_text(
            f"""💸 WITHDRAW REQUEST

🆔 #{r['id']}
👤 User: {r['user_id']}
💰 Amount: ${r['amount']:.2f}
💳 Method: {r['method']}
📌 Account: {r['account']}
📊 Status: {r['status']}""", reply_markup=keyboard)

async def withdrawal_callback(update, context):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("Admin only.", show_alert=True)
        return
    await query.answer()
    data = query.data
    wid = int(data.split(":")[1])
    row = db.execute("SELECT * FROM withdrawals WHERE id=?", (wid,)).fetchone()
    if not row or row["status"] != "pending":
        await query.edit_message_text("Request already processed.")
        return
    if data.startswith("wdapprove:"):
        db.execute("UPDATE withdrawals SET status='approved' WHERE id=?", (wid,))
        db.execute("UPDATE users SET total_withdraw=total_withdraw+? WHERE user_id=?", (row["amount"],row["user_id"]))
        db.commit()
        await query.edit_message_text(f"✅ Withdrawal #{wid} approved.")
        await safe_send(context,row["user_id"],f"✅ Withdrawal Approved

🆔 #{wid}
💰 Amount: ${row['amount']:.2f}")
    else:
        db.execute("UPDATE withdrawals SET status='rejected' WHERE id=?", (wid,))
        db.commit()
        change_balance(row["user_id"],row["amount"])
        await query.edit_message_text(f"❌ Withdrawal #{wid} rejected and refunded.")
        await safe_send(context,row["user_id"],f"❌ Withdrawal Rejected

🆔 #{wid}
💰 Refunded: ${row['amount']:.2f}")

async def services(update, context):
    if update.effective_user.id != ADMIN_ID: return
    text = "📦 SERVICES

"
    for country, slist in SERVICES.items():
        text += f"🌍 {country}
"
        for service, price in slist.items():
            text += f"  • {service}: ${price:.2f}
"
        text += "
"
    await update.message.reply_text(text)

async def inventory(update, context):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("📱 DEMO INVENTORY

" + "
".join(f"• {n}" for n in DEMO_NUMBERS) + "

⚠️ Demo/test numbers only.")

async def text_handler(update, context):
    user_id = update.effective_user.id
    text = update.message.text
    await ensure_user(update)

    if banned(user_id):
        await update.message.reply_text("🚫 Your account is banned.")
        return

    action = context.user_data.get("action")

    if action == "withdraw_amount":
        try:
            amount = float(text)
            if amount < MIN_WITHDRAW or amount > balance(user_id):
                raise ValueError
            context.user_data["withdraw_amount"] = amount
            context.user_data["action"] = "withdraw_method"
            await update.message.reply_text("💳 Send withdrawal method.

Example:
bKash
Nagad
USDT")
        except Exception:
            await update.message.reply_text("❌ Invalid amount.")
        return

    if action == "withdraw_method":
        context.user_data["withdraw_method"] = text
        context.user_data["action"] = "withdraw_account"
        await update.message.reply_text("📌 Send your payment account/address.")
        return

    if action == "withdraw_account":
        amount = context.user_data["withdraw_amount"]
        method = context.user_data["withdraw_method"]
        wid = create_withdraw(user_id,amount,method,text)
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ Withdrawal Request Created

🆔 Request: #{wid}
💰 Amount: ${amount:.2f}
💳 Method: {method}
⏳ Status: Pending")
        return

    if user_id == ADMIN_ID:
        if action == "admin_add":
            try:
                target, amount = text.split()
                target, amount = int(target), float(amount)
                if not get_user(target):
                    await update.message.reply_text("❌ User not found.")
                    return
                change_balance(target,amount)
                context.user_data.clear()
                await update.message.reply_text(f"✅ Balance Added

👤 User: {target}
💰 Amount: ${amount:.2f}")
                await safe_send(context,target,f"💰 Balance Added

Your wallet has been credited: ${amount:.2f}")
            except Exception:
                await update.message.reply_text("❌ Format: USER_ID AMOUNT")
            return

        if action == "admin_remove":
            try:
                target, amount = text.split()
                target, amount = int(target), float(amount)
                if balance(target) < amount:
                    await update.message.reply_text("❌ Insufficient user balance.")
                    return
                change_balance(target,-amount)
                context.user_data.clear()
                await update.message.reply_text("✅ Balance removed.")
            except Exception:
                await update.message.reply_text("❌ Format: USER_ID AMOUNT")
            return

        if action in ("ban","unban"):
            try:
                target = int(text)
                value = 1 if action == "ban" else 0
                db.execute("UPDATE users SET banned=? WHERE user_id=?", (value,target))
                db.commit()
                context.user_data.clear()
                await update.message.reply_text(("🚫 User banned." if value else "✅ User unbanned."))
            except Exception:
                await update.message.reply_text("❌ Invalid User ID.")
            return

        if action == "broadcast":
            context.user_data.clear()
            await do_broadcast(update,context)
            return

    if text == "📱 Get Number": await get_number(update,context)
    elif text == "📊 Status": await status(update,context)
    elif text == "📋 Active Number": await active_numbers(update,context)
    elif text == "💰 Wallet": await wallet(update,context)
    elif text == "🔗 Refer": await refer(update,context)
    elif text == "👤 Profile": await profile(update,context)
    elif text == "💸 Withdraw": await withdraw(update,context)
    elif text == "🆘 Support": await support(update,context)
    elif text == "📋 Orders": await orders(update,context)
    elif user_id == ADMIN_ID:
        if text == "👥 Users": await admin_users(update,context)
        elif text == "📊 Statistics": await statistics(update,context)
        elif text == "➕ Add Balance": await admin_add_balance(update,context)
        elif text == "➖ Remove Balance": await admin_remove_balance(update,context)
        elif text == "💸 Withdrawals": await admin_withdrawals(update,context)
        elif text == "📢 Broadcast": await admin_broadcast(update,context)
        elif text == "🚫 Ban User": await admin_ban(update,context)
        elif text == "✅ Unban User": await admin_unban(update,context)
        elif text == "📦 Services": await services(update,context)
        elif text == "📱 Demo Inventory": await inventory(update,context)
        elif text == "🏠 Main Menu":
            await update.message.reply_text("🏠 Main Menu",reply_markup=main_keyboard())

async def error_handler(update, context):
    logger.error("Update error: %s", context.error)

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")
    db_init()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("admin",admin))
    app.add_handler(CallbackQueryHandler(withdrawal_callback,pattern=r"^wd(approve|reject):"))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_handler))
    app.add_error_handler(error_handler)
    print("🤖 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
