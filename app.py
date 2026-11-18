import os
import sqlite3
from flask import Flask, render_template, request, jsonify, g

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "app.db")
TEMP_USER_ID = 1  # placeholder until login is added in the next step

VALID_STATUSES = {"pending", "rejected", "success"}
TOTAL_CELLS = 1000


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
    db.execute(
        "INSERT OR IGNORE INTO users (id, username, password_hash) "
        "VALUES (1, 'temp', 'x')"
    )
    db.commit()
    db.close()


init_db()


# ---------- Routes ----------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/cells", methods=["GET"])
def get_cells():
    rows = get_db().execute(
        "SELECT * FROM cells WHERE user_id = ?", (TEMP_USER_ID,)
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
        (TEMP_USER_ID, cell_id, title, desc, status,
         payload.get("date"), payload.get("outcomeDate")),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/cells/<int:cell_id>", methods=["DELETE"])
def delete_cell(cell_id):
    db = get_db()
    db.execute(
        "DELETE FROM cells WHERE user_id = ? AND cell_id = ?",
        (TEMP_USER_ID, cell_id),
    )
    db.commit()
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, port=5000)