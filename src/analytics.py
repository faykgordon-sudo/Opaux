"""
src/analytics.py -- Keyword and application analytics for Opaux.
"""

import json
import logging
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# Statuses that count as a positive response
_POSITIVE_STATUSES = {"responded", "interview", "offer"}


def compute_keyword_analytics(db_path: str) -> dict:
    """
    Query the jobs table at *db_path* and return a dict with:

    - top_keywords:          list of {keyword, total_uses, response_count, response_rate}
                             sorted by response_rate desc, top 20 (min 2 uses to qualify)
    - status_breakdown:      {status: count} for all statuses in the DB
    - monthly_applications:  list of {month: "YYYY-MM", count} for last 6 months
    - avg_ats_score:         float average of ats_score (NULL rows excluded)
    - avg_score:             float average of score (NULL rows excluded)

    Uses sqlite3 directly to avoid circular imports.
    Returns safe defaults if the DB does not exist or has no jobs table yet.
    """
    empty = {
        "top_keywords": [],
        "status_breakdown": {},
        "monthly_applications": [],
        "avg_ats_score": 0.0,
        "avg_score": 0.0,
    }

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    except Exception as exc:
        log.warning("analytics: could not open DB %s: %s", db_path, exc)
        return empty

    try:
        # ── Fetch all relevant job data ────────────────────────────────────
        try:
            rows = conn.execute(
                "SELECT keywords_matched, status, applied_date, ats_score, score FROM jobs"
            ).fetchall()
        except sqlite3.OperationalError:
            # Table doesn't exist yet
            return empty

        if not rows:
            return empty

        # ── Keyword analytics ─────────────────────────────────────────────
        keyword_uses: Counter = Counter()
        keyword_responses: Counter = Counter()

        for row in rows:
            status = row["status"] or ""
            is_positive = status in _POSITIVE_STATUSES

            kw_raw = row["keywords_matched"]
            if not kw_raw:
                continue
            try:
                keywords = json.loads(kw_raw)
                if not isinstance(keywords, list):
                    continue
            except (json.JSONDecodeError, TypeError):
                continue

            for kw in keywords:
                if not isinstance(kw, str) or not kw.strip():
                    continue
                kw_clean = kw.strip().lower()
                keyword_uses[kw_clean] += 1
                if is_positive:
                    keyword_responses[kw_clean] += 1

        top_keywords = []
        for kw, total in keyword_uses.items():
            if total < 2:
                continue
            resp_count = keyword_responses.get(kw, 0)
            rate = round(resp_count / total, 4) if total > 0 else 0.0
            top_keywords.append({
                "keyword": kw,
                "total_uses": total,
                "response_count": resp_count,
                "response_rate": rate,
            })

        top_keywords.sort(key=lambda x: (-x["response_rate"], -x["total_uses"]))
        top_keywords = top_keywords[:20]

        # ── Status breakdown ──────────────────────────────────────────────
        status_rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM jobs GROUP BY status"
        ).fetchall()
        status_breakdown = {
            (r["status"] or "unknown"): r["cnt"] for r in status_rows
        }

        # ── Monthly applications (last 6 months) ─────────────────────────
        now = datetime.now(timezone.utc)
        months = []
        for offset in range(5, -1, -1):
            # Walk back 'offset' months from current month
            first_of_current = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            target = first_of_current - timedelta(days=offset * 30)
            month_str = target.strftime("%Y-%m")
            months.append(month_str)

        # Count applied_date per month (applied means status transitioned to applied+)
        month_counts: dict[str, int] = defaultdict(int)
        applied_rows = conn.execute(
            "SELECT applied_date FROM jobs WHERE applied_date IS NOT NULL"
        ).fetchall()
        for r in applied_rows:
            applied_date = r["applied_date"] or ""
            if len(applied_date) >= 7:
                month_str = applied_date[:7]  # "YYYY-MM"
                if month_str in months:
                    month_counts[month_str] += 1

        monthly_applications = [
            {"month": m, "count": month_counts.get(m, 0)}
            for m in months
        ]

        # ── Average scores ────────────────────────────────────────────────
        ats_row = conn.execute(
            "SELECT AVG(ats_score) AS avg FROM jobs WHERE ats_score IS NOT NULL"
        ).fetchone()
        avg_ats_score = round(float(ats_row["avg"]), 2) if ats_row and ats_row["avg"] is not None else 0.0

        score_row = conn.execute(
            "SELECT AVG(score) AS avg FROM jobs WHERE score IS NOT NULL"
        ).fetchone()
        avg_score = round(float(score_row["avg"]), 2) if score_row and score_row["avg"] is not None else 0.0

        return {
            "top_keywords": top_keywords,
            "status_breakdown": status_breakdown,
            "monthly_applications": monthly_applications,
            "avg_ats_score": avg_ats_score,
            "avg_score": avg_score,
        }

    except Exception as exc:
        log.error("analytics: unexpected error for DB %s: %s", db_path, exc)
        return empty

    finally:
        conn.close()
