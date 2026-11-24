"""Login do Google e playlists no YouTube / YouTube Music.

Fluxo de login (só no domínio de produção, que é o redirect cadastrado no Google):
1. /api/google/login gera um "state" aleatório, guarda num cookie httpOnly (criptografado, 10 min) e manda a pessoa
   para o Google.
2. /google-callback confere o state, troca o código por tokens no servidor (o client secret nunca sai dele) e grava
   na tabela google_tokens: refresh token criptografado (mesma chave dos tokens do Garmin), access token, validade e escopo.
3. access_token() devolve um token válido, renovando quando faltar 60 s ou menos; invalid_grant = desconectado.

Cota do YouTube (do projeto no Google, para todos os usuários juntos): 10.000 unidades por dia; busca = 100,
criar playlist = 50, pôr vídeo na playlist = 50. Por isso a busca de vídeo fica em cache no banco (youtube_video_cache).
"""
from __future__ import annotations

import json
import os
import secrets
import time
import urllib.parse
from datetime import datetime

from . import db, music

PROD_HOST = "haxfit.vercel.app"
REDIRECT_URI = f"https://{PROD_HOST}/google-callback"
SCOPE = "https://www.googleapis.com/auth/youtube"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/youtube/v3"
STATE_COOKIE = "gg_google_state"
STATE_TTL_S = 600
GLOBAL = -1                     # cota é do projeto no Google: conta para todos os usuários juntos
QUOTA_KEY = "youtube_quota"
STATE_KEY = "youtube_state"     # playlists HAX automáticas de cada pessoa
STATUS_KEY = "youtube_status"   # "desconectado: ..." quando o Google derruba o acesso
DAILY_UNITS = 9000              # margem dentro das 10.000 do Google
COST = {"search": 100, "insert": 50, "playlist": 50}


class YTError(RuntimeError):
    def __init__(self, msg: str, status: int = 400, reason: str | None = None):
        super().__init__(msg)
        self.status, self.reason = status, reason


class QuotaExceeded(YTError):
    def __init__(self):
        super().__init__("O limite diário do YouTube acabou (o Google libera de novo à meia-noite do horário do Pacífico, "
                         "umas 4 ou 5 da manhã no Brasil). O que faltou entra amanhã.", 429, "quotaExceeded")


def client_id() -> str | None:
    return (os.environ.get("GOOGLE_CLIENT_ID") or "").strip() or None


def _client_secret() -> str | None:
    return (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip() or None


def configured() -> bool:
    return bool(client_id() and _client_secret())


def oauth_available(host: str | None) -> bool:
    """O login do Google só funciona no domínio de produção (é o único redirect cadastrado)."""
    return configured() and (host or "").split(":")[0].lower() == PROD_HOST


# ------------------------------------------------------------------ state (cookie) ----

def _fernet():
    from .garmin_sync import _fernet as f
    return f()


def new_state(user_id: int, purpose: str = "youtube") -> tuple[str, str]:
    """(state para o Google, valor do cookie). O cookie é criptografado: guarda o state, a pessoa, a finalidade
    ("youtube" = conectar o YouTube; "login" = entrar com Google) e a validade."""
    state = secrets.token_urlsafe(24)
    cookie = _fernet().encrypt(json.dumps({"s": state, "u": int(user_id), "p": purpose,
                                           "exp": time.time() + STATE_TTL_S}).encode()).decode()
    return state, cookie


def read_state(cookie: str | None, state: str | None) -> dict | None:
    """{"u": usuário, "p": finalidade} se o state bate com o cookie e não venceu; senão None."""
    if not cookie or not state:
        return None
    try:
        d = json.loads(_fernet().decrypt(cookie.encode()).decode())
    except Exception:  # noqa: BLE001 - cookie adulterado, de outra chave ou corrompido
        return None
    if not secrets.compare_digest(str(d.get("s")), str(state)) or time.time() > float(d.get("exp") or 0):
        return None
    return {"u": int(d.get("u") or 0), "p": d.get("p") or "youtube"}


def check_state(cookie: str | None, state: str | None, purpose: str = "youtube") -> int | None:
    """Usuário dono do pedido se o state bate, não venceu e é da mesma finalidade; senão None."""
    d = read_state(cookie, state)
    return d["u"] if d and d["p"] == purpose else None


def login_url(state: str) -> str:
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id(), "redirect_uri": REDIRECT_URI, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state})


