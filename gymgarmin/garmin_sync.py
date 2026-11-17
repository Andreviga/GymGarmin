"""Integração direta com o Garmin Connect (API não-oficial via `garminconnect`).

Por que não-oficial? A API oficial de treinos da Garmin (Connect Developer Program /
Training API) só é liberada para empresas aprovadas. Para uso pessoal, a biblioteca
open-source `garminconnect` (>= 0.3.x) faz o mesmo login que o app móvel da Garmin
e usa os endpoints do editor web de treinos (`/workout-service`).

Observações importantes (nov/2026):
- `garth` foi descontinuado em 2026 porque a Garmin mudou o fluxo de autenticação;
  o `garminconnect` 0.3.x tem login próprio (com suporte a MFA). Tokens antigos do
  garth não funcionam: faça login de novo uma vez.
- Os tokens ficam em ~/.gymgarmin/garmin_tokens (ou $GYMGARMIN_HOME). A senha NÃO é
  salva — só os tokens, que se renovam sozinhos.
- Por ser uma API não documentada, a Garmin pode mudá-la sem aviso. Se o envio
  falhar, use o arquivo .FIT (cópia via USB), que não depende de API.
"""

from __future__ import annotations

import os
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import catalog

_lock = threading.Lock()
_client = None
_pending_mfa: dict[str, Any] = {}


def home_dir() -> Path:
    from .db import home_dir as _home

    return _home()


def token_dir() -> Path:
    return home_dir() / "garmin_tokens"


class GarminUnavailable(RuntimeError):
    pass


def _lib():
    try:
        import garminconnect  # noqa: F401
        from garminconnect import Garmin
    except ImportError as e:  # pragma: no cover - depende do ambiente
        raise GarminUnavailable(
            "Biblioteca 'garminconnect' não instalada. Rode: pip install 'garminconnect>=0.3.20'"
        ) from e
    return Garmin


def status() -> dict:
    tdir = token_dir()
    has_tokens = tdir.exists() and any(tdir.iterdir()) if tdir.exists() else False
    try:
        _lib()
        lib_ok = True
    except GarminUnavailable:
        lib_ok = False
    return {
        "library_installed": lib_ok,
        "has_tokens": bool(has_tokens),
        "connected": _client is not None,
        "mfa_pending": bool(_pending_mfa),
        "display_name": getattr(_client, "display_name", None) if _client else None,
    }


def connect_with_tokens() -> bool:
    """Tenta reconectar com tokens salvos. Retorna True se conectado."""
    global _client
    with _lock:
        if _client is not None:
            return True
        tdir = token_dir()
        if not tdir.exists() or not any(tdir.iterdir()):
            return False
        Garmin = _lib()
        api = Garmin()
        api.login(str(tdir))
        _client = api
        return True


def login(email: str, password: str) -> dict:
    """Login com e-mail e senha. Se a conta tiver MFA, retorna {'mfa_required': True}."""
    global _client
    Garmin = _lib()
    with _lock:
        api = Garmin(email=email, password=password, return_on_mfa=True)
        result, state = api.login(str(token_dir()))
        if result == "needs_mfa":
            _pending_mfa.clear()
            _pending_mfa.update({"api": api, "state": state})
            return {"mfa_required": True}
        _save_tokens(api)
        _client = api
        return {"connected": True, "display_name": getattr(api, "display_name", None)}


def submit_mfa(code: str) -> dict:
    global _client
    with _lock:
        if not _pending_mfa:
            raise RuntimeError("Nenhum login aguardando código MFA")
        api, state = _pending_mfa["api"], _pending_mfa["state"]
        api.resume_login(state, code.strip())
        _pending_mfa.clear()
        _save_tokens(api)
        _client = api
        return {"connected": True, "display_name": getattr(api, "display_name", None)}


def _save_tokens(api) -> None:
    tdir = token_dir()
    tdir.mkdir(parents=True, exist_ok=True)
    try:
        api.client.dump(str(tdir))
        os.chmod(tdir, 0o700)
    except Exception:  # noqa: BLE001 - salvar tokens é melhor-esforço
        pass


def logout() -> None:
    global _client
    with _lock:
        _client = None
        _pending_mfa.clear()
        tdir = token_dir()
        if tdir.exists():
            for f in tdir.iterdir():
                f.unlink()


def client():
    if _client is None and not connect_with_tokens():
        raise RuntimeError("Não conectado ao Garmin Connect. Faça login em Configurações → Garmin.")
    return _client


def push_workout(workout_json: dict, schedule_date: str | None = None) -> dict:
    """Envia o treino ao Garmin Connect e (opcional) agenda no calendário."""
    api = client()
    created = api.upload_workout(workout_json)
    workout_id = created.get("workoutId") if isinstance(created, dict) else None
    out = {"workout_id": workout_id, "name": workout_json.get("workoutName")}
    if schedule_date and workout_id:
        sched = api.schedule_workout(workout_id, schedule_date)
        out["scheduled"] = schedule_date
        out["schedule_id"] = (sched or {}).get("workoutScheduleId") if isinstance(sched, dict) else None
    if workout_id:
        out["url"] = f"https://connect.garmin.com/modern/workout/{workout_id}"
    return out


