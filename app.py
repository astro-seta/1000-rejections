import os
import re
import sqlite3
from functools import wraps
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from flask import (Flask, render_template, request, jsonify, g,
                   session, redirect, url_for)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
IS_PROD = os.environ.get("APP_ENV") == "production"
_secret = os.environ.get("SECRET_KEY")
if IS_PROD and not _secret:
    raise RuntimeError("SECRET_KEY must be set in production")

app.config["SECRET_KEY"] = _secret or "dev-only-change-me"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = IS_PROD
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024  # reject request bodies over 32 KB

if IS_PROD:
    # Behind nginx: read the visitor's real IP from the forwarded headers
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["600 per hour"],
    storage_uri="memory://",
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "app.db")

VALID_STATUSES = {"pending", "rejected", "success"}
TOTAL_CELLS = 1000
USERNAME_RE = re.compile(r"^[a-z0-9_]{3,30}$")
VALID_THEMES = {"coquette", "minimal", "midnight", "sage", "cbsafe"}


# ---------- Database helpers ----------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_FILE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_FILE)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS cells (
            user_id INTEGER NOT NULL,
            cell_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            date TEXT,
            outcome_date TEXT,
            PRIMARY KEY (user_id, cell_id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)

    cols = [r[1] for r in db.execute("PRAGMA table_info(users)")]
    if "theme" not in cols:
        db.execute("ALTER TABLE users ADD COLUMN theme TEXT NOT NULL DEFAULT 'coquette'")

    db.commit()
    db.close()


init_db()

@app.after_request
def add_security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp

@app.errorhandler(429)
def too_many_requests(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "too many requests"}), 429
    mode = "signup" if request.path == "/signup" else "login"
    return render_template(
        "auth.html", mode=mode, username="",
        error="Too many attempts. Please wait a while and try again."
    ), 429

# ---------- Auth helpers ----------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "not logged in"}), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def current_user_id():
    return session["user_id"]


# ---------- Auth routes ----------
@app.route("/signup", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def signup():
    if "user_id" in session:
        return redirect(url_for("index"))
    error, username = None, ""
    if request.method == "POST":
                    if request.form.get("alt_contact"):
            # Bots fill the hidden field. Pretend nothing happened.
            return redirect(url_for("login"))
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        if not USERNAME_RE.match(username):
            error = "Username must be 3-30 characters: letters, numbers or underscore."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif len(password) > 128:
            error = "Password is too long (max 128 characters)."
        else:
            db = get_db()
            try:
                cur = db.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    (username, generate_password_hash(password)),
                )
                db.commit()
            except sqlite3.IntegrityError:
                error = "That username is already taken."
            else:
                session.clear()
                session["user_id"] = cur.lastrowid
                session["username"] = username
                return redirect(url_for("index"))
    return render_template("auth.html", mode="signup", error=error, username=username)


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute; 100 per hour", methods=["POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("index"))
    error, username = None, ""
    if request.method == "POST":

 
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        row = get_db().execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if row and len(password) <= 128 and check_password_hash(row["password_hash"], password):
            session.clear()
            session["user_id"] = row["id"]
            session["username"] = row["username"]
            return redirect(url_for("index"))
        error = "Invalid username or password."
    return render_template("auth.html", mode="login", error=error, username=username)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- App routes ----------
@app.route("/")
@login_required
def index():
    row = get_db().execute(
        "SELECT theme FROM users WHERE id = ?", (current_user_id(),)
    ).fetchone()
    theme = row["theme"] if row else "coquette"
    return render_template("index.html", username=session["username"], theme=theme)


@app.route("/api/cells", methods=["GET"])
@login_required
def get_cells():
    rows = get_db().execute(
        "SELECT * FROM cells WHERE user_id = ?", (current_user_id(),)
    ).fetchall()
    result = {}
    for r in rows:
        entry = {
            "title": r["title"],
            "desc": r["description"],
            "status": r["status"],
            "date": r["date"],
        }
        if r["outcome_date"]:
            entry["outcomeDate"] = r["outcome_date"]
        result[str(r["cell_id"])] = entry
    return jsonify(result)


@app.route("/api/cells/<int:cell_id>", methods=["POST"])
@login_required
def update_cell(cell_id):
    if not 1 <= cell_id <= TOTAL_CELLS:
        return jsonify({"error": "invalid cell"}), 400

    payload = request.get_json(silent=True) or {}
    title = str(payload.get("title", "")).strip()[:80]
    desc = str(payload.get("desc", "")).strip()[:1000]
    status = payload.get("status")

    if not title or status not in VALID_STATUSES:
        return jsonify({"error": "invalid data"}), 400

    db = get_db()
    db.execute(
        """
        INSERT INTO cells (user_id, cell_id, title, description, status, date, outcome_date)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, cell_id) DO UPDATE SET
            title = excluded.title,
            description = excluded.description,
            status = excluded.status,
            date = excluded.date,
            outcome_date = excluded.outcome_date
        """,
        (current_user_id(), cell_id, title, desc, status,
         payload.get("date"), payload.get("outcomeDate")),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/cells/<int:cell_id>", methods=["DELETE"])
@login_required
def delete_cell(cell_id):
    db = get_db()
    db.execute(
        "DELETE FROM cells WHERE user_id = ? AND cell_id = ?",
        (current_user_id(), cell_id),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/theme", methods=["POST"])
@login_required
def set_theme():
    theme = (request.get_json(silent=True) or {}).get("theme")
    if theme not in VALID_THEMES:
        return jsonify({"error": "invalid theme"}), 400
    db = get_db()
    db.execute("UPDATE users SET theme = ? WHERE id = ?", (theme, current_user_id()))
    db.commit()
    return jsonify({"ok": True})

@app.route("/api/session", methods=["GET"])
@login_required
def check_session():
    resp = jsonify({"ok": True})
    resp.headers["Cache-Control"] = "no-store"
    return resp

if __name__ == "__main__":
    app.run(debug=not IS_PROD, port=5000)