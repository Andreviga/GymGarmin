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
import re
import time
import os
import secrets
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import catalog, clock

_lock = threading.Lock()
# Cliente do Garmin e login aguardando MFA, por usuário do app (cada pessoa tem sua conta Garmin)
_clients: dict[int, Any] = {}
_pending_mfa: dict[int, dict] = {}


def _uid() -> int:
    from .db import current_user

    return current_user()


def current():
    """Cliente do Garmin já conectado do usuário atual (ou None)."""
    return _clients.get(_uid())


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


class GarminTemporary(RuntimeError):
    """O Garmin não respondeu agora (fora do ar, limite, rede). Os tokens continuam guardados."""


# O garminconnect transforma qualquer falha ao renovar o token (inclusive rede/Garmin fora do ar)
# em "Username and password are required". Por isso só pedimos para entrar de novo quando a
# recusa se repete em execuções separadas por algumas horas.
AUTH_FAIL_CONFIRM = 2
AUTH_FAIL_MIN_HOURS = 6


def _is_auth_error(e: Exception) -> bool:
    txt = f"{type(e).__name__} {e}".lower()
    return "authentication" in txt or "401" in txt or "403" in txt or "unauthorized" in txt or "password" in txt


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
    if _uid() != 1:
        return  # arquivo da versão local de um usuário só
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
    if _pending_mfa.get(_uid()):
        return True
    raw = _get_secret(MFA_KEY)
    if not raw:
        return False
    try:
        started = datetime.fromisoformat(json.loads(raw).get("started"))
    except (ValueError, TypeError):
        return False
    return clock.now() - started < timedelta(minutes=MFA_TTL_MIN)


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
        "connected": current() is not None or has_tokens,
        "mfa_pending": _mfa_pending() if key_ok else False,
        "display_name": getattr(current(), "display_name", None) if current() else st.get("display_name"),
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
    with _lock:
        if current() is not None:
            return True
        _migrate_file_tokens()
        tokens = _get_secret(TOKENS_KEY)
        if not tokens:
            return False
        Garmin = _lib()
        api = Garmin()
        try:
            api.login(tokens)  # JSON inline: carrega e renova o token se estiver vencendo
        except Exception as e:  # noqa: BLE001
            st = sync_state()
            if _is_auth_error(e):
                n = int(st.get("auth_failures") or 0) + 1
                since = st.get("auth_failed_since") or clock.now().isoformat(timespec="seconds")
                try:
                    waited = clock.now() - datetime.fromisoformat(since) >= timedelta(hours=AUTH_FAIL_MIN_HOURS)
                except ValueError:
                    waited = True
                if n >= AUTH_FAIL_CONFIRM and waited:  # recusa confirmada → precisa entrar de novo
                    _del(TOKENS_KEY)
                    _set_sync_state(needs_reconnect=True, auth_failures=0, auth_failed_since=None,
                                    last_error=f"Login do Garmin expirou: entre de novo ({type(e).__name__}).")
                    return False
                _set_sync_state(auth_failures=n, auth_failed_since=since)
            raise GarminTemporary(f"O Garmin não respondeu agora ({type(e).__name__}); tento de novo mais tarde.") from e
        _save_tokens(api)
        _clients[_uid()] = api
        _set_sync_state(needs_reconnect=False, auth_failures=0, auth_failed_since=None,
                        display_name=getattr(api, "display_name", None))
        return True


def login(email: str, password: str) -> dict:
    """Login com e-mail e senha. Se a conta tiver MFA, retorna {'mfa_required': True}."""
    Garmin = _lib()
    with _lock:
        api = Garmin(email=email, password=password, return_on_mfa=True)
        result, state = api.login()
        if result == "needs_mfa":
            _pending_mfa[_uid()] = {"api": api, "state": state}
            # Outra instância do servidor pode receber o código: guarda o estado (cifrado, 10 min)
            _put_secret(MFA_KEY, {"state": state, "started": clock.now().isoformat()})
            return {"mfa_required": True}
        return _finish_login(api)


def _finish_login(api) -> dict:
    _pending_mfa.pop(_uid(), None)
    _del(MFA_KEY)
    _save_tokens(api)
    _clients[_uid()] = api
    _set_sync_state(needs_reconnect=False, last_error=None, auth_failures=0, auth_failed_since=None,
                    display_name=getattr(api, "display_name", None))
    return {"connected": True, "display_name": getattr(api, "display_name", None)}


def submit_mfa(code: str) -> dict:
    with _lock:
        mine = _pending_mfa.get(_uid())
        if mine:
            api, state = mine["api"], mine["state"]
        else:
            raw = _get_secret(MFA_KEY) if _mfa_pending() else None
            if not raw:
                raise RuntimeError("Nenhum login aguardando código MFA (ou o código expirou). Entre de novo.")
            state = json.loads(raw)["state"]
            api = _lib()(return_on_mfa=True)
        api.resume_login(state, code.strip())
        return _finish_login(api)


def logout() -> None:
    with _lock:
        _clients.pop(_uid(), None)
        _pending_mfa.pop(_uid(), None)
        _del(TOKENS_KEY)
        _del(MFA_KEY)
        tdir = token_dir()
        if tdir.exists() and _uid() == 1:
            for f in tdir.iterdir():
                f.unlink()
        _set_sync_state(needs_reconnect=False, display_name=None)


def persist() -> None:
    """Grava o token se ele se renovou durante chamadas fora da sincronização (prontidão, métricas, envio)."""
    c = current()
    if c is not None:
        try:
            _save_tokens(c)
        except Exception:  # noqa: BLE001
            pass


def client():
    if current() is None and not connect_with_tokens():
        raise GarminNeedsLogin("Não conectado ao Garmin Connect. Entre em Perfil → Garmin.")
    return current()


def push_workout(workout_json: dict, schedule_date: str | None = None) -> dict:
    """Envia o treino ao Garmin Connect e (opcional) agenda no calendário."""
    api = client()
    try:
        created = api.upload_workout(workout_json)
    finally:
        persist()
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


# Quando o relógio detecta só a categoria (o nome do exercício vem vazio, o comum na
# detecção automática), usa o exercício mais comum da categoria, não o primeiro do catálogo.
CATEGORY_DEFAULT = {
    "BENCH_PRESS": "Barbell_Bench_Press_-_Medium_Grip", "LATERAL_RAISE": "Side_Lateral_Raise",
    "TRICEPS_EXTENSION": "Triceps_Pushdown", "ROW": "Seated_Cable_Rows", "PULL_UP": "Wide-Grip_Lat_Pulldown",
    "CURL": "Dumbbell_Bicep_Curl", "SQUAT": "Barbell_Squat", "DEADLIFT": "Barbell_Deadlift",
    "SHOULDER_PRESS": "Dumbbell_Shoulder_Press", "LUNGE": "Dumbbell_Lunges", "FLYE": "Dumbbell_Flyes",
    "LEG_CURL": "Lying_Leg_Curls", "CALF_RAISE": "Standing_Calf_Raises", "CRUNCH": "Crunches", "SIT_UP": "Sit-Up",
    "PLANK": "Plank", "HIP_RAISE": "Barbell_Hip_Thrust", "SHRUG": "Barbell_Shrug", "PUSH_UP": "Pushups",
    "LEG_RAISE": "Hanging_Leg_Raise", "CORE": "Ab_Roller", "HYPEREXTENSION": "Hyperextensions_Back_Extensions",
    "CARRY": "Farmers_Walk", "OLYMPIC_LIFT": "Power_Clean", "HIP_SWING": "One-Arm_Kettlebell_Swings",
    "PLYO": "Box_Jump_Multiple_Response", "HIP_STABILITY": "Thigh_Abductor",
}
UNKNOWN_ID = "Garmin_desconhecido"
UNKNOWN_NAME = "Exercício não identificado pelo relógio"
WATCH_ONLY_IDS = {UNKNOWN_ID, "Garmin_musculacao"}  # sem exercício real: fora do histórico de cargas


def _reverse_map(preferred: set[str] | None = None) -> dict[tuple[str, str], str]:
    """(categoria, exercício do Garmin) → exercício do app; (categoria, None) → o mais provável da categoria.

    Prioridade: o que está no plano do usuário > o padrão da categoria > o primeiro popular > o primeiro."""
    preferred = preferred or set()
    defaults = set(CATEGORY_DEFAULT.values())

    def rank(ex):
        return (ex["id"] in preferred, ex["id"] in defaults, bool(ex.get("popular")))

    best: dict[tuple[str, str], dict] = {}
    for ex in catalog.all_exercises():
        g = ex.get("garmin") or {}
        if not g.get("category"):
            continue
        if ex.get("rotation") is False and ex["id"] not in preferred:
            continue  # aparelho raro "por escolha": o afundo com barra do relógio não vira "afundo na máquina" sozinho
        keys = [(g["category"], None)] + ([(g["category"], g["exercise"])] if g.get("exercise") else [])
        for key in keys:
            cur = best.get(key)
            if cur is None or rank(ex) > rank(cur):
                best[key] = ex
    rev = {k: v["id"] for k, v in best.items()}
    for cat, eid in CATEGORY_DEFAULT.items():
        if rev.get((cat, None)) not in preferred:
            rev[(cat, None)] = eid
    return rev


def _legacy_reverse_map() -> dict[tuple[str, str], str]:
    """O mapa usado até a v1 (primeiro exercício do catálogo por categoria) — para corrigir importações antigas."""
    rev: dict[tuple[str, str], str] = {}
    for ex in catalog.all_exercises():
        g = ex.get("garmin") or {}
        key = (g.get("category"), g.get("exercise"))
        if key not in rev or ex["popular"]:
            rev[key] = ex["id"]
        rev.setdefault((g.get("category"), None), ex["id"])
    return rev


def remap_imported(sessions: list[dict], preferred_ids: set[str] | None = None) -> list[dict]:
    """Corrige sessões já importadas com o mapa antigo (ex.: supino → 'Alternating_Floor_Press').

    Só mexe em itens cujo exercício ainda é o palpite antigo da categoria (o usuário não trocou)."""
    old, rev = _legacy_reverse_map(), _reverse_map(preferred_ids)
    changed = []
    for s in sessions:
        if s.get("source") != "garmin":
            continue
        dirty = False
        for it in s.get("items") or []:
            g = it.get("garmin") or {}
            if not g or not it.get("sets") or it.get("labeled"):
                continue  # o que a pessoa já respondeu (categoria confirmada, bloco nomeado) não é remapeado
            cat, name = g.get("category"), g.get("name")
            if it.get("exercise_id") == UNKNOWN_ID:
                if not it.get("_name"):
                    it["_name"], dirty = UNKNOWN_NAME, True
                continue
            was = old.get((cat, name)) if name else None
            was = was or old.get((cat, None))
            now = rev.get((cat, name)) or rev.get((cat, None))
            if now and it.get("exercise_id") == was and now != was:
                it["exercise_id"], dirty = now, True
            if not name and not it.get("guess"):
                it["guess"], dirty = True, True
        if dirty:
            changed.append(s)
    return changed


def activity_to_session(activity: dict, exercise_sets: dict | None, preferred_ids: set[str] | None = None,
                        hr_max: int | None = None, cat_prefs: dict | None = None) -> dict:
    """Converte uma atividade do Garmin (com séries) em sessão do GymGarmin.

    `cat_prefs` = o que a pessoa já disse sobre cada categoria do relógio ("rosca" = rosca martelo):
    vale quando o relógio não informa o nome do exercício, e o item deixa de ser palpite."""
    prefs = cat_prefs or {}
    start = activity.get("startTimeLocal") or activity.get("startTimeGMT") or clock.now().isoformat()
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
            exact = rev.get((cat, name)) if name else None
            chosen = None if exact else prefs.get(cat)
            eid = exact or chosen or rev.get((cat, None)) or UNKNOWN_ID
            w = s.get("weight")
            load = (w / 1000.0 if w and w > 500 else w) or 0
            entry = {"reps": s.get("repetitionCount") or 0, "load_kg": round(load, 1),
                     "duration_s": round(s.get("duration") or 0)}
            if current and current["exercise_id"] == eid:
                current["sets"].append(entry)
            else:
                current = {"exercise_id": eid, "sets": [entry], "garmin": {"category": cat, "name": name}}
                if eid == UNKNOWN_ID:
                    current["_name"] = UNKNOWN_NAME
                elif chosen:
                    current["labeled"] = "categoria"  # a pessoa já confirmou o que essa categoria é para ela
                elif not name or (cat, name) not in rev:
                    current["guess"] = True  # o relógio só disse a categoria: confira e troque se precisar
                session["items"].append(current)
    else:
        item = endurance_item(activity, hr_max)
        if item:
            session["items"].append(item)
        elif _is_strength(activity):
            # o relógio gravou musculação sem detectar as séries: conta tempo e FC (carga por sRPE)
            mins = round((activity.get("duration") or 0) / 60, 1)
            if mins >= 5:
                it = {"exercise_id": STRENGTH_GENERIC, "_name": "Musculação (relógio, sem séries detectadas)",
                      "strength_min": mins, "duration_min": mins,
                      "zone": _zone_from_hr(activity.get("averageHR"), hr_max)}
                for k_src, k in (("averageHR", "avg_hr"), ("maxHR", "max_hr"), ("calories", "kcal")):
                    if activity.get(k_src):
                        it[k] = activity[k_src]
                session["items"].append(it)
    if session["items"]:
        summ = {k: activity.get(src) for src, k in (("averageHR", "avg_hr"), ("maxHR", "max_hr"), ("calories", "kcal"))
                if activity.get(src)}
        if activity.get("duration"):
            summ["duration_min"] = round(activity["duration"] / 60, 1)
            session["duration_min"] = summ["duration_min"]
        session["garmin_summary"] = summ
    return session


STRENGTH_GENERIC = "Garmin_musculacao"


def _is_strength(activity: dict) -> bool:
    return "strength" in ((activity.get("activityType") or {}).get("typeKey") or "").lower()


def importable_type(type_key: str | None) -> bool:
    """Tipos que o app registra: modalidades, musculação, aparelhos de cardio e as outras atividades
    (futebol, yoga, tênis…) como atividade genérica. Só fica de fora o que não é treino (meditação, dirigir…)."""
    from . import sports as sp

    t = (type_key or "").lower()
    return t in sp.GARMIN_TYPE_TO_SPORT or t in GYM_MACHINES or "strength" in t or activity_kind(t) is not None


def needs_detail(type_key: str | None) -> bool:
    """Musculação (séries) e modalidades (potência, TSS, elevação) pedem o detalhe da atividade;
    aparelho de cardio e atividade genérica entram só com o resumo da lista (uma chamada a menos por atividade)."""
    from . import sports as sp

    t = (type_key or "").lower()
    return t in sp.GARMIN_TYPE_TO_SPORT or "strength" in t


# Outras atividades do relógio → atividade genérica do catálogo (tempo, FC e calorias contam na carga da semana).
# A ordem importa: a primeira regra que casa vence. Palavras em inglês (Garmin, Apple, Health Connect) e português.
NOT_WORKOUT = ("breath", "meditat", "meditaç", "respira", "motor", "motocross", "driving", "racing", "flying", "gaming", "navigat",
               "transition", "incident", "multi_sport", "multisport", "sleep", "hunting", "fishing", "pesca", "mind",
               "snowmobil", "overland", "drone", "sky_div", "skydiv", "parachut", "paraglid", "hang_glid", "wingsuit")
