"""Séries que o relógio gravou sem dizer o exercício: o app pergunta e aprende.

O relógio detecta as séries sozinho, mas quase nunca sabe qual exercício foi. Chegam três casos:

1. nome exato (treino enviado do app para o relógio): nada a fazer;
2. só a CATEGORIA ("rosca", "supino", "agachamento"): o app chuta o exercício mais comum da categoria
   (item com `guess`). Uma resposta por categoria ("rosca = rosca martelo") corrige todos os treinos de
   uma vez e vale para os próximos;
3. nada (categoria UNKNOWN): um item só com as séries de vários exercícios seguidos. O app separa em
   blocos onde a carga recomeça, sugere pelo treino planejado do dia e pede o exercício de cada bloco.

Enquanto a pessoa não responde, as séries contam no volume da semana, mas não entram na evolução de
cargas de nenhum exercício (sem nome não há o que comparar) — por isso vale perguntar.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from . import catalog

UNKNOWN_ID = "Garmin_desconhecido"
PREFS_KEY = "watch_prefs"
REVIEW_DAYS = 28        # treinos recentes em que ainda vale perguntar "o que foi?"
MAX_SESSIONS = 4
DROP_RATIO = 0.72       # carga cai para menos de 72% da anterior: começou outro exercício
REPS_JUMP = 1.8         # ou as repetições quase dobram sem a carga subir (nova subida de carga, do leve)
NOISE_S = 6             # série de 0 repetição e poucos segundos: toque sem querer

CATEGORY_PT = {
    "BENCH_PRESS": "Supino", "CURL": "Rosca", "SQUAT": "Agachamento", "ROW": "Remada", "TRICEPS_EXTENSION": "Tríceps",
    "LATERAL_RAISE": "Elevação lateral", "SIT_UP": "Abdominal", "PUSH_UP": "Flexão de braço", "PULL_UP": "Puxada / barra fixa",
    "DEADLIFT": "Levantamento terra", "SHOULDER_PRESS": "Desenvolvimento", "LUNGE": "Afundo", "FLYE": "Crucifixo / voador",
    "LEG_CURL": "Flexora", "CALF_RAISE": "Panturrilha", "CRUNCH": "Abdominal", "PLANK": "Prancha", "HIP_RAISE": "Elevação pélvica",
    "SHRUG": "Encolhimento", "LEG_RAISE": "Elevação de pernas", "CORE": "Core", "HYPEREXTENSION": "Extensão lombar",
    "CARRY": "Caminhada com carga", "OLYMPIC_LIFT": "Levantamento olímpico", "HIP_SWING": "Swing", "PLYO": "Saltos",
    "HIP_STABILITY": "Abdução / adução de quadril", "LEG_EXTENSION": "Extensora", "CHOP": "Rotação de tronco",
    "TOTAL_BODY": "Corpo inteiro", "CARDIO": "Cardio", "WARM_UP": "Aquecimento",
}


def load_prefs(raw: str | None) -> dict:
    try:
        d = json.loads(raw or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def category_prefs(prefs: dict) -> dict[str, str]:
    """categoria do relógio → exercício que a pessoa confirmou (só os que ainda existem no catálogo)."""
    return {c: e for c, e in (prefs.get("categories") or {}).items() if catalog.get(e)}


def _name(eid: str | None) -> str:
    return (catalog.get(eid) or {}).get("name") or str(eid or "")


def _clean(sets: list[dict]) -> list[dict]:
    return [s for s in sets if not (not (s.get("reps") or 0) and not (s.get("load_kg") or 0) and (s.get("duration_s") or 0) < NOISE_S)]


def blocks(sets: list[dict]) -> list[tuple[int, int]]:
    """Fatias [início, fim) de um item sem nome: cada uma, provavelmente, um exercício.

    Corta onde a carga cai bastante (o exercício seguinte recomeça do leve) ou onde as repetições quase
    dobram sem a carga subir. Série sem carga digitada (0 kg) fica no bloco em que está."""
    out, start, last_load, last_reps = [], 0, 0.0, 0
    for i, s in enumerate(sets):
        load, reps = float(s.get("load_kg") or 0), int(s.get("reps") or 0)
        cut = False
        if i > start and load > 0 and last_load > 0:
            cut = load < DROP_RATIO * last_load or (last_reps and reps >= REPS_JUMP * last_reps and load <= last_load)
        if cut:
            out.append((start, i))
            start, last_load, last_reps = i, 0.0, 0
        if load > 0:
            last_load = load
        if reps:
            last_reps = reps
    if sets:
        out.append((start, len(sets)))
    return out


def _is_unknown(it: dict) -> bool:
    return it.get("exercise_id") == UNKNOWN_ID and bool(it.get("sets"))


def _is_guess(it: dict) -> bool:
    return bool(it.get("guess")) and bool(it.get("sets")) and bool((it.get("garmin") or {}).get("category"))


def _plan_candidates(entry: dict | None, session: dict) -> list[str]:
    """Exercícios de força do treino planejado no dia que ainda não aparecem com nome na sessão, na ordem do treino."""
    if not entry:
        return []
    named = {it.get("exercise_id") for it in session.get("items") or [] if not _is_unknown(it) and not it.get("guess")}
    out = []
    for it in entry.get("items") or []:
        eid = it.get("exercise_id")
        ex = catalog.get(eid) or {}
        if ex.get("kind") == "strength" and (it.get("phase") or "principal") == "principal" and eid not in named and eid not in out:
            out.append(eid)
    return out


def review(sessions: list[dict], entries: list[dict], prefs: dict, today: date, preferred: list[str] | None = None) -> dict:
    """O que o relógio gravou e o app ainda não sabe o que é: categorias a confirmar e treinos com séries sem nome.

    `preferred` = exercícios do plano da pessoa (aparecem primeiro nas opções de cada categoria)."""
    mine = list(preferred or [])
    for s in sessions:  # o que ela mesma registrou no app (nome certo) também vem antes dos genéricos
        if s.get("client_id") or not s.get("source"):
            mine += [it.get("exercise_id") for it in s.get("items") or [] if it.get("sets") and not it.get("guess")]
    confirmed = category_prefs(prefs)
    dismissed = set(prefs.get("dismissed") or [])
    cats: dict[str, dict] = {}
    unknown_total = guess_total = 0
    recent = []
    lo = (today - timedelta(days=REVIEW_DAYS)).isoformat()
    by_day: dict[str, dict] = {}
    for e in entries:
        if e.get("kind") == "forca":
            by_day.setdefault(str(e.get("date"))[:10], e)
    for s in sorted(sessions, key=lambda x: str(x.get("date")), reverse=True):
        day = str(s.get("date"))[:10]
        items = s.get("items") or []
        for it in items:
            if _is_guess(it):
                c = it["garmin"]["category"]
                row = cats.setdefault(c, {"category": c, "label": CATEGORY_PT.get(c, c.replace("_", " ").capitalize()), "sets": 0,
                                          "sessions": 0, "last": day, "by_ex": {}})
                n = len(_clean(it["sets"]))
                row["sets"] += n
                row["sessions"] += 1
                row["by_ex"][it["exercise_id"]] = row["by_ex"].get(it["exercise_id"], 0) + n
                guess_total += n
            elif _is_unknown(it):
                unknown_total += len(_clean(it["sets"]))
        if day < lo or s.get("id") in dismissed or len(recent) >= MAX_SESSIONS or not any(_is_unknown(it) for it in items):
            continue
        cands = _plan_candidates(by_day.get(day), s)
        blks = []
        for idx, it in enumerate(items):
            if not _is_unknown(it):
                continue
            sets = it["sets"]
            for a, b in blocks(sets):
                part = _clean(sets[a:b])
                if not part:
                    continue
                # bloco com carga não é exercício de peso do corpo (barra fixa, flexão): pula esses na sugestão
                loaded = any((x.get("load_kg") or 0) > 0 for x in part)
                pick = next((c for c in cands if not (loaded and (catalog.get(c) or {}).get("equipment") == "body only")), None)
                near = _neighbor(items, idx) if len(part) <= 2 and (a, b) == (0, len(sets)) else None
                if near:  # 1–2 séries soltas entre séries do MESMO exercício: o relógio só não reconheceu aquelas
                    pick = near
                elif pick:
                    cands.remove(pick)
                sug = pick
                blks.append({"item": idx, "from": a, "to": b,
                             "sets": [{"reps": x.get("reps") or 0, "load_kg": x.get("load_kg") or 0} for x in part],
                             "suggestion": {"id": sug, "name": _name(sug)} if sug else None})
        if blks:
            recent.append({"session_id": s.get("id"), "date": day, "title": s.get("day_name") or s.get("notes") or "Treino do relógio",
                           "planned": (by_day.get(day) or {}).get("day_name"), "blocks": blks,
                           "sets": sum(len(b["sets"]) for b in blks)})
    out_cats = []
    for c, row in cats.items():
        cur = max(row["by_ex"], key=row["by_ex"].get)
        opts = [cur] + [e for e in _category_options(c, mine) if e != cur]
        out_cats.append({"category": c, "label": row["label"], "sets": row["sets"], "sessions": row["sessions"], "last": row["last"],
                         "current": {"id": cur, "name": _name(cur)}, "options": [{"id": e, "name": _name(e)} for e in opts[:5]]})
    out_cats.sort(key=lambda r: -r["sets"])
    return {"categories": out_cats, "sessions": recent, "unknown_sets": unknown_total, "guess_sets": guess_total,
            "confirmed": [{"category": c, "label": CATEGORY_PT.get(c, c), "exercise": {"id": e, "name": _name(e)}} for c, e in confirmed.items()]}


def _neighbor(items: list[dict], idx: int) -> str | None:
    """Exercício dos itens vizinhos quando o de antes e o de depois são o mesmo (ou só existe um vizinho com nome)."""
    def named(i: int) -> str | None:
        if 0 <= i < len(items) and items[i].get("sets") and not _is_unknown(items[i]):
            return items[i].get("exercise_id")
        return None
    prev, nxt = named(idx - 1), named(idx + 1)
    if prev and nxt:
        return prev if prev == nxt else None
    return prev or nxt


def _category_options(cat: str, mine: list[str] | None = None) -> list[str]:
    """Exercícios que o relógio classificaria nessa categoria: os da pessoa (plano, treinos no app) e depois os populares."""
    out = []
    for eid in mine or []:
        ex = catalog.get(eid) or {}
        if (ex.get("garmin") or {}).get("category") == cat and ex.get("kind") == "strength" and eid not in out:
            out.append(eid)
    for ex in catalog.all_exercises():
        g = ex.get("garmin") or {}
        if g.get("category") == cat and ex.get("kind") == "strength" and ex.get("popular") and ex["id"] not in out:
            out.append(ex["id"])
    return out[:8]


def apply_category(sessions: list[dict], category: str, exercise_id: str) -> list[dict]:
    """A pessoa disse o que essa categoria é para ela: troca em todos os treinos em que era palpite. Devolve as sessões alteradas."""
    changed = []
    for s in sessions:
        dirty = False
        for it in s.get("items") or []:
            if _is_guess(it) and it["garmin"]["category"] == category:
                it["exercise_id"] = exercise_id
                it.pop("guess", None)
                it.pop("_name", None)
                it["labeled"] = "categoria"
                dirty = True
        if dirty:
            s["items"] = _merge_neighbors(s["items"])
            changed.append(s)
    return changed


def apply_label(session: dict, item: int, start: int, end: int, exercise_id: str) -> dict:
    """Dá nome a um bloco de séries de um item sem nome. O resto do item continua sem nome (antes e depois do bloco)."""
    items = list(session.get("items") or [])
    if not (0 <= item < len(items)) or not _is_unknown(items[item]):
        raise ValueError("Essas séries já têm exercício ou o treino mudou. Abra de novo.")
    it = items[item]
    sets = it["sets"]
    if not (0 <= start < end <= len(sets)):
        raise ValueError("Bloco de séries inválido")
    before, mid, after = sets[:start], _clean(sets[start:end]), sets[end:]
    if not mid:
        raise ValueError("Esse bloco não tem série válida")
    base = {k: v for k, v in it.items() if k not in ("sets", "_name")}
    new = []
    if _clean(before):
        new.append({**base, "_name": it.get("_name"), "sets": before})
    new.append({"exercise_id": exercise_id, "sets": mid, "garmin": it.get("garmin"), "labeled": "bloco"})
    if _clean(after):
        new.append({**base, "_name": it.get("_name"), "sets": after})
    session["items"] = _merge_neighbors(items[:item] + new + items[item + 1:])
    return session


def _merge_neighbors(items: list[dict]) -> list[dict]:
    """Dois itens seguidos do mesmo exercício (com séries) viram um só."""
    out: list[dict] = []
    for it in items:
        prev = out[-1] if out else None
        if (prev and it.get("sets") and prev.get("sets") and prev.get("exercise_id") == it.get("exercise_id")
                and it.get("exercise_id") != UNKNOWN_ID and bool(prev.get("guess")) == bool(it.get("guess"))):
            prev["sets"] = list(prev["sets"]) + list(it["sets"])
        else:
            out.append(dict(it))
    return out
