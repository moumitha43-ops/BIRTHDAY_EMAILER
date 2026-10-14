"""
app.py — BIT Birthday Wishes · Flask backend
Single-admin, SQLite, Playwright screenshots, Gmail SMTP, APScheduler
Deploy on Render.com with persistent disk at /data
"""
import os, time, copy, threading, secrets
from io import BytesIO
from pathlib import Path
from datetime import date
from flask import (Flask, request, jsonify, render_template,
                   session, send_file)
from werkzeug.utils import secure_filename

from helpers import (
    init_db, cfg_get, cfg_set, cfg_set_many, get_all_config,
    is_setup_done, create_admin, check_login, get_admin_username,
    import_csv_file, get_all_students, delete_student,
    get_todays_birthdays, get_birthdays_on, get_month_birthday_map,
    get_template, save_template, get_recent_logs,
    now_local, today_local, find_logo, save_logo, remove_logo, LOGO_EXTS,
    logger, UPLOAD_DIR, OUTPUT_DIR, DATA_DIR
)
from scheduler import start_scheduler, update_schedule, get_next_run
from send import send_all
from pdf_report import generate_birthday_report

# ── Flask setup ────────────────────────────────────────────────────────────────
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024   # 200 MB

# SECRET_KEY — persist to disk so sessions survive restarts
_SK_PATH = DATA_DIR / "secret.key"
if _SK_PATH.exists():
    app.secret_key = _SK_PATH.read_text().strip()
else:
    _sk = secrets.token_hex(32)
    _SK_PATH.write_text(_sk)
    app.secret_key = _sk

# ── Initialise DB immediately on import ───────────────────────────────────────
init_db()
start_scheduler()

# ── In-memory send job state (polled by the dashboard) ────────────────────────
_job_lock = threading.Lock()
_job = {}


def _blank_job():
    return {
        "running": False, "total": 0, "progress": 0,
        "results": [], "error": "",
        "items": [],            # one entry per student: status pending→…→sent/failed
        "events": [],           # live feed (newest last)
        "current": None,        # who is being processed right now
        "counts": {"sent": 0, "failed": 0, "skipped": 0, "cancelled": 0},
        "cancel": False, "started_at": None, "finished_at": None,
    }


_job.update(_blank_job())


def _ev(kind, text):
    """Append to the live feed. Caller must hold _job_lock."""
    _job["events"].append({"t": now_local().strftime("%H:%M:%S"),
                           "kind": kind, "text": text})
    del _job["events"][:-300]


def _start_job(matches):
    with _job_lock:
        _job.clear()
        _job.update(_blank_job())
        _job.update(
            running=True, total=len(matches), started_at=time.time(),
            items=[{"id": m["id"], "name": m["name"], "email": m["email"],
                    "status": "pending", "detail": "", "error": "",
                    "duration": None, "at": ""} for m in matches])
        _ev("info", f"Started — {len(matches)} email(s) queued")


def _on_stage(i, student, stage, detail=""):
    with _job_lock:
        it = _job["items"][i - 1]
        it["status"], it["detail"] = stage, detail
        _job["current"] = {"index": i, "name": student["name"],
                           "email": student["email"],
                           "stage": stage, "detail": detail}
        if stage == "retrying":
            _ev("warn", f"Retrying {student['name']} ({detail})")


def _on_progress(i, total, r):
    with _job_lock:
        it = _job["items"][i - 1]
        it.update(status=r["status"], error=r.get("error", ""),
                  duration=r.get("duration"), at=r.get("at", ""), detail="")
        _job["counts"][r["status"]] = _job["counts"].get(r["status"], 0) + 1
        _job["progress"] = i
        _job["results"].append(r)
        _job["current"] = None
        secs = f" ({r['duration']}s)" if r.get("duration") else ""
        if r["status"] == "sent":
            _ev("ok", f"Sent to {r['name']}{secs}")
        elif r["status"] == "skipped":
            _ev("warn", f"Skipped {r['name']} — already sent today")
        else:
            _ev("err", f"Failed: {r['name']} — {r.get('error', '')[:120]}")


def _should_stop():
    return _job.get("cancel", False)


def _finish_job(error=""):
    with _job_lock:
        stopped = _job["cancel"]
        left = 0
        for it in _job["items"]:
            if it["status"] in ("pending", "rendering", "sending", "retrying"):
                it["status"], it["detail"] = "cancelled", ""
                left += 1
        _job["counts"]["cancelled"] = left
        _job["running"], _job["current"] = False, None
        _job["finished_at"] = time.time()
        if error:
            _job["error"] = error
            _ev("err", f"Run aborted: {error[:160]}")
        elif stopped:
            _ev("warn", f"Stopped by you — {left} email(s) not sent")
        else:
            c = _job["counts"]
            _ev("ok", f"Finished — {c['sent']} sent, {c['failed']} failed, "
                      f"{c['skipped']} skipped")


# ── Auth helpers ───────────────────────────────────────────────────────────────
def logged_in() -> bool:
    return session.get("auth") is True


