"""Importar/exportar planilhas de treino em CSV (Excel / Google Planilhas → "Salvar como CSV").

Colunas aceitas (cabeçalho em português ou inglês, separador ; ou ,):
  treino | exercicio | series | reps | carga_kg | descanso_s | tempo_s | duracao_min | zona | obs

Exemplo:
  treino;exercicio;series;reps;carga_kg;descanso_s;tempo_s;duracao_min;zona;obs
  A;Esteira caminhada;;;;;;10;2;aquecimento
  A;Supino reto com barra;4;8-12;60;120;;;;
  A;Prancha;3;;;60;45;;;
"""

from __future__ import annotations

import csv
import io

from . import catalog

ALIASES = {
    "treino": "day", "dia": "day", "day": "day", "divisao": "day",
    "exercicio": "exercise", "exercise": "exercise", "nome": "exercise",
    "series": "sets", "sets": "sets",
    "reps": "reps", "repeticoes": "reps", "repetições": "reps",
    "carga": "load_kg", "carga_kg": "load_kg", "peso": "load_kg", "kg": "load_kg", "load_kg": "load_kg",
    "descanso": "rest_s", "descanso_s": "rest_s", "intervalo": "rest_s", "rest_s": "rest_s",
    "tempo_s": "duration_s", "duracao_s": "duration_s", "tempo": "duration_s", "duration_s": "duration_s",
    "duracao_min": "duration_min", "minutos": "duration_min", "duration_min": "duration_min",
    "zona": "zone", "zone": "zone", "zona_fc": "zone",
    "obs": "notes", "observacao": "notes", "observacoes": "notes", "notes": "notes",
    "id": "exercise_id", "exercise_id": "exercise_id",
}

COLUMNS = ["treino", "exercicio", "exercise_id", "series", "reps", "carga_kg", "descanso_s", "tempo_s",
           "duracao_min", "zona", "obs"]


def _num(v, cast=float):
    if v is None:
        return None
    s = str(v).strip().replace(",", ".")
    if not s:
        return None
    try:
        return cast(float(s))
    except ValueError:
        return None


def import_csv(text: str, plan_name: str = "Planilha importada") -> dict:
    text = text.lstrip("﻿")
    sample = text[:2000]
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    days: dict[str, dict] = {}
    unmatched: list[dict] = []
    matched: list[dict] = []
    for n, row in enumerate(reader, start=2):
        r = {}
        for k, v in row.items():
            if k is None:
                continue
            key = ALIASES.get(catalog.strip_accents(k.strip()).replace(" ", "_"))
            if key:
                r[key] = (v or "").strip()
        name = r.get("exercise", "")
        if not name and not r.get("exercise_id"):
            continue
        ex = catalog.get(r.get("exercise_id")) if r.get("exercise_id") else None
        ex = ex or catalog.best_match(name)
        if not ex:
            unmatched.append({"line": n, "name": name})
            continue
        matched.append({"line": n, "input": name, "matched": ex["name"], "exercise_id": ex["id"]})
        day_name = r.get("day") or "A"
        day = days.setdefault(day_name, {"name": day_name, "items": []})
        item = {"exercise_id": ex["id"]}
        if ex["kind"] == "cardio":
            item["duration_min"] = _num(r.get("duration_min")) or 10
            item["zone"] = _num(r.get("zone"), int) or 2
        else:
            item["sets"] = _num(r.get("sets"), int) or 3
            reps = r.get("reps")
            if reps:
                item["reps"] = int(reps) if reps.isdigit() else reps
            if _num(r.get("load_kg")) is not None:
                item["load_kg"] = _num(r.get("load_kg"))
            item["rest_s"] = _num(r.get("rest_s"), int) or 90
            if _num(r.get("duration_s"), int):
                item["duration_s"] = _num(r.get("duration_s"), int)
        if r.get("notes"):
            item["notes"] = r["notes"]
        day["items"].append(item)
    return {
        "plan": {"name": plan_name, "days": list(days.values()), "days_per_week": max(1, len(days))},
        "matched": matched,
        "unmatched": unmatched,
    }


def export_csv(plan: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(COLUMNS)
    for d in plan.get("days", []):
        for it in d.get("items", []):
            ex = catalog.get(it.get("exercise_id")) or {"name": it.get("exercise_id")}
            w.writerow([d.get("name"), ex["name"], it.get("exercise_id"), it.get("sets") or "", it.get("reps") or "",
                        it.get("load_kg") or "", it.get("rest_s") or "", it.get("duration_s") or "",
                        it.get("duration_min") or "", it.get("zone") or "", it.get("notes") or ""])
    return buf.getvalue()
