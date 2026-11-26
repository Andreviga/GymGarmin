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
    # corrida forte: faixa da passada até +16 (156–172 bpm para passada 156): acompanha o ritmo e dá um pouco mais de gás
    "corrida_ritmo": {"label": "Corrida forte", "cad_lo": 0, "cad_hi": 16, "order": "ajuste", "desc": "passada firme, um pouco mais rápida"},
    # tiros: faixa (não um alvo exato) da passada +6 até +29 — no tiro, música mais rápida dá mais gás
    "tiros": {"label": "Tiros", "cad_lo": 6, "cad_hi": 29, "order": "rapida", "desc": "tiros e intervalados: rápida, para dar gás"},
    "calma": {"label": "Calma", "lo": 60, "hi": 95, "order": "desce", "desc": "volta à calma, alongamento, yoga"},
}
# estilos para descobrir músicas novas no BPM certo (busca do Spotify por gênero; o BPM vem da ReccoBeats/Deezer)
GENRES: dict[str, tuple[str, list[str]]] = {
    # populares
    "sertanejo": ("Sertanejo", ["sertanejo", "sertanejo universitario"]), "funk": ("Funk", ["funk carioca", "funk"]),
    "pagode": ("Pagode / samba", ["pagode", "samba"]), "rock": ("Rock", ["rock", "classic rock"]),
    "pop": ("Pop", ["pop", "dance pop"]), "rap": ("Rap / hip-hop", ["hip hop", "rap"]), "trap": ("Trap", ["trap"]),
    "eletronica": ("Eletrônica", ["edm", "electro house"]), "house": ("House", ["house", "tech house"]),
    "reggaeton": ("Reggaeton / latino", ["reggaeton", "latin"]), "forro": ("Forró / piseiro", ["forro", "piseiro"]),
    "mpb": ("MPB", ["mpb"]), "gospel": ("Gospel", ["gospel"]), "metal": ("Metal", ["metal", "heavy metal"]),
    "indie": ("Indie / alternativo", ["indie", "alternative rock"]), "anos80": ("Anos 80 e 90", ["80s", "90s"]),
    "kpop": ("K-pop", ["k-pop"]), "axe": ("Axé", ["axe"]),
    # nichados (vários com BPM alto: bons para tiros e mais gás)
    "hardtechno": ("Hard techno", ["hard techno", "industrial techno", "schranz"]),
    "hardcore_el": ("Hardcore (eletrônico / gabber)", ["hardcore", "gabber", "uptempo hardcore", "frenchcore"]),
    "hardstyle": ("Hardstyle", ["hardstyle", "rawstyle"]), "techno": ("Techno", ["techno", "melodic techno"]),
    "dnb": ("Drum and bass", ["drum and bass", "liquid funk", "neurofunk"]), "dubstep": ("Dubstep / bass", ["dubstep", "riddim"]),
    "psytrance": ("Psytrance", ["psytrance", "full on"]), "trance": ("Trance", ["trance", "uplifting trance"]),
    "hardcore_punk": ("Hardcore punk", ["hardcore punk", "melodic hardcore"]), "metalcore": ("Metalcore / post-hardcore", ["metalcore", "post-hardcore"]),
    "punk": ("Punk", ["punk", "pop punk"]), "numetal": ("Nu metal", ["nu metal"]), "thrash": ("Thrash / death metal", ["thrash metal", "death metal"]),
    "industrial": ("Industrial / EBM", ["industrial", "ebm"]), "phonk": ("Phonk", ["phonk", "drift phonk"]),
    "synthwave": ("Synthwave", ["synthwave", "darksynth"]), "rocknac": ("Rock nacional", ["rock nacional", "brazilian rock"]),
    "brega": ("Brega funk / arrocha", ["brega funk", "arrocha"]), "afro": ("Afrobeats / amapiano", ["afrobeats", "amapiano"]),
    "reggae": ("Reggae", ["reggae"]), "jazz": ("Jazz / blues", ["jazz", "blues"]), "lofi": ("Lo-fi / chill", ["lo-fi", "chill"]),
}
NICHE_FROM = "hardtechno"   # a partir deste, a tela mostra como "nichados"
# famílias de estilos parecidos: cada momento ganha uma playlist por família (quando tem músicas suficientes),
# para não pular de hard techno para sertanejo no meio do treino
FAMILIES: dict[str, dict] = {
    "pesada": {"label": "Eletrônica pesada", "genres": {"hardtechno", "hardcore_el", "hardstyle", "techno", "industrial", "dnb", "dubstep", "psytrance"},
               "kw": ("hard techno", "hardstyle", "gabber", "frenchcore", "uptempo", "schranz", "industrial", "techno", "drum", "dnb", "dubstep",
                      "riddim", "psy", "neurofunk", "bass music", "jungle", "ebm")},
    "eletronica": {"label": "Eletrônica e dance", "genres": {"eletronica", "house", "trance", "synthwave"},
                   "kw": ("house", "dance", "electro", "edm", "trance", "synth", "disco", "big room", "club")},
    "rock": {"label": "Rock e metal", "genres": {"rock", "metal", "indie", "punk", "hardcore_punk", "metalcore", "numetal", "thrash", "rocknac"},
             "kw": ("metal", "rock", "punk", "hardcore", "grunge", "emo", "alternative", "alternativ", "indie", "post-hardcore")},
    "rap": {"label": "Rap, trap e phonk", "genres": {"rap", "trap", "phonk"},
            "kw": ("rap", "hip hop", "hip-hop", "trap", "phonk", "drill", "r&b", "grime")},
    "br": {"label": "Brasileiras", "genres": {"sertanejo", "funk", "pagode", "forro", "axe", "brega", "mpb", "gospel"},
           "kw": ("sertanejo", "pagode", "samba", "axé", "axe", "forró", "forro", "piseiro", "mpb", "brazil", "brasil", "funk carioca",
                  "baile funk", "brega", "arrocha", "bossa", "gospel", "música brasileira")},
    "pop": {"label": "Pop e latino", "genres": {"pop", "kpop", "anos80", "reggaeton", "afro"},
            "kw": ("pop", "k-pop", "latin", "reggaeton", "afro", "amapiano", "80s", "90s", "dancehall")},
    "calma": {"label": "Calmas e acústicas", "genres": {"lofi", "jazz", "reggae"},
              "kw": ("jazz", "blues", "soul", "lo-fi", "lofi", "chill", "reggae", "classical", "clássica", "folk", "acoustic", "ambient",
                     "country", "funk", "bossa")},
}
GENRE_FAM = {g: f for f, d in FAMILIES.items() for g in d["genres"]}
STYLE_MIN = 12          # músicas para a família virar playlist própria no momento
STYLE_MAX_PER_MODE = 3  # no máximo 3 estilos por momento (os que têm mais músicas)