def require_login(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*a, **kw):
        if not logged_in():
            return jsonify({"error": "Not authenticated."}), 401
        return fn(*a, **kw)
    return wrapper


# ══════════════════════════════════════════════════════════════════════════════
# PAGE
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")


# ══════════════════════════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/auth/status", methods=["GET"])
def api_auth_status():
    """
    Called by frontend on every page load.
    setup_needed is always False — credentials are hardcoded.
    """
    return jsonify({
        "ok":           True,
        "setup_needed": False,
        "logged_in":    logged_in(),
        "username":     get_admin_username() if logged_in() else "",
        "logo":         _logo_version(),
    })


@app.route("/api/setup", methods=["POST"])
def api_setup():
    # Setup is disabled — credentials are hardcoded
    return jsonify({"error": "Setup is disabled."}), 400


@app.route("/api/login", methods=["POST"])
def api_login():
    d = request.get_json(silent=True) or {}
    username = (d.get("username") or "").strip()
    password = d.get("password", "")

    if not username or not password:
        return jsonify({"error": "Username and password are required."}), 400

    if check_login(username, password):
        session.permanent = True
        session["auth"]   = True
        logger.info("Admin logged in: %s", username)
        return jsonify({"message": "Logged in.", "username": username})

    logger.warning("Failed login attempt for: %s", username)
    return jsonify({"error": "Incorrect username or password."}), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"message": "Logged out."})


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/dashboard", methods=["GET"])
@require_login
def api_dashboard():
    matches  = get_todays_birthdays()
    students = get_all_students()
    today    = today_local()
    return jsonify({
        "date":           today.strftime("%A, %d %B %Y"),
        "count":          len(matches),
        "matches":        matches,
        "total_students": len(students),
        "skipped":        0,
    })


# ══════════════════════════════════════════════════════════════════════════════
# STUDENTS
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/students", methods=["GET"])
@require_login
def api_students():
    return jsonify({"students": get_all_students()})


@app.route("/api/students/<int:sid>", methods=["DELETE"])
@require_login
def api_delete_student(sid):
    delete_student(sid)
    logger.info("Deleted student id=%d", sid)
    return jsonify({"message": "Student removed."})


# ══════════════════════════════════════════════════════════════════════════════
# CSV
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/csv/upload", methods=["POST"])
@require_login
def api_csv_upload():
    if "file" not in request.files:
        return jsonify({"error": "No file provided."}), 400
    f = request.files["file"]
    if not f.filename.lower().endswith(".csv"):
        return jsonify({"error": "Only .csv files are accepted."}), 400

    dest = UPLOAD_DIR / secure_filename(f.filename)
    f.save(str(dest))
    try:
        result = import_csv_file(str(dest))
    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    except Exception as e:
        logger.error("CSV import error: %s", e)
        return jsonify({"error": str(e)}), 500

    return jsonify(result)


# ══════════════════════════════════════════════════════════════════════════════
# CALENDAR
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/calendar/<int:month>", methods=["GET"])
@require_login
def api_calendar_month(month):
    if not 1 <= month <= 12:
        return jsonify({"error": "Invalid month."}), 400
    return jsonify(get_month_birthday_map(month))


@app.route("/api/calendar/pdf/<int:day>/<int:month>", methods=["GET"])
@require_login
def api_calendar_pdf(day, month):
    import calendar as cal_mod
    students = get_birthdays_on(day, month)
    try:
        qdate = date(today_local().year, month, day)
    except ValueError:
        qdate = None
    pdf_bytes = generate_birthday_report(
        students,
        query_date=qdate,
        title=f"Birthday Report — {day:02d} {cal_mod.month_name[month]}",
    )
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"BIT_Birthdays_{day:02d}_{month:02d}.pdf",
    )


@app.route("/api/report/today/pdf", methods=["GET"])
@require_login
def api_today_pdf():
    today     = today_local()
    students  = get_todays_birthdays()
    pdf_bytes = generate_birthday_report(
        students,
        query_date=today,
        title=f"Today's Birthday Report — {today.strftime('%d %B %Y')}",
    )
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"BIT_Birthdays_Today_{today.isoformat()}.pdf",
    )


# ══════════════════════════════════════════════════════════════════════════════
# SEND
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/send", methods=["POST"])
@require_login
def api_send():
    if _job.get("running"):
        return jsonify({"error": "A send job is already in progress."}), 409

    matches = get_todays_birthdays()
    if not matches:
        return jsonify({"message": "No birthdays today — nothing to send.", "total": 0})

    _start_job(matches)

    def _run():
        err = ""
        try:
            send_all(matches, progress_cb=_on_progress,
                     stage_cb=_on_stage, should_stop=_should_stop)
        except Exception as e:
            logger.error("Send job error: %s", e)
            err = str(e)
        finally:
            _finish_job(error=err)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({
        "message": f"Sending to {len(matches)} student(s)…",
        "total":   len(matches),
    })


