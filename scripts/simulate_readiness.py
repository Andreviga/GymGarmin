"""Simulações da prontidão diária: o app decide certo com o que o relógio mede?

Uso:  python scripts/simulate_readiness.py [--n 200] [--days 84] [--seed 1]

Cada atleta tem um ESTADO VERDADEIRO por dia, vindo do treino (fadiga), do sono, do estresse da
vida e de episódios de doença (3–6 dias, com um dia "esquisito" antes). O relógio mede esse estado
com ruído e conforme o modelo: com Training Readiness · sem (sono + HRV + Body Battery) ·
só sono + Body Battery · nada. O app calcula a nota com readiness.score (inclusive forma/TSB e
FC de repouso acima da base), como em produção.

Verdade → ação certa: descansado → seguir · cansado → moderar (seguir aceitável) ·
muito cansado → trocar (moderar/descanso aceitáveis) · doente → descanso (trocar aceitável).

Mede: acerto por tipo de relógio; "perigosos" (doente/muito cansado e o app manda seguir);
"conservador demais" (descansado e o app corta o treino forte); e, em 12 semanas, atletas que
SEGUEM a prontidão × que a IGNORAM: dias doente, treinos fortes feitos em boa condição, carga total.
Confere também os ajustes no treino: forte vira leve, reduções certas, prova e treino feito intocados.
"""
from __future__ import annotations

import argparse
import math
import random
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gymgarmin import endurance as en, readiness  # noqa: E402

DEVICES = ["com_TR", "sono_hrv_bb", "sono_bb", "nada"]
OK = {"descansado": {"seguir"}, "cansado": {"moderar", "seguir"}, "muito_cansado": {"trocar", "moderar", "descanso"},
      "doente": {"descanso", "trocar"}}
BEST = {"descansado": "seguir", "cansado": "moderar", "muito_cansado": "trocar", "doente": "descanso"}
ISSUES: Counter = Counter()
EX: dict = defaultdict(list)


def issue(k, ex):
    ISSUES[k] += 1
    if len(EX[k]) < 4:
        EX[k].append(ex)


def truth_state(R, ill):
    if ill:
        return "doente"
    return "descansado" if R >= 65 else "cansado" if R >= 45 else "muito_cansado"


def run(rng, device, policy, days):
    """Um atleta por `days` dias. policy: 'segue' (faz o que o app manda) ou 'ignora'."""
    hrv_base, rhr_base = rng.uniform(40, 75), rng.uniform(45, 62)
    ctl = atl = 50.0
    fatigue = 0.0
    ill_left, prodrome = 0, False
    rhr_hist = []
    out = Counter()
    decisions = []
    for d in range(days):
        hard_day = d % 7 in (1, 3, 5)
        # doença: chance maior quando a fadiga está alta (e treinar forte doente prolonga)
        if ill_left == 0 and not prodrome and rng.random() < 0.006 + 0.02 * max(0, fatigue - 0.5):
            prodrome = True
        elif prodrome:
            prodrome, ill_left = False, rng.randint(3, 6)
        ill = ill_left > 0
        sleep_def = max(0.0, rng.gauss(0.15, 0.2)) + (0.3 if ill else 0)
        stress = max(0.0, rng.gauss(0.2, 0.15))
        R = 100 - 45 * fatigue - 30 * sleep_def - 15 * stress - (12 if prodrome else 0) + rng.gauss(0, 4)
        state = truth_state(R, ill)
        # o que o relógio mede
        data = {}
        if device != "nada":
            data["sleep_score"] = int(max(20, min(100, 84 - 45 * sleep_def - 8 * ill + rng.gauss(0, 7))))
            data["body_battery_max"] = int(max(5, min(100, 92 - 35 * fatigue - 30 * sleep_def - 25 * ill - (8 if prodrome else 0) + rng.gauss(0, 9))))
        hrv = hrv_base * (1 - 0.22 * fatigue - 0.3 * ill - (0.1 if prodrome else 0) + rng.gauss(0, 0.08))
        if device in ("com_TR", "sono_hrv_bb"):
            ratio = hrv / hrv_base
            data["hrv_status"] = "BALANCED" if ratio >= 0.9 else "UNBALANCED" if ratio >= 0.78 else "LOW"
        if device == "com_TR":
            data["garmin_readiness"] = int(max(1, min(100, R + rng.gauss(0, 7) - (15 if ill else 0))))
        rhr = rhr_base + 5 * fatigue + 8 * ill + (4 if prodrome else 0) + rng.gauss(0, 1.5)
        if device != "nada":
            if len(rhr_hist) >= 14:
                base = st.median(rhr_hist[-35:-3] or rhr_hist)
                delta = st.mean(rhr_hist[-2:] + [rhr]) - base
                data["rhr_delta"] = round(delta, 1)
            rhr_hist.append(rhr)
        form = ctl - atl
        sc = readiness.score(data, form)
        act = sc["action"]
        decisions.append((state, act, hard_day))
        # o dia de treino
        planned = 110 if hard_day else 50
        do = act if policy == "segue" else "seguir"
        load = {"seguir": planned, "moderar": planned * 0.8, "trocar": 45, "descanso": 0}[do]
        if ill and do in ("seguir", "moderar") and hard_day:
            ill_left += 1  # treinar forte doente prolonga a doença
            out["forte doente"] += 1
        if hard_day and do == "seguir":
            out["fortes feitos"] += 1
            if state == "descansado":
                out["fortes de qualidade"] += 1
            elif state in ("muito_cansado", "doente"):
                out["fortes em mau estado"] += 1
        # fadiga responde à carga do dia e à condição
        k = 1.6 if state in ("muito_cansado", "doente") else 1.0
        fatigue = max(0.0, min(1.2, fatigue * 0.86 + k * load / 900 - (0.05 if do == "descanso" else 0)))
        ctl += (load - ctl) / 42
        atl += (load - atl) / 7
        out["carga"] += load
        out["dias doente"] += int(ill)
        if ill_left:
            ill_left -= 1
    return decisions, out