def family_of(text: str | None) -> str | None:
    """Família de um nome de gênero (do Deezer, do Spotify ou digitado). Ordem importa: pesada antes de eletrônica e rock."""
    t = (text or "").lower()
    if not t:
        return None
    if "funk" in t and ("carioca" in t or "baile" in t or "brasil" in t or t.strip() == "funk"):
        return "br"
    for fam in ("pesada", "rap", "br", "rock", "eletronica", "pop", "calma"):
        if any(k in t for k in FAMILIES[fam]["kw"]):
            return fam
    return None
MIX = {"so_minhas": "Só as minhas", "completar": "Completar as curtas com novidades", "misturar": "Misturar novidades"}
DISC_AFF = {"completar": 0.25, "misturar": 0.85}
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
    for tid, r in old.items():          # descobertas (estilos favoritos) continuam enquanto o estilo estiver escolhido
        if r.get("src") == "descoberta" and tid not in found and r.get("genre") in (settings().get("genres") or []):
            found[tid] = r
    seen: dict[str, int] = {}
    try:                                # estilos que a pessoa já ouve (sugestão na tela; o Spotify pode não mandar)
        for a in (call("GET", "/me/top/artists", params={"time_range": "medium_term", "limit": 30}) or {}).get("items") or []:
            for g in a.get("genres") or []:
                for gid, (_, qs) in GENRES.items():
                    if any(q in g.lower() for q in qs):
                        seen[gid] = seen.get(gid, 0) + 1
    except MusicError:
        pass
    lib.update(tracks=found, updated=time.time(), genres_seen=seen)
    _save_lib(lib)
    return lib


def _search(q: str, off: int) -> tuple[int, list[dict]]:
    """Busca de músicas no Spotify (até 10 por vez desde 2026). Sem 'market': ele pede uma permissão a mais."""
    try:
        d = call("GET", "/search", params={"q": q, "type": "track", "limit": 10, "offset": off}) or {}
        return 200, ((d.get("tracks") or {}).get("items") or [])
    except MusicError as e:
        return getattr(e, "status", 0) or -1, []


