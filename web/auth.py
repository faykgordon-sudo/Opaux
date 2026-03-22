"""
web/auth.py -- User model, auth DB, and all auth routes.
"""

import secrets
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import UserMixin, current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

AUTH_DB = Path("data/auth.db")

auth_bp = Blueprint("auth", __name__)

# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id                    TEXT PRIMARY KEY,
    email                 TEXT UNIQUE NOT NULL,
    password_hash         TEXT NOT NULL,
    plan                  TEXT DEFAULT 'free',
    stripe_customer_id    TEXT,
    stripe_subscription_id TEXT,
    api_calls_this_month  INTEGER DEFAULT 0,
    calls_reset_date      TEXT,
    email_verified        INTEGER DEFAULT 0,
    created_at            TEXT DEFAULT CURRENT_TIMESTAMP,
    is_active             INTEGER DEFAULT 1
);
"""

CREATE_RESET_TOKENS_TABLE = """
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token       TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    used        INTEGER DEFAULT 0
);
"""


def init_auth_db() -> None:
    AUTH_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(AUTH_DB)
    conn.execute(CREATE_USERS_TABLE)
    conn.execute(CREATE_RESET_TOKENS_TABLE)
    # Migrations for existing DBs
    existing = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    migrations = {
        "plan":                    "ALTER TABLE users ADD COLUMN plan TEXT DEFAULT 'free'",
        "stripe_customer_id":      "ALTER TABLE users ADD COLUMN stripe_customer_id TEXT",
        "stripe_subscription_id":  "ALTER TABLE users ADD COLUMN stripe_subscription_id TEXT",
        "api_calls_this_month":    "ALTER TABLE users ADD COLUMN api_calls_this_month INTEGER DEFAULT 0",
        "calls_reset_date":        "ALTER TABLE users ADD COLUMN calls_reset_date TEXT",
        "email_verified":          "ALTER TABLE users ADD COLUMN email_verified INTEGER DEFAULT 0",
    }
    for col, sql in migrations.items():
        if col not in existing:
            conn.execute(sql)
    conn.commit()
    conn.close()


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(AUTH_DB)
    c.row_factory = sqlite3.Row
    return c


# ---------------------------------------------------------------------------
# User model
# ---------------------------------------------------------------------------

class User(UserMixin):
    def __init__(self, row: sqlite3.Row):
        self.id = row["id"]
        self.email = row["email"]
        self.plan = row["plan"] or "free"
        self.stripe_customer_id = row["stripe_customer_id"]
        self.stripe_subscription_id = row["stripe_subscription_id"]
        self.api_calls_this_month = row["api_calls_this_month"] or 0
        self.calls_reset_date = row["calls_reset_date"]
        self.email_verified = bool(row["email_verified"])
        self._is_active = bool(row["is_active"])

    @property
    def is_active(self):
        return self._is_active

    @property
    def plan_limit(self) -> int:
        from web.config import Config
        return Config.PLAN_LIMITS.get(self.plan, 20)

    @property
    def calls_remaining(self) -> int:
        return max(0, self.plan_limit - self.api_calls_this_month)

    @property
    def is_admin(self) -> bool:
        try:
            return self.email.lower() in current_app.config.get("ADMIN_EMAILS", [])
        except RuntimeError:
            return False

    # -- Lookups --

    @staticmethod
    def get(user_id: str) -> "User | None":
        conn = _conn()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.close()
        return User(row) if row else None

    @staticmethod
    def get_by_email(email: str) -> "User | None":
        conn = _conn()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
        conn.close()
        return User(row) if row else None

    @staticmethod
    def count() -> int:
        conn = _conn()
        n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()
        return n

    @staticmethod
    def all_users() -> list["User"]:
        conn = _conn()
        rows = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
        conn.close()
        return [User(r) for r in rows]

    # -- Mutations --

    @staticmethod
    def create(email: str, password: str) -> "User":
        user_id = uuid.uuid4().hex
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)",
                (user_id, email.lower(), generate_password_hash(password)),
            )
            conn.commit()
        finally:
            conn.close()
        return User.get(user_id)

    @staticmethod
    def check_password(email: str, password: str) -> "User | None":
        conn = _conn()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
        conn.close()
        if row and check_password_hash(row["password_hash"], password):
            return User(row)
        return None

    @staticmethod
    def set_password(user_id: str, new_password: str) -> None:
        conn = _conn()
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new_password), user_id),
        )
        conn.commit()
        conn.close()

    @staticmethod
    def update_plan(user_id: str, plan: str, stripe_customer_id: str = None,
                    stripe_subscription_id: str = None) -> None:
        conn = _conn()
        conn.execute(
            "UPDATE users SET plan=?, stripe_customer_id=?, stripe_subscription_id=? WHERE id=?",
            (plan, stripe_customer_id, stripe_subscription_id, user_id),
        )
        conn.commit()
        conn.close()

    @staticmethod
    def increment_api_calls(user_id: str) -> int:
        """Increment API call counter, reset monthly. Returns new count."""
        now = datetime.now(UTC)
        conn = _conn()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            conn.close()
            return 0

        reset_date = row["calls_reset_date"]
        current_calls = row["api_calls_this_month"] or 0

        # Reset if new month
        if reset_date:
            rd = datetime.fromisoformat(reset_date)
            if now.year != rd.year or now.month != rd.month:
                current_calls = 0

        new_count = current_calls + 1
        conn.execute(
            "UPDATE users SET api_calls_this_month=?, calls_reset_date=? WHERE id=?",
            (new_count, now.isoformat(), user_id),
        )
        conn.commit()
        conn.close()
        return new_count

    @staticmethod
    def deactivate(user_id: str) -> None:
        conn = _conn()
        conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()

    # -- Password reset tokens --

    @staticmethod
    def create_reset_token(user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        conn = _conn()
        # Invalidate old tokens for this user
        conn.execute("UPDATE password_reset_tokens SET used=1 WHERE user_id=?", (user_id,))
        conn.execute(
            "INSERT INTO password_reset_tokens (token, user_id, expires_at) VALUES (?,?,?)",
            (token, user_id, expires),
        )
        conn.commit()
        conn.close()
        return token

    @staticmethod
    def validate_reset_token(token: str) -> "User | None":
        conn = _conn()
        row = conn.execute(
            "SELECT * FROM password_reset_tokens WHERE token=? AND used=0",
            (token,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        expires = datetime.fromisoformat(row["expires_at"])
        if datetime.now(UTC) > expires:
            return None
        return User.get(row["user_id"])

    @staticmethod
    def consume_reset_token(token: str) -> None:
        conn = _conn()
        conn.execute("UPDATE password_reset_tokens SET used=1 WHERE token=?", (token,))
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# Per-user path helpers
# ---------------------------------------------------------------------------

def user_data_dir(user_id: str) -> Path:
    return Path("data") / "users" / user_id

def user_db_path(user_id: str) -> str:
    d = user_data_dir(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return str(d / "jobs.db")

def user_settings_path(user_id: str) -> Path:
    return user_data_dir(user_id) / "settings.yaml"

def user_profile_path(user_id: str) -> Path:
    return user_data_dir(user_id) / "profile.yaml"

def user_output_dir(user_id: str) -> Path:
    d = user_data_dir(user_id) / "output"
    d.mkdir(parents=True, exist_ok=True)
    return d

def user_upload_dir(user_id: str) -> Path:
    d = user_data_dir(user_id) / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d

def load_user_config(user_id: str) -> dict:
    import yaml
    p = user_settings_path(user_id)
    if p.exists():
        with open(p, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    else:
        global_cfg = Path("config/settings.yaml")
        cfg = {}
        if global_cfg.exists():
            with open(global_cfg, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
    cfg["_user_id"] = user_id
    cfg["_profile_path"] = str(user_profile_path(user_id))
    cfg["_output_dir"] = str(user_output_dir(user_id))
    cfg.setdefault("database", {})["path"] = user_db_path(user_id)
    cfg.setdefault("output", {})["dir"] = str(user_output_dir(user_id))
    return cfg


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    from web.extensions import limiter
    if request.method == "POST":
        with current_app.app_context():
            limiter.limit("10 per minute")(lambda: None)()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        user = User.check_password(email, password)
        if user and user.is_active:
            login_user(user, remember=True)
            return redirect(request.args.get("next") or url_for("dashboard"))
        return render_template("login.html", error="Invalid email or password.")
    return render_template("login.html")


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not email or not password:
            return render_template("signup.html", error="Email and password are required.")
        if len(password) < 8:
            return render_template("signup.html", error="Password must be at least 8 characters.")
        if password != confirm:
            return render_template("signup.html", error="Passwords do not match.")
        if User.get_by_email(email):
            return render_template("signup.html", error="An account with this email already exists.")
        user = User.create(email, password)
        login_user(user, remember=True)
        return redirect(url_for("setup"))
    return render_template("signup.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        user = User.get_by_email(email)
        # Always show success to prevent email enumeration
        if user:
            token = User.create_reset_token(user.id)
            _send_reset_email(user.email, token)
        return render_template("forgot_password.html", sent=True)
    return render_template("forgot_password.html", sent=False)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    user = User.validate_reset_token(token)
    if not user:
        return render_template("reset_password.html", error="This link is invalid or has expired.", token=None)

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(password) < 8:
            return render_template("reset_password.html", error="Password must be at least 8 characters.", token=token)
        if password != confirm:
            return render_template("reset_password.html", error="Passwords do not match.", token=token)
        User.set_password(user.id, password)
        User.consume_reset_token(token)
        return render_template("reset_password.html", success=True, token=None)

    return render_template("reset_password.html", token=token)


@auth_bp.route("/account/change-password", methods=["POST"])
@login_required
def change_password():
    current_pw = request.form.get("current_password", "")
    new_pw = request.form.get("new_password", "")
    confirm_pw = request.form.get("confirm_password", "")

    user = User.check_password(current_user.email, current_pw)
    if not user:
        flash("Current password is incorrect.", "error")
        return redirect(url_for("settings"))
    if len(new_pw) < 8:
        flash("New password must be at least 8 characters.", "error")
        return redirect(url_for("settings"))
    if new_pw != confirm_pw:
        flash("Passwords do not match.", "error")
        return redirect(url_for("settings"))

    User.set_password(current_user.id, new_pw)
    flash("Password updated successfully.", "success")
    return redirect(url_for("settings"))


@auth_bp.route("/account/delete", methods=["POST"])
@login_required
def delete_account():
    confirm = request.form.get("confirm_delete", "")
    if confirm != current_user.email:
        flash("Type your email address to confirm deletion.", "error")
        return redirect(url_for("settings"))
    User.deactivate(current_user.id)
    logout_user()
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Email helper
# ---------------------------------------------------------------------------

def _send_reset_email(email: str, token: str) -> None:
    try:
        from flask_mail import Message

        from web.extensions import mail
        reset_url = url_for("auth.reset_password", token=token, _external=True)
        msg = Message(
            subject="Reset your Opaux password",
            recipients=[email],
            html=render_template("emails/reset_password.html", reset_url=reset_url),
        )
        mail.send(msg)
    except Exception:
        pass  # Silently fail if email not configured — log in production
