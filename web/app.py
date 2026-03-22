"""
web/app.py -- Opaux Flask application factory.
"""

import json
import os
import queue
import sys
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

import yaml
from flask import (
    Flask,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(config_object=None):
    app = Flask(__name__)

    # Config
    from web.config import Config
    app.config.from_object(config_object or Config)

    # Extensions
    from web.extensions import csrf, limiter, login_manager, mail
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    mail.init_app(app)

    # User loader
    from web.auth import User, init_auth_db
    init_auth_db()

    @login_manager.user_loader
    def load_user(user_id):
        return User.get(user_id)

    # Blueprints
    from web.auth import auth_bp
    app.register_blueprint(auth_bp)

    from web.billing import billing_bp
    app.register_blueprint(billing_bp)

    from web.admin import admin_bp
    app.register_blueprint(admin_bp)

    from web.profile_editor import profile_bp
    app.register_blueprint(profile_bp)

    # Exempt Stripe webhooks from CSRF
    csrf.exempt(billing_bp)

    # Register all main routes
    _register_routes(app)

    # Scheduler
    if app.config.get("SCHEDULER_ENABLED") and not app.debug:
        from web.scheduler import start_scheduler
        start_scheduler(app)

    return app


# ---------------------------------------------------------------------------
# Background task runner
# ---------------------------------------------------------------------------

_tasks: dict[str, dict] = {}
_task_queues: dict[str, queue.Queue] = {}


def _new_task() -> str:
    task_id = str(uuid.uuid4())
    _tasks[task_id] = {"status": "running", "log": [], "result": None}
    _task_queues[task_id] = queue.Queue()
    return task_id


def _task_log(task_id: str, message: str, level: str = "info") -> None:
    entry = {"ts": datetime.now().strftime("%H:%M:%S"), "msg": message, "level": level}
    _tasks[task_id]["log"].append(entry)
    if task_id in _task_queues:
        _task_queues[task_id].put(entry)


def _task_done(task_id: str, result=None) -> None:
    _tasks[task_id]["status"] = "done"
    _tasks[task_id]["result"] = result
    if task_id in _task_queues:
        _task_queues[task_id].put(None)


def _task_error(task_id: str, error: str) -> None:
    _tasks[task_id]["status"] = "error"
    _tasks[task_id]["result"] = error
    _task_log(task_id, error, level="error")
    if task_id in _task_queues:
        _task_queues[task_id].put(None)


def _check_plan_limit(user_id: str) -> tuple[bool, str]:
    """Returns (allowed, error_message). Increments usage if allowed."""
    from web.auth import User
    user = User.get(user_id)
    if not user:
        return False, "User not found."
    if user.api_calls_this_month >= user.plan_limit:
        return False, (
            f"You've used all {user.plan_limit} AI calls this month on the "
            f"<strong>{user.plan}</strong> plan. "
            f"<a href='/billing' class='underline'>Upgrade</a> for more."
        )
    User.increment_api_calls(user_id)
    return True, ""


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def _register_routes(app: Flask):

    # ── SSE stream ──────────────────────────────────────────────────────────

    @app.route("/stream/<task_id>")
    def stream(task_id: str):
        if task_id not in _tasks:
            abort(404)

        def generate():
            q = _task_queues.get(task_id)
            if not q:
                yield "data: {}\n\n"
                return
            while True:
                try:
                    entry = q.get(timeout=30)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                if entry is None:
                    status = _tasks[task_id]["status"]
                    result = _tasks[task_id].get("result", "")
                    yield f"data: {json.dumps({'done': True, 'status': status, 'result': str(result or '')})}\n\n"
                    break
                yield f"data: {json.dumps(entry)}\n\n"

        return Response(generate(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.route("/task/<task_id>/status")
    def task_status(task_id: str):
        task = _tasks.get(task_id)
        return jsonify(task or {"status": "not_found"})

    # ── Dashboard ───────────────────────────────────────────────────────────

    @app.route("/")
    @login_required
    def dashboard():
        from src.database import get_all_jobs, get_connection, init_db
        from web.auth import load_user_config
        cfg = load_user_config(current_user.id)
        db = cfg["database"]["path"]
        init_db(db)
        conn = get_connection(db)
        jobs = get_all_jobs(conn)
        conn.close()

        total     = len(jobs)
        applied   = sum(1 for j in jobs if (j.get("status") or "") in ("applied","responded","interview","offer"))
        responded = sum(1 for j in jobs if (j.get("status") or "") in ("responded","interview","offer"))
        interview = sum(1 for j in jobs if (j.get("status") or "") in ("interview","offer"))
        offer     = sum(1 for j in jobs if (j.get("status") or "") == "offer")
        tailored  = sum(1 for j in jobs if (j.get("status") or "") in ("tailored","applied","responded","interview","offer"))
        recent    = sorted(jobs, key=lambda j: j.get("score") or 0, reverse=True)[:8]

        return render_template("dashboard.html",
            stats={
                "total": total, "applied": applied, "responded": responded,
                "interview": interview, "offer": offer, "tailored": tailored,
                "response_rate": round(responded/applied*100, 1) if applied else 0,
                "interview_rate": round(interview/applied*100, 1) if applied else 0,
            },
            recent_jobs=recent, config=cfg,
        )

    # ── Jobs list ────────────────────────────────────────────────────────────

    @app.route("/jobs")
    @login_required
    def jobs():
        from src.database import get_all_jobs, get_connection, init_db
        from web.auth import load_user_config
        cfg = load_user_config(current_user.id)
        db = cfg["database"]["path"]
        init_db(db)
        conn = get_connection(db)

        status_filter = request.args.get("status")
        q = request.args.get("q", "").strip()
        page = max(1, int(request.args.get("page", 1)))
        per_page = 25

        all_jobs = get_all_jobs(conn, status=status_filter)
        conn.close()

        # Search filter
        if q:
            ql = q.lower()
            all_jobs = [
                j for j in all_jobs
                if ql in (j.get("title") or "").lower()
                or ql in (j.get("company") or "").lower()
                or ql in (j.get("location") or "").lower()
            ]

        total_count = len(all_jobs)
        total_pages = max(1, (total_count + per_page - 1) // per_page)
        page = min(page, total_pages)
        paginated = all_jobs[(page-1)*per_page : page*per_page]

        return render_template("jobs.html",
            jobs=paginated, status_filter=status_filter, q=q,
            page=page, total_pages=total_pages, total_count=total_count,
        )

    # ── Job detail ───────────────────────────────────────────────────────────

    @app.route("/jobs/<job_id>")
    @login_required
    def job_detail(job_id: str):
        from src.database import get_connection, get_job_by_id, init_db
        from web.auth import load_user_config
        cfg = load_user_config(current_user.id)
        db = cfg["database"]["path"]
        init_db(db)
        conn = get_connection(db)
        job = get_job_by_id(conn, job_id)
        conn.close()
        if not job:
            abort(404)

        keywords = []
        try:
            keywords = json.loads(job.get("keywords_matched") or "[]")
        except Exception:
            pass

        supported_langs = {
            "en":"English","de":"German","fr":"French","es":"Spanish",
            "pt":"Portuguese","nl":"Dutch","it":"Italian","pl":"Polish",
            "sv":"Swedish","da":"Danish","no":"Norwegian","fi":"Finnish",
        }
        return render_template("job_detail.html",
            job=job, keywords=keywords, config=cfg, supported_langs=supported_langs,
        )

    # ── Tailor ───────────────────────────────────────────────────────────────

    @app.route("/jobs/<job_id>/tailor", methods=["POST"])
    @login_required
    def tailor_job(job_id: str):
        allowed, err = _check_plan_limit(current_user.id)
        if not allowed:
            return jsonify({"error": err}), 402

        fmt = request.form.get("format", "german")
        lang = request.form.get("lang", "de")
        task_id = _new_task()
        uid = current_user.id

        def run():
            try:
                from src.tailoring import run_tailoring
                from web.auth import load_user_config
                cfg = load_user_config(uid)
                _task_log(task_id, f"Tailoring CV ({fmt} / {lang})...")
                path = run_tailoring(cfg, cfg["database"]["path"], job_id, format=fmt, lang=lang)
                _task_log(task_id, f"Saved: {path}", "success")
                _task_done(task_id, path)
            except Exception as e:
                _task_error(task_id, str(e))

        threading.Thread(target=run, daemon=True).start()
        return jsonify({"task_id": task_id})

    # ── Cover letter ─────────────────────────────────────────────────────────

    @app.route("/jobs/<job_id>/cover", methods=["POST"])
    @login_required
    def cover_job(job_id: str):
        allowed, err = _check_plan_limit(current_user.id)
        if not allowed:
            return jsonify({"error": err}), 402

        task_id = _new_task()
        uid = current_user.id

        def run():
            try:
                from src.cover_letter import run_cover_letter
                from web.auth import load_user_config
                cfg = load_user_config(uid)
                _task_log(task_id, "Generating cover letter...")
                path = run_cover_letter(cfg, cfg["database"]["path"], job_id)
                _task_log(task_id, f"Saved: {path}", "success")
                _task_done(task_id, path)
            except Exception as e:
                _task_error(task_id, str(e))

        threading.Thread(target=run, daemon=True).start()
        return jsonify({"task_id": task_id})

    # ── PDF ──────────────────────────────────────────────────────────────────

    @app.route("/jobs/<job_id>/pdf", methods=["POST"])
    @login_required
    def pdf_job(job_id: str):
        from src.database import get_connection, get_job_by_id, init_db
        from src.pdf_renderer import render_pdf
        from web.auth import load_user_config
        cfg = load_user_config(current_user.id)
        db = cfg["database"]["path"]
        init_db(db)
        conn = get_connection(db)
        job = get_job_by_id(conn, job_id)
        conn.close()
        if not job:
            return jsonify({"error": "Job not found"}), 404

        results = {}
        for key, label in [("cv_path","cv"),("cover_letter_path","cover")]:
            path = job.get(key)
            if path and os.path.exists(path):
                try:
                    results[label] = render_pdf(path)
                except Exception as e:
                    results[f"{label}_error"] = str(e)
        return jsonify(results)

    # ── Download ─────────────────────────────────────────────────────────────

    @app.route("/jobs/<job_id>/download/<doc_type>")
    @login_required
    def download_doc(job_id: str, doc_type: str):
        from src.database import get_connection, get_job_by_id, init_db
        from web.auth import load_user_config
        cfg = load_user_config(current_user.id)
        db = cfg["database"]["path"]
        init_db(db)
        conn = get_connection(db)
        job = get_job_by_id(conn, job_id)
        conn.close()
        if not job:
            abort(404)

        field_map = {
            "cv_docx":"cv_path","cover_docx":"cover_letter_path",
            "cv_pdf":"cv_path","cover_pdf":"cover_letter_path",
        }
        field = field_map.get(doc_type)
        if not field:
            abort(400)

        path = job.get(field, "")
        if doc_type.endswith("_pdf"):
            path = str(Path(path).with_suffix(".pdf")) if path else ""
        if not path or not os.path.exists(path):
            abort(404)
        return send_file(path, as_attachment=True)

    # ── Status update ────────────────────────────────────────────────────────

    @app.route("/jobs/<job_id>/status", methods=["POST"])
    @login_required
    def update_status(job_id: str):
        from src.database import get_connection, get_job_by_id, init_db, update_job
        from web.auth import load_user_config
        new_status = request.form.get("status")
        notes = request.form.get("notes", "")
        valid = ["discovered","scored","tailored","applied","responded","interview","offer"]
        if new_status not in valid:
            return jsonify({"error": "Invalid status"}), 400

        cfg = load_user_config(current_user.id)
        db = cfg["database"]["path"]
        init_db(db)
        conn = get_connection(db)
        try:
            kwargs: dict = {"status": new_status}
            if notes:
                kwargs["notes"] = notes
            if new_status == "applied":
                kwargs["applied_date"] = datetime.now(UTC).isoformat()
            elif new_status in ("responded","interview","offer"):
                kwargs["response_date"] = datetime.now(UTC).isoformat()
            update_job(conn, job_id, **kwargs)

            # Email notification for responses
            job = get_job_by_id(conn, job_id)
        finally:
            conn.close()

        if new_status in ("responded","interview","offer") and job:
            try:
                from web.auth import User
                from web.email_service import send_response_alert
                user = User.get(current_user.id)
                send_response_alert(user, job, new_status)
            except Exception:
                pass

        return jsonify({"ok": True, "status": new_status})

    # ── Discover ─────────────────────────────────────────────────────────────

    @app.route("/discover", methods=["POST"])
    @login_required
    def discover():
        task_id = _new_task()
        uid = current_user.id

        def run():
            try:
                from src.discovery import run_discovery
                from web.auth import load_user_config
                cfg = load_user_config(uid)
                _task_log(task_id, "Discovering jobs...")
                count = run_discovery(cfg, cfg["database"]["path"])
                _task_log(task_id, f"Found {count} new jobs.", "success")
                _task_done(task_id, count)
            except Exception as e:
                _task_error(task_id, str(e))

        threading.Thread(target=run, daemon=True).start()
        return jsonify({"task_id": task_id})

    # ── Score ────────────────────────────────────────────────────────────────

    @app.route("/score", methods=["POST"])
    @login_required
    def score():
        allowed, err = _check_plan_limit(current_user.id)
        if not allowed:
            return jsonify({"error": err}), 402

        task_id = _new_task()
        uid = current_user.id

        def run():
            try:
                from src.scoring import run_scoring
                from web.auth import load_user_config
                cfg = load_user_config(uid)
                _task_log(task_id, "Scoring jobs...")
                scored = run_scoring(cfg, cfg["database"]["path"])
                _task_log(task_id, f"Scored {len(scored)} jobs.", "success")
                _task_done(task_id, len(scored))
            except Exception as e:
                _task_error(task_id, str(e))

        threading.Thread(target=run, daemon=True).start()
        return jsonify({"task_id": task_id})

    # ── Setup / CV upload ────────────────────────────────────────────────────

    @app.route("/setup")
    @login_required
    def setup():
        from web.auth import load_user_config, user_profile_path
        cfg = load_user_config(current_user.id)
        return render_template("setup.html",
            profile_exists=user_profile_path(current_user.id).exists(),
            has_api_key=bool(cfg.get("claude_api_key", "")),
        )

    @app.route("/setup/upload", methods=["POST"])
    @login_required
    def setup_upload():
        from web.auth import (
            load_user_config,
            user_profile_path,
            user_settings_path,
            user_upload_dir,
        )
        task_id = _new_task()
        uid = current_user.id

        api_key = request.form.get("api_key", "").strip()
        if not api_key:
            api_key = load_user_config(uid).get("claude_api_key", "")
        if not api_key:
            return jsonify({"error": "Claude API key is required."}), 400

        if "cv_file" not in request.files:
            return jsonify({"error": "No file uploaded."}), 400
        file = request.files["cv_file"]
        if not file.filename:
            return jsonify({"error": "No file selected."}), 400

        ext = Path(file.filename).suffix.lower()
        if ext not in (".pdf", ".docx", ".doc"):
            return jsonify({"error": "Only PDF and DOCX files are supported."}), 400

        upload_path = str(user_upload_dir(uid) / f"cv_{uuid.uuid4().hex[:8]}{ext}")
        file.save(upload_path)

        def run():
            try:
                _task_log(task_id, f"Reading {file.filename}...")
                from src.cv_parser import run_cv_parser, save_profile, update_settings_from_cv
                parsed = run_cv_parser(api_key, upload_path)
                name = parsed.get("personal", {}).get("name", "")
                terms = parsed.get("suggested_search_terms", [])
                loc = parsed.get("suggested_location", "")
                if name:
                    _task_log(task_id, f"Identified: {name}")
                if terms:
                    _task_log(task_id, f"Search terms: {', '.join(terms)}")
                if loc:
                    _task_log(task_id, f"Location: {loc}")
                _task_log(task_id, "Saving profile...")
                save_profile(parsed, profile_path=str(user_profile_path(uid)))
                update_settings_from_cv(parsed, settings_path=str(user_settings_path(uid)))
                # Persist API key
                p = user_settings_path(uid)
                cfg2 = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
                cfg2["claude_api_key"] = api_key
                p.write_text(yaml.dump(cfg2, allow_unicode=True, default_flow_style=False), encoding="utf-8")
                try:
                    os.remove(upload_path)
                except Exception:
                    pass
                _task_log(task_id, "Profile created!", "success")
                _task_done(task_id, json.dumps({"name": name, "terms": terms, "location": loc}))
            except Exception as e:
                try:
                    os.remove(upload_path)
                except Exception:
                    pass
                _task_error(task_id, str(e))

        threading.Thread(target=run, daemon=True).start()
        return jsonify({"task_id": task_id})

    # ── Settings ─────────────────────────────────────────────────────────────

    @app.route("/settings")
    @login_required
    def settings():
        from web.auth import load_user_config, user_profile_path
        cfg = load_user_config(current_user.id)
        profile = {}
        p = user_profile_path(current_user.id)
        if p.exists():
            with open(p, encoding="utf-8") as f:
                profile = yaml.safe_load(f) or {}
        return render_template("settings.html", config=cfg, profile=profile,
                               flashes=get_flashed_messages_safe())

    @app.route("/settings", methods=["POST"])
    @login_required
    def save_settings():
        from web.auth import load_user_config, user_settings_path
        cfg = load_user_config(current_user.id)
        data = request.form
        cfg.setdefault("application", {})
        cfg.setdefault("discovery", {})
        cfg.setdefault("scoring", {})
        if data.get("claude_api_key"):
            cfg["claude_api_key"] = data["claude_api_key"]
        if data.get("search_term"):
            cfg["discovery"]["search_term"] = data["search_term"]
        if data.get("location"):
            cfg["discovery"]["location"] = data["location"]
        if data.get("default_lang"):
            cfg["application"]["default_lang"] = data["default_lang"]
        if data.get("default_format"):
            cfg["application"]["default_format"] = data["default_format"]
        if data.get("min_score"):
            cfg["scoring"]["min_score"] = float(data["min_score"])
        for k in ("_user_id","_profile_path","_output_dir"):
            cfg.pop(k, None)
        p = user_settings_path(current_user.id)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
        flash("Settings saved.", "success")
        return redirect(url_for("settings"))

    # ── Analytics ────────────────────────────────────────────────────────────

    @app.route("/analytics")
    @login_required
    def analytics():
        from src.analytics import compute_keyword_analytics
        from web.auth import load_user_config
        cfg = load_user_config(current_user.id)
        data = compute_keyword_analytics(cfg["database"]["path"])
        return render_template("analytics.html", analytics=data)

    # ── Landing (public) ─────────────────────────────────────────────────────

    @app.route("/landing")
    def landing():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        return render_template("landing.html")

    # ── Flash helper ─────────────────────────────────────────────────────────

    from flask import get_flashed_messages

    def get_flashed_messages_safe():
        try:
            return get_flashed_messages(with_categories=True)
        except Exception:
            return []

    app.jinja_env.globals["get_flashed_messages_safe"] = get_flashed_messages_safe


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_server(host="127.0.0.1", port=5000, debug=False):
    app = create_app()
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    run_server(debug=True)
