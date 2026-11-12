"""
Muse 邀请码互刷 Telegram Bot
================================
功能：两个新用户互相交换邀请码，各自去 Muse 里兑换对方的码。

核心流程
--------
1. /start 注册
2. /submit <你的邀请码> 提交自己的码（每人只能 1 个）
3. /match 进入配对队列，Bot 按先进先出配对两个人
4. 配对成功后，双方同时收到对方的码 + 兑换教程
5. 各自兑换后点「我已兑换 ✅」，双方都确认 = 互刷成功，信誉 +1
6. 点「对方的码无效 ❌」，对方的码会被清空并记一次举报，对方需重新提交

重要限制（来自 Muse 官方规则，Bot 无法绕过）
----------------------------------------------
- 兑换入口只在加入 Muse 后的前 48 小时内出现：
  手机 App：设置 > Redeem token
  网页 muse.ai：设置 > General > Usage > Redeem invite code
- 超过 48 小时、已兑换过、码已被用完 / 被撤销，都会兑换失败。
  所以这个 Bot 只对「刚加入 48 小时内、还没兑换过」的人有用。
- Muse 没有公开 API 可以自动验证一个码是否有效、是否已兑换，
  所以确认全靠双方诚信 + 举报机制，Bot 不能保证 100% 成功。

运行方式：python bot.py（需要先在 .env 里填 TELEGRAM_BOT_TOKEN）
依赖：python-telegram-bot>=21,<22
数据库：SQLite，默认 invite_bot.db，零配置。
"""

import logging
import os
import re
import sqlite3
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "invite_bot.db")
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()
}
# 一个码被 2 个人举报就自动封禁（防刷假码）
REPORTS_TO_BAN = int(os.getenv("REPORTS_TO_BAN", "2"))

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logger = logging.getLogger("muse-invite-bot")

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{4,32}$")

REDEEM_GUIDE = (
    "📱 手机 App：打开 Muse > 设置 > Redeem token，输入对方的码\n"
    "💻 网页：muse.ai > 设置 > General > Usage > Redeem invite code\n"
    "⏰ 注意：只有加入后 48 小时内、还没兑换过的人才能兑换。"
)