def _recco_similar(seeds: list[str], size: int = 40) -> list[dict]:
    """Músicas parecidas com as da pessoa (ReccoBeats, a partir de ids do Spotify)."""
    if not seeds:
        return []
    st, d = _http("GET", "https://api.reccobeats.com/v1/track/recommendation?" + urllib.parse.urlencode({"seeds": ",".join(seeds[:5]), "size": size}), timeout=10)
    _DIAG["recco_rec"] = st
    out = []
    for c in ((d or {}).get("content") or []) if st == 200 and isinstance(d, dict) else []:
        href = c.get("href") or ""
        sid = href.rstrip("/").split("/")[-1] if "spotify.com" in href else None
        name = c.get("trackTitle") or c.get("name")
        if sid and name:
            out.append({"id": sid, "name": name, "uri": f"spotify:track:{sid}", "type": "track", "duration_ms": c.get("durationMs"),
                        "artists": [{"name": a.get("name", "")} for a in c.get("artists") or []], "external_ids": {"isrc": c.get("isrc")}})
    return out


def _genre_queries(g: str) -> list[str]:
    if g.startswith("custom:"):
        return [g[7:]]
    return GENRES[g][1] if g in GENRES else []


def discover(budget_s: float = 8.0, cad: int | None = None) -> int:
    """Músicas novas dos estilos escolhidos. Para cada estilo: busca do Spotify por gênero; se não vier nada, pelo
    nome do estilo; e "parecidas com as suas" pela ReccoBeats. Só completam/misturam as playlists (as da pessoa vêm
    antes). Estilo que trouxe músicas só é buscado de novo em 7 dias; o que veio vazio tenta de novo no próximo."""
    s = settings()
    genres = [g for g in s.get("genres") or [] if g in GENRES or g.startswith("custom:") or g == "parecidas"]
    if not genres or s.get("mix") == "so_minhas":
        return 0
    lib = _lib()
    tracks = lib.setdefault("tracks", {})
    done = lib.setdefault("disc_at", {})
    diag = lib.setdefault("disc_diag", {})
    t0, added = time.monotonic(), 0

    def add(t: dict, g: str) -> int:
        if t and t.get("id") and t["id"] not in tracks and t.get("uri"):
            tracks[t["id"]] = {**_track_row(t, DISC_AFF["completar"], "descoberta"), "genre": g}
            return 1
        return 0

    for g in genres:
        recent = time.time() - (done.get(g) or 0) < 7 * 86400 and (diag.get(g) or {}).get("found")
        if recent or time.monotonic() - t0 > budget_s:   # vazio (ou da busca antiga) tenta de novo
            continue
        found, how, last = 0, None, None
        if g == "parecidas":
            own = sorted((r for r in tracks.values() if r.get("src") != "descoberta" and r.get("bpm")), key=lambda r: -r.get("aff", 0))
            fast = [r for r in own if (r.get("bpm") or 0) >= 150][:3] + own[:2]
            seeds = [r["uri"].split(":")[-1] for r in fast if r.get("uri")]
            for t in _safe(_recco_similar, seeds) or []:
                found += add(t, g)
            how, last = "parecidas", _DIAG.get("recco_rec")
        else:
            for mode in ("genre", "texto"):
                for q in _genre_queries(g):
                    for off in (0, 10, 20, 30):
                        if time.monotonic() - t0 > budget_s:
                            break
                        last, items = _search(f'genre:"{q}"' if mode == "genre" else q, off)
                        for t in items:
                            found += add(t, g)
                        if len(items) < 10:
                            break
                if found >= 15:
                    how = mode
                    break
                how = mode
        diag[g] = {"found": found, "how": how, "status": last, "at": int(time.time())}
        added += found
        if found:
            done[g] = time.time()
    _save_lib(lib)
    return added


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


STYLE_V = 1   # versão da classificação de estilo: subir faz classificar de novo


def _deezer_genres(row: dict, albums: dict) -> list[str]:
    """Gêneros do álbum da música no Deezer (pelo ISRC; sem ele, pela busca de artista + nome)."""
    t = None
    if row.get("isrc"):
        st, d = _http("GET", f"https://api.deezer.com/track/isrc:{urllib.parse.quote(row['isrc'])}", timeout=8)
        if st == 200 and isinstance(d, dict) and not d.get("error"):
            t = d
    if t is None:
        q = f'artist:"{(row.get("artist") or "").split(",")[0]}" track:"{row.get("name") or ""}"'
        st, d = _http("GET", "https://api.deezer.com/search?" + urllib.parse.urlencode({"q": q, "limit": 1}), timeout=8)
        t = ((d or {}).get("data") or [None])[0] if st == 200 and isinstance(d, dict) else None
    alb = str(((t or {}).get("album") or {}).get("id") or "")
    if not alb:
        return []
    if alb not in albums:
        st, a = _http("GET", f"https://api.deezer.com/album/{alb}", timeout=8)
        albums[alb] = [g.get("name") for g in ((a or {}).get("genres") or {}).get("data") or [] if g.get("name")] \
            if st == 200 and isinstance(a, dict) else []
    return albums[alb]


