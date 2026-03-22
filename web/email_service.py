"""
web/email_service.py -- Email delivery helpers for Opaux using Flask-Mail.
"""

import logging

from flask import current_app, render_template

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _mail_enabled() -> bool:
    """Return True only when MAIL_ENABLED is set in the current app config."""
    return bool(current_app.config.get("MAIL_ENABLED", False))


def _send(subject: str, recipients: list[str], html: str) -> None:
    """Low-level send wrapper. Raises on error (caller decides how to handle)."""
    from flask_mail import Message
    from web.extensions import mail

    msg = Message(
        subject=subject,
        recipients=recipients,
        html=html,
    )
    mail.send(msg)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_response_alert(user, job: dict, new_status: str) -> None:
    """
    Send an email to *user* notifying them that application status changed.

    :param user:       User object (must have .email attribute)
    :param job:        Job dict with at least 'title' and 'company' keys
    :param new_status: The new application status string
    """
    if not _mail_enabled():
        return

    title = job.get("title") or "Unknown position"
    company = job.get("company") or ""
    subject = f"Application update: {title}"
    if company:
        subject += f" at {company}"

    try:
        html = render_template(
            "emails/response_alert.html",
            user=user,
            job=job,
            new_status=new_status,
        )
        _send(subject=subject, recipients=[user.email], html=html)
        log.info("Response alert sent to %s (job=%s, status=%s)", user.email, job.get("id"), new_status)
    except Exception as exc:
        log.error("Failed to send response alert to %s: %s", user.email, exc)


def send_new_jobs_digest(user, jobs: list[dict]) -> None:
    """
    Send a weekly digest email to *user* with the list of new matching jobs.

    :param user: User object (must have .email attribute)
    :param jobs: List of job dicts to include in the digest
    """
    if not _mail_enabled():
        return

    subject = "New jobs matching your profile"

    try:
        html = render_template(
            "emails/jobs_digest.html",
            user=user,
            jobs=jobs,
        )
        _send(subject=subject, recipients=[user.email], html=html)
        log.info("Jobs digest sent to %s (%d jobs)", user.email, len(jobs))
    except Exception as exc:
        log.error("Failed to send jobs digest to %s: %s", user.email, exc)


def send_reset_email(email: str, reset_url: str) -> None:
    """
    Send a password-reset email to *email*.

    :param email:     Recipient email address
    :param reset_url: Full URL the user should visit to reset their password
    """
    if not _mail_enabled():
        return

    subject = "Reset your Opaux password"

    try:
        html = render_template(
            "emails/reset_password.html",
            reset_url=reset_url,
        )
        _send(subject=subject, recipients=[email], html=html)
        log.info("Password reset email sent to %s", email)
    except Exception as exc:
        log.error("Failed to send password reset email to %s: %s", email, exc)
