"""Galeria de aparelhos: foto, vídeo de alguém usando, o que dá para fazer e dicas.

Dados em data/machine_gallery.json (pesquisa + conferência). Vídeos são do YouTube (incorporados pelo
player oficial, sem copiar nada) e fotos são de licença aberta do Wikimedia Commons, sempre com crédito.
`check_media` confere no ar quais vídeos existem/aceitam incorporação e quais fotos respondem.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "machine_gallery.json"
YT_ID = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})")
CATEGORIES = [("pernas", "Pernas"), ("gluteo", "Glúteo"), ("costas", "Costas"), ("peito", "Peito"), ("ombros", "Ombros"),
              ("bracos", "Braços"), ("core", "Core"), ("cardio", "Cardio"), ("funcional", "Funcional")]


def yt_id(url: str) -> str | None:
    m = YT_ID.search(url or "")
    return m.group(1) if m else None


@lru_cache(maxsize=1)
def _file_load() -> list[dict]:
    if not DATA.exists():
        return []
    return json.loads(DATA.read_text())


def load() -> list[dict]:
    """Aparelhos da pesquisa + os aprovados na área do desenvolvedor (fotos da comunidade)."""
    base = _file_load()
    try:
        from . import my_gym
        customs, extra = my_gym.custom_machines(), my_gym.extra_photos()
    except Exception:  # noqa: BLE001 - sem banco (scripts/testes): só a pesquisa
        return base
    if extra:
        base = [{**m, "photos": extra[m["machine_id"]] + (m.get("photos") or [])} if m["machine_id"] in extra else m for m in base]
    return base + customs


load.cache_clear = _file_load.cache_clear  # compatível com quem limpava o cache do arquivo


def get(machine_id: str) -> dict | None:
    return next((m for m in load() if m["machine_id"] == machine_id), None)


def by_exercise() -> dict[str, str]:
    """exercício do catálogo → aparelho onde ele é feito (para o detalhe do exercício mostrar o vídeo).

    Só entra aparelho que tem vídeo ou foto para mostrar; entre dois, vale o que cita o exercício na lista
    "o que dá para fazer" (o banco onde se faz supino com halter não é "o aparelho" do afundo com halteres)."""
    out: dict[str, str] = {}
    weak: dict[str, str] = {}
    for m in load():
        if not (_videos(m) or _photos(m)):
            continue
        direct = {e.get("exercise_id") for e in m.get("exercises") or [] if e.get("exercise_id")}
        for eid in m.get("existing_exercise_ids") or []:
            (out if eid in direct else weak).setdefault(eid, m["machine_id"])
    return {**weak, **out}


by_exercise.cache_clear = lambda: None  # sem cache: os aparelhos da comunidade entram na hora


# resultado da conferência no ar (YouTube/Commons), guardado no banco para todas as contas (user -1)
GLOBAL_USER = -1
_STATUS: dict = {"at": 0.0, "map": {}}


def status_map() -> dict:
    import time
    if time.time() - _STATUS["at"] > 600:
        try:
            from . import db
            _STATUS["map"] = json.loads(db.kv_get("media_status", user_id=GLOBAL_USER) or "{}")
        except Exception:  # noqa: BLE001 - sem banco: usa o que está no arquivo
            _STATUS["map"] = {}
        _STATUS["at"] = time.time()
    return _STATUS["map"]


def save_status(results: dict) -> int:
    """Junta a conferência nova ao que já está no banco (chave = URL)."""
    from . import db
    cur = json.loads(db.kv_get("media_status", user_id=GLOBAL_USER) or "{}")
    n = 0
    for r in results.values():
        for item in (r.get("videos") or []) + (r.get("photos") or []):
            if item.get("status") in ("ok", "no_embed", "missing"):
                cur[item["url"]] = {k: item.get(k) for k in ("status", "title", "channel") if item.get(k)}
                n += 1
    db.kv_set("media_status", json.dumps(cur, ensure_ascii=False), user_id=GLOBAL_USER)
    _STATUS["at"] = 0.0
    return n


def _st(item: dict) -> dict:
    """Item com o status conferido (banco) por cima do que veio no arquivo."""
    return {**item, **status_map().get(item.get("url", ""), {})}


def _videos(m: dict) -> list[dict]:
    """Vídeos conferidos (ok) primeiro; os que não aceitam incorporação viram só link."""
    vids = []
    for v in m.get("videos") or []:
        v = _st(v)
        vid = v.get("id") or yt_id(v.get("url", ""))
        if not vid or v.get("status") in ("missing", "pending", "unknown"):
            continue
        vids.append({**v, "id": vid, "embed": v.get("status") != "no_embed"})
    return vids


def _photos(m: dict) -> list[dict]:
    return [p for p in (_st(p) for p in m.get("photos") or []) if p.get("url") and p.get("status") == "ok"]


def summary(m: dict) -> dict:
    vids, photos = _videos(m), _photos(m)
    thumb = photos[0]["url"] if photos else (f"https://i.ytimg.com/vi/{vids[0]['id']}/hqdefault.jpg" if vids else None)
    return {"id": m["machine_id"], "name": m["name"], "name_en": m.get("name_en"), "category": m.get("category"),
            "is_new": bool(m.get("is_new")), "community": bool(m.get("community")), "availability_br": m.get("availability_br"), "thumb": thumb,
            "thumb_kind": "photo" if photos else "video" if vids else None, "has_video": bool(vids),
            "n_exercises": len(m.get("exercises") or []), "from_photo": bool(m.get("from_photo")),
            # busca também pelos nomes que as pessoas usam ("leg press 90") e pela marca ("hammer strength", "total health")
            "_search": _norm(" ".join([m["name"], m.get("name_en") or "", " ".join(m.get("aliases") or []),
                                       " ".join(e.get("name", "") for e in m.get("exercises") or [])])),
            "_brands": _norm(" ".join(m.get("brands") or []))}


def detail(m: dict) -> dict:
    out = {k: v for k, v in m.items() if k not in ("videos", "photos", "exercises")}
    out["videos"], out["photos"] = _videos(m), _photos(m)
    exs = []
    for e in m.get("exercises") or []:
        x = {k: v for k, v in e.items() if k != "video"}
        v = _st(e.get("video") or {})
        vid = yt_id(v.get("url", ""))
        if vid and v.get("status") not in ("missing", "pending", "unknown"):
            x["video"] = {"id": vid, "embed": v.get("status") != "no_embed", "channel": v.get("channel"), "title": v.get("title")}
        exs.append(x)
    out["exercises"] = exs
    out["id"] = m["machine_id"]
    return out


def _norm(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def search(q: str = "", category: str | None = None) -> list[dict]:
    items = [summary(m) for m in load()]
    if category:
        items = [i for i in items if i["category"] == category]
    if q:
        items = [i for i in items if matches(q, i["_search"], i["_brands"])]
    order = {"comum": 0, "média": 1, "rara": 2}
    items.sort(key=lambda i: (not i["has_video"], order.get(i["availability_br"] or "", 3), i["name"]))
    for i in items:
        i["s"] = i.pop("_search", "")  # a tela filtra por aqui: nome, apelidos ("leg press 90") e exercícios…
        i["b"] = i.pop("_brands", "")  # …e marcas (só por começo de palavra)
    return items


def matches(q: str, names: str, brands: str) -> bool:
    """Todas as palavras da busca aparecem no nome/apelidos/exercícios, ou são começo de uma palavra da marca.
    Pelo menos uma palavra tem de bater no nome ou ser uma marca inteira ("total health", "matrix")."""
    tok = lambda s: re.findall(r"[a-z0-9]+", s)  # noqa: E731
    words, nw, bw = tok(_norm(q)), tok(names), tok(brands)
    if not words:
        return True
    # por começo de palavra: "press" não casa com "descompressão", "leg" não casa com a marca "Legend" no meio da busca
    in_name = [any(n.startswith(w) for n in nw) for w in words]
    in_brand = [any(b.startswith(w) for b in bw) for w in words]
    if not all(n or b for n, b in zip(in_name, in_brand)):
        return False
    phrase = " ".join(words)
    return any(in_name) or (len(phrase) >= 4 and f" {phrase}" in " " + " ".join(bw))


# ------------------------------------------------------------------ conferência no ar ----

def _fetch(url: str, method: str = "GET", timeout: float = 6.0) -> tuple[int, bytes]:
    req = urllib.request.Request(url, method=method, headers={"User-Agent": "HybridLab/1.0 (media check)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - só URLs do nosso próprio arquivo de dados
            return r.status, (r.read(200_000) if method == "GET" else b"")
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:  # noqa: BLE001 - rede: conta como "não conferido"
        return 0, b""


def _check_video(url: str) -> dict:
    vid = yt_id(url)
    if not vid:
        return {"url": url, "status": "missing"}
    code, body = _fetch("https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(f"https://www.youtube.com/watch?v={vid}"))
    if code == 200:
        try:
            j = json.loads(body)
        except ValueError:
            j = {}
        return {"url": url, "id": vid, "status": "ok", "title": j.get("title"), "channel": j.get("author_name")}
    return {"url": url, "id": vid, "status": "no_embed" if code in (401, 403) else "missing" if code in (400, 404) else "unknown", "code": code}


def _check_photo(url: str) -> dict:
    code, _ = _fetch(url, method="HEAD")
    if code == 405:
        code, _ = _fetch(url)
    return {"url": url, "status": "ok" if code == 200 else "missing" if code in (404, 410) else "unknown", "code": code}


def check_media(candidates: list[dict], workers: int = 16) -> dict:
    """candidates: [{machine_id, videos:[url], photos:[url]}] → resultado por aparelho (roda em paralelo)."""
    jobs = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for c in candidates:
            for u in c.get("videos") or []:
                jobs.append((c["machine_id"], "videos", ex.submit(_check_video, u)))
            for u in c.get("photos") or []:
                jobs.append((c["machine_id"], "photos", ex.submit(_check_photo, u)))
        out: dict = {}
        for mid, kind, fut in jobs:
            out.setdefault(mid, {"videos": [], "photos": []})[kind].append(fut.result())
    return out


def category_name(key: str) -> str:
    return dict(CATEGORIES).get(key, key)


__all__ = ["load", "get", "search", "summary", "detail", "by_exercise", "check_media", "CATEGORIES", "yt_id"]