FAM_PRIORITY = ("pesada", "rap", "br", "rock", "calma", "eletronica", "pop")   # do mais específico ao mais genérico


def _fam_from(row: dict, genres: list[str]) -> str | None:
    """Família pelos gêneros do álbum: vale a mais específica. "Techno/House" do Deezer junta as duas coisas: com
    140 bpm ou mais é eletrônica pesada (hard techno), abaixo é house/dance."""
    found = set()
    for g in genres:
        gl = (g or "").lower()
        if "techno" in gl and "house" in gl:
            found.add("pesada" if (row.get("bpm") or 0) >= 140 else "eletronica")
            continue
        f = family_of(g)
        if f:
            found.add(f)
    return next((f for f in FAM_PRIORITY if f in found), None)


def classify(budget_s: float = 6.0) -> int:
    """Põe cada música numa família de estilo. Descobertas já vêm com o estilo; as suas, pelo gênero do álbum no Deezer.
    Devolve quantas ainda faltam."""
    lib = _lib()
    tracks = lib.get("tracks") or {}
    if lib.get("style_v") != STYLE_V:
        for r in tracks.values():
            r.pop("fam", None)
            r.pop("fam_tried", None)
        lib["style_v"] = STYLE_V
    albums = lib.setdefault("albums", {})
    todo = []
    for r in tracks.values():
        if r.get("fam") or r.get("fam_tried") or not r.get("bpm"):
            continue
        g = r.get("genre") or ""
        f = GENRE_FAM.get(g) or (family_of(g[7:]) if g.startswith("custom:") else None)
        if f:
            r["fam"] = f
        else:
            todo.append(r)
    t0 = time.monotonic()
    while todo and time.monotonic() - t0 < budget_s:
        chunk, todo = todo[:24], todo[24:]
        with ThreadPoolExecutor(max_workers=8) as ex:
            res = list(ex.map(lambda r: _safe(_deezer_genres, r, albums) or [], chunk))
        for r, gs in zip(chunk, res):
            r["fam_tried"] = True
            f = _fam_from(r, gs)
            if f:
                r["fam"] = f
    if len(albums) > 3000:                   # cache de álbuns não cresce para sempre
        lib["albums"] = dict(list(albums.items())[-2000:])
    _save_lib(lib)
    return sum(1 for r in tracks.values() if r.get("bpm") and not r.get("fam") and not r.get("fam_tried"))


def styles_for(mode: str, cad: int, lib: dict | None = None) -> list[dict]:
    """Famílias de estilo que viram playlist neste momento: as que têm músicas suficientes, das maiores para as menores."""
    lib = lib or _lib()
    rows = pick(mode, cad, lib, n=10_000)
    cnt: dict[str, int] = {}
    for r in rows:
        if r.get("fam"):
            cnt[r["fam"]] = cnt.get(r["fam"], 0) + 1
    fams = sorted((f for f, n in cnt.items() if n >= STYLE_MIN), key=lambda f: -cnt[f])[:STYLE_MAX_PER_MODE]
    return [{"fam": f, "label": FAMILIES[f]["label"], "n": min(cnt[f], MAX_PER_LIST)} for f in fams]


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ escolher ----

def _cands(bpm: float, mode: str, cad: int) -> list[float]:
    """BPMs que a música "vale": corrida aceita metade/dobro (um passo por batida ou a cada duas); nas outras,
    só corrige o detector que às vezes dobra o BPM de música lenta (acima de 185)."""
    if "cad" in MODES[mode] or "cad_lo" in MODES[mode]:
        return [bpm, bpm * 2, bpm / 2]
    return [bpm] + ([bpm / 2] if bpm > 185 else [])


def _fit(bpm: float, mode: str, cad: int) -> float | None:
    """0 = no alvo; None = não serve."""
    m = MODES[mode]
    if "cad_lo" in m:
        lo, hi = cad + m["cad_lo"], cad + m["cad_hi"]
        ok = [abs(b - (lo + hi) / 2) / hi * 0.5 for b in _cands(bpm, mode, cad) if lo <= b <= hi]
        return min(ok) if ok else None
    if "cad" in m:
        target = cad + m["cad"]
        best = min(abs(b - target) / target for b in _cands(bpm, mode, cad))
        return best if best <= TOL else None
    mid = (m["lo"] + m["hi"]) / 2
    ok = [abs(b - mid) / mid * 0.5 for b in _cands(bpm, mode, cad) if m["lo"] <= b <= m["hi"]]
    return min(ok) if ok else None


