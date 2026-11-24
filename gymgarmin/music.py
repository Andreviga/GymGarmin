"""Música no treino com o Spotify: as músicas que a pessoa gosta, no BPM certo para cada momento.

Como funciona:
1. A pessoa conecta o Spotify (login PKCE: o app não guarda senha; os tokens ficam criptografados).
2. O app lê as músicas que ela mais ouve (top do último mês e dos últimos 6 meses), as curtidas e as
   que tocaram por último, e descobre o BPM de cada uma no Deezer (o Spotify não informa mais o BPM
   para apps novos). Quando dá, a ReccoBeats completa com a "energia" da música.
3. Monta playlists "HAX · ..." na conta dela, uma por momento do treino, só com as músicas dela:
   - Aquecimento 100–125 bpm (sobe aos poucos)
   - Musculação 125–150 bpm (faixa preferida para exercício forte; Karageorghis)
   - Mais gás 145–180 bpm (última série, finalizador, tiros)
   - Corrida leve / ritmo / tiros: BPM = cadência da passada (ou metade dela), para correr no ritmo
   - Calma 60–95 bpm (volta à calma, alongamento, yoga)
4. No treino, o botão 🎵 toca a playlist do momento; com Premium o app troca sozinho ao mudar de fase
   e o "⚡ Mais gás" põe agora a música mais rápida que ela gosta.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import db

API = "https://api.spotify.com/v1"
ACCOUNTS = "https://accounts.spotify.com"
TOKENS_KEY = "spotify_tokens"
LIB_KEY = "spotify_lib"
SET_KEY = "music_settings"
SCOPES = ("user-top-read user-library-read user-read-recently-played playlist-read-private playlist-modify-private "
          "playlist-modify-public user-read-playback-state user-modify-playback-state user-read-currently-playing")

# momentos do treino: faixa de BPM, se vale metade/dobro (corrida: um passo a cada batida ou a cada 2)
MODES: dict[str, dict] = {
    "aquecimento": {"label": "Aquecimento", "lo": 100, "hi": 125, "order": "sobe", "desc": "sobe aos poucos, prepara sem cansar"},
    "musculacao": {"label": "Musculação", "lo": 125, "hi": 150, "order": "gosto", "desc": "a faixa que mais ajuda no treino forte"},
    "gas": {"label": "Mais gás", "lo": 145, "hi": 180, "order": "rapida", "desc": "última série, finalizador, tiros"},
    "corrida_leve": {"label": "Corrida leve", "cad": 0, "order": "ajuste", "desc": "no ritmo da sua passada"},
    "corrida_ritmo": {"label": "Corrida forte", "cad": 6, "order": "ajuste", "desc": "passada um pouco mais rápida"},
    "tiros": {"label": "Tiros", "cad": 12, "order": "ajuste", "desc": "passada rápida: tiros e intervalados"},
    "calma": {"label": "Calma", "lo": 60, "hi": 95, "order": "desce", "desc": "volta à calma, alongamento, yoga"},
}
DEFAULT_CADENCE = 162
TOL = 0.035          # corrida: até 3,5% fora da cadência ainda dá para acompanhar
MAX_PER_LIST = 40


class MusicError(RuntimeError):
    pass


def client_id() -> str | None:
    return (os.environ.get("SPOTIFY_CLIENT_ID") or "").strip() or None


def redirect_uri(base: str) -> str:
    return base.rstrip("/") + "/spotify-callback"


# ------------------------------------------------------------------ HTTP ----

UA = "HAX-fitness/1.0 (+https://haxfit.vercel.app)"   # sem identificação, Deezer/ReccoBeats bloqueiam (Cloudflare)


def _http(method: str, url: str, headers: dict | None = None, body: bytes | None = None, timeout: float = 12) -> tuple[int, dict | None]:
    req = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - hosts fixos (Spotify, Deezer, ReccoBeats)
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read() or b"null")
        except ValueError:
            data = None
        return e.code, data


# ------------------------------------------------------------------ tokens ----

def _put_tokens(t: dict) -> None:
    from .garmin_sync import _put_secret
    _put_secret(TOKENS_KEY, t)


def _tokens() -> dict | None:
    from .garmin_sync import _get_secret
    raw = _get_secret(TOKENS_KEY)
    return json.loads(raw) if raw else None


def connected() -> bool:
    return bool(_tokens())


def _form(d: dict) -> bytes:
    return urllib.parse.urlencode(d).encode()


def exchange(code: str, verifier: str, redirect: str) -> dict:
    cid = client_id()
    if not cid:
        raise MusicError("Falta o SPOTIFY_CLIENT_ID no servidor.")
    st, data = _http("POST", f"{ACCOUNTS}/api/token", {"Content-Type": "application/x-www-form-urlencoded"},
                     _form({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
                            "client_id": cid, "code_verifier": verifier}))
    if st != 200 or not data or not data.get("access_token"):
        raise MusicError(f"O Spotify recusou a conexão ({(data or {}).get('error_description') or st}).")
    t = {"access": data["access_token"], "refresh": data.get("refresh_token"), "exp": time.time() + int(data.get("expires_in") or 3600) - 60}
    _put_tokens(t)
    me = call("GET", "/me")
    s = settings()
    s.update(product=(me or {}).get("product"), name=(me or {}).get("display_name"), user=(me or {}).get("id"))
    save_settings(s)
    return s


def _access() -> str:
    t = _tokens()
    if not t:
        raise MusicError("Spotify não conectado.")
    if time.time() < t.get("exp", 0):
        return t["access"]
    st, data = _http("POST", f"{ACCOUNTS}/api/token", {"Content-Type": "application/x-www-form-urlencoded"},
                     _form({"grant_type": "refresh_token", "refresh_token": t.get("refresh") or "", "client_id": client_id() or ""}))
    if st != 200 or not data or not data.get("access_token"):
        raise MusicError("A conexão com o Spotify expirou. Conecte de novo no Perfil.")
    t.update(access=data["access_token"], exp=time.time() + int(data.get("expires_in") or 3600) - 60)
    if data.get("refresh_token"):
        t["refresh"] = data["refresh_token"]       # o Spotify pode trocar o refresh token
    _put_tokens(t)
    return t["access"]


def call(method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict | None:
    url = (path if path.startswith("http") else API + path) + (("?" + urllib.parse.urlencode(params)) if params else "")
    st, data = _http(method, url, {"Authorization": f"Bearer {_access()}", "Content-Type": "application/json"},
                     json.dumps(body).encode() if body is not None else None)
    if st >= 400:
        err = ((data or {}).get("error") or {}) if isinstance(data, dict) else {}
        e = MusicError(err.get("message") or f"Spotify {st}")
        e.status, e.reason = st, err.get("reason")  # type: ignore[attr-defined]
        raise e
    return data


def disconnect() -> None:
    from .garmin_sync import _del
    _del(TOKENS_KEY)


# ------------------------------------------------------------------ ajustes ----

def settings() -> dict:
    return json.loads(db.kv_get(SET_KEY) or "{}")


def save_settings(s: dict) -> None:
    db.kv_set(SET_KEY, json.dumps(s))


def cadence_target(recent_cadences: list[float] | None = None) -> int:
    """Cadência de corrida (passos/min): a que a pessoa escolheu, ou a mediana das corridas (só as que são
    corrida mesmo: corre-caminha puxa a média para baixo), ou 162."""
    s = settings()
    if s.get("cadence"):
        return int(s["cadence"])
    runs = sorted(c for c in (recent_cadences or []) if c and 145 <= c <= 200)
    return int(round(runs[len(runs) // 2])) if runs else DEFAULT_CADENCE


# ------------------------------------------------------------------ biblioteca ----

def _lib() -> dict:
    return json.loads(db.kv_get(LIB_KEY) or '{"tracks": {}}')


def _save_lib(lib: dict) -> None:
    db.kv_set(LIB_KEY, json.dumps(lib, ensure_ascii=False))


def _track_row(t: dict, aff: float, src: str) -> dict:
    return {"name": t.get("name"), "artist": ", ".join(a.get("name", "") for a in t.get("artists") or [])[:80],
            "uri": t.get("uri"), "isrc": (t.get("external_ids") or {}).get("isrc"), "ms": t.get("duration_ms"),
            "aff": aff, "src": src}


def fetch_library() -> dict:
    """Músicas que a pessoa gosta: top (mês e 6 meses) pesam mais; curtidas e recém-tocadas completam."""
    lib = _lib()
    old = lib.get("tracks") or {}
    found: dict[str, dict] = {}

    def add(t: dict | None, aff: float, src: str) -> None:
        if not t or not t.get("id") or t.get("is_local") or t.get("type", "track") != "track":
            return
        cur = found.get(t["id"])
        if cur:
            cur["aff"] = round(cur["aff"] + aff * 0.5, 2)
        else:
            row = _track_row(t, aff, src)
            prev = old.get(t["id"]) or {}
            for k in ("bpm", "energy", "tried"):
                if k in prev:
                    row[k] = prev[k]
            found[t["id"]] = row

    for rng, w in (("short_term", 3.0), ("medium_term", 2.0), ("long_term", 1.2)):
        try:
            items = (call("GET", "/me/top/tracks", params={"time_range": rng, "limit": 50}) or {}).get("items") or []
        except MusicError:
            items = []
        for k, t in enumerate(items):
            add(t, w * (1 - k / 100), "top")
    for off in (0, 50, 100, 150):
        try:
            items = (call("GET", "/me/tracks", params={"limit": 50, "offset": off}) or {}).get("items") or []
        except MusicError:
            break
        for k, it in enumerate(items):
            add(it.get("track"), 1.0 - (off + k) / 400, "curtida")
        if len(items) < 50:
            break
    try:
        for it in (call("GET", "/me/player/recently-played", params={"limit": 50}) or {}).get("items") or []:
            add(it.get("track"), 0.6, "recente")
    except MusicError:
        pass
    lib.update(tracks=found, updated=time.time())
    _save_lib(lib)
    return lib


def _deezer_bpm(row: dict) -> float | None:
    if row.get("isrc"):
        st, d = _http("GET", f"https://api.deezer.com/track/isrc:{urllib.parse.quote(row['isrc'])}", timeout=8)
        _DIAG["deezer"] = st
        if st == 200 and isinstance(d, dict) and not d.get("error") and d.get("bpm"):
            return float(d["bpm"])
    q = f'artist:"{(row.get("artist") or "").split(",")[0]}" track:"{row.get("name") or ""}"'
    st, d = _http("GET", "https://api.deezer.com/search?" + urllib.parse.urlencode({"q": q, "limit": 1}), timeout=8)
    hit = ((d or {}).get("data") or [None])[0] if st == 200 and isinstance(d, dict) else None
    if hit and hit.get("id"):
        st, t = _http("GET", f"https://api.deezer.com/track/{hit['id']}", timeout=8)
        if st == 200 and isinstance(t, dict) and t.get("bpm"):
            return float(t["bpm"])
    return None


def _recco(ids: list[str]) -> dict[str, dict]:
    """BPM e energia pela ReccoBeats (por id do Spotify). Opcional: se falhar, fica só o do Deezer."""
    if not ids:
        return {}
    st, d = _http("GET", "https://api.reccobeats.com/v1/audio-features?" + urllib.parse.urlencode({"ids": ",".join(ids[:40])}), timeout=10)
    _DIAG["recco"] = st
    out = {}
    for f in ((d or {}).get("content") or []) if st == 200 and isinstance(d, dict) else []:
        href = f.get("href") or ""
        sid = href.rstrip("/").split("/")[-1] if "spotify.com" in href else f.get("spotifyId") or f.get("id")
        if sid:
            out[sid] = {"bpm": f.get("tempo"), "energy": f.get("energy")}
    return out


_DIAG: dict = {}
BPM_V = 2   # versão da busca de BPM: subir faz tentar de novo as músicas que ficaram sem


def _spotify_features(ids: list[str]) -> dict[str, dict]:
    """O próprio Spotify (apps antigos ainda têm acesso; apps novos recebem 403 — aí para de tentar)."""
    if _DIAG.get("spotify_af") == 403 or not ids:
        return {}
    try:
        d = call("GET", "/audio-features", params={"ids": ",".join(ids[:100])}) or {}
        _DIAG["spotify_af"] = 200
    except MusicError as e:
        _DIAG["spotify_af"] = getattr(e, "status", 0) or -1
        return {}
    return {f["id"]: {"bpm": f.get("tempo"), "energy": f.get("energy")} for f in d.get("audio_features") or [] if f and f.get("id")}


def enrich(budget_s: float = 8.0, batch: int = 40) -> dict:
    """Descobre o BPM das músicas que ainda não têm (em lotes, para caber no tempo do servidor)."""
    lib = _lib()
    tracks = lib.get("tracks") or {}
    if lib.get("bpm_v") != BPM_V:          # busca melhorou: tenta de novo as que ficaram sem BPM
        for r in tracks.values():
            if r.get("bpm") is None:
                r.pop("tried", None)
        lib["bpm_v"] = BPM_V
    todo = [tid for tid, r in tracks.items() if r.get("bpm") is None and not r.get("tried")]
    src = lib.setdefault("diag", {"spotify": 0, "recco": 0, "deezer": 0, "none": 0})
    t0 = time.monotonic()
    while todo and time.monotonic() - t0 < budget_s:
        chunk, todo = todo[:batch], todo[batch:]
        sp = _safe(_spotify_features, chunk) or {}
        try:
            rec = _recco([t for t in chunk if not (sp.get(t) or {}).get("bpm")])
        except Exception as e:  # noqa: BLE001
            _DIAG["recco_err"] = str(e)[:120]
            rec = {}
        need = [tid for tid in chunk if not (sp.get(tid) or {}).get("bpm") and not (rec.get(tid) or {}).get("bpm")]
        with ThreadPoolExecutor(max_workers=8) as ex:
            dz = dict(zip(need, ex.map(lambda tid: _safe(_deezer_bpm, tracks[tid]), need)))
        for tid in chunk:
            r = tracks[tid]
            ss, rr = sp.get(tid) or {}, rec.get(tid) or {}
            bpm = ss.get("bpm") or rr.get("bpm") or dz.get(tid)
            r["tried"] = True
            if bpm:
                r["bpm"] = round(float(bpm), 1)
            src["spotify" if ss.get("bpm") else "recco" if rr.get("bpm") else "deezer" if dz.get(tid) else "none"] += 1
            energy = ss.get("energy") if ss.get("energy") is not None else rr.get("energy")
            if energy is not None:
                r["energy"] = round(float(energy), 2)
    src["last"] = dict(_DIAG)
    _save_lib(lib)
    pending = sum(1 for r in tracks.values() if r.get("bpm") is None and not r.get("tried"))
    return {"pending": pending, "known": sum(1 for r in tracks.values() if r.get("bpm")), "total": len(tracks)}


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ escolher ----

def _cands(bpm: float, mode: str, cad: int) -> list[float]:
    """BPMs que a música "vale": corrida aceita metade/dobro (um passo por batida ou a cada duas); nas outras,
    só corrige o detector que às vezes dobra o BPM de música lenta (acima de 185)."""
    if "cad" in MODES[mode]:
        return [bpm, bpm * 2, bpm / 2]
    return [bpm] + ([bpm / 2] if bpm > 185 else [])


def _fit(bpm: float, mode: str, cad: int) -> float | None:
    """0 = no alvo; None = não serve."""
    m = MODES[mode]
    if "cad" in m:
        target = cad + m["cad"]
        best = min(abs(b - target) / target for b in _cands(bpm, mode, cad))
        return best if best <= TOL else None
    mid = (m["lo"] + m["hi"]) / 2
    ok = [abs(b - mid) / mid * 0.5 for b in _cands(bpm, mode, cad) if m["lo"] <= b <= m["hi"]]
    return min(ok) if ok else None


def _eff_bpm(bpm: float, mode: str, cad: int) -> float:
    m = MODES[mode]
    target = cad + m["cad"] if "cad" in m else (m["lo"] + m["hi"]) / 2
    return min(_cands(bpm, mode, cad), key=lambda b: abs(b - target))


def pick(mode: str, cad: int, lib: dict | None = None, n: int = MAX_PER_LIST) -> list[dict]:
    tracks = (lib or _lib()).get("tracks") or {}
    rows = []
    for tid, r in tracks.items():
        if not r.get("bpm"):
            continue
        f = _fit(r["bpm"], mode, cad)
        if f is None:
            continue
        energy = r.get("energy")
        if mode == "calma" and energy is not None and energy > 0.75:
            continue
        if mode in ("gas", "tiros", "musculacao") and energy is not None and energy < 0.45:
            continue
        score = r.get("aff", 1) * 1.0 - f * 6 + ((energy or 0.6) - 0.6) * (2 if mode in ("gas", "tiros", "musculacao") else -2 if mode == "calma" else 0)
        rows.append({"id": tid, **r, "eff": round(_eff_bpm(r["bpm"], mode, cad)), "score": round(score, 3)})
    order = MODES[mode]["order"]
    rows.sort(key=lambda x: -x["score"])
    rows = rows[:n]
    if order == "sobe":
        rows.sort(key=lambda x: x["eff"])
    elif order == "desce":
        rows.sort(key=lambda x: -x["eff"])
    elif order == "rapida":
        rows.sort(key=lambda x: (-x["eff"] // 8, -x["score"]))
    return rows


# ------------------------------------------------------------------ playlists ----

def _set_items(pid: str, uris: list[str]) -> None:
    """Troca as músicas da playlist (rotas novas do Spotify em 2026: /items; as antigas /tracks como reserva)."""
    for base in (f"/playlists/{pid}/items", f"/playlists/{pid}/tracks"):
        try:
            call("PUT", base, {"uris": uris[:100]})
            return
        except MusicError as e:
            if getattr(e, "status", 0) not in (403, 404, 405):
                raise
    raise MusicError("O Spotify não deixou atualizar a playlist.")


def _create(name: str, desc: str) -> dict:
    body = {"name": name, "public": False, "description": desc}
    try:
        return call("POST", "/me/playlists", body) or {}
    except MusicError as e:
        if getattr(e, "status", 0) not in (403, 404, 405):
            raise
        uid = settings().get("user") or (call("GET", "/me") or {}).get("id")
        return call("POST", f"/users/{uid}/playlists", body) or {}


def build_playlists(cad: int) -> dict:
    """Cria/atualiza as playlists HAX com as músicas da pessoa no BPM de cada momento."""
    lib = _lib()
    s = settings()
    pls = s.get("playlists") or {}
    out = {}
    for mode, m in MODES.items():
        rows = pick(mode, cad, lib)
        extra = f" · alvo {cad + m['cad']} passos/min" if "cad" in m else f" · {m['lo']}–{m['hi']} bpm"
        name = f"HAX · {m['label']}"
        desc = f"Feita pelo app HAX com as músicas que você mais ouve: {m['desc']}{extra}."
        if len(rows) < 3:
            out[mode] = {"n": len(rows), "id": (pls.get(mode) or {}).get("id"), "few": True}
            continue
        pid = (pls.get(mode) or {}).get("id")
        if not pid:
            p = _create(name, desc)
            pid = p.get("id")
            if not pid:
                continue
        try:
            _set_items(pid, [r["uri"] for r in rows])
        except MusicError as e:
            if getattr(e, "status", 0) == 404:   # a pessoa apagou a playlist no Spotify: cria de novo
                pid = _create(name, desc).get("id")
                _set_items(pid, [r["uri"] for r in rows])
            else:
                raise
        out[mode] = {"id": pid, "n": len(rows), "url": f"https://open.spotify.com/playlist/{pid}"}
    s["playlists"] = {k: v for k, v in out.items() if v.get("id")}
    s["built_cad"] = cad
    s["built"] = time.time()
    save_settings(s)
    return out


# ------------------------------------------------------------------ tocar ----

def play(mode: str) -> dict:
    """Toca a playlist do momento (Spotify Premium, com o app do Spotify aberto em algum aparelho)."""
    pl = (settings().get("playlists") or {}).get(mode)
    if not pl:
        raise MusicError("Playlist ainda não montada: toque em 'Atualizar minhas playlists'.")
    try:
        call("PUT", "/me/player/shuffle", params={"state": "false"})
    except MusicError:
        pass
    try:
        call("PUT", "/me/player/play", {"context_uri": f"spotify:playlist:{pl['id']}"})
    except MusicError as e:
        if getattr(e, "reason", None) == "PREMIUM_REQUIRED":
            s = settings(); s["premium_ok"] = False; save_settings(s)
        raise
    s = settings()
    if s.get("premium_ok") is not True:
        s["premium_ok"] = True; save_settings(s)
    return {"ok": True, "url": f"https://open.spotify.com/playlist/{pl['id']}"}


def boost(cad: int, calmer: bool = False) -> dict:
    """⚡ Mais gás: a música mais rápida que a pessoa gosta, agora (fila + pular). calmer=True: o contrário."""
    lib = _lib()
    rows = pick("calma" if calmer else "gas", cad, lib, n=12)
    if not rows:
        raise MusicError("Ainda não achei músicas suas nessa faixa de BPM.")
    recent = set(settings().get("boosted") or [])
    r = next((x for x in rows if x["id"] not in recent), rows[0])
    call("POST", "/me/player/queue", params={"uri": r["uri"]})
    call("POST", "/me/player/next")
    s = settings()
    s["boosted"] = ([r["id"]] + [x for x in (s.get("boosted") or []) if x != r["id"]])[:8]
    save_settings(s)
    return {"ok": True, "name": r["name"], "artist": r["artist"], "bpm": round(r["bpm"])}


def now_playing() -> dict | None:
    d = call("GET", "/me/player/currently-playing")
    item = (d or {}).get("item")
    if not item:
        return None
    r = (_lib().get("tracks") or {}).get(item.get("id")) or {}
    return {"name": item.get("name"), "artist": ", ".join(a.get("name", "") for a in item.get("artists") or []),
            "bpm": round(r["bpm"]) if r.get("bpm") else None, "playing": bool((d or {}).get("is_playing"))}


def summary(cad: int) -> dict:
    lib = _lib()
    tracks = lib.get("tracks") or {}
    s = settings()
    counts = {m: len(pick(m, cad, lib)) for m in MODES} if tracks else {}
    premium = s.get("product") == "premium" or (not s.get("product") and s.get("premium_ok") is not False)
    return {"configured": bool(client_id()), "connected": connected(), "name": s.get("name"), "premium": premium,
            "product": s.get("product"), "tracks": len(tracks), "with_bpm": sum(1 for r in tracks.values() if r.get("bpm")),
            "pending": sum(1 for r in tracks.values() if r.get("bpm") is None and not r.get("tried")),
            "cadence": cad, "cadence_manual": bool(s.get("cadence")), "auto": s.get("auto", True),
            "modes": [{"id": k, "label": m["label"], "desc": m["desc"],
                       "range": f"{cad + m['cad']} passos/min" if "cad" in m else f"{m['lo']}–{m['hi']} bpm",
                       "n": counts.get(k, 0), **({"url": f"https://open.spotify.com/playlist/{p['id']}"} if (p := (s.get("playlists") or {}).get(k)) else {})}
                      for k, m in MODES.items()],
            "built": s.get("built"), "updated": lib.get("updated")}


def search_url(mode: str, cad: int) -> str:
    """Sem conectar: abre a busca do Spotify com o tipo de música do momento."""
    m = MODES[mode]
    q = {"aquecimento": "aquecimento treino", "musculacao": "treino academia 130 bpm", "gas": "treino pesado 160 bpm",
         "corrida_leve": f"corrida {cad} bpm", "corrida_ritmo": f"corrida {cad + 6} bpm", "tiros": f"corrida {cad + 12} bpm",
         "calma": "alongamento relaxar"}[mode]
    return "https://open.spotify.com/search/" + urllib.parse.quote(q) if m else "https://open.spotify.com"
