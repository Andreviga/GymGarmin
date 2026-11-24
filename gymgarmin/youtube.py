"""YouTube Music: as mesmas playlists HAX (montadas com as músicas do Spotify, no BPM de cada momento) no YouTube.

O YouTube não deixa outro app dar play, mas as playlists aparecem no YouTube Music. O Google limita o uso gratuito
a 10.000 "unidades" por dia (buscar uma música custa 100 e pôr na playlist custa 50), então as playlists enchem aos
poucos — umas 50 músicas por dia, revezando entre as playlists — e o vídeo achado para cada música fica guardado.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
from datetime import datetime

from . import db, music

TOKENS_KEY = "youtube_tokens"
STATE_KEY = "youtube_state"
SCOPE = "https://www.googleapis.com/auth/youtube"
API = "https://www.googleapis.com/youtube/v3"
DAILY_UNITS = 9000          # margem dentro das 10.000 do Google
COST = {"search": 100, "insert": 50, "playlist": 50, "list": 1}


class YTError(RuntimeError):
    pass


def client_id() -> str | None:
    return (os.environ.get("GOOGLE_CLIENT_ID") or "").strip() or None


def _secret() -> str | None:
    return (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip() or None


def configured() -> bool:
    return bool(client_id() and _secret())


def redirect_uri(base: str) -> str:
    return base.rstrip("/") + "/google-callback"


def _tokens() -> dict | None:
    from .garmin_sync import _get_secret
    raw = _get_secret(TOKENS_KEY)
    return json.loads(raw) if raw else None


def _put_tokens(t: dict) -> None:
    from .garmin_sync import _put_secret
    _put_secret(TOKENS_KEY, t)


def connected() -> bool:
    return bool(_tokens())


def _form(d: dict) -> bytes:
    return urllib.parse.urlencode(d).encode()


def exchange(code: str, verifier: str, redirect: str) -> None:
    st, d = music._http("POST", "https://oauth2.googleapis.com/token", {"Content-Type": "application/x-www-form-urlencoded"},
                        _form({"code": code, "client_id": client_id() or "", "client_secret": _secret() or "", "redirect_uri": redirect,
                               "grant_type": "authorization_code", "code_verifier": verifier}))
    if st != 200 or not d or not d.get("access_token"):
        raise YTError(f"O Google recusou a conexão ({(d or {}).get('error_description') or (d or {}).get('error') or st}).")
    _put_tokens({"access": d["access_token"], "refresh": d.get("refresh_token"), "exp": time.time() + int(d.get("expires_in") or 3600) - 60})


def _access() -> str:
    t = _tokens()
    if not t:
        raise YTError("YouTube não conectado.")
    if time.time() < t.get("exp", 0):
        return t["access"]
    st, d = music._http("POST", "https://oauth2.googleapis.com/token", {"Content-Type": "application/x-www-form-urlencoded"},
                        _form({"grant_type": "refresh_token", "refresh_token": t.get("refresh") or "", "client_id": client_id() or "",
                               "client_secret": _secret() or ""}))
    if st != 200 or not d or not d.get("access_token"):
        raise YTError("A conexão com o YouTube expirou. Conecte de novo.")
    t.update(access=d["access_token"], exp=time.time() + int(d.get("expires_in") or 3600) - 60)
    _put_tokens(t)
    return t["access"]


def _call(method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict | None:
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    st, d = music._http(method, url, {"Authorization": f"Bearer {_access()}", "Content-Type": "application/json"},
                        json.dumps(body).encode() if body is not None else None)
    if st >= 400:
        errs = (((d or {}).get("error") or {}).get("errors") or [{}]) if isinstance(d, dict) else [{}]
        e = YTError(((d or {}).get("error") or {}).get("message") if isinstance(d, dict) else f"YouTube {st}")
        e.status, e.reason = st, errs[0].get("reason")  # type: ignore[attr-defined]
        raise e
    return d


def disconnect() -> None:
    from .garmin_sync import _del
    _del(TOKENS_KEY)


# ------------------------------------------------------------------ cota do dia ----

def _quota_day() -> str:
    """A cota do Google zera à meia-noite do horário do Pacífico."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
    except Exception:  # noqa: BLE001
        return time.strftime("%Y-%m-%d", time.gmtime(time.time() - 8 * 3600))


def _state() -> dict:
    st = json.loads(db.kv_get(STATE_KEY) or "{}")
    st.setdefault("playlists", {})
    st.setdefault("vids", {})
    st.setdefault("added", {})
    if (st.get("quota") or {}).get("day") != _quota_day():
        st["quota"] = {"day": _quota_day(), "used": 0, "blocked": False}
    return st


def _save(st: dict) -> None:
    db.kv_set(STATE_KEY, json.dumps(st))