def _eff_bpm(bpm: float, mode: str, cad: int) -> float:
    m = MODES[mode]
    target = (cad + m["cad"] if "cad" in m else cad + (m["cad_lo"] + m["cad_hi"]) / 2 if "cad_lo" in m
              else (m["lo"] + m["hi"]) / 2)
    return min(_cands(bpm, mode, cad), key=lambda b: abs(b - target))


RULES_V = 4   # muda quando as faixas mudam: a tela refaz as playlists sozinha (4: playlists por estilo)


def _range_txt(m: dict, cad: int) -> str:
    if "cad_lo" in m:
        return f"{cad + m['cad_lo']}–{cad + m['cad_hi']} bpm"
    if "cad" in m:
        return f"{cad + m['cad']} passos/min"
    return f"{m['lo']}–{m['hi']} bpm"


def pick(mode: str, cad: int, lib: dict | None = None, n: int = MAX_PER_LIST, fam: str | None = None) -> list[dict]:
    tracks = (lib or _lib()).get("tracks") or {}
    st = settings()
    mix, genres = st.get("mix") or "completar", set(st.get("genres") or [])
    blocked = set(st.get("blocked") or [])
    lift = performers() if mode in ("gas", "musculacao", "tiros") else {}
    rows = []
    for tid, r in tracks.items():
        if not r.get("bpm"):
            continue
        if r.get("src") == "descoberta":
            if mix == "so_minhas" or r.get("genre") not in genres:   # estilo desmarcado: sai
                continue
            r = {**r, "aff": DISC_AFF.get(mix, 0.25)}
        f = _fit(r["bpm"], mode, cad)
        if f is None:
            continue
        if tid in blocked:
            continue
        if fam and r.get("fam") != fam:
            continue
        energy = r.get("energy")
        if mode == "calma" and energy is not None and energy > 0.75:
            continue
        if mode in ("gas", "tiros", "musculacao") and energy is not None and energy < 0.45:
            continue
        score = r.get("aff", 1) * 1.0 - f * 6 + ((energy or 0.6) - 0.6) * (2 if mode in ("gas", "tiros", "musculacao") else -2 if mode == "calma" else 0)
        if tid in lift:                       # música com que você rende mais: sobe (até +1,5)
            score += min(1.5, lift[tid]["gain"] * 25)
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


def _fill(pid: str | None, name: str, desc: str, rows: list[dict]) -> str | None:
    if not pid:
        pid = _create(name, desc).get("id")
        if not pid:
            return None
    try:
        _set_items(pid, [r["uri"] for r in rows])
    except MusicError as e:
        if getattr(e, "status", 0) != 404:
            raise
        pid = _create(name, desc).get("id")   # a pessoa apagou a playlist no Spotify: cria de novo
        _set_items(pid, [r["uri"] for r in rows])
    return pid


def build_playlists(cad: int, budget_s: float = 14.0) -> dict:
    """Cria/atualiza as playlists HAX com as músicas da pessoa no BPM de cada momento: a mistura de cada momento e uma
    por família de estilo (quando tem músicas suficientes). Para não passar do tempo do servidor, as de estilo que não
    couberem ficam para a próxima chamada ("done": False)."""
    t0 = time.monotonic()
    lib = _lib()
    s = settings()
    pls = s.get("playlists") or {}
    sty = s.get("style_playlists") or {}
    out, styles_out, done = {}, {}, True
    for mode, m in MODES.items():
        rows = pick(mode, cad, lib)
        if len(rows) < 3:
            out[mode] = {"n": len(rows), "id": (pls.get(mode) or {}).get("id"), "few": True}
            continue
        pid = _fill((pls.get(mode) or {}).get("id"), f"HAX · {m['label']}",
                    f"Feita pelo app HAX com as músicas que você mais ouve: {m['desc']} · {_range_txt(m, cad)}.", rows)
        if pid:
            out[mode] = {"id": pid, "n": len(rows), "url": f"https://open.spotify.com/playlist/{pid}"}
    for mode, m in MODES.items():
        for st in styles_for(mode, cad, lib):
            key = f"{mode}|{st['fam']}"
            old = sty.get(key) or ({"id": (s.get("style_ids") or {}).get(key)} if (s.get("style_ids") or {}).get(key) else {})
            if time.monotonic() - t0 > budget_s:
                done = False
                if old.get("id"):
                    styles_out[key] = old
                continue
            rows = pick(mode, cad, lib, fam=st["fam"])
            pid = _fill(old.get("id"), f"HAX · {m['label']} · {st['label']}",
                        f"Feita pelo app HAX: {m['desc']}, só {st['label'].lower()} · {_range_txt(m, cad)}.", rows)
            if pid:
                styles_out[key] = {"id": pid, "n": len(rows), "fam": st["fam"], "mode": mode,
                                   "url": f"https://open.spotify.com/playlist/{pid}"}
    # estilo que perdeu músicas continua guardado (a playlist não é apagada no Spotify), só sai da lista do app
    s["playlists"] = {k: v for k, v in out.items() if v.get("id")}
    s["style_playlists"] = styles_out
    s["style_ids"] = {**(s.get("style_ids") or {}), **{k: v["id"] for k, v in styles_out.items()}}
    if done:
        s["built_cad"] = cad
        s["built"] = time.time()
        s["rules_v"] = RULES_V
    save_settings(s)
    return {"modes": out, "styles": styles_out, "done": done}


