"""Minha academia: o aparelho de verdade que cada pessoa usa.

Pensado para quem está no meio do treino:
- o aparelho do plano está ocupado ou a academia tem outro modelo → "Trocar" mostra exercícios que fazem
  o mesmo movimento (mesmo padrão do gerador e mesmos músculos), com os aparelhos que a pessoa já
  fotografou primeiro;
- cada máquina tem regulagem (banco, encosto, pino, pegada) e escala de carga própria → a pessoa fotografa
  o aparelho dela, anota a regulagem, e da próxima vez o app mostra a foto e a regulagem no exercício;
- a foto pode ir para a fila da área do desenvolvedor: o administrador aprova e o aparelho entra na
  galeria para todo mundo (ou a foto é juntada a um aparelho que já existe).

Fotos ficam no próprio banco (tabela kv, usuário global -1), já reduzidas no celular (~150–400 KB).
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
import time

from . import catalog, clock, db

GLOBAL = -1
MAX_BYTES = 900_000
DATA_URL = re.compile(r"^data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=\s]+)$")
_CACHE: dict = {"custom": None, "t": 0.0}


def _get(key: str, user_id: int | None, default):
    raw = db.kv_get(key, user_id=user_id)
    return json.loads(raw) if raw else default


def _put(key: str, value, user_id: int | None) -> None:
    db.kv_set(key, json.dumps(value, ensure_ascii=False), user_id=user_id)


# ------------------------------------------------------------------ fotos ----

def save_photo(data_url: str, owner: int) -> str:
    m = DATA_URL.match((data_url or "").strip())
    if not m:
        raise ValueError("Foto inválida: envie uma imagem JPEG, PNG ou WebP.")
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", m.group(2)), validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError("Foto corrompida.") from e
    if len(raw) > MAX_BYTES:
        raise ValueError("Foto grande demais (máx. ~900 KB). Tente de novo — o app reduz antes de enviar.")
    pid = secrets.token_hex(8)
    _put(f"photo:{pid}", {"mime": m.group(1), "b64": base64.b64encode(raw).decode(), "owner": owner,
                          "public": False, "created": clock.now().isoformat(timespec="seconds")}, GLOBAL)
    return pid


def photo(pid: str) -> dict | None:
    if not re.fullmatch(r"[0-9a-f]{16}", pid or ""):
        return None
    return _get(f"photo:{pid}", GLOBAL, None)


def can_see(p: dict, user_id: int, is_admin: bool) -> bool:
    return bool(p.get("public") or p.get("owner") == user_id or is_admin)


def _set_public(pid: str, public: bool = True) -> None:
    p = photo(pid)
    if p:
        p["public"] = public
        _put(f"photo:{pid}", p, GLOBAL)


def photo_url(pid: str | None) -> str | None:
    return f"/api/photos/{pid}" if pid else None


# --------------------------------------------------------- meus aparelhos ----

def _mine(user_id: int | None = None) -> list[dict]:
    return _get("my_machines", user_id, [])


def _view(m: dict) -> dict:
    ex = catalog.get(m.get("exercise_id")) or {}
    return {**m, "photo_url": photo_url(m.get("photo_id")), "exercise_name": ex.get("name")}


def list_mine(user_id: int | None = None) -> list[dict]:
    return [_view(m) for m in sorted(_mine(user_id), key=lambda m: m.get("created") or "", reverse=True)]


def for_exercise(exercise_id: str, user_id: int | None = None) -> dict | None:
    hits = [m for m in _mine(user_id) if m.get("exercise_id") == exercise_id]
    return _view(max(hits, key=lambda m: m.get("created") or "")) if hits else None


def add_mine(user_id: int, user_name: str | None, image: str | None, exercise_id: str, name: str,
             settings: str | None, suggest: bool) -> dict:
    if not catalog.get(exercise_id):
        raise ValueError("Exercício desconhecido.")
    pid = save_photo(image, user_id) if image else None
    item = {"id": secrets.token_hex(6), "photo_id": pid, "exercise_id": exercise_id,
            "name": (name or "").strip()[:80] or catalog.get(exercise_id)["name"],
            "settings": (settings or "").strip()[:200] or None, "created": clock.now().isoformat(timespec="seconds")}
    mine = _mine(user_id)
    mine.append(item)
    _put("my_machines", mine, user_id)
    if suggest and pid:
        submit(pid, user_id, user_name, exercise_id, item["name"], item["settings"])
        item["suggested"] = True
    return _view(item)


def update_mine(user_id: int, mid: str, fields: dict) -> dict | None:
    mine = _mine(user_id)
    for m in mine:
        if m["id"] == mid:
            if "name" in fields:
                m["name"] = (fields.get("name") or "").strip()[:80] or m["name"]
            if "settings" in fields:
                m["settings"] = (fields.get("settings") or "").strip()[:200] or None
            if fields.get("exercise_id") and catalog.get(fields["exercise_id"]):
                m["exercise_id"] = fields["exercise_id"]
            _put("my_machines", mine, user_id)
            return _view(m)
    return None


def delete_mine(user_id: int, mid: str) -> bool:
    mine = _mine(user_id)
    keep = [m for m in mine if m["id"] != mid]
    if len(keep) == len(mine):
        return False
    gone = next(m for m in mine if m["id"] == mid)
    _put("my_machines", keep, user_id)
    p = photo(gone.get("photo_id") or "")
    queued = any(q.get("photo_id") == gone.get("photo_id") and q.get("status") == "pendente" for q in _queue())
    if p and not p.get("public") and not queued:
        db.kv_delete(f"photo:{gone['photo_id']}", user_id=GLOBAL)
    return True


# ------------------------------------------------------- trocar exercício ----

_STATIONS: set | None = None


def station_ids() -> set:
    """Estações e movimentos de circuito (burpee, wall ball…): não são sugestão para "treinar o músculo X"."""
    global _STATIONS
    if _STATIONS is None:
        from pathlib import Path
        f = Path(__file__).resolve().parent / "data" / "extra_exercises.json"
        _STATIONS = {e["id"] for e in json.loads(f.read_text())} if f.exists() else set()
    return _STATIONS


def rank_for_muscle(items: list[dict], muscle: str, user_id: int | None = None) -> list[dict]:
    """Ordena exercícios para quem quer treinar UM músculo: primeiro os que têm esse músculo como principal,
    os aparelhos da academia da pessoa, os que o próprio gerador usa e os mais comuns; estações de circuito,
    pliometria e alongamento vão para o fim."""
    from .generator import PATTERNS

    curated = {e for ids in PATTERNS.values() for e in ids}
    mine = {m["exercise_id"] for m in _mine(user_id)}
    stations = station_ids()

    def key(e: dict):
        odd = e["id"] in stations or e.get("equipment") == "other" or e.get("category") in ("plyometrics", "stretching", "cardio")
        known = e["id"] in mine or e["id"] in curated or bool(e.get("popular"))  # traduzido e comum (não exercício de especialista)
        return (odd, not known, (e.get("primaryMuscles") or [None])[0] != muscle, e["id"] not in mine, e["id"] not in curated,
                not e.get("popular"), e["name"])

    return sorted(items, key=key)


def alternatives(exercise_id: str, user_id: int | None = None, limit: int = 14) -> list[str]:
    """Exercícios que fazem o 'mesmo' trabalho: mesmo padrão de movimento do gerador e mesmos músculos principais."""
    from .generator import PATTERNS

    ex = catalog.get(exercise_id)
    if not ex:
        return []
    out: list[str] = []
    mine = {m["exercise_id"] for m in _mine(user_id)}

    def add(eid: str) -> None:
        if eid != exercise_id and eid not in out and catalog.get(eid):
            out.append(eid)

    same_pattern = [e for ids in PATTERNS.values() if exercise_id in ids for e in ids]
    if not same_pattern and ex.get("pattern") in PATTERNS:  # aparelho "por escolha" (fora do rodízio): irmãos pelo padrão dele
        same_pattern = list(PATTERNS[ex["pattern"]])
    prim = set(ex.get("primaryMuscles") or [])
    by_muscle = []
    stations = station_ids()  # burpee, wall ball…
    main_muscle = (ex.get("primaryMuscles") or [None])[0]
    for m in ex.get("primaryMuscles") or []:  # lista (ordem fixa): a sugestão não muda de um toque para outro
        for c in catalog.search(muscle=m, limit=80):
            if c["kind"] != ex["kind"] or (c.get("primaryMuscles") or [None])[0] != main_muscle:
                continue
            if c["id"] in stations or c.get("equipment") == "other" or c.get("category") in ("plyometrics", "stretching", "cardio"):
                continue
            if ex.get("mechanic") and c.get("mechanic") and c["mechanic"] != ex["mechanic"]:
                continue
            if (c.get("rotation") is False or c.get("availability_br") == "rara") and c["id"] not in mine:
                continue  # aparelho raro/por escolha só vira opção de troca para quem cadastrou esse aparelho
            by_muscle.append(c["id"])
    for eid in [e for e in same_pattern + by_muscle if e in mine]:
        add(eid)
    for eid in same_pattern:
        add(eid)
    for eid in by_muscle:
        add(eid)
    return out[:limit]


# ------------------------------------------------------ área do desenvolvedor ----

def _queue() -> list[dict]:
    return _get("dev_queue", GLOBAL, [])


def submit(pid: str, user_id: int, user_name: str | None, exercise_id: str | None, name: str, settings: str | None) -> dict:
    q = _queue()
    item = {"qid": secrets.token_hex(6), "photo_id": pid, "user_id": user_id, "user_name": user_name,
            "exercise_id": exercise_id, "name": name, "settings": settings, "status": "pendente",
            "created": clock.now().isoformat(timespec="seconds")}
    q.append(item)
    _put("dev_queue", q, GLOBAL)
    return item


def queue(status: str = "pendente") -> list[dict]:
    out = []
    for q in _queue():
        if q.get("status") != status:
            continue
        ex = catalog.get(q.get("exercise_id")) or {}
        out.append({**q, "photo_url": photo_url(q["photo_id"]), "exercise_name": ex.get("name")})
    return sorted(out, key=lambda q: q["created"], reverse=True)


def _set_status(qid: str, status: str, **extra) -> dict | None:
    q = _queue()
    for it in q:
        if it["qid"] == qid:
            it.update(status=status, decided=clock.now().isoformat(timespec="seconds"), **extra)
            _put("dev_queue", q, GLOBAL)
            return it
    return None


def custom_machines() -> list[dict]:
    if _CACHE["custom"] is None or time.time() - _CACHE["t"] > 30:
        _CACHE["custom"] = _get("custom_machines", GLOBAL, [])
        _CACHE["extra"] = _get("machine_extra_photos", GLOBAL, {})
        _CACHE["t"] = time.time()
    return _CACHE["custom"]


def extra_photos() -> dict:
    custom_machines()
    return _CACHE.get("extra") or {}


def _invalidate() -> None:
    _CACHE["custom"] = None
    from . import gallery
    gallery.by_exercise.cache_clear()


def _machine_from(name: str, category: str, exercise_ids: list[str], what: str | None, pid: str, mid: str) -> dict:
    exs = []
    for eid in exercise_ids:
        ex = catalog.get(eid)
        if ex:
            exs.append({"name": ex["name"], "how": (what or "").strip()[:400] or None,
                        "muscles": ex.get("primaryMuscles") or [], "exercise_id": eid})
    return {"machine_id": mid, "name": name.strip()[:80], "name_en": None, "category": category, "is_new": False,
            "availability_br": "comum", "community": True, "tips": [], "why": (what or "").strip()[:400] or None,
            "existing_exercise_ids": [e["exercise_id"] for e in exs], "exercises": exs, "videos": [],
            "photos": [{"url": photo_url(pid), "status": "ok", "author": "comunidade HAX"}]}


def approve(qid: str, name: str, category: str, exercise_ids: list[str], what: str | None = None,
            merge_into: str | None = None) -> dict:
    item = next((q for q in _queue() if q["qid"] == qid), None)
    if not item:
        raise ValueError("Item da fila não encontrado.")
    _set_public(item["photo_id"])
    if merge_into:
        extra = _get("machine_extra_photos", GLOBAL, {})
        extra.setdefault(merge_into, []).append({"url": photo_url(item["photo_id"]), "status": "ok", "author": "comunidade HAX"})
        _put("machine_extra_photos", extra, GLOBAL)
        _set_status(qid, "aprovado", machine_id=merge_into)
        _invalidate()
        return {"machine_id": merge_into, "merged": True}
    if not (name or "").strip() or not exercise_ids:
        raise ValueError("Dê um nome e ligue pelo menos um exercício.")
    m = _machine_from(name, category or "funcional", exercise_ids, what, item["photo_id"], f"cm_{qid}")
    customs = _get("custom_machines", GLOBAL, [])
    customs.append(m)
    _put("custom_machines", customs, GLOBAL)
    _set_status(qid, "aprovado", machine_id=m["machine_id"])
    _invalidate()
    return m


def discard(qid: str) -> bool:
    item = _set_status(qid, "descartado")
    return bool(item)


def delete_custom(machine_id: str) -> bool:
    customs = _get("custom_machines", GLOBAL, [])
    keep = [m for m in customs if m["machine_id"] != machine_id]
    if len(keep) == len(customs):
        return False
    _put("custom_machines", keep, GLOBAL)
    _invalidate()
    return True


# ------------------------------------------------------------------ feedback ----

def add_feedback(user_id: int, user_name: str | None, text: str, where: str | None, device: str | None) -> dict:
    text = (text or "").strip()
    if len(text) < 3:
        raise ValueError("Escreva um pouco mais 🙂")
    items = _get("feedback", GLOBAL, [])
    item = {"id": secrets.token_hex(5), "user_id": user_id, "user_name": user_name, "text": text[:2000],
            "where": (where or "")[:60], "device": (device or "")[:120], "done": False,
            "created": clock.now().isoformat(timespec="seconds")}
    items.append(item)
    _put("feedback", items[-300:], GLOBAL)
    return item


def feedback(limit: int = 60) -> list[dict]:
    return sorted(_get("feedback", GLOBAL, []), key=lambda f: f["created"], reverse=True)[:limit]


def feedback_done(fid: str, done: bool = True) -> bool:
    items = _get("feedback", GLOBAL, [])
    for f in items:
        if f["id"] == fid:
            f["done"] = done
            _put("feedback", items, GLOBAL)
            return True
    return False