# palavras curtas só valem inteiras ("esport" pegava "Esportes Aquáticos"; "nap" pegaria outras)
NOT_WORKOUT_WORDS = re.compile(r"(^|[^a-z])(e[_ -]?sports?|nap|atv|utv)($|[^a-z])")
ACTIVITY_RULES = [
    (("yoga", "ioga", "pilates", "stretch", "along", "mobili", "flexib", "barre", "tai_chi", "tai chi", "taichi", "cooldown", "cool_down",
      "desaquec", "recovery", "recupera"), "atividade_mobilidade"),
    (("soccer", "football", "futebol", "futsal", "basket", "basquet", "volley", "volei", "vôlei", "handball", "handebol", "rugby",
      "hockey", "lacrosse", "baseball", "softball", "cricket", "ultimate", "team"), "atividade_coletivo"),
    (("tennis", "tenis", "tênis", "padel", "paddel", "pickleball", "squash", "racquet", "racket", "raquete", "badminton"), "atividade_raquete"),
    (("boxing", "boxe", "martial", "marcia", "mma", "wrestl", "judo", "judô", "karat", "caratê", "jiu", "kickbox", "muay", "fight", "combat",
      "luta", "capoeira", "fencing", "esgrima", "taekwon"), "atividade_luta"),
    (("danc", "dança", "zumba"), "atividade_danca"),
    (("rock_climb", "indoor_climb", "climbing", "boulder", "escalada"), "atividade_escalada"),
    (("surf", "paddl", "kayak", "caiaque", "canoe", "canoa", "sail", "vela", "kite", "wake", "raft", "snorkel", "diving",
      "mergulho", "boating", "water_sport", "water sport", "waterski", "water_ski", "aquatic", "aquátic"), "atividade_aquatica"),
    (("ski", "esqui", "snowboard", "snow", "skat", "patin"), "atividade_neve_patins"),
    (("hiit", "bootcamp", "cardio", "aerob", "aeróbic", "fitness_equipment", "circuit", "interval", "tabata", "step_"), "atividade_hiit"),
]
ACTIVITY_NAMES_PT = {
    "yoga": "Yoga", "pilates": "Pilates", "soccer": "Futebol", "american_football": "Futebol americano", "basketball": "Basquete",
    "volleyball": "Vôlei", "beach_volleyball": "Vôlei de praia", "handball": "Handebol", "rugby": "Rúgbi", "tennis": "Tênis",
    "tennis_v2": "Tênis", "padel": "Padel", "paddelball": "Padel", "pickleball": "Pickleball", "squash": "Squash",
    "badminton": "Badminton", "table_tennis": "Tênis de mesa", "boxing": "Boxe", "mixed_martial_arts": "Artes marciais",
    "dance": "Dança", "rock_climbing": "Escalada", "indoor_climbing": "Escalada indoor", "bouldering": "Boulder",
    "surfing": "Surfe", "stand_up_paddleboarding": "Stand up paddle", "kayaking": "Caiaque", "kayaking_v2": "Caiaque",
    "hiit": "HIIT", "indoor_cardio": "Cardio", "skating": "Patinação", "inline_skating": "Patins", "golf": "Golfe",
    "resort_skiing_snowboarding_ws": "Esqui / snowboard", "other": "Outra atividade",
}
ACTIVITY_LIGHT_TSS_H = 15  # yoga, pilates, alongamento: carga fixa e baixa por hora (a FC mal sai do repouso)


def activity_kind(type_key: str | None) -> str | None:
    """Tipo do relógio → atividade genérica do catálogo; None quando não é treino (meditação, dirigir…) ou não há tipo."""
    t = (type_key or "").lower().strip()
    if not t or any(k in t for k in NOT_WORKOUT) or NOT_WORKOUT_WORDS.search(t):
        return None
    for keys, act in ACTIVITY_RULES:
        if any(k in t for k in keys):
            return act
    return "atividade_outra"


