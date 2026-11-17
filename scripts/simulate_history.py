"""Simulações da evolução de cargas: levantadores virtuais com força real conhecida → o histórico acerta?

Uso:  python scripts/simulate_history.py [--n 60] [--seed 1]

Cada levantador tem um 1RM verdadeiro por exercício que muda com o tempo (cenário) e uma
"resistência de reps" pessoal (a relação reps × carga não é exatamente a de Epley para todo mundo).
A cada treino ele escolhe carga para a faixa de reps e o RPE alvo, faz as séries (com o dia bom/ruim)
e registra — às vezes sem RPE, às vezes com erro de digitação, às vezes pelo Garmin.

Cenários: progride (iniciante/intermediário/avançado) · estagna · regride · deload (a cada 5ª semana) ·
faixas (blocos de 5, 10 e 3 reps com força quase igual) · erro (uma carga digitada ×10) · pausa
(6 semanas parado) · barra (barra fixa só com o corpo e depois com peso) · garmin (séries importadas,
sem RPE, às vezes exercício trocado) · duplo (mesmo exercício duas vezes no dia) · longo (3 anos).

Mede: status (progredindo/estável/estagnado/caindo) × verdade; erro do %/semana; recordes falsos
(recorde anunciado sem a força ter subido) e perdidos; efeito de erro de digitação; deload acusado de
queda; texto; tempo de cálculo com anos de histórico; recorde do fim do treino × histórico.
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gymgarmin import history  # noqa: E402

TODAY = date(2026, 11, 17)
ISSUES: Counter = Counter()
EX: dict = defaultdict(list)
HIT: dict = defaultdict(lambda: [0, 0])
STATS: dict = defaultdict(list)
EXS = ["Barbell_Squat", "Barbell_Bench_Press_-_Medium_Grip", "Barbell_Deadlift", "Standing_Military_Press"]


def issue(k, ex):
    ISSUES[k] += 1
    if len(EX[k]) < 4:
        EX[k].append(ex)


def score(key, ok):
    HIT[key][0] += int(ok)
    HIT[key][1] += 1


def rnd(x, step=2.5):
    return max(step, round(x / step) * step)


def lifter(rng: random.Random, scen: str):
    weeks = 156 if scen == "longo" else 24
    level = rng.choice(["iniciante", "intermediario", "avancado"]) if scen in ("progride", "longo", "deload", "garmin", "duplo") else "intermediario"
    rate = {"iniciante": 0.015, "intermediario": 0.007, "avancado": 0.003}[level]
    k_rep = rng.uniform(0.8, 1.2)          # quantas reps a pessoa aguenta a uma % do 1RM, relativo a Epley
    logs_rpe = rng.random() < 0.7
    per_week = rng.choice([2, 3])
    start = TODAY - timedelta(weeks=weeks)
    base = {e: rng.uniform(50, 140) for e in EXS}
    pause = (weeks // 2 - 3, weeks // 2 + 3) if scen == "pausa" else None

    def true_1rm(e, w):  # w = semana (float)
        b = base[e]
        if scen in ("progride", "deload", "garmin", "duplo", "longo"):
            r = rate if scen != "longo" else rate * (0.6 if w > 52 else 1)
            return b * (1 + r) ** w
        if scen == "estagna":
            return b * (1 + 0.01) ** min(w, 6)
        if scen == "regride":
            peak = b * 1.01 ** (weeks - 8)
            return b * 1.01 ** w if w <= weeks - 8 else peak * (1 - 0.012) ** (w - (weeks - 8))
        if scen == "faixas":
            return b * 1.003 ** w
        if scen == "pausa":
            if w < pause[0]:
                return b * 1.008 ** w
            pk = b * 1.008 ** pause[0]
            if w < pause[1]:
                return pk
            return pk * 0.92 * 1.012 ** (w - pause[1])
        return b * 1.006 ** w  # erro, barra

    sessions, truth = [], defaultdict(list)  # truth[e] = [(date, capacidade do dia, 1RM verdadeiro)]
    sid = 0
    typo_at = rng.randint(int(weeks * 0.4), int(weeks * 0.8)) if scen == "erro" else None
    for w in range(weeks):
        if pause and pause[0] <= w < pause[1]:
            continue
        deload = scen in ("deload", "longo") and w % 5 == 4
        if scen == "faixas":
            block = (w // 4) % 3
            reps, rpe_t = [(5, 8), (10, 8), (3, 8.5)][block]
        else:
            reps, rpe_t = rng.choice([(5, 8), (8, 8), (6, 8.5)]) if w == 0 else (sessions and reps or 8, rpe_t if sessions else 8)
            if w == 0:
                reps, rpe_t = rng.choice([(5, 8), (8, 8), (10, 7.5)])
        if deload:
            rpe_used, sets_n = 6.0, 2
        else:
            rpe_used, sets_n = rpe_t, 3
        days_in_week = sorted(rng.sample(range(7), per_week))
        for di, dw in enumerate(days_in_week):
            d = start + timedelta(weeks=w, days=dw)
            if d >= TODAY:
                continue
            ex_today = EXS[di % 2::2] if per_week > 1 else EXS
            garmin = scen == "garmin" and rng.random() < 0.5
            items = []
            for e in ex_today:
                T = true_1rm(e, w + dw / 7)
                C = T * (1 + rng.gauss(0, 0.025))        # dia bom/ruim
                rir = 10 - rpe_used
                L = rnd(T / (1 + (reps + rir) / (30 * k_rep)))
                rmax = max(1, int(30 * k_rep * (C / L - 1)))
                done = min(reps, rmax)
                actual_rpe = max(5.5, min(10.0, 10 - (rmax - done)))
                logged = round((actual_rpe + rng.choice([-0.5, 0, 0, 0.5])) * 2) / 2 if logs_rpe and not garmin else None
                sets = []
                for j in range(sets_n):
                    rj = max(1, done - (1 if j == sets_n - 1 and rng.random() < 0.4 else 0))
                    s = {"reps": rj, "load_kg": L, "done": True}
                    if logged is not None:
                        s["rpe"] = logged
                    sets.append(s)
                if rng.random() < 0.5:  # séries de aquecimento marcadas
                    sets.insert(0, {"reps": 5, "load_kg": rnd(L * 0.5), "warmup": True, "done": True})
                if typo_at is not None and w == typo_at and di == 0 and e == ex_today[0]:
                    sets[0]["load_kg"] = L * 10  # 60 → 600
                    truth["_typo"].append((d.isoformat(), e))
                eid = e
                if garmin and rng.random() < 0.1:
                    eid = rng.choice(EXS)  # relógio reconheceu outro exercício
                    truth["_mislabel"].append((d.isoformat(), eid))
                items.append({"exercise_id": eid, "sets": sets})
                truth[e].append((d.isoformat(), C, T, deload))
            if scen == "barra":
                bw = 78.0
                T = 1.45 * bw * 1.007 ** w  # 1RM total (corpo + anilha): ~13 barras só com o corpo no início
                C = T * (1 + rng.gauss(0, 0.025))
                added = 0.0 if w < weeks // 2 else rnd(max(2.5, T / (1 + 8 / 30) - bw), 2.5)
                rmax = max(1, int(30 * (C / (bw + added) - 1)))
                items.append({"exercise_id": "Pullups", "sets": [{"reps": min(rmax, 20), "load_kg": added, "done": True}] * 3})
                truth["Pullups"].append((d.isoformat(), C, T, False))
            sid += 1
            sess = {"id": sid, "date": f"{d.isoformat()}T{rng.randint(6, 20):02d}:00", "items": items,
                    "source": "garmin" if garmin else "app"}
            sessions.append(sess)
            if scen == "duplo" and rng.random() < 0.15:
                sid += 1
                sessions.append({"id": sid, "date": f"{d.isoformat()}T21:00", "items": [
                    {"exercise_id": items[0]["exercise_id"], "sets": [{"reps": 12, "load_kg": rnd(items[0]["sets"][-1]["load_kg"] * 0.6), "done": True}] * 2}]})
    return sessions, truth, {"weeks": weeks, "level": level, "k_rep": k_rep, "logs_rpe": logs_rpe}


def true_pct_week(rows):
    """%/semana verdadeiro nas últimas 8 semanas (do 1RM verdadeiro)."""
    rec = [(date.fromisoformat(d), T) for d, _C, T, _dl in rows if date.fromisoformat(d) >= TODAY - timedelta(weeks=8)]
    if len(rec) < 3:
        return None
    a, b = rec[0], rec[-1]
    wks = max(1e-6, (b[0] - a[0]).days / 7)
    return 100 * ((b[1] / a[1]) ** (1 / wks) - 1)


def check(scen, sessions, truth, meta, pid):
    t0 = time.perf_counter()
    ov = history.overview(sessions, TODAY)
    STATS[f"tempo overview ({'3 anos' if scen == 'longo' else '6 meses'}) ms"].append(round(1000 * (time.perf_counter() - t0)))
    rows = {r["exercise_id"]: r for r in ov["exercises"]}
    for e in EXS + (["Pullups"] if scen == "barra" else []):
        if e not in rows or not truth.get(e):
            continue
        sm = rows[e]["summary"]
        tp = true_pct_week(truth[e])
        # ---- status × verdade (só quando o fim do histórico é "limpo": não termina em deload/pausa recente)
        last_deload = truth[e][-1][3]
        if tp is not None:
            if tp >= 0.6:
                truth_cls = "progredindo"
            elif tp <= -0.8:
                truth_cls = "caindo"
            else:
                truth_cls = "parado"
            got = sm["status"]
            ok = (got == "progredindo") if truth_cls == "progredindo" else \
                 (got == "caindo") if truth_cls == "caindo" else got in ("estavel", "estagnado")
            if abs(tp - 0.5) < 0.25 and got in ("progredindo", "estavel", "estagnado"):
                ok = True  # na fronteira entre "progredindo" e "estável" qualquer um dos dois é honesto
            if abs(tp + 1.0) < 0.3 and got in ("caindo", "estavel", "estagnado"):
                ok = True
            score(f"{scen}: status certo ({truth_cls})", ok)
            if not ok and len(EX[f"status errado {scen}"]) < 3:
                EX[f"status errado {scen}"].append((pid, e, truth_cls, got, round(tp, 2), sm.get("pct_per_week")))
            if truth_cls == "progredindo" and got == "caindo":
                issue("acusou queda com a força subindo", (scen, pid, e, round(tp, 2), sm.get("pct_per_week"), last_deload))
            if sm.get("pct_per_week") is not None:
                STATS[f"erro do %/semana (pontos) — {scen}"].append(round(abs(sm["pct_per_week"] - tp), 2))
        # ---- recordes
        hist = history.exercise_history(sessions, e, TODAY)
        tr = {d: (C, T) for d, C, T, _ in truth[e]}
        best_e, best_T = None, None   # melhor 1RM estimado anunciado até aqui e o 1RM verdadeiro daquele dia
        for i, p in enumerate(hist["points"]):
            T = tr.get(p["date"], (None, None))[1]
            if T is None or p.get("suspect") or not p.get("e1rm"):
                continue
            if "e1rm" in p["prs"] and best_e:
                claimed = p["e1rm"] / best_e - 1
                real_gain = T / best_T - 1
                ok = claimed - real_gain <= 0.05 and not (real_gain < -0.01 and claimed > 0.02)
                score(f"{scen}: recorde de 1RM anunciado é real (não inflado)", ok)
                if not ok and len(EX[f"recorde falso {scen}"]) < 3:
                    EX[f"recorde falso {scen}"].append((pid, e, p["date"], p["best_set"], round(100 * claimed, 1), round(100 * real_gain, 1)))
            if best_e is None or (p["e1rm"] > best_e and ("e1rm" in p["prs"] or i == 0 or best_e is None)):
                best_e, best_T = p["e1rm"], T
            # recorde de verdade que passou batido: força real 5%+ acima e nenhum recorde em 6 treinos
        pts = [p for p in hist["points"] if not p.get("suspect")]
        Ts = [T for _d, _C, T, _dl in truth[e]]
        above_peak = len(Ts) > 6 and Ts[-1] > max(Ts[:-6]) * 1.02  # força real acima do pico anterior
        if len(pts) >= 8 and tp is not None and tp >= 0.6 and above_peak:
            score(f"{scen}: com força subindo, anuncia recorde nas últimas 6 sessões", any(p["prs"] for p in pts[-6:]))
        # ---- erro de digitação
        for d, ee in truth.get("_typo", []):
            if ee != e:
                continue
            best = sm["best"]["top_load"]
            real_best = max(s["load_kg"] for x in sessions for it in x["items"] if it["exercise_id"] == e
                            for s in it["sets"] if not s.get("warmup") and x["date"][:10] != d)
            score("erro: carga digitada ×10 não vira recorde permanente", best <= real_best * 1.05)
            score("erro: status não distorcido pelo erro", sm["status"] in ("progredindo", "estavel"))
    # ---- barra fixa: corpo → com peso
    if scen == "barra" and "Pullups" in rows:
        sm = rows["Pullups"]["summary"]
        tpb = true_pct_week(truth["Pullups"]) or 0
        score("barra: status certo ao começar a pôr peso (força subindo)",
              sm["status"] == "progredindo" or (abs(tpb - 0.5) < 0.25 and sm["status"] in ("estavel", "estagnado")))
        if sm.get("change_total_pct") is not None and abs(sm["change_total_pct"]) > 300:
            issue("barra: variação total absurda ao misturar reps e kg", (pid, sm["change_total_pct"], sm["first_value"], sm["last_value"]))
    # ---- deload: não pode virar "queda" logo depois
    if scen == "deload":
        for e in EXS:
            if e in rows and truth[e][-1][3]:
                score("deload: última semana leve não vira 'caindo'", rows[e]["summary"]["status"] != "caindo")
    # ---- recorde do fim do treino × histórico
    last = sessions[-1]
    sp = history.session_prs(sessions, last)
    for pr in sp:
        pts = history.exercise_history(sessions, pr["exercise_id"], TODAY)["points"]
        p = next((x for x in pts if x["date"] == last["date"][:10]), None)
        if not p or set(p["prs"]) != set(pr["prs"]):
            issue("recorde do fim do treino ≠ histórico", (scen, pid, pr["exercise_id"]))
    # ---- texto
    for r in ov["exercises"]:
        m = r["summary"].get("message") or ""
        if "None" in m or "nan" in m.split() or any(c.isdigit() and m[i + 1:i + 2] == "." and m[i + 2:i + 3].isdigit() for i, c in enumerate(m[:-2])):
            issue("texto com valor estranho", (scen, pid, m))


SCEN = ["progride", "estagna", "regride", "deload", "faixas", "erro", "pausa", "barra", "garmin", "duplo", "longo"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    for scen in SCEN:
        n = max(5, a.n // 6) if scen == "longo" else a.n
        for pid in range(n):
            sessions, truth, meta = lifter(rng, scen)
            try:
                check(scen, sessions, truth, meta, pid)
            except Exception as ex:  # noqa: BLE001
                import traceback
                issue("exceção", (scen, pid, repr(ex)[:150], traceback.format_exc(limit=3)[-300:]))
    print(f"\n=== evolução de cargas: {len(SCEN)} cenários (seed {a.seed}) ===")
    print("\n--- acerto (%)")
    for k, (ok, tot) in sorted(HIT.items()):
        pct = 100 * ok / tot if tot else 0
        print(f"{pct:6.1f}%  {k}  (n={tot}){'  ←' if pct < 85 else ''}")
    for k in sorted(EX):
        if k.startswith(("status errado", "recorde falso")):
            print(f"   ex. {k}: {EX[k]}")
    print("\n--- estatísticas")
    for k, v in sorted(STATS.items()):
        s = sorted(v)
        print(f"{k}: mediana {s[len(s) // 2]} · p90 {s[int(len(s) * 0.9)]} · máx {s[-1]} · n {len(s)}")
    if ISSUES:
        print("\n--- problemas")
        for k, n in ISSUES.most_common():
            print(f"{n:5d}  {k}")
            for ex in EX[k]:
                print("        ", str(ex)[:260])


if __name__ == "__main__":
    main()