# ------------------------------------------------------------------ tocar ----

def play(mode: str, fam: str | None = None) -> dict:
    """Toca a playlist do momento (ou a do estilo escolhido). Spotify Premium, com o app do Spotify aberto em algum aparelho."""
    s0 = settings()
    pl = (s0.get("style_playlists") or {}).get(f"{mode}|{fam}") if fam else None
    pl = pl or (s0.get("playlists") or {}).get(mode)
    if not pl:
        raise MusicError("Playlist ainda não montada: toque em 'Atualizar minhas playlists'.")
    if fam:
        s0.setdefault("style_pref", {})[mode] = fam
        save_settings(s0)
    return _play_playlist(pl["id"])


def _play_playlist(pid: str) -> dict:
    pl = {"id": pid}
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


# ------------------------------------------------------------------ treino avulso: músicas na ordem das fases ----
SESSION_KEY = "session"   # uma playlist só ("HAX · Treino avulso"), refeita a cada treino


SEQ_FALLBACK = {"calma": "aquecimento", "aquecimento": "musculacao", "tiros": "gas", "gas": "tiros", "corrida_ritmo": "corrida_leve",
                "corrida_leve": "corrida_ritmo", "musculacao": "aquecimento"}


def sequence(segments: list[dict], cad: int, fam: str | None = None, lib: dict | None = None) -> list[dict]:
    """Enche cada fase do treino com músicas do momento certo (no estilo escolhido, se tiver o bastante), na ordem.
    A música que passa do fim de uma fase continua tocando: a próxima fase começa na música seguinte."""
    lib = lib or _lib()
    used: set[str] = set()
    out: list[dict] = []
    t, end = 0.0, 0.0
    for i, sg in enumerate(segments):
        end += sg["dur_s"]
        cands = pick(sg["mode"], cad, lib, n=200, fam=fam) if fam else []
        if len([c for c in cands if c["id"] not in used]) < 4:
            cands = cands + [c for c in pick(sg["mode"], cad, lib, n=200) if c not in cands]
        if len([c for c in cands if c["id"] not in used]) < 2:      # sem músicas nessa faixa: a mais parecida
            cands = cands + [c for c in pick(SEQ_FALLBACK[sg["mode"]], cad, lib, n=200) if c not in cands]
        for r in cands:
            if t >= end - 45:          # falta menos de 45 s na fase: a próxima música já é da fase seguinte
                break
            if r["id"] in used:
                continue
            used.add(r["id"])
            dur = (r.get("ms") or 210_000) / 1000
            out.append({"seg": i, "start_s": int(t), "id": r["id"], "uri": r["uri"], "name": r["name"], "artist": r["artist"],
                        "bpm": r.get("eff") or round(r.get("bpm") or 0), "fam": r.get("fam"), "ms": r.get("ms")})
            t += dur
    return out


def session_playlist(title: str, tracks: list[dict], play_now: bool = True) -> dict:
    """Põe as músicas do treino avulso, na ordem, na playlist "HAX · Treino avulso" e dá play (Premium)."""
    if not tracks:
        raise MusicError("Ainda não há músicas suas com BPM para montar a sequência.")
    s = settings()
    old = (s.get("playlists_extra") or {}).get(SESSION_KEY)
    pid = _fill(old, "HAX · Treino avulso", f"Feita pelo app HAX para o treino: {title}. Toque em ordem (sem aleatório).", tracks)
    s = settings()
    s.setdefault("playlists_extra", {})[SESSION_KEY] = pid
    save_settings(s)
    url = f"https://open.spotify.com/playlist/{pid}"
    if not play_now:
        return {"ok": True, "url": url, "played": False}
    try:
        _play_playlist(pid)
        return {"ok": True, "url": url, "played": True}
    except MusicError as e:
        return {"ok": True, "url": url, "played": False, "why": str(e)}


