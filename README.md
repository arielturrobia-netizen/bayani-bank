# Bayani Bank — "Tuloy ang Pangarap."

An online banking **simulator** built for SF Activity 3. No real money is involved.

**Look:** Filipino komiks hero style: flag blue, red and sun yellow, sun-shield logo, Hero ID card, ATM-style PIN keypad.

**Stack:** Flask 3 · Flask-SQLAlchemy · PostgreSQL (Render) · Gunicorn · Jinja2 · Tailwind CSS · Lucide icons · Vanilla JS (Fetch API) · python-dotenv

## Features

| Area | What it does |
|---|---|
| **Admin login** | Username + password. Opens customer accounts with an auto-generated 12-digit account number and a **temporary 6-digit PIN** (shown once). Can reset PINs, unlock and deactivate accounts. Views all transactions and the full audit log. |
| **Customer login** | Account number + PIN. Must replace the temporary PIN on first sign-in. |
| **Deposit / Withdraw** | Modal flow without page reloads (Fetch API) → amount → PIN → receipt with reference number. |
| **Check balance** | Hidden by default, tap the eye to show; auto-hides after 15s. |
| **Change PIN** | Rejects weak PINs (111111, 123456, same as old). |
| **Database** | Users, transactions and every security event (logins, failures, lockouts, PIN changes, deposits, withdrawals) are saved in PostgreSQL. |

### Security features after each transaction
1. **PIN re-entry** to authorize every deposit and withdrawal.
2. **Simulated SMS one-time PIN** for withdrawals of ₱10,000 or more (expires in 2 minutes, 3 tries).
3. **Receipt + reference number**, and the event is written to the audit log.
4. **Balance and account number are re-masked** and the PIN is wiped from the screen.
5. **10-second cooldown** before the next transaction (stops double-submits).
6. **CSRF token is rotated** after each transaction.
7. Daily withdrawal limit (₱20,000), ₱50,000 max per transaction, row locking so a balance can't go negative.

Always on: lockout for 5 minutes after 3 wrong PINs/passwords, auto sign-out after 5 idle minutes (with a 60-second warning), hashed PINs/passwords, session-fixation protection, secure cookies on Render, no-cache on account pages.

## Run locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then edit ADMIN_PASSWORD and SECRET_KEY
python app.py               # open http://127.0.0.1:5000
```

Without `DATABASE_URL`, it uses a local SQLite file so you can test quickly.

## Deploy to Render (free)

1. Push this folder to a new GitHub repository.
2. On [render.com](https://render.com): **New → Blueprint**, pick the repo. Render reads `render.yaml` and creates the PostgreSQL database and the web service together.
3. When asked, type an `ADMIN_PASSWORD`. `SECRET_KEY` is generated for you.
4. Wait for the deploy, then open `https://bayani-bank.onrender.com` (or whatever URL Render shows). Submit that URL.

**Manual alternative:** create a PostgreSQL instance, then a Web Service with
Build `pip install -r requirements.txt`, Start `gunicorn app:app --preload --workers 2 --bind 0.0.0.0:$PORT`,
and env vars `DATABASE_URL` (Internal Database URL), `SECRET_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `PYTHON_VERSION=3.12.8`.

Notes: free web services sleep after ~15 minutes idle (first visit takes ~50s to wake). Render's free database expires after 30 days.

## Demo script
1. Sign in as admin → open an account for a customer with an opening deposit → copy the account number and temporary PIN.
2. Sign out → Customer tab → sign in with those → set a new PIN.
3. Deposit ₱1,000, withdraw ₱500, then withdraw ₱10,000 to see the simulated OTP.
4. Enter a wrong PIN 3 times to show the lockout; sign in as admin to unlock it and view the audit log.