def check_adjustments(rng):
    """Os ajustes no treino fazem sentido?"""
    a = {"run_vdot": 45, "ftp_w": 230, "css_s100": 110}
    for _ in range(200):
        sport = rng.choice(["corrida", "ciclismo", "natacao"])
        kind = rng.choice(["intervalado", "limiar", "longo", "leve", "prova"] if sport == "corrida" else ["limiar", "longo", "leve", "vo2"] if sport == "ciclismo" else ["css", "longo", "leve"])
        vol = {"corrida": rng.choice([8, 12, 21.1]), "ciclismo": rng.choice([1.0, 2.0, 3.0]), "natacao": rng.choice([1500, 2500])}[sport]
        try:
            it = en.build_session(sport, kind, vol, {}, a, "intermediario")
        except Exception:  # noqa: BLE001 - tipo que a modalidade não tem
            continue
        e = it["endurance"]
        entry = {"id": 1, "status": "planejado", "day_name": e["title"], "items": [it], "garmin_workout_id": 5}
        for act in ("moderar", "trocar", "descanso"):
            new = readiness.adjust_entry(entry, act, a)
            if e.get("type") == "prova":
                if new and new["items"][0]["endurance"]["type"] != "prova":
                    issue("ajuste mexeu na prova", (sport, kind, act))
                continue
            if new is None:
                if e["hard"] or (act != "moderar" and e.get("role") == "longo" or kind == "longo" and act != "moderar"):
                    issue("treino forte/longo sem ajuste", (sport, kind, act))
                continue
            ne = new["items"][0]["endurance"]
            if ne["dur_s"] > e["dur_s"] + 1:
                issue("ajuste aumentou a duração", (sport, kind, act, e["dur_s"], ne["dur_s"]))
            if act in ("trocar", "descanso") and ne["hard"]:
                issue("'trocar' manteve sessão forte", (sport, kind, act))
            if act == "moderar" and e["hard"] and ne["dur_s"] > e["dur_s"] * 0.95:
                issue("'moderar' quase não reduziu", (sport, kind, round(ne["dur_s"] / e["dur_s"], 2)))
            if not new.get("garmin_outdated"):
                issue("ajuste sem marcar reenvio ao Garmin", (sport, kind, act))
            back = readiness.undo_entry(new)
            if back["items"] != entry["items"]:
                issue("desfazer não voltou ao original", (sport, kind, act))
        done = {**entry, "status": "feito"}
        if readiness.adjust_entry(done, "trocar", a) is not None:
            issue("ajuste em treino já feito", (sport, kind))
    # musculação
    for act in ("moderar", "trocar", "descanso"):
        e = {"id": 2, "status": "planejado", "items": [{"exercise_id": "Barbell_Squat", "sets": 4, "role": "main", "phase": "principal"},
                                                      {"exercise_id": "Leg_Extensions", "sets": 3, "role": "acc", "phase": "principal"},
                                                      {"exercise_id": "Hip_Circle", "sets": 1, "phase": "preparo"}]}
        new = readiness.adjust_entry(e, act)
        sets = [i["sets"] for i in new["items"]]
        if any(x < 1 for x in sets) or sets[2] != 1:
            issue("musculação: ajuste errado nas séries", (act, sets))
        if sum(sets) >= 8:
            issue("musculação: ajuste não reduziu", (act, sets))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--days", type=int, default=84)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    print(f"\n=== prontidão diária: {a.n} atletas × {a.days} dias por tipo de relógio (seed {a.seed}) ===")
    for device in DEVICES:
        rng = random.Random(a.seed)
        conf = Counter()
        outs = {"segue": Counter(), "ignora": Counter()}
        for i in range(a.n):
            for policy in ("segue", "ignora"):
                r2 = random.Random(a.seed * 10000 + i)  # mesmo atleta nas duas políticas
                dec, out = run(r2, device, policy, a.days)
                outs[policy].update(out)
                if policy == "segue":
                    for state, act, _hard in dec:
                        conf[(state, act)] += 1
        tot = sum(conf.values())
        ok = sum(n for (s, act), n in conf.items() if act in OK[s])
        exact = sum(n for (s, act), n in conf.items() if act == BEST[s])
        danger = sum(n for (s, act), n in conf.items() if s in ("doente", "muito_cansado") and act == "seguir")
        bad_days = sum(n for (s, act), n in conf.items() if s in ("doente", "muito_cansado"))
        over = sum(n for (s, act), n in conf.items() if s == "descansado" and act in ("trocar", "descanso"))
        fresh = sum(n for (s, act), n in conf.items() if s == "descansado")
        sick_ok = sum(n for (s, act), n in conf.items() if s == "doente" and act in OK["doente"])
        sick = sum(n for (s, act), n in conf.items() if s == "doente")
        print(f"\n### relógio: {device}")
        print(f"  decisão aceitável {100 * ok / tot:5.1f}% · exata {100 * exact / tot:5.1f}% · "
              f"perigosa (doente/muito cansado → seguir) {100 * danger / max(1, bad_days):5.1f}% dos dias ruins · "
              f"conservador demais (descansado → trocar/folga) {100 * over / max(1, fresh):5.1f}% dos dias bons · "
              f"doente → folga/leve {100 * sick_ok / max(1, sick):5.1f}%")
        dist = Counter()
        for (s, act), n in conf.items():
            dist[act] += n
        print("  ações: " + " · ".join(f"{k} {100 * v / tot:.0f}%" for k, v in dist.most_common()))
        S, I = outs["segue"], outs["ignora"]
        print(f"  12 semanas — segue × ignora: dias doente {S['dias doente'] / a.n:.1f} × {I['dias doente'] / a.n:.1f} · "
              f"fortes em mau estado {S['fortes em mau estado'] / a.n:.1f} × {I['fortes em mau estado'] / a.n:.1f} · "
              f"fortes de qualidade {S['fortes de qualidade'] / a.n:.1f} × {I['fortes de qualidade'] / a.n:.1f} · "
              f"carga total {S['carga'] / max(1, I['carga']):.2f} da de quem ignora")
    check_adjustments(random.Random(a.seed))
    if ISSUES:
        print("\n--- problemas nos ajustes")
        for k, n in ISSUES.most_common():
            print(f"{n:5d}  {k}")
            for ex in EX[k]:
                print("        ", str(ex)[:220])
    else:
        print("\n--- ajustes no treino: nenhum problema")


if __name__ == "__main__":
    main()