def boost(cad: int, calmer: bool = False) -> dict:
    """⚡ Mais gás: a música mais rápida que a pessoa gosta, agora (fila + pular). calmer=True: o contrário."""
    lib = _lib()
    rows = pick("calma" if calmer else "gas", cad, lib, n=12)
    if not rows:
        raise MusicError("Ainda não achei músicas suas nessa faixa de BPM.")
    recent = set(settings().get("boosted") or [])
    lift = performers() if not calmer else {}
    rows = sorted(rows, key=lambda x: -(lift.get(x["id"], {}).get("gain", 0))) if lift else rows
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
    picks = {m: pick(m, cad, lib) for m in MODES} if tracks else {}
    counts = {m: len(v) for m, v in picks.items()}
    news = {m: sum(1 for r in v if r.get("src") == "descoberta") for m, v in picks.items()}
    premium = s.get("product") == "premium" or (not s.get("product") and s.get("premium_ok") is not False)
    return {"configured": bool(client_id()), "connected": connected(), "name": s.get("name"), "premium": premium,
            "product": s.get("product"), "tracks": len(tracks), "with_bpm": sum(1 for r in tracks.values() if r.get("bpm")),
            "pending": sum(1 for r in tracks.values() if r.get("bpm") is None and not r.get("tried")),
            "cadence": cad, "cadence_manual": bool(s.get("cadence")), "auto": s.get("auto", True),
            "modes": [{"id": k, "label": m["label"], "desc": m["desc"],
                       "range": _range_txt(m, cad),
                       "n": counts.get(k, 0), "new": news.get(k, 0), **({"url": f"https://open.spotify.com/playlist/{p['id']}"} if (p := (s.get("playlists") or {}).get(k)) else {}),
                       "styles": [{"fam": v["fam"], "label": FAMILIES[v["fam"]]["label"], "n": v["n"], "url": v["url"]}
                                  for v in (s.get("style_playlists") or {}).values() if v.get("mode") == k and v.get("fam") in FAMILIES],
                       "style_pref": (s.get("style_pref") or {}).get(k)}
                      for k, m in MODES.items()],
            "styled": sum(1 for r in tracks.values() if r.get("fam")),
            "families": [{"id": k, "label": v["label"]} for k, v in FAMILIES.items()],
            "built": s.get("built"), "updated": lib.get("updated"),
            "genres": [{"id": "parecidas", "label": "Parecidas com as que você ouve", "on": "parecidas" in (s.get("genres") or []), "seen": 0, "group": "voce"}]
                      + [{"id": g, "label": lab, "on": g in (s.get("genres") or []), "seen": (lib.get("genres_seen") or {}).get(g, 0),
                          "group": "nicho" if list(GENRES).index(g) >= list(GENRES).index(NICHE_FROM) else "popular"}
                         for g, (lab, _) in GENRES.items()]
                      + [{"id": g, "label": g[7:], "on": True, "seen": 0, "group": "livre"} for g in s.get("genres") or [] if g.startswith("custom:")],
            "disc_diag": lib.get("disc_diag") or {},
            "mix": s.get("mix") or "completar", "mix_options": [{"id": k, "label": v} for k, v in MIX.items()],
            "discovered": sum(1 for r in tracks.values() if r.get("src") == "descoberta"),
            "stale": bool(s.get("built")) and (s.get("rules_v") != RULES_V or s.get("built_cad") != cad),
            "blocked": [{"id": t, "name": (tracks.get(t) or {}).get("name") or "música", "artist": (tracks.get(t) or {}).get("artist")}
                        for t in (s.get("blocked") or [])[:30]],
            "perf": perf_summary()}


def search_url(mode: str, cad: int) -> str:
    """Sem conectar: abre a busca do Spotify com o tipo de música do momento."""
    m = MODES[mode]
    q = {"aquecimento": "aquecimento treino", "musculacao": "treino academia 130 bpm", "gas": "treino pesado 160 bpm",
         "corrida_leve": f"corrida {cad} bpm", "corrida_ritmo": f"corrida {cad + 8} bpm", "tiros": f"corrida {cad + 18} bpm",
         "calma": "alongamento relaxar"}[mode]
    return "https://open.spotify.com/search/" + urllib.parse.quote(q) if m else "https://open.spotify.com"


# ------------------------------------------------------------------ 👎 não tocar mais ----