# ambiente de cada tipo de atividade do Garmin (para escolher o item do catálogo)
TYPE_ENV = {"treadmill_running": "esteira", "indoor_running": "esteira", "trail_running": "trilha",
            "track_running": "pista", "indoor_cycling": "rolo", "virtual_ride": "rolo", "mountain_biking": "mtb",
            "open_water_swimming": "aguas_abertas", "lap_swimming": "piscina", "indoor_rowing": "indoor",
            "rowing": "agua", "treadmill_walking": "esteira", "indoor_walking": "esteira"}
GYM_MACHINES = {"elliptical": "cardio_eliptico", "stair_climbing": "cardio_escada", "stairs": "cardio_escada",
                "floor_climbing": "cardio_escada", "jump_rope": "cardio_corda", "jumprope": "cardio_corda"}


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
        _steps_into(item, activity)
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
        return _steps_into({"exercise_id": machine, **base}, activity)
    if "strength" in type_key:
        return None  # musculação: tratada por quem chama (séries ou tempo de treino)
    act = activity_kind(type_key)
    if act:
        item = {"exercise_id": act, **base, "activity_type": type_key}
        if not avg:
            item["zone"] = 1
        label = ACTIVITY_NAMES_PT.get(type_key)
        if label:
            item["_name"] = label
        if activity.get("distance"):
            item["distance_km"] = round(float(activity["distance"]) / 1000, 3)
        if act == "atividade_mobilidade":  # leve: não fecha cardio da agenda nem conta como treino intenso
            item.update(zone=1, light=True, tss=round(base["duration_min"] / 60 * ACTIVITY_LIGHT_TSS_H, 1))
        return _steps_into(item, activity)
    return None


def _steps_into(item: dict, activity: dict) -> dict:
    """Passos e cadência medidos na atividade (o total do dia do relógio inclui estes passos)."""
    try:
        if activity.get("steps") and float(activity["steps"]) > 0:
            item["steps"] = int(float(activity["steps"]))
        cad = activity.get("averageRunCadence") or activity.get("averageRunningCadenceInStepsPerMinute")
        if cad and 40 <= float(cad) <= 260:
            item["cadence"] = round(float(cad))
    except (TypeError, ValueError):
        pass
    return item


def recent_activities(days: int = 14) -> list[dict]:
    api = client()
    start = (clock.today() - timedelta(days=days)).isoformat()
    acts = api.get_activities_by_date(start, clock.today().isoformat())
    return [{
        "activity_id": a.get("activityId"), "name": a.get("activityName"),
        "type": (a.get("activityType") or {}).get("typeKey"), "start": a.get("startTimeLocal"),
        "duration_min": round((a.get("duration") or 0) / 60), "avg_hr": a.get("averageHR"),
        "calories": a.get("calories"), "distance_km": round((a.get("distance") or 0) / 1000, 2) or None,
        "steps": a.get("steps"), "cadence": a.get("averageRunningCadenceInStepsPerMinute"),
        "max_hr": a.get("maxHR"), "duration_s": a.get("duration"), "distance_m": a.get("distance"),
    } for a in acts or []]


def session_from_summary(a: dict, hr_max: int | None = None) -> dict:
    """Sessão montada só com o resumo da lista de atividades (sem baixar o detalhe)."""
    flat = {"activityId": a.get("activity_id"), "activityName": a.get("name"), "activityType": {"typeKey": a.get("type")},
            "startTimeLocal": a.get("start"), "duration": a.get("duration_s") or (a.get("duration_min") or 0) * 60,
            "averageHR": a.get("avg_hr"), "maxHR": a.get("max_hr"), "calories": a.get("calories"),
            "distance": a.get("distance_m"), "steps": a.get("steps")}
    return activity_to_session({k: v for k, v in flat.items() if v is not None}, None, None, hr_max)


