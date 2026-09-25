# AI Trading Chart Bot — Vercel zero-config

A Telegram bot: users send a screenshot of a chart/trade, it's analyzed by
Gemini, and they can keep asking follow-up questions about it. One free
analysis per user, +1 more for every 5 friends they refer, then paid plans.

`index.py` sits at the project root with no `vercel.json` — same zero-config
Flask/Vercel pattern as before, so every route (`/`, `/api/webhook`,
`/api/setup`) is served by this one file.

## What's included
- **Screenshot analysis** — Gemini (`gemini-3.8-flash` by default, vision +
  text) reads the chart and answers using a system prompt you control.
- **Follow-up Q&A** — conversation history per user is kept in Postgres
  (`conversation` JSONB column), so "what about the RSI?" right after a
  chart makes sense to the model. Sending a *new* screenshot starts a fresh
  thread. Follow-ups on an already-analyzed screenshot don't use another
  credit — only a new screenshot does.
- **Referral system** — `/referral` gives each user a link
  (`t.me/<bot>?start=ref_<their_id>`); every 5 people who join through it
  earns the referrer +1 free analysis, repeatable.
- **Buttons** — an inline menu (Plans, Referral link, Help) plus a
  "Start over" button after each analysis.
- **Plans** — hardcoded in `PLANS` in `index.py` as placeholders (edit the
  labels/prices/credits to whatever you actually want to sell).

## About the payment flow (read this)
You didn't mention a payment processor, so **plan purchases are manual for
now**: tapping a plan button messages the user your payment instructions and
notifies you (via `ADMIN_CHAT_ID`) with an **Approve** button that unlocks
their plan on tap. No Stripe/processor keys needed to get started. If you
want real in-Telegram checkout later, Telegram's native Payments API (with a
Stripe or other provider token) is the natural upgrade — say the word and
it can be wired in.

## Environment variables (set in Vercel → Project → Settings → Environment Variables)
| Variable | Required | What it's for |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | From [@BotFather](https://t.me/BotFather) |
| `DATABASE_URL` | yes | Postgres connection string (Vercel Postgres, Neon, Supabase — any serverless Postgres works) |
| `GEMINI_API_KEY` | yes | Your Gemini API key |
| `WEBHOOK_SECRET` | recommended | Any random string you make up — used to verify webhook calls really come from Telegram |
| `SETUP_KEY` | recommended | Any random string you make up — protects the one-time `/api/setup` route |
| `ADMIN_CHAT_ID` | optional | Your own Telegram user ID, to receive plan-purchase requests to approve |
| `GEMINI_MODEL` | optional | Defaults to `gemini-3.8-flash` |
| `SYSTEM_PROMPT` | optional | Overrides the built-in default analysis prompt |

**About the Gemini key you shared in chat:** don't hardcode it into the
source — set it as `GEMINI_API_KEY` in Vercel's dashboard instead, so it's
never committed to GitHub. Since it was pasted into this conversation,
it's worth regenerating it in Google AI Studio and using the new one here,
just as a precaution.

## Deploy
```bash
npm i -g vercel   # if not already installed
vercel            # from inside this folder — set the env vars above first (or after, then redeploy)
```

## One-time setup (creates the DB tables + registers the webhook)
Visit, once, in a browser:
```
https://<your-deployment>.vercel.app/api/setup?key=<your SETUP_KEY>
```
It should respond `{"ok": true, ...}`. After that, message your bot on
Telegram — `/start`, then send it a chart screenshot.

## Structure
- `index.py` — the whole app: DB layer, Telegram + Gemini calls, routes
- `requirements.txt` — Flask, requests, psycopg2-binary

## Assumptions worth knowing about
- **Plans/prices are placeholders** — you didn't specify tiers, so `PLANS`
  in `index.py` has an example Basic (20 credits/$5) and Pro (unlimited
  30 days/$15). Edit freely.
- **Referral bonus repeats**: every 5 successful referrals (not just the
  first 5) earns another free analysis.
- **The default system prompt** frames the bot as educational/not a
  licensed financial advisor — worth keeping some version of that even if
  you rewrite the rest, and worth reviewing your local regulations around
  giving trading-related output to users.
- If `psycopg2-binary` ever fails to install on your Vercel account's
  Python runtime, `pg8000` (pure Python) is a drop-in-ish alternative worth
  trying instead.
