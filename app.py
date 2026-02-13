import os
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from flask import Flask, flash, g, redirect, render_template, request, send_from_directory, url_for

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "expenses.db"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = "dev-secret"


STATUS_SUBMITTED = "SUBMITTED_PENDING_MANAGER"
STATUS_PENDING_SUPERVISOR = "PENDING_SUPERVISOR"
STATUS_REJECTED_MANAGER = "REJECTED_BY_MANAGER"
STATUS_REJECTED_SUPERVISOR = "REJECTED_BY_SUPERVISOR"
STATUS_APPROVED = "APPROVED_FINAL"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_error):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            role TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS contracts (
            id INTEGER PRIMARY KEY,
            contract_number TEXT NOT NULL,
            name TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS charge_codes (
            id INTEGER PRIMARY KEY,
            contract_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            description TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(contract_id) REFERENCES contracts(id)
        );

        CREATE TABLE IF NOT EXISTS expense_batches (
            id INTEGER PRIMARY KEY,
            employee_id INTEGER NOT NULL,
            manager_id INTEGER NOT NULL,
            supervisor_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            rejection_reason TEXT,
            submitted_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(employee_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS expense_items (
            id INTEGER PRIMARY KEY,
            batch_id INTEGER NOT NULL,
            expense_date TEXT NOT NULL,
            description TEXT NOT NULL,
            amount REAL NOT NULL,
            contract_id INTEGER NOT NULL,
            charge_code_id INTEGER NOT NULL,
            receipt_path TEXT NOT NULL,
            FOREIGN KEY(batch_id) REFERENCES expense_batches(id),
            FOREIGN KEY(contract_id) REFERENCES contracts(id),
            FOREIGN KEY(charge_code_id) REFERENCES charge_codes(id)
        );

        CREATE TABLE IF NOT EXISTS approval_actions (
            id INTEGER PRIMARY KEY,
            batch_id INTEGER NOT NULL,
            actor_id INTEGER NOT NULL,
            actor_role TEXT NOT NULL,
            action TEXT NOT NULL,
            comment TEXT,
            acted_at TEXT NOT NULL,
            FOREIGN KEY(batch_id) REFERENCES expense_batches(id)
        );

        CREATE TABLE IF NOT EXISTS notification_events (
            id INTEGER PRIMARY KEY,
            batch_id INTEGER NOT NULL,
            recipient_user_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            FOREIGN KEY(batch_id) REFERENCES expense_batches(id)
        );
        """
    )

    user_count = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] if False else db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if user_count == 0:
        db.executemany(
            "INSERT INTO users (id, name, email, role) VALUES (?, ?, ?, ?)",
            [
                (1, "Erin Employee", "erin@example.com", "EMPLOYEE"),
                (2, "Maya Manager", "manager@example.com", "MANAGER"),
                (3, "Sam Supervisor", "supervisor@example.com", "SUPERVISOR"),
            ],
        )

    contract_count = db.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
    if contract_count == 0:
        db.executemany(
            "INSERT INTO contracts (id, contract_number, name) VALUES (?, ?, ?)",
            [
                (1, "GOV-1001", "USAF Logistics Modernization"),
                (2, "GOV-2002", "Navy Port Systems Refresh"),
            ],
        )
        db.executemany(
            "INSERT INTO charge_codes (contract_id, code, description, active) VALUES (?, ?, ?, 1)",
            [
                (1, "TRAVEL-01", "Program travel"),
                (1, "MEALS-02", "Customer meeting meals"),
                (2, "EQP-03", "Field equipment"),
                (2, "TRANS-04", "Shipping and transportation"),
            ],
        )

    db.commit()
    db.close()


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds")


def save_receipt(file_storage):
    extension = Path(file_storage.filename).suffix
    name = f"{uuid4().hex}{extension}"
    destination = UPLOAD_DIR / name
    file_storage.save(destination)
    return name


def send_notification(batch_id, recipient_id, subject, body):
    db = get_db()
    db.execute(
        "INSERT INTO notification_events (batch_id, recipient_user_id, subject, body, sent_at) VALUES (?, ?, ?, ?, ?)",
        (batch_id, recipient_id, subject, body, now_iso()),
    )
    db.commit()


def get_common_data():
    db = get_db()
    contracts = db.execute("SELECT * FROM contracts ORDER BY contract_number").fetchall()
    codes = db.execute("SELECT * FROM charge_codes WHERE active = 1 ORDER BY code").fetchall()
    return contracts, codes


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/employee/new", methods=["GET", "POST"])
def employee_new():
    db = get_db()
    contracts, codes = get_common_data()

    if request.method == "POST":
        rows = int(request.form.get("row_count", "1"))
        errors = []
        parsed = []

        for i in range(rows):
            prefix = f"items-{i}-"
            expense_date = request.form.get(prefix + "date", "").strip()
            description = request.form.get(prefix + "description", "").strip()
            amount_raw = request.form.get(prefix + "amount", "").strip()
            contract_id = request.form.get(prefix + "contract_id", "").strip()
            charge_code_id = request.form.get(prefix + "charge_code_id", "").strip()
            receipt = request.files.get(prefix + "receipt")

            if not any([expense_date, description, amount_raw, contract_id, charge_code_id, receipt and receipt.filename]):
                continue

            if not expense_date or not description or not amount_raw or not contract_id or not charge_code_id:
                errors.append(f"Row {i + 1}: All fields are required.")
                continue

            try:
                amount = float(amount_raw)
                if amount <= 0:
                    raise ValueError
            except ValueError:
                errors.append(f"Row {i + 1}: Amount must be greater than zero.")
                continue

            code_row = db.execute(
                "SELECT id FROM charge_codes WHERE id = ? AND contract_id = ? AND active = 1",
                (charge_code_id, contract_id),
            ).fetchone()
            if not code_row:
                errors.append(f"Row {i + 1}: Charge code is invalid for selected contract.")
                continue

            if not receipt or not receipt.filename:
                errors.append(f"Row {i + 1}: Receipt is required.")
                continue

            receipt_name = save_receipt(receipt)
            parsed.append((expense_date, description, amount, int(contract_id), int(charge_code_id), receipt_name))

        if not parsed:
            errors.append("At least one valid expense row is required.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("employee_new.html", contracts=contracts, codes=codes)

        ts = now_iso()
        cursor = db.execute(
            """INSERT INTO expense_batches (employee_id, manager_id, supervisor_id, status, submitted_at, updated_at)
               VALUES (1, 2, 3, ?, ?, ?)""",
            (STATUS_SUBMITTED, ts, ts),
        )
        batch_id = cursor.lastrowid

        db.executemany(
            """INSERT INTO expense_items
               (batch_id, expense_date, description, amount, contract_id, charge_code_id, receipt_path)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(batch_id, *item) for item in parsed],
        )
        db.commit()

        send_notification(
            batch_id,
            2,
            f"Expense batch #{batch_id} submitted",
            "An expense batch requires your approval.",
        )
        flash(f"Expense batch #{batch_id} submitted to manager.", "success")
        return redirect(url_for("employee_batches"))

    return render_template("employee_new.html", contracts=contracts, codes=codes)


@app.route("/employee/batches")
def employee_batches():
    db = get_db()
    batches = db.execute(
        """SELECT b.*, u.name AS manager_name
           FROM expense_batches b
           JOIN users u ON u.id = b.manager_id
           WHERE b.employee_id = 1
           ORDER BY b.id DESC"""
    ).fetchall()
    return render_template("employee_batches.html", batches=batches)


@app.route("/manager/queue")
def manager_queue():
    db = get_db()
    batches = db.execute(
        """SELECT b.*, u.name AS employee_name
           FROM expense_batches b
           JOIN users u ON u.id = b.employee_id
           WHERE b.manager_id = 2 AND b.status = ?
           ORDER BY b.id DESC""",
        (STATUS_SUBMITTED,),
    ).fetchall()
    return render_template("manager_queue.html", batches=batches)


@app.route("/supervisor/queue")
def supervisor_queue():
    db = get_db()
    batches = db.execute(
        """SELECT b.*, u.name AS employee_name
           FROM expense_batches b
           JOIN users u ON u.id = b.employee_id
           WHERE b.supervisor_id = 3 AND b.status = ?
           ORDER BY b.id DESC""",
        (STATUS_PENDING_SUPERVISOR,),
    ).fetchall()
    return render_template("supervisor_queue.html", batches=batches)


@app.route("/batch/<int:batch_id>")
def batch_detail(batch_id):
    db = get_db()
    batch = db.execute(
        """SELECT b.*, e.name AS employee_name, m.name AS manager_name, s.name AS supervisor_name
           FROM expense_batches b
           JOIN users e ON e.id = b.employee_id
           JOIN users m ON m.id = b.manager_id
           JOIN users s ON s.id = b.supervisor_id
           WHERE b.id = ?""",
        (batch_id,),
    ).fetchone()
    if not batch:
        return "Not found", 404

    items = db.execute(
        """SELECT i.*, c.contract_number, cc.code AS charge_code
           FROM expense_items i
           JOIN contracts c ON c.id = i.contract_id
           JOIN charge_codes cc ON cc.id = i.charge_code_id
           WHERE i.batch_id = ?""",
        (batch_id,),
    ).fetchall()

    actions = db.execute(
        """SELECT a.*, u.name AS actor_name
           FROM approval_actions a
           JOIN users u ON u.id = a.actor_id
           WHERE a.batch_id = ?
           ORDER BY a.id""",
        (batch_id,),
    ).fetchall()
    return render_template("batch_detail.html", batch=batch, items=items, actions=actions)


def update_batch_status(batch_id, actor_id, actor_role, action, comment):
    db = get_db()
    batch = db.execute("SELECT * FROM expense_batches WHERE id = ?", (batch_id,)).fetchone()
    if not batch:
        flash("Batch not found.", "error")
        return redirect(url_for("index"))

    ts = now_iso()
    if actor_role == "MANAGER":
        if action == "APPROVE":
            new_status = STATUS_PENDING_SUPERVISOR
            notify_id = batch["supervisor_id"]
            subject = f"Expense batch #{batch_id} awaiting supervisor review"
            body = "Manager approved the batch. Your review is required."
        else:
            new_status = STATUS_REJECTED_MANAGER
            notify_id = batch["employee_id"]
            subject = f"Expense batch #{batch_id} rejected by manager"
            body = f"Manager rejected batch: {comment}"
    else:
        if action == "APPROVE":
            new_status = STATUS_APPROVED
            notify_id = batch["employee_id"]
            subject = f"Expense batch #{batch_id} fully approved"
            body = "Your expense batch has received final approval."
        else:
            new_status = STATUS_REJECTED_SUPERVISOR
            notify_id = batch["employee_id"]
            subject = f"Expense batch #{batch_id} rejected by supervisor"
            body = f"Supervisor rejected batch: {comment}"

    db.execute(
        "UPDATE expense_batches SET status = ?, rejection_reason = ?, updated_at = ? WHERE id = ?",
        (new_status, comment if action == "REJECT" else None, ts, batch_id),
    )
    db.execute(
        """INSERT INTO approval_actions (batch_id, actor_id, actor_role, action, comment, acted_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (batch_id, actor_id, actor_role, action, comment, ts),
    )
    db.commit()

    send_notification(batch_id, notify_id, subject, body)
    flash(f"Batch #{batch_id} {action.lower()}d.", "success")
    return redirect(url_for("batch_detail", batch_id=batch_id))


@app.route("/manager/batch/<int:batch_id>/approve", methods=["POST"])
def manager_approve(batch_id):
    return update_batch_status(batch_id, 2, "MANAGER", "APPROVE", request.form.get("comment", "").strip())


@app.route("/manager/batch/<int:batch_id>/reject", methods=["POST"])
def manager_reject(batch_id):
    comment = request.form.get("comment", "").strip()
    if not comment:
        flash("Rejection comment is required.", "error")
        return redirect(url_for("batch_detail", batch_id=batch_id))
    return update_batch_status(batch_id, 2, "MANAGER", "REJECT", comment)


@app.route("/supervisor/batch/<int:batch_id>/approve", methods=["POST"])
def supervisor_approve(batch_id):
    return update_batch_status(batch_id, 3, "SUPERVISOR", "APPROVE", request.form.get("comment", "").strip())


@app.route("/supervisor/batch/<int:batch_id>/reject", methods=["POST"])
def supervisor_reject(batch_id):
    comment = request.form.get("comment", "").strip()
    if not comment:
        flash("Rejection comment is required.", "error")
        return redirect(url_for("batch_detail", batch_id=batch_id))
    return update_batch_status(batch_id, 3, "SUPERVISOR", "REJECT", comment)


@app.route("/receipts/<path:filename>")
def receipts(filename):
    return send_from_directory(UPLOAD_DIR, filename)


if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
