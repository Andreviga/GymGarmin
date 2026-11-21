"""Séries que o relógio gravou sem dizer o exercício: o app pergunta e aprende.

O relógio detecta as séries sozinho, mas quase nunca sabe qual exercício foi. Chegam três casos:

1. nome exato (treino enviado do app para o relógio): nada a fazer;
2. só a CATEGORIA ("rosca", "supino", "agachamento"): o app chuta o exercício mais comum da categoria
   (item com `guess`). Uma resposta por categoria ("rosca = rosca martelo") corrige todos os treinos de
   uma vez e vale para os próximos; a resposta pode ser trocada depois;
3. nada (categoria UNKNOWN): um item só com as séries de vários exercícios seguidos. O app separa em
   blocos onde a carga recomeça (a pessoa pode dividir um bloco na tela), sugere pelo treino planejado
   do dia e pede o exercício de cada bloco. Dá para desfazer.

O que conta onde: séries só com a categoria contam no músculo do palpite; séries sem nome contam apenas
no total de séries da semana. Só com o exercício certo entram no volume por músculo e na evolução de cargas.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from . import catalog

UNKNOWN_ID = "Garmin_desconhecido"
UNKNOWN_NAME = "Exercício não identificado pelo relógio"
PREFS_KEY = "watch_prefs"
REVIEW_DAYS = 28        # treinos recentes em que ainda vale perguntar "o que foi?"
MAX_SESSIONS = 4
DROP_RATIO = 0.72       # carga cai para menos de 72% da anterior: começou outro exercício
REPS_JUMP = 1.8         # ou as repetições quase dobram sem a carga subir (nova subida de carga, do leve)
LOAD_JUMP = 2.5         # ou a carga salta 2,5× sem as repetições caírem (8 kg de elevação lateral → 120 kg de leg press)
ZERO_RUN = 2            # 2+ séries seguidas a 0 kg depois de séries com carga (ou o contrário): outro exercício (peso do corpo)
NOISE_S = 6             # série de 0 repetição e poucos segundos: toque sem querer

CATEGORY_PT = {
    "BENCH_PRESS": "Supino", "CURL": "Rosca", "SQUAT": "Agachamento", "ROW": "Remada", "TRICEPS_EXTENSION": "Tríceps",
    "LATERAL_RAISE": "Elevação lateral", "SIT_UP": "Abdominal", "PUSH_UP": "Flexão de braço", "PULL_UP": "Puxada / barra fixa",
    "DEADLIFT": "Levantamento terra", "SHOULDER_PRESS": "Desenvolvimento", "LUNGE": "Afundo", "FLYE": "Crucifixo / voador",
    "LEG_CURL": "Flexora", "CALF_RAISE": "Panturrilha", "CRUNCH": "Abdominal", "PLANK": "Prancha", "HIP_RAISE": "Elevação pélvica",
    "SHRUG": "Encolhimento", "LEG_RAISE": "Elevação de pernas", "CORE": "Core", "HYPEREXTENSION": "Extensão lombar",
    "CARRY": "Caminhada com carga", "OLYMPIC_LIFT": "Levantamento olímpico", "HIP_SWING": "Swing", "PLYO": "Saltos",
    "HIP_STABILITY": "Abdução / adução de quadril", "LEG_EXTENSION": "Extensora", "CHOP": "Rotação de tronco",
    "TOTAL_BODY": "Corpo inteiro",
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


def _noise(s: dict) -> bool:
    return not (s.get("reps") or 0) and not (s.get("load_kg") or 0) and (s.get("duration_s") or 0) < NOISE_S


def _clean(sets: list[dict]) -> list[dict]:
    return [s for s in sets if not _noise(s)]


def signature(sets: list[dict]) -> list[list[float]]:
    """O que a tela mostrou de um bloco (repetições e carga de cada série): confere se o pedido fala das mesmas séries."""
    return [[int(s.get("reps") or 0), float(s.get("load_kg") or 0)] for s in _clean(sets)]


def blocks(sets: list[dict]) -> list[tuple[int, int]]:
    """Fatias [início, fim) de um item sem nome: cada uma, provavelmente, um exercício.

    Corta onde a carga cai bastante (o exercício seguinte recomeça do leve), onde as repetições quase dobram
    sem a carga subir, onde a carga salta muito sem as repetições caírem, e onde começa ou termina uma sequência
    de séries a 0 kg (exercício com o peso do corpo). Uma série isolada sem carga digitada fica no bloco em que está.
    É uma estimativa: na tela a pessoa pode dividir um bloco em qualquer série."""
    n = len(sets)
    load = [float(s.get("load_kg") or 0) for s in sets]
    reps = [int(s.get("reps") or 0) for s in sets]

    def zero_run(i: int) -> bool:  # a série i faz parte de uma sequência de 2+ séries a 0 kg
        return load[i] == 0 and ((i + 1 < n and load[i + 1] == 0) or (i > 0 and load[i - 1] == 0))

    out, start, last_load, last_reps = [], 0, 0.0, 0
    for i in range(n):
        cut = False
        if i > start:
            if load[i] > 0 and last_load > 0:
                cut = (load[i] < DROP_RATIO * last_load
                       or bool(last_reps and reps[i] >= REPS_JUMP * last_reps and load[i] <= last_load)
                       or (load[i] >= LOAD_JUMP * last_load and (not last_reps or reps[i] >= 0.6 * last_reps)))
            if not cut and zero_run(i) and not zero_run(i - 1) and last_load > 0:
                cut = True   # entrou numa sequência de peso do corpo
            if not cut and load[i] > 0 and i - start >= ZERO_RUN and all(load[k] == 0 for k in range(start, i)):
                cut = True   # saiu de uma sequência de peso do corpo
        if cut:
            out.append((start, i))
            start, last_load, last_reps = i, 0.0, 0
        if load[i] > 0:
            last_load = load[i]
        if reps[i]:
            last_reps = reps[i]
    if sets:
        out.append((start, n))
    return out


def _is_unknown(it: dict) -> bool:
    return it.get("exercise_id") == UNKNOWN_ID and bool(it.get("sets"))


def _is_guess(it: dict) -> bool:
    """Palpite por categoria que vale perguntar: só quando o palpite é um exercício de força
    (séries que o relógio marcou como cardio/aquecimento no meio do treino não viram pergunta)."""
    return (bool(it.get("guess")) and bool(it.get("sets")) and bool((it.get("garmin") or {}).get("category"))
            and (catalog.get(it.get("exercise_id")) or {}).get("kind") == "strength")


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


def _set_view(sets: list[dict], a: int, b: int) -> list[dict]:
    """Séries válidas de sets[a:b] com a posição original (`i`), para a tela poder dividir o bloco numa série."""
    return [{"reps": x.get("reps") or 0, "load_kg": x.get("load_kg") or 0, "i": k} for k, x in enumerate(sets[a:b], start=a) if not _noise(x)]


def review(sessions: list[dict], entries: list[dict], prefs: dict, today: date, preferred: list[str] | None = None) -> dict:
    """O que o relógio gravou e o app ainda não sabe o que é (categorias a confirmar, treinos com séries sem nome)
    e o que a pessoa já respondeu, para poder corrigir.

    `preferred` = exercícios do plano da pessoa (aparecem primeiro nas opções de cada categoria)."""
    mine = list(preferred or [])
    for s in sessions:  # o que ela mesma registrou no app (nome certo) também vem antes dos genéricos
        if s.get("client_id") or not s.get("source"):
            mine += [it.get("exercise_id") for it in s.get("items") or [] if it.get("sets") and not it.get("guess")]
    confirmed = category_prefs(prefs)
    dismissed = set(prefs.get("dismissed") or [])
    cats: dict[str, dict] = {}
    unknown_total = guess_total = 0
    recent, labeled, put_aside = [], [], []
    lo = (today - timedelta(days=REVIEW_DAYS)).isoformat()
    by_day: dict[str, dict] = {}
    for e in entries:
        if e.get("kind") == "forca":
            by_day.setdefault(str(e.get("date"))[:10], e)
    for s in sorted(sessions, key=lambda x: str(x.get("date")), reverse=True):
        day = str(s.get("date"))[:10]
        items = s.get("items") or []
        title = s.get("day_name") or s.get("notes") or "Treino do relógio"
        for idx, it in enumerate(items):
            if _is_guess(it):
                c = it["garmin"]["category"]
                row = cats.setdefault(c, {"category": c, "label": CATEGORY_PT.get(c, c.replace("_", " ").capitalize()), "sets": 0,
                                          "sessions": set(), "last": day, "by_ex": {}})
                n = len(_clean(it["sets"]))
                row["sets"] += n
                row["sessions"].add(s.get("id"))
                row["by_ex"][it["exercise_id"]] = row["by_ex"].get(it["exercise_id"], 0) + n
                guess_total += n
            elif _is_unknown(it):
                unknown_total += len(_clean(it["sets"]))
            elif it.get("labeled") == "bloco" and day >= lo and len(labeled) < 12:
                labeled.append({"session_id": s.get("id"), "item": idx, "date": day,
                                "exercise": {"id": it.get("exercise_id"), "name": _name(it.get("exercise_id"))},
                                "sets": _set_view(it.get("sets") or [], 0, len(it.get("sets") or []))})
        has_unknown = any(_is_unknown(it) for it in items)
        if has_unknown and day >= lo and s.get("id") in dismissed and len(put_aside) < 6:
            put_aside.append({"session_id": s.get("id"), "date": day, "title": title,
                              "sets": sum(len(_clean(it["sets"])) for it in items if _is_unknown(it))})
        if day < lo or s.get("id") in dismissed or len(recent) >= MAX_SESSIONS or not has_unknown:
            continue
        cands = _plan_candidates(by_day.get(day), s)
        blks = []
        for idx, it in enumerate(items):
            if not _is_unknown(it):
                continue
            sets = it["sets"]
            for a, b in blocks(sets):
                part = _set_view(sets, a, b)
                if not part:
                    continue
                # bloco com carga não é exercício de peso do corpo (barra fixa, flexão): pula esses na sugestão
                loaded = any(x["load_kg"] > 0 for x in part)
                pick = next((c for c in cands if not (loaded and (catalog.get(c) or {}).get("equipment") == "body only")), None)
                near = _neighbor(items, idx) if len(part) <= 2 and (a, b) == (0, len(sets)) else None
                if near:  # 1–2 séries soltas entre séries do MESMO exercício: o relógio só não reconheceu aquelas
                    pick = near
                elif pick:
                    cands.remove(pick)
                blks.append({"item": idx, "from": a, "to": b, "sets": part,
                             "suggestion": {"id": pick, "name": _name(pick)} if pick else None})
        if blks:
            recent.append({"session_id": s.get("id"), "date": day, "title": title,
                           "planned": (by_day.get(day) or {}).get("day_name"), "blocks": blks,
                           "sets": sum(len(b["sets"]) for b in blks)})
    out_cats = []
    for c, row in cats.items():
        cur = max(row["by_ex"], key=row["by_ex"].get)
        opts = [cur] + [e for e in _category_options(c, mine) if e != cur]
        out_cats.append({"category": c, "label": row["label"], "sets": row["sets"], "sessions": len(row["sessions"]), "last": row["last"],
                         "current": {"id": cur, "name": _name(cur)}, "options": [{"id": e, "name": _name(e)} for e in opts[:5]]})
    out_cats.sort(key=lambda r: -r["sets"])
    return {"categories": out_cats, "sessions": recent, "unknown_sets": unknown_total, "guess_sets": guess_total,
            "confirmed": [{"category": c, "label": CATEGORY_PT.get(c, c.replace("_", " ").capitalize()), "exercise": {"id": e, "name": _name(e)}}
                          for c, e in confirmed.items()],
            "labeled": labeled, "dismissed": put_aside}


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
        if (g.get("category") == cat and ex.get("kind") == "strength" and ex.get("popular") and ex.get("rotation", True)
                and ex["id"] not in out):
            out.append(ex["id"])
    return out[:8]


def apply_category(sessions: list[dict], category: str, exercise_id: str) -> list[dict]:
    """A pessoa disse o que essa categoria é para ela: troca em todos os treinos em que era palpite — e também nos
    que já tinham sido trocados por uma resposta anterior dessa categoria (para a resposta poder ser corrigida).
    Devolve as sessões alteradas."""
    changed = []
    for s in sessions:
        dirty = False
        for it in s.get("items") or []:
            if not it.get("sets") or (it.get("garmin") or {}).get("category") != category:
                continue
            if _is_guess(it) or (it.get("labeled") == "categoria" and it.get("exercise_id") != exercise_id):
                it["exercise_id"] = exercise_id
                it.pop("guess", None)
                it.pop("_name", None)
                it["labeled"] = "categoria"
                dirty = True
        if dirty:
            s["items"] = _merge_neighbors(s["items"])
            changed.append(s)
    return changed


def apply_label(session: dict, item: int, start: int, end: int, exercise_id: str, expect: list | None = None) -> dict:
    """Dá nome a um bloco de séries de um item sem nome. O resto do item continua sem nome (antes e depois do bloco).

    `expect` = as séries que a tela mostrou nesse bloco (repetições e carga): se a posição não contiver exatamente
    essas séries (tela desatualizada, dois toques seguidos), o pedido é recusado em vez de dar nome às séries erradas."""
    items = list(session.get("items") or [])
    if not (0 <= item < len(items)) or not _is_unknown(items[item]):
        raise ValueError("Essas séries já têm exercício ou o treino mudou. Abra de novo.")
    it = items[item]
    sets = it["sets"]
    if not (0 <= start < end <= len(sets)):
        raise ValueError("Esse treino mudou desde que a tela abriu. Abra de novo.")
    before, mid, after = sets[:start], _clean(sets[start:end]), sets[end:]
    if not mid:
        raise ValueError("Esse bloco não tem série válida")
    if expect is not None:
        try:
            want = [[int(r), float(k)] for r, k in expect]
        except (TypeError, ValueError):
            raise ValueError("Bloco de séries inválido") from None
        if want != signature(mid):
            raise ValueError("Esse treino mudou desde que a tela abriu. Abra de novo.")
    base = {k: v for k, v in it.items() if k not in ("sets", "_name")}
    new = []
    if _clean(before):
        new.append({**base, "_name": it.get("_name") or UNKNOWN_NAME, "sets": before})
    new.append({"exercise_id": exercise_id, "sets": mid, "garmin": it.get("garmin"), "labeled": "bloco"})
    if _clean(after):
        new.append({**base, "_name": it.get("_name") or UNKNOWN_NAME, "sets": after})
    session["items"] = _merge_neighbors(items[:item] + new + items[item + 1:])
    return session


def unlabel(session: dict, item: int) -> dict:
    """Desfaz o nome dado a um bloco: as séries voltam a ser "sem nome" (e voltam a ser perguntadas)."""
    items = list(session.get("items") or [])
    if not (0 <= item < len(items)) or items[item].get("labeled") != "bloco":
        raise ValueError("Esse bloco não foi nomeado por aqui (ou o treino mudou). Abra de novo.")
    it = items[item]
    items[item] = {"exercise_id": UNKNOWN_ID, "_name": UNKNOWN_NAME, "sets": it.get("sets") or [],
                   "garmin": it.get("garmin") or {"category": "UNKNOWN", "name": None}}
    session["items"] = items
    return session


def _merge_neighbors(items: list[dict]) -> list[dict]:
    """Dois itens seguidos do mesmo exercício com a mesma origem (palpite, categoria confirmada, bloco nomeado) viram um só.
    Só junta itens vindos do relógio com lista de séries; item anotado no app nunca é mexido."""
    out: list[dict] = []
    for it in items:
        prev = out[-1] if out else None
        same = (prev is not None and isinstance(it.get("sets"), list) and isinstance(prev.get("sets"), list)
                and bool(it.get("garmin")) and bool(prev.get("garmin"))
                and prev.get("exercise_id") == it.get("exercise_id") and it.get("exercise_id") != UNKNOWN_ID
                and bool(prev.get("guess")) == bool(it.get("guess")) and prev.get("labeled") == it.get("labeled"))
        if same:
            prev["sets"] = list(prev["sets"]) + list(it["sets"])
        else:
            out.append(dict(it))
    return out
