import json
import re
import sqlite3
import threading
import uuid
import hashlib
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "clinic.db"
_lock = threading.Lock()
MAX_DAYS_AHEAD = 14

DOCTORS = [
    ("d1", "Dr. Aisha Rao", "General Physician", ["09:00", "09:30", "10:00", "11:00", "14:00", "15:30"]),
    ("d2", "Dr. Vikram Mehta", "Cardiologist", ["10:00", "10:30", "12:00", "16:00", "17:00"]),
    ("d3", "Dr. Priya Nair", "Dermatologist", ["09:15", "11:15", "13:00", "15:00", "16:30"]),
    ("d4", "Dr. Sameer Khan", "Orthopedic Surgeon", ["08:30", "10:30", "12:30", "14:30", "16:00"]),
    ("d5", "Dr. Ananya Sharma", "Pediatrician", ["09:00", "10:00", "11:30", "14:00", "15:00"]),
    ("d6", "Dr. Rajesh Gupta", "Neurologist", ["10:00", "11:30", "13:30", "15:30", "17:00"]),
    ("d7", "Dr. Sneha Kulkarni", "ENT Specialist", ["09:30", "11:00", "12:30", "14:30", "16:30"]),
    ("d8", "Dr. Rohan Verma", "Psychiatrist", ["11:00", "12:00", "14:00", "16:00", "17:30"]),
    ("d9", "Dr. Meera Nambiar", "Gynecologist", ["09:00", "10:30", "12:00", "14:00", "15:30"]),
    ("d10", "Dr. Amit Patel", "Ophthalmologist", ["08:30", "10:00", "11:30", "13:30", "15:00"]),
]


class DataError(Exception):
    """Business-rule error whose message is safe to read to the LLM."""


@contextmanager
def _db():
    with _lock:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def _hash_pw(pw: str) -> str:
    return hashlib.sha256(f"salt_ziora_{pw}".encode()).hexdigest()


def init_db() -> None:
    with _db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS doctors (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                speciality TEXT NOT NULL, slots TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS appointments (
                id TEXT PRIMARY KEY, phone TEXT NOT NULL,
                doctor_id TEXT NOT NULL REFERENCES doctors(id),
                date TEXT NOT NULL, slot TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'booked',
                user_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                picture TEXT,
                password_hash TEXT,
                provider TEXT NOT NULL DEFAULT 'local',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            -- two active bookings can never share the same doctor/date/slot
            CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_slot
                ON appointments(doctor_id, date, slot) WHERE status = 'booked';
            """
        )
        # Check if user_id column exists in appointments
        cols = [r["name"] for r in c.execute("PRAGMA table_info(appointments)").fetchall()]
        if "user_id" not in cols:
            c.execute("ALTER TABLE appointments ADD COLUMN user_id TEXT")

        # Always upsert all doctors
        c.executemany(
            "INSERT OR REPLACE INTO doctors (id, name, speciality, slots) VALUES (?,?,?,?)",
            [(i, n, s, json.dumps(sl)) for i, n, s, sl in DOCTORS],
        )


# ---------- helpers ----------
def today() -> str:
    return date.today().isoformat()


def tomorrow() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


def normalize_phone(raw: str) -> str:
    """'+91 98765 43210' / '9876543210' -> '9876543210'. Raises DataError if not 10 digits."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) > 10:
        digits = digits[-10:]
    if len(digits) != 10:
        raise DataError("Phone number must have 10 digits. Ask the caller to repeat it.")
    return digits


def format_phone(d: str) -> str:
    return f"+91 {d[:5]} {d[5:]}"


def parse_date(raw: str | None) -> str:
    if not raw:
        return tomorrow()
    try:
        d = date.fromisoformat(raw.strip())
    except ValueError:
        raise DataError("Date must be in YYYY-MM-DD format.")
    if d < date.today():
        raise DataError("That date is in the past.")
    if d > date.today() + timedelta(days=MAX_DAYS_AHEAD):
        raise DataError(f"Bookings are only open for the next {MAX_DAYS_AHEAD} days.")
    return d.isoformat()


def normalize_time(raw: str) -> str:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})", raw or "")
    if not m:
        raise DataError("Time must be in HH:MM 24-hour format.")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _appt(r: sqlite3.Row) -> dict:
    keys = r.keys()
    return {
        "id": r["id"], "phone": format_phone(r["phone"]),
        "doctorId": r["doctor_id"], "doctorName": r["name"],
        "speciality": r["speciality"], "date": r["date"],
        "slot": r["slot"], "status": r["status"],
        "userId": r["user_id"] if "user_id" in keys else None,
    }


_APPT_SQL = """SELECT a.*, d.name, d.speciality FROM appointments a
               JOIN doctors d ON d.id = a.doctor_id"""


# ---------- users / auth ----------
def create_user(email: str, password: str | None = None, name: str = "", picture: str | None = None, provider: str = "local") -> dict:
    email = email.lower().strip()
    user_id = "u-" + uuid.uuid4().hex[:8]
    pw_hash = _hash_pw(password) if password else None
    display_name = name.strip() if name and name.strip() else email.split("@")[0].title()
    with _db() as c:
        existing = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if existing:
            raise DataError("An account with this email already exists.")
        c.execute(
            "INSERT INTO users (id, email, name, picture, password_hash, provider) VALUES (?,?,?,?,?,?)",
            (user_id, email, display_name, picture, pw_hash, provider)
        )
        return {"id": user_id, "email": email, "name": display_name, "picture": picture, "provider": provider}