def import_activity(activity_id: int, preferred_ids: set[str] | None = None, hr_max: int | None = None,
                    cat_prefs: dict | None = None) -> dict:
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
              "poolLength", "averageSpeed", "steps", "averageRunCadence"):
        v = summary.get(k) if summary.get(k) is not None else act.get(k)
        if v is not None:
            flat[k] = v
    sets = None
    if "strength" in str((flat["activityType"] or {}).get("typeKey", "")):
        sets = api.get_activity_exercise_sets(activity_id)
    return activity_to_session(flat, sets, preferred_ids, hr_max, cat_prefs)


# ---------------------------------------------------------------------------
# Sincronização automática (cron diário + ao abrir o app)
# ---------------------------------------------------------------------------

AUTO_MIN_HOURS = 1     # ao abrir o app, só sincroniza se a última tiver mais que isso (20 min com treino de hoje aberto)
MAX_PER_RUN = 15       # limite de importações por execução (gentil com a API)
FIRST_SYNC_DAYS = 42   # 6 semanas: o condicionamento (CTL) precisa do passado para não começar do zero
LOOKBACK_DAYS = 14     # o relógio pode passar dias sem sincronizar: sempre olha 2 semanas para trás
MAX_ATTEMPTS = 3       # atividade que falha 3 vezes é deixada de lado
SKIP_KEY = "garmin_skipped"
MIN_ACTIVITY_MIN = 3   # atividade de menos de 3 min sem séries: toque acidental no relógio


def _is_strength_session(session: dict) -> bool:
    return any(it.get("sets") for it in session.get("items") or [])


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
        gap = (now or clock.now()) - datetime.fromisoformat(last)
        if st.get("pending") and gap >= timedelta(minutes=1):
            return True  # a última parou no limite/prazo com atividades na fila: continua já
        return gap >= timedelta(hours=min_hours)
    except ValueError:
        return True


def _skipped() -> dict:
    from . import db

    try:
        return json.loads(db.kv_get(SKIP_KEY) or "{}")
    except ValueError:
        return {}


def forget_activity(activity_id) -> None:
    """A pessoa apagou no app um treino que veio do relógio: a sincronização não o traz de volta."""
    if not activity_id:
        return
    d = _skipped()
    d[str(activity_id)] = {"final": True, "why": "apagada"}
    _save_skipped(d)


def _save_skipped(d: dict) -> None:
    from . import db

    if len(d) > 600:  # guarda só os mais recentes
        d = dict(list(d.items())[-500:])
    db.kv_set(SKIP_KEY, json.dumps(d))


