"""Persistência: SQLite local (um arquivo, sem servidor) ou Postgres quando houver DATABASE_URL.

- No computador: SQLite em ~/.gymgarmin/gymgarmin.sqlite3.
- No Vercel: Postgres (Neon) via DATABASE_URL — os dados persistem entre execuções.
  Sem DATABASE_URL o Vercel usa SQLite em /tmp (temporário).

Modelo pensado para multiusuário desde já (coluna user_id), embora a primeira
versão seja de uso pessoal: o usuário 1 é criado automaticamente.
Planilhas e sessões guardam os itens como JSON — simples de evoluir.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import ssl
import threading
from contextlib import contextmanager
from urllib.parse import parse_qs, unquote, urlparse
from datetime import datetime
from pathlib import Path
from . import clock

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
CREATE TABLE IF NOT EXISTS kv (
    user_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
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


def home_dir() -> Path:
    """Pasta de dados: $GYMGARMIN_HOME, /tmp no Vercel (único lugar gravável) ou ~/.gymgarmin."""
    env = os.environ.get("GYMGARMIN_HOME")
    if env:
        p = Path(env)
    elif os.environ.get("VERCEL"):
        p = Path("/tmp/gymgarmin")
    else:
        p = Path.home() / ".gymgarmin"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    env = os.environ.get("GYMGARMIN_DB")
    if env:
        return Path(env)
    return home_dir() / "gymgarmin.sqlite3"


_ready: set[str] = set()
ID_TABLES = ("users", "plans", "sessions", "schedule", "garmin_pushes")


def now() -> str:
    return clock.now().isoformat(timespec="seconds")


def database_url() -> str | None:
    url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
    return url if url and url.startswith(("postgres://", "postgresql://")) else None


def is_postgres() -> bool:
    return database_url() is not None


# ---- adaptador Postgres (mesma interface usada com sqlite3) ----------------

class _PGCursor:
    def __init__(self, cur, returning_id: bool):
        self._cur = cur
        self.lastrowid = None
        self._rows = None
        if cur.description:
            cols = [d[0] for d in cur.description]
            self._rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            if returning_id and self._rows:
                self.lastrowid = self._rows[0].get("id")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows or []


class _PGConn:
    """Conexão pg8000 com tradução mínima do SQL do SQLite (? → %s, RETURNING id)."""

    def __init__(self, url: str):
        import pg8000.dbapi

        u = urlparse(url)
        q = parse_qs(u.query)
        ctx = None
        if q.get("sslmode", ["require"])[0] != "disable":
            ctx = ssl.create_default_context()
        self.raw = pg8000.dbapi.connect(
            user=unquote(u.username or ""), password=unquote(u.password or ""), host=u.hostname,
            port=u.port or 5432, database=(u.path or "/postgres").lstrip("/") or "postgres",
            ssl_context=ctx, timeout=20,
        )

    @staticmethod
    def _sql(sql: str) -> tuple[str, bool]:
        out = sql.replace("?", "%s")
        returning = False
        m = re.match(r"\s*INSERT\s+INTO\s+(\w+)", out, re.I)
        if m and m.group(1) in ID_TABLES and "RETURNING" not in out.upper():
            out += " RETURNING id"
            returning = True
        return out, returning

    def execute(self, sql: str, args=()):
        q, returning = self._sql(sql)
        cur = self.raw.cursor()
        cur.execute(q, tuple(args))
        return _PGCursor(cur, returning)

    def executescript(self, script: str) -> None:
        script = script.replace("INTEGER PRIMARY KEY,", "SERIAL PRIMARY KEY,")
        for stmt in script.split(";"):
            if stmt.strip():
                self.raw.cursor().execute(stmt)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        try:
            self.raw.close()
        except Exception:  # noqa: BLE001
            pass


_pg_lock = threading.RLock()
_pg_state = threading.local()
_pg_shared: dict[str, _PGConn] = {}


@contextmanager
def _pg_conn(url: str):
    with _pg_lock:
        depth = getattr(_pg_state, "depth", 0)
        if depth == 0:
            c = _pg_shared.get(url)
            if c is None:
                c = _pg_shared[url] = _PGConn(url)
            else:
                try:  # conexão reaproveitada (função "quente"): confere se ainda está viva
                    c.raw.cursor().execute("SELECT 1")
                except Exception:  # noqa: BLE001
                    c.close()
                    c = _pg_shared[url] = _PGConn(url)
        c = _pg_shared[url]
        _pg_state.depth = depth + 1
        try:
            yield c
            if depth == 0:
                c.commit()
        except Exception:
            if depth == 0:
                c.rollback()
            raise
        finally:
            _pg_state.depth = depth


@contextmanager
def conn():
    url = database_url()
    key = url or str(db_path())
    if key not in _ready:
        _ready.add(key)
        init()
    if url:
        with _pg_conn(url) as c:
            yield c
        return
    c = sqlite3.connect(db_path())
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _fix_sequences(c) -> None:
    if not is_postgres():
        return
    for t in ID_TABLES:
        c.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), COALESCE((SELECT MAX(id) FROM {t}), 0) + 1, false)")


def init() -> None:
    _ready.add(database_url() or str(db_path()))
    with conn() as c:
        c.executescript(SCHEMA)
        if not c.execute("SELECT 1 FROM users WHERE id = 1").fetchone():
            c.execute("INSERT INTO users (id, name, profile, created_at) VALUES (1, 'Eu', ?, ?)",
                      (json.dumps(DEFAULT_PROFILE), now()))
            _fix_sequences(c)


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
    keep = {k: plan.get(k) for k in ("days", "days_per_week", "notes", "goal", "generator", "goals_week", "rationale",
                                     "endurance_plan")
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
                                  "schedule_id", "garmin_summary", "merged") if k in s}
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
        c.execute("INSERT INTO garmin_override (user_id, exercise_id, category, exercise) VALUES (?,?,?,?) "
                  "ON CONFLICT (user_id, exercise_id) DO UPDATE SET category = excluded.category, "
                  "exercise = excluded.exercise",
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


# ---- backup ------------------------------------------------------------------

# ---- chave/valor (estado da integração Garmin; fora do backup de propósito) --

def kv_get(key: str, user_id: int = 1) -> str | None:
    with conn() as c:
        r = c.execute("SELECT value FROM kv WHERE user_id = ? AND key = ?", (user_id, key)).fetchone()
    return r["value"] if r else None


def kv_set(key: str, value: str, user_id: int = 1) -> None:
    with conn() as c:
        c.execute("INSERT INTO kv (user_id, key, value, updated_at) VALUES (?,?,?,?) "
                  "ON CONFLICT (user_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                  (user_id, key, value, now()))


def kv_delete(key: str, user_id: int = 1) -> None:
    with conn() as c:
        c.execute("DELETE FROM kv WHERE user_id = ? AND key = ?", (user_id, key))


def garmin_imported_ids(user_id: int = 1) -> set:
    """IDs de atividades do Garmin já importadas (para a sincronização não duplicar)."""
    out = set()
    with conn() as c:
        for r in c.execute("SELECT data FROM sessions WHERE user_id = ?", (user_id,)).fetchall():
            aid = json.loads(r["data"]).get("garmin_activity_id")
            if aid:
                out.add(str(aid))
    return out


BACKUP_TABLES = ("users", "plans", "sessions", "schedule", "garmin_override")


def export_all() -> dict:
    out = {"version": 1, "exported_at": now()}
    with conn() as c:
        for t in BACKUP_TABLES:
            out[t] = [dict(r) for r in c.execute(f"SELECT * FROM {t}").fetchall()]
    return out


def import_all(data: dict) -> dict:
    counts = {}
    present = [t for t in BACKUP_TABLES if data.get(t) is not None]
    with conn() as c:
        for t in reversed(present):  # filhos antes dos pais (chaves estrangeiras no Postgres)
            c.execute(f"DELETE FROM {t}")
        for t in present:
            for r in data[t]:
                cols = list(r.keys())
                c.execute(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                          [r[k] for k in cols])
            counts[t] = len(data[t])
        _fix_sequences(c)
    return counts