def authenticate_user(email: str, password: str) -> dict:
    email = email.lower().strip()
    pw_hash = _hash_pw(password)
    with _db() as c:
        row = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not row or row["password_hash"] != pw_hash:
            raise DataError("Invalid email or password.")
        return {"id": row["id"], "email": row["email"], "name": row["name"], "picture": row["picture"], "provider": row["provider"]}


def get_or_create_google_user(email: str, name: str = "", picture: str | None = None, google_id: str | None = None) -> dict:
    email = email.lower().strip()
    display_name = name.strip() if name and name.strip() else email.split("@")[0].title()
    with _db() as c:
        row = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if row:
            c.execute("UPDATE users SET name=?, picture=? WHERE email=?", (display_name or row["name"], picture or row["picture"], email))
            return {"id": row["id"], "email": row["email"], "name": display_name or row["name"], "picture": picture or row["picture"], "provider": "google"}
        user_id = google_id or ("u-" + uuid.uuid4().hex[:8])
        c.execute(
            "INSERT INTO users (id, email, name, picture, provider) VALUES (?,?,?,?,?)",
            (user_id, email, display_name, picture, "google")
        )
        return {"id": user_id, "email": email, "name": display_name, "picture": picture, "provider": "google"}


def get_user_by_id(user_id: str) -> dict | None:
    with _db() as c:
        row = c.execute("SELECT * FROM users WHERE id=? OR email=?", (user_id, user_id)).fetchone()
        if not row:
            return None
        return {"id": row["id"], "email": row["email"], "name": row["name"], "picture": row["picture"], "provider": row["provider"]}


# ---------- doctors & availability ----------
def list_doctors() -> list[dict]:
    with _db() as c:
        return [
            {"id": r["id"], "name": r["name"], "speciality": r["speciality"], "slots": json.loads(r["slots"])}
            for r in c.execute("SELECT * FROM doctors ORDER BY id")
        ]


def specialities() -> list[str]:
    return list(dict.fromkeys([d["speciality"] for d in list_doctors()]))


def find_doctors(name: str | None = None, speciality: str | None = None) -> list[dict]:
    docs = list_doctors()
    if name:
        n = re.sub(r"^dr\.?\s*", "", name.lower().strip())
        docs = [d for d in docs if n and n in d["name"].lower()]
    if speciality:
        s = speciality.lower().strip()
        docs = [d for d in docs if s in d["speciality"].lower() or d["speciality"].lower() in s]
    return docs


def free_slots(doctor_id: str, day: str) -> list[str]:
    with _db() as c:
        row = c.execute("SELECT slots FROM doctors WHERE id=?", (doctor_id,)).fetchone()
        if not row:
            return []
        taken = {
            r["slot"] for r in c.execute(
                "SELECT slot FROM appointments WHERE doctor_id=? AND date=? AND status='booked'",
                (doctor_id, day))
        }
    slots = [s for s in json.loads(row["slots"]) if s not in taken]
    if day == today():  # hide slots that already passed today
        now = datetime.now().strftime("%H:%M")
        slots = [s for s in slots if s > now]
    return slots


# ---------- appointments ----------
def all_appointments(user_id: str | None = None) -> list[dict]:
    with _db() as c:
        if user_id:
            # Filter specifically by logged in user account
            return [_appt(r) for r in c.execute(_APPT_SQL + " WHERE a.user_id=? ORDER BY a.rowid DESC", (user_id,))]
        return [_appt(r) for r in c.execute(_APPT_SQL + " ORDER BY a.rowid DESC")]


def active_appointments(phone: str, user_id: str | None = None) -> list[dict]:
    with _db() as c:
        if user_id:
            return [_appt(r) for r in c.execute(
                _APPT_SQL + " WHERE a.phone=? AND a.status='booked' AND a.user_id=? ORDER BY a.date, a.slot", (phone, user_id))]
        return [_appt(r) for r in c.execute(
            _APPT_SQL + " WHERE a.phone=? AND a.status='booked' ORDER BY a.date, a.slot", (phone,))]


def book(phone: str, doctor_id: str, day: str, slot: str, user_id: str | None = None) -> dict:
    slot = normalize_time(slot)
    with _db() as c:
        doc = c.execute("SELECT * FROM doctors WHERE id=?", (doctor_id,)).fetchone()
    if not doc:
        raise DataError("Unknown doctor_id. Call check_doctor_availability first.")
    if slot not in json.loads(doc["slots"]):
        raise DataError(f"{doc['name']} does not have a {slot} slot.")
    if slot not in free_slots(doctor_id, day):
        raise DataError(f"The {slot} slot on {day} is not available. Offer the caller another slot.")
    appt_id = "a-" + uuid.uuid4().hex[:8]
    try:
        with _db() as c:
            c.execute(
                "INSERT INTO appointments (id, phone, doctor_id, date, slot, user_id) VALUES (?,?,?,?,?,?)",
                (appt_id, phone, doctor_id, day, slot, user_id))
    except sqlite3.IntegrityError:
        raise DataError("Someone just took that slot. Offer the caller another one.")
    with _db() as c:
        return _appt(c.execute(_APPT_SQL + " WHERE a.id=?", (appt_id,)).fetchone())


def cancel(appointment_id: str) -> dict | None:
    with _db() as c:
        row = c.execute("SELECT id FROM appointments WHERE id=? AND status='booked'", (appointment_id,)).fetchone()
        if not row:
            return None
        c.execute("UPDATE appointments SET status='cancelled' WHERE id=?", (appointment_id,))
        return _appt(c.execute(_APPT_SQL + " WHERE a.id=?", (appointment_id,)).fetchone())
