"""
web/scheduler.py -- APScheduler background job scheduler for Opaux.
"""

import logging
import threading

log = logging.getLogger(__name__)

# In-memory flag to prevent double-start across threads
_scheduler_started = False
_scheduler_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Scheduled job implementations
# ---------------------------------------------------------------------------

def nightly_discovery_and_score(app) -> None:
    """
    Runs daily at 2:00 AM.
    For each active user, runs job discovery and scoring.
    """
    with app.app_context():
        try:
            from src.discovery import run_discovery
            from src.scoring import run_scoring
            from web.auth import User, load_user_config

            users = User.all_users()
            active_users = [u for u in users if u._is_active]
            log.info("Nightly discovery+scoring: processing %d users", len(active_users))

            for user in active_users:
                try:
                    cfg = load_user_config(user.id)
                    db_path = cfg["database"]["path"]

                    # Discovery
                    try:
                        count = run_discovery(cfg, db_path)
                        log.info("User %s: discovered %d new jobs", user.id, count)
                    except Exception as exc:
                        log.error("Discovery failed for user %s: %s", user.id, exc)

                    # Scoring
                    try:
                        scored = run_scoring(cfg, db_path)
                        log.info("User %s: scored %d jobs", user.id, len(scored) if scored else 0)
                    except Exception as exc:
                        log.error("Scoring failed for user %s: %s", user.id, exc)

                except Exception as exc:
                    log.error("Nightly job failed for user %s: %s", user.id, exc)

        except Exception as exc:
            log.error("Nightly discovery+scoring encountered a fatal error: %s", exc)


def weekly_digest(app) -> None:
    """
    Runs every Monday at 8:00 AM.
    For each non-free user, sends a digest of top unapplied scored jobs.
    """
    with app.app_context():
        try:
            from web.auth import User, load_user_config
            from web.email_service import send_new_jobs_digest

            users = User.all_users()
            paid_users = [u for u in users if u._is_active and u.plan != "free"]
            log.info("Weekly digest: processing %d paid users", len(paid_users))

            for user in paid_users:
                try:
                    cfg = load_user_config(user.id)
                    db_path = cfg["database"]["path"]

                    # Get top scored, unapplied jobs
                    import sqlite3
                    from pathlib import Path
                    if not Path(db_path).exists():
                        continue

                    conn = sqlite3.connect(db_path)
                    conn.row_factory = sqlite3.Row
                    try:
                        rows = conn.execute(
                            """
                            SELECT * FROM jobs
                            WHERE status IN ('discovered', 'scored')
                              AND score IS NOT NULL
                            ORDER BY score DESC
                            LIMIT 10
                            """
                        ).fetchall()
                        top_jobs = [dict(r) for r in rows]
                    finally:
                        conn.close()

                    if not top_jobs:
                        log.info("User %s: no new jobs for digest", user.id)
                        continue

                    send_new_jobs_digest(user, top_jobs)
                    log.info("User %s: sent digest with %d jobs", user.id, len(top_jobs))

                except Exception as exc:
                    log.error("Weekly digest failed for user %s: %s", user.id, exc)

        except Exception as exc:
            log.error("Weekly digest encountered a fatal error: %s", exc)


# ---------------------------------------------------------------------------
# Scheduler start
# ---------------------------------------------------------------------------

def start_scheduler(app) -> None:
    """
    Create and start the APScheduler BackgroundScheduler.
    Guards against double-start with an in-memory lock.
    """
    global _scheduler_started

    with _scheduler_lock:
        if _scheduler_started:
            log.warning("Scheduler already started; skipping.")
            return
        _scheduler_started = True

    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        scheduler = BackgroundScheduler(timezone="UTC")

        # Nightly discovery + scoring at 02:00 UTC
        scheduler.add_job(
            func=nightly_discovery_and_score,
            args=[app],
            trigger="cron",
            hour=2,
            minute=0,
            id="nightly_discovery_and_score",
            name="Nightly discovery and scoring",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # Weekly digest every Monday at 08:00 UTC
        scheduler.add_job(
            func=weekly_digest,
            args=[app],
            trigger="cron",
            day_of_week="mon",
            hour=8,
            minute=0,
            id="weekly_digest",
            name="Weekly job digest email",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        try:
            scheduler.start()
            log.info("APScheduler started successfully.")
        except Exception as exc:
            log.error("Failed to start APScheduler: %s", exc)
            with _scheduler_lock:
                # Reset flag so a retry is possible if desired
                pass

    except ImportError:
        log.warning(
            "apscheduler is not installed. Background scheduling is disabled. "
            "Install it with: pip install apscheduler"
        )
    except Exception as exc:
        log.error("Unexpected error while setting up scheduler: %s", exc)
