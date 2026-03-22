"""
web/config.py -- Flask app configuration loaded from environment.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


class Config:
    # Core
    SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-insecure-key-change-me"
    FLASK_ENV = os.environ.get("FLASK_ENV", "production")
    DEBUG = FLASK_ENV == "development"

    # CSRF
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600  # 1 hour

    # Upload limits
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10 MB

    # Email
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.postmarkapp.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "1") == "1"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "noreply@opaux.io")
    MAIL_ENABLED = bool(MAIL_USERNAME and MAIL_PASSWORD)

    # Stripe
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    STRIPE_PRICE_STARTER = os.environ.get("STRIPE_PRICE_STARTER", "")
    STRIPE_PRICE_PRO = os.environ.get("STRIPE_PRICE_PRO", "")
    STRIPE_ENABLED = bool(STRIPE_SECRET_KEY)

    # Admin
    ADMIN_EMAILS = [
        e.strip().lower()
        for e in os.environ.get("ADMIN_EMAILS", "").split(",")
        if e.strip()
    ]

    # Scheduler
    SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "0") == "1"

    # Plan limits (AI calls per month)
    PLAN_LIMITS = {
        "free":    20,
        "starter": 200,
        "pro":     999999,
    }
