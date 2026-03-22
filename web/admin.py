"""
web/admin.py -- Admin blueprint for Opaux.
"""

import logging
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

log = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__)


# ---------------------------------------------------------------------------
# Admin-required decorator
# ---------------------------------------------------------------------------

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash("Access denied. Admin privileges required.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated_function


# ---------------------------------------------------------------------------
# Admin index
# ---------------------------------------------------------------------------

@admin_bp.route("/admin/")
@admin_required
def admin_index():
    from web.auth import User

    all_users = User.all_users()
    total_users = len(all_users)

    # Users by plan
    plan_counts = {"free": 0, "starter": 0, "pro": 0}
    for user in all_users:
        plan = user.plan or "free"
        if plan in plan_counts:
            plan_counts[plan] += 1
        else:
            plan_counts[plan] = plan_counts.get(plan, 0) + 1

    # Total jobs across all users (count rows in each user's jobs.db)
    total_jobs = 0
    users_data_dir = Path("data") / "users"
    for user in all_users:
        db_path = users_data_dir / user.id / "jobs.db"
        if db_path.exists():
            try:
                import sqlite3
                conn = sqlite3.connect(str(db_path))
                count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
                conn.close()
                total_jobs += count
            except Exception as exc:
                log.warning("Could not count jobs for user %s: %s", user.id, exc)

    # Recent signups (last 7 days)
    cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    recent_signups = []
    for user in all_users:
        try:
            # created_at stored as ISO string from CURRENT_TIMESTAMP
            created = getattr(user, "created_at", None)
            # Re-fetch from DB to get created_at (not exposed on User model directly)
        except Exception:
            pass

    # Directly query the DB for recent signups since User model doesn't expose created_at
    try:
        import sqlite3

        from web.auth import AUTH_DB
        conn = sqlite3.connect(str(AUTH_DB))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM users WHERE created_at >= ? ORDER BY created_at DESC",
            (cutoff,),
        ).fetchall()
        conn.close()
        from web.auth import User as UserModel
        recent_signups = [UserModel(r) for r in rows]
    except Exception as exc:
        log.warning("Could not query recent signups: %s", exc)
        recent_signups = []

    return render_template(
        "admin/index.html",
        total_users=total_users,
        plan_counts=plan_counts,
        total_jobs=total_jobs,
        recent_signups=recent_signups,
    )


# ---------------------------------------------------------------------------
# Admin users list
# ---------------------------------------------------------------------------

@admin_bp.route("/admin/users")
@admin_required
def admin_users():
    from web.auth import User
    users = User.all_users()
    return render_template("admin/users.html", users=users)


# ---------------------------------------------------------------------------
# Set plan manually
# ---------------------------------------------------------------------------

@admin_bp.route("/admin/users/<user_id>/plan", methods=["POST"])
@admin_required
def admin_set_plan(user_id: str):
    from web.auth import User

    plan = request.form.get("plan", "free").strip().lower()
    valid_plans = ("free", "starter", "pro")
    if plan not in valid_plans:
        flash(f"Invalid plan '{plan}'. Must be one of: {', '.join(valid_plans)}.", "error")
        return redirect(url_for("admin.admin_users"))

    user = User.get(user_id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("admin.admin_users"))

    User.update_plan(user_id, plan)
    flash(f"Plan for {user.email} updated to '{plan}'.", "success")
    return redirect(url_for("admin.admin_users"))