# ------------------------------------------------------------------ tokens (tabela google_tokens) ----

def _row(user_id: int) -> dict | None:
    with db.conn() as c:
        r = c.execute("SELECT * FROM google_tokens WHERE id = ?", (int(user_id),)).fetchone()
    return dict(r) if r else None


def _save(user_id: int, access: str, expires_in: float, scope: str | None, refresh: str | None) -> None:
    now = db.now()
    exp = time.time() + float(expires_in or 3600)
    enc = _fernet().encrypt(refresh.encode()).decode() if refresh else None
    with db.conn() as c:
        if enc:
            c.execute("INSERT INTO google_tokens (id, refresh_token_enc, access_token, expires_at, scope, created_at, updated_at) "
                      "VALUES (?,?,?,?,?,?,?) ON CONFLICT (id) DO UPDATE SET refresh_token_enc = excluded.refresh_token_enc, "
                      "access_token = excluded.access_token, expires_at = excluded.expires_at, scope = excluded.scope, "
                      "updated_at = excluded.updated_at", (int(user_id), enc, access, exp, scope, now, now))
        else:   # renovação: o Google não manda refresh token de novo
            c.execute("UPDATE google_tokens SET access_token = ?, expires_at = ?, scope = COALESCE(?, scope), updated_at = ? "
                      "WHERE id = ?", (access, exp, scope, now, int(user_id)))


def _delete(user_id: int) -> None:
    with db.conn() as c:
        c.execute("DELETE FROM google_tokens WHERE id = ?", (int(user_id),))


def connected(user_id: int | None = None) -> bool:
    r = _row(user_id or db.current_user())
    return bool(r and r.get("refresh_token_enc"))


def _form(d: dict) -> bytes:
    return urllib.parse.urlencode(d).encode()


def exchange_code(code: str, user_id: int) -> None:
    """Troca o código do Google por tokens (no servidor, com o client secret) e grava criptografado."""
    st, d = music._http("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"},
                        _form({"client_id": client_id() or "", "client_secret": _client_secret() or "", "code": code,
                               "grant_type": "authorization_code", "redirect_uri": REDIRECT_URI}))
    if st != 200 or not isinstance(d, dict) or not d.get("access_token"):
        err = (d or {}).get("error") if isinstance(d, dict) else None
        raise YTError(f"O Google recusou a conexão ({err or st}).", 400, err)
    if not d.get("refresh_token"):
        raise YTError("O Google não mandou o acesso permanente. Tente conectar de novo.", 400, "no_refresh_token")
    _save(user_id, d["access_token"], d.get("expires_in") or 3600, d.get("scope"), d["refresh_token"])
    db.kv_delete(STATUS_KEY, user_id=user_id)


def access_token(user_id: int | None = None) -> str:
    """Token válido: o guardado se faltar mais de 60 s para vencer; senão renova (refresh_token)."""
    uid = user_id or db.current_user()
    r = _row(uid)
    if not r or not r.get("refresh_token_enc"):
        raise YTError("YouTube não conectado.", 401, "not_connected")
    if r.get("access_token") and float(r.get("expires_at") or 0) - time.time() > 60:
        return r["access_token"]
    try:
        refresh = _fernet().decrypt(r["refresh_token_enc"].encode()).decode()
    except Exception as e:  # chave trocada: trata como desconectado
        _delete(uid)
        raise YTError("A conexão com o YouTube precisa ser refeita.", 401, "not_connected") from e
    st, d = music._http("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"},
                        _form({"client_id": client_id() or "", "client_secret": _client_secret() or "",
                               "refresh_token": refresh, "grant_type": "refresh_token"}))
    if st == 200 and isinstance(d, dict) and d.get("access_token"):
        _save(uid, d["access_token"], d.get("expires_in") or 3600, d.get("scope"), None)
        return d["access_token"]
    if isinstance(d, dict) and d.get("error") == "invalid_grant":   # acesso revogado ou vencido: desconecta
        _delete(uid)
        db.kv_set(STATUS_KEY, "desconectado: o Google retirou o acesso; conecte de novo", user_id=uid)
        raise YTError("O acesso ao YouTube foi retirado ou venceu. Conecte de novo.", 401, "invalid_grant")
    raise YTError("Não consegui renovar o acesso ao YouTube agora. Tente de novo em instantes.", 502, "refresh_failed")