def _spend(st: dict, what: str) -> bool:
    q = st["quota"]
    if q.get("blocked") or q["used"] + COST[what] > DAILY_UNITS:
        return False
    q["used"] += COST[what]
    return True


# ------------------------------------------------------------------ sincronizar ----

def _ensure_playlist(st: dict, mode: str) -> str | None:
    pid = st["playlists"].get(mode)
    if pid:
        return pid
    if not _spend(st, "playlist"):
        return None
    m = music.MODES[mode]
    d = _call("POST", "/playlists", {"part": "snippet,status"},
              {"snippet": {"title": f"HAX · {m['label']}", "description": f"Feita pelo app HAX: {m['desc']}. Suas músicas no BPM certo."},
               "status": {"privacyStatus": "private"}})
    pid = (d or {}).get("id")
    if pid:
        st["playlists"][mode] = pid
    return pid


def _find_video(st: dict, row: dict) -> str | None:
    if not _spend(st, "search"):
        raise YTError("cota")
    q = f"{row.get('name') or ''} {(row.get('artist') or '').split(',')[0]}".strip()
    d = _call("GET", "/search", {"part": "id", "q": q, "type": "video", "videoCategoryId": "10", "maxResults": 1})
    items = (d or {}).get("items") or []
    return ((items[0].get("id") or {}).get("videoId")) if items else None


def sync(budget_s: float = 8.0, cad: int | None = None, per_mode: int | None = None) -> dict:
    """Põe no YouTube as músicas que faltam em cada playlist HAX, revezando entre as playlists (todas enchem juntas)
    até acabar o tempo desta chamada ou a cota do dia."""
    st = _state()
    cad = cad or music.cadence_target()
    wanted = {m: [r for r in music.pick(m, cad)][: per_mode or music.MAX_PER_LIST] for m in music.MODES}
    t0 = time.monotonic()
    stop = None
    progressed = True
    try:
        while progressed and time.monotonic() - t0 < budget_s and not stop:
            progressed = False
            for mode, rows in wanted.items():
                done = set(st["added"].get(mode) or [])
                nxt = next((r for r in rows if r["id"] not in done and st["vids"].get(r["id"], "") is not None), None)
                if not nxt:
                    continue
                if time.monotonic() - t0 > budget_s:
                    break
                pid = _ensure_playlist(st, mode)
                if not pid:
                    stop = "cota"
                    break
                vid = st["vids"].get(nxt["id"], "")
                if vid == "":
                    vid = _find_video(st, nxt)
                    st["vids"][nxt["id"]] = vid           # None = não achou: não gasta de novo
                if vid:
                    if not _spend(st, "insert"):
                        stop = "cota"
                        break
                    try:
                        _call("POST", "/playlistItems", {"part": "snippet"},
                              {"snippet": {"playlistId": pid, "resourceId": {"kind": "youtube#video", "videoId": vid}}})
                    except YTError as e:
                        if getattr(e, "status", 0) == 404:   # a pessoa apagou a playlist no YouTube: cria de novo
                            st["playlists"].pop(mode, None)
                            st["added"].pop(mode, None)
                            continue
                        raise
                st["added"].setdefault(mode, []).append(nxt["id"])
                progressed = True
    except YTError as e:
        if str(e) == "cota" or getattr(e, "reason", "") in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
            st["quota"]["blocked"] = getattr(e, "reason", "") in ("quotaExceeded", "dailyLimitExceeded")
            stop = "cota"
        else:
            _save(st)
            raise
    _save(st)
    return {**summary(st, wanted, cad), "stopped": stop}


def summary(st: dict | None = None, wanted: dict | None = None, cad: int | None = None) -> dict:
    st = st or _state()
    if wanted is None:
        cad = cad or music.cadence_target()
        wanted = {m: music.pick(m, cad) for m in music.MODES} if connected() else {}
    pls = []
    pending = 0
    for mode, m in music.MODES.items():
        rows = wanted.get(mode) or []
        added = set(st["added"].get(mode) or [])
        done = sum(1 for r in rows if r["id"] in added and st["vids"].get(r["id"]))
        missing = sum(1 for r in rows if r["id"] not in added and st["vids"].get(r["id"], "") is not None)
        pending += missing
        pid = st["playlists"].get(mode)
        pls.append({"id": mode, "label": m["label"], "done": done, "total": len(rows),
                    "url": f"https://music.youtube.com/playlist?list={pid}" if pid else None})
    left = max(0, DAILY_UNITS - st["quota"]["used"]) if not st["quota"].get("blocked") else 0
    return {"configured": configured(), "connected": connected(), "playlists": pls, "pending": pending,
            "today_left": left // (COST["search"] + COST["insert"])}
