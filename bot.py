# bot.py
import random, string, logging, sqlite3, asyncio, os
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, MessageHandler, filters)
import config
import pterodactyl as pt

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

CHOOSE_TYPE, ENTER_EMAIL = range(2)
ADMIN_CHOOSE_TYPE, ADMIN_CHOOSE_RAM, ADMIN_CHOOSE_DISK, ADMIN_CHOOSE_CPU, ADMIN_ENTER_EMAIL = range(20, 25)
ASK_NEW_PASS = 10
ASK_EMAIL_FOR_PASS, ASK_PASS_NEW = range(11, 13)
ASK_BROADCAST, ASK_PLAN = range(30, 32)
ASK_CREDIT_USER, ASK_CREDIT_AMOUNT = range(32, 34)
ASK_TRANSFER_USER, ASK_TRANSFER_AMOUNT, ASK_GIFT_CODE, ASK_GIFT_SPEC = range(34, 38)

EGGS_CACHE = {}
ALLOCATION_CACHE = {}

def db():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS users (telegram_id INTEGER PRIMARY KEY, username TEXT, first_seen TEXT NOT NULL, trial_used INTEGER NOT NULL DEFAULT 0)")
    conn.execute("CREATE TABLE IF NOT EXISTS server_credentials (server_id INTEGER PRIMARY KEY, telegram_id INTEGER NOT NULL, email TEXT NOT NULL, username TEXT NOT NULL, password TEXT NOT NULL, updated_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS owned_servers (telegram_id INTEGER NOT NULL, server_id INTEGER PRIMARY KEY, panel_user_id INTEGER NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS notification_log (telegram_id INTEGER, server_id INTEGER, day TEXT, PRIMARY KEY(telegram_id, server_id, day))")
    conn.execute("CREATE TABLE IF NOT EXISTS backup_requests (server_id INTEGER PRIMARY KEY, requested_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending')")
    conn.execute("CREATE TABLE IF NOT EXISTS wallets (telegram_id INTEGER PRIMARY KEY, points INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS points_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER NOT NULL, delta INTEGER NOT NULL, kind TEXT NOT NULL, reference TEXT, created_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS referrals (referred_id INTEGER PRIMARY KEY, referrer_id INTEGER NOT NULL, reward_granted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS gift_codes (code TEXT PRIMARY KEY, points INTEGER NOT NULL, max_uses INTEGER NOT NULL, uses INTEGER NOT NULL DEFAULT 0, expires_at TEXT NOT NULL, created_by INTEGER NOT NULL, created_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS gift_redemptions (code TEXT NOT NULL, telegram_id INTEGER NOT NULL, redeemed_at TEXT NOT NULL, PRIMARY KEY(code, telegram_id))")
    conn.execute("INSERT OR IGNORE INTO wallets(telegram_id, points, updated_at) SELECT telegram_id, 0, ? FROM users", (datetime.now().isoformat(),))
    conn.commit()
    return conn

def register_user(user):
    conn = db(); now=datetime.now().isoformat(); conn.execute("INSERT OR IGNORE INTO users(telegram_id, username, first_seen) VALUES (?, ?, ?)", (user.id, user.username or "", now)); conn.execute("UPDATE users SET username=? WHERE telegram_id=?", (user.username or "", user.id)); conn.execute("INSERT OR IGNORE INTO wallets(telegram_id,points,updated_at) VALUES(?,?,?)",(user.id,0,now)); conn.commit(); conn.close()

def setting(key, default):
    conn = db(); row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone(); conn.close()
    return row["value"] if row else str(default)

def set_setting(key, value):
    conn = db(); conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value))); conn.commit(); conn.close()

def free_plan():
    return {
        "days": int(setting("free_days", config.SERVER_DURATION_DAYS)),
        "memory": int(setting("free_memory_mb", config.TRIAL_MEMORY_MB)),
        "disk": int(setting("free_disk_mb", config.TRIAL_DISK_MB)),
        "cpu": int(setting("free_cpu", config.TRIAL_CPU)),
    }

def points_balance(uid):
    conn=db(); row=conn.execute("SELECT points FROM wallets WHERE telegram_id=?",(int(uid),)).fetchone(); conn.close(); return int(row["points"]) if row else 0

def change_points(uid, delta, kind, reference=""):
    if int(uid) == config.DEVELOPER_ID and int(delta) < 0:
        return True, 10**12
    conn=db()
    try:
        conn.execute("INSERT OR IGNORE INTO wallets(telegram_id,points,updated_at) VALUES(?,?,?)",(int(uid),0,datetime.now().isoformat()))
        row=conn.execute("SELECT points FROM wallets WHERE telegram_id=?",(int(uid),)).fetchone(); new=int(row["points"])+int(delta)
        if new < 0: conn.rollback(); return False, int(row["points"])
        conn.execute("UPDATE wallets SET points=?,updated_at=? WHERE telegram_id=?",(new,datetime.now().isoformat(),int(uid)))
        conn.execute("INSERT INTO points_ledger(telegram_id,delta,kind,reference,created_at) VALUES(?,?,?,?,?)",(int(uid),int(delta),kind,reference,datetime.now().isoformat()))
        conn.commit(); return True,new
    except Exception: conn.rollback(); raise
    finally: conn.close()

def transfer_points(sender, receiver, amount):
    if int(amount) <= 0 or int(sender)==int(receiver): return False,"invalid"
    conn=db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        a=conn.execute("SELECT points FROM wallets WHERE telegram_id=?",(int(sender),)).fetchone()
        if not a or int(a["points"]) < int(amount): conn.rollback(); return False,"insufficient"
        conn.execute("INSERT OR IGNORE INTO wallets(telegram_id,points,updated_at) VALUES(?,?,?)",(int(receiver),0,datetime.now().isoformat()))
        for uid,delta in ((sender,-int(amount)),(receiver,int(amount))):
            conn.execute("UPDATE wallets SET points=points+?,updated_at=? WHERE telegram_id=?",(delta,datetime.now().isoformat(),int(uid)))
            conn.execute("INSERT INTO points_ledger(telegram_id,delta,kind,reference,created_at) VALUES(?,?,?,?,?)",(int(uid),delta,"transfer",str(sender),datetime.now().isoformat()))
        conn.commit(); return True,"ok"
    except Exception: conn.rollback(); raise
    finally: conn.close()

def refund_purchase(context, uid):
    plan=context.user_data.pop("purchase_plan",None)
    if plan:
        change_points(uid,int(plan["points"]),"purchase_refund",plan["name"])

PLANS={
    "basic":{"name":"Basic","points":100,"days":7,"memory":1024,"disk":2048,"cpu":100},
    "pro":{"name":"Pro","points":250,"days":30,"memory":2048,"disk":4096,"cpu":150},
    "max":{"name":"Max","points":500,"days":30,"memory":4096,"disk":10240,"cpu":200},
}

def trial_available(uid):
    conn = db(); row = conn.execute("SELECT trial_used FROM users WHERE telegram_id=?", (uid,)).fetchone(); conn.close(); return not row or not row["trial_used"]

def mark_trial_used(uid):
    conn = db(); conn.execute("UPDATE users SET trial_used=1 WHERE telegram_id=?", (uid,)); conn.commit(); conn.close()

def save_owned_server(tg_id, server_id, panel_uid, expiry, email="", username="", password=""):
    conn = db(); now=datetime.now().isoformat(); conn.execute("INSERT OR REPLACE INTO owned_servers VALUES (?, ?, ?, ?, ?)", (tg_id, int(server_id), int(panel_uid), expiry.isoformat(), now)); conn.execute("INSERT OR REPLACE INTO server_credentials VALUES (?,?,?,?,?,?)",(int(server_id),int(tg_id),email,username,password,now)); conn.commit(); conn.close()

def owned_server_ids(tg_id):
    conn = db(); rows = conn.execute("SELECT server_id FROM owned_servers WHERE telegram_id=?", (tg_id,)).fetchall(); conn.close(); return {int(r["server_id"]) for r in rows}

def owns_server(tg_id, sid):
    return int(sid) in owned_server_ids(tg_id)

def subscription_text():
    return config.SUBSCRIPTION_MESSAGE

def main_keyboard(dev=False):
    rows = [["🖥️ إنشاء سيرفر", "🛒 شراء بالنقاط"], ["📋 سيرفراتي", "👤 ملفي"], ["💰 تحويل نقاط", "🎁 كود هدية"], ["✅ حالة البوت", "💳 الاشتراك"]]
    if dev:
        rows += [["📊 الإحصائيات", "🗂️ كل السيرفرات"], ["👥 المستخدمون", "📣 إذاعة"], ["⚙️ الخطة المجانية", "⛔ إيقاف الكل"], ["▶️ تشغيل الكل"]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)

