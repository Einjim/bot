import base64
import json
import os
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Config — everything secret comes from environment variables, set in the
# Vercel project dashboard. Nothing sensitive is hardcoded in this file.
# ---------------------------------------------------------------------------
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
SETUP_KEY = os.environ.get("SETUP_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")

DEFAULT_SYSTEM_PROMPT = (
    "You are a trading chart analysis assistant inside a Telegram bot. Users "
    "send you screenshots of price charts, open positions, or trading-platform "
    "screens, then ask questions about them. Read the chart carefully (price "
    "action, visible indicators, support/resistance, trend, volume if shown) "
    "and answer clearly and specifically. You are not a licensed financial "
    "advisor: frame analysis as educational, never as a guaranteed outcome, "
    "and note that trading carries risk when giving a directional opinion. "
    "Keep replies concise and use Telegram-friendly formatting (short "
    "paragraphs, '-' for bullet points, *word* for bold)."
)
SYSTEM_PROMPT = os.environ.get("SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT)

FREE_LIMIT = 1              # free screenshot analyses every new user starts with
REFERRALS_PER_BONUS = 5     # invite this many people -> +1 more free analysis

# Placeholder plans/prices — edit these to whatever you actually want to sell.
PLANS = {
    "basic": {"label": "Basic — 20 chart analyses", "price": "$5", "credits": 20},
    "pro": {"label": "Pro — Unlimited for 30 days", "price": "$15", "days": 30},
}

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
TELEGRAM_FILE_URL = "https://api.telegram.org/file/bot{token}/{path}"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_bot_username_cache = {"value": None}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id BIGINT PRIMARY KEY,
    username TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    referred_by BIGINT REFERENCES users(id),
    referral_count INT NOT NULL DEFAULT 0,
    bonus_credits INT NOT NULL DEFAULT 0,
    free_used INT NOT NULL DEFAULT 0,
    plan TEXT NOT NULL DEFAULT 'free',
    plan_credits_remaining INT,
    plan_active_until TIMESTAMPTZ,
    pending_plan TEXT,
    conversation JSONB NOT NULL DEFAULT '[]'::jsonb
);
"""


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def get_db():
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    conn.autocommit = True
    return conn


def ensure_schema():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
    finally:
        conn.close()


def get_or_create_user(conn, tg_id, username, referrer_id=None):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM users WHERE id = %s", (tg_id,))
        row = cur.fetchone()
        if row:
            if username and row["username"] != username:
                cur.execute("UPDATE users SET username = %s WHERE id = %s", (username, tg_id))
            return row, False
        cur.execute(
            "INSERT INTO users (id, username, referred_by) VALUES (%s, %s, %s) RETURNING *",
            (tg_id, username, referrer_id),
        )
        return cur.fetchone(), True


def register_referral(conn, referrer_id):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "UPDATE users SET referral_count = referral_count + 1 "
            "WHERE id = %s RETURNING referral_count",
            (referrer_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        awarded = row["referral_count"] % REFERRALS_PER_BONUS == 0
        if awarded:
            cur.execute("UPDATE users SET bonus_credits = bonus_credits + 1 WHERE id = %s", (referrer_id,))
        return row["referral_count"], awarded


def save_conversation(conn, user_id, history):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE users SET conversation = %s::jsonb WHERE id = %s",
            (json.dumps(history), user_id),
        )


def apply_plan(conn, user_id, plan_key, plan):
    with conn.cursor() as cur:
        if "days" in plan:
            cur.execute(
                "UPDATE users SET plan = %s, "
                "plan_active_until = now() + (%s || ' days')::interval, "
                "pending_plan = NULL WHERE id = %s",
                (plan_key, plan["days"], user_id),
            )
        else:
            cur.execute(
                "UPDATE users SET plan = %s, "
                "plan_credits_remaining = COALESCE(plan_credits_remaining, 0) + %s, "
                "pending_plan = NULL WHERE id = %s",
                (plan_key, plan.get("credits", 0), user_id),
            )


def can_analyze(user):
    now = datetime.now(timezone.utc)
    if user["plan_active_until"] and user["plan_active_until"] > now:
        return True, "pro"
    if user["plan_credits_remaining"] and user["plan_credits_remaining"] > 0:
        return True, "basic"
    allowed_free = FREE_LIMIT + user["bonus_credits"]
    if user["free_used"] < allowed_free:
        return True, "free"
    return False, None


def record_usage(conn, user_id, tier):
    with conn.cursor() as cur:
        if tier == "free":
            cur.execute("UPDATE users SET free_used = free_used + 1 WHERE id = %s", (user_id,))
        elif tier == "basic":
            cur.execute(
                "UPDATE users SET plan_credits_remaining = plan_credits_remaining - 1 WHERE id = %s",
                (user_id,),
            )
        # "pro" is unlimited until plan_active_until — nothing to decrement.


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------
def tg_call(method, **params):
    url = TELEGRAM_API.format(token=BOT_TOKEN, method=method)
    resp = requests.post(url, json=params, timeout=15)
    return resp.json()


def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_call("sendMessage", **payload)


def answer_callback(callback_id, text=None):
    params = {"callback_query_id": callback_id}
    if text:
        params["text"] = text
    return tg_call("answerCallbackQuery", **params)


def download_photo(file_id):
    info = tg_call("getFile", file_id=file_id)
    if not info.get("ok"):
        return None, None
    file_path = info["result"]["file_path"]
    url = TELEGRAM_FILE_URL.format(token=BOT_TOKEN, path=file_path)
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    mime = "image/png" if file_path.lower().endswith(".png") else "image/jpeg"
    return resp.content, mime


def get_bot_username():
    if not _bot_username_cache["value"]:
        me = tg_call("getMe")
        if me.get("ok"):
            _bot_username_cache["value"] = me["result"]["username"]
    return _bot_username_cache["value"] or ""


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------
def call_gemini(history):
    """history: list of {"role": "user"/"model", "parts": [...]}. Returns (text, error)."""
    body = {"contents": history, "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]}}
    try:
        resp = requests.post(
            GEMINI_URL.format(model=GEMINI_MODEL),
            headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
            json=body,
            timeout=45,
        )
        data = resp.json()
    except requests.RequestException as e:
        return None, f"Couldn't reach Gemini: {e}"

    if isinstance(data, dict) and "error" in data:
        return None, data["error"].get("message", "Gemini returned an error.")

    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
        return (text or "I couldn't find anything to say about that."), None
    except (KeyError, IndexError, TypeError):
        return None, "Gemini didn't return a usable response."


# ---------------------------------------------------------------------------
# Menus
# ---------------------------------------------------------------------------
def main_menu():
    return {
        "inline_keyboard": [
            [{"text": "💳 Plans & Pricing", "callback_data": "menu_plans"}],
            [{"text": "👥 My Referral Link", "callback_data": "menu_referral"}],
            [{"text": "ℹ️ Help", "callback_data": "menu_help"}],
        ]
    }


def plans_menu():
    rows = [[{"text": f"{p['label']} — {p['price']}", "callback_data": f"buy_{key}"}] for key, p in PLANS.items()]
    return {"inline_keyboard": rows}


def reset_button():
    return {"inline_keyboard": [[{"text": "🔄 Start over", "callback_data": "menu_reset"}]]}


# ---------------------------------------------------------------------------
# Message / callback handling
# ---------------------------------------------------------------------------
def send_referral_info(chat_id, user):
    username = get_bot_username()
    link = f"https://t.me/{username}?start=ref_{user['id']}" if username else "(bot username unavailable)"
    send_message(
        chat_id,
        f"👥 *Your referral link:*\n{link}\n\n"
        f"Referrals so far: *{user['referral_count']}*\n"
        f"Bonus free analyses earned: *{user['bonus_credits']}*\n\n"
        f"Every {REFERRALS_PER_BONUS} friends who join = +1 free analysis.",
    )


def handle_photo(conn, chat_id, user, photo_sizes, caption=None):
    allowed, tier = can_analyze(user)
    if not allowed:
        send_message(
            chat_id,
            "🚫 You're out of free analyses. Buy a plan below, or invite "
            f"{REFERRALS_PER_BONUS} friends with your referral link for +1 more free.",
            reply_markup=plans_menu(),
        )
        return

    send_message(chat_id, "🔎 Analyzing your screenshot…")

    file_id = photo_sizes[-1]["file_id"]
    image_bytes, mime = download_photo(file_id)
    if not image_bytes:
        send_message(chat_id, "Couldn't download that image from Telegram — please try sending it again.")
        return

    question = caption.strip() if caption else "Analyze this trading chart/screenshot."
    history = [{
        "role": "user",
        "parts": [
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(image_bytes).decode()}},
            {"text": question},
        ],
    }]

    reply, error = call_gemini(history)
    if error:
        send_message(chat_id, f"⚠️ {error}")
        return

    history.append({"role": "model", "parts": [{"text": reply}]})
    save_conversation(conn, user["id"], history)
    record_usage(conn, user["id"], tier)
    send_message(chat_id, reply, reply_markup=reset_button())


def handle_question(conn, chat_id, user, text):
    # Follow-up questions about an already-analyzed screenshot don't cost
    # another credit — only a *new* screenshot does (see handle_photo).
    history = user.get("conversation") or []
    if not history:
        send_message(
            chat_id,
            "Send me a chart screenshot first, then ask away! Use /plans to see paid options.",
            reply_markup=main_menu(),
        )
        return

    history = history + [{"role": "user", "parts": [{"text": text}]}]
    reply, error = call_gemini(history)
    if error:
        send_message(chat_id, f"⚠️ {error}")
        return

    history.append({"role": "model", "parts": [{"text": reply}]})
    save_conversation(conn, user["id"], history)
    send_message(chat_id, reply)


def handle_message(conn, message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    tg_id = from_user.get("id", chat_id)
    username = from_user.get("username") or from_user.get("first_name")
    text = message.get("text", "") or ""

    referrer_id = None
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        if len(parts) > 1 and parts[1].startswith("ref_"):
            try:
                candidate = int(parts[1][4:])
                if candidate != tg_id:
                    referrer_id = candidate
            except ValueError:
                pass

    user, is_new = get_or_create_user(conn, tg_id, username, referrer_id)

    if is_new and referrer_id:
        result = register_referral(conn, referrer_id)
        if result:
            count, awarded = result
            note = f"🎉 Someone joined using your link! You've referred {count}."
            if awarded:
                note += f" You just earned +1 free analysis (every {REFERRALS_PER_BONUS} counts)."
            send_message(referrer_id, note)

    if text.startswith("/start"):
        remaining = max(0, FREE_LIMIT + user["bonus_credits"] - user["free_used"])
        send_message(
            chat_id,
            "👋 *Welcome!* Send me a screenshot of a chart or trade and I'll "
            "analyze it — ask follow-up questions right after.\n\n"
            f"You have *{remaining}* free analysis(es) left.",
            reply_markup=main_menu(),
        )
        return

    if text.startswith("/plans"):
        send_message(chat_id, "Choose a plan:", reply_markup=plans_menu())
        return

    if text.startswith("/referral"):
        send_referral_info(chat_id, user)
        return

    if message.get("photo"):
        handle_photo(conn, chat_id, user, message["photo"], message.get("caption"))
        return

    if text:
        handle_question(conn, chat_id, user, text)
        return

    send_message(chat_id, "Send me a chart screenshot to get started, or use /plans to see paid options.")


def handle_callback(conn, callback):
    data = callback.get("data", "")
    message = callback.get("message", {}) or {}
    chat_id = message.get("chat", {}).get("id")
    from_user = callback.get("from", {})
    tg_id = from_user.get("id", chat_id)
    username = from_user.get("username") or from_user.get("first_name")

    user, _ = get_or_create_user(conn, tg_id, username)

    if data == "menu_plans":
        answer_callback(callback["id"])
        send_message(chat_id, "Choose a plan:", reply_markup=plans_menu())
        return

    if data == "menu_referral":
        answer_callback(callback["id"])
        send_referral_info(chat_id, user)
        return

    if data == "menu_help":
        answer_callback(callback["id"])
        send_message(
            chat_id,
            "Send a chart screenshot any time and I'll analyze it. Ask follow-up "
            "questions right after. Use /plans to see paid tiers.",
            reply_markup=main_menu(),
        )
        return

    if data == "menu_reset":
        answer_callback(callback["id"], "Cleared")
        save_conversation(conn, user["id"], [])
        send_message(chat_id, "Cleared — send a new screenshot whenever you're ready.")
        return

    if data.startswith("buy_"):
        plan_key = data[len("buy_"):]
        plan = PLANS.get(plan_key)
        if not plan:
            answer_callback(callback["id"], "Unknown plan")
            return
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET pending_plan = %s WHERE id = %s", (plan_key, user["id"]))
        answer_callback(callback["id"], "Request sent!")
        send_message(
            chat_id,
            f"You've requested *{plan['label']}* ({plan['price']}).\n\n"
            "Payment isn't wired up to a processor yet — for now, send payment "
            "using [your payment details here] and your plan will be applied "
            "once confirmed.",
        )
        if ADMIN_CHAT_ID:
            send_message(
                ADMIN_CHAT_ID,
                f"💰 Plan request: user `{tg_id}` (@{username}) wants *{plan['label']}*.",
                reply_markup={"inline_keyboard": [[
                    {"text": "✅ Approve", "callback_data": f"approve_{tg_id}_{plan_key}"},
                ]]},
            )
        return

    if data.startswith("approve_"):
        if not ADMIN_CHAT_ID or str(chat_id) != str(ADMIN_CHAT_ID):
            answer_callback(callback["id"], "Not authorized")
            return
        _, target_id, plan_key = data.split("_", 2)
        plan = PLANS.get(plan_key)
        if not plan:
            answer_callback(callback["id"], "Unknown plan")
            return
        apply_plan(conn, int(target_id), plan_key, plan)
        answer_callback(callback["id"], "Approved")
        send_message(chat_id, f"Approved {target_id} for {plan['label']}.")
        send_message(int(target_id), f"✅ You're upgraded to *{plan['label']}*! Send a screenshot whenever you're ready.")
        return

    answer_callback(callback["id"])


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return jsonify(
        status="ok",
        service="AI trading chart bot",
        configured={
            "TELEGRAM_BOT_TOKEN": bool(BOT_TOKEN),
            "DATABASE_URL": bool(DATABASE_URL),
            "GEMINI_API_KEY": bool(GEMINI_API_KEY),
            "WEBHOOK_SECRET": bool(WEBHOOK_SECRET),
        },
    )


@app.route("/api/setup")
def setup():
    if not SETUP_KEY or request.args.get("key") != SETUP_KEY:
        return jsonify(ok=False, error="Missing or invalid setup key"), 403
    if not BOT_TOKEN or not DATABASE_URL:
        return jsonify(ok=False, error="TELEGRAM_BOT_TOKEN and/or DATABASE_URL not set"), 400

    ensure_schema()
    webhook_url = f"https://{request.host}/api/webhook"
    result = tg_call("setWebhook", url=webhook_url, secret_token=WEBHOOK_SECRET, drop_pending_updates=True)
    return jsonify(ok=result.get("ok", False), telegram_response=result, webhook_url=webhook_url)


@app.route("/api/webhook", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return jsonify(ok=False), 403

    update = request.get_json(silent=True) or {}
    try:
        conn = get_db()
        try:
            if "message" in update:
                handle_message(conn, update["message"])
            elif "callback_query" in update:
                handle_callback(conn, update["callback_query"])
        finally:
            conn.close()
    except Exception as e:
        print("webhook error:", e)

    # Always 200 so Telegram doesn't retry-storm this update.
    return jsonify(ok=True), 200


if __name__ == "__main__":
    app.run(debug=True)