@app.route("/api/send/status", methods=["GET"])
@require_login
def api_send_status():
    with _job_lock:
        j = copy.deepcopy(_job)
    now = time.time()
    started = j.get("started_at")
    end = j.get("finished_at") or now
    j["elapsed"] = round(end - started, 1) if started else 0
    sent_times = [i["duration"] for i in j["items"]
                  if i["status"] == "sent" and i.get("duration")]
    left = j["total"] - j["progress"]
    j["eta"] = (round(sum(sent_times) / len(sent_times) * left)
                if j["running"] and sent_times else None)
    j["events"] = j["events"][-60:]
    return jsonify(j)


@app.route("/api/send/cancel", methods=["POST"])
@require_login
def api_send_cancel():
    with _job_lock:
        if not _job.get("running"):
            return jsonify({"error": "Nothing is being sent."}), 400
        _job["cancel"] = True
        _ev("warn", "Stop requested — finishing the current email first…")
    return jsonify({"message": "Stopping after the current email."})


# ══════════════════════════════════════════════════════════════════════════════
# COLLEGE LOGO
# ══════════════════════════════════════════════════════════════════════════════

def _logo_version() -> str:
    """'' if no logo, else a cache-busting token (file mtime)."""
    p = find_logo()
    return str(int(p.stat().st_mtime)) if p else ""


@app.route("/api/logo", methods=["GET"])
def api_logo_get():
    p = find_logo()
    if not p:
        return jsonify({"error": "No logo."}), 404
    return send_file(str(p), max_age=0)


@app.route("/api/logo", methods=["POST"])
@require_login
def api_logo_upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file provided."}), 400
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in LOGO_EXTS:
        return jsonify({"error": "Logo must be a PNG or JPG image."}), 400
    data = f.read()
    if len(data) > 3 * 1024 * 1024:
        return jsonify({"error": "Logo is larger than 3 MB."}), 400
    try:
        from PIL import Image
        Image.open(BytesIO(data)).verify()
    except Exception:
        return jsonify({"error": "That file is not a valid image."}), 400
    save_logo(data, "jpg" if ext == "jpeg" else ext)
    return jsonify({"message": "Logo saved.", "logo": _logo_version()})


@app.route("/api/logo", methods=["DELETE"])
@require_login
def api_logo_delete():
    remove_logo()
    return jsonify({"message": "Logo removed.", "logo": _logo_version()})


# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATE
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/template", methods=["GET"])
@require_login
def api_get_template():
    return jsonify({"html": get_template()})


@app.route("/api/template", methods=["POST"])
@require_login
def api_save_template():
    d    = request.get_json(silent=True) or {}
    html = d.get("html", "").strip()
    if not html:
        return jsonify({"error": "Template cannot be empty."}), 400
    save_template(html)
    return jsonify({"message": "Template saved."})


@app.route("/api/template/upload", methods=["POST"])
@require_login
def api_upload_template():
    if "file" not in request.files:
        return jsonify({"error": "No file provided."}), 400
    f    = request.files["file"]
    html = f.read().decode("utf-8", errors="replace").strip()
    if not html:
        return jsonify({"error": "Template file is empty."}), 400
    save_template(html)
    logger.info("Template uploaded from file")
    return jsonify({"message": "Template uploaded and saved."})


@app.route("/api/template/preview/<int:sid>", methods=["GET"])
@require_login
def api_preview(sid):
    students = get_all_students()
    student  = next((s for s in students if s["id"] == sid), None)
    if not student:
        return jsonify({"error": "Student not found."}), 404
    tpl = get_template()
    if not tpl:
        return jsonify({"error": "No template uploaded yet."}), 404
    return jsonify({"html": tpl.replace("{{name}}", student["name"])})


# ══════════════════════════════════════════════════════════════════════════════
# SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/settings", methods=["GET"])
@require_login
def api_get_settings():
    cfg  = get_all_config()
    safe = {k: v for k, v in cfg.items()
            if k not in ("app_password", "admin_pass_hash")}
    safe["password_set"] = bool(cfg.get("app_password"))
    safe["next_run"]     = get_next_run()
    return jsonify(safe)


@app.route("/api/settings", methods=["POST"])
@require_login
def api_save_settings():
    d       = request.get_json(silent=True) or {}
    allowed = {"smtp_server", "smtp_port", "sender_email", "app_password",
               "send_time", "timezone", "auto_send"}
    updates = {k: v for k, v in d.items() if k in allowed}
    if not updates:
        return jsonify({"error": "No valid fields to update."}), 400

    cfg_set_many(updates)

    if any(k in updates for k in ("send_time", "auto_send", "timezone")):
        update_schedule(
            send_time=updates.get("send_time"),
            auto_send=updates.get("auto_send") in (True, "true", "True"),
            timezone=updates.get("timezone"),
        )

    logger.info("Settings updated: %s",
                [k for k in updates if k != "app_password"])
    return jsonify({"message": "Settings saved.", "next_run": get_next_run()})


# ══════════════════════════════════════════════════════════════════════════════
# LOGS
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/logs", methods=["GET"])
@require_login
def api_logs():
    n = int(request.args.get("n", 100))
    return jsonify({"logs": get_recent_logs(n)})


# ══════════════════════════════════════════════════════════════════════════════
# RUN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info("BIT Birthday Wishes starting on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=False)