# ---------------------------------------------------------------- 数据库
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                invite_code TEXT,
                status TEXT NOT NULL DEFAULT 'idle',
                -- idle / queued / paired / banned
                pair_id INTEGER,
                successful_swaps INTEGER NOT NULL DEFAULT 0,
                reports_received INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pairs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_a INTEGER NOT NULL,
                user_b INTEGER NOT NULL,
                code_a TEXT NOT NULL,
                code_b TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                -- active / completed / disputed / cancelled
                confirmed_a INTEGER NOT NULL DEFAULT 0,
                confirmed_b INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            """
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_user(tg_id: int):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (tg_id,)
        ).fetchone()


def upsert_user(user):
    with db() as conn:
        conn.execute(
            """
            INSERT INTO users (telegram_id, username, invite_code, status, created_at, updated_at)
            VALUES (?, ?, NULL, 'idle', ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username, updated_at = excluded.updated_at
            """,
            (user.id, user.username or user.full_name, now_iso(), now_iso()),
        )


def set_status(tg_id: int, status: str, pair_id=None):
    with db() as conn:
        conn.execute(
            "UPDATE users SET status = ?, pair_id = ?, updated_at = ? WHERE telegram_id = ?",
            (status, pair_id, now_iso(), tg_id),
        )


# ---------------------------------------------------------------- 通用
def is_banned(user_row) -> bool:
    return bool(user_row and user_row["status"] == "banned")


async def reply_or_edit(update: Update, text: str, reply_markup=None):
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)


# ---------------------------------------------------------------- 命令
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    await update.message.reply_text(
        "👋 欢迎来到 Muse 邀请码互刷 Bot\n\n"
        "玩法很简单：你把你的邀请码交出来，Bot 帮你配对另一个人，\n"
        "你们互相去 Muse 里兑换对方的码，各得奖励。\n\n"
        "1️⃣ /submit 你的邀请码 — 例：/submit ABC123\n"
        "2️⃣ /match 开始配对\n"
        "3️⃣ 收到对方的码后去兑换，回来点「我已兑换 ✅」\n\n"
        "⚠️ 先看清楚：\n"
        f"{REDEEM_GUIDE}\n"
        "超过 48 小时、已兑换过的人兑换会失败，配对也是白配。\n\n"
        "其他命令：/status /cancel /rules /help"
    )


async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📜 互刷规则\n\n"
        "• 每人只能提交 1 个邀请码，换码先用 /submit 重新提交\n"
        "• 每次只能有 1 个进行中的配对，完成后才能再 /match\n"
        "• 配对后 24 小时内不确认，配对自动作废（由管理员 / 对方举报处理）\n"
        "• 对方码无效请点「对方的码无效 ❌」，不要直接拉黑跑路\n"
        "• 被 2 人举报码无效会被封禁，申诉请联系管理员\n"
        "• Bot 无法验证 Muse 的码是否真实有效，兑换结果以 Muse  App / 网页显示为准\n\n"
        f"{REDEEM_GUIDE}"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start — 开始\n"
        "/submit <邀请码> — 提交 / 更换你的邀请码\n"
        "/mycode — 查看我的邀请码\n"
        "/match — 进入配对队列\n"
        "/status — 我的状态和互刷成功次数\n"
        "/cancel — 退出队列 / 取消当前配对\n"
        "/rules — 规则和兑换方法\n"
        "/stats — 全站统计"
    )


async def submit_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user)
    row = get_user(user.id)
    if is_banned(row):
        await update.message.reply_text("⛔ 你已被封禁，请联系管理员申诉。")
        return
    if row["status"] == "paired":
        await update.message.reply_text("你正在配对中，先完成或 /cancel 当前配对再换码。")
        return
    if not context.args:
        await update.message.reply_text("用法：/submit 你的邀请码\n例：/submit ABC123")
        return

    code = context.args[0].strip()
    if not CODE_RE.match(code):
        await update.message.reply_text(
            "邀请码格式不对：只接受 4-32 位的字母、数字、_、-。\n"
            "请直接复制你 Muse 邀请页显示的码，不要自己编一个。"
        )
        return

    # 一个码只能被一个人提交
    with db() as conn:
        clash = conn.execute(
            "SELECT telegram_id FROM users WHERE invite_code = ? AND telegram_id != ?",
            (code, user.id),
        ).fetchone()
        if clash:
            await update.message.reply_text("这个码已经被别人提交过了，请确认是你自己的码。")
            return
        conn.execute(
            "UPDATE users SET invite_code = ?, updated_at = ? WHERE telegram_id = ?",
            (code, now_iso(), user.id),
        )
    await update.message.reply_text(
        f"✅ 已保存你的邀请码：`{code}`\n现在可以 /match 开始配对了。",
        parse_mode="Markdown",
    )


async def mycode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row = get_user(update.effective_user.id)
    if not row or not row["invite_code"]:
        await update.message.reply_text("你还没提交邀请码：/submit 你的邀请码")
        return
    await update.message.reply_text(
        f"你的邀请码：`{row['invite_code']}`\n状态：{row['status']} | 成功互刷：{row['successful_swaps']} 次",
        parse_mode="Markdown",
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row = get_user(update.effective_user.id)
    if not row:
        await update.message.reply_text("请先 /start")
        return
    await update.message.reply_text(
        f"👤 @{row['username'] or update.effective_user.id}\n"
        f"邀请码：{row['invite_code'] or '未提交'}\n"
        f"状态：{row['status']}\n"
        f"成功互刷：{row['successful_swaps']} 次\n"
        f"被举报：{row['reports_received']} 次"
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        queued = conn.execute(
            "SELECT COUNT(*) FROM users WHERE status='queued'"
        ).fetchone()[0]
        completed = conn.execute(
            "SELECT COUNT(*) FROM pairs WHERE status='completed'"
        ).fetchone()[0]
    await update.message.reply_text(
        f"📊 全站统计\n注册用户：{total_users}\n排队中：{queued}\n成功互刷：{completed} 对"
    )


# ---------------------------------------------------------------- 配对
async def try_match(context: ContextTypes.DEFAULT_TYPE):
    """从队列里取最先排队的 2 个人配对。返回配对 id，没有则返回 None。"""
    with db() as conn:
        queued = conn.execute(
            "SELECT * FROM users WHERE status='queued' AND invite_code IS NOT NULL "
            "ORDER BY updated_at ASC LIMIT 2"
        ).fetchall()
        if len(queued) < 2:
            return None
        a, b = queued[0], queued[1]
        cur = conn.execute(
            "INSERT INTO pairs (user_a, user_b, code_a, code_b, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (a["telegram_id"], b["telegram_id"], a["invite_code"], b["invite_code"], now_iso()),
        )
        pair_id = cur.lastrowid
        conn.execute(
            "UPDATE users SET status='paired', pair_id=? WHERE telegram_id IN (?, ?)",
            (pair_id, a["telegram_id"], b["telegram_id"]),
        )

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ 我已兑换", callback_data=f"confirm:{pair_id}")],
            [InlineKeyboardButton("❌ 对方的码无效", callback_data=f"invalid:{pair_id}")],
        ]
    )
    # 给 A 发 B 的码，给 B 发 A 的码
    await context.bot.send_message(
        a["telegram_id"],
        f"🎉 配对成功！\n\n对方的 Muse 邀请码：`{b['invite_code']}`\n"
        f"对方：@{b['username'] or b['telegram_id']}\n\n"
        f"{REDEEM_GUIDE}\n兑换完成后回来点下面的按钮：",
        parse_mode="Markdown",
        reply_markup=kb,
    )
    await context.bot.send_message(
        b["telegram_id"],
        f"🎉 配对成功！\n\n对方的 Muse 邀请码：`{a['invite_code']}`\n"
        f"对方：@{a['username'] or a['telegram_id']}\n\n"
        f"{REDEEM_GUIDE}\n兑换完成后回来点下面的按钮：",
        parse_mode="Markdown",
        reply_markup=kb,
    )
    logger.info("paired %s <-> %s (pair %s)", a["telegram_id"], b["telegram_id"], pair_id)
    return pair_id


async def match_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user)
    row = get_user(user.id)
    if is_banned(row):
        await update.message.reply_text("⛔ 你已被封禁，请联系管理员申诉。")
        return
    if not row["invite_code"]:
        await update.message.reply_text("先提交你的邀请码：/submit 你的邀请码")
        return
    if row["status"] == "paired":
        await update.message.reply_text("你已经在配对中了，兑换完请点「我已兑换 ✅」。")
        return
    if row["status"] == "queued":
        await update.message.reply_text("你已经在排队了，耐心等一下，有人来就自动配对。")
        return

    set_status(user.id, "queued")
    pair_id = await try_match(context)
    if pair_id:
        return  # try_match 里已经给双方发了消息
    await update.message.reply_text(
        "✅ 已进入配对队列。\n有人提交并 /match 后会自动配对你，我会第一时间发对方的码给你。\n"
        "不想等了可以 /cancel 退出。"
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row = get_user(update.effective_user.id)
    if not row:
        await update.message.reply_text("请先 /start")
        return
    if row["status"] == "queued":
        set_status(update.effective_user.id, "idle")
        await update.message.reply_text("已退出排队。想再配对随时 /match。")
        return
    if row["status"] == "paired" and row["pair_id"]:
        pair_id = row["pair_id"]
        with db() as conn:
            pair = conn.execute("SELECT * FROM pairs WHERE id=?", (pair_id,)).fetchone()
            if pair and pair["status"] == "active":
                other = pair["user_b"] if pair["user_a"] == update.effective_user.id else pair["user_a"]
                conn.execute("UPDATE pairs SET status='cancelled' WHERE id=?", (pair_id,))
                conn.execute(
                    "UPDATE users SET status='idle', pair_id=NULL WHERE telegram_id IN (?, ?)",
                    (pair["user_a"], pair["user_b"]),
                )
                await update.message.reply_text("已取消当前配对。")
                try:
                    await context.bot.send_message(
                        other, "对方取消了本次配对，你可以重新 /match 排队。"
                    )
                except Exception:
                    pass
                return
    await update.message.reply_text("你现在没有排队或配对，无需取消。")


# ---------------------------------------------------------------- 确认 / 举报
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action, pair_id_s = query.data.split(":")
    pair_id = int(pair_id_s)
    me = update.effective_user.id

    with db() as conn:
        pair = conn.execute("SELECT * FROM pairs WHERE id=?", (pair_id,)).fetchone()
    if not pair or pair["status"] != "active":
        await query.edit_message_text("这个配对已经结束了，请重新 /match。")
        return
    if me not in (pair["user_a"], pair["user_b"]):
        await query.edit_message_text("这不是你的配对。")
        return

    other = pair["user_b"] if me == pair["user_a"] else pair["user_a"]
    is_a = me == pair["user_a"]

    if action == "confirm":
        with db() as conn:
            if is_a:
                conn.execute("UPDATE pairs SET confirmed_a=1 WHERE id=?", (pair_id,))
            else:
                conn.execute("UPDATE pairs SET confirmed_b=1 WHERE id=?", (pair_id,))
            pair = conn.execute("SELECT * FROM pairs WHERE id=?", (pair_id,)).fetchone()
            if pair["confirmed_a"] and pair["confirmed_b"]:
                conn.execute("UPDATE pairs SET status='completed' WHERE id=?", (pair_id,))
                conn.execute(
                    "UPDATE users SET status='idle', pair_id=NULL, "
                    "successful_swaps=successful_swaps+1 WHERE telegram_id IN (?, ?)",
                    (pair["user_a"], pair["user_b"]),
                )
        if pair["confirmed_a"] and pair["confirmed_b"]:
            await query.edit_message_text("🎉 双方都确认了，互刷成功！信誉 +1，可以再 /match 下一轮。")
            try:
                await context.bot.send_message(
                    other, "🎉 对方也确认了，本次互刷成功！信誉 +1，可以再 /match 下一轮。"
                )
            except Exception:
                pass
        else:
            await query.edit_message_text("✅ 已记录你的确认，等对方也确认就完成互刷。")
            try:
                await context.bot.send_message(other, "对方已确认兑换，还差你点一下「我已兑换 ✅」。")
            except Exception:
                pass
        return

    if action == "invalid":
        # 举报对方的码无效：清空对方的码、记一次举报，举报人回队列
        with db() as conn:
            conn.execute("UPDATE pairs SET status='disputed' WHERE id=?", (pair_id,))
            conn.execute(
                "UPDATE users SET invite_code=NULL, reports_received=reports_received+1, "
                "status='idle', pair_id=NULL WHERE telegram_id=?",
                (other,),
            )
            conn.execute(
                "UPDATE users SET status='queued', pair_id=NULL WHERE telegram_id=?", (me,)
            )
            reported = conn.execute(
                "SELECT * FROM users WHERE telegram_id=?", (other,)
            ).fetchone()
            if reported and reported["reports_received"] >= REPORTS_TO_BAN:
                conn.execute(
                    "UPDATE users SET status='banned' WHERE telegram_id=?", (other,)
                )
        await query.edit_message_text(
            "已举报对方的码无效，你已重新进入排队，下一位配对对象会尽快发给你。"
        )
        try:
            await context.bot.send_message(
                other,
                "⚠️ 你的邀请码被对方举报无效，已被清空。\n"
                "请确认是 Muse 邀请页显示的码，重新 /submit 后再 /match。\n"
                f"被举报 {REPORTS_TO_BAN} 次会被封禁。",
            )
        except Exception:
            pass
        # 举报人已回队列，尝试再配对
        await try_match(context)
        return


# ---------------------------------------------------------------- 管理员
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("这个命令只有管理员能用。")
        return
    await stats(update, context)


async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("这个命令只有管理员能用。")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("用法：/ban <telegram_id>")
        return
    set_status(int(context.args[0]), "banned")
    await update.message.reply_text(f"已封禁 {context.args[0]}")


def main():
    if not TOKEN:
        raise SystemExit("请先设置 TELEGRAM_BOT_TOKEN（见 .env.example）")
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("rules", rules))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("submit", submit_code))
    app.add_handler(CommandHandler("mycode", mycode))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("match", match_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("admin_stats", admin_stats))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CallbackQueryHandler(on_button, pattern=r"^(confirm|invalid):\d+$"))
    logger.info("Bot started, polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