def delete_workout(workout_id: int) -> None:
    client().delete_workout(workout_id)


# ---------------------------------------------------------------------------
# Importar treinos feitos no relógio → sessões do app
# ---------------------------------------------------------------------------


def _reverse_map(preferred: set[str] | None = None) -> dict[tuple[str, str], str]:
    rev: dict[tuple[str, str], str] = {}
    for ex in catalog.all_exercises():
        g = ex.get("garmin") or {}
        key = (g.get("category"), g.get("exercise"))
        if key not in rev or ex["id"] in (preferred or set()) or (ex["popular"] and rev[key] not in (preferred or set())):
            rev[key] = ex["id"]
        cat_key = (g.get("category"), None)
        if cat_key not in rev or ex["id"] in (preferred or set()):
            rev[cat_key] = ex["id"]
    return rev


def activity_to_session(activity: dict, exercise_sets: dict | None, preferred_ids: set[str] | None = None,
                        hr_max: int | None = None) -> dict:
    """Converte uma atividade do Garmin (com séries) em sessão do GymGarmin."""
    start = activity.get("startTimeLocal") or activity.get("startTimeGMT") or datetime.now().isoformat()
    session = {
        "date": str(start).replace(" ", "T"),
        "source": "garmin",
        "garmin_activity_id": activity.get("activityId"),
        "notes": activity.get("activityName"),
        "items": [],
    }
    sets = (exercise_sets or {}).get("exerciseSets") or []
    if sets:
        rev = _reverse_map(preferred_ids)
        current: dict | None = None
        for s in sets:
            if s.get("setType") != "ACTIVE":
                continue
            exs = sorted(s.get("exercises") or [], key=lambda e: -(e.get("probability") or 0))
            cat = exs[0].get("category") if exs else None
            name = exs[0].get("name") if exs else None
            eid = rev.get((cat, name)) or rev.get((cat, None)) or "Garmin_desconhecido"
            w = s.get("weight")
            load = (w / 1000.0 if w and w > 500 else w) or 0
            entry = {"reps": s.get("repetitionCount") or 0, "load_kg": round(load, 1),
                     "duration_s": round(s.get("duration") or 0)}
            if current and current["exercise_id"] == eid:
                current["sets"].append(entry)
            else:
                current = {"exercise_id": eid, "sets": [entry], "garmin": {"category": cat, "name": name}}
                session["items"].append(current)
    else:
        type_key = ((activity.get("activityType") or {}).get("typeKey") or "").lower()
        machine = {
            "treadmill_running": "cardio_esteira_corrida", "indoor_running": "cardio_esteira_corrida",
            "indoor_cycling": "cardio_bike_vertical", "virtual_ride": "cardio_bike_vertical",
            "elliptical": "cardio_eliptico", "indoor_rowing": "cardio_remo", "stair_climbing": "cardio_escada",
            "walking": "cardio_esteira_caminhada", "treadmill_walking": "cardio_esteira_caminhada",
        }.get(type_key)
        if machine:
            avg = activity.get("averageHR")
            zone = 2
            if avg and hr_max:
                pct = avg / hr_max
                zone = 1 if pct < 0.6 else 2 if pct < 0.7 else 3 if pct < 0.8 else 4 if pct < 0.9 else 5
            session["items"].append({"exercise_id": machine, "duration_min": round((activity.get("duration") or 0) / 60, 1),
                                     "zone": zone, "avg_hr": avg, "kcal": activity.get("calories")})
    return session


def recent_activities(days: int = 14) -> list[dict]:
    api = client()
    start = (date.today() - timedelta(days=days)).isoformat()
    acts = api.get_activities_by_date(start, date.today().isoformat())
    return [{
        "activity_id": a.get("activityId"), "name": a.get("activityName"),
        "type": (a.get("activityType") or {}).get("typeKey"), "start": a.get("startTimeLocal"),
        "duration_min": round((a.get("duration") or 0) / 60), "avg_hr": a.get("averageHR"),
        "calories": a.get("calories"),
    } for a in acts or []]


def import_activity(activity_id: int, preferred_ids: set[str] | None = None, hr_max: int | None = None) -> dict:
    api = client()
    act = api.get_activity(str(activity_id))
    summary = act.get("summaryDTO") or {}
    flat = {
        "activityId": act.get("activityId", activity_id),
        "activityName": act.get("activityName"),
        "activityType": act.get("activityTypeDTO") or act.get("activityType") or {},
        "startTimeLocal": summary.get("startTimeLocal") or act.get("startTimeLocal"),
        "duration": summary.get("duration") or act.get("duration"),
        "averageHR": summary.get("averageHR") or act.get("averageHR"),
        "calories": summary.get("calories") or act.get("calories"),
    }
    sets = None
    if "strength" in str((flat["activityType"] or {}).get("typeKey", "")):
        sets = api.get_activity_exercise_sets(activity_id)
    return activity_to_session(flat, sets, preferred_ids, hr_max)
