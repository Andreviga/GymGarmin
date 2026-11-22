"""Bem-estar: yoga, Pilates, mobilidade, alongamento e respiração em sessões guiadas por objetivo.

As sessões estão em data/wellness.json (gerado por scripts/build_wellness.py, com as fontes de cada uma).
Aqui: a versão da sessão para a pessoa (passos trocados conforme a saúde) e o que sugerir hoje.
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

from . import personal

DATA = Path(__file__).resolve().parent / "data" / "wellness.json"


@lru_cache(maxsize=1)
def data() -> dict:
    return json.loads(DATA.read_text())


def _conds(p: dict) -> set[str]:
    a = personal.answers(p or {})
    out = set(a.get("conditions") or [])
    for lim in (p or {}).get("limitations") or []:
        if lim.get("key") == "osso":
            out.add("osteoporose")
    return out


def session(sid: str, p: dict | None = None) -> dict | None:
    s = next((x for x in data()["sessions"] if x["id"] == sid), None)
    if not s:
        return None
    s = copy.deepcopy(s)
    conds = _conds(p or {})
    steps, swapped = [], []
    for st in s["steps"]:
        if conds & set(st.get("avoid") or []) and st.get("alt"):
            swapped.append(st["n"])
            st = {**st["alt"], "swapped_from": st["n"]}
        steps.append(st)
    s["steps"] = steps
    if swapped:
        s["personal_note"] = ("Pela sua saúde dos ossos, troquei: " + ", ".join(swapped) +
                              " (sem dobrar ou rolar a coluna com carga).")
    return s


def summaries(p: dict | None = None) -> list[dict]:
    return [{k: v for k, v in session(s["id"], p).items() if k != "steps"} | {"n_steps": len(s["steps"])}
            for s in data()["sessions"]]


def suggest(p: dict | None, *, hour: int, run_planned: bool = False, run_done: bool = False, rest_day: bool = False,
            low_readiness: bool = False) -> list[dict]:
    """Até 3 sessões para hoje, cada uma com o motivo."""
    p = p or {}
    a = personal.answers(p)
    lims = {lim.get("key") for lim in p.get("limitations") or []}
    out: list[tuple[int, str, str]] = []

    def add(prio: int, sid: str, why: str):
        if not any(x[1] == sid for x in out):
            out.append((prio, sid, why))

    if run_planned and not run_done:
        add(10, "aquecimento_corrida", "Tem corrida hoje: aqueça antes (sem alongamento parado longo)")
    if run_done:
        add(9, "pos_corrida", "Depois da corrida: soltar e somar tempo de alongamento")
    if hour >= 20:
        add(8, "yoga_sono", "À noite: acalma o corpo para dormir")
    if a.get("stress") in ("moderado", "muito") or low_readiness:
        add(8 if low_readiness else 7, "resp_suspiro" if hour < 20 else "resp_ressonante",
            "Prontidão baixa hoje: 5 min de respiração ajudam" if low_readiness else "Você marcou estresse alto: 5 min por dia ajudam")
        if rest_day:
            add(6, "yoga_estresse", "Dia de descanso: yoga suave para baixar o estresse")
    if "joelho" in lims:
        add(6, "joelho", "Você marcou joelho: fortalecer é o que mais ajuda")
    if "lombar" in lims:
        add(6, "lombar", "Você marcou lombar: tronco forte e coluna em movimento")
    if "ombro" in lims:
        add(6, "ombro", "Você marcou ombro: manguito e escápula fortes")
    if a.get("job") == "sentado" and 8 <= hour < 19:
        add(5, "pausa_escritorio", "Trabalho sentado: uma pausa a cada 60–90 min")
    if rest_day:
        running = bool((((p.get("endurance") or {}).get("sports") or {}).get("corrida") or {}).get("enabled"))
        add(4, "yoga_corredores" if running else "amplitude", "Dia de descanso: bom para ganhar amplitude")
    if not out:
        add(1, "mobilidade_articular", "6 min para soltar as articulações")
    out.sort(key=lambda x: -x[0])
    by_id = {s["id"]: s for s in data()["sessions"]}
    return [{"id": sid, "why": why, "title": by_id[sid]["title"], "minutes": by_id[sid]["minutes"], "icon": by_id[sid]["icon"]}
            for _, sid, why in out[:3]]