def sync(save_session, imported_ids: set, preferred_ids: set[str] | None = None, hr_max: int | None = None,
         trigger: str = "manual", now: datetime | None = None, merge=None, until: float | None = None,
         cat_prefs: dict | None = None) -> dict:
    """Importa as atividades novas do Garmin como sessões.

    `save_session` grava a sessão (db.save_session); `imported_ids` são os IDs já
    importados; `merge(session)` (opcional) junta a atividade a um treino já registrado
    no app no mesmo dia e devolve a sessão gravada (ou None se não há par).
    `until` (time.monotonic) é o prazo: ao passar dele para de importar e o resto fica
    para a próxima vez (na Vercel a função é cortada aos 30 s, sem gravar nada).
    Só lê dados com os tokens guardados: se não houver login válido, marca
    "reconectar" e não tenta entrar por senha.
    """
    now = now or clock.now()
    st = sync_state()
    started = now.isoformat(timespec="seconds")
    try:
        api = client()
    except GarminTemporary as e:
        _set_sync_state(last_run=started, last_ok=False, last_error=str(e), trigger=trigger)
        return {"ok": False, "temporary": True, "error": str(e), "imported": []}
    except GarminNeedsLogin as e:
        _set_sync_state(last_run=started, last_ok=False, last_error=str(e), trigger=trigger,
                        needs_reconnect=bool(st.get("display_name")) or bool(st.get("needs_reconnect")))
        return {"ok": False, "needs_login": True, "error": str(e), "imported": []}

    days = LOOKBACK_DAYS if st.get("last_ok_at") else FIRST_SYNC_DAYS
    if st.get("last_ok_at") and not st.get("backfill_done"):
        days = FIRST_SYNC_DAYS  # quem conectou antes da busca de 6 semanas: busca o passado uma vez
    elif st.get("last_ok_at"):
        try:
            days = min(30, max(LOOKBACK_DAYS, (now - datetime.fromisoformat(st["last_ok_at"])).days + 2))
        except ValueError:
            pass
    imported, skipped, errors, new = [], 0, [], []
    skip = _skipped()
    skip_changed = False
    act_steps: dict = {}
    try:
        acts = recent_activities(days)
        for a in acts:
            aid = str(a.get("activity_id") or "")
            if aid and a.get("steps"):
                act_steps[aid] = {"steps": a["steps"], "cadence": a.get("cadence")}
            sk = skip.get(aid) or {}
            by_type = sk.get("final") and "attempts" not in sk and sk.get("why") == a.get("type")  # descartada pelo tipo
            if not aid or aid in imported_ids or (sk.get("final") and not (by_type and importable_type(a.get("type")))):
                continue
            if not importable_type(a.get("type")):
                skip[aid] = {"final": True, "why": a.get("type")}  # meditação, dirigir…: não é treino, nem baixa o detalhe
                skip_changed = True
                skipped += 1
                continue
            new.append(a)
        new.sort(key=lambda a: str(a.get("start") or ""))
        done = 0
        for a in new[:MAX_PER_RUN]:
            if until is not None and done and time.monotonic() > until:
                break  # sem tempo: o resto entra na próxima sincronização
            done += 1
            aid = str(a["activity_id"])
            try:
                session = (import_activity(a["activity_id"], preferred_ids, hr_max, cat_prefs) if needs_detail(a.get("type"))
                           else session_from_summary(a, hr_max))
            except Exception as e:  # noqa: BLE001
                if _is_rate_limit(e) or _is_auth_error(e) or "connection" in type(e).__name__.lower():
                    raise
                n = int((skip.get(aid) or {}).get("attempts") or 0) + 1
                skip[aid] = {"attempts": n, "final": n >= MAX_ATTEMPTS, "why": type(e).__name__}
                skip_changed = True
                errors.append(f"{a.get('name') or aid}: {type(e).__name__}")
                continue
            if not session.get("items") or (session.get("duration_min") or 0) < MIN_ACTIVITY_MIN and not _is_strength_session(session):
                skip[aid] = {"final": True, "why": "sem dados"}
                skip_changed = True
                skipped += 1
                continue
            try:
                saved = (merge(session) if merge else None) or save_session(session)
            except ValueError:
                continue  # já importada por outra execução
            imported.append({"session_id": saved.get("id"), "name": a.get("name"), "date": str(a.get("start"))[:10],
                             "type": a.get("type"), "merged": bool(saved.get("merged"))})
    except Exception as e:  # noqa: BLE001
        if _is_auth_error(e):
            _clients.pop(_uid(), None)  # na próxima, reconecta com os tokens guardados (e confirma se a recusa persiste)
        msg = ("O Garmin limitou as requisições agora; tento de novo na próxima sincronização."
               if _is_rate_limit(e) else f"Falha ao sincronizar: {type(e).__name__}: {e}")
        _set_sync_state(last_run=started, last_ok=False, last_error=msg, trigger=trigger)
        return {"ok": False, "error": msg, "imported": imported}
    finally:
        _save_tokens(api)  # o token pode ter se renovado no meio: sem isso o próximo login falha
        if skip_changed:
            _save_skipped(skip)

    _set_sync_state(last_run=started, last_ok=True, last_ok_at=started, last_error="; ".join(errors) or None,
                    trigger=trigger, last_imported=imported[-10:] if imported else [], needs_reconnect=False,
                    pending=max(0, len(new) - done), backfill_done=st.get("backfill_done") or (days >= FIRST_SYNC_DAYS and len(new) <= done))
    return {"ok": True, "imported": imported, "skipped": skipped, "errors": errors, "window_days": days,
            "pending": max(0, len(new) - done), "activity_steps": act_steps}
