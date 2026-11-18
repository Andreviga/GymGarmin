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
def load() -> list[dict]:
    if not DATA.exists():
        return []
    return json.loads(DATA.read_text())


def get(machine_id: str) -> dict | None:
    return next((m for m in load() if m["machine_id"] == machine_id), None)


@lru_cache(maxsize=1)
def by_exercise() -> dict[str, str]:
    """exercício do catálogo → aparelho onde ele é feito (para o detalhe do exercício mostrar o vídeo)."""
    out: dict[str, str] = {}
    for m in load():
        for eid in m.get("existing_exercise_ids") or []:
            out.setdefault(eid, m["machine_id"])
    return out


def _videos(m: dict) -> list[dict]:
    """Vídeos conferidos (ok) primeiro; os que não aceitam incorporação viram só link."""
    vids = []
    for v in m.get("videos") or []:
        vid = v.get("id") or yt_id(v.get("url", ""))
        if not vid or v.get("status") in ("missing",):
            continue
        vids.append({**v, "id": vid, "embed": v.get("status") != "no_embed"})
    return vids


def _photos(m: dict) -> list[dict]:
    return [p for p in (m.get("photos") or []) if p.get("url") and p.get("status") != "missing"]


def summary(m: dict) -> dict:
    vids, photos = _videos(m), _photos(m)
    thumb = photos[0]["url"] if photos else (f"https://i.ytimg.com/vi/{vids[0]['id']}/hqdefault.jpg" if vids else None)
    return {"id": m["machine_id"], "name": m["name"], "name_en": m.get("name_en"), "category": m.get("category"),
            "is_new": bool(m.get("is_new")), "availability_br": m.get("availability_br"), "thumb": thumb,
            "thumb_kind": "photo" if photos else "video" if vids else None, "has_video": bool(vids),
            "n_exercises": len(m.get("exercises") or []), "_search": _norm(" ".join([m["name"], m.get("name_en") or "",
                                                                                    " ".join(e.get("name", "") for e in m.get("exercises") or [])]))}


def detail(m: dict) -> dict:
    out = {k: v for k, v in m.items() if k not in ("videos", "photos")}
    out["videos"], out["photos"] = _videos(m), _photos(m)
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
        qn = _norm(q).split()
        items = [i for i in items if all(w in i["_search"] for w in qn)]
    order = {"comum": 0, "média": 1, "rara": 2}
    items.sort(key=lambda i: (not i["has_video"], order.get(i["availability_br"] or "", 3), i["name"]))
    for i in items:
        i.pop("_search", None)
    return items


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
