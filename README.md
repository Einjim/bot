# Telegram Auto-Reply Bot ("yes" bot) — Vercel zero-config

A tiny Flask app with a login page: paste a Telegram bot token, it registers
a webhook for that bot, and from then on the bot replies **"yes"** to every
message it receives.

## How it works
- `index.py` sits at the project root with no `vercel.json` — Vercel's
  zero-config Python builder detects the Flask `app` object and routes
  **every** path (`/`, `/api/webhook/...`, etc.) through this one function.
- The login page (`/`) takes your bot token, verifies it with Telegram's
  `getMe`, then calls `setWebhook` pointing Telegram at
  `https://<your-deployment>/api/webhook/<token>`.
- Because Vercel functions are stateless (nothing is kept in memory or on
  disk between requests), the token is carried **in the webhook URL itself**
  rather than in a database — that's what lets the webhook handler know
  which bot to reply as, on every serverless cold start, with zero extra
  infrastructure.
- When Telegram calls that webhook with a new message, the handler replies
  with `sendMessage(chat_id, "yes")` and returns immediately.
- A "Disconnect this bot" button on the page calls `deleteWebhook` to stop it.

## Get a bot token
Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`,
and follow the prompts. It gives you a token that looks like
`123456789:AAExampleTokenFromBotFather`.

## Run locally
```bash
pip install -r requirements.txt
python index.py
```
Visit http://127.0.0.1:5000 — note that `setWebhook` needs a public HTTPS
URL, so the "Connect bot" flow only fully works once deployed (or through a
tunnel like `ngrok`/`cloudflared` pointed at your local server).

## Deploy to Vercel
```bash
npm i -g vercel   # if not already installed
vercel            # from inside this folder
```
Then open the deployment URL, paste your bot token, and hit **Connect bot**.
Message your bot on Telegram — it will reply "yes".

## Structure
- `index.py` — the whole app: login page, webhook handler, Telegram API calls
- `requirements.txt` — Flask + requests

## Notes
- Anyone who has your bot token can control the bot, so treat it like a
  password — this app doesn't store it anywhere itself; it only lives in the
  webhook URL Telegram calls.
- If you rotate the bot token in BotFather, just reconnect with the new one.