def disconnect(user_id: int | None = None) -> None:
    """Revoga no Google (se der) e apaga os tokens."""
    uid = user_id or db.current_user()
    r = _row(uid)
    if r and r.get("refresh_token_enc"):
        try:
            tok = _fernet().decrypt(r["refresh_token_enc"].encode()).decode()
            music._http("POST", REVOKE_URL, {"Content-Type": "application/x-www-form-urlencoded"}, _form({"token": tok}))
        except Exception:  # noqa: BLE001, S110 - apaga mesmo se o Google não responder (sem logar: envolve o token)
            pass
    _delete(uid)
    db.kv_delete(STATUS_KEY, user_id=uid)


def status_note(user_id: int | None = None) -> str | None:
    return db.kv_get(STATUS_KEY, user_id=user_id or db.current_user())


# ------------------------------------------------------------------ cota (do projeto todo) ----

def _quota_day() -> str:
    """A cota do Google zera à meia-noite do horário do Pacífico."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
    except Exception:  # noqa: BLE001
        return time.strftime("%Y-%m-%d", time.gmtime(time.time() - 8 * 3600))


def _quota() -> dict:
    q = json.loads(db.kv_get(QUOTA_KEY, user_id=GLOBAL) or "{}")
    if q.get("day") != _quota_day():
        q = {"day": _quota_day(), "used": 0, "blocked": False}
    return q


def _spend(what: str) -> None:
    q = _quota()
    if q.get("blocked") or q["used"] + COST[what] > DAILY_UNITS:
        raise QuotaExceeded()
    q["used"] += COST[what]
    db.kv_set(QUOTA_KEY, json.dumps(q), user_id=GLOBAL)


def _block_quota() -> None:
    q = _quota()
    q["blocked"] = True
    db.kv_set(QUOTA_KEY, json.dumps(q), user_id=GLOBAL)


def units_left() -> int:
    q = _quota()
    return 0 if q.get("blocked") else max(0, DAILY_UNITS - q["used"])


# ------------------------------------------------------------------ serviço YouTube ----

def _call(method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict | None:
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    st, d = music._http(method, url, {"Authorization": f"Bearer {access_token()}", "Content-Type": "application/json"},
                        json.dumps(body).encode() if body is not None else None)
    if st >= 400:
        err = (d or {}).get("error") if isinstance(d, dict) else None
        reason = ((err or {}).get("errors") or [{}])[0].get("reason") if isinstance(err, dict) else None
        if st == 403 and reason in ("quotaExceeded", "dailyLimitExceeded"):
            _block_quota()
            raise QuotaExceeded()
        msg = (err or {}).get("message") if isinstance(err, dict) else None
        raise YTError(msg or f"O YouTube respondeu {st}.", st, reason)
    return d


def create_playlist(title: str, description: str = "", privacy: str = "private") -> str:
    _spend("playlist")
    d = _call("POST", "/playlists", {"part": "snippet,status"},
              {"snippet": {"title": title[:150], "description": description[:5000]},
               "status": {"privacyStatus": privacy if privacy in ("private", "unlisted", "public") else "private"}})
    pid = (d or {}).get("id")
    if not pid:
        raise YTError("O YouTube não criou a playlist.", 502)
    return pid


def add_video(playlist_id: str, video_id: str) -> None:
    _spend("insert")
    _call("POST", "/playlistItems", {"part": "snippet"},
          {"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}})


def _norm_query(q: str) -> str:
    return " ".join((q or "").lower().split())[:200]


def find_video_id(query: str) -> str | None:
    """Vídeo da música (cache no banco primeiro: cada busca no YouTube gasta 100 unidades). None = não achou."""
    key = _norm_query(query)
    if not key:
        return None
    with db.conn() as c:
        r = c.execute("SELECT video_id FROM youtube_video_cache WHERE query = ?", (key,)).fetchone()
    if r is not None:
        return dict(r)["video_id"]
    _spend("search")
    d = _call("GET", "/search", {"part": "snippet", "type": "video", "maxResults": 1, "q": query, "videoCategoryId": "10"})
    items = (d or {}).get("items") or []
    vid = ((items[0].get("id") or {}).get("videoId")) if items else None
    with db.conn() as c:
        c.execute("INSERT INTO youtube_video_cache (query, video_id, created_at) VALUES (?,?,?) "
                  "ON CONFLICT (query) DO UPDATE SET video_id = excluded.video_id", (key, vid, db.now()))
    return vid


def _search_http(token: str, query: str) -> tuple[str, str | None, str | None]:
    """(query, video_id, erro) — só HTTP, para rodar em paralelo."""
    url = API + "/search?" + urllib.parse.urlencode({"part": "snippet", "type": "video", "maxResults": 1, "q": query,
                                                    "videoCategoryId": "10"})
    st, d = music._http("GET", url, {"Authorization": f"Bearer {token}"})
    if st >= 400:
        err = (d or {}).get("error") if isinstance(d, dict) else None
        reason = ((err or {}).get("errors") or [{}])[0].get("reason") if isinstance(err, dict) else None
        return query, None, reason or str(st)
    items = (d or {}).get("items") or []
    return query, ((items[0].get("id") or {}).get("videoId")) if items else None, None


def prefetch(queries: list[str], workers: int = 8) -> int:
    """Busca de uma vez (em paralelo) os vídeos que ainda não estão no cache, dentro da cota do dia."""
    from concurrent.futures import ThreadPoolExecutor
    todo = [q for q in dict.fromkeys(queries) if q and not _cached(q)[0]]
    cap = units_left() // COST["search"]
    todo = todo[:cap]
    if not todo:
        return 0
    token = access_token()
    q = _quota()
    q["used"] += COST["search"] * len(todo)
    db.kv_set(QUOTA_KEY, json.dumps(q), user_id=GLOBAL)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda x: _search_http(token, x), todo))
    n = 0
    for query, vid, err in results:
        if err in ("quotaExceeded", "dailyLimitExceeded"):
            _block_quota()
            continue
        if err:
            continue                       # erro passageiro: tenta de novo outro dia
        with db.conn() as c:
            c.execute("INSERT INTO youtube_video_cache (query, video_id, created_at) VALUES (?,?,?) "
                      "ON CONFLICT (query) DO UPDATE SET video_id = excluded.video_id", (_norm_query(query), vid, db.now()))
        n += 1
    return n


def playlist_url(pid: str) -> str:
    return f"https://music.youtube.com/playlist?list={pid}"


def make_playlist(name: str, songs: list[dict]) -> dict:
    """Cria uma playlist com as músicas na ordem. Se a cota acabar no meio, devolve o que deu e o que ficou faltando."""
    songs = [s for s in songs if (s.get("titulo") or "").strip()][:200]
    if not (name or "").strip():
        raise YTError("Dê um nome à playlist.")
    if not songs:
        raise YTError("Mande pelo menos uma música.")
    found, missing, pending = [], [], []
    for s in songs:       # cota acabou na busca: erro claro (o que já foi achado fica no cache e não gasta amanhã)
        vid = find_video_id(f"{s.get('titulo', '').strip()} {s.get('artista', '').strip()}")
        (found if vid else missing).append((s, vid))
    pid = create_playlist(name.strip(), "Criada pelo app HAX.")
    added = 0
    try:
        for s, vid in found:
            add_video(pid, vid)
            added += 1
    except QuotaExceeded:
        pending = [s for s, _ in found[added:]] + pending
    out = {"url": playlist_url(pid), "playlist_id": pid, "adicionadas": added,
           "nao_encontradas": [{"artista": s.get("artista"), "titulo": s.get("titulo")} for s, _ in missing]}
    if pending:
        out["pendentes"] = [{"artista": s.get("artista"), "titulo": s.get("titulo")} for s in pending]
        out["aviso"] = str(QuotaExceeded())
    return out


# ------------------------------------------------------------------ playlists HAX automáticas ----

def _state() -> dict:
    st = json.loads(db.kv_get(STATE_KEY) or "{}")
    st.setdefault("playlists", {})
    st.setdefault("added", {})
    return st


def _save_state(st: dict) -> None:
    db.kv_set(STATE_KEY, json.dumps(st))


def _query_for(row: dict) -> str:
    return f"{row.get('name') or ''} {(row.get('artist') or '').split(',')[0]}".strip()


def _cached(query: str) -> tuple[bool, str | None]:
    with db.conn() as c:
        r = c.execute("SELECT video_id FROM youtube_video_cache WHERE query = ?", (_norm_query(query),)).fetchone()
    return (r is not None, dict(r)["video_id"] if r is not None else None)


def sync(budget_s: float = 8.0, cad: int | None = None) -> dict:
    """As mesmas playlists HAX do Spotify (no BPM de cada momento) no YouTube, revezando entre elas para todas
    crescerem juntas, até acabar o tempo desta chamada ou a cota do dia."""
    st = _state()
    cad = cad or music.cadence_target()
    wanted = {m: music.pick(m, cad) for m in music.MODES}
    t0, stop, progressed = time.monotonic(), None, True
    try:
        nxt_q, depth = [], max((len(v) for v in wanted.values()), default=0)
        for k in range(depth):              # revezando: a 1ª de cada playlist, depois a 2ª...
            for mode, rows in wanted.items():
                if k < len(rows) and rows[k]["id"] not in set(st["added"].get(mode) or []):
                    nxt_q.append(_query_for(rows[k]))
        prefetch(nxt_q[: max(0, units_left() // (COST["search"] + COST["insert"]))])
        while progressed and time.monotonic() - t0 < budget_s:
            progressed = False
            for mode, rows in wanted.items():
                if time.monotonic() - t0 > budget_s:
                    break
                done = set(st["added"].get(mode) or [])
                nxt = None
                for r in rows:
                    if r["id"] in done:
                        continue
                    hit, vid = _cached(_query_for(r))
                    if hit and not vid:
                        continue            # já procurado e não achado: não gasta de novo
                    nxt = r
                    break
                if not nxt:
                    continue
                pid = st["playlists"].get(mode)
                if not pid:
                    m = music.MODES[mode]
                    pid = create_playlist(f"HAX · {m['label']}", f"Feita pelo app HAX: {m['desc']}. Suas músicas no BPM certo.")
                    st["playlists"][mode] = pid
                vid = find_video_id(_query_for(nxt))
                if vid:
                    try:
                        add_video(pid, vid)
                    except YTError as e:
                        if e.status == 404:             # a pessoa apagou a playlist no YouTube: cria de novo
                            st["playlists"].pop(mode, None)
                            st["added"].pop(mode, None)
                            continue
                        raise
                    st["added"].setdefault(mode, []).append(nxt["id"])
                progressed = True
    except QuotaExceeded:
        stop = "cota"
    finally:
        _save_state(st)
    return {**summary(st, wanted), "stopped": stop}


def summary(st: dict | None = None, wanted: dict | None = None, cad: int | None = None) -> dict:
    st = st or _state()
    if wanted is None:
        cad = cad or music.cadence_target()
        wanted = {m: music.pick(m, cad) for m in music.MODES}
    pls, pending = [], 0
    for mode, m in music.MODES.items():
        rows = wanted.get(mode) or []
        added = set(st["added"].get(mode) or [])
        done = sum(1 for r in rows if r["id"] in added)
        for r in rows:
            if r["id"] not in added:
                hit, vid = _cached(_query_for(r))
                pending += 0 if (hit and not vid) else 1
        pid = st["playlists"].get(mode)
        pls.append({"id": mode, "label": m["label"], "done": done, "total": len(rows), "url": playlist_url(pid) if pid else None})
    return {"playlists": pls, "pending": pending, "today_left": units_left() // (COST["search"] + COST["insert"])}
