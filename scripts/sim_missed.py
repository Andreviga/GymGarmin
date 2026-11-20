"""Simula a semana de um perfil e o que acontece ao pular/perder um treino ou fazer um treino livre.

Uso (da raiz do repositório):
    python3 scripts/sim_missed.py --profile andre --today 2026-11-19 --skip Empurrar
    python3 scripts/sim_missed.py --profile inter4 --today 2026-11-17 --skip "Inferior A"
    python3 scripts/sim_missed.py --profile andre --today 2026-11-20 --free Barbell_Curl:3,Triceps_Pushdown:3
    python3 scripts/sim_missed.py --list        # perfis disponíveis

Como biblioteca:
    from scripts.sim_missed import Sim
    s = Sim("andre", "2026-11-19"); s.show(); r = s.skip("Empurrar"); s.show()

Mostra, por treino: exercícios principais (séries), séries diretas por músculo NA SESSÃO, minutos, e avisos:
  - REGIÃO: exercício de superior em dia de inferior (ou o contrário) vindo de compensação
  - COLADO: mesmo músculo com ≥4 séries diretas em dias seguidos
  - SESSÃO>10: mais de 10 séries diretas de um músculo numa sessão
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROFILES = {
    # o perfil real do André: PPL 6 dias + corrida 4× no mesmo treino, lombar "cuidado"
    "andre": {"name": "Andre", "age": 34, "sex": "M", "weight_kg": 98, "height_cm": 180, "goal": "hipertrofia", "training_months": 72,
              "available_days": [0, 1, 2, 3, 4, 5, 6], "session_minutes": 90, "limitations": [{"key": "lombar", "severity": "cuidado"}],
              "same_session": True, "endurance": {"sports": {"corrida": {"enabled": True, "env": "esteira", "sessions": 4, "long_day": 6,
                                                                       "goal": {"type": "prova", "distance": 5, "target": "0:20:00", "date": "2027-02-28"}}},
                                               "tests": True}},
    "ini3": {"name": "Rosa", "age": 58, "sex": "F", "weight_kg": 70, "goal": "saude", "training_months": 2, "available_days": [0, 2, 4], "session_minutes": 45},
    "inter4": {"name": "Ana", "age": 31, "sex": "F", "weight_kg": 62, "goal": "hipertrofia", "training_months": 14, "available_days": [0, 1, 3, 4], "session_minutes": 60},
    "avanc5": {"name": "Bia", "age": 29, "sex": "F", "weight_kg": 60, "goal": "hipertrofia", "training_months": 40, "available_days": [0, 1, 2, 3, 4], "session_minutes": 75},
    "forca3": {"name": "Caio", "age": 40, "sex": "M", "weight_kg": 90, "goal": "forca", "training_months": 30, "available_days": [0, 2, 4], "session_minutes": 75},
    "emag5livre": {"name": "Davi", "age": 45, "sex": "M", "weight_kg": 95, "goal": "emagrecimento", "training_months": 8, "available_days": [0, 1, 2, 3, 4, 5], "session_minutes": 50},
    "hibrido4": {"name": "Eva", "age": 35, "sex": "F", "weight_kg": 64, "goal": "hibrido", "training_months": 20, "available_days": [0, 1, 3, 5], "session_minutes": 60,
                 "endurance": {"sports": {"corrida": {"enabled": True, "env": "rua", "sessions": 2}}}},
}
UPPER = {"chest", "lats", "middle back", "shoulders", "biceps", "triceps", "traps", "forearms"}
LOWER = {"quadriceps", "hamstrings", "glutes", "calves", "adductors", "abductors"}


class Sim:
    def __init__(self, profile: str | dict = "andre", today: str = "2026-11-19", seed: int = 0):
        tmp = tempfile.mkdtemp(prefix="haxsim_")
        os.environ["GYMGARMIN_HOME"] = tmp
        os.environ["GYMGARMIN_DB"] = str(Path(tmp) / "sim.sqlite3")
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("VERCEL", None)
        from gymgarmin import catalog, clock, db, generator, schedule
        self.catalog, self.clock, self.db, self.generator, self.schedule = catalog, clock, db, generator, schedule
        db._ready.clear() if hasattr(db, "_ready") else None
        self.set_today(today)
        prof = dict(PROFILES[profile]) if isinstance(profile, str) else dict(profile)
        db.save_profile(prof)
        # a planilha nasce na segunda da semana (como no app: treino gerado e semana montada)
        monday = self.today - timedelta(days=self.today.weekday())
        self.set_today(monday.isoformat())
        plan = generator.generate(db.get_profile(), seed, today=monday)
        plan["generator"]["cycle_start"] = monday.isoformat()
        saved = db.create_plan(plan)
        db.save_profile({"schedule_plan_id": saved["id"]})
        self.plan = saved
        schedule.ensure_week(db.get_profile(), monday, monday)
        self.set_today(today)

    # ---- relógio
    def set_today(self, iso: str, hour: int = 12) -> None:
        self.today = date.fromisoformat(iso)
        self.clock.freeze(datetime.combine(self.today, datetime.min.time()).replace(hour=hour))

    # ---- leitura
    def entries(self) -> list[dict]:
        return self.schedule.ensure_week(self.db.get_profile(), self.today, self.today)

    def find(self, name: str, status: tuple = ("planejado", "perdido")) -> dict:
        for e in self.entries():
            if name.lower() in (e.get("day_name") or "").lower() and e["status"] in status:
                return e
        raise SystemExit(f"treino '{name}' não encontrado: {[e.get('day_name') for e in self.entries()]}")

    def muscles(self, e: dict) -> dict[str, float]:
        """Séries DIRETAS por músculo (músculos primários) dos exercícios principais."""
        out: dict[str, float] = defaultdict(float)
        for it in self.schedule._strength_items(e):
            for m in self.catalog.get(it["exercise_id"])["primaryMuscles"]:
                out[m] += int(it.get("sets") or 0)
        return dict(out)

    def region(self, e: dict) -> str:
        m = self.muscles(e)
        up, lo = sum(v for k, v in m.items() if k in UPPER), sum(v for k, v in m.items() if k in LOWER)
        return "superior" if up > 2 * lo else "inferior" if lo > 2 * up else "misto"

    def warnings(self) -> list[str]:
        es = [e for e in self.entries() if e.get("kind") == "forca" and e["status"] in ("planejado", "feito")]
        out = []
        for e in es:
            base = defaultdict(float)
            for it in self.schedule._strength_items(e):
                if it.get("added_from"):
                    continue
                for m in self.catalog.get(it["exercise_id"])["primaryMuscles"]:
                    base[m] += int(it.get("sets") or 0)
            up, lo = sum(v for k, v in base.items() if k in UPPER), sum(v for k, v in base.items() if k in LOWER)
            reg = "superior" if up > 2 * lo else "inferior" if lo > 2 * up else "misto"
            for it in self.schedule._strength_items(e):
                if it.get("added_from"):
                    prim = set(self.catalog.get(it["exercise_id"])["primaryMuscles"])
                    if (reg == "inferior" and prim & UPPER) or (reg == "superior" and prim & LOWER):
                        out.append(f"REGIÃO: {it['exercise_id']} (de {it['added_from']}) entrou em {e['date']} {e['day_name']} [{reg}]")
            for m, v in self.muscles(e).items():
                if v > 10:
                    out.append(f"SESSÃO>10: {m} com {v:g} séries diretas em {e['date']} {e['day_name']}")
        for a in es:
            for b in es:
                if (date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days == 1:
                    ma, mb = self.muscles(a), self.muscles(b)
                    both = [m for m in ma if ma[m] >= 4 and mb.get(m, 0) >= 4]
                    soft = [m for m in ma if m not in both and ma[m] >= 2 and mb.get(m, 0) >= 6 or (ma[m] >= 6 and 2 <= mb.get(m, 0) < 4)]
                    if both:
                        out.append(f"COLADO: {', '.join(both)} com ≥4 séries em {a['date']} e {b['date']}")
                    if soft:
                        out.append(f"COLADO-leve: {', '.join(soft)} em {a['date']} ({a['day_name']}) e {b['date']} ({b['day_name']})")
        return out

    def show(self, title: str = "") -> None:
        print(f"\n===== {title or 'semana'} (hoje = {self.today}) =====")
        for e in self.entries():
            its = self.schedule._strength_items(e)
            mins = round(self.schedule._minutes(e))
            head = f"{e['date']} {self.schedule.WEEKDAYS[date.fromisoformat(e['date']).weekday()]} [{e['status']:9s}] {e.get('day_name')} ({e.get('kind')}, ~{mins} min)"
            print(head)
            if its:
                print("    " + " | ".join(f"{i['exercise_id']} {i.get('sets')}x{i.get('reps') or str(i.get('duration_s')) + 's'}"
                                          + (f" +{i['added']}" if i.get("added") else "") + (f" <de {i['added_from']}>" if i.get("added_from") else "")
                                          for i in its))
                print("    séries diretas na sessão: " + ", ".join(f"{m} {v:g}" for m, v in sorted(self.muscles(e).items(), key=lambda x: -x[1])))
            for c in e.get("changes") or []:
                print("    * " + c)
        for w in self.warnings():
            print("  !! " + w)

    # ---- ações
    def options(self, name: str) -> dict:
        """As opções de "Não vou treinar" (prévia, nada é gravado)."""
        e = self.find(name)
        r = self.schedule.handle_missed(e["id"], self.db.get_profile(), self.today, dry_run=True)
        print(f"\n>>> opções para {e['date']} {e['day_name']} (recomendado: {r['recommended']} — {r['why']})")
        for o in r["options"]:
            mark = "★" if o.get("recommended") else " "
            print(f"   {mark} [{o['id']}] {o['title']}: " + (o.get("text") or f"indisponível — {o.get('reason')}"))
        for d in r.get("move_days") or []:
            print(f"       dia {d['label']}: " + ("ok" if d["ok"] else f"bloqueado ({d['reason']})")
                  + (f" · avisos: {'; '.join(d['warnings'])}" if d["warnings"] else ""))
        return r

    def skip(self, name: str, strategy: str | None = None, to_date: str | None = None) -> dict:
        e = self.find(name)
        r = self.schedule.handle_missed(e["id"], self.db.get_profile(), self.today, strategy=strategy, to_date=to_date)
        print(f"\n>>> pulou {e['date']} {e['day_name']}: estratégia = {r['strategy']}")
        for c in r["changes"]:
            print("    - " + c)
        if r.get("unrecovered"):
            print("    não coube: " + ", ".join(r["unrecovered"]))
        return r

    def undo(self, name: str) -> None:
        e = self.find(name, status=("pulado",))
        self.schedule.undo(e["id"])
        print(f"\n>>> desfez o ajuste de {e['date']} {e['day_name']}")

    def balance(self) -> list[dict]:
        rows = self.schedule.balance(self.db.get_profile(), self.today, self.today)
        print("\n    saldo da semana: " + "; ".join(f"{b['pt']} {b['projected']:g}/{b['ref']:g}" + (f" (faltam {b['missing']:g})" if b["state"] == "falta" else "")
                                                   for b in rows))
        return rows

    def free(self, items: list[tuple[str, int]], when: str | None = None, reps: int = 10, load: float = 20.0) -> dict:
        """Treino livre (sem vínculo com a agenda), como o botão 'Treino livre' do app."""
        self._n = getattr(self, "_n", 0) + 1
        body = {"date": f"{when or self.today.isoformat()}T18:00", "day_name": "Treino livre", "client_id": f"sim-{self._n}",
                "items": [{"exercise_id": eid, "sets": [{"reps": reps, "load_kg": load} for _ in range(n)]} for eid, n in items]}
        saved = self.db.save_session(body)
        c = self.schedule.credit_session(saved, self.db.get_profile(), self.today)
        what = (f"fechou {c['closed']['day_name']} ({c['closed']['label']}, cobertura {c['closed']['coverage']})" if c["closed"]
                else f"PERGUNTA ({c['question']['kind']}): foi o treino {c['question']['day_name']} de {c['question']['label']}? cobertura {c['question']['coverage']}"
                if c["question"] else "treino extra" + (f" (o de hoje, {c['kept_open']['day_name']}, continua na agenda)" if c["kept_open"] else ""))
        print(f"\n>>> treino livre ({c['work_sets']} séries de trabalho): {what}")
        return c

    def done(self, name: str) -> None:
        """Registra o treino planejado como feito (todas as séries)."""
        e = self.find(name)
        body = {"date": f"{e['date']}T18:00", "schedule_id": e["id"], "day_name": e["day_name"],
                "items": [{"exercise_id": i["exercise_id"], "sets": [{"reps": 8, "load_kg": 30} for _ in range(int(i.get("sets") or 1))]}
                          for i in self.schedule._strength_items(e)]}
        saved = self.db.save_session(body)
        self.schedule.credit_session(saved, self.db.get_profile(), self.today)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="andre")
    ap.add_argument("--today", default="2026-11-19")
    ap.add_argument("--skip", action="append", default=[], help="nome (trecho) do treino a pular; pode repetir")
    ap.add_argument("--strategy", choices=["seguir", "reforcar", "mover"], help="opção escolhida ao pular (padrão: seguir)")
    ap.add_argument("--free", help="treino livre hoje: Exercicio_Id:series,Outro_Id:series")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        for k, v in PROFILES.items():
            print(k, "→", {x: v[x] for x in ("goal", "training_months", "available_days", "session_minutes")})
        raise SystemExit
    s = Sim(a.profile, a.today)
    s.show("antes")
    for name in a.skip:
        s.options(name)
        s.skip(name, a.strategy)
    if a.free:
        s.free([(x.split(":")[0], int(x.split(":")[1])) for x in a.free.split(",")])
    if a.skip or a.free:
        s.balance()
    if a.skip or a.free:
        s.show("depois")