async def subscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(subscription_text(), reply_markup=main_keyboard(is_dev(update.effective_user.id)))

def is_dev(uid):
    return uid == config.DEVELOPER_ID

def rand_pass(n=14):
    chars = string.ascii_letters + string.digits + "!@#"
    return "".join(random.choice(chars) for _ in range(n))

def rand_user():
    return "user" + "".join(random.choices(string.digits, k=6))

def admin_footer():
    return f"\n\n━━━━━━━━━━━━━━\n{config.BRAND_FOOTER}"

def resource_price(ram, disk, cpu):
    vals = [ram * config.PRICE_RAM_PER_GB, disk * config.PRICE_DISK_PER_GB, (cpu / 100) * config.PRICE_CPU_PER_100]
    if any(v == "inf" for v in (ram, disk, cpu)):
        return config.INFINITE_RESOURCE_PRICE
    return f"{sum(vals):.2f} {config.PRICE_CURRENCY} / شهر"

def load_eggs():
    eggs, err = pt.discover_eggs()
    if err:
        log.error(f"فشل اكتشاف البيض: {err}")
        return False
    EGGS_CACHE.update(eggs)
    nodes, err = pt.list_nodes()
    if not err and nodes.get("data"):
        node_id = nodes["data"][0]["attributes"]["id"]
        alloc, _ = pt.get_free_allocation(node_id)
        if alloc:
            ALLOCATION_CACHE["id"] = alloc
    log.info(f"✅ البيض: {list(eggs.keys())} | تخصيص: {ALLOCATION_CACHE.get('id')}")
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    if context.args:
        token=context.args[0]
        if token.startswith("ref_"):
            try:
                ref=int(token[4:])
                if ref != update.effective_user.id:
                    conn=db(); exists=conn.execute("SELECT 1 FROM referrals WHERE referred_id=?",(update.effective_user.id,)).fetchone()
                    if not exists and conn.execute("SELECT 1 FROM users WHERE telegram_id=?",(ref,)).fetchone():
                        conn.execute("INSERT INTO referrals(referred_id,referrer_id,reward_granted,created_at) VALUES(?,?,0,?)",(update.effective_user.id,ref,datetime.now().isoformat())); conn.commit(); conn.close()
                        change_points(ref,50,"referral",str(update.effective_user.id)); change_points(update.effective_user.id,25,"referral",str(ref))
                        await update.message.reply_text("🎉 تمت إضافة مكافأة الإحالة: 25 نقطة لك و50 نقطة لصاحب الرابط.")
                    else: conn.close()
            except Exception: pass
        elif token.startswith("gift_"):
            await redeem_gift(update, token[5:])
    if not is_dev(update.effective_user.id):
        text = (
            "👋 **مرحباً بك في Arab Code Hosting!**\n\n"
            "🎁 سيرفر مجاني لأول مرة لمدة 7 أيام.\n"
            "🖥️ `/create` — إنشاء سيرفر Python أو Node.js\n"
            "📋 `/servers` — سيرفراتك\n"
            f"\nبعد انتهاء التجربة: {config.SUBSCRIPTION_MESSAGE}")
        if os.path.exists(config.BRAND_IMAGE):
            with open(config.BRAND_IMAGE, "rb") as photo:
                await update.message.reply_photo(photo=photo, caption=text,
                    parse_mode="Markdown", reply_markup=main_keyboard(False))
        else:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard(False))
        return
    await update.message.reply_text(
        "👋 **مرحباً يا مطور!**\n\n"
        "📦 `/create` — إنشاء سيرفر\n"
        "📋 `/servers` — قائمة السيرفرات\n"
        "📊 `/stats` — إحصائيات\n"
        "🔑 `/chpass` — تغيير باسورد\n"
        "🔄 `/renew <id>` — تجديد\n"
        "🧩 `/changegg <id>` — تغيير البيضة\n"
        "⛔ `/suspendall` — إيقاف الكل\n"
        "✅ `/unsuspendall` — تشغيل الكل\n"
        "👥 `/users` — مستخدمو Telegram\n"
        "📣 `/broadcast` — إذاعة\n"
        "⚙️ `/freeplan` — إعداد الخطة المجانية\n"
        f"ℹ️ `/status` — حالة البوت",
        parse_mode="Markdown", reply_markup=main_keyboard(True))

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    if not is_dev(update.effective_user.id):
        await update.message.reply_text("✅ الخدمة تعمل. استخدم /create لإنشاء سيرفرك المجاني.", reply_markup=main_keyboard(False))
        return
    servers, err = pt.list_servers()
    if err:
        await update.message.reply_text(f"❌ البانل غير متصل: {err}")
        return
    total = servers.get("meta", {}).get("pagination", {}).get("total", 0)
    eggs_list = ", ".join(EGGS_CACHE.keys()) if EGGS_CACHE else "لم تُكتشف"
    await update.message.reply_text(
        f"✅ **البوت يعمل**\n\n🌐 البانل: متصل\n"
        f"📊 السيرفرات: `{total}`\n🧩 البيض: `{eggs_list}`\n"
        f"🎯 التخصيص: `{ALLOCATION_CACHE.get('id', '؟')}`\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        parse_mode="Markdown")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_dev(update.effective_user.id):
        return
    users, e1 = pt.api("GET", "/users")
    servers, e2 = pt.list_servers()
    if e1 or e2:
        await update.message.reply_text("❌ فشل جلب الإحصائيات.")
        return
    u = users.get("meta", {}).get("pagination", {}).get("total", 0)
    s = servers.get("meta", {}).get("pagination", {}).get("total", 0)
    await update.message.reply_text(
        f"📊 **إحصائيات:**\n\n👥 المستخدمون: `{u}`\n🖥️ السيرفرات: `{s}`",
        parse_mode="Markdown")

async def create_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    if is_dev(update.effective_user.id):
        if not EGGS_CACHE:
            load_eggs()
        keyboard = []
        if "python" in EGGS_CACHE:
            keyboard.append([InlineKeyboardButton("🐍 Python", callback_data="admin_type_python")])
        if "node" in EGGS_CACHE:
            keyboard.append([InlineKeyboardButton("🟢 Node.js 22", callback_data="admin_type_node")])
        keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data="admin_cancel")])
        await update.message.reply_text("🛠️ **إنشاء سيرفر إداري**\n\nاختر البيضة:",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return ADMIN_CHOOSE_TYPE
    if not is_dev(update.effective_user.id) and not trial_available(update.effective_user.id):
        kb=[[InlineKeyboardButton(f"🛒 {v['name']} — {v['points']} نقطة",callback_data=f"plan_{k}")] for k,v in PLANS.items()]
        await update.message.reply_text("⚠️ انتهت التجربة المجانية. اختر خطة للشراء بالنقاط:",reply_markup=InlineKeyboardMarkup(kb)); return CHOOSE_TYPE
    if not EGGS_CACHE:
        load_eggs()
    if not EGGS_CACHE:
        await update.message.reply_text("❌ لم يتم اكتشاف بيض البانل.")
        return ConversationHandler.END
    keyboard = []
    if "python" in EGGS_CACHE:
        keyboard.append([InlineKeyboardButton("🐍 Python", callback_data="type_python")])
    if "node" in EGGS_CACHE:
        keyboard.append([InlineKeyboardButton("🟢 Node.js 22", callback_data="type_node")])
    keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
    await update.message.reply_text("🧩 **اختر النوع:**",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return CHOOSE_TYPE

async def admin_choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "admin_cancel":
        await q.edit_message_text("❌ تم الإلغاء.")
        return ConversationHandler.END
    stype = q.data.replace("admin_type_", "")
    context.user_data["type"] = stype
    context.user_data["egg"] = EGGS_CACHE[stype]
    kb = [[InlineKeyboardButton(f"🟦 {n}G", callback_data=f"admin_ram_{n}") for n in range(1, 4)],
          [InlineKeyboardButton(f"🟦 {n}G", callback_data=f"admin_ram_{n}") for n in range(4, 6)],
          [InlineKeyboardButton("♾️ RAM Infinite", callback_data="admin_ram_inf")],
          [InlineKeyboardButton("🔙 رجوع", callback_data="admin_back_type")]]
    await q.edit_message_text(f"✅ البيضة: **{stype.upper()}**\n\nاختر RAM:",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return ADMIN_CHOOSE_RAM

async def admin_choose_ram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "admin_back_type":
        kb = [[InlineKeyboardButton("🐍 Python", callback_data="admin_type_python"), InlineKeyboardButton("🟢 Node.js 22", callback_data="admin_type_node")], [InlineKeyboardButton("❌ إلغاء", callback_data="admin_cancel")]]
        await q.edit_message_text("🛠️ اختر البيضة:", reply_markup=InlineKeyboardMarkup(kb))
        return ADMIN_CHOOSE_TYPE
    value = q.data.replace("admin_ram_", "")
    context.user_data["ram"] = "inf" if value == "inf" else int(value)
    kb = [[InlineKeyboardButton(f"💿 {n}G", callback_data=f"admin_disk_{n}") for n in range(1, 6)],
          [InlineKeyboardButton(f"💿 {n}G", callback_data=f"admin_disk_{n}") for n in range(6, 11)],
          [InlineKeyboardButton("🔙 رجوع", callback_data="admin_back_ram")]]
    await q.edit_message_text("اختر مساحة التخزين Disk:", reply_markup=InlineKeyboardMarkup(kb))
    return ADMIN_CHOOSE_DISK

async def admin_choose_disk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "admin_back_ram":
        kb = [[InlineKeyboardButton(f"🟦 {n}G", callback_data=f"admin_ram_{n}") for n in range(1, 4)], [InlineKeyboardButton(f"🟦 {n}G", callback_data=f"admin_ram_{n}") for n in range(4, 6)], [InlineKeyboardButton("♾️ RAM Infinite", callback_data="admin_ram_inf")], [InlineKeyboardButton("🔙 رجوع", callback_data="admin_back_type")]]
        await q.edit_message_text("اختر RAM:", reply_markup=InlineKeyboardMarkup(kb))
        return ADMIN_CHOOSE_RAM
    context.user_data["disk"] = int(q.data.replace("admin_disk_", ""))
    kb = [[InlineKeyboardButton(f"⚡ {n}%", callback_data=f"admin_cpu_{n}") for n in (100, 150, 200)],
          [InlineKeyboardButton("♾️ CPU Infinite", callback_data="admin_cpu_inf")],
          [InlineKeyboardButton("🔙 رجوع", callback_data="admin_back_disk")]]
    await q.edit_message_text("اختر CPU:", reply_markup=InlineKeyboardMarkup(kb))
    return ADMIN_CHOOSE_CPU

async def admin_choose_cpu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "admin_back_disk":
        kb = [[InlineKeyboardButton(f"💿 {n}G", callback_data=f"admin_disk_{n}") for n in range(1, 6)], [InlineKeyboardButton(f"💿 {n}G", callback_data=f"admin_disk_{n}") for n in range(6, 11)], [InlineKeyboardButton("🔙 رجوع", callback_data="admin_back_ram")]]
        await q.edit_message_text("اختر مساحة التخزين Disk:", reply_markup=InlineKeyboardMarkup(kb))
        return ADMIN_CHOOSE_DISK
    value = q.data.replace("admin_cpu_", "")
    context.user_data["cpu"] = "inf" if value == "inf" else int(value)
    ram, disk, cpu = context.user_data["ram"], context.user_data["disk"], context.user_data["cpu"]
    await q.edit_message_text(
        f"📦 الموارد المختارة:\nRAM: `{ram if ram != 'inf' else 'Infinite'}`\n"
        f"Disk: `{disk}G`\nCPU: `{cpu if cpu != 'inf' else 'Infinite'}`\n"
        f"💰 السعر المبدئي: `{resource_price(ram, disk, cpu)}`\n\n"
        "أرسل إيميل المستخدم أو اسم المستخدم (سيضاف @arab-code.cv تلقائيًا):",
        parse_mode="Markdown")
    return ADMIN_ENTER_EMAIL

async def admin_receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    email = update.message.text.strip()
    if "@" not in email:
        email = f"{email}@arab-code.cv"
    existing, _ = pt.find_user_by_email(email)
    if existing:
        await update.message.reply_text("⚠️ الإيميل مستخدم بالفعل. أرسل اسمًا آخر:")
        return ADMIN_ENTER_EMAIL
    password, username = rand_pass(), rand_user()
    egg, stype = context.user_data["egg"], context.user_data["type"]
    ram, disk, cpu = context.user_data["ram"], context.user_data["disk"], context.user_data["cpu"]
    msg = await update.message.reply_text("⏳ جاري إنشاء السيرفر الإداري...")
    uid, err = pt.create_user(email, username, password)
    if err:
        await msg.edit_text(f"❌ فشل إنشاء المستخدم: {err}")
        return ConversationHandler.END
    is_py = stype == "python"
    env = pt.build_environment(egg, is_py)
    alloc_id, err = pt.get_free_allocation(1)
    if err:
        pt.delete_user(uid); await msg.edit_text(f"❌ لا يوجد تخصيص: {err}"); return ConversationHandler.END
    limits = {"memory": 0 if ram == "inf" else ram * 1024, "swap": 0,
              "disk": disk * 1024, "io": 500, "cpu": 0 if cpu == "inf" else cpu}
    server, err = pt.create_server(uid, egg["id"], f"{username}-srv",
        config.DOCKER_PYTHON if is_py else config.DOCKER_NODE,
        egg.get("startup") or (config.STARTUP_PYTHON if is_py else config.STARTUP_NODE),
        env, alloc_id, limits=limits)
    if err:
        pt.delete_user(uid); await msg.edit_text(f"❌ فشل إنشاء السيرفر: {err}"); return ConversationHandler.END
    sid = server["id"]
    expiry = datetime.now() + timedelta(days=config.ADMIN_SERVER_DURATION_DAYS)
    save_owned_server(update.effective_user.id, sid, uid, expiry, email, username, password)
    pt.update_server_details(sid, {"name": f"{username}-srv", "user": int(uid), "description": f"expires_at={expiry.isoformat()}", "external_id": expiry.isoformat()})
    invoice = ("🧾 **فاتورة إنشاء سيرفر**\n\n"
        f"🆔 رقم السيرفر: `{sid}`\n📧 الحساب: `{email}`\n🔑 كلمة المرور: `{password}`\n"
        f"👤 اسم المستخدم: `{username}`\n🧩 البيضة: `{stype.upper()}`\n\n"
        f"💾 RAM: `{ram if ram != 'inf' else 'Infinite'}`\n💿 Disk: `{disk}G`\n"
        f"⚡ CPU: `{cpu if cpu != 'inf' else 'Infinite'}`\n"
        f"💰 السعر: `{resource_price(ram, disk, cpu)}`\n📅 الصلاحية حتى: `{expiry.strftime('%Y-%m-%d')}`"
        f"{admin_footer()}")
    await msg.edit_text(invoice, parse_mode="Markdown")
    return ConversationHandler.END

async def choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "cancel":
        refund_purchase(context, update.effective_user.id)
        await q.edit_message_text("❌ تم الإلغاء.")
        return ConversationHandler.END
    if q.data.startswith("plan_"):
        key=q.data[5:]; plan=PLANS.get(key)
        if not plan: await q.edit_message_text("❌ الخطة غير موجودة."); return ConversationHandler.END
        ok,balance=change_points(update.effective_user.id,-plan["points"],"purchase",key)
        if not ok: await q.edit_message_text(f"❌ ليس لديك رصيد كافٍ. تحتاج {plan['points']} نقطة، ورصيدك {balance}.\n\nلجمع النقاط استخدم رابط الإحالة أو تواصل مع الأدمن @Monte_dev_md."); return ConversationHandler.END
        context.user_data["purchase_plan"]=plan
        await q.edit_message_text(f"✅ تم حجز {plan['name']} مقابل {plan['points']} نقطة. اختر البيضة:",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🐍 Python",callback_data="type_python"),InlineKeyboardButton("🟢 Node.js",callback_data="type_node")],[InlineKeyboardButton("❌ إلغاء",callback_data="cancel")]])); return CHOOSE_TYPE
    stype = q.data.replace("type_", "")
    context.user_data["type"] = stype
    context.user_data["egg"] = EGGS_CACHE[stype]
    await q.edit_message_text(
        f"✅ النوع: **{stype.upper()}**\n\n📧 **أدخل الإيميل:**\n"
        "(سيضاف `@arab-code.cv` تلقائياً)\n\n/cancel للإلغاء",
        parse_mode="Markdown")
    return ENTER_EMAIL

async def receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    email = update.message.text.strip()
    if "@" not in email:
        email = f"{email}@arab-code.cv"
    existing, _ = pt.find_user_by_email(email)
    if existing:
        await update.message.reply_text("⚠️ الإيميل مستخدم.")
        return ENTER_EMAIL
    password = rand_pass()
    username = rand_user()
    stype = context.user_data["type"]
    egg = context.user_data["egg"]
    msg = await update.message.reply_text("⏳ جاري الإنشاء...")
    uid, err = pt.create_user(email, username, password)
    if err:
        refund_purchase(context, update.effective_user.id)
        await msg.edit_text(f"❌ فشل إنشاء المستخدم: {err}")
        return ConversationHandler.END
    is_py = (stype == "python")
    docker = config.DOCKER_PYTHON if is_py else config.DOCKER_NODE
    startup = egg.get("startup") or (config.STARTUP_PYTHON if is_py else config.STARTUP_NODE)
    env = pt.build_environment(egg, is_py)
    # Always ask Wings for a fresh free allocation; cached allocations become
    # assigned after the first server and can cause misleading create errors.
    alloc_id, err = pt.get_free_allocation(1)
    if err:
        pt.delete_user(uid)
        refund_purchase(context, update.effective_user.id)
        await msg.edit_text(f"❌ لا يوجد تخصيص متاح: {err}")
        return ConversationHandler.END
    plan = context.user_data.get("purchase_plan") or free_plan()
    limits = {"memory": plan["memory"], "swap": 0, "disk": plan["disk"], "io": 500, "cpu": plan["cpu"]}
    server, err = pt.create_server(uid, egg["id"], f"{username}-srv",
        docker, startup, env, alloc_id, limits=limits)
    if err and ("[404]" in err or "[409]" in err or "allocation" in err.lower()):
        # A stale allocation or a transient Wings response should not consume
        # the user's trial. Fetch another free allocation and retry once.
        fresh_alloc, alloc_err = pt.get_free_allocation(1)
        if fresh_alloc and fresh_alloc != alloc_id:
            server, err = pt.create_server(uid, egg["id"], f"{username}-srv",
                docker, startup, env, fresh_alloc)
    if err:
        pt.delete_user(uid)
        refund_purchase(context, update.effective_user.id)
        await msg.edit_text(f"❌ فشل إنشاء السيرفر: {err}")
        return ConversationHandler.END
    sid = server["id"]
    plan = context.user_data.pop("purchase_plan", None) or plan
    expiry = datetime.now() + timedelta(days=plan["days"])
    save_owned_server(update.effective_user.id, sid, uid, expiry, email, username, password)
    if not is_dev(update.effective_user.id):
        mark_trial_used(update.effective_user.id)
    pt.update_server_details(sid, {
        "name": f"{username}-srv", "user": int(uid),
        "description": f"expires_at={expiry.isoformat()}",
        "external_id": expiry.isoformat()})
    await msg.edit_text(
        f"✅ **تم إنشاء السيرفر!**\n\n📧 `{email}`\n🔑 `{password}`\n"
        f"👤 `{username}`\n\n🌐 {config.PANEL_URL}\n🆔 `{sid}`\n"
        f"🧩 {stype.upper()}\n⏳ {plan['days']} أيام\n"
        f"📅 {expiry.strftime('%Y-%m-%d %H:%M')}\n\n⚠️ احفظ البيانات!",
        parse_mode="Markdown")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    refund_purchase(context, update.effective_user.id)
    await update.message.reply_text("❌ تم الإلغاء.")
    return ConversationHandler.END

async def servers_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    if is_dev(update.effective_user.id):
        await _show_servers(update, 0)
        return
    ids = owned_server_ids(update.effective_user.id)
    if not ids:
        await update.message.reply_text("📭 لا توجد سيرفرات لك. استخدم /create للبدء.")
        return
    lines, kb = ["📋 **سيرفراتك:**\n"], []
    for sid in sorted(ids):
        srv, err = pt.get_server(sid)
        if err or not srv: continue
        a = srv["attributes"]; icon = "⛔" if a.get("suspended") else "✅"
        lines.append(f"{icon} `{sid}` — {a['name']}")
        kb.append([InlineKeyboardButton(f"⚙️ {a['name']}", callback_data=f"mng_{sid}"), InlineKeyboardButton("📄 بيانات", callback_data=f"data_{sid}")])
    await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def _show_servers(update, page=0):
    res, err = pt.list_servers()
    target = update.callback_query.message if update.callback_query else update.message
    if err:
        await target.reply_text(f"❌ {err}")
        return
    data = res.get("data", [])
    if not data:
        await target.reply_text("📭 لا توجد سيرفرات.")
        return
    PER = 8
    start = page * PER
    chunk = data[start:start + PER]
    text = f"📋 **السيرفرات** ({len(data)}) — صفحة {page+1}\n\n"
    kb = []
    for s in chunk:
        a = s["attributes"]
        icon = "⛔" if a.get("suspended") else "✅"
        text += f"{icon} `{a['id']}` — {a['name']}\n"
        kb.append([InlineKeyboardButton(f"⚙️ {a['name']}", callback_data=f"mng_{a['id']}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"pg_{page-1}"))
    if start + PER < len(data):
        nav.append(InlineKeyboardButton("➡️", callback_data=f"pg_{page+1}"))
    if nav:
        kb.append(nav)
    if update.callback_query:
        await update.callback_query.edit_message_text(text,
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(text,
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def paginate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await _show_servers(update, int(q.data.split("_")[1]))

async def server_data_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    sid=int(q.data.split("_",1)[1])
    if not is_dev(update.effective_user.id) and not owns_server(update.effective_user.id,sid):
        await q.answer("⛔ هذا السيرفر ليس لك.",show_alert=True); return
    srv,err=pt.get_server(sid)
    if err or not srv: await q.edit_message_text(f"❌ تعذر قراءة السيرفر: {err}"); return
    a=srv["attributes"]; conn=db(); row=conn.execute("SELECT expires_at FROM owned_servers WHERE server_id=?",(sid,)).fetchone(); cred=conn.execute("SELECT email,username,password FROM server_credentials WHERE server_id=?",(sid,)).fetchone(); conn.close()
    if not cred:
        u,ue=pt.get_user(a.get("user")) if a.get("user") else (None,"غير متاح")
        attrs=u.get("attributes",{}) if u else {}; email=attrs.get("email","غير محفوظ"); username=attrs.get("username","غير محفوظ"); password="غير محفوظ — استخدم تغيير كلمة المرور"
    else: email,username,password=cred["email"],cred["username"],cred["password"]
    l=a.get("limits",{}); status="موقوف" if a.get("suspended") else "يعمل"; expiry=row["expires_at"][:16].replace("T"," ") if row else "غير مسجل"
    text=(f"📄 بيانات السيرفر {sid}\n\n🖥️ الاسم: {a.get('name','-')}\n📡 الحالة: {status}\n\n📧 الإيميل: {email}\n👤 اسم المستخدم: {username}\n🔑 كلمة المرور: {password}\n\n🌐 رابط اللوحة: {config.PANEL_URL}\n🆔 Server ID: {sid}\n🧩 Egg ID: {a.get('egg','-')}\n💾 RAM: {l.get('memory','-')} MB\n💿 Disk: {l.get('disk','-')} MB\n⚡ CPU: {l.get('cpu','-')}%\n📅 ينتهي: {expiry}")
    await q.edit_message_text(text,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ إدارة السيرفر",callback_data=f"mng_{sid}")]]))

async def manage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    sid = q.data.split("_")[1]
    if not is_dev(update.effective_user.id) and not owns_server(update.effective_user.id, sid):
        await q.edit_message_text("⛔ هذا السيرفر ليس تابعاً لحسابك.")
        return
    srv, err = pt.get_server(sid)
    if err:
        await q.edit_message_text(f"❌ {err}")
        return
    a = srv["attributes"]
    desc = a.get("description", "") or ""
    expiry = "غير معروف"
    if "expires_at=" in desc:
        try:
            expiry = desc.split("expires_at=")[1].split(" ")[0][:16].replace("T", " ")
        except Exception:
            pass
    text = (f"⚙️ **إدارة:** {a['name']}\n\n🆔 `{sid}`\n"
        f"🧩 البيضة: `{a.get('egg')}`\n💾 RAM: `{a['limits']['memory']} MB`\n"
        f"💿 Disk: `{a['limits']['disk']} MB`\n⚡ CPU: `{a['limits']['cpu']}%`\n"
        f"📅 الانتهاء: `{expiry}`\n⛔ معلق: `{a.get('suspended', False)}`")
    if not is_dev(update.effective_user.id):
        kb = [[InlineKeyboardButton("⏹ إيقاف", callback_data=f"act_suspend_{sid}"), InlineKeyboardButton("▶️ تشغيل", callback_data=f"act_unsuspend_{sid}")],
              [InlineKeyboardButton("🔄 إعادة تثبيت", callback_data=f"act_reinstall_{sid}"), InlineKeyboardButton("📊 الموارد", callback_data=f"act_res_{sid}")],
              [InlineKeyboardButton("⚡ ترقية بالنقاط", callback_data=f"act_upgrade_{sid}")],
              [InlineKeyboardButton("🔙 رجوع", callback_data="back_servers")]]
        await q.edit_message_text(text + subscription_text(), reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return
    kb = [
        [InlineKeyboardButton("⏹ إيقاف", callback_data=f"act_suspend_{sid}"),
         InlineKeyboardButton("▶️ تشغيل", callback_data=f"act_unsuspend_{sid}")],
        [InlineKeyboardButton("🔄 إعادة تثبيت", callback_data=f"act_reinstall_{sid}"),
         InlineKeyboardButton("⏳ تجديد", callback_data=f"act_renew_{sid}")],
        [InlineKeyboardButton("🧩 تغيير البيضة", callback_data=f"act_chegg_{sid}"),
         InlineKeyboardButton("🔑 تغيير الباسورد", callback_data=f"act_chpass_{sid}")],
        [InlineKeyboardButton("📊 الموارد", callback_data=f"act_res_{sid}"),
         InlineKeyboardButton("🗑 حذف", callback_data=f"act_del_{sid}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back_servers")],
    ]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown")

async def server_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split("_", 2)
    action, sid = parts[1], parts[2]
    if action != "res":
        await q.answer()
    if not is_dev(update.effective_user.id) and not owns_server(update.effective_user.id, sid):
        await q.answer("⛔ هذا السيرفر ليس تابعاً لك", show_alert=True)
        return

    if action == "suspend":
        _, err = pt.suspend_server(sid)
        await q.answer("✅ تم الإيقاف" if not err else f"❌ {err}", show_alert=True)
    elif action == "unsuspend":
        _, err = pt.unsuspend_server(sid)
        await q.answer("✅ تم التشغيل" if not err else f"❌ {err}", show_alert=True)
    elif action == "reinstall":
        _, err = pt.reinstall_server(sid)
        await q.answer("✅ تم الإرسال" if not err else f"❌ {err}", show_alert=True)
    elif action == "renew":
        expiry, err = extend_owned_server(sid, config.SERVER_DURATION_DAYS)
        await q.edit_message_text((f"✅ **تم التجديد!**\n🆔 `{sid}`\n📅 `{expiry.strftime('%Y-%m-%d %H:%M')}`" if not err else f"❌ {err}"), parse_mode="Markdown")
        return
    elif action == "upgrade":
        kb=[[InlineKeyboardButton(f"⬆️ {v['name']} — {v['points']} نقطة",callback_data=f"upgrade_{sid}_{k}")] for k,v in PLANS.items()]
        kb.append([InlineKeyboardButton("🔙 رجوع",callback_data=f"mng_{sid}")])
        await q.edit_message_text("⚡ اختر ترقية الموارد والمدة:",reply_markup=InlineKeyboardMarkup(kb)); return
    elif action == "res":
        srv, err = pt.get_server(sid)
        if err:
            await q.answer(f"❌ {err}", show_alert=True)
            return
        a = srv["attributes"]
        await q.answer(f"💾 RAM: {a['limits']['memory']} MB\n"
            f"💿 Disk: {a['limits']['disk']} MB\n"
            f"⚡ CPU: {a['limits']['cpu']}%", show_alert=True)
        return
    elif action == "chegg":
        kb = []
        if "python" in EGGS_CACHE:
            kb.append([InlineKeyboardButton("🐍 Python", callback_data=f"setegg_{sid}_python")])
        if "node" in EGGS_CACHE:
            kb.append([InlineKeyboardButton("🟢 Node.js 22", callback_data=f"setegg_{sid}_node")])
        kb.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"mng_{sid}")])
        await q.edit_message_text("🧩 **اختر البيضة الجديدة:**\n⚠️ سيُعاد التثبيت وتُحذف الملفات!",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return
    elif action == "chpass":
        context.user_data["chpass_sid"] = sid
        await q.edit_message_text("🔑 **أرسل كلمة المرور الجديدة:**\n(6+ أحرف)\n\n/cancel للإلغاء")
        return ASK_NEW_PASS
    elif action == "del":
        kb = [[InlineKeyboardButton("✅ نعم", callback_data=f"confdel_{sid}"),
               InlineKeyboardButton("❌ لا", callback_data=f"mng_{sid}")]]
        await q.edit_message_text(f"⚠️ **تأكيد حذف `{sid}`؟**\nسيُحذف نهائياً!",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return
    await manage(update, context)

async def upgrade_plan_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    _,sid,key=q.data.split("_"); plan=PLANS.get(key)
    if not plan or (not is_dev(update.effective_user.id) and not owns_server(update.effective_user.id,sid)):
        await q.edit_message_text("⛔ غير مسموح."); return
    ok,balance=change_points(update.effective_user.id,-plan["points"],"upgrade",f"{sid}:{key}")
    if not ok: await q.answer(f"❌ تحتاج {plan['points']} نقطة. رصيدك {balance}.",show_alert=True); return
    srv,err=pt.get_server(sid)
    if err: change_points(update.effective_user.id,plan["points"],"upgrade_refund",sid); await q.edit_message_text(f"❌ {err}"); return
    a=srv["attributes"]; old=a["limits"]
    allocation=a.get("allocation")
    if isinstance(allocation,dict): allocation=allocation.get("id")
    if not allocation:
        change_points(update.effective_user.id,plan["points"],"upgrade_refund",sid)
        await q.edit_message_text("❌ تعذر معرفة تخصيص السيرفر، وتم رد النقاط. تواصل مع الأدمن."); return
    _,err=pt.update_server_build(sid,{"memory":plan["memory"],"disk":plan["disk"],"cpu":plan["cpu"],"swap":old.get("swap",0),"io":old.get("io",500),"threads":old.get("threads"),"oom_disabled":old.get("oom_disabled",True),"allocation":int(allocation),"feature_limits":a.get("feature_limits",{"databases":0,"allocations":0,"backups":0})})
    if err:
        change_points(update.effective_user.id,plan["points"],"upgrade_refund",sid); await q.edit_message_text(f"❌ فشل تحديث الموارد: {err}"); return
    expiry,err=extend_owned_server(sid,plan["days"])
    await q.edit_message_text((f"✅ تمت الترقية!\n💰 {plan['points']} نقطة\n💾 RAM {plan['memory']}MB\n💿 Disk {plan['disk']}MB\n⚡ CPU {plan['cpu']}%\n📅 حتى {expiry.strftime('%Y-%m-%d %H:%M')}" if not err else f"✅ الموارد حدثت، لكن تعذر تمديد المدة: {err}"))

async def set_egg_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, sid, stype = q.data.split("_", 2)
    egg = EGGS_CACHE.get(stype)
    if not egg:
        await q.edit_message_text("❌ البيضة غير متوفرة.")
        return
    is_py = (stype == "python")
    docker = config.DOCKER_PYTHON if is_py else config.DOCKER_NODE
    startup = egg.get("startup") or (config.STARTUP_PYTHON if is_py else config.STARTUP_NODE)
    env = pt.build_environment(egg, is_py)
    _, err = pt.update_server_build(sid, {"egg": egg["id"],
        "docker_image": docker, "startup": startup, "environment": env})
    if err:
        await q.edit_message_text(f"❌ {err}")
        return
    pt.reinstall_server(sid)
    await q.edit_message_text(
        f"✅ **تم تغيير البيضة إلى {stype.upper()}!**\n🔄 جاري إعادة التثبيت...",
        parse_mode="Markdown")

async def receive_new_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pwd = update.message.text.strip()
    if len(pwd) < 6:
        await update.message.reply_text("❌ قصيرة!")
        return ASK_NEW_PASS
    sid = context.user_data.get("chpass_sid")
    srv, err = pt.get_server(sid)
    if err:
        await update.message.reply_text(f"❌ {err}")
        return ConversationHandler.END
    uid = srv["attributes"]["user"]
    if isinstance(uid, dict):
        uid = uid["id"]
    _, err = pt.update_user(uid, {"password": pwd})
    if err:
        await update.message.reply_text(f"❌ {err}")
    else:
        await update.message.reply_text(f"✅ **تم التغيير!**\n\n🔑 `{pwd}`",
            parse_mode="Markdown")
    return ConversationHandler.END

async def confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    sid = q.data.split("_")[1]
    _, err = pt.delete_server(sid)
    if err:
        await q.edit_message_text(f"❌ {err}")
    else:
        await q.edit_message_text(f"✅ تم حذف `{sid}`.", parse_mode="Markdown")

async def back_servers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await _show_servers(update, 0)

async def chpass_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_dev(update.effective_user.id):
        return
    await update.message.reply_text("📧 أرسل إيميل المستخدم:")
    return ASK_EMAIL_FOR_PASS

async def receive_email_for_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text.strip()
    user, err = pt.find_user_by_email(email)
    if err:
        await update.message.reply_text(f"❌ {err}")
        return ConversationHandler.END
    context.user_data["pass_uid"] = user["id"]
    context.user_data["pass_email"] = email
    await update.message.reply_text("🔑 أرسل كلمة المرور الجديدة:")
    return ASK_PASS_NEW

async def receive_pass_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pwd = update.message.text.strip()
    if len(pwd) < 6:
        await update.message.reply_text("❌ قصيرة!")
        return ASK_PASS_NEW
    uid = context.user_data["pass_uid"]
    email = context.user_data["pass_email"]
    _, err = pt.update_user(uid, {"password": pwd})
    if err:
        await update.message.reply_text(f"❌ {err}")
    else:
        await update.message.reply_text(f"✅ تم!\n📧 `{email}`\n🔑 `{pwd}`",
            parse_mode="Markdown")
    return ConversationHandler.END

async def renew_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_dev(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("`/renew <server_id>`", parse_mode="Markdown")
        return
    sid = context.args[0]
    expiry, err = extend_owned_server(sid, config.SERVER_DURATION_DAYS)
    if err:
        await update.message.reply_text(f"❌ {err}")
        return
    await update.message.reply_text(f"✅ تم التجديد حتى `{expiry.strftime('%Y-%m-%d %H:%M')}`", parse_mode="Markdown")

async def changegg_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_dev(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("`/changegg <server_id>`", parse_mode="Markdown")
        return
    sid = context.args[0]
    kb = []
    if "python" in EGGS_CACHE:
        kb.append([InlineKeyboardButton("🐍 Python", callback_data=f"setegg_{sid}_python")])
    if "node" in EGGS_CACHE:
        kb.append([InlineKeyboardButton("🟢 Node.js 22", callback_data=f"setegg_{sid}_node")])
    await update.message.reply_text(f"🧩 اختر البيضة للسيرفر `{sid}`:",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def suspend_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_dev(update.effective_user.id):
        return
    servers, err = pt.list_servers()
    if err:
        await update.message.reply_text(f"❌ {err}")
        return
    ok = bad = 0
    for s in servers.get("data", []):
        _, e = pt.suspend_server(s["attributes"]["id"])
        if e:
            bad += 1
        else:
            ok += 1
    await update.message.reply_text(f"⛔ تم — ✅ {ok} | ❌ {bad}")

async def unsuspend_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_dev(update.effective_user.id):
        return
    servers, err = pt.list_servers()
    if err:
        await update.message.reply_text(f"❌ {err}")
        return
    ok = bad = 0
    for s in servers.get("data", []):
        _, e = pt.unsuspend_server(s["attributes"]["id"])
        if e:
            bad += 1
        else:
            ok += 1
    await update.message.reply_text(f"▶️ تم — ✅ {ok} | ❌ {bad}")

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user); uid=update.effective_user.id
    conn=db(); u=conn.execute("SELECT first_seen,username,trial_used FROM users WHERE telegram_id=?",(uid,)).fetchone(); refs=conn.execute("SELECT count(*) c FROM referrals WHERE referrer_id=?",(uid,)).fetchone()["c"]; conn.close()
    link=f"https://t.me/{(await context.bot.get_me()).username}?start=ref_{uid}"
    await update.message.reply_text(f"👤 ملفك الشخصي\n\n🆔 {uid}\n👤 @{u['username'] or 'بدون'}\n💰 النقاط: {points_balance(uid)}\n👥 الإحالات: {refs}\n🎁 رابط الإحالة:\n{link}")

async def buy_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await create_start(update, context)

async def finish_transfer(update, context, receiver, amount):
    ok,_=transfer_points(update.effective_user.id,int(receiver),int(amount))
    if not ok: await update.message.reply_text("❌ لا يوجد رصيد كافٍ أو البيانات غير صحيحة."); return ConversationHandler.END
    await update.message.reply_text(f"✅ تم تحويل {amount} نقطة إلى المستخدم {receiver} وتسجيل العملية.")
    try: await context.bot.send_message(int(receiver),f"💰 استلمت {amount} نقطة من مستخدم آخر.")
    except Exception: pass
    return ConversationHandler.END

async def transfer_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    if len(context.args)==2:
        try: return await finish_transfer(update,context,int(context.args[0]),int(context.args[1]))
        except Exception: await update.message.reply_text("❌ البيانات غير صحيحة."); return ConversationHandler.END
    await update.message.reply_text("👤 أرسل Telegram ID المستلم:")
    return ASK_TRANSFER_USER

async def receive_transfer_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: context.user_data["transfer_uid"]=int(update.message.text.strip())
    except Exception: await update.message.reply_text("❌ أرسل ID رقميًا."); return ASK_TRANSFER_USER
    await update.message.reply_text("💰 أرسل عدد النقاط المراد تحويلها:")
    return ASK_TRANSFER_AMOUNT

async def receive_transfer_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: amount=int(update.message.text.strip())
    except Exception: await update.message.reply_text("❌ أرسل عددًا صحيحًا."); return ASK_TRANSFER_AMOUNT
    return await finish_transfer(update,context,context.user_data.pop("transfer_uid",0),amount)

async def create_gift(update, context, points, uses, hours):
    if points<=0 or uses<=0 or uses>10000 or hours<=0 or hours>8760:
        await update.message.reply_text("❌ الحدود: النقاط أكبر من 0، الاستخدامات 1-10000، والمدة 1-8760 ساعة."); return ConversationHandler.END
    import secrets
    code=secrets.token_urlsafe(9).replace("-","").replace("_","").upper(); exp=datetime.now()+timedelta(hours=hours)
    conn=db(); conn.execute("INSERT INTO gift_codes VALUES(?,?,?,?,?,?,?)",(code,points,uses,0,exp.isoformat(),update.effective_user.id,datetime.now().isoformat())); conn.commit(); conn.close()
    me=await context.bot.get_me(); await update.message.reply_text(f"🎁 تم إنشاء كود الهدية\n\nالكود: {code}\nالرابط: https://t.me/{me.username}?start=gift_{code}\nالقيمة: {points} نقطة\nالاستخدامات: {uses}\nينتهي: {exp.strftime('%Y-%m-%d %H:%M')}")
    return ConversationHandler.END

async def gift_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_dev(update.effective_user.id):
        if len(context.args)==3:
            try: return await create_gift(update,context,*map(int,context.args))
            except Exception: await update.message.reply_text("❌ أرسل أرقامًا صحيحة."); return ConversationHandler.END
        await update.message.reply_text("🎁 أرسل: عدد النقاط ثم عدد المستخدمين ثم الساعات\nمثال: 100 10 48")
        return ASK_GIFT_SPEC
    await update.message.reply_text("🎁 أرسل كود الهدية:")
    return ASK_GIFT_CODE

async def receive_gift_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code=update.message.text.strip().replace(" ","").upper()
    await redeem_gift(update,code); return ConversationHandler.END

async def receive_gift_spec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: values=list(map(int,update.message.text.split()))
    except Exception: await update.message.reply_text("❌ أرسل ثلاثة أرقام: النقاط الاستخدامات الساعات."); return ASK_GIFT_SPEC
    if len(values)!=3: await update.message.reply_text("❌ الصيغة: 100 10 48"); return ASK_GIFT_SPEC
    return await create_gift(update,context,*values)

async def redeem_gift(update, code):
    uid=update.effective_user.id; conn=db()
    try:
        conn.execute("BEGIN IMMEDIATE"); row=conn.execute("SELECT * FROM gift_codes WHERE code=?",(code.upper(),)).fetchone()
        if not row or row["uses"]>=row["max_uses"] or datetime.fromisoformat(row["expires_at"])<=datetime.now(): conn.rollback(); await update.message.reply_text("❌ الكود غير صالح أو منتهي."); return
        conn.execute("INSERT INTO gift_redemptions VALUES(?,?,?)",(code.upper(),uid,datetime.now().isoformat())); conn.execute("UPDATE gift_codes SET uses=uses+1 WHERE code=?",(code.upper(),)); conn.commit()
    except Exception:
        conn.rollback(); await update.message.reply_text("⚠️ تم استخدام هذا الكود مسبقًا أو حدث تعارض."); return
    finally: conn.close()
    change_points(uid,int(row["points"]),"gift",code.upper()); await update.message.reply_text(f"🎉 تمت إضافة {row['points']} نقطة إلى رصيدك.")

async def finish_add_credit(update, context, uid, amount):
    if int(amount)<=0 or int(amount)>1000000:
        await update.message.reply_text("❌ قيمة النقاط يجب أن تكون بين 1 و1,000,000."); return ConversationHandler.END
    ok,balance=change_points(int(uid),int(amount),"admin_credit",str(update.effective_user.id))
    if not ok:
        await update.message.reply_text("❌ تعذر إضافة النقاط."); return ConversationHandler.END
    invoice=f"🧾 فاتورة إضافة نقاط\n\n👤 المستخدم: {uid}\n➕ النقاط: {amount}\n💰 الرصيد الجديد: {balance}"
    await update.message.reply_text(invoice)
    try: await context.bot.send_message(int(uid),invoice+"\n\n✅ تم الإيداع بواسطة الإدارة.")
    except Exception: pass
    return ConversationHandler.END

async def add_credits_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_dev(update.effective_user.id): return ConversationHandler.END
    if len(context.args)==2:
        try: return await finish_add_credit(update,context,int(context.args[0]),int(context.args[1]))
        except Exception: await update.message.reply_text("❌ البيانات غير صحيحة."); return ConversationHandler.END
    await update.message.reply_text("👤 أرسل Telegram ID المستخدم:")
    return ASK_CREDIT_USER

async def receive_credit_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_dev(update.effective_user.id): return ConversationHandler.END
    try: context.user_data["credit_uid"]=int(update.message.text.strip())
    except Exception: await update.message.reply_text("❌ أرسل ID رقميًا."); return ASK_CREDIT_USER
    await update.message.reply_text("💰 أرسل عدد النقاط المراد إضافتها:")
    return ASK_CREDIT_AMOUNT

async def receive_credit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_dev(update.effective_user.id): return ConversationHandler.END
    try: amount=int(update.message.text.strip())
    except Exception: await update.message.reply_text("❌ أرسل عددًا صحيحًا."); return ASK_CREDIT_AMOUNT
    return await finish_add_credit(update,context,context.user_data.pop("credit_uid",0),amount)


async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_dev(update.effective_user.id): return
    conn = db(); rows = conn.execute("SELECT telegram_id, username, first_seen, trial_used FROM users ORDER BY first_seen DESC").fetchall(); conn.close()
    if not rows:
        await update.message.reply_text("📭 لا يوجد مستخدمون مسجلون بعد."); return
    text = [f"👥 **مستخدمو Telegram ({len(rows)})**\n"]
    kb = []
    for r in rows[:50]:
        name = f"@{r['username']}" if r['username'] else str(r['telegram_id'])
        text.append(f"• `{r['telegram_id']}` — {name} — {'استعمل التجربة' if r['trial_used'] else 'التجربة متاحة'}")
        kb.append([InlineKeyboardButton(f"🖥️ سيرفرات {name}", callback_data=f"adm_user_{r['telegram_id']}")])
    await update.message.reply_text("\n".join(text), reply_markup=InlineKeyboardMarkup(kb))

def extend_owned_server(sid, days):
    conn=db(); row=conn.execute("SELECT expires_at FROM owned_servers WHERE server_id=?",(int(sid),)).fetchone()
    if not row: conn.close(); return None, "السيرفر غير مسجل في البوت"
    current=datetime.fromisoformat(row["expires_at"]); base=max(current,datetime.now()); expiry=base+timedelta(days=int(days))
    srv, serr=pt.get_server(sid)
    if serr or not srv: conn.close(); return None, serr or "تعذر قراءة السيرفر"
    current_name=srv["attributes"].get("name") or f"server-{sid}"
    current_user=srv["attributes"].get("user")
    if not current_user: conn.close(); return None, "تعذر معرفة مالك السيرفر"
    _, err=pt.update_server_details(sid,{"name":current_name,"user":int(current_user),"description":f"expires_at={expiry.isoformat()}","external_id":expiry.isoformat()})
    if err: conn.close(); return None, err
    conn.execute("UPDATE owned_servers SET expires_at=? WHERE server_id=?",(expiry.isoformat(),int(sid))); conn.commit(); conn.close()
    pt.unsuspend_server(sid); return expiry, None

async def admin_user_servers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    if not is_dev(update.effective_user.id): return
    uid=int(q.data.split("_")[-1]); conn=db(); rows=conn.execute("SELECT server_id,expires_at FROM owned_servers WHERE telegram_id=? ORDER BY created_at DESC",(uid,)).fetchall(); conn.close()
    if not rows: await q.edit_message_text(f"📭 لا توجد سيرفرات للمستخدم `{uid}`.", parse_mode="Markdown"); return
    lines=[f"🖥️ **سيرفرات المستخدم** `{uid}`\n"]
    for r in rows:
        left=max(0,(datetime.fromisoformat(r['expires_at'])-datetime.now()).days)
        lines.append(f"• السيرفر `{r['server_id']}` — متبقي `{left}` يوم — ينتهي `{r['expires_at'][:16].replace('T',' ')}`")
    kb=[[InlineKeyboardButton(f"➕ 7 أيام للسيرفر {r['server_id']}",callback_data=f"adm_extend_{r['server_id']}_7"), InlineKeyboardButton(f"➕ 30 يومًا",callback_data=f"adm_extend_{r['server_id']}_30")] for r in rows]
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_dev(update.effective_user.id): return ConversationHandler.END
    await update.message.reply_text("📣 أرسل نص الإذاعة الآن. اكتب /cancel للإلغاء.")
    return ASK_BROADCAST

async def receive_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_dev(update.effective_user.id): return ConversationHandler.END
    conn=db(); ids=[r[0] for r in conn.execute("SELECT telegram_id FROM users").fetchall()]; conn.close()
    ok=bad=0
    for uid in ids:
        try:
            await context.bot.send_message(uid, update.message.text)
            ok += 1
            await asyncio.sleep(0.05)
        except Exception as exc:
            bad += 1; log.warning("broadcast failed for %s: %s", uid, exc)
    await update.message.reply_text(f"📣 اكتملت الإذاعة\n✅ تم الإرسال: `{ok}`\n❌ فشل: `{bad}`", parse_mode="Markdown")
    return ConversationHandler.END

async def admin_plan_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_dev(update.effective_user.id): return ConversationHandler.END
    p=free_plan()
    await update.message.reply_text(f"⚙️ الخطة الحالية: {p['days']} يوم | RAM {p['memory']}MB | Disk {p['disk']}MB | CPU {p['cpu']}%\n\nأرسل بالشكل:\n`days=7 ram=1024 disk=2048 cpu=100`", parse_mode="Markdown")
    return ASK_PLAN

async def receive_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_dev(update.effective_user.id): return ConversationHandler.END
    import re
    vals={k:int(v) for k,v in re.findall(r"(days|ram|disk|cpu)\s*=\s*(\d+)", update.message.text.lower())}
    if set(vals) != {"days","ram","disk","cpu"} or any(v < 0 for v in vals.values()) or vals["days"] == 0:
        await update.message.reply_text("❌ الصيغة غير صحيحة. مثال: `days=7 ram=1024 disk=2048 cpu=100`", parse_mode="Markdown"); return ASK_PLAN
    for k,v in vals.items(): set_setting("free_" + ("memory_mb" if k=="ram" else "disk_mb" if k=="disk" else k), v)
    p=free_plan(); await update.message.reply_text(f"✅ تم تحديث الخطة المجانية:\n📅 {p['days']} يوم\n💾 RAM {p['memory']}MB\n💿 Disk {p['disk']}MB\n⚡ CPU {p['cpu']}%")
    return ConversationHandler.END

async def daily_notifications_loop(application):
    while True:
        try:
            conn=db(); rows=conn.execute("SELECT telegram_id,server_id,expires_at FROM owned_servers").fetchall()
            today=datetime.now().date().isoformat()
            for r in rows:
                left=(datetime.fromisoformat(r['expires_at'])-datetime.now()).total_seconds()
                days=max(0,int(left//86400))
                if days <= 0: status_text="⛔ انتهت الصلاحية وسيتم إيقاف السيرفر."
                elif days <= 3: status_text=f"⚠️ متبقي {days} يوم فقط — تواصل مع {config.DEVELOPER_USERNAME} للتجديد."
                else: status_text=f"✅ متبقي {days} يوم."
                exists=conn.execute("SELECT 1 FROM notification_log WHERE telegram_id=? AND server_id=? AND day=?",(r['telegram_id'],r['server_id'],today)).fetchone()
                if not exists:
                    try:
                        await application.bot.send_message(r['telegram_id'], f"🔔 تحديث السيرفر `{r['server_id']}`\n{status_text}\n📅 الانتهاء: `{r['expires_at'][:16].replace('T',' ')}`", parse_mode="Markdown")
                        conn.execute("INSERT OR IGNORE INTO notification_log VALUES (?,?,?)",(r['telegram_id'],r['server_id'],today)); conn.commit()
                    except Exception as exc: log.warning("notification failed for %s: %s",r['telegram_id'],exc)
                if days <= 1:
                    conn.execute("INSERT OR IGNORE INTO backup_requests VALUES (?,?,?)",(r['server_id'],datetime.now().isoformat(),"pending")); conn.commit()
            conn.close()
        except Exception as exc: log.error("daily notification loop failed: %s",exc)
        await asyncio.sleep(config.TRIAL_CHECK_INTERVAL_SECONDS)

async def admin_extend_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    if not is_dev(update.effective_user.id): return
    _,_,sid,days=q.data.split("_")
    expiry,err=extend_owned_server(sid,int(days))
    await q.edit_message_text((f"✅ تم تمديد السيرفر `{sid}` حتى `{expiry.strftime('%Y-%m-%d %H:%M')}`" if not err else f"❌ {err}"),parse_mode="Markdown")

async def expiry_loop():
    while True:
        try:
            conn = db(); rows = conn.execute("SELECT server_id, expires_at FROM owned_servers").fetchall(); conn.close()
            now = datetime.now()
            for row in rows:
                try:
                    if datetime.fromisoformat(row["expires_at"]) <= now:
                        _, err = pt.suspend_server(row["server_id"])
                        if not err: log.info("⏳ تم إيقاف السيرفر المنتهي %s", row["server_id"])
                except Exception as exc:
                    log.error("expiry check failed for %s: %s", row["server_id"], exc)
        except Exception as exc:
            log.error("expiry loop failed: %s", exc)
        await asyncio.sleep(config.TRIAL_CHECK_INTERVAL_SECONDS)

async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    routes = {
        "🖥️ إنشاء سيرفر": create_start,
        "📋 سيرفراتي": servers_list,
        "✅ حالة البوت": status,
        "💳 الاشتراك": subscribe_cmd,
        "📊 الإحصائيات": stats,
        "🗂️ كل السيرفرات": servers_list,
        "⛔ إيقاف الكل": suspend_all,
        "▶️ تشغيل الكل": unsuspend_all,
        "👥 المستخدمون": admin_users,
        "📣 إذاعة": admin_broadcast_start,
        "⚙️ الخطة المجانية": admin_plan_start,
        "👤 ملفي": profile,
        "🛒 شراء بالنقاط": buy_start,
        "💰 تحويل نقاط": transfer_start,
        "🎁 كود هدية": gift_start,
    }
    handler = routes.get(update.message.text)
    if handler:
        await handler(update, context)

def main():
    db()
    if not load_eggs():
        log.warning("⚠️ فشل اكتشاف البيض — ستُعاد عند /create")
    app = Application.builder().token(config.BOT_TOKEN).build()
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("create", create_start),
                      MessageHandler(filters.Regex("^🖥️ إنشاء سيرفر$"), create_start)],
        states={CHOOSE_TYPE: [CallbackQueryHandler(choose_type, pattern="^(type_|plan_|cancel)")],
                ENTER_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_email)],
                ADMIN_CHOOSE_TYPE: [CallbackQueryHandler(admin_choose_type, pattern="^admin_(type_|cancel)")],
                ADMIN_CHOOSE_RAM: [CallbackQueryHandler(admin_choose_ram, pattern="^admin_(ram_|back_type)")],
                ADMIN_CHOOSE_DISK: [CallbackQueryHandler(admin_choose_disk, pattern="^admin_(disk_|back_ram)")],
                ADMIN_CHOOSE_CPU: [CallbackQueryHandler(admin_choose_cpu, pattern="^admin_(cpu_|back_disk)")],
                ADMIN_ENTER_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_receive_email)]},
        fallbacks=[CommandHandler("cancel", cancel)]))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("broadcast", admin_broadcast_start), MessageHandler(filters.Regex("^📣 إذاعة$"), admin_broadcast_start)],
        states={ASK_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_broadcast)]},
        fallbacks=[CommandHandler("cancel", cancel)]))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("freeplan", admin_plan_start), MessageHandler(filters.Regex("^⚙️ الخطة المجانية$"), admin_plan_start)],
        states={ASK_PLAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_plan)]},
        fallbacks=[CommandHandler("cancel", cancel)]))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("transfer", transfer_start), MessageHandler(filters.Regex("^💰 تحويل نقاط$"), transfer_start)],
        states={ASK_TRANSFER_USER:[MessageHandler(filters.TEXT & ~filters.COMMAND, receive_transfer_user)], ASK_TRANSFER_AMOUNT:[MessageHandler(filters.TEXT & ~filters.COMMAND, receive_transfer_amount)]},
        fallbacks=[CommandHandler("cancel", cancel)]))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("gift", gift_start), MessageHandler(filters.Regex("^🎁 كود هدية$"), gift_start)],
        states={ASK_GIFT_CODE:[MessageHandler(filters.TEXT & ~filters.COMMAND, receive_gift_code)], ASK_GIFT_SPEC:[MessageHandler(filters.TEXT & ~filters.COMMAND, receive_gift_spec)]},
        fallbacks=[CommandHandler("cancel", cancel)]))
    app.add_handler(CommandHandler("users", admin_users))
    app.add_handler(CallbackQueryHandler(admin_user_servers, pattern="^adm_user_"))
    app.add_handler(CallbackQueryHandler(admin_extend_cb, pattern="^adm_extend_"))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("chpass", chpass_cmd)],
        states={ASK_EMAIL_FOR_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_email_for_pass)],
                ASK_PASS_NEW: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_pass_new)]},
        fallbacks=[CommandHandler("cancel", cancel)]))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(server_action, pattern="^act_chpass_")],
        states={ASK_NEW_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_pass)]},
        fallbacks=[CommandHandler("cancel", cancel)]))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("subscribe", subscribe_cmd))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("servers", servers_list))
    app.add_handler(CommandHandler("renew", renew_cmd))
    app.add_handler(CommandHandler("changegg", changegg_cmd))
    app.add_handler(CommandHandler("suspendall", suspend_all))
    app.add_handler(CommandHandler("unsuspendall", unsuspend_all))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("addcredits", add_credits_start), CommandHandler("add_credits", add_credits_start)],
        states={ASK_CREDIT_USER:[MessageHandler(filters.TEXT & ~filters.COMMAND, receive_credit_user)], ASK_CREDIT_AMOUNT:[MessageHandler(filters.TEXT & ~filters.COMMAND, receive_credit_amount)]},
        fallbacks=[CommandHandler("cancel", cancel)]))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^(🖥️ إنشاء سيرفر|🛒 شراء بالنقاط|📋 سيرفراتي|👤 ملفي|💰 تحويل نقاط|🎁 كود هدية|✅ حالة البوت|💳 الاشتراك|📊 الإحصائيات|🗂️ كل السيرفرات|👥 المستخدمون|📣 إذاعة|⚙️ الخطة المجانية|⛔ إيقاف الكل|▶️ تشغيل الكل)$"), menu_router))
    app.add_handler(CallbackQueryHandler(paginate, pattern="^pg_"))
    app.add_handler(CallbackQueryHandler(server_data_cb, pattern="^data_"))
    app.add_handler(CallbackQueryHandler(manage, pattern="^mng_"))
    app.add_handler(CallbackQueryHandler(set_egg_cb, pattern="^setegg_"))
    app.add_handler(CallbackQueryHandler(upgrade_plan_cb, pattern="^upgrade_"))
    app.add_handler(CallbackQueryHandler(confirm_delete, pattern="^confdel_"))
    app.add_handler(CallbackQueryHandler(back_servers, pattern="^back_servers$"))
    app.add_handler(CallbackQueryHandler(server_action, pattern="^act_"))
    async def after_start(application):
        await application.bot.set_my_commands([
            BotCommand("start", "القائمة الرئيسية"), BotCommand("create", "إنشاء سيرفر"),
            BotCommand("servers", "سيرفراتي"), BotCommand("status", "حالة البوت"),
            BotCommand("subscribe", "الاشتراك عند المطور"), BotCommand("profile", "ملفي ورصيدي"),
            BotCommand("transfer", "تحويل نقاط"),
        ], scope=BotCommandScopeDefault())
        await application.bot.set_my_commands([
            BotCommand("users", "مستخدمو Telegram"), BotCommand("broadcast", "إذاعة"),
            BotCommand("freeplan", "الخطة المجانية"), BotCommand("addcredits", "إضافة نقاط"),
            BotCommand("gift", "إنشاء كود هدية"), BotCommand("stats", "الإحصائيات"),
        ], scope=BotCommandScopeChat(chat_id=config.DEVELOPER_ID))
        asyncio.create_task(expiry_loop())
        asyncio.create_task(daily_notifications_loop(application))
    app.post_init = after_start
    log.info("🤖 البوت يعمل...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
