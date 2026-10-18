"""
Bayani Bank — "Tuloy ang Pangarap."
An online banking SIMULATOR built with Flask + Flask-SQLAlchemy + PostgreSQL.
No real money is involved.
"""
import os
import re
import secrets
import string
import time
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps

from dotenv import load_dotenv
from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

# --------------------------------------------------------------------------
# Branding & banking rules
# --------------------------------------------------------------------------
BANK_NAME = "Bayani Bank"
BANK_TAGLINE = "Tuloy ang Pangarap."

MAX_TXN = Decimal("50000.00")             # max per deposit / withdrawal
DAILY_WITHDRAW_LIMIT = Decimal("20000.00")
OTP_THRESHOLD = Decimal("10000.00")       # withdrawals at/above this need an OTP
MAX_BALANCE = Decimal("9999999.99")
MAX_FAILED_ATTEMPTS = 3
LOCK_MINUTES = 5
IDLE_MINUTES = 5
TXN_COOLDOWN_SECONDS = 10
OTP_TTL_SECONDS = 120
PH_OFFSET = timedelta(hours=8)            # timestamps are stored in UTC

# --------------------------------------------------------------------------
# App & database setup
# --------------------------------------------------------------------------
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

db_url = os.environ.get("DATABASE_URL", "sqlite:///bayani_bank.db")
# Use the psycopg2 driver explicitly (newer SQLAlchemy defaults to psycopg v3).
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg2://", 1)
elif db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    SQLALCHEMY_DATABASE_URI=db_url,
    SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")),  # HTTPS on Render
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
)

db = SQLAlchemy(app)