def block(track_id: str | None = None) -> dict:
    """Tira a música de todas as playlists HAX para sempre (a que está tocando, se não disser qual) e pula."""
    tid = track_id
    if not tid:
        d = call("GET", "/me/player/currently-playing")
        tid = ((d or {}).get("item") or {}).get("id")
    if not tid:
        raise MusicError("Nada tocando no Spotify agora.")
    s = settings()
    s["blocked"] = list(dict.fromkeys([tid] + (s.get("blocked") or [])))[:500]
    save_settings(s)
    name = ((_lib().get("tracks") or {}).get(tid) or {}).get("name")
    try:
        call("POST", "/me/player/next")
    except MusicError:
        pass
    return {"ok": True, "id": tid, "name": name}


def unblock(track_id: str) -> None:
    s = settings()
    s["blocked"] = [x for x in s.get("blocked") or [] if x != track_id]
    save_settings(s)


# ------------------------------------------------------------------ trilha do treino ----

def soundtrack(start_local: str, minutes: float, tz) -> list[dict]:
    """Músicas que tocaram durante o treino (o Spotify guarda as últimas 50 com o horário)."""
    from datetime import datetime, timedelta, timezone
    try:
        t0 = datetime.fromisoformat(str(start_local)[:19]).replace(tzinfo=tz)
    except ValueError:
        return []
    t1 = t0 + timedelta(minutes=max(10.0, float(minutes or 60)) + 5)
    after = int((t0 - timedelta(minutes=5)).astimezone(timezone.utc).timestamp() * 1000)
    d = call("GET", "/me/player/recently-played", params={"limit": 50, "after": after}) or {}
    out = []
    for it in d.get("items") or []:
        try:
            at = datetime.fromisoformat(str(it.get("played_at")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if t0 - timedelta(minutes=5) <= at <= t1:
            t = it.get("track") or {}
            out.append({"name": t.get("name"), "artist": ", ".join(a.get("name", "") for a in t.get("artists") or []),
                        "at": at.isoformat()})
    out.sort(key=lambda x: x["at"])
    return [{"name": x["name"], "artist": x["artist"]} for x in out if x["name"]][:30]


# ------------------------------------------------------------------ músicas que rendem mais ----

PERF_KEY = "music_perf"


def mark_set(exercise_id: str, reps: int, load: float | None, session_key: str) -> dict | None:
    """Anota a música que tocava nesta série (para descobrir com quais você rende mais)."""
    if not reps:
        return None
    d = call("GET", "/me/player/currently-playing")
    item = (d or {}).get("item") if (d or {}).get("is_playing") else None
    if not item or not item.get("id"):
        return None
    rows = json.loads(db.kv_get(PERF_KEY) or "[]")
    rows.append({"t": item["id"], "e": exercise_id, "r": int(reps), "l": float(load or 0), "k": str(session_key)[:40],
                 "d": time.strftime("%Y-%m-%d")})
    db.kv_set(PERF_KEY, json.dumps(rows[-3000:]))
    return {"id": item["id"], "name": item.get("name")}


def _set_score(r: dict) -> float:
    """Força da série: 1RM estimado (Epley) com carga; só repetições sem carga."""
    return r["l"] * (1 + r["r"] / 30) if r["l"] else float(r["r"])


def performers(min_sets: int = 4) -> dict[str, dict]:
    """Por música: quanto as séries feitas com ela rendem acima do normal daquele exercício naquele treino
    (compara com a mediana das outras séries do mesmo exercício no mesmo dia: carga, cansaço e dia já ficam de fora)."""
    rows = json.loads(db.kv_get(PERF_KEY) or "[]")
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r["k"], r["e"]), []).append(r)
    rel: dict[str, list[float]] = {}
    for g in groups.values():
        if len(g) < 2:
            continue
        sc = sorted(_set_score(x) for x in g)
        med = sc[len(sc) // 2] if len(sc) % 2 else (sc[len(sc) // 2 - 1] + sc[len(sc) // 2]) / 2
        if med <= 0:
            continue
        for x in g:
            rel.setdefault(x["t"], []).append(_set_score(x) / med - 1)
    out = {}
    for t, v in rel.items():
        if len(v) >= min_sets:
            gain = sum(v) / len(v)
            if gain > 0.01:
                out[t] = {"gain": round(gain, 4), "sets": len(v)}
    return out


def perf_summary() -> dict:
    rows = json.loads(db.kv_get(PERF_KEY) or "[]")
    lib = (_lib().get("tracks") or {})
    best = sorted(performers().items(), key=lambda kv: -kv[1]["gain"])[:5]
    return {"sets": len(rows), "days": len({r["d"] for r in rows}),
            "top": [{"id": t, "name": (lib.get(t) or {}).get("name") or "música", "artist": (lib.get(t) or {}).get("artist"),
                     "gain_pct": round(v["gain"] * 100, 1), "sets": v["sets"]} for t, v in best]}
