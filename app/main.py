"""Plants: a small self-hosted household plant care log."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from app.library import LIBRARY, SOURCE as LIBRARY_SOURCE

BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("PLANTS_DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "plants.db"
PHOTOS_DIR = DATA_DIR / "photos"
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

PHOTO_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/heic": ".heic",
}
PHOTO_MAX_BYTES = 10 * 1024 * 1024
STORED_NAME_RE = re.compile(r"^[0-9a-f]{32}\.(?:jpg|png|webp|gif|heic)$")
UPLOAD_CHUNK = 1024 * 1024

COOKIE = "plants_session"
SESSION_DAYS = 30
PBKDF2_ITERATIONS = 260_000
LOGIN_LIMIT = 5
LOGIN_WINDOW_SECONDS = 15 * 60
API_LIMIT = 100
API_WINDOW_SECONDS = 60
API_FAIL_LIMIT = 5
API_FAIL_WINDOW_SECONDS = 15 * 60
WEATHER_TTL_SECONDS = 30 * 60

TASK_KINDS = ("water", "fertilize", "mist", "repot", "custom")
TASK_LABELS = {
    "water": "Water",
    "fertilize": "Fertilize",
    "mist": "Mist",
    "repot": "Repot",
    "custom": "Care",
}

_login_failures: dict[str, deque[float]] = defaultdict(deque)
_login_lock = threading.Lock()
_api_calls: dict[str, deque[float]] = defaultdict(deque)
_api_failures: dict[str, deque[float]] = defaultdict(deque)
_api_lock = threading.Lock()
_weather_cache: dict[str, Any] = {}
_weather_lock = threading.Lock()

app = FastAPI(title="Plants", version="0.1.0", docs_url=None, openapi_url=None)


# ---------------------------------------------------------------- helpers


def today() -> date:
    """Local date for the container's TZ, so 'due today' matches the wall clock."""
    return datetime.now().astimezone().date()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def check_date(value: str | None) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    value = value.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) or iso_date(value) is None:
        raise ValueError("Dates must be YYYY-MM-DD")
    return value


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def set_setting(c, key: str, value: str) -> None:
    c.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def get_setting(c, key: str, default: str = "") -> str:
    row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


# ---------------------------------------------------------------- security


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=()"
    if request.url.path == "/api/docs":
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; object-src 'none'; "
            "img-src 'self' data: https://fastapi.tiangolo.com; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net"
        )
    else:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'; object-src 'none'; img-src 'self' data: blob:; "
            "script-src 'self'; style-src 'self' 'unsafe-inline'"
        )
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def client_ip(request: Request) -> str:
    # Uvicorn swaps in the real client address only when the proxy is trusted
    # (FORWARDED_ALLOW_IPS), so this is safe to rate limit on.
    return request.client.host if request.client else "unknown"


def _window(hits: deque[float], limit: int, window: int, count: bool) -> int:
    now = time.monotonic()
    while hits and now - hits[0] >= window:
        hits.popleft()
    if len(hits) >= limit:
        return max(1, int(window - (now - hits[0])) + 1)
    if count:
        hits.append(now)
    return 0


def login_retry_after(ip: str) -> int:
    with _login_lock:
        return _window(_login_failures[ip], LOGIN_LIMIT, LOGIN_WINDOW_SECONDS, False)


def record_login_failure(ip: str) -> None:
    with _login_lock:
        _login_failures[ip].append(time.monotonic())


def clear_login_failures(ip: str) -> None:
    with _login_lock:
        _login_failures.pop(ip, None)


# ---------------------------------------------------------------- database


def init_db() -> None:
    fresh = not DB_PATH.exists()
    with db() as c:
        c.executescript(
            """
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY,
          username TEXT NOT NULL UNIQUE COLLATE NOCASE,
          password_hash TEXT NOT NULL,
          salt TEXT NOT NULL,
          is_admin INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          expires_at TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS api_tokens (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          token_hash TEXT NOT NULL UNIQUE,
          prefix TEXT NOT NULL,
          created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          created_at TEXT NOT NULL,
          last_used_at TEXT,
          revoked INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS rooms (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL UNIQUE COLLATE NOCASE,
          sort INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS plants (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          species TEXT NOT NULL DEFAULT '',
          room_id INTEGER REFERENCES rooms(id) ON DELETE SET NULL,
          acquired TEXT,
          pot_size TEXT NOT NULL DEFAULT '',
          pot_material TEXT NOT NULL DEFAULT '',
          light TEXT NOT NULL DEFAULT '',
          notes TEXT NOT NULL DEFAULT '',
          care_source TEXT NOT NULL DEFAULT '',
          outdoor INTEGER NOT NULL DEFAULT 0,
          photo TEXT,
          created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tasks (
          id INTEGER PRIMARY KEY,
          plant_id INTEGER NOT NULL REFERENCES plants(id) ON DELETE CASCADE,
          kind TEXT NOT NULL,
          label TEXT NOT NULL DEFAULT '',
          interval_days INTEGER NOT NULL,
          winter_interval_days INTEGER,
          last_done TEXT,
          cycle_start TEXT,
          snoozed_until TEXT,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY,
          plant_id INTEGER NOT NULL REFERENCES plants(id) ON DELETE CASCADE,
          task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
          kind TEXT NOT NULL DEFAULT '',
          action TEXT NOT NULL,
          date TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT '',
          photo TEXT,
          user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
          via TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS push_subscriptions (
          id INTEGER PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          endpoint TEXT NOT NULL UNIQUE,
          p256dh TEXT NOT NULL,
          auth TEXT NOT NULL,
          user_agent TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          last_success_at TEXT,
          last_error TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_plant ON tasks(plant_id);
        CREATE INDEX IF NOT EXISTS idx_events_plant ON events(plant_id, date);
        """
        )
        if fresh:
            seed_example(c)


def seed_example(c) -> None:
    stamp = now_iso()
    room = c.execute("INSERT INTO rooms(name,sort) VALUES('Living room',0)").lastrowid
    lib = LIBRARY[0]
    pid = c.execute(
        """INSERT INTO plants(name,species,room_id,light,notes,care_source,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?)""",
        (
            "Example pothos",
            lib["species"],
            room,
            lib["light"],
            lib["care"] + " This is an example plant; edit or delete it.",
            lib["source"],
            stamp,
            stamp,
        ),
    ).lastrowid
    c.execute(
        "INSERT INTO tasks(plant_id,kind,interval_days,created_at) VALUES(?,?,?,?)",
        (pid, "water", lib["water_days"], today().isoformat()),
    )


@app.on_event("startup")
def startup() -> None:
    init_db()
    problem = vapid_problem()
    if problem and problem != "not set":
        print(f"browser push disabled: {problem}")
    if os.getenv("PLANTS_NOTIFY_WORKER", "true").lower() == "true":
        threading.Thread(target=notification_worker, daemon=True).start()


# ---------------------------------------------------------------- auth


def clean_username(value: str) -> str:
    value = (value or "").strip()
    if not (3 <= len(value) <= 40) or not all(ch.isalnum() or ch in "._-" for ch in value):
        raise HTTPException(
            400,
            "Username must be 3-40 characters using letters, numbers, dots, dashes, or underscores",
        )
    return value


