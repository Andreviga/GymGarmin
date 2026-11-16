"""Persistência local em SQLite (um arquivo, sem servidor).

Modelo pensado para multiusuário desde já (coluna user_id), embora a primeira
versão seja de uso pessoal: o usuário 1 é criado automaticamente.
Planilhas e sessões guardam os itens como JSON — simples de evoluir.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL DEFAULT 'Eu',
    profile TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    data TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    date TEXT NOT NULL,
    plan_id INTEGER,
    day_name TEXT,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_date ON sessions(user_id, date);
CREATE TABLE IF NOT EXISTS garmin_override (
    user_id INTEGER NOT NULL,
    exercise_id TEXT NOT NULL,
    category TEXT NOT NULL,
    exercise TEXT NOT NULL,
    PRIMARY KEY (user_id, exercise_id)
);
CREATE TABLE IF NOT EXISTS schedule (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    plan_id INTEGER,
    day_idx INTEGER,
    status TEXT NOT NULL DEFAULT 'planejado',
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_schedule_user_date ON schedule(user_id, date);
CREATE TABLE IF NOT EXISTS garmin_pushes (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    plan_id INTEGER,
    day_name TEXT,
    workout_id INTEGER,
    scheduled TEXT,
    created_at TEXT NOT NULL
);
"""

DEFAULT_PROFILE = {
    "name": "Eu",
    "age": None,
    "sex": None,
    "weight_kg": None,
    "height_cm": None,
    "hr_max": None,
    "hr_rest": None,
    "level": "intermediario",
    "goal": "hipertrofia",
    "volume_factor": 1.0,
    "days_per_week": 4,
    "limitations": [],
    "training_months": None,
    "available_days": None,
    "session_minutes": 60,
    "equipment": [],
    "cardio_machines": [],
    "cardio_target_min": None,
    "schedule_plan_id": None,
}


def db_path() -> Path:
    env = os.environ.get("GYMGARMIN_DB")
    if env:
        return Path(env)
    home = Path(os.environ.get("GYMGARMIN_HOME", Path.home() / ".gymgarmin"))
    home.mkdir(parents=True, exist_ok=True)
    return home / "gymgarmin.sqlite3"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def conn():
    c = sqlite3.connect(db_path())
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        if not c.execute("SELECT 1 FROM users WHERE id = 1").fetchone():
            c.execute("INSERT INTO users (id, name, profile, created_at) VALUES (1, 'Eu', ?, ?)",
                      (json.dumps(DEFAULT_PROFILE), now()))


# ---- perfil ---------------------------------------------------------------

def get_profile(user_id: int = 1) -> dict:
    with conn() as c:
        row = c.execute("SELECT profile FROM users WHERE id = ?", (user_id,)).fetchone()
    return {**DEFAULT_PROFILE, **(json.loads(row["profile"]) if row else {})}


def save_profile(profile: dict, user_id: int = 1) -> dict:
    merged = {**get_profile(user_id), **profile}
    with conn() as c:
        c.execute("UPDATE users SET profile = ?, name = ? WHERE id = ?",
                  (json.dumps(merged, ensure_ascii=False), merged.get("name") or "Eu", user_id))
    return merged


# ---- planilhas ------------------------------------------------------------

def _plan_row(r) -> dict:
    d = json.loads(r["data"])
    return {"id": r["id"], "name": r["name"], "active": bool(r["active"]),
            "created_at": r["created_at"], "updated_at": r["updated_at"], **d}


def list_plans(user_id: int = 1) -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM plans WHERE user_id = ? ORDER BY active DESC, updated_at DESC",
                         (user_id,)).fetchall()
    return [_plan_row(r) for r in rows]


def get_plan(plan_id: int, user_id: int = 1) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM plans WHERE id = ? AND user_id = ?", (plan_id, user_id)).fetchone()
    return _plan_row(r) if r else None


def _plan_data(plan: dict) -> str:
    keep = {k: plan.get(k) for k in ("days", "days_per_week", "notes", "goal", "generator", "goals_week", "rationale")
            if k in plan}
    keep.setdefault("days", [])
    return json.dumps(keep, ensure_ascii=False)


def create_plan(plan: dict, user_id: int = 1) -> dict:
    with conn() as c:
        cur = c.execute("INSERT INTO plans (user_id, name, data, active, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                        (user_id, plan.get("name") or "Minha planilha", _plan_data(plan),
                         1 if plan.get("active", True) else 0, now(), now()))
        pid = cur.lastrowid
    return get_plan(pid, user_id)


def update_plan(plan_id: int, plan: dict, user_id: int = 1) -> dict | None:
    with conn() as c:
        c.execute("UPDATE plans SET name = ?, data = ?, active = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                  (plan.get("name") or "Minha planilha", _plan_data(plan), 1 if plan.get("active", True) else 0,
                   now(), plan_id, user_id))
    return get_plan(plan_id, user_id)


def delete_plan(plan_id: int, user_id: int = 1) -> None:
    with conn() as c:
        c.execute("DELETE FROM plans WHERE id = ? AND user_id = ?", (plan_id, user_id))


# ---- sessões (treinos realizados) -----------------------------------------

def _session_row(r) -> dict:
    d = json.loads(r["data"])
    return {"id": r["id"], "date": r["date"], "plan_id": r["plan_id"], "day_name": r["day_name"], **d}


