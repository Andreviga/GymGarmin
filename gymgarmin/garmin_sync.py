"""Integração direta com o Garmin Connect (API não-oficial via `garminconnect`).

Por que não-oficial? A API oficial de treinos da Garmin (Connect Developer Program /
Training API) só é liberada para empresas aprovadas. Para uso pessoal, a biblioteca
open-source `garminconnect` (>= 0.3.x) faz o mesmo login que o app móvel da Garmin
e usa os endpoints do editor web de treinos (`/workout-service`).

Observações importantes (nov/2026):
- `garth` foi descontinuado em 2026 porque a Garmin mudou o fluxo de autenticação;
  o `garminconnect` 0.3.x tem login próprio (com suporte a MFA). Tokens antigos do
  garth não funcionam: faça login de novo uma vez.
- Os tokens ficam no banco (tabela kv), criptografados com GARMIN_TOKEN_KEY. A senha
  NÃO é salva — só os tokens, que se renovam sozinhos.
- Sincronização automática: uma vez por dia (cron da Vercel) e ao abrir o app se a
  última tiver mais de 6 h. Nunca tenta login por senha sozinha: se o token expirar,
  marca "reconectar" e espera o usuário entrar de novo.
- Por ser uma API não documentada, a Garmin pode mudá-la sem aviso. Se o envio
  falhar, use o arquivo .FIT (cópia via USB), que não depende de API.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
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
    """Pasta antiga dos tokens (antes de irem para o banco). Só lida para migrar."""
    return home_dir() / "garmin_tokens"


class GarminUnavailable(RuntimeError):
    pass


class GarminNeedsLogin(RuntimeError):
    """Sem tokens válidos: é preciso entrar de novo (e-mail/senha e, se houver, MFA)."""


def _lib():
    try:
        import garminconnect  # noqa: F401
        from garminconnect import Garmin
    except ImportError as e:  # pragma: no cover - depende do ambiente
        raise GarminUnavailable(
            "Biblioteca 'garminconnect' não instalada. Rode: pip install 'garminconnect>=0.3.20'"
        ) from e
    return Garmin


# ---------------------------------------------------------------------------
# Tokens e estado do MFA guardados no banco, criptografados.
# Na Vercel o disco é temporário: arquivo de token some a cada nova instância.
# A senha do Garmin nunca é guardada — só os tokens, que se renovam sozinhos.
# ---------------------------------------------------------------------------

TOKENS_KEY = "garmin_tokens"
MFA_KEY = "garmin_mfa"
SYNC_KEY = "garmin_sync"
MFA_TTL_MIN = 10


def _secret() -> str:
    secret = os.environ.get("GARMIN_TOKEN_KEY")
    if secret:
        return secret
    if os.environ.get("VERCEL"):
        raise GarminUnavailable("Falta a variável GARMIN_TOKEN_KEY no servidor para guardar o login do Garmin.")
    # Uso local: chave aleatória num arquivo só do usuário
    kf = home_dir() / "garmin.key"
    if not kf.exists():
        kf.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(48))
    return kf.read_text().strip()


def _fernet():
    from cryptography.fernet import Fernet

    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(_secret().encode()).digest()))


def _put_secret(key: str, data: dict | str) -> None:
    from . import db

    raw = data if isinstance(data, str) else json.dumps(data)
    db.kv_set(key, _fernet().encrypt(raw.encode()).decode())


def _get_secret(key: str) -> str | None:
    from cryptography.fernet import InvalidToken

    from . import db

    v = db.kv_get(key)
    if not v:
        return None
    try:
        return _fernet().decrypt(v.encode()).decode()
    except InvalidToken:
        return None  # chave trocada: trata como sem login


def _del(key: str) -> None:
    from . import db

    db.kv_delete(key)


def _migrate_file_tokens() -> None:
    """Tokens que ficaram na pasta antiga (versão local) passam para o banco uma vez."""
    f = token_dir() / "garmin_tokens.json"
    try:
        if f.exists() and not _get_secret(TOKENS_KEY):
            _put_secret(TOKENS_KEY, f.read_text())
            f.unlink()
    except Exception:  # noqa: BLE001 - migração é melhor-esforço
        pass


def sync_state() -> dict:
    from . import db

    try:
        return json.loads(db.kv_get(SYNC_KEY) or "{}")
    except ValueError:
        return {}


def _set_sync_state(**kw) -> dict:
    from . import db

    st = sync_state()
    st.update(kw)
    db.kv_set(SYNC_KEY, json.dumps(st, ensure_ascii=False))
    return st


def _mfa_pending() -> bool:
    if _pending_mfa:
        return True
    raw = _get_secret(MFA_KEY)
    if not raw:
        return False
    try:
        started = datetime.fromisoformat(json.loads(raw).get("started"))
    except (ValueError, TypeError):
        return False
    return datetime.now() - started < timedelta(minutes=MFA_TTL_MIN)


def status() -> dict:
    try:
        _lib()
        lib_ok = True
    except GarminUnavailable:
        lib_ok = False
    try:
        _secret()  # sem chave configurada na Vercel não dá para guardar o login
        _migrate_file_tokens()
        has_tokens = bool(_get_secret(TOKENS_KEY))
        key_ok, key_error = True, None
    except GarminUnavailable as e:
        has_tokens, key_ok, key_error = False, False, str(e)
    st = sync_state()
    return {
        "library_installed": lib_ok,
        "has_tokens": has_tokens,
        "connected": _client is not None or has_tokens,
        "mfa_pending": _mfa_pending() if key_ok else False,
        "display_name": getattr(_client, "display_name", None) if _client else st.get("display_name"),
        "storage_ok": key_ok,
        "storage_error": key_error,
        "needs_reconnect": bool(st.get("needs_reconnect")) and not has_tokens,
        "sync": {k: st.get(k) for k in ("last_run", "last_ok", "last_imported", "last_error", "trigger", "last_cron")},
    }


def _save_tokens(api) -> None:
    """Grava os tokens atuais (podem ter sido renovados durante as chamadas)."""
    try:
        data = api.client.dumps()
    except Exception:  # noqa: BLE001
        return
    if data and data != _get_secret(TOKENS_KEY):
        _put_secret(TOKENS_KEY, data)


def connect_with_tokens() -> bool:
    """Reconecta com os tokens guardados, sem nunca tentar login por senha."""
    global _client
    with _lock:
        if _client is not None:
            return True
        _migrate_file_tokens()
        tokens = _get_secret(TOKENS_KEY)
        if not tokens:
            return False
        Garmin = _lib()
        api = Garmin()
        try:
            api.login(tokens)  # JSON inline: carrega e renova o token se estiver vencendo
        except Exception as e:  # noqa: BLE001 - refresh recusado → precisa entrar de novo
            _del(TOKENS_KEY)
            _set_sync_state(needs_reconnect=True, last_error=f"Login do Garmin expirou: entre de novo ({type(e).__name__}).")
            return False
        _save_tokens(api)
        _client = api
        _set_sync_state(needs_reconnect=False, display_name=getattr(api, "display_name", None))
        return True


def login(email: str, password: str) -> dict:
    """Login com e-mail e senha. Se a conta tiver MFA, retorna {'mfa_required': True}."""
    global _client
    Garmin = _lib()
    with _lock:
        api = Garmin(email=email, password=password, return_on_mfa=True)
        result, state = api.login()
        if result == "needs_mfa":
            _pending_mfa.clear()
            _pending_mfa.update({"api": api, "state": state})
            # Outra instância do servidor pode receber o código: guarda o estado (cifrado, 10 min)
            _put_secret(MFA_KEY, {"state": state, "started": datetime.now().isoformat()})
            return {"mfa_required": True}
        return _finish_login(api)


def _finish_login(api) -> dict:
    global _client
    _pending_mfa.clear()
    _del(MFA_KEY)
    _save_tokens(api)
    _client = api
    _set_sync_state(needs_reconnect=False, last_error=None, display_name=getattr(api, "display_name", None))
    return {"connected": True, "display_name": getattr(api, "display_name", None)}


def submit_mfa(code: str) -> dict:
    with _lock:
        if _pending_mfa:
            api, state = _pending_mfa["api"], _pending_mfa["state"]
        else:
            raw = _get_secret(MFA_KEY) if _mfa_pending() else None
            if not raw:
                raise RuntimeError("Nenhum login aguardando código MFA (ou o código expirou). Entre de novo.")
            state = json.loads(raw)["state"]
            api = _lib()(return_on_mfa=True)
        api.resume_login(state, code.strip())
        return _finish_login(api)


def logout() -> None:
    global _client
    with _lock:
        _client = None
        _pending_mfa.clear()
        _del(TOKENS_KEY)
        _del(MFA_KEY)
        tdir = token_dir()
        if tdir.exists():
            for f in tdir.iterdir():
                f.unlink()
        _set_sync_state(needs_reconnect=False, display_name=None)


def client():
    if _client is None and not connect_with_tokens():
        raise GarminNeedsLogin("Não conectado ao Garmin Connect. Entre em Perfil → Garmin.")
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
        item = endurance_item(activity, hr_max)
        if item:
            session["items"].append(item)
    return session


# ambiente de cada tipo de atividade do Garmin (para escolher o item do catálogo)
TYPE_ENV = {"treadmill_running": "esteira", "indoor_running": "esteira", "trail_running": "trilha",
            "track_running": "pista", "indoor_cycling": "rolo", "virtual_ride": "rolo", "mountain_biking": "mtb",
            "open_water_swimming": "aguas_abertas", "lap_swimming": "piscina", "indoor_rowing": "indoor",
            "rowing": "agua", "treadmill_walking": "esteira", "indoor_walking": "esteira"}
GYM_MACHINES = {"elliptical": "cardio_eliptico", "stair_climbing": "cardio_escada", "stairs": "cardio_escada",
                "indoor_cardio": "cardio_eliptico"}


def _zone_from_hr(avg, hr_max):
    if not avg or not hr_max:
        return 2
    pct = avg / hr_max
    return 1 if pct < 0.6 else 2 if pct < 0.7 else 3 if pct < 0.8 else 4 if pct < 0.9 else 5


def endurance_item(activity: dict, hr_max: int | None = None) -> dict | None:
    """Atividade de resistência do Garmin → item de sessão (com distância, FC, potência, TSS)."""
    from . import sports as sp

    type_key = ((activity.get("activityType") or {}).get("typeKey") or "").lower()
    avg = activity.get("averageHR")
    base = {"duration_min": round((activity.get("duration") or 0) / 60, 1), "zone": _zone_from_hr(avg, hr_max)}
    for k_src, k in (("averageHR", "avg_hr"), ("maxHR", "max_hr"), ("calories", "kcal")):
        if activity.get(k_src):
            base[k] = activity[k_src]
    sport = sp.GARMIN_TYPE_TO_SPORT.get(type_key)
    if sport:
        env, ex_id = sp.sport_env(sport, TYPE_ENV.get(type_key))
        item = {"exercise_id": ex_id, "sport": sport, **base}
        if activity.get("distance"):
            item["distance_km"] = round(float(activity["distance"]) / 1000, 3)
        for k_src, k in (("avgPower", "avg_power"), ("averagePower", "avg_power"), ("normPower", "np"),
                         ("normalizedPower", "np"), ("trainingStressScore", "tss"), ("elevationGain", "elev_m"),
                         ("aerobicTrainingEffect", "te_aerobic"), ("anaerobicTrainingEffect", "te_anaerobic")):
            if activity.get(k_src) and k not in item:
                item[k] = round(float(activity[k_src]), 1)
        if activity.get("poolLength"):
            item["pool_m"] = activity["poolLength"]
        if item.get("distance_km") and item["duration_min"]:
            secs = item["duration_min"] * 60
            if sport in ("corrida", "caminhada"):
                item["pace_s_km"] = round(secs / item["distance_km"])
            elif sport == "natacao":
                item["pace_s_100"] = round(secs / (item["distance_km"] * 10), 1)
            elif sport == "remo":
                item["split_s_500"] = round(secs / (item["distance_km"] * 2), 1)
            else:
                item["speed_kmh"] = round(item["distance_km"] / (secs / 3600), 1)
        return item
    machine = GYM_MACHINES.get(type_key)
    if machine:
        return {"exercise_id": machine, **base}
    return None


def recent_activities(days: int = 14) -> list[dict]:
    api = client()
    start = (date.today() - timedelta(days=days)).isoformat()
    acts = api.get_activities_by_date(start, date.today().isoformat())
    return [{
        "activity_id": a.get("activityId"), "name": a.get("activityName"),
        "type": (a.get("activityType") or {}).get("typeKey"), "start": a.get("startTimeLocal"),
        "duration_min": round((a.get("duration") or 0) / 60), "avg_hr": a.get("averageHR"),
        "calories": a.get("calories"), "distance_km": round((a.get("distance") or 0) / 1000, 2) or None,
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
    }
    for k in ("duration", "averageHR", "maxHR", "calories", "distance", "averagePower", "normalizedPower", "avgPower",
              "normPower", "trainingStressScore", "elevationGain", "aerobicTrainingEffect", "anaerobicTrainingEffect",
              "poolLength", "averageSpeed"):
        v = summary.get(k) if summary.get(k) is not None else act.get(k)
        if v is not None:
            flat[k] = v
    sets = None
    if "strength" in str((flat["activityType"] or {}).get("typeKey", "")):
        sets = api.get_activity_exercise_sets(activity_id)
    return activity_to_session(flat, sets, preferred_ids, hr_max)


# ---------------------------------------------------------------------------
# Sincronização automática (cron diário + ao abrir o app)
# ---------------------------------------------------------------------------

AUTO_MIN_HOURS = 6     # ao abrir o app, só sincroniza se a última tiver mais que isso
MAX_PER_RUN = 15       # limite de importações por execução (gentil com a API)
FIRST_SYNC_DAYS = 14


def _is_rate_limit(e: Exception) -> bool:
    txt = f"{type(e).__name__} {e}".lower()
    return "429" in txt or "too many" in txt or "toomanyrequests" in txt


def due(min_hours: float = AUTO_MIN_HOURS, now: datetime | None = None) -> bool:
    st = sync_state()
    if st.get("needs_reconnect"):
        return False
    last = st.get("last_run")
    if not last:
        return True
    try:
        return (now or datetime.now()) - datetime.fromisoformat(last) >= timedelta(hours=min_hours)
    except ValueError:
        return True


def sync(save_session, imported_ids: set, preferred_ids: set[str] | None = None, hr_max: int | None = None,
         trigger: str = "manual", now: datetime | None = None) -> dict:
    """Importa as atividades novas do Garmin como sessões.

    `save_session` grava a sessão (db.save_session); `imported_ids` são os IDs já
    importados. Só lê dados com os tokens guardados: se não houver login válido,
    marca "reconectar" e não tenta entrar por senha.
    """
    now = now or datetime.now()
    st = sync_state()
    started = now.isoformat(timespec="seconds")
    try:
        api = client()
    except GarminNeedsLogin as e:
        _set_sync_state(last_run=started, last_ok=False, last_error=str(e), trigger=trigger,
                        needs_reconnect=bool(st.get("display_name")) or bool(st.get("needs_reconnect")))
        return {"ok": False, "needs_login": True, "error": str(e), "imported": []}

    # Janela: desde a última sincronização boa (com 2 dias de folga), no máximo 30 dias
    days = FIRST_SYNC_DAYS
    if st.get("last_ok_at"):
        try:
            days = min(30, max(2, (now - datetime.fromisoformat(st["last_ok_at"])).days + 2))
        except ValueError:
            pass
    imported, skipped, errors = [], 0, []
    try:
        acts = recent_activities(days)
        new = [a for a in acts if a.get("activity_id") and str(a["activity_id"]) not in imported_ids]
        new.sort(key=lambda a: str(a.get("start") or ""))
        for a in new[:MAX_PER_RUN]:
            try:
                session = import_activity(a["activity_id"], preferred_ids, hr_max)
            except Exception as e:  # noqa: BLE001
                if _is_rate_limit(e):
                    raise
                errors.append(f"{a.get('name') or a['activity_id']}: {type(e).__name__}")
                continue
            if not session.get("items"):
                skipped += 1  # corrida na rua, pedal etc.: o app só registra academia
                continue
            try:
                saved = save_session(session)
            except ValueError:
                continue  # já importada por outra execução
            imported.append({"session_id": saved.get("id"), "name": a.get("name"), "date": str(a.get("start"))[:10],
                             "type": a.get("type")})
        _save_tokens(api)
    except Exception as e:  # noqa: BLE001
        msg = ("O Garmin limitou as requisições agora; tento de novo na próxima sincronização."
               if _is_rate_limit(e) else f"Falha ao sincronizar: {type(e).__name__}: {e}")
        _set_sync_state(last_run=started, last_ok=False, last_error=msg, trigger=trigger)
        return {"ok": False, "error": msg, "imported": imported}

    _set_sync_state(last_run=started, last_ok=True, last_ok_at=started, last_error="; ".join(errors) or None,
                    trigger=trigger, last_imported=imported[-10:] if imported else [], needs_reconnect=False)
    return {"ok": True, "imported": imported, "skipped": skipped, "errors": errors, "window_days": days,
            "pending": max(0, len(new) - MAX_PER_RUN)}