def password_record(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    if len(password or "") < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return digest.hex(), salt.hex()


def verify_password(password: str, expected: str, salt: str) -> bool:
    try:
        digest, _ = password_record(password, salt)
    except HTTPException:
        return False
    return hmac.compare_digest(digest, expected)


def public_user(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "username": row["username"],
        "is_admin": bool(row["is_admin"]),
        "active": bool(row["active"]),
    }


def current_user(request: Request, admin: bool = False) -> sqlite3.Row:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Not signed in")
    with db() as c:
        row = c.execute(
            """SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.token_hash=? AND s.expires_at>? AND u.active=1""",
            (hashlib.sha256(token.encode()).hexdigest(), now_iso()),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Session expired")
    if admin and not row["is_admin"]:
        raise HTTPException(403, "Administrator access required")
    return row


def set_session(response: Response, user_id: int) -> None:
    raw = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    with db() as c:
        c.execute("DELETE FROM sessions WHERE expires_at<=?", (now_iso(),))
        c.execute(
            "INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)",
            (hashlib.sha256(raw.encode()).hexdigest(), user_id, expires.isoformat(), now_iso()),
        )
    response.set_cookie(
        COOKIE,
        raw,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        samesite="strict",
        secure=os.getenv("PLANTS_COOKIE_SECURE", "false").lower() == "true",
        path="/",
    )


def token_auth(request: Request) -> tuple[sqlite3.Row, sqlite3.Row]:
    """Authenticate a bearer API token. Returns (token, owning user)."""
    auth = request.headers.get("authorization", "")
    ip = client_ip(request)
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    token_hash = hashlib.sha256(auth[7:].strip().encode()).hexdigest()
    with db() as c:
        row = c.execute(
            "SELECT * FROM api_tokens WHERE token_hash=? AND revoked=0", (token_hash,)
        ).fetchone()
        user = (
            c.execute("SELECT * FROM users WHERE id=? AND active=1", (row["created_by"],)).fetchone()
            if row
            else None
        )
    if not row:
        with _api_lock:
            retry = _window(_api_failures[ip], API_FAIL_LIMIT, API_FAIL_WINDOW_SECONDS, False)
            if not retry:
                _api_failures[ip].append(time.monotonic())
        if retry:
            raise HTTPException(
                429, "Too many failed API attempts. Try again later.", headers={"Retry-After": str(retry)}
            )
        raise HTTPException(401, "Invalid API token", headers={"WWW-Authenticate": "Bearer"})
    if not user:
        raise HTTPException(403, "Token owner is inactive")
    with _api_lock:
        retry = _window(_api_calls[token_hash], API_LIMIT, API_WINDOW_SECONDS, True)
    if retry:
        raise HTTPException(
            429, "API rate limit exceeded. Try again later.", headers={"Retry-After": str(retry)}
        )
    with db() as c:
        c.execute("UPDATE api_tokens SET last_used_at=? WHERE id=?", (now_iso(), row["id"]))
    return row, user


class Credentials(BaseModel):
    username: str = Field(max_length=40)
    password: str = Field(max_length=200)


@app.get("/api/status")
def status():
    with db() as c:
        setup_required = c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        name = get_setting(c, "app_name", "Your Plants")
    return {"setup_required": setup_required, "app_name": name}


@app.post("/api/setup")
def setup(body: Credentials, response: Response):
    username = clean_username(body.username)
    pw, salt = password_record(body.password)
    with db() as c:
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            raise HTTPException(409, "Setup is already complete")
        uid = c.execute(
            "INSERT INTO users(username,password_hash,salt,is_admin,active,created_at) VALUES(?,?,?,1,1,?)",
            (username, pw, salt, now_iso()),
        ).lastrowid
        c.execute("UPDATE plants SET created_by=? WHERE created_by IS NULL", (uid,))
    set_session(response, uid)
    return {"ok": True}


@app.post("/api/login")
def login(body: Credentials, request: Request, response: Response):
    ip = client_ip(request)
    retry = login_retry_after(ip)
    if retry:
        raise HTTPException(
            429, "Too many login attempts. Try again later.", headers={"Retry-After": str(retry)}
        )
    with db() as c:
        row = c.execute(
            "SELECT * FROM users WHERE username=? COLLATE NOCASE", (body.username.strip(),)
        ).fetchone()
    if not row or not row["active"] or not verify_password(body.password, row["password_hash"], row["salt"]):
        record_login_failure(ip)
        raise HTTPException(401, "Invalid username or password")
    clear_login_failures(ip)
    set_session(response, row["id"])
    return {"user": public_user(row)}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with db() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    return public_user(current_user(request))


# ---------------------------------------------------------------- scheduling logic


def winter_months(c) -> set[int]:
    raw = get_setting(c, "winter_months", "11,12,1,2")
    out = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= 12:
            out.add(int(part))
    return out


def season_config(c) -> dict[str, Any]:
    try:
        mult = float(get_setting(c, "winter_multiplier", "1") or 1)
    except ValueError:
        mult = 1.0
    return {"months": winter_months(c), "multiplier": mult}


def task_status(task, season: dict[str, Any], on: date | None = None) -> dict[str, Any]:
    """Work out when a task is next due and explain why, in plain words."""
    on = on or today()
    winter = on.month in season["months"]
    interval = int(task["interval_days"])
    effective = interval
    season_note = ""
    if winter and task["winter_interval_days"]:
        effective = int(task["winter_interval_days"])
        season_note = f"winter interval {effective} days"
    elif winter and abs(season["multiplier"] - 1.0) > 1e-9:
        effective = max(1, round(interval * season["multiplier"]))
        season_note = f"winter x{season['multiplier']:g} = {effective} days"
    start = iso_date(task["cycle_start"])
    created = iso_date(str(task["created_at"])[:10]) or on
    due = start + timedelta(days=effective) if start else created
    snoozed = iso_date(task["snoozed_until"])
    if snoozed and snoozed > due:
        due = snoozed
    days = (due - on).days
    if days < 0:
        state = "overdue"
    elif days == 0:
        state = "today"
    elif days <= 7:
        state = "upcoming"
    else:
        state = "later"
    parts = [f"every {interval} days"]
    if season_note:
        parts.append(season_note)
    last = iso_date(task["last_done"])
    parts.append(f"last done {last.strftime('%b %-d')}" if last else "never logged")
    if start and last and start > last:
        parts.append(f"skipped {start.strftime('%b %-d')}")
    if snoozed and snoozed >= on and snoozed == due:
        parts.append(f"snoozed to {snoozed.strftime('%b %-d')}")
    return {
        "next_due": due.isoformat(),
        "days": days,
        "state": state,
        "effective_interval": effective,
        "reason": (lambda r: r[:1].upper() + r[1:])(", ".join(parts)),
    }


def task_dict(task, season) -> dict[str, Any]:
    kind = task["kind"]
    return {
        "id": task["id"],
        "plant_id": task["plant_id"],
        "kind": kind,
        "label": task["label"] or TASK_LABELS.get(kind, "Care"),
        "custom_label": task["label"],
        "interval_days": task["interval_days"],
        "winter_interval_days": task["winter_interval_days"],
        "last_done": task["last_done"],
        "snoozed_until": task["snoozed_until"],
        **task_status(task, season),
    }


def display_user(c, user_id) -> str:
    if user_id is None:
        return ""
    row = c.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
    return row[0] if row else "Former user"


def plant_dict(c, row, season=None) -> dict[str, Any]:
    season = season or season_config(c)
    room = (
        c.execute("SELECT name FROM rooms WHERE id=?", (row["room_id"],)).fetchone()
        if row["room_id"]
        else None
    )
    tasks = [
        task_dict(t, season)
        for t in c.execute("SELECT * FROM tasks WHERE plant_id=? ORDER BY id", (row["id"],))
    ]
    tasks.sort(key=lambda t: (t["days"], t["id"]))
    return {
        "id": row["id"],
        "name": row["name"],
        "species": row["species"],
        "room_id": row["room_id"],
        "room": room[0] if room else "",
        "acquired": row["acquired"],
        "pot_size": row["pot_size"],
        "pot_material": row["pot_material"],
        "light": row["light"],
        "notes": row["notes"],
        "care_source": row["care_source"],
        "outdoor": bool(row["outdoor"]),
        "photo": f"/api/photos/{row['photo']}" if row["photo"] else None,
        "added_by": display_user(c, row["created_by"]) or "System",
        "tasks": tasks,
        "next": tasks[0] if tasks else None,
    }


def get_plant_row(c, plant_id: int):
    row = c.execute("SELECT * FROM plants WHERE id=?", (plant_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Plant not found")
    return row


def get_task_row(c, task_id: int):
    row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Task not found")
    return row


def recompute_task(c, task_id: int) -> None:
    """Rebuild last_done/cycle_start from the log, e.g. after an entry is deleted."""
    done = c.execute(
        "SELECT MAX(date) FROM events WHERE task_id=? AND action='done'", (task_id,)
    ).fetchone()[0]
    skipped = c.execute(
        "SELECT MAX(date) FROM events WHERE task_id=? AND action='skip'", (task_id,)
    ).fetchone()[0]
    start = max([d for d in (done, skipped) if d], default=None)
    c.execute("UPDATE tasks SET last_done=?, cycle_start=? WHERE id=?", (done, start, task_id))


def log_event(c, plant_id, task_id, kind, action, when, note, user_id, via="", photo=None) -> int:
    return c.execute(
        """INSERT INTO events(plant_id,task_id,kind,action,date,note,photo,user_id,via,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (plant_id, task_id, kind, action, when, note or "", photo, user_id, via, now_iso()),
    ).lastrowid


def apply_care(c, task, action: str, user_id: int, when: str | None = None, days: int | None = None,
               note: str = "", via: str = "") -> None:
    on = today()
    if action in ("done", "skip"):
        d = iso_date(when) if when else on
        if d is None:
            raise HTTPException(400, "Dates must be YYYY-MM-DD")
        if d > on:
            raise HTTPException(400, "Care can't be logged in the future")
        log_event(c, task["plant_id"], task["id"], task["kind"], action, d.isoformat(), note, user_id, via)
        recompute_task(c, task["id"])
        c.execute("UPDATE tasks SET snoozed_until=NULL WHERE id=?", (task["id"],))
    elif action == "snooze":
        if not days or days < 1 or days > 365:
            raise HTTPException(400, "Snooze between 1 and 365 days")
        until = (on + timedelta(days=days)).isoformat()
        c.execute("UPDATE tasks SET snoozed_until=? WHERE id=?", (until, task["id"]))
        log_event(c, task["plant_id"], task["id"], task["kind"], "snooze", on.isoformat(),
                  note or f"Snoozed {days} day{'s' if days != 1 else ''}", user_id, via)
    else:
        raise HTTPException(400, "Unknown action")
    c.execute("UPDATE plants SET updated_at=? WHERE id=?", (now_iso(), task["plant_id"]))


# ---------------------------------------------------------------- models


class TaskIn(BaseModel):
    kind: Literal["water", "fertilize", "mist", "repot", "custom"] = "water"
    label: str = Field(default="", max_length=40)
    interval_days: int = Field(ge=1, le=730)
    winter_interval_days: int | None = Field(default=None, ge=1, le=730)
    last_done: str | None = None

    @field_validator("last_done")
    @classmethod
    def _date(cls, v):
        return check_date(v)


class PlantIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    species: str = Field(default="", max_length=120)
    room_id: int | None = None
    room: str | None = Field(default=None, max_length=60)
    acquired: str | None = None
    pot_size: str = Field(default="", max_length=40)
    pot_material: str = Field(default="", max_length=40)
    light: str = Field(default="", max_length=120)
    notes: str = Field(default="", max_length=4000)
    care_source: str = Field(default="", max_length=300)
    outdoor: bool = False
    tasks: list[TaskIn] | None = None

    @field_validator("acquired")
    @classmethod
    def _date(cls, v):
        return check_date(v)

    @field_validator("care_source")
    @classmethod
    def _url(cls, v):
        v = (v or "").strip()
        if v and not re.match(r"^https?://", v, re.I):
            raise ValueError("Care source must be a web link starting with http:// or https://")
        return v


class PlantPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    species: str | None = Field(default=None, max_length=120)
    room_id: int | None = None
    room: str | None = Field(default=None, max_length=60)
    acquired: str | None = None
    pot_size: str | None = Field(default=None, max_length=40)
    pot_material: str | None = Field(default=None, max_length=40)
    light: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=4000)
    care_source: str | None = Field(default=None, max_length=300)
    outdoor: bool | None = None

    @field_validator("acquired")
    @classmethod
    def _date(cls, v):
        return check_date(v)

    @field_validator("care_source")
    @classmethod
    def _url(cls, v):
        if v is None:
            return v
        v = v.strip()
        if v and not re.match(r"^https?://", v, re.I):
            raise ValueError("Care source must be a web link starting with http:// or https://")
        return v


class CareIn(BaseModel):
    date: str | None = None
    days: int | None = None
    note: str = Field(default="", max_length=1000)

    @field_validator("date")
    @classmethod
    def _date(cls, v):
        return check_date(v)


class BatchIn(BaseModel):
    task_ids: list[int] = Field(min_length=1, max_length=500)
    action: Literal["done", "skip", "snooze"]
    date: str | None = None
    days: int | None = None
    note: str = Field(default="", max_length=1000)

    @field_validator("date")
    @classmethod
    def _date(cls, v):
        return check_date(v)


class RoomIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


# ---------------------------------------------------------------- rooms


def resolve_room(c, room_id: int | None, room_name: str | None) -> int | None:
    if room_name is not None and room_name.strip():
        name = room_name.strip()
        row = c.execute("SELECT id FROM rooms WHERE name=? COLLATE NOCASE", (name,)).fetchone()
        if row:
            return row["id"]
        sort = c.execute("SELECT COALESCE(MAX(sort),0)+1 FROM rooms").fetchone()[0]
        return c.execute("INSERT INTO rooms(name,sort) VALUES(?,?)", (name, sort)).lastrowid
    if room_id is not None:
        if not c.execute("SELECT 1 FROM rooms WHERE id=?", (room_id,)).fetchone():
            raise HTTPException(400, "Room not found")
    return room_id


def rooms_list(c) -> list[dict[str, Any]]:
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "plants": c.execute("SELECT COUNT(*) FROM plants WHERE room_id=?", (r["id"],)).fetchone()[0],
        }
        for r in c.execute("SELECT * FROM rooms ORDER BY sort, name COLLATE NOCASE")
    ]


@app.get("/api/rooms")
def list_rooms(request: Request):
    current_user(request)
    with db() as c:
        return rooms_list(c)


@app.post("/api/rooms", status_code=201)
def add_room(body: RoomIn, request: Request):
    current_user(request)
    with db() as c:
        rid = resolve_room(c, None, body.name)
        return next(r for r in rooms_list(c) if r["id"] == rid)


@app.put("/api/rooms/{room_id}")
def rename_room(room_id: int, body: RoomIn, request: Request):
    current_user(request)
    with db() as c:
        if not c.execute("SELECT 1 FROM rooms WHERE id=?", (room_id,)).fetchone():
            raise HTTPException(404, "Room not found")
        try:
            c.execute("UPDATE rooms SET name=? WHERE id=?", (body.name.strip(), room_id))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "A room with that name already exists")
        return next(r for r in rooms_list(c) if r["id"] == room_id)


@app.delete("/api/rooms/{room_id}")
def delete_room(room_id: int, request: Request):
    current_user(request)
    with db() as c:
        if not c.execute("DELETE FROM rooms WHERE id=?", (room_id,)).rowcount:
            raise HTTPException(404, "Room not found")
    return {"ok": True}


# ---------------------------------------------------------------- plants


def insert_tasks(c, plant_id: int, tasks: list[TaskIn]) -> None:
    for t in tasks:
        c.execute(
            """INSERT INTO tasks(plant_id,kind,label,interval_days,winter_interval_days,created_at)
            VALUES(?,?,?,?,?,?)""",
            (plant_id, t.kind, t.label.strip(), t.interval_days, t.winter_interval_days, today().isoformat()),
        )


def seed_last_done(c, plant_id: int, tasks: list[TaskIn], user_id: int, via: str = "") -> None:
    rows = c.execute("SELECT * FROM tasks WHERE plant_id=? ORDER BY id", (plant_id,)).fetchall()
    for row, t in zip(rows[-len(tasks):] if tasks else [], tasks):
        if t.last_done:
            apply_care(c, row, "done", user_id, when=t.last_done, note="Logged when the plant was added", via=via)


def create_plant(c, body: PlantIn, user_id: int, via: str = "") -> int:
    stamp = now_iso()
    room_id = resolve_room(c, body.room_id, body.room)
    pid = c.execute(
        """INSERT INTO plants(name,species,room_id,acquired,pot_size,pot_material,light,notes,
        care_source,outdoor,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            body.name.strip(), body.species.strip(), room_id, body.acquired, body.pot_size.strip(),
            body.pot_material.strip(), body.light.strip(), body.notes.strip(), body.care_source,
            int(body.outdoor), user_id, stamp, stamp,
        ),
    ).lastrowid
    tasks = body.tasks if body.tasks is not None else [TaskIn(kind="water", interval_days=7)]
    insert_tasks(c, pid, tasks)
    seed_last_done(c, pid, tasks, user_id, via)
    return pid


def patch_plant(c, plant_id: int, body: PlantPatch | PlantIn) -> None:
    row = get_plant_row(c, plant_id)
    data = body.model_dump(exclude_unset=True)
    fields = {}
    for key in ("name", "species", "acquired", "pot_size", "pot_material", "light", "notes", "care_source"):
        if key in data:
            val = data[key]
            fields[key] = val.strip() if isinstance(val, str) else val
    if "outdoor" in data and data["outdoor"] is not None:
        fields["outdoor"] = int(data["outdoor"])
    if "room" in data or "room_id" in data:
        if data.get("room") is not None and not data["room"].strip() and "room_id" not in data:
            fields["room_id"] = None
        else:
            fields["room_id"] = resolve_room(c, data.get("room_id"), data.get("room"))
    if fields.get("name") == "":
        raise HTTPException(400, "Name is required")
    if fields:
        fields["updated_at"] = now_iso()
        cols = ",".join(f"{k}=?" for k in fields)
        c.execute(f"UPDATE plants SET {cols} WHERE id=?", (*fields.values(), row["id"]))


@app.get("/api/plants")
def list_plants(request: Request):
    current_user(request)
    with db() as c:
        season = season_config(c)
        return [plant_dict(c, r, season) for r in c.execute("SELECT * FROM plants ORDER BY name COLLATE NOCASE")]


@app.get("/api/plants/{plant_id}")
def get_plant(plant_id: int, request: Request):
    current_user(request)
    with db() as c:
        return plant_dict(c, get_plant_row(c, plant_id))


@app.post("/api/plants", status_code=201)
def add_plant(body: PlantIn, request: Request):
    user = current_user(request)
    with db() as c:
        pid = create_plant(c, body, user["id"])
        return plant_dict(c, get_plant_row(c, pid))


@app.put("/api/plants/{plant_id}")
def update_plant(plant_id: int, body: PlantIn, request: Request):
    current_user(request)
    with db() as c:
        data = body.model_copy()
        patch_plant(c, plant_id, PlantPatch(**data.model_dump(exclude={"tasks"})))
        return plant_dict(c, get_plant_row(c, plant_id))


@app.delete("/api/plants/{plant_id}")
def delete_plant(plant_id: int, request: Request):
    current_user(request)
    with db() as c:
        row = get_plant_row(c, plant_id)
        names = [row["photo"]] + [r[0] for r in c.execute("SELECT photo FROM events WHERE plant_id=?", (plant_id,))]
        c.execute("DELETE FROM plants WHERE id=?", (plant_id,))
    for name in names:
        unlink_photo(name)
    return {"ok": True}


@app.post("/api/plants/{plant_id}/duplicate", status_code=201)
def duplicate_plant(plant_id: int, request: Request):
    user = current_user(request)
    with db() as c:
        row = get_plant_row(c, plant_id)
        stamp = now_iso()
        pid = c.execute(
            """INSERT INTO plants(name,species,room_id,acquired,pot_size,pot_material,light,notes,
            care_source,outdoor,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"{row['name']} (copy)", row["species"], row["room_id"], row["acquired"], row["pot_size"],
                row["pot_material"], row["light"], row["notes"], row["care_source"], row["outdoor"],
                user["id"], stamp, stamp,
            ),
        ).lastrowid
        for t in c.execute("SELECT * FROM tasks WHERE plant_id=?", (plant_id,)).fetchall():
            c.execute(
                """INSERT INTO tasks(plant_id,kind,label,interval_days,winter_interval_days,created_at)
                VALUES(?,?,?,?,?,?)""",
                (pid, t["kind"], t["label"], t["interval_days"], t["winter_interval_days"], today().isoformat()),
            )
        return plant_dict(c, get_plant_row(c, pid))


# ---------------------------------------------------------------- tasks and care


@app.post("/api/plants/{plant_id}/tasks", status_code=201)
def add_task(plant_id: int, body: TaskIn, request: Request):
    user = current_user(request)
    with db() as c:
        get_plant_row(c, plant_id)
        insert_tasks(c, plant_id, [body])
        seed_last_done(c, plant_id, [body], user["id"])
        return plant_dict(c, get_plant_row(c, plant_id))


@app.put("/api/tasks/{task_id}")
def update_task(task_id: int, body: TaskIn, request: Request):
    current_user(request)
    with db() as c:
        task = get_task_row(c, task_id)
        c.execute(
            "UPDATE tasks SET kind=?, label=?, interval_days=?, winter_interval_days=? WHERE id=?",
            (body.kind, body.label.strip(), body.interval_days, body.winter_interval_days, task_id),
        )
        return plant_dict(c, get_plant_row(c, task["plant_id"]))


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int, request: Request):
    current_user(request)
    with db() as c:
        task = get_task_row(c, task_id)
        c.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        return plant_dict(c, get_plant_row(c, task["plant_id"]))


def care_endpoint(task_id: int, action: str, body: CareIn, user_id: int, via: str = ""):
    with db() as c:
        task = get_task_row(c, task_id)
        apply_care(c, task, action, user_id, when=body.date, days=body.days, note=body.note, via=via)
        return plant_dict(c, get_plant_row(c, task["plant_id"]))


@app.post("/api/tasks/{task_id}/done")
def task_done(task_id: int, body: CareIn, request: Request):
    return care_endpoint(task_id, "done", body, current_user(request)["id"])


@app.post("/api/tasks/{task_id}/skip")
def task_skip(task_id: int, body: CareIn, request: Request):
    return care_endpoint(task_id, "skip", body, current_user(request)["id"])


@app.post("/api/tasks/{task_id}/snooze")
def task_snooze(task_id: int, body: CareIn, request: Request):
    return care_endpoint(task_id, "snooze", body, current_user(request)["id"])


@app.post("/api/care/batch")
def batch_care(body: BatchIn, request: Request):
    user = current_user(request)
    with db() as c:
        tasks = [get_task_row(c, tid) for tid in dict.fromkeys(body.task_ids)]
        for task in tasks:
            apply_care(c, task, body.action, user["id"], when=body.date, days=body.days, note=body.note)
    return {"ok": True, "count": len(tasks)}


def due_items(c, horizon: int = 7) -> list[dict[str, Any]]:
    season = season_config(c)
    items = []
    for p in c.execute("SELECT * FROM plants ORDER BY name COLLATE NOCASE").fetchall():
        room = c.execute("SELECT name FROM rooms WHERE id=?", (p["room_id"],)).fetchone() if p["room_id"] else None
        for t in c.execute("SELECT * FROM tasks WHERE plant_id=?", (p["id"],)):
            td = task_dict(t, season)
            if td["days"] <= horizon:
                items.append(
                    {
                        **td,
                        "task_id": td["id"],
                        "plant_name": p["name"],
                        "species": p["species"],
                        "room": room[0] if room else "",
                        "room_id": p["room_id"],
                        "outdoor": bool(p["outdoor"]),
                        "photo": f"/api/photos/{p['photo']}" if p["photo"] else None,
                    }
                )
    items.sort(key=lambda i: (i["days"], i["room"].lower(), i["plant_name"].lower()))
    return items


@app.get("/api/due")
def list_due(request: Request, days: int = 7):
    current_user(request)
    with db() as c:
        return {"today": today().isoformat(), "items": due_items(c, max(0, min(days, 60)))}


# ---------------------------------------------------------------- timeline and photos


def photo_path(stored: str) -> Path:
    if not isinstance(stored, str) or not STORED_NAME_RE.fullmatch(stored):
        raise HTTPException(404, "Photo is missing")
    path = (PHOTOS_DIR / stored).resolve()
    if path.parent != PHOTOS_DIR.resolve():
        raise HTTPException(404, "Photo is missing")
    return path


def unlink_photo(stored: str | None) -> None:
    if isinstance(stored, str) and STORED_NAME_RE.fullmatch(stored):
        (PHOTOS_DIR / stored).unlink(missing_ok=True)


def sniff_image(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"hevc"):
        return ".heic"
    return None


async def read_photo_upload(file: UploadFile) -> tuple[bytes, str]:
    """Bytes and extension of an uploaded photo, with the same size and type checks everywhere."""
    chunks, total = [], 0
    while True:
        chunk = await file.read(UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > PHOTO_MAX_BYTES:
            raise HTTPException(400, "Photos are limited to 10 MB")
        chunks.append(chunk)
    data = b"".join(chunks)
    ext = sniff_image(data)
    if not ext:
        raise HTTPException(400, "Photos must be JPEG, PNG, WebP, GIF, or HEIC")
    return data, ext


async def save_photo(file: UploadFile) -> str:
    data, ext = await read_photo_upload(file)
    name = secrets.token_hex(16) + ext
    (PHOTOS_DIR / name).write_bytes(data)
    return name


def event_dict(c, row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "plant_id": row["plant_id"],
        "task_id": row["task_id"],
        "kind": row["kind"],
        "label": TASK_LABELS.get(row["kind"], row["kind"].capitalize() if row["kind"] else ""),
        "action": row["action"],
        "date": row["date"],
        "note": row["note"],
        "photo": f"/api/photos/{row['photo']}" if row["photo"] else None,
        "user": display_user(c, row["user_id"]),
        "via": row["via"],
        "created_at": row["created_at"],
    }


@app.get("/api/plants/{plant_id}/events")
def list_events(plant_id: int, request: Request):
    current_user(request)
    with db() as c:
        get_plant_row(c, plant_id)
        return [
            event_dict(c, r)
            for r in c.execute(
                "SELECT * FROM events WHERE plant_id=? ORDER BY date DESC, id DESC LIMIT 500", (plant_id,)
            )
        ]


async def add_timeline_entry(plant_id: int, user_id: int, note: str, when: str | None,
                             photo: UploadFile | None, main: bool, via: str = "") -> dict[str, Any]:
    note = (note or "").strip()[:1000]
    try:
        when = check_date(when) or today().isoformat()
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if iso_date(when) > today():
        raise HTTPException(400, "Entries can't be dated in the future")
    stored = await save_photo(photo) if photo is not None and photo.filename else None
    if not stored and not note:
        raise HTTPException(400, "Add a photo or a note")
    with db() as c:
        try:
            row = get_plant_row(c, plant_id)
        except HTTPException:
            unlink_photo(stored)
            raise
        eid = log_event(c, plant_id, None, "", "photo" if stored else "note", when, note, user_id, via, stored)
        if stored and (main or not row["photo"]):
            c.execute("UPDATE plants SET photo=?, updated_at=? WHERE id=?", (stored, now_iso(), plant_id))
        return event_dict(c, c.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone())


@app.post("/api/plants/{plant_id}/events", status_code=201)
async def add_event(
    plant_id: int,
    request: Request,
    note: str = Form(default=""),
    date: str = Form(default=""),
    main: bool = Form(default=False),
    photo: UploadFile | None = File(default=None),
):
    user = current_user(request)
    return await add_timeline_entry(plant_id, user["id"], note, date, photo, main)


@app.post("/api/plants/{plant_id}/main-photo")
def set_main_photo(plant_id: int, request: Request, event_id: int):
    current_user(request)
    with db() as c:
        get_plant_row(c, plant_id)
        ev = c.execute("SELECT photo FROM events WHERE id=? AND plant_id=?", (event_id, plant_id)).fetchone()
        if not ev or not ev["photo"]:
            raise HTTPException(404, "Photo not found")
        c.execute("UPDATE plants SET photo=? WHERE id=?", (ev["photo"], plant_id))
        return plant_dict(c, get_plant_row(c, plant_id))


@app.delete("/api/events/{event_id}")
def delete_event(event_id: int, request: Request):
    user = current_user(request)
    with db() as c:
        ev = c.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if not ev:
            raise HTTPException(404, "Entry not found")
        if not user["is_admin"] and ev["user_id"] != user["id"]:
            raise HTTPException(403, "Only the person who logged this entry or an administrator can delete it")
        c.execute("DELETE FROM events WHERE id=?", (event_id,))
        if ev["task_id"]:
            recompute_task(c, ev["task_id"])
        photo = ev["photo"]
        if photo:
            plant = c.execute("SELECT photo FROM plants WHERE id=?", (ev["plant_id"],)).fetchone()
            if plant and plant["photo"] == photo:
                nxt = c.execute(
                    "SELECT photo FROM events WHERE plant_id=? AND photo IS NOT NULL ORDER BY date DESC, id DESC LIMIT 1",
                    (ev["plant_id"],),
                ).fetchone()
                c.execute("UPDATE plants SET photo=? WHERE id=?", (nxt[0] if nxt else None, ev["plant_id"]))
            still_used = c.execute("SELECT 1 FROM plants WHERE photo=?", (photo,)).fetchone()
    if photo and not still_used:
        unlink_photo(photo)
    return {"ok": True}


@app.get("/api/photos/{name}")
def get_photo(name: str, request: Request):
    current_user(request)
    path = photo_path(name)
    if not path.is_file():
        raise HTTPException(404, "Photo is missing")
    media = {v: k for k, v in PHOTO_TYPES.items()}[path.suffix]
    return FileResponse(path, media_type=media, headers={"Cache-Control": "private, max-age=86400"})


# ---------------------------------------------------------------- library


@app.get("/api/library")
def library(request: Request):
    current_user(request)
    return {"source": LIBRARY_SOURCE, "plants": LIBRARY}


# ---------------------------------------------------------------- photo identification (Pl@ntNet)

PLANTNET_URL = "https://my-api.plantnet.org/v2/identify/all"
PLANTNET_TIMEOUT = 10
PLANTNET_RESULTS = 5
PLANTNET_MEDIA = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif", ".heic": "image/heic"}


def plantnet_key() -> str:
    return os.getenv("PLANTS_PLANTNET_API_KEY", "").strip()


@app.get("/api/identify")
def identify_status(request: Request):
    current_user(request)
    return {"configured": bool(plantnet_key())}


def plantnet_identify(data: bytes, ext: str, key: str) -> dict[str, Any]:
    """Ask Pl@ntNet what a photo shows. Raises HTTPException with a plain reason on failure."""
    boundary = "plants" + secrets.token_hex(12)
    body = b"\r\n".join([
        f"--{boundary}".encode(),
        f'Content-Disposition: form-data; name="images"; filename="photo{ext}"'.encode(),
        f"Content-Type: {PLANTNET_MEDIA.get(ext, 'image/jpeg')}".encode(),
        b"",
        data,
        f"--{boundary}".encode(),
        b'Content-Disposition: form-data; name="organs"',
        b"",
        b"auto",
        f"--{boundary}--".encode(),
        b"",
    ])
    req = urllib.request.Request(
        f"{PLANTNET_URL}?api-key={urllib.parse.quote(key)}",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": "plants-self-hosted/0.1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=PLANTNET_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise HTTPException(502, "Pl@ntNet rejected the API key. Check PLANTS_PLANTNET_API_KEY.")
        if e.code == 429:
            raise HTTPException(429, "The Pl@ntNet daily identification limit was reached. The free plan resets each day.")
        if e.code in (400, 404, 413, 415):
            raise HTTPException(400, "Pl@ntNet couldn't read that photo. Try a sharp, close-up photo of leaves or flowers.")
        raise HTTPException(503, "Pl@ntNet is having trouble right now. Try again later.")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        raise HTTPException(503, "Pl@ntNet didn't answer in time. Check the server can reach the internet, then try again.")


@app.post("/api/identify")
async def identify_photo(request: Request, photo: UploadFile = File(...)):
    current_user(request)
    key = plantnet_key()
    if not key:
        raise HTTPException(404, "Photo identification isn't set up on this server. Add PLANTS_PLANTNET_API_KEY to the compose file; see the readme.")
    data, ext = await read_photo_upload(photo)
    payload = await run_in_threadpool(plantnet_identify, data, ext, key)
    suggestions = []
    for result in payload.get("results", [])[:PLANTNET_RESULTS]:
        species = result.get("species") or {}
        scientific = species.get("scientificNameWithoutAuthor") or species.get("scientificName") or ""
        if not scientific:
            continue
        suggestions.append({
            "scientific": scientific,
            "common": (species.get("commonNames") or [""])[0],
            "score": round((result.get("score") or 0) * 100),
        })
    return {"suggestions": suggestions}


# ---------------------------------------------------------------- weather (Open-Meteo)

WMO = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Cloudy", 45: "Fog", 48: "Fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Rain showers",
    81: "Rain showers", 82: "Heavy showers", 85: "Snow showers", 86: "Snow showers",
    95: "Thunderstorm", 96: "Thunderstorm", 99: "Thunderstorm",
}


def http_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "plants-self-hosted/0.1"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode())


def weather_hint(daily: list[dict[str, Any]], current: dict[str, Any], imperial: bool) -> str:
    """Plain facts that matter when checking plants. Never changes a schedule."""
    if not daily:
        return ""
    rain_days = [d for d in daily[:3] if (d["precip_probability"] or 0) >= 60]
    hot = 90 if imperial else 32
    cold = 40 if imperial else 4
    hints = []
    if rain_days:
        hints.append(f"Rain likely {rain_days[0]['label']}; outdoor pots may not need water.")
    if any((d["high"] or -999) >= hot for d in daily[:3]):
        hints.append("Hot days ahead; outdoor pots dry out faster.")
    if any((d["low"] if d["low"] is not None else 999) <= cold for d in daily[:3]):
        hints.append("Cold nights ahead; bring tender plants in.")
    if current.get("humidity") is not None and current["humidity"] < 30:
        hints.append("Dry air; ferns and tropicals may want misting.")
    return " ".join(hints)


def fetch_weather(lat: float, lon: float, imperial: bool) -> dict[str, Any]:
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "current": "temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max",
        "timezone": "auto",
        "forecast_days": "5",
    }
    if imperial:
        params.update(temperature_unit="fahrenheit", precipitation_unit="inch", wind_speed_unit="mph")
    data = http_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params))
    cur = data.get("current", {})
    d = data.get("daily", {})
    daily = []
    for i, day in enumerate(d.get("time", [])):
        dt = iso_date(day)
        daily.append(
            {
                "date": day,
                "label": "today" if i == 0 else ("tomorrow" if i == 1 else dt.strftime("%A")),
                "short": "Today" if i == 0 else dt.strftime("%a"),
                "summary": WMO.get(d["weather_code"][i], ""),
                "code": d["weather_code"][i],
                "high": d["temperature_2m_max"][i],
                "low": d["temperature_2m_min"][i],
                "precip": d["precipitation_sum"][i],
                "precip_probability": d["precipitation_probability_max"][i],
            }
        )
    current = {
        "temperature": cur.get("temperature_2m"),
        "humidity": cur.get("relative_humidity_2m"),
        "precipitation": cur.get("precipitation"),
        "wind": cur.get("wind_speed_10m"),
        "summary": WMO.get(cur.get("weather_code"), ""),
        "code": cur.get("weather_code"),
    }
    week_rain = round(sum(x["precip"] or 0 for x in daily), 2)
    return {
        "current": current,
        "daily": daily,
        "rain_total": week_rain,
        "hint": weather_hint(daily, current, imperial),
        "units": {
            "temperature": "°F" if imperial else "°C",
            "precipitation": "in" if imperial else "mm",
            "wind": "mph" if imperial else "km/h",
        },
    }


@app.get("/api/weather")
def weather(request: Request, refresh: bool = False):
    current_user(request)
    with db() as c:
        name = get_setting(c, "weather_name")
        lat = get_setting(c, "weather_lat")
        lon = get_setting(c, "weather_lon")
        imperial = get_setting(c, "units", "imperial") != "metric"
    if not lat or not lon:
        return {"configured": False}
    key = f"{lat},{lon},{imperial}"
    with _weather_lock:
        cached = _weather_cache.get(key)
        if cached and not refresh and time.time() - cached["at"] < WEATHER_TTL_SECONDS:
            return {**cached["data"], "cached": True}
    if os.getenv("PLANTS_WEATHER_OFFLINE", "false").lower() == "true":
        return {"configured": True, "location": name, "error": "Weather is turned off (offline mode)."}
    try:
        data = fetch_weather(float(lat), float(lon), imperial)
    except Exception as exc:  # network, parse, or upstream error
        if cached:
            return {**cached["data"], "cached": True, "stale": True}
        return {"configured": True, "location": name, "error": f"Weather unavailable right now ({type(exc).__name__})."}
    data = {"configured": True, "location": name, "fetched_at": now_iso(), **data}
    with _weather_lock:
        _weather_cache.clear()
        _weather_cache[key] = {"at": time.time(), "data": data}
    return data


@app.get("/api/weather/search")
def weather_search(q: str, request: Request):
    current_user(request, True)
    q = q.strip()[:80]
    if len(q) < 2:
        return []
    try:
        data = http_json(
            "https://geocoding-api.open-meteo.com/v1/search?"
            + urllib.parse.urlencode({"name": q, "count": 6, "language": "en", "format": "json"})
        )
    except Exception:
        raise HTTPException(502, "Location search is unavailable right now")
    out = []
    for r in data.get("results", []) or []:
        label = ", ".join(x for x in (r.get("name"), r.get("admin1"), r.get("country_code")) if x)
        out.append({"label": label, "latitude": r.get("latitude"), "longitude": r.get("longitude")})
    return out


# ---------------------------------------------------------------- settings

SETTINGS_DEFAULTS = {
    "app_name": "Your Plants",
    "weather_name": "",
    "weather_lat": "",
    "weather_lon": "",
    "units": "imperial",
    "winter_months": "11,12,1,2",
    "winter_multiplier": "1",
}


def read_settings(c) -> dict[str, Any]:
    out = {k: get_setting(c, k, v) for k, v in SETTINGS_DEFAULTS.items()}
    out["winter_multiplier"] = float(out["winter_multiplier"] or 1)
    out["winter_months"] = sorted(winter_months(c))
    return out


class SettingsIn(BaseModel):
    app_name: str | None = Field(default=None, max_length=60)
    weather_name: str | None = Field(default=None, max_length=120)
    weather_lat: float | None = Field(default=None, ge=-90, le=90)
    weather_lon: float | None = Field(default=None, ge=-180, le=180)
    clear_weather: bool = False
    units: Literal["imperial", "metric"] | None = None
    winter_months: list[int] | None = None
    winter_multiplier: float | None = Field(default=None, ge=0.25, le=4)

    @field_validator("winter_months")
    @classmethod
    def _months(cls, v):
        if v is None:
            return v
        if any(m < 1 or m > 12 for m in v):
            raise ValueError("Months are 1-12")
        return sorted(set(v))


@app.get("/api/settings")
def get_settings(request: Request):
    current_user(request)
    with db() as c:
        return read_settings(c)


@app.put("/api/settings")
def update_settings(body: SettingsIn, request: Request):
    current_user(request, True)
    with db() as c:
        if body.app_name is not None:
            if not body.app_name.strip():
                raise HTTPException(400, "Name is required")
            set_setting(c, "app_name", body.app_name.strip())
        if body.clear_weather:
            for k in ("weather_name", "weather_lat", "weather_lon"):
                set_setting(c, k, "")
        elif body.weather_lat is not None and body.weather_lon is not None:
            set_setting(c, "weather_lat", f"{body.weather_lat:.4f}")
            set_setting(c, "weather_lon", f"{body.weather_lon:.4f}")
            set_setting(c, "weather_name", (body.weather_name or "").strip())
        if body.units:
            set_setting(c, "units", body.units)
        if body.winter_months is not None:
            set_setting(c, "winter_months", ",".join(str(m) for m in body.winter_months))
        if body.winter_multiplier is not None:
            set_setting(c, "winter_multiplier", f"{body.winter_multiplier:g}")
        return read_settings(c)


# ---------------------------------------------------------------- users


class UserCreate(BaseModel):
    username: str
    password: str
    is_admin: bool = False


class UserUpdate(BaseModel):
    username: str | None = None
    password: str | None = None
    is_admin: bool | None = None
    active: bool | None = None


@app.get("/api/users")
def list_users(request: Request):
    current_user(request, True)
    with db() as c:
        return [public_user(r) for r in c.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE")]


@app.post("/api/users")
def create_user(body: UserCreate, request: Request):
    current_user(request, True)
    username = clean_username(body.username)
    pw, salt = password_record(body.password)
    try:
        with db() as c:
            uid = c.execute(
                "INSERT INTO users(username,password_hash,salt,is_admin,active,created_at) VALUES(?,?,?,?,1,?)",
                (username, pw, salt, int(body.is_admin), now_iso()),
            ).lastrowid
            return public_user(c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Username already exists")


@app.put("/api/users/{item_id}")
def update_user(item_id: int, body: UserUpdate, request: Request):
    actor = current_user(request, True)
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        username = clean_username(body.username) if body.username is not None else row["username"]
        is_admin = int(body.is_admin if body.is_admin is not None else row["is_admin"])
        active = int(body.active if body.active is not None else row["active"])
        if actor["id"] == item_id and (not is_admin or not active):
            raise HTTPException(400, "You cannot deactivate yourself or remove your own administrator access")
        pw, salt = row["password_hash"], row["salt"]
        if body.password:
            pw, salt = password_record(body.password)
        try:
            c.execute(
                "UPDATE users SET username=?,password_hash=?,salt=?,is_admin=?,active=? WHERE id=?",
                (username, pw, salt, is_admin, active, item_id),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Username already exists")
        if not active:
            c.execute("DELETE FROM sessions WHERE user_id=?", (item_id,))
        elif body.password:
            keep = request.cookies.get(COOKIE) if actor["id"] == item_id else None
            keep_hash = hashlib.sha256(keep.encode()).hexdigest() if keep else ""
            c.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (item_id, keep_hash))
        return public_user(c.execute("SELECT * FROM users WHERE id=?", (item_id,)).fetchone())


# ---------------------------------------------------------------- API tokens


def token_dict(row, raw: str | None = None) -> dict[str, Any]:
    out = {
        "id": row["id"],
        "name": row["name"],
        "prefix": row["prefix"],
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"],
    }
    if raw is not None:
        out["token"] = raw
    return out


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


@app.get("/api/tokens")
def list_tokens(request: Request):
    user = current_user(request)
    with db() as c:
        if user["is_admin"]:
            rows = c.execute("SELECT * FROM api_tokens WHERE revoked=0 ORDER BY id")
        else:
            rows = c.execute("SELECT * FROM api_tokens WHERE revoked=0 AND created_by=? ORDER BY id", (user["id"],))
        return [token_dict(r) for r in rows]


@app.post("/api/tokens", status_code=201)
def create_token(body: TokenIn, request: Request):
    user = current_user(request)
    raw = "pla_" + secrets.token_urlsafe(32)
    with db() as c:
        tid = c.execute(
            "INSERT INTO api_tokens(name,token_hash,prefix,created_by,created_at) VALUES(?,?,?,?,?)",
            (body.name.strip(), hashlib.sha256(raw.encode()).hexdigest(), raw[:11], user["id"], now_iso()),
        ).lastrowid
        return token_dict(c.execute("SELECT * FROM api_tokens WHERE id=?", (tid,)).fetchone(), raw)


@app.delete("/api/tokens/{item_id}")
def revoke_token(item_id: int, request: Request):
    user = current_user(request)
    with db() as c:
        if user["is_admin"]:
            n = c.execute("DELETE FROM api_tokens WHERE id=?", (item_id,)).rowcount
        else:
            n = c.execute("DELETE FROM api_tokens WHERE id=? AND created_by=?", (item_id, user["id"])).rowcount
        if not n:
            raise HTTPException(404, "Token not found")
    return {"ok": True}


# ---------------------------------------------------------------- token API (v1)
# Lets a script, home automation, or an AI assistant add plants from a photo of
# the plant tag, read what's due, and log care. Everything is attributed to the
# token owner and tagged with the token name.


@app.get("/api/v1/plants", tags=["v1"], summary="List plants with their care tasks and next due dates")
def v1_plants(request: Request):
    token_auth(request)
    with db() as c:
        season = season_config(c)
        return [plant_dict(c, r, season) for r in c.execute("SELECT * FROM plants ORDER BY name COLLATE NOCASE")]


@app.get("/api/v1/plants/{plant_id}", tags=["v1"], summary="Get one plant")
def v1_plant(plant_id: int, request: Request):
    token_auth(request)
    with db() as c:
        return plant_dict(c, get_plant_row(c, plant_id))


@app.post("/api/v1/plants", tags=["v1"], status_code=201,
          summary="Add a plant (name, species, room, care notes, and task intervals)")
def v1_add_plant(body: PlantIn, request: Request):
    token, user = token_auth(request)
    with db() as c:
        pid = create_plant(c, body, user["id"], via=token["name"])
        return plant_dict(c, get_plant_row(c, pid))


@app.patch("/api/v1/plants/{plant_id}", tags=["v1"], summary="Update some fields of a plant")
def v1_patch_plant(plant_id: int, body: PlantPatch, request: Request):
    token_auth(request)
    with db() as c:
        patch_plant(c, plant_id, body)
        return plant_dict(c, get_plant_row(c, plant_id))


@app.post("/api/v1/plants/{plant_id}/tasks", tags=["v1"], status_code=201, summary="Add a care task to a plant")
def v1_add_task(plant_id: int, body: TaskIn, request: Request):
    token, user = token_auth(request)
    with db() as c:
        get_plant_row(c, plant_id)
        insert_tasks(c, plant_id, [body])
        seed_last_done(c, plant_id, [body], user["id"], via=token["name"])
        return plant_dict(c, get_plant_row(c, plant_id))


@app.get("/api/v1/due", tags=["v1"], summary="Care that is overdue, due today, or due within N days")
def v1_due(request: Request, days: int = 7):
    token_auth(request)
    with db() as c:
        return {"today": today().isoformat(), "items": due_items(c, max(0, min(days, 60)))}


@app.post("/api/v1/tasks/{task_id}/{action}", tags=["v1"], summary="Log care: done, skip, or snooze")
def v1_care(task_id: int, action: Literal["done", "skip", "snooze"], body: CareIn, request: Request):
    token, user = token_auth(request)
    return care_endpoint(task_id, action, body, user["id"], via=token["name"])


@app.post("/api/v1/plants/{plant_id}/photos", tags=["v1"], status_code=201,
          summary="Add a photo and/or note to a plant's timeline")
async def v1_add_photo(
    plant_id: int,
    request: Request,
    note: str = Form(default=""),
    date: str = Form(default=""),
    main: bool = Form(default=False),
    photo: UploadFile | None = File(default=None),
):
    token, user = token_auth(request)
    return await add_timeline_entry(plant_id, user["id"], note, date, photo, main, via=token["name"])


@app.get("/api/v1/rooms", tags=["v1"], summary="List rooms")
def v1_rooms(request: Request):
    token_auth(request)
    with db() as c:
        return rooms_list(c)


@app.get("/api/v1/library", tags=["v1"], summary="Starter library of common houseplants")
def v1_library(request: Request):
    token_auth(request)
    return {"source": LIBRARY_SOURCE, "plants": LIBRARY}


def v1_openapi() -> dict[str, Any]:
    routes = [r for r in app.routes if getattr(r, "path", "").startswith("/api/v1/")]
    schema = get_openapi(title="Plants API", version=app.version, routes=routes,
                         description="Authenticate with `Authorization: Bearer <token>`. Create tokens in Settings.")
    schema.setdefault("components", {})["securitySchemes"] = {"bearer": {"type": "http", "scheme": "bearer"}}
    schema["security"] = [{"bearer": []}]
    return schema


@app.get("/api/openapi.json", include_in_schema=False)
def openapi_json():
    return JSONResponse(v1_openapi())


@app.get("/api/docs", include_in_schema=False)
def api_docs():
    return get_swagger_ui_html(openapi_url="/api/openapi.json", title="Plants API")


# ---------------------------------------------------------------- notifications (Apprise)

NOTIFY_DEFAULTS = {
    "notify_urls": "",
    "notify_mode": "digest",
    "notify_hour": "8",
    "quiet_start": "21",
    "quiet_end": "8",
    "overdue_repeat_days": "2",
    "public_url": "",
}


def send_notification(urls: str, title: str, body: str) -> tuple[bool, str]:
    targets = urls.split()
    if not targets:
        return False, "no notification URLs"
    try:
        import apprise
    except ImportError:
        return False, "apprise is not installed"
    ap = apprise.Apprise()
    for url in targets:
        ap.add(url)
    if ap.notify(title=title, body=body):
        return True, ""
    return False, "delivery failed; check the URL and its service"


def in_quiet_hours(hour: int, start: int, end: int) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def notification_items(c, now: datetime, state_key: str = "notify_state") -> list[dict[str, Any]]:
    """Due/overdue tasks that should be announced now, honoring the repeat setting."""
    state = json.loads(get_setting(c, state_key, "{}") or "{}")
    try:
        repeat = int(get_setting(c, "overdue_repeat_days", NOTIFY_DEFAULTS["overdue_repeat_days"]))
    except ValueError:
        repeat = 2
    on = now.date()
    out = []
    for item in due_items(c, 0):
        prev = state.get(str(item["task_id"]))
        if not prev or prev.get("due") != item["next_due"]:
            out.append(item)
        elif item["state"] == "overdue" and repeat > 0:
            sent = iso_date(prev.get("sent"))
            if sent and (on - sent).days >= repeat:
                out.append(item)
    return out


def describe(item: dict[str, Any], base: str) -> str:
    if item["days"] < 0:
        when = f"overdue {-item['days']} day{'s' if item['days'] != -1 else ''}"
    else:
        when = "due today"
    where = f" ({item['room']})" if item["room"] else ""
    line = f"{item['label']} check: {item['plant_name']}{where} - {when}"
    if base:
        line += f"\n{base.rstrip('/')}/#/plant/{item['plant_id']}"
    return line


def mark_announced(c, key: str, items: list[dict[str, Any]], now: datetime) -> None:
    state = json.loads(get_setting(c, key, "{}") or "{}")
    for item in items:
        state[str(item["task_id"])] = {"due": item["next_due"], "sent": now.date().isoformat()}
    live = {str(r[0]) for r in c.execute("SELECT id FROM tasks")}
    state = {k: v for k, v in state.items() if k in live}
    set_setting(c, key, json.dumps(state))


def send_apprise_alerts(cfg: dict[str, str], items, app_name: str, base: str) -> None:
    footer = "Check the soil before watering."
    if cfg["notify_mode"] == "each":
        for item in items:
            ok, detail = send_notification(
                cfg["notify_urls"], f"{app_name}: {item['plant_name']}", describe(item, base) + "\n" + footer
            )
            if not ok:
                raise RuntimeError(detail)
    else:
        body = "\n".join(describe(i, base) for i in items) + "\n\n" + footer
        n = len(items)
        ok, detail = send_notification(cfg["notify_urls"], f"{app_name}: {n} plant{'s' if n != 1 else ''} to check", body)
        if not ok:
            raise RuntimeError(detail)


def run_notification_check(now: datetime | None = None, force: bool = False) -> int:
    """Send due-care alerts through Apprise and browser push.

    Both channels share the schedule (send-from hour, quiet hours, repeat) but keep their own
    "already announced" state, so a failing Apprise URL never blocks push or the other way round.
    Returns how many items were announced on the busiest channel.
    """
    now = now or datetime.now().astimezone()
    errors: list[str] = []
    counts = [0]
    with db() as c:
        cfg = {k: get_setting(c, k, v) for k, v in NOTIFY_DEFAULTS.items()}
        want_apprise = bool(cfg["notify_urls"].strip())
        want_push = push_configured() and c.execute(
            "SELECT 1 FROM push_subscriptions s JOIN users u ON u.id=s.user_id WHERE u.active=1 LIMIT 1"
        ).fetchone() is not None
        if not want_apprise and not want_push:
            return 0
        if not force:
            if in_quiet_hours(now.hour, int(cfg["quiet_start"]), int(cfg["quiet_end"])):
                return 0
            if now.hour < int(cfg["notify_hour"]):
                return 0
        app_name = get_setting(c, "app_name", "Your Plants")
        base = cfg["public_url"].strip()
        if want_apprise:
            items = notification_items(c, now, "notify_state")
            if items:
                try:
                    send_apprise_alerts(cfg, items, app_name, base)
                    mark_announced(c, "notify_state", items, now)
                    counts.append(len(items))
                except Exception as exc:  # keep going so push still goes out
                    errors.append(f"apprise: {exc}")
        if want_push:
            items = notification_items(c, now, "push_state")
            if items:
                send_push_alerts(c, cfg["notify_mode"], items, app_name)
                mark_announced(c, "push_state", items, now)
                counts.append(len(items))
    if errors:
        raise RuntimeError("; ".join(errors))
    return max(counts)


def notification_worker() -> None:
    while True:
        try:
            run_notification_check()
        except Exception as exc:
            print(f"notification check failed: {exc}")
        time.sleep(600)


class NotificationSettingsIn(BaseModel):
    notify_urls: str = Field(default="", max_length=2000)
    notify_mode: Literal["digest", "each"] = "digest"
    notify_hour: int = Field(default=8, ge=0, le=23)
    quiet_start: int = Field(default=21, ge=0, le=23)
    quiet_end: int = Field(default=8, ge=0, le=23)
    overdue_repeat_days: int = Field(default=2, ge=0, le=30)
    public_url: str = Field(default="", max_length=300)

    @field_validator("public_url")
    @classmethod
    def _url(cls, v):
        v = (v or "").strip()
        if v and not re.match(r"^https?://", v, re.I):
            raise ValueError("App address must start with http:// or https://")
        return v


def notify_settings(c) -> dict[str, Any]:
    cfg = {k: get_setting(c, k, v) for k, v in NOTIFY_DEFAULTS.items()}
    for k in ("notify_hour", "quiet_start", "quiet_end", "overdue_repeat_days"):
        cfg[k] = int(cfg[k])
    return cfg


@app.get("/api/notifications")
def get_notifications(request: Request):
    current_user(request, True)
    with db() as c:
        return notify_settings(c)


@app.put("/api/notifications")
def put_notifications(body: NotificationSettingsIn, request: Request):
    current_user(request, True)
    urls = body.notify_urls.strip()
    if any("://" not in u for u in urls.split()):
        raise HTTPException(400, "Each notification URL needs a scheme, like ntfy:// or pover://")
    with db() as c:
        data = body.model_dump()
        data["notify_urls"] = urls
        for k, v in data.items():
            set_setting(c, k, str(v))
        return notify_settings(c)


@app.post("/api/notifications/test")
def test_notification(request: Request):
    current_user(request, True)
    with db() as c:
        urls = get_setting(c, "notify_urls")
        name = get_setting(c, "app_name", "Your Plants")
    if not urls.strip():
        raise HTTPException(400, "Add at least one notification URL first")
    ok, detail = send_notification(urls, f"{name}: test notification", "Notifications are working. Care reminders will arrive here.")
    if not ok:
        raise HTTPException(502, detail or "Notification delivery failed")
    return {"ok": True}


# ---------------------------------------------------------------- browser push (Web Push + VAPID)

PUSH_TTL_SECONDS = 24 * 60 * 60
PUSH_MAX_PER_USER = 20
# Push services used by current browsers. The server only ever POSTs to these, so a signed-in user
# can't point it at an arbitrary host. Add more with PLANTS_PUSH_HOSTS (comma-separated suffixes).
PUSH_HOST_SUFFIXES = (
    "fcm.googleapis.com",  # Chrome, Edge on Android, Brave, Opera, Samsung Internet
    "google.com",  # Chromium builds without Google API keys (jmt17.google.com)
    "push.services.mozilla.com",  # Firefox
    "push.apple.com",  # Safari on macOS and iOS/iPadOS home-screen apps
    "notify.windows.com",  # Edge on Windows
)
B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")


def vapid_keys() -> tuple[str, str]:
    return (os.getenv("PLANTS_VAPID_PUBLIC_KEY", "").strip(), os.getenv("PLANTS_VAPID_PRIVATE_KEY", "").strip())


_vapid_checked: dict[tuple[str, str], str] = {}


def vapid_problem() -> str:
    """Empty when the VAPID key pair is set and valid; otherwise a short reason."""
    public, private = vapid_keys()
    if not public or not private:
        return "not set"
    if (public, private) not in _vapid_checked:
        _vapid_checked[(public, private)] = check_vapid_pair(public, private)
    return _vapid_checked[(public, private)]


def check_vapid_pair(public: str, private: str) -> str:
    try:
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid

        key = Vapid.from_string(private)
        raw = key.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        derived = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    except Exception:
        return "PLANTS_VAPID_PRIVATE_KEY is not a valid key"
    if derived != public.rstrip("="):
        return "PLANTS_VAPID_PUBLIC_KEY doesn't match the private key"
    return ""


def push_configured() -> bool:
    return vapid_problem() == ""


def vapid_subject(c=None) -> str:
    subject = os.getenv("PLANTS_VAPID_SUBJECT", "").strip()
    if subject:
        return subject
    base = get_setting(c, "public_url", "").strip() if c is not None else ""
    if base.startswith("https://"):
        return base
    return "mailto:admin@example.com"


def push_hosts() -> tuple[str, ...]:
    extra = tuple(h.strip().lower().lstrip(".") for h in os.getenv("PLANTS_PUSH_HOSTS", "").split(",") if h.strip())
    return PUSH_HOST_SUFFIXES + extra


def push_endpoint_allowed(endpoint: str) -> bool:
    try:
        url = urllib.parse.urlsplit(endpoint)
    except ValueError:
        return False
    host = (url.hostname or "").lower()
    if url.scheme != "https" or not host or url.username or url.password:
        return False
    return any(host == h or host.endswith("." + h) for h in push_hosts())


def webpush_send(sub: dict[str, Any], payload: str, subject: str) -> None:
    """Deliver one push message. Raises PushGone when the subscription no longer exists."""
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info=sub,
            data=payload,
            vapid_private_key=vapid_keys()[1],
            vapid_claims={"sub": subject},  # fresh dict: pywebpush writes the per-endpoint "aud" into it
            ttl=PUSH_TTL_SECONDS,
            timeout=10,
        )
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        if status in (404, 410):
            raise PushGone(str(status)) from exc
        raise RuntimeError(f"push service returned {status or 'an error'}") from exc


class PushGone(Exception):
    """The browser unsubscribed or the subscription expired."""


def push_to_rows(c, rows, message: dict[str, Any]) -> dict[str, int]:
    """Send one message to each subscription row. Expired ones are deleted; errors are recorded."""
    payload = json.dumps(message)
    subject = vapid_subject(c)
    result = {"sent": 0, "removed": 0, "failed": 0}
    for row in rows:
        sub = {"endpoint": row["endpoint"], "keys": {"p256dh": row["p256dh"], "auth": row["auth"]}}
        try:
            webpush_send(sub, payload, subject)
        except PushGone:
            c.execute("DELETE FROM push_subscriptions WHERE id=?", (row["id"],))
            result["removed"] += 1
        except Exception as exc:
            c.execute("UPDATE push_subscriptions SET last_error=? WHERE id=?", (str(exc)[:200], row["id"]))
            print(f"push delivery failed: {exc}")
            result["failed"] += 1
        else:
            c.execute("UPDATE push_subscriptions SET last_success_at=?, last_error='' WHERE id=?", (now_iso(), row["id"]))
            result["sent"] += 1
    return result


def push_line(item: dict[str, Any]) -> str:
    when = f"overdue {-item['days']} day{'s' if item['days'] != -1 else ''}" if item["days"] < 0 else "due today"
    where = f" ({item['room']})" if item["room"] else ""
    return f"{item['label']} check: {item['plant_name']}{where} - {when}"


def push_messages(mode: str, items: list[dict[str, Any]], app_name: str) -> list[dict[str, Any]]:
    if mode == "each":
        return [
            {"title": f"{app_name}: {i['plant_name']}", "body": push_line(i) + "\nCheck the soil before watering.",
             "tag": f"plants-task-{i['task_id']}", "url": "/#/"}
            for i in items
        ]
    n = len(items)
    lines = [push_line(i) for i in items[:6]]
    if n > 6:
        lines.append(f"and {n - 6} more")
    return [{"title": f"{app_name}: {n} plant{'s' if n != 1 else ''} to check", "body": "\n".join(lines),
             "tag": "plants-due", "url": "/#/"}]


def send_push_alerts(c, mode: str, items: list[dict[str, Any]], app_name: str) -> dict[str, int]:
    total = {"sent": 0, "removed": 0, "failed": 0}
    for message in push_messages(mode, items, app_name):
        rows = c.execute(
            "SELECT s.* FROM push_subscriptions s JOIN users u ON u.id=s.user_id WHERE u.active=1"
        ).fetchall()
        for k, v in push_to_rows(c, rows, message).items():
            total[k] += v
    return total


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=20, max_length=200)
    auth: str = Field(min_length=8, max_length=100)

    @field_validator("p256dh", "auth")
    @classmethod
    def _b64(cls, v):
        if not B64URL_RE.fullmatch(v):
            raise ValueError("Push keys must be base64url")
        return v


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=1000)
    keys: PushKeys


class PushEndpointIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=1000)


def push_status(c, user_id: int, endpoint: str = "") -> dict[str, Any]:
    devices = c.execute("SELECT COUNT(*) FROM push_subscriptions WHERE user_id=?", (user_id,)).fetchone()[0]
    this = None
    if endpoint:
        this = c.execute(
            "SELECT last_success_at, last_error FROM push_subscriptions WHERE user_id=? AND endpoint=?",
            (user_id, endpoint),
        ).fetchone()
    return {
        "configured": push_configured(),
        "problem": "" if push_configured() or vapid_problem() == "not set" else vapid_problem(),
        "public_key": vapid_keys()[0] if push_configured() else "",
        "devices": devices,
        "subscribed": this is not None,
        "last_error": this["last_error"] if this else "",
    }


@app.get("/api/push")
def get_push(request: Request, endpoint: str = ""):
    user = current_user(request)
    with db() as c:
        return push_status(c, user["id"], endpoint[:1000])


@app.post("/api/push/subscribe")
def push_subscribe(body: PushSubscriptionIn, request: Request):
    user = current_user(request)
    if not push_configured():
        raise HTTPException(503, "Push isn't set up on the server yet. An administrator needs to add VAPID keys.")
    if not push_endpoint_allowed(body.endpoint):
        raise HTTPException(400, "That push service isn't on the allowed list (see PLANTS_PUSH_HOSTS in the readme)")
    agent = request.headers.get("user-agent", "")[:200]
    with db() as c:
        existing = c.execute("SELECT id FROM push_subscriptions WHERE endpoint=?", (body.endpoint,)).fetchone()
        if existing:
            c.execute(
                "UPDATE push_subscriptions SET user_id=?, p256dh=?, auth=?, user_agent=?, last_error='' WHERE id=?",
                (user["id"], body.keys.p256dh, body.keys.auth, agent, existing["id"]),
            )
        else:
            count = c.execute("SELECT COUNT(*) FROM push_subscriptions WHERE user_id=?", (user["id"],)).fetchone()[0]
            if count >= PUSH_MAX_PER_USER:
                c.execute(
                    "DELETE FROM push_subscriptions WHERE id IN (SELECT id FROM push_subscriptions WHERE user_id=? "
                    "ORDER BY COALESCE(last_success_at, created_at) LIMIT ?)",
                    (user["id"], count - PUSH_MAX_PER_USER + 1),
                )
            c.execute(
                "INSERT INTO push_subscriptions(user_id,endpoint,p256dh,auth,user_agent,created_at) VALUES(?,?,?,?,?,?)",
                (user["id"], body.endpoint, body.keys.p256dh, body.keys.auth, agent, now_iso()),
            )
        return push_status(c, user["id"], body.endpoint)


@app.post("/api/push/unsubscribe")
def push_unsubscribe(body: PushEndpointIn, request: Request):
    user = current_user(request)
    with db() as c:
        c.execute("DELETE FROM push_subscriptions WHERE user_id=? AND endpoint=?", (user["id"], body.endpoint))
        return push_status(c, user["id"])


@app.post("/api/push/test")
def push_test(body: PushEndpointIn, request: Request):
    user = current_user(request)
    if not push_configured():
        raise HTTPException(503, "Push isn't set up on the server yet")
    with db() as c:
        rows = c.execute(
            "SELECT * FROM push_subscriptions WHERE user_id=? AND endpoint=?", (user["id"], body.endpoint)
        ).fetchall()
        if not rows:
            raise HTTPException(404, "This device isn't subscribed. Turn push on first.")
        name = get_setting(c, "app_name", "Your Plants")
        result = push_to_rows(c, rows, {"title": f"{name}: test notification",
                                        "body": "Push is working. Care reminders will show up here.",
                                        "tag": "plants-test", "url": "/#/"})
    if result["removed"]:
        raise HTTPException(410, "This browser's subscription has expired. Turn push off and on again.")
    if result["failed"]:
        raise HTTPException(502, "The push service didn't accept the message. Check the server's VAPID keys.")
    return {"ok": True}


# ---------------------------------------------------------------- backup


BACKUP_TABLES = ("settings", "users", "api_tokens", "rooms", "plants", "tasks", "events")
BACKUP_DELETE_ORDER = ("push_subscriptions", "events", "tasks", "plants", "rooms", "api_tokens", "sessions", "users", "settings")


@app.get("/api/export")
def export_data(request: Request):
    current_user(request, True)
    with db() as c:
        tables = {t: [dict(r) for r in c.execute(f"SELECT * FROM {t}")] for t in BACKUP_TABLES}
    tables["settings"] = [r for r in tables["settings"] if r["key"] not in ("notify_state", "push_state")]
    names = {r["photo"] for r in tables["plants"] if r["photo"]} | {r["photo"] for r in tables["events"] if r["photo"]}
    files = {}
    for name in names:
        if STORED_NAME_RE.fullmatch(name or "") and (PHOTOS_DIR / name).is_file():
            files[name] = base64.b64encode((PHOTOS_DIR / name).read_bytes()).decode("ascii")
    return {"app": "plants", "version": 1, "exported_at": now_iso(), "tables": tables, "photos": files}


@app.post("/api/import")
async def import_data(request: Request):
    user = current_user(request, True)
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "That file isn't a Plants backup")
    if not isinstance(data, dict) or data.get("app") != "plants" or not isinstance(data.get("tables"), dict):
        raise HTTPException(400, "That file isn't a Plants backup")
    tables = data["tables"]
    if not any(u.get("is_admin") and u.get("active", 1) for u in tables.get("users", [])):
        raise HTTPException(400, "Backup has no active administrator; refusing to lock you out")
    photos = data.get("photos") or {}
    with db() as c:
        cols = {t: {r[1] for r in c.execute(f"PRAGMA table_info({t})")} for t in BACKUP_TABLES}
        for t in BACKUP_DELETE_ORDER:
            c.execute(f"DELETE FROM {t}")
        for t in BACKUP_TABLES:
            for row in tables.get(t, []):
                keys = [k for k in row if k in cols[t]]
                if keys:
                    c.execute(
                        f"INSERT INTO {t}({','.join(keys)}) VALUES({','.join('?' * len(keys))})",
                        [row[k] for k in keys],
                    )
    for name, b64 in photos.items():
        if STORED_NAME_RE.fullmatch(name):
            (PHOTOS_DIR / name).write_bytes(base64.b64decode(b64))
    return {"ok": True, "signed_out": True, "by": user["username"]}


# ---------------------------------------------------------------- static


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(BASE / "static" / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(BASE / "static" / "index.html", headers={"Cache-Control": "no-cache"})
