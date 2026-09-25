import re

import requests
from flask import Flask, request, render_template_string

app = Flask(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
REPLY_TEXT = "yes"

# A Telegram bot token always looks like  <digits>:<35 alnum/_- chars>
TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")

PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Telegram Auto-Reply Bot</title>
<style>
  :root {
    --tg-blue: #229ED9;
    --tg-blue-dark: #1b87ba;
    --bg: #f4f7f9;
    --card: #ffffff;
    --text: #1c2733;
    --muted: #6b7a89;
    --border: #e2e8ee;
    --green: #1f9d55;
    --red: #d1453b;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--bg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: var(--text);
    padding: 24px;
  }
  .card {
    width: 100%;
    max-width: 440px;
    background: var(--card);
    border-radius: 16px;
    box-shadow: 0 10px 30px rgba(20, 40, 60, 0.08);
    padding: 32px 28px;
  }
  .logo {
    width: 48px;
    height: 48px;
    border-radius: 12px;
    background: var(--tg-blue);
    display: flex;
    align-items: center;
    justify-content: center;
    margin-bottom: 16px;
  }
  .logo svg { width: 26px; height: 26px; fill: #fff; }
  h1 { font-size: 20px; margin: 0 0 6px; }
  p.sub { color: var(--muted); font-size: 14px; margin: 0 0 24px; line-height: 1.5; }
  label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 6px; }
  input[type=text] {
    width: 100%;
    padding: 12px 14px;
    border: 1px solid var(--border);
    border-radius: 10px;
    font-size: 14px;
    margin-bottom: 6px;
    outline: none;
    transition: border-color .15s;
  }
  input[type=text]:focus { border-color: var(--tg-blue); }
  .hint { color: var(--muted); font-size: 12px; margin-bottom: 20px; }
  .hint a { color: var(--tg-blue); text-decoration: none; }
  button {
    width: 100%;
    padding: 12px 14px;
    border: none;
    border-radius: 10px;
    background: var(--tg-blue);
    color: #fff;
    font-size: 15px;
    font-weight: 600;
    cursor: pointer;
    transition: background .15s;
  }
  button:hover { background: var(--tg-blue-dark); }
  .banner {
    border-radius: 10px;
    padding: 12px 14px;
    font-size: 13.5px;
    line-height: 1.5;
    margin-bottom: 20px;
  }
  .banner.ok { background: #e9f9ef; color: var(--green); border: 1px solid #bfe9cf; }
  .banner.err { background: #fdecea; color: var(--red); border: 1px solid #f6c6c2; }
  .bot-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 14px;
    padding-top: 14px;
    border-top: 1px solid var(--border);
  }
  .bot-row a {
    color: var(--tg-blue);
    font-weight: 600;
    text-decoration: none;
    font-size: 14px;
  }
  form.inline { margin-top: 10px; }
  .disconnect {
    background: transparent;
    color: var(--red);
    border: 1px solid #f2d3d0;
    font-size: 13px;
    padding: 8px 12px;
    width: auto;
  }
  .disconnect:hover { background: #fdecea; }
</style>
</head>
<body>
  <div class="card">
    <div class="logo">
      <svg viewBox="0 0 24 24"><path d="M21.5 3.5 2.7 10.9c-1.2.5-1.2 1.2-.2 1.5l4.8 1.5 1.9 5.7c.2.6.4.8.9.8.5 0 .7-.2 1-.5l2.4-2.3 4.9 3.6c.9.5 1.6.2 1.8-.8L23.9 4.9c.3-1.2-.5-1.8-1.4-1.4-.3.1-.3.1 0 0Zm-3.9 3.8L9 13.9l-.4 3.6-1.6-4.9 11.6-6.1c.5-.3.9-.1.6.5Z"/></svg>
    </div>
    <h1>Telegram Auto-Reply Bot</h1>
    <p class="sub">Paste your bot's token below. Once connected, the bot will reply <strong>"yes"</strong> to every message it receives.</p>

    {% if message %}
      <div class="banner {{ 'ok' if ok else 'err' }}">{{ message }}</div>
    {% endif %}

    <form method="post" action="/">
      <label for="bot_token">Bot token</label>
      <input type="text" id="bot_token" name="bot_token" placeholder="123456789:AAExampleTokenFromBotFather" value="{{ token_value }}" autocomplete="off" spellcheck="false" required>
      <div class="hint">Don't have one? Message <a href="https://t.me/BotFather" target="_blank" rel="noopener">@BotFather</a> on Telegram and send <code>/newbot</code>.</div>
      <button type="submit">Connect bot</button>
    </form>

    {% if bot_username %}
      <div class="bot-row">
        <a href="https://t.me/{{ bot_username }}" target="_blank" rel="noopener">Open @{{ bot_username }} &rarr;</a>
      </div>
      <form class="inline" method="post" action="/disconnect">
        <input type="hidden" name="bot_token" value="{{ token_value }}">
        <button type="submit" class="disconnect">Disconnect this bot</button>
      </form>
    {% endif %}
  </div>
</body>
</html>
"""


def tg_call(token, method, **params):
    """Call a Telegram Bot API method and return the parsed JSON response."""
    url = TELEGRAM_API.format(token=token, method=method)
    resp = requests.post(url, json=params, timeout=10)
    return resp.json()


def build_webhook_url(token):
    # request.host already includes the correct scheme-less host:port,
    # and Vercel always serves over https.
    return f"https://{request.host}/api/webhook/{token}"


@app.route("/", methods=["GET", "POST"])
def index():
    context = {"message": None, "ok": False, "bot_username": None, "token_value": ""}

    if request.method == "POST":
        token = (request.form.get("bot_token") or "").strip()
        context["token_value"] = token

        if not token or not TOKEN_RE.match(token):
            context["message"] = "That doesn't look like a valid bot token. Copy it exactly as BotFather gave it to you."
            return render_template_string(PAGE, **context)

        try:
            me = tg_call(token, "getMe")
        except requests.RequestException:
            context["message"] = "Couldn't reach Telegram right now. Please try again."
            return render_template_string(PAGE, **context)

        if not me.get("ok"):
            context["message"] = f"Telegram rejected that token: {me.get('description', 'unknown error')}."
            return render_template_string(PAGE, **context)

        bot_username = me["result"].get("username")

        try:
            hook = tg_call(token, "setWebhook", url=build_webhook_url(token), drop_pending_updates=True)
        except requests.RequestException:
            context["message"] = "Connected to the bot, but couldn't register the webhook. Please try again."
            return render_template_string(PAGE, **context)

        if not hook.get("ok"):
            context["message"] = f"Couldn't set the webhook: {hook.get('description', 'unknown error')}."
            return render_template_string(PAGE, **context)

        context["ok"] = True
        context["bot_username"] = bot_username
        context["message"] = f"Connected! @{bot_username} will now reply \"{REPLY_TEXT}\" to every message."

    return render_template_string(PAGE, **context)


@app.route("/disconnect", methods=["POST"])
def disconnect():
    token = (request.form.get("bot_token") or "").strip()
    context = {"message": None, "ok": False, "bot_username": None, "token_value": ""}

    if token and TOKEN_RE.match(token):
        try:
            result = tg_call(token, "deleteWebhook")
            if result.get("ok"):
                context["message"] = "Bot disconnected. It will no longer receive or reply to messages."
                context["ok"] = True
            else:
                context["message"] = f"Couldn't disconnect: {result.get('description', 'unknown error')}."
        except requests.RequestException:
            context["message"] = "Couldn't reach Telegram right now. Please try again."
    else:
        context["message"] = "Missing or invalid token."

    return render_template_string(PAGE, **context)


@app.route("/api/webhook/<token>", methods=["GET", "POST"])
def webhook(token):
    if request.method == "GET":
        # Someone opened the URL in a browser instead of Telegram POSTing to it.
        return {"ok": True, "info": "This endpoint only accepts Telegram webhook POST requests."}, 200

    update = request.get_json(silent=True) or {}
    message = update.get("message") or update.get("edited_message") or update.get("channel_post")

    if message and "chat" in message:
        chat_id = message["chat"]["id"]
        try:
            tg_call(token, "sendMessage", chat_id=chat_id, text=REPLY_TEXT)
        except requests.RequestException:
            pass  # Telegram will retry the webhook delivery; nothing else to do here.

    # Always 200 so Telegram doesn't keep retrying this update.
    return {"ok": True}, 200


if __name__ == "__main__":
    app.run(debug=True)