def list_sessions(user_id: int = 1, since: str | None = None, limit: int = 500) -> list[dict]:
    q = "SELECT * FROM sessions WHERE user_id = ?"
    args: list = [user_id]
    if since:
        q += " AND date >= ?"
        args.append(since)
    q += " ORDER BY date DESC LIMIT ?"
    args.append(limit)
    with conn() as c:
        rows = c.execute(q, args).fetchall()
    return [_session_row(r) for r in rows]


def get_session(session_id: int, user_id: int = 1) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id)).fetchone()
    return _session_row(r) if r else None


def _session_data(s: dict) -> str:
    keep = {k: s.get(k) for k in ("items", "notes", "source", "garmin_activity_id", "feeling", "duration_min",
                                  "schedule_id") if k in s}
    keep.setdefault("items", [])
    return json.dumps(keep, ensure_ascii=False)


def save_session(s: dict, user_id: int = 1, session_id: int | None = None) -> dict:
    date = s.get("date") or now()
    with conn() as c:
        if session_id:
            c.execute("UPDATE sessions SET date = ?, plan_id = ?, day_name = ?, data = ? WHERE id = ? AND user_id = ?",
                      (date, s.get("plan_id"), s.get("day_name"), _session_data(s), session_id, user_id))
            sid = session_id
        else:
            if s.get("garmin_activity_id"):
                for r in c.execute("SELECT id, data FROM sessions WHERE user_id = ?", (user_id,)).fetchall():
                    if json.loads(r["data"]).get("garmin_activity_id") == s["garmin_activity_id"]:
                        raise ValueError("Esta atividade do Garmin já foi importada")
            cur = c.execute("INSERT INTO sessions (user_id, date, plan_id, day_name, data, created_at) VALUES (?,?,?,?,?,?)",
                            (user_id, date, s.get("plan_id"), s.get("day_name"), _session_data(s), now()))
            sid = cur.lastrowid
    return get_session(sid, user_id)


def delete_session(session_id: int, user_id: int = 1) -> None:
    with conn() as c:
        c.execute("DELETE FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id))


def last_performance(exercise_id: str, user_id: int = 1) -> dict | None:
    """Última vez que o exercício foi feito (para sugerir carga/progressão)."""
    for s in list_sessions(user_id, limit=200):
        for it in s.get("items", []):
            if it.get("exercise_id") == exercise_id and (it.get("sets") or it.get("duration_min")):
                return {"date": s["date"], **{k: it.get(k) for k in ("sets", "duration_min", "zone")}}
    return None


# ---- mapeamento Garmin personalizado --------------------------------------

def garmin_overrides(user_id: int = 1) -> dict[str, dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM garmin_override WHERE user_id = ?", (user_id,)).fetchall()
    return {r["exercise_id"]: {"category": r["category"], "exercise": r["exercise"]} for r in rows}


def set_garmin_override(exercise_id: str, category: str, exercise: str, user_id: int = 1) -> None:
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO garmin_override (user_id, exercise_id, category, exercise) VALUES (?,?,?,?)",
                  (user_id, exercise_id, category, exercise))


def log_push(plan_id: int | None, day_name: str, workout_id: int | None, scheduled: str | None, user_id: int = 1) -> None:
    with conn() as c:
        c.execute("INSERT INTO garmin_pushes (user_id, plan_id, day_name, workout_id, scheduled, created_at) VALUES (?,?,?,?,?,?)",
                  (user_id, plan_id, day_name, workout_id, scheduled, now()))


# ---- agenda semanal --------------------------------------------------------

ENTRY_COLS = ("id", "date", "plan_id", "day_idx", "status")


def _entry_row(r) -> dict:
    return {"id": r["id"], "date": r["date"], "plan_id": r["plan_id"], "day_idx": r["day_idx"],
            "status": r["status"], **json.loads(r["data"])}


def list_schedule(start: str, end: str, user_id: int = 1) -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM schedule WHERE user_id = ? AND date >= ? AND date <= ? ORDER BY date, id",
                         (user_id, start, end)).fetchall()
    return [_entry_row(r) for r in rows]


def get_entry(entry_id: int, user_id: int = 1) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM schedule WHERE id = ? AND user_id = ?", (entry_id, user_id)).fetchone()
    return _entry_row(r) if r else None


def save_entry(e: dict, user_id: int = 1) -> dict:
    data = json.dumps({k: v for k, v in e.items() if k not in ENTRY_COLS}, ensure_ascii=False, default=str)
    with conn() as c:
        if e.get("id"):
            c.execute("UPDATE schedule SET date = ?, plan_id = ?, day_idx = ?, status = ?, data = ?, updated_at = ? "
                      "WHERE id = ? AND user_id = ?",
                      (e["date"], e.get("plan_id"), e.get("day_idx"), e.get("status", "planejado"), data, now(),
                       e["id"], user_id))
            eid = e["id"]
        else:
            cur = c.execute("INSERT INTO schedule (user_id, date, plan_id, day_idx, status, data, created_at, updated_at) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            (user_id, e["date"], e.get("plan_id"), e.get("day_idx"), e.get("status", "planejado"),
                             data, now(), now()))
            eid = cur.lastrowid
    return get_entry(eid, user_id)


def delete_entry(entry_id: int, user_id: int = 1) -> None:
    with conn() as c:
        c.execute("DELETE FROM schedule WHERE id = ? AND user_id = ?", (entry_id, user_id))