def utcnow():
    return datetime.utcnow()


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(10), nullable=False)            # admin | customer
    username = db.Column(db.String(40), unique=True)           # admins only
    full_name = db.Column(db.String(120), nullable=False)
    account_number = db.Column(db.String(12), unique=True)     # customers only
    pin_hash = db.Column(db.String(256), nullable=False)       # PIN or admin password
    balance = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    must_change_pin = db.Column(db.Boolean, nullable=False, default=False)
    failed_attempts = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime)

    transactions = db.relationship("Transaction", backref="user", lazy="dynamic",
                                   order_by="Transaction.created_at.desc()")

    def is_locked(self):
        return bool(self.locked_until and self.locked_until > utcnow())

    def lock_minutes_left(self):
        if not self.is_locked():
            return 0
        return max(1, int((self.locked_until - utcnow()).total_seconds() // 60) + 1)

    @property
    def label(self):
        if self.role == "admin":
            return f"admin:{self.username}"
        return f"{self.full_name} ({mask_account(self.account_number)})"


class Transaction(db.Model):
    __tablename__ = "transactions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    kind = db.Column(db.String(12), nullable=False)            # deposit | withdrawal
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    balance_after = db.Column(db.Numeric(14, 2), nullable=False)
    reference = db.Column(db.String(24), unique=True, nullable=False)
    note = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    actor = db.Column(db.String(160), nullable=False)
    action = db.Column(db.String(40), nullable=False)
    details = db.Column(db.String(500))
    ip = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def peso(value):
    return "₱{:,.2f}".format(Decimal(value or 0))


def ph_time(dt, fmt="%b %d, %Y · %I:%M %p"):
    if not dt:
        return "—"
    return (dt + PH_OFFSET).strftime(fmt)


def format_account(num):
    return " ".join(num[i:i + 4] for i in range(0, len(num), 4)) if num else ""


def mask_account(num):
    return f"•••• •••• {num[-4:]}" if num else ""


def greeting():
    hour = (utcnow() + PH_OFFSET).hour
    if hour < 12:
        return "Magandang umaga"
    if hour < 18:
        return "Magandang hapon"
    return "Magandang gabi"


def ph_today_start_utc():
    now_ph = utcnow() + PH_OFFSET
    return now_ph.replace(hour=0, minute=0, second=0, microsecond=0) - PH_OFFSET


def withdrawn_today(user_id):
    total = (db.session.query(func.coalesce(func.sum(Transaction.amount), 0))
             .filter(Transaction.user_id == user_id,
                     Transaction.kind == "withdrawal",
                     Transaction.created_at >= ph_today_start_utc())
             .scalar())
    return Decimal(str(total or 0)).quantize(Decimal("0.01"))


def generate_pin():
    while True:
        pin = f"{secrets.randbelow(10**6):06d}"
        if pin_policy_error(pin) is None:
            return pin


def generate_account_number():
    while True:
        num = str(secrets.choice("123456789")) + "".join(
            secrets.choice(string.digits) for _ in range(11))
        if not User.query.filter_by(account_number=num).first():
            return num


def generate_reference():
    stamp = (utcnow() + PH_OFFSET).strftime("%y%m%d")
    alphabet = string.ascii_uppercase + string.digits
    while True:
        ref = "BB" + stamp + "".join(secrets.choice(alphabet) for _ in range(6))
        if not Transaction.query.filter_by(reference=ref).first():
            return ref


def pin_policy_error(pin):
    if not re.fullmatch(r"\d{6}", pin or ""):
        return "PIN must be exactly 6 digits."
    if len(set(pin)) == 1:
        return "PIN can't be the same digit repeated."
    digits = [int(c) for c in pin]
    steps = {digits[i + 1] - digits[i] for i in range(5)}
    if steps == {1} or steps == {-1}:
        return "PIN can't be a straight sequence like 123456."
    return None


def parse_amount(raw):
    try:
        amount = Decimal(str(raw).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None, "Enter a valid amount."
    if not amount.is_finite() or amount <= 0:
        return None, "Amount must be greater than zero."
    if amount != amount.quantize(Decimal("0.01")):
        return None, "Amounts can have at most 2 decimal places."
    if amount > MAX_TXN:
        return None, f"The maximum per transaction is {peso(MAX_TXN)}."
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), None


def limit_error(user, kind, amount):
    balance = Decimal(user.balance)
    if kind == "withdrawal":
        if amount > balance:
            return f"Insufficient balance. You have {peso(balance)} available."
        used = withdrawn_today(user.id)
        if used + amount > DAILY_WITHDRAW_LIMIT:
            left = max(DAILY_WITHDRAW_LIMIT - used, Decimal("0"))
            return f"That exceeds your daily withdrawal limit. You can still withdraw {peso(left)} today."
    elif balance + amount > MAX_BALANCE:
        return "That deposit would exceed the maximum allowed balance."
    return None


def audit(action, details="", user=None):
    u = user if user is not None else g.get("user")
    db.session.add(AuditLog(
        user_id=u.id if u else None,
        actor=u.label if u else "anonymous",
        action=action,
        details=(details or "")[:500],
        ip=(request.remote_addr or "?")[:64],
    ))


def register_failure(user, action):
    """Count a wrong PIN/password. Returns (locked, attempts_left)."""
    user.failed_attempts = (user.failed_attempts or 0) + 1
    if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
        user.failed_attempts = 0
        user.locked_until = utcnow() + timedelta(minutes=LOCK_MINUTES)
        audit("ACCOUNT_LOCKED", f"{action}: {MAX_FAILED_ATTEMPTS} wrong attempts, locked {LOCK_MINUTES} min", user)
        return True, 0
    audit(action, f"Wrong PIN/password (attempt {user.failed_attempts}/{MAX_FAILED_ATTEMPTS})", user)
    return False, MAX_FAILED_ATTEMPTS - user.failed_attempts


def rotate_csrf():
    session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def is_api():
    return request.path.startswith("/api/")


def home_for(user):
    return url_for("admin_dashboard") if user.role == "admin" else url_for("dashboard")


def txn_json(t):
    return {
        "reference": t.reference,
        "kind": t.kind,
        "amount": str(t.amount),
        "amount_display": peso(t.amount),
        "balance_after_display": peso(t.balance_after),
        "note": t.note or "",
        "time_display": ph_time(t.created_at),
    }


app.jinja_env.filters.update(peso=peso, ph_time=ph_time,
                             account=format_account, mask=mask_account)


@app.context_processor
def inject_globals():
    return {
        "BANK_NAME": BANK_NAME,
        "BANK_TAGLINE": BANK_TAGLINE,
        "csrf_token": lambda: session.get("csrf", ""),
        "IDLE_SECONDS": IDLE_MINUTES * 60,
        "greeting": greeting(),
        "rules": {
            "max_txn": MAX_TXN, "daily": DAILY_WITHDRAW_LIMIT,
            "otp": OTP_THRESHOLD, "attempts": MAX_FAILED_ATTEMPTS,
            "lock": LOCK_MINUTES, "idle": IDLE_MINUTES,
            "cooldown": TXN_COOLDOWN_SECONDS,
        },
    }


@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    if request.endpoint != "static":
        resp.headers["Cache-Control"] = "no-store"   # no cached balances on Back button
    return resp


# --------------------------------------------------------------------------
# Session, idle timeout and CSRF guard
# --------------------------------------------------------------------------
@app.before_request
def guard():
    g.user = None
    if request.endpoint == "static":
        return None
    if "csrf" not in session:
        rotate_csrf()

    uid = session.get("uid")
    if uid:
        now = time.time()
        if now - session.get("last_seen", 0) > IDLE_MINUTES * 60:
            user = db.session.get(User, uid)
            if user:
                audit("AUTO_LOGOUT", f"Idle for over {IDLE_MINUTES} minutes", user)
                db.session.commit()
            session.clear()
            rotate_csrf()
            flash(f"You were signed out after {IDLE_MINUTES} minutes of inactivity.", "info")
            if is_api():
                return jsonify(error="Your session expired. Please sign in again.",
                               redirect=url_for("login")), 401
            return redirect(url_for("login"))
        user = db.session.get(User, uid)
        if not user or not user.is_active:
            session.clear()
            rotate_csrf()
            return redirect(url_for("login"))
        g.user = user
        session["last_seen"] = now

    if request.method == "POST":
        token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
        if not secrets.compare_digest(token, session.get("csrf", "")):
            if is_api():
                return jsonify(error="Security token expired. Please refresh the page."), 400
            flash("Your form expired for security reasons. Please try again.", "error")
            return redirect(request.referrer or url_for("login"))
    return None


def login_required(role):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not g.user:
                if is_api():
                    return jsonify(error="Please sign in.", redirect=url_for("login")), 401
                return redirect(url_for("login"))
            if g.user.role != role:
                abort(403)
            if (role == "customer" and g.user.must_change_pin
                    and request.endpoint != "change_pin"):
                if is_api():
                    return jsonify(error="Please change your default PIN first.",
                                   redirect=url_for("change_pin")), 403
                return redirect(url_for("change_pin"))
            return view(*args, **kwargs)
        return wrapper
    return decorator


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
@app.route("/")
def index():
    return redirect(home_for(g.user) if g.user else url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(home_for(g.user))
    mode = request.values.get("mode", "customer")
    if mode not in ("customer", "admin"):
        mode = "customer"

    if request.method == "POST":
        if mode == "admin":
            ident = request.form.get("username", "").strip().lower()
            secret = request.form.get("password", "")
            user = User.query.filter_by(role="admin", username=ident).first()
        else:
            ident = re.sub(r"\D", "", request.form.get("account_number", ""))
            secret = request.form.get("pin", "")
            user = User.query.filter_by(role="customer", account_number=ident).first()

        if user and user.is_locked():
            audit("LOGIN_BLOCKED", "Sign-in attempt while locked", user)
            db.session.commit()
            flash(f"This account is locked for security. Try again in {user.lock_minutes_left()} "
                  "minute(s), or ask the bank admin to unlock it.", "error")
        elif user and not user.is_active:
            audit("LOGIN_BLOCKED", "Sign-in attempt on deactivated account", user)
            db.session.commit()
            flash("This account has been deactivated. Please contact the bank.", "error")
        elif user and check_password_hash(user.pin_hash, secret):
            previous_login = user.last_login_at
            user.failed_attempts = 0
            user.locked_until = None
            user.last_login_at = utcnow()
            audit("LOGIN_SUCCESS", f"Signed in as {user.role}", user)
            db.session.commit()
            session.clear()                       # prevent session fixation
            session.permanent = True
            session["uid"] = user.id
            session["last_seen"] = time.time()
            session["prev_login"] = ph_time(previous_login) if previous_login else None
            rotate_csrf()
            return redirect(home_for(user))
        else:
            if user:
                locked, _ = register_failure(user, "LOGIN_FAILED")
            else:
                locked = False
                masked = ident[-4:] if ident else "blank"
                audit("LOGIN_FAILED", f"Unknown {'username' if mode == 'admin' else 'account'} (…{masked})", None)
            db.session.commit()
            if locked:
                flash(f"Too many wrong attempts. This account is locked for {LOCK_MINUTES} minutes.", "error")
            else:
                what = "username or password" if mode == "admin" else "account number or PIN"
                flash(f"Incorrect {what}. {MAX_FAILED_ATTEMPTS} wrong attempts lock the account "
                      f"for {LOCK_MINUTES} minutes.", "error")
        return redirect(url_for("login", mode=mode))

    return render_template("login.html", mode=mode)


@app.route("/logout", methods=["GET", "POST"])
def logout():
    if g.user:
        reason = request.args.get("reason")
        audit("AUTO_LOGOUT" if reason == "idle" else "LOGOUT",
              "Idle timeout" if reason == "idle" else "Signed out", g.user)
        db.session.commit()
    session.clear()
    rotate_csrf()
    if request.args.get("reason") == "idle":
        flash(f"You were signed out after {IDLE_MINUTES} minutes of inactivity.", "info")
    else:
        flash("You've logged out safely. Salamat, bayani!", "success")
    return redirect(url_for("login"))


@app.post("/api/ping")
def ping():
    """Keeps an active session alive (called while the user is moving around the page)."""
    if not g.user:
        return jsonify(error="Please sign in.", redirect=url_for("login")), 401
    return jsonify(ok=True)


# --------------------------------------------------------------------------
# Customer
# --------------------------------------------------------------------------
@app.route("/dashboard")
@login_required("customer")
def dashboard():
    user = g.user
    txns = user.transactions.limit(50).all()
    used = withdrawn_today(user.id)
    security_events = (AuditLog.query.filter_by(user_id=user.id)
                       .order_by(AuditLog.created_at.desc()).limit(8).all())
    return render_template(
        "dashboard.html", user=user, txns=txns,
        withdrawn_today=used,
        daily_left=max(DAILY_WITHDRAW_LIMIT - used, Decimal("0")),
        security_events=security_events,
        prev_login=session.get("prev_login"),
    )


def _cooldown_left():
    left = TXN_COOLDOWN_SECONDS - (time.time() - session.get("last_txn_at", 0))
    return int(left) + 1 if left > 0 else 0


@app.post("/api/transactions")
@login_required("customer")
def create_transaction():
    data = request.get_json(silent=True) or {}
    kind = data.get("kind")
    if kind not in ("deposit", "withdrawal"):
        return jsonify(error="Unknown transaction type."), 400
    amount, err = parse_amount(data.get("amount"))
    if err:
        return jsonify(error=err), 400

    wait = _cooldown_left()
    if wait:
        return jsonify(error=f"For your security, please wait {wait}s before your next transaction."), 429

    user = g.user
    if not check_password_hash(user.pin_hash, str(data.get("pin", ""))):
        locked, left = register_failure(user, "TXN_PIN_FAILED")
        db.session.commit()
        if locked:
            session.clear()
            rotate_csrf()
            flash(f"Your account was locked for {LOCK_MINUTES} minutes after "
                  f"{MAX_FAILED_ATTEMPTS} wrong PIN attempts.", "error")
            return jsonify(error="Too many wrong PINs. Your account is locked and you've been signed out.",
                           redirect=url_for("login")), 423
        return jsonify(error=f"Wrong PIN. {left} attempt(s) left before your account is locked."), 401

    user.failed_attempts = 0
    err = limit_error(user, kind, amount)
    if err:
        db.session.commit()
        return jsonify(error=err), 400

    if kind == "withdrawal" and amount >= OTP_THRESHOLD:
        otp = f"{secrets.randbelow(10**6):06d}"
        session["pending_txn"] = {
            "kind": kind, "amount": str(amount),
            "otp_hash": generate_password_hash(otp),
            "expires": time.time() + OTP_TTL_SECONDS, "tries": 0,
        }
        audit("OTP_SENT", f"Simulated OTP sent for {peso(amount)} withdrawal")
        db.session.commit()
        return jsonify(status="otp_required", sim_otp=otp, expires_in=OTP_TTL_SECONDS,
                       amount_display=peso(amount))

    db.session.commit()
    return _finalize(kind, amount)


@app.post("/api/transactions/confirm")
@login_required("customer")
def confirm_transaction():
    pending = session.get("pending_txn")
    if not pending:
        return jsonify(error="There's no transaction waiting for an OTP."), 400
    if time.time() > pending["expires"]:
        session.pop("pending_txn", None)
        audit("OTP_EXPIRED", "Withdrawal cancelled: OTP expired")
        db.session.commit()
        return jsonify(error="The OTP expired, so the withdrawal was cancelled.", cancelled=True), 400

    otp = str((request.get_json(silent=True) or {}).get("otp", "")).strip()
    if not check_password_hash(pending["otp_hash"], otp):
        pending["tries"] += 1
        if pending["tries"] >= 3:
            session.pop("pending_txn", None)
            audit("OTP_FAILED", "Withdrawal cancelled after 3 wrong OTPs")
            db.session.commit()
            return jsonify(error="Wrong OTP 3 times. The withdrawal was cancelled.", cancelled=True), 400
        session["pending_txn"] = pending
        return jsonify(error=f"Wrong OTP. {3 - pending['tries']} attempt(s) left."), 401

    return _finalize(pending["kind"], Decimal(pending["amount"]))


@app.post("/api/transactions/cancel")
@login_required("customer")
def cancel_transaction():
    if session.pop("pending_txn", None):
        audit("TXN_CANCELLED", "Customer cancelled at OTP step")
        db.session.commit()
    return jsonify(ok=True)


def _finalize(kind, amount):
    # Lock the account row so two requests can't spend the same money.
    user = db.session.execute(
        db.select(User).where(User.id == g.user.id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()

    err = limit_error(user, kind, amount)
    if err:
        db.session.rollback()
        session.pop("pending_txn", None)
        return jsonify(error=err), 400

    balance = Decimal(user.balance)
    user.balance = balance + amount if kind == "deposit" else balance - amount
    txn = Transaction(user_id=user.id, kind=kind, amount=amount,
                      balance_after=user.balance, reference=generate_reference(),
                      note="Cash deposit (simulated)" if kind == "deposit" else "Cash withdrawal (simulated)")
    db.session.add(txn)
    audit(kind.upper(), f"{peso(amount)} · Ref {txn.reference} · New balance {peso(user.balance)}", user)
    db.session.commit()

    # Post-transaction security: new CSRF token, cooldown starts, OTP cleared.
    session.pop("pending_txn", None)
    session["last_txn_at"] = time.time()
    new_csrf = rotate_csrf()
    used = withdrawn_today(user.id)
    return jsonify(
        status="ok",
        csrf=new_csrf,
        receipt=txn_json(txn),
        balance=str(user.balance),
        balance_display=peso(user.balance),
        daily_left_display=peso(max(DAILY_WITHDRAW_LIMIT - used, Decimal("0"))),
        withdrawn_today=str(used),
        cooldown=TXN_COOLDOWN_SECONDS,
    )


@app.route("/change-pin", methods=["GET", "POST"])
@login_required("customer")
def change_pin():
    user = g.user
    if request.method == "POST":
        current = request.form.get("current_pin", "")
        new = request.form.get("new_pin", "")
        confirm = request.form.get("confirm_pin", "")
        if not check_password_hash(user.pin_hash, current):
            locked, left = register_failure(user, "PIN_CHANGE_FAILED")
            db.session.commit()
            if locked:
                session.clear()
                rotate_csrf()
                flash(f"Too many wrong PINs. Your account is locked for {LOCK_MINUTES} minutes.", "error")
                return redirect(url_for("login"))
            flash(f"Your current PIN is incorrect. {left} attempt(s) left.", "error")
        elif new != confirm:
            flash("The new PINs don't match.", "error")
        elif (err := pin_policy_error(new)):
            flash(err, "error")
        elif check_password_hash(user.pin_hash, new):
            flash("Your new PIN must be different from the current one.", "error")
        else:
            user.pin_hash = generate_password_hash(new)
            user.must_change_pin = False
            user.failed_attempts = 0
            audit("PIN_CHANGED", "Customer changed their PIN", user)
            db.session.commit()
            rotate_csrf()
            flash("Your PIN was changed. Keep it to yourself — the bank will never ask for it.", "success")
            return redirect(url_for("dashboard"))
        return redirect(url_for("change_pin"))
    return render_template("change_pin.html", user=user, forced=user.must_change_pin)


# --------------------------------------------------------------------------
# Admin
# --------------------------------------------------------------------------
@app.route("/admin")
@login_required("admin")
def admin_dashboard():
    customers = User.query.filter_by(role="customer").order_by(User.created_at.desc()).all()
    stats = {
        "customers": len(customers),
        "deposits_held": sum((Decimal(c.balance) for c in customers), Decimal("0")),
        "txns_today": Transaction.query.filter(Transaction.created_at >= ph_today_start_utc()).count(),
        "locked": sum(1 for c in customers if c.is_locked()),
    }
    txns = Transaction.query.order_by(Transaction.created_at.desc()).limit(40).all()
    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(80).all()
    reveal = session.pop("reveal", None)
    return render_template("admin.html", customers=customers, stats=stats,
                           txns=txns, logs=logs, reveal=reveal,
                           prev_login=session.get("prev_login"))


@app.post("/admin/customers")
@login_required("admin")
def admin_create_customer():
    name = " ".join(request.form.get("full_name", "").split())
    if not (2 <= len(name) <= 120):
        flash("Enter the customer's full name (2–120 characters).", "error")
        return redirect(url_for("admin_dashboard"))
    raw_deposit = request.form.get("initial_deposit", "").strip() or "0"
    if raw_deposit in ("0", "0.00"):
        opening = Decimal("0.00")
    else:
        opening, err = parse_amount(raw_deposit)
        if err:
            flash(f"Opening deposit: {err}", "error")
            return redirect(url_for("admin_dashboard"))

    pin = generate_pin()
    customer = User(role="customer", full_name=name,
                    account_number=generate_account_number(),
                    pin_hash=generate_password_hash(pin),
                    balance=opening, must_change_pin=True)
    db.session.add(customer)
    db.session.flush()
    if opening > 0:
        db.session.add(Transaction(user_id=customer.id, kind="deposit", amount=opening,
                                   balance_after=opening, reference=generate_reference(),
                                   note="Opening deposit"))
    audit("CUSTOMER_CREATED",
          f"Opened account for {name} ({mask_account(customer.account_number)}), opening deposit {peso(opening)}")
    db.session.commit()
    session["reveal"] = {"title": "Account opened", "name": name,
                         "account": format_account(customer.account_number), "pin": pin}
    return redirect(url_for("admin_dashboard"))


def _get_customer(cid):
    customer = db.session.get(User, cid)
    if not customer or customer.role != "customer":
        abort(404)
    return customer


@app.post("/admin/customers/<int:cid>/reset-pin")
@login_required("admin")
def admin_reset_pin(cid):
    customer = _get_customer(cid)
    pin = generate_pin()
    customer.pin_hash = generate_password_hash(pin)
    customer.must_change_pin = True
    customer.failed_attempts = 0
    customer.locked_until = None
    audit("PIN_RESET", f"Issued new default PIN for {customer.label}")
    db.session.commit()
    session["reveal"] = {"title": "PIN reset", "name": customer.full_name,
                         "account": format_account(customer.account_number), "pin": pin}
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/customers/<int:cid>/unlock")
@login_required("admin")
def admin_unlock(cid):
    customer = _get_customer(cid)
    customer.locked_until = None
    customer.failed_attempts = 0
    audit("ACCOUNT_UNLOCKED", f"Unlocked {customer.label}")
    db.session.commit()
    flash(f"{customer.full_name}'s account is unlocked.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/customers/<int:cid>/toggle")
@login_required("admin")
def admin_toggle(cid):
    customer = _get_customer(cid)
    customer.is_active = not customer.is_active
    audit("ACCOUNT_ACTIVATED" if customer.is_active else "ACCOUNT_DEACTIVATED", customer.label)
    db.session.commit()
    flash(f"{customer.full_name}'s account is now {'active' if customer.is_active else 'deactivated'}.", "success")
    return redirect(url_for("admin_dashboard"))


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(405)
def http_error(e):
    if is_api():
        return jsonify(error=e.description), e.code
    return render_template("error.html", code=e.code, message=e.description), e.code


# --------------------------------------------------------------------------
# Database bootstrap (runs once on start)
# --------------------------------------------------------------------------
def init_db():
    with app.app_context():
        db.create_all()
        username = os.environ.get("ADMIN_USERNAME", "admin").strip().lower()
        if not User.query.filter_by(role="admin").first():
            password = os.environ.get("ADMIN_PASSWORD", "BayaniAdmin2026!")
            db.session.add(User(role="admin", username=username, full_name="Bank Administrator",
                                pin_hash=generate_password_hash(password)))
            db.session.add(AuditLog(actor="system", action="ADMIN_SEEDED",
                                    details=f"Admin account '{username}' created", ip="-"))
            db.session.commit()
            print(f"[{BANK_NAME}] Admin account '{username}' created.")


try:
    init_db()
except Exception as exc:  # another worker may be creating tables at the same moment
    print(f"[{BANK_NAME}] init_db skipped: {exc}")

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=int(os.environ.get("PORT", 5000)))
