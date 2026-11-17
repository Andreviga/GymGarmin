/* GymGarmin – app web (vanilla JS, sem build). */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const view = $("#view");
const state = { meta: null, profile: null, draftPlan: null, planTab: 0, session: null };

async function api(path, opts = {}) {
  const o = { ...opts, headers: { "Content-Type": "application/json", ...(opts.headers || {}) } };
  if (o.body && typeof o.body !== "string") o.body = JSON.stringify(o.body);
  const r = await fetch(path, o);
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = j.detail || msg; } catch (_) { /* sem corpo */ }
    throw new Error(msg);
  }
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.text();
}

function toast(msg, ms = 2600) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
}
function store(key, val) { try { if (val === undefined) return JSON.parse(localStorage.getItem(key)); if (val === null) localStorage.removeItem(key); else localStorage.setItem(key, JSON.stringify(val)); } catch (_) { return null; } }

function openModal(html) { $("#modal-body").innerHTML = html; $("#modal").hidden = false; return $("#modal-body"); }
function closeModal() { $("#modal").hidden = true; $("#modal-body").innerHTML = ""; }
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal" || e.target.classList.contains("modal-close")) closeModal(); });

const STATUS_CLASS = { esquecido: "bad", abaixo: "warn", manutencao: "", otimo: "ok", alto: "warn", excesso: "bad" };
const LIM_CLASS = { ok: "ok", cuidado: "warn", evitar: "bad" };
const LIM_TXT = { ok: "liberado", cuidado: "cuidado", evitar: "evitar" };
const fmtDate = (iso) => { try { return new Date(iso).toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" }); } catch (_) { return iso; } };
const zoneOpts = (sel) => [1, 2, 3, 4, 5].map((z) => `<option value="${z}" ${+sel === z ? "selected" : ""}>Z${z}</option>`).join("");

function limBadge(lim) {
  if (!lim || lim.status === "ok") return "";
  return `<span class="pill ${LIM_CLASS[lim.status]}" title="${esc(lim.reasons.join("\n"))}">${LIM_TXT[lim.status]}</span>`;
}

/* ------------------------------------------------------------ barras --- */
function muscleBars(rows, key) {
  const max = Math.max(10, ...rows.map((m) => Math.max(m[key], m.landmarks.mrv * 1.15)));
  const pct = (v) => `${Math.min(100, (v / max) * 100)}%`;
  return `<div class="bars">${rows.filter((m) => m.muscle !== "neck" || m[key] > 0).map((m) => {
    const L = m.landmarks;
    return `<div class="bar-row" title="${esc(m.pt)}: ${m[key]} séries — ${esc(m.status_pt)} (mín. ${L.mev}, ótimo ${L.mav_low}–${L.mav_high}, máx. ${L.mrv})">
      <span class="bar-label">${esc(m.pt)}</span>
      <span class="bar-track">
        <span class="bar-band" style="left:0;width:${pct(L.mev)};background:var(--band-low)"></span>
        <span class="bar-band" style="left:${pct(L.mev)};width:calc(${pct(L.mav_low)} - ${pct(L.mev)});background:var(--band-mid)"></span>
        <span class="bar-band" style="left:${pct(L.mav_low)};width:calc(${pct(L.mav_high)} - ${pct(L.mav_low)});background:var(--band-ok)"></span>
        <span class="bar-band" style="left:${pct(L.mav_high)};width:calc(${pct(L.mrv)} - ${pct(L.mav_high)});background:var(--band-mid)"></span>
        <span class="bar-band" style="left:${pct(L.mrv)};right:0;background:var(--band-low)"></span>
        <span class="bar-fill" style="width:${pct(m[key])}"></span>
      </span>
      <span class="bar-val"><span class="pill ${STATUS_CLASS[m.status]}">${m[key]}</span></span>
    </div>`;
  }).join("")}</div>
  <p class="small muted">Faixas: <span class="pill bad">abaixo do mínimo</span> <span class="pill warn">manutenção / alto</span> <span class="pill ok">ótimo</span> — séries/semana (primário = 1, secundário = 0,5).</p>`;
}

function recsHtml(recs) {
  if (!recs.length) return `<p class="muted">Nada a corrigir. Bom equilíbrio! 💪</p>`;
  return recs.map((r) => `<div class="rec ${r.level}">${esc(r.text)}
    ${r.suggestions && r.suggestions.length ? `<div class="chips" style="margin-top:6px">${r.suggestions.map((s) => `<button class="chip" data-ex="${esc(s.id)}">${esc(s.name)}${s.status === "cuidado" ? " ⚠" : ""}</button>`).join("")}</div>` : ""}
  </div>`).join("");
}

function balanceHtml(bal) {
  return `<ul class="list">${bal.map((b) => `<li><div class="row between gap"><span>${esc(b.label)}</span>
    <span class="pill ${b.status === "ok" ? "ok" : b.status === "sem_dados" ? "muted" : "warn"}">${b.ratio ?? "—"}</span></div>
    <div class="small muted">${esc(b.a)} × ${esc(b.b)} séries · ideal ${b.ideal[0]}–${b.ideal[1]} · ${esc(b.message)}</div></li>`).join("")}</ul>`;
}

/* --------------------------------------------------------------- HOJE --- */
const STATUS_INFO = { planejado: ["Planejado", ""], feito: ["Feito ✔", "ok"], perdido: ["Perdido", "bad"], pulado: ["Não treinou", "warn"], substituido: ["Substituído", "muted"] };
const PHASES = [["preparo", "Preparação (aquecimento + mobilidade)"], ["principal", "Treino"], ["pos", "Pós-treino (alongamento)"]];
const ddmm = (iso) => { const [y, m, d] = iso.slice(0, 10).split("-"); return `${d}/${m}`; };
const addDays = (iso, n) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };

function timeBar(t) {
  if (!t) return "";
  const segs = [["preparo", "Preparo", "var(--c-prep)"], ["forca_trabalho", "Força", "var(--c-work)"], ["forca_descanso", "Descanso", "var(--c-rest)"], ["cardio", "Cardio", "var(--c-cardio)"], ["pos", "Alongamento", "var(--c-post)"]];
  const tot = Math.max(t.total, t.orcamento || 0, 1);
  const on = segs.filter((x) => t[x[0]] > 0);
  return `<div class="tbar">${on.map((x) => `<span style="width:${(t[x[0]] / tot) * 100}%;background:${x[2]}" title="${x[1]}: ${t[x[0]]} min"></span>`).join("")}</div>
    <div class="small muted">${on.map((x) => `<span class="dot" style="background:${x[2]}"></span>${x[1]} ${Math.round(t[x[0]])}′`).join(" ")} · <b>${Math.round(t.total)} min</b>${t.orcamento ? ` de ${t.orcamento}` : ""}</div>`;
}

function itemLine(it) {
  const name = esc(it._name || it._ex?.name || it.exercise_id);
  let d = "";
  if (it.intervals) d = `${it.warmup_min || 0}′ + ${it.intervals.repeats}× (${it.intervals.work_s}s Z${it.intervals.work_zone} / ${it.intervals.rest_s}s) + ${it.cooldown_min || 0}′`;
  else if (it.duration_min) d = `${it.duration_min} min${it.zone ? " Z" + it.zone : ""}`;
  else if (it.sets) d = `${it.sets}×${it.reps || (it.duration_s ? it.duration_s + "s" : "")}${it.rest_s && it.phase !== "preparo" && it.phase !== "pos" ? ` · ${it.rest_s}s` : ""}`;
  const tags = [it.ramp ? `<span class="pill">+${it.ramp} série</span>` : "", it.deload ? `<span class="pill warn">deload</span>` : "", it.added ? `<span class="pill warn">+${it.added}</span>` : "", it.added_from ? `<span class="pill warn">compensação</span>` : "", it.finisher ? `<span class="pill muted">finalizador</span>` : ""].join(" ");
  return `<li><span data-ex="${esc(it.exercise_id)}" style="cursor:pointer">${name}</span> <span class="muted">${esc(d)}</span> ${tags}</li>`;
}

function itemsByPhase(items) {
  return PHASES.map(([ph, label]) => {
    const list = items.filter((i) => (i.phase || "principal") === ph);
    return list.length ? `<div class="small"><b>${label}</b><ul class="plain">${list.map(itemLine).join("")}</ul></div>` : "";
  }).join("");
}

function meter(label, done, target, unit = "") {
  const pct = target ? Math.min(100, (done / target) * 100) : 0;
  return `<div><div class="row between small"><span>${label}</span><b>${done}${unit} / ${target}${unit}</b></div><div class="meter"><span style="width:${pct}%"></span></div></div>`;
}

async function viewHoje(weekDate) {
  const sch = await api(`/api/schedule${weekDate ? `?date=${weekDate}` : ""}`);
  if (!sch.plan) {
    view.innerHTML = `<div class="card"><h2>Monte seu treino automático</h2>
      <p>Diga há quanto tempo treina, quais dias pode ir e quanto tempo tem — o app monta a divisão, escolhe os exercícios respeitando suas limitações, calcula o tempo de cada sessão (aquecimento, séries, descanso, cardio e alongamento) e reorganiza a semana quando você faltar.</p>
      <button class="btn" id="gen-open">Gerar meu treino</button> <a class="btn ghost" href="#planilhas">Montar manualmente</a></div>`;
    $("#gen-open").onclick = () => openGenerator();
    return;
  }
  const g = sch.goals;
  const missed = sch.entries.filter((e) => e.status === "perdido");
  const outdated = sch.entries.some((e) => e.garmin_outdated && e.status === "planejado");
  const cyc = sch.cycle;
  view.innerHTML = `
  ${missed.length ? `<div class="rec alerta"><b>Treino perdido:</b> ${missed.map((e) => `${e.weekday} ${ddmm(e.date)} – ${esc(e.day_name)}`).join(", ")}.
     <div class="row gap wrap" style="margin-top:6px">${missed.map((e) => `<button class="btn sm" data-skip="${e.id}">Ajustar a semana (${e.weekday})</button>`).join("")}</div></div>` : ""}
  <div class="card">
    <div class="row between gap"><button class="icon-btn" data-week="${addDays(sch.week_start, -7)}" title="Semana anterior">‹</button>
      <div class="grow" style="text-align:center"><h2 style="margin:0">Semana de ${ddmm(sch.week_start)}</h2>
        <div class="small muted">${esc(sch.plan.name)}${cyc ? ` · ciclo ${cyc.cycle}, semana ${cyc.week}/${cyc.length}${cyc.deload ? " — <b>DELOAD</b>" : ""}` : ""}</div></div>
      <button class="icon-btn" data-week="${addDays(sch.week_start, 7)}" title="Próxima semana">›</button></div>
    ${cyc && cyc.deload ? `<p class="small rec">Semana de deload: menos séries, carga −10% e RPE ~6 para o corpo assimilar o ciclo.</p>` : ""}
    <div class="stack" style="margin-top:10px">
      ${sch.entries.length ? sch.entries.map((e) => dayCard(e, sch.today)).join("") : `<p class="empty">Nenhum treino nesta semana.</p>`}
    </div>
    <div class="row gap wrap" style="margin-top:10px">
      <button class="btn sm ${outdated ? "" : "ghost"}" id="push-week">${outdated ? "Reenviar alterações ao Garmin" : "Enviar semana ao Garmin"}</button>
      <button class="btn sm ghost" id="gen-open">Regerar treino</button>
      <button class="btn sm ghost" id="rebuild">Refazer semana pela planilha</button>
    </div>
  </div>
  <div class="card"><h2>Metas da semana</h2><div class="stack">
    ${meter("Treinos", g.sessions.done, g.sessions.planned)}
    ${meter("Cardio (min equivalentes OMS)", g.cardio.done, g.cardio.target)}
    ${meter("Séries de força", g.sets.done, g.sets.planned)}
    ${meter("Tempo treinado", g.minutes.done, g.minutes.planned, " min")}
  </div></div>
  <div class="card" id="hoje-recs"><h2>O que ajustar</h2><p class="muted small">Carregando…</p></div>`;
  bindAgenda(sch);
  api("/api/report/weekly").then((rep) => { const b = $("#hoje-recs"); if (b) b.innerHTML = `<h2>O que ajustar</h2>${recsHtml(rep.recommendations.slice(0, 6))}`; }).catch(() => {});
}

function dayCard(e, today) {
  const [label, cls] = STATUS_INFO[e.status] || [e.status, ""];
  const active = e.status === "planejado" || e.status === "perdido";
  return `<div class="day ${e.date === today ? "today" : ""} ${active ? "" : "dim"}">
    <div class="row between gap"><div><b>${e.weekday} ${ddmm(e.date)}</b> · ${esc(e.day_name)}</div><span class="pill ${cls}">${label}</span></div>
    ${active ? timeBar(e.time) : ""}
    ${e.changes && e.changes.length ? `<ul class="small changes">${e.changes.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}
    ${e.skip_result ? `<ul class="small changes">${e.skip_result.changes.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}
    ${e.garmin_outdated ? `<span class="pill warn">Garmin desatualizado</span>` : e.garmin_workout_id ? `<span class="pill ok">no Garmin</span>` : ""}
    <details><summary class="small">Ver treino</summary>${itemsByPhase(e.items)}</details>
    <div class="row gap wrap" style="margin-top:6px">
      ${e.status === "planejado" ? `<a class="btn sm" href="#treinar/agenda/${e.id}">Treinar</a>
        <button class="btn sm ghost" data-skip="${e.id}">Não vou treinar</button>
        <button class="btn sm ghost" data-move="${e.id}" data-date="${e.date}">Mover</button>
        <button class="btn sm ghost" data-push="${e.id}">Garmin</button>
        <a class="btn sm ghost" href="/api/schedule/${e.id}/fit">.FIT</a>` : ""}
      ${e.status === "perdido" ? `<button class="btn sm" data-skip="${e.id}">Ajustar a semana</button><a class="btn sm ghost" href="#treinar/agenda/${e.id}">Registrar mesmo assim</a>` : ""}
      ${e.status === "pulado" && e.can_undo ? `<button class="btn sm ghost" data-undo="${e.id}">Desfazer ajuste</button>` : ""}
    </div></div>`;
}

function bindAgenda(sch) {
  view.onclick = async (ev) => {
    const t = ev.target.closest("button, [data-ex]"); if (!t) return;
    const d = t.dataset;
    try {
      if (d.ex) return showExercise(d.ex);
      if (d.week) { location.hash = `#hoje/${d.week}`; return; }
      if (d.skip) {
        t.disabled = true;
        const r = await api(`/api/schedule/${d.skip}/skip`, { method: "POST" });
        openModal(`<h2>${r.strategy === "mover" ? "Treino remarcado" : "Semana reajustada"}</h2>
          <ul>${r.changes.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
          <p class="small muted">Você pode desfazer pelo card do dia. Treinos já enviados ao Garmin foram marcados para reenviar.</p>
          <button class="btn" id="ok-close">Ok</button>`);
        $("#ok-close").onclick = closeModal;
        return viewHoje(sch.week_start);
      }
      if (d.undo) { await api(`/api/schedule/${d.undo}/undo`, { method: "POST" }); toast("Ajuste desfeito"); return viewHoje(sch.week_start); }
      if (d.move) {
        const days = [0, 1, 2, 3, 4, 5, 6].map((i) => addDays(sch.week_start, i)).filter((x) => x >= sch.today && x !== d.date);
        const body = openModal(`<h2>Mover para…</h2><div class="chips">${days.map((x) => `<button class="chip" data-to="${x}">${["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"][(new Date(x + "T12:00:00").getDay() + 6) % 7]} ${ddmm(x)}</button>`).join("")}</div>`);
        body.onclick = async (e2) => {
          const to = e2.target.dataset.to; if (!to) return;
          const r = await api(`/api/schedule/${d.move}/move`, { method: "POST", body: { date: to } });
          closeModal(); toast(r.warning || "Treino movido", r.warning ? 6000 : 2500); viewHoje(sch.week_start);
        };
        return;
      }
      if (d.push) { t.disabled = true; const r = await api(`/api/schedule/${d.push}/push`, { method: "POST" }); toast(`Enviado ao Garmin para ${r.scheduled}`); return viewHoje(sch.week_start); }
      if (t.id === "push-week") {
        t.disabled = true;
        const r = await api("/api/schedule/push-week", { method: "POST", body: { only_outdated: false } });
        toast(`${r.length} treinos enviados ao Garmin Connect`); return viewHoje(sch.week_start);
      }
      if (t.id === "gen-open") return openGenerator();
      if (t.id === "rebuild") { if (!confirm("Recriar os treinos ainda não feitos desta semana a partir da planilha?")) return; await api("/api/schedule/rebuild", { method: "POST" }); return viewHoje(sch.week_start); }
    } catch (err) { t.disabled = false; toast(err.message, 5000); if (/login|conectado/i.test(err.message)) location.hash = "#perfil"; }
  };
}

/* ------------------------------------------------------------- GERADOR -- */
const MONTH_OPTS = [[2, "Começando (< 3 meses)"], [4, "3 a 6 meses"], [9, "6 a 12 meses"], [18, "1 a 2 anos"], [36, "2 a 5 anos"], [72, "Mais de 5 anos"]];
async function openGenerator() {
  const opts = await api("/api/generator/options");
  const p = state.profile || {};
  const avail = p.available_days || [0, 2, 4];
  const nearest = (m) => MONTH_OPTS.reduce((a, b) => (Math.abs(b[0] - (m ?? 12)) < Math.abs(a[0] - (m ?? 12)) ? b : a))[0];
  let seed = 0;
  const body = openModal(`<h2>Gerar treino automático</h2>
    <div class="grid2">
      <div class="field"><label>Tempo de prática</label><select id="gm-months">${MONTH_OPTS.map(([v, t]) => `<option value="${v}" ${nearest(p.training_months) === v ? "selected" : ""}>${t}</option>`).join("")}</select></div>
      <div class="field"><label>Objetivo</label><select id="gm-goal">${opts.goals.map((g) => `<option value="${g.id}" ${p.goal === g.id ? "selected" : ""}>${esc(g.label)}</option>`).join("")}</select></div>
      <div class="field"><label>Minutos por sessão</label><input id="gm-min" type="number" min="20" max="180" step="5" value="${p.session_minutes || 60}"></div>
      <div class="field"><label>Meta de cardio (min/semana, vazio = pelo objetivo)</label><input id="gm-cardio" type="number" min="0" step="10" value="${p.cardio_target_min ?? ""}"></div>
    </div>
    <label>Dias que você pode treinar</label>
    <div class="chips" id="gm-days">${opts.weekdays.map((w, i) => `<label class="chip"><input type="checkbox" value="${i}" ${avail.includes(i) ? "checked" : ""}> ${w}</label>`).join("")}</div>
    <p class="small muted">Limitações, equipamentos e aparelhos preferidos vêm do seu Perfil.</p>
    <div class="row gap wrap"><button class="btn ghost" id="gm-prev">Pré-visualizar</button><button class="btn ghost" id="gm-var" hidden>Outra variação</button><button class="btn" id="gm-apply" hidden>Usar e montar minha semana</button></div>
    <div id="gm-out" style="margin-top:12px"></div>`);
  const read = () => ({
    training_months: +$("#gm-months", body).value, goal: $("#gm-goal", body).value,
    session_minutes: +$("#gm-min", body).value || 60,
    cardio_target_min: $("#gm-cardio", body).value === "" ? null : +$("#gm-cardio", body).value,
    available_days: $$("#gm-days input", body).filter((c) => c.checked).map((c) => +c.value),
  });
  const preview = async () => {
    const prof = read();
    if (!prof.available_days.length) return toast("Escolha pelo menos um dia");
    $("#gm-out", body).innerHTML = `<p class="muted">Montando…</p>`;
    const plan = await api("/api/generator/preview", { method: "POST", body: { profile: prof, seed } });
    $("#gm-out", body).innerHTML = `<h3>Por que assim</h3><ul class="small">${plan.rationale.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
      ${plan.days.map((d) => `<div class="day"><b>${esc(d.name)}</b>${timeBar(d.time)}${itemsByPhase(d.items.map((i) => ({ ...i, _name: i._name })))}</div>`).join("")}
      <h3>Volume semanal planejado</h3>${muscleBars(plan.analysis.muscles, "sets_week")}`;
    // nomes dos exercícios
    const ids = [...new Set(plan.days.flatMap((d) => d.items.map((i) => i.exercise_id)))];
    const names = {}; await Promise.all(ids.map(async (id) => { try { names[id] = (await api(`/api/exercises/${encodeURIComponent(id)}`)).name; } catch (_) { /* */ } }));
    $$("#gm-out [data-ex]", body).forEach((el) => { if (names[el.dataset.ex]) el.textContent = names[el.dataset.ex]; });
    $("#gm-var", body).hidden = false; $("#gm-apply", body).hidden = false;
  };
  $("#gm-prev", body).onclick = () => preview().catch((e) => toast(e.message));
  $("#gm-var", body).onclick = () => { seed += 1; preview().catch((e) => toast(e.message)); };
  $("#gm-apply", body).onclick = async () => {
    await api("/api/generator/apply", { method: "POST", body: { profile: read(), seed } });
    state.profile = await api("/api/profile");
    closeModal(); toast("Treino gerado e semana montada!");
    if (location.hash.startsWith("#hoje") || !location.hash) route(); else location.hash = "#hoje";
  };
}

/* ----------------------------------------------------------- PLANILHAS -- */
async function viewPlanilhas() {
  const plans = await api("/api/plans");
  view.innerHTML = `
  <div class="card"><div class="row between wrap gap"><h2>Suas planilhas</h2>
    <div class="row gap wrap"><button class="btn sm" id="gen-plan">Gerar automático</button><button class="btn sm ghost" id="new-plan">Nova</button><button class="btn sm ghost" id="tpl-plan">Modelos</button><button class="btn sm ghost" id="csv-plan">Importar CSV</button></div></div>
    ${plans.length ? `<ul class="list">${plans.map((p) => `<li><a href="#plano/${p.id}" class="row between" style="text-decoration:none;color:inherit">
      <span><b>${esc(p.name)}</b><br><span class="small muted">${p.days.length} treinos · ${p.days_per_week || p.days.length}×/semana</span></span>
      <span class="pill ${p.active ? "ok" : "muted"}">${p.active ? "ativa" : "arquivada"}</span></a></li>`).join("")}</ul>`
      : `<p class="empty">Crie uma planilha do zero, a partir de um modelo ou importando um CSV (Excel/Google Planilhas).</p>`}
  </div>`;
  $("#gen-plan").onclick = () => openGenerator();
  $("#new-plan").onclick = async () => {
    const p = await api("/api/plans", { method: "POST", body: { name: "Minha planilha", days_per_week: state.profile.days_per_week || 3, days: [{ name: "A", items: [] }, { name: "B", items: [] }] } });
    location.hash = `#plano/${p.id}`;
  };
  $("#tpl-plan").onclick = () => {
    const body = openModal(`<h2>Modelos</h2><p class="small muted">Exercícios marcados como “evitar” nas suas limitações são trocados automaticamente.</p>
      <ul class="list">${state.meta.templates.map((t) => `<li><b>${esc(t.name)}</b><div class="small muted">${esc(t.description)}</div>
      <button class="btn sm" style="margin-top:6px" data-tpl="${t.id}">Usar este modelo</button></li>`).join("")}</ul>`);
    body.onclick = async (e) => {
      const id = e.target.dataset.tpl; if (!id) return;
      const p = await api("/api/plans", { method: "POST", body: { template: id } });
      closeModal(); if (p.notes) toast(p.notes, 6000); location.hash = `#plano/${p.id}`;
    };
  };
  $("#csv-plan").onclick = () => {
    const body = openModal(`<h2>Importar planilha (CSV)</h2>
      <p class="small muted">No Excel/Google Planilhas: Arquivo → Baixar/Salvar como → CSV. Colunas: <code>treino; exercicio; series; reps; carga_kg; descanso_s; tempo_s; duracao_min; zona; obs</code>. Nomes em português ou inglês.</p>
      <div class="field"><label>Nome</label><input id="csv-name" value="Planilha importada"></div>
      <div class="field"><label>Arquivo</label><input type="file" id="csv-file" accept=".csv,text/csv"></div>
      <div class="field"><label>…ou cole o conteúdo</label><textarea id="csv-text" placeholder="treino;exercicio;series;reps;carga_kg;descanso_s&#10;A;Supino reto com barra;4;8-12;60;120&#10;A;Esteira caminhada;;;;;;10;2"></textarea></div>
      <div class="row gap"><button class="btn ghost" id="csv-preview">Pré-visualizar</button><button class="btn" id="csv-save">Importar</button></div>
      <div id="csv-out" style="margin-top:10px"></div>`);
    $("#csv-file", body).onchange = async (e) => { const f = e.target.files[0]; if (f) $("#csv-text", body).value = await f.text(); };
    const run = async (save) => {
      const res = await api("/api/plans/import-csv", { method: "POST", body: { csv: $("#csv-text", body).value, name: $("#csv-name", body).value, save } });
      $("#csv-out", body).innerHTML = `<p class="small">${res.matched.length} exercícios reconhecidos${res.unmatched.length ? `, <b>${res.unmatched.length} não encontrados</b>` : ""}.</p>
        <ul class="list small">${res.matched.map((m) => `<li>linha ${m.line}: “${esc(m.input)}” → <b>${esc(m.matched)}</b></li>`).join("")}
        ${res.unmatched.map((m) => `<li class="pill bad">linha ${m.line}: “${esc(m.name)}” não encontrado</li>`).join("")}</ul>`;
      if (save && res.saved) { closeModal(); location.hash = `#plano/${res.saved.id}`; }
    };
    $("#csv-preview", body).onclick = () => run(false).catch((e) => toast(e.message));
    $("#csv-save", body).onclick = () => run(true).catch((e) => toast(e.message));
  };
}

/* -------------------------------------------------------- editor plano -- */
async function viewPlano(id) {
  state.draftPlan = await api(`/api/plans/${id}`);
  state.planTab = Math.min(state.planTab, Math.max(0, state.draftPlan.days.length - 1));
  renderPlano();
}

function renderPlano() {
  const p = state.draftPlan;
  const day = p.days[state.planTab];
  view.innerHTML = `
  <div class="card">
    <div class="row gap"><input id="plan-name" value="${esc(p.name)}" aria-label="Nome da planilha" class="grow">
      <button class="btn" id="plan-save">Salvar</button></div>
    <div class="row gap wrap" style="margin-top:8px">
      <label class="row gap small" style="margin:0">Treinos por semana <input id="plan-dpw" class="num" type="number" min="1" max="14" value="${p.days_per_week || p.days.length}"></label>
      <label class="row gap small" style="margin:0"><input type="checkbox" id="plan-active" ${p.active ? "checked" : ""}> ativa</label>
    </div>
    ${p.notes ? `<p class="small muted">${esc(p.notes)}</p>` : ""}
  </div>
  <div class="tabs">${p.days.map((d, i) => `<button data-day="${i}" class="${i === state.planTab ? "on" : ""}">${esc(d.name)}</button>`).join("")}
    <button id="add-day">+ treino</button></div>
  ${day ? `<div class="card">
    <div class="row gap"><input id="day-name" value="${esc(day.name)}" class="grow" aria-label="Nome do treino">
      <button class="icon-btn" id="del-day" title="Excluir treino">🗑</button></div>
    <div id="day-time" style="margin-top:8px"></div>
    <div style="margin-top:10px">${day.items.map((it, i) => planItemHtml(it, i, day.items.length)).join("") || `<p class="empty">Adicione exercícios ou cardio.</p>`}</div>
    <div class="row gap wrap"><button class="btn sm" id="add-ex">+ Exercício</button><button class="btn sm ghost" id="add-cardio">+ Cardio</button></div>
  </div>
  <div class="card"><h2>Enviar ao Garmin</h2>
    <p class="small muted">Salve antes de exportar. <b>.FIT</b>: copie via USB para <code>GARMIN/NewFiles</code> no relógio. <b>Garmin Connect</b>: envia direto para sua biblioteca de treinos (e agenda no calendário), sincronizando com o relógio.</p>
    <div class="row gap wrap">
      <a class="btn sm ghost" href="/api/plans/${p.id}/days/${state.planTab}/fit">Baixar .FIT</a>
      <input type="date" id="push-date" style="width:auto" value="${new Date().toISOString().slice(0, 10)}">
      <button class="btn sm" id="push">Enviar ao Garmin Connect</button>
      <button class="btn sm ghost" id="show-json">Ver JSON</button>
    </div>
  </div>` : ""}
  <div class="card" id="plan-analysis"><h2>Análise da planilha</h2><p class="muted small">Calculando…</p></div>
  <div class="row gap wrap" style="margin-bottom:16px"><a class="btn sm ghost" href="/api/plans/${p.id}/csv">Exportar CSV</a><button class="btn sm danger" id="del-plan">Excluir planilha</button></div>`;
  bindPlano();
  refreshPlanAnalysis();
}

function planItemHtml(it, i, n) {
  const ex = it._ex || {};
  const isCardio = (it.exercise_id || "").startsWith("cardio_") || ex.kind === "cardio";
  const name = ex.name || it._name || it.exercise_id;
  const f = (k, label, type = "text", extra = "") => `<div><label>${label}</label><input data-f="${k}" data-i="${i}" type="${type}" value="${esc(it[k] ?? "")}" ${extra}></div>`;
  return `<div class="plan-item">
    <div class="row gap"><span class="grow"><b class="link" data-ex="${esc(it.exercise_id)}" style="cursor:pointer">${esc(name)}</b>
      ${it.phase === "preparo" ? `<span class="pill">preparo</span>` : it.phase === "pos" ? `<span class="pill">pós</span>` : ""}${it.finisher ? ` <span class="pill muted">finalizador</span>` : ""} ${limBadge(it._lim)}</span>
      <button class="icon-btn" data-mv="${i}:-1" ${i === 0 ? "disabled" : ""} title="Subir">↑</button>
      <button class="icon-btn" data-mv="${i}:1" ${i === n - 1 ? "disabled" : ""} title="Descer">↓</button>
      <button class="icon-btn" data-rm="${i}" title="Remover">✕</button></div>
    <div class="fields">
      ${isCardio ? `${f("duration_min", "Minutos", "number", 'min="1"')}
        <div><label>Zona FC</label><select data-f="zone" data-i="${i}">${zoneOpts(it.zone || 2)}</select></div>
        ${f("notes", "Obs. (vel./incl./nível)")}`
      : `${f("sets", "Séries", "number", 'min="1"')}${f("reps", "Reps")}${f("load_kg", "Carga kg", "number", 'step="0.5"')}
        ${f("rest_s", "Descanso s", "number", 'step="15"')}${f("duration_s", "Tempo s", "number")}${f("notes", "Obs.")}`}
    </div>
    ${isCardio && it.intervals ? `<p class="small muted">Intervalado: ${it.intervals.repeats}× ${it.intervals.work_s}s Z${it.intervals.work_zone} / ${it.intervals.rest_s}s Z${it.intervals.rest_zone}</p>` : ""}
  </div>`;
}

function readPlanForm() {
  const p = state.draftPlan;
  p.name = $("#plan-name").value.trim() || "Minha planilha";
  p.days_per_week = +$("#plan-dpw").value || p.days.length;
  p.active = $("#plan-active").checked;
  const day = p.days[state.planTab];
  if (day) {
    day.name = $("#day-name").value.trim() || day.name;
    $$("[data-f]").forEach((el) => {
      const it = day.items[+el.dataset.i]; const k = el.dataset.f; let v = el.value;
      if (["sets", "rest_s", "duration_s", "duration_min", "zone"].includes(k)) v = v === "" ? null : +v;
      else if (k === "load_kg") v = v === "" ? null : parseFloat(v);
      else if (k === "reps") v = /^\d+$/.test(v) ? +v : (v || null);
      if (v === null || v === "") delete it[k]; else it[k] = v;
    });
  }
}

function cleanPlan(p) {
  return { ...p, days: p.days.map((d) => ({ ...d, items: d.items.map((it) => Object.fromEntries(Object.entries(it).filter(([k]) => !k.startsWith("_")))) })) };
}

let analysisTimer;
function refreshPlanAnalysis() {
  clearTimeout(analysisTimer);
  analysisTimer = setTimeout(async () => {
    const box = $("#plan-analysis"); if (!box) return;
    try {
      const a = await api("/api/plans/analyze", { method: "POST", body: cleanPlan(state.draftPlan) });
      const conflicts = a.conflicts.map((c) => `<li><span class="pill ${LIM_CLASS[c.status]}">${LIM_TXT[c.status]}</span> <b>${esc(c.name)}</b> <span class="small muted">(${esc(c.day)})</span><div class="small muted">${esc(c.reasons.join(" · "))}</div></li>`).join("");
      const dt = $("#day-time"); if (dt && a.times) dt.innerHTML = timeBar(a.times[state.planTab]);
      box.innerHTML = `<h2>Análise da planilha</h2>
        <p class="small muted">Volume semanal planejado com ${a.days_per_week} treinos/semana · cardio ${a.cardio_minutes_week} min (${a.cardio_who_equivalent} equivalentes OMS).</p>
        ${muscleBars(a.muscles, "sets_week")}
        ${conflicts ? `<h3>Conflitos com suas limitações</h3><ul class="list">${conflicts}</ul>` : ""}
        <h3>Equilíbrio</h3>${balanceHtml(a.balance)}
        <h3>Sugestões</h3>${recsHtml(a.recommendations)}`;
      // marca limitações nos itens
      const lims = {}; a.conflicts.forEach((c) => (lims[c.exercise_id] = c));
      let changed = false;
      state.draftPlan.days.forEach((d) => d.items.forEach((it) => { const l = lims[it.exercise_id] || null; if ((it._lim || null) !== l) { it._lim = l; changed = true; } }));
      if (changed) $$(".plan-item").forEach((el, i) => { const it = state.draftPlan.days[state.planTab]?.items[i]; const b = $(".grow", el); if (it && b) { const old = $(".pill", b); if (old) old.remove(); b.insertAdjacentHTML("beforeend", limBadge(it._lim)); } });
    } catch (e) { box.innerHTML = `<p class="pill bad">${esc(e.message)}</p>`; }
  }, 250);
}

async function savePlan(quiet) {
  readPlanForm();
  const saved = await api(`/api/plans/${state.draftPlan.id}`, { method: "PUT", body: cleanPlan(state.draftPlan) });
  const names = {}; state.draftPlan.days.forEach((d) => d.items.forEach((it) => (names[it.exercise_id] = it._ex)));
  saved.days.forEach((d) => d.items.forEach((it) => (it._ex = names[it.exercise_id])));
  state.draftPlan = saved;
  if (!quiet) toast("Planilha salva");
}

function bindPlano() {
  const p = state.draftPlan;
  const ensureNames = async () => {
    const missing = new Set(); p.days.forEach((d) => d.items.forEach((it) => { if (!it._ex) missing.add(it.exercise_id); }));
    if (!missing.size) return;
    await Promise.all([...missing].map(async (id) => { try { const ex = await api(`/api/exercises/${encodeURIComponent(id)}`); p.days.forEach((d) => d.items.forEach((it) => { if (it.exercise_id === id) it._ex = ex; })); } catch (_) { /* id desconhecido */ } }));
    renderPlano();
  };
  ensureNames();
  $("#plan-save").onclick = () => savePlan().catch((e) => toast(e.message));
  view.oninput = (e) => { if (e.target.matches("[data-f], #plan-dpw")) { readPlanForm(); refreshPlanAnalysis(); } };
  view.onchange = view.oninput;
  $$("[data-day]").forEach((b) => (b.onclick = () => { readPlanForm(); state.planTab = +b.dataset.day; renderPlano(); }));
  $("#add-day").onclick = () => { readPlanForm(); p.days.push({ name: String.fromCharCode(65 + p.days.length), items: [] }); state.planTab = p.days.length - 1; renderPlano(); };
  const dd = $("#del-day"); if (dd) dd.onclick = () => { if (!confirm("Excluir este treino?")) return; p.days.splice(state.planTab, 1); state.planTab = Math.max(0, state.planTab - 1); renderPlano(); };
  $$("[data-rm]").forEach((b) => (b.onclick = () => { readPlanForm(); p.days[state.planTab].items.splice(+b.dataset.rm, 1); renderPlano(); }));
  $$("[data-mv]").forEach((b) => (b.onclick = () => { readPlanForm(); const [i, d] = b.dataset.mv.split(":").map(Number); const items = p.days[state.planTab].items; [items[i], items[i + d]] = [items[i + d], items[i]]; renderPlano(); }));
  const addEx = (kind) => pickExercise(kind, (ex) => {
    readPlanForm();
    const item = ex.kind === "cardio" ? { exercise_id: ex.id, duration_min: 10, zone: 2 }
      : { exercise_id: ex.id, sets: 3, reps: ex.timed ? undefined : "8-12", rest_s: 90, ...(ex.timed ? { duration_s: 30 } : {}) };
    item._ex = ex; p.days[state.planTab].items.push(item); renderPlano();
  });
  const ae = $("#add-ex"); if (ae) ae.onclick = () => addEx(null);
  const ac = $("#add-cardio"); if (ac) ac.onclick = () => addEx("cardio");
  $("#del-plan").onclick = async () => { if (!confirm("Excluir a planilha inteira?")) return; await api(`/api/plans/${p.id}`, { method: "DELETE" }); location.hash = "#planilhas"; };
  const push = $("#push");
  if (push) push.onclick = async () => {
    push.disabled = true;
    try {
      await savePlan(true);
      const r = await api(`/api/plans/${p.id}/days/${state.planTab}/push`, { method: "POST", body: { date: $("#push-date").value || null } });
      openModal(`<h2>Enviado ✔</h2><p>“${esc(r.name)}” está na sua biblioteca do Garmin Connect${r.scheduled ? ` e agendado para <b>${esc(r.scheduled)}</b>` : ""}.</p>
        <p class="small muted">Sincronize o relógio pelo app Garmin Connect. ${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">Abrir no Garmin Connect</a>` : ""}</p>`);
    } catch (e) { toast(e.message, 5000); if (/login|conectado/i.test(e.message)) location.hash = "#perfil"; }
    finally { push.disabled = false; }
  };
  const sj = $("#show-json");
  if (sj) sj.onclick = async () => {
    await savePlan(true);
    const j = await api(`/api/plans/${p.id}/days/${state.planTab}/connect.json`);
    openModal(`<h2>JSON do Garmin Connect</h2><p class="small muted">Formato do editor de treinos (workout-service).</p>
      <a class="btn sm ghost" href="/api/plans/${p.id}/days/${state.planTab}/connect.json?download=1">Baixar</a><pre class="json">${esc(JSON.stringify(j, null, 2))}</pre>`);
  };
  view.onclick = (e) => { const id = e.target.dataset && e.target.dataset.ex; if (id) showExercise(id); };
}

/* ----------------------------------------------------- seletor exercício */
function pickExercise(kind, onPick) {
  const body = openModal(`<h2>${kind === "cardio" ? "Aparelho de cardio" : "Adicionar exercício"}</h2>
    <div class="row gap"><input id="pk-q" placeholder="Buscar (ex.: supino, remada, esteira)" autofocus class="grow">
      <select id="pk-m" style="width:auto"><option value="">Músculo</option>${state.meta.muscles.map((m) => `<option value="${m.key}">${esc(m.pt)}</option>`).join("")}</select></div>
    <label class="row gap small" style="margin:8px 0"><input type="checkbox" id="pk-hide" checked> ocultar exercícios a evitar</label>
    <ul class="list" id="pk-list"></ul>`);
  let t;
  const load = async () => {
    const qs = new URLSearchParams({ q: $("#pk-q", body).value, limit: 40, hide_avoid: $("#pk-hide", body).checked });
    if (kind) qs.set("kind", kind);
    if ($("#pk-m", body).value) qs.set("muscle", $("#pk-m", body).value);
    const res = await api(`/api/exercises?${qs}`);
    $("#pk-list", body).innerHTML = res.map((ex) => `<li class="ex-item" data-pick="${esc(ex.id)}">
      ${ex.images[0] ? `<img class="thumb" loading="lazy" src="${esc(ex.images[0])}" alt="">` : `<span class="thumb"></span>`}
      <span class="grow"><b>${esc(ex.name)}</b> ${limBadge(ex.limitation)}<br><span class="small muted">${esc(ex.primary_pt.join(", "))} · ${esc(ex.equipment_pt || "")}</span></span></li>`).join("") || `<li class="empty">Nada encontrado.</li>`;
    body._res = res;
  };
  body.oninput = () => { clearTimeout(t); t = setTimeout(load, 200); };
  body.onchange = body.oninput;
  body.onclick = (e) => { const li = e.target.closest("[data-pick]"); if (!li) return; const ex = body._res.find((x) => x.id === li.dataset.pick); closeModal(); onPick(ex); };
  load();
}

/* --------------------------------------------------- detalhe exercício -- */
async function showExercise(id) {
  const ex = await api(`/api/exercises/${encodeURIComponent(id)}`);
  const g = ex.garmin || {};
  const body = openModal(`<h2>${esc(ex.name)}</h2>
    ${ex.name !== ex.name_en ? `<p class="small muted">${esc(ex.name_en)}</p>` : ""}
    <div class="imgs">${ex.images.map((s) => `<img loading="lazy" src="${esc(s)}" alt="">`).join("")}</div>
    <p><span class="pill">${esc(ex.category_pt)}</span> <span class="pill muted">${esc(ex.equipment_pt || "")}</span> <span class="pill muted">${esc(ex.level_pt || "")}</span>
      ${ex.mechanic_pt && ex.mechanic_pt !== "—" ? `<span class="pill muted">${esc(ex.mechanic_pt)}</span>` : ""} ${ex.impact ? `<span class="pill muted">impacto ${esc(ex.impact)}</span>` : ""}</p>
    <p><b>Primários:</b> ${esc(ex.primary_pt.join(", ") || "—")}<br><b>Secundários:</b> ${esc(ex.secondary_pt.join(", ") || "—")}</p>
    ${ex.limitation.status !== "ok" ? `<div class="rec ${ex.limitation.status === "evitar" ? "alerta" : "atencao"}"><b>${LIM_TXT[ex.limitation.status].toUpperCase()}</b><br>${ex.limitation.reasons.map(esc).join("<br>")}</div>` : ex.limitation.reasons.length ? `<div class="rec">${ex.limitation.reasons.map(esc).join("<br>")}</div>` : ""}
    ${ex.last ? `<p class="small"><b>Última vez</b> (${fmtDate(ex.last.date)}): ${ex.last.sets ? ex.last.sets.map((s) => `${s.reps ?? "-"}×${s.load_kg ?? 0}kg`).join(", ") : `${ex.last.duration_min} min Z${ex.last.zone}`}</p>` : ""}
    ${ex.instructions && ex.instructions.length ? `<details><summary>Execução</summary><ol class="small">${ex.instructions.map((s) => `<li>${esc(s)}</li>`).join("")}</ol></details>` : ""}
    <div class="hr"></div>
    <div class="row between gap wrap"><div class="small">No Garmin: <b>${esc(g.garmin_name || g.category)}</b> <span class="muted">(${esc(g.category)} / ${esc(g.exercise)})</span>
      <span class="pill ${g.confidence === "alta" || g.confidence === "manual" ? "ok" : "warn"}">${esc(g.confidence)}</span></div>
      ${ex.machine ? "" : `<button class="btn sm ghost" id="g-edit">Alterar</button>`}</div>
    <div id="g-box"></div>`);
  const ge = $("#g-edit", body);
  if (ge) ge.onclick = () => {
    $("#g-box", body).innerHTML = `<input id="g-q" placeholder="Buscar no catálogo do Garmin (inglês)" value="${esc(ex.name_en.split(" ").slice(0, 2).join(" "))}"><ul class="list small" id="g-list"></ul>`;
    const run = async () => {
      const res = await api(`/api/garmin/catalog?q=${encodeURIComponent($("#g-q", body).value)}`);
      $("#g-list", body).innerHTML = res.map((r) => `<li><button class="chip" data-gc="${esc(r.category)}" data-ge="${esc(r.exercise)}">${esc(r.name)}</button> <span class="muted">${esc(r.category)}</span></li>`).join("");
    };
    $("#g-q", body).oninput = run; run();
    $("#g-list", body).onclick = async (e) => {
      const b = e.target.closest("[data-gc]"); if (!b) return;
      await api(`/api/exercises/${encodeURIComponent(ex.id)}/garmin`, { method: "PUT", body: { category: b.dataset.gc, exercise: b.dataset.ge } });
      toast("Mapeamento Garmin atualizado"); showExercise(ex.id);
    };
  };
}

/* ------------------------------------------------------------- TREINAR -- */
async function viewTreinar(planId, idx) {
  if (planId === "agenda") {
    const draft = store("gg_session");
    if (draft && String(draft.schedule_id) === String(idx)) state.session = draft;
    else state.session = await api(`/api/schedule/${idx}/prefill`);
    return renderSession();
  }
  if (planId === undefined) {
    const draft = store("gg_session");
    const plans = (await api("/api/plans")).filter((p) => p.active);
    view.innerHTML = `${draft ? `<div class="card"><h2>Treino em andamento</h2><p>${esc(draft.day_name || "Treino livre")} — ${fmtDate(draft.date)}</p>
      <div class="row gap"><button class="btn" id="resume">Continuar</button><button class="btn danger" id="discard">Descartar</button></div></div>` : ""}
      <div class="card"><h2>Escolha o treino</h2>${plans.map((p) => `<p class="small muted">${esc(p.name)}</p><div class="chips">${p.days.map((d, i) => `<a class="btn sm ghost" href="#treinar/${p.id}/${i}">${esc(d.name)}</a>`).join("")}</div>`).join("") || `<p class="muted">Sem planilhas ativas.</p>`}
      <div class="hr"></div><button class="btn ghost" id="free">Treino livre</button></div>`;
    const r = $("#resume"); if (r) r.onclick = () => { state.session = draft; renderSession(); };
    const d = $("#discard"); if (d) d.onclick = () => { store("gg_session", null); viewTreinar(); };
    $("#free").onclick = () => { state.session = { date: new Date().toISOString().slice(0, 16), day_name: "Treino livre", items: [] }; renderSession(); };
    return;
  }
  const draft = store("gg_session");
  if (draft && String(draft.plan_id) === String(planId) && draft.day_name && draft._idx === +idx) state.session = draft;
  else { state.session = await api(`/api/sessions/prefill/${planId}/${idx}`); state.session._idx = +idx; }
  renderSession();
}

function renderSession() {
  const s = state.session;
  store("gg_session", s);
  const idxs = s.items.map((it, i) => [it, i]);
  const phaseOf = (it) => it.phase || "principal";
  const compact = (ph) => idxs.filter(([it]) => phaseOf(it) === ph);
  const compactCard = (ph, title) => compact(ph).length ? `<div class="card"><h3>${title}</h3><ul class="list">${compact(ph).map(([it, i]) => `<li class="row gap">
      <button class="check ${it.done ? "on" : ""}" data-pdone="${i}">✓</button>
      <span class="grow"><b data-ex="${esc(it.exercise_id)}" style="cursor:pointer">${esc(it.name)}</b><br><span class="small muted">${it.kind === "cardio" ? `${it.duration_min} min Z${it.zone}` : `${(it.plan && it.plan.duration_s) || 30}s${(it.plan && it.plan.sets) > 1 ? ` × ${it.plan.sets}` : ""}`}${it.plan && it.plan.notes ? " · " + esc(it.plan.notes) : ""}</span></span></li>`).join("")}</ul></div>` : "";
  view.innerHTML = `<div class="card"><div class="row between gap"><h2 class="grow">${esc(s.day_name || "Treino")}</h2>
      <input type="datetime-local" id="s-date" value="${esc((s.date || "").slice(0, 16))}" style="width:auto"></div>
      ${s.deload ? `<p class="small rec">Semana de deload: menos séries, carga −10%, RPE ~6.</p>` : ""}</div>
    ${compactCard("preparo", "Preparação — aquecimento e mobilidade")}
    ${idxs.filter(([it]) => phaseOf(it) === "principal").map(([it, i]) => sessionItemHtml(it, i)).join("")}
    ${compactCard("pos", "Pós-treino — alongamento")}
    <div class="card"><button class="btn sm ghost" id="s-add">+ Adicionar exercício</button>
      <div class="field" style="margin-top:10px"><label>Como foi o treino?</label><textarea id="s-notes" placeholder="Sensações, dores, energia…">${esc(s.notes || "")}</textarea></div>
      <div class="row gap"><button class="btn" id="s-finish">Finalizar e salvar</button><button class="btn danger" id="s-cancel">Descartar</button></div></div>`;
  bindSession();
}

function sessionItemHtml(it, i) {
  const plan = it.plan || {};
  const lastTxt = it.last ? (it.last.sets ? it.last.sets.map((x) => `${x.reps ?? "-"}×${x.load_kg ?? 0}`).join(" · ") : `${it.last.duration_min} min Z${it.last.zone}`) : "";
  const head = `<div class="row gap">${it.images && it.images[0] ? `<img class="thumb" src="${esc(it.images[0])}" alt="" loading="lazy">` : ""}
    <div class="grow"><b data-ex="${esc(it.exercise_id)}" style="cursor:pointer">${esc(it.name)}</b> ${limBadge(it.limitation)}
    <div class="small muted">${it.kind === "cardio" ? `${plan.duration_min || ""} min · Z${plan.zone || ""}` : `${plan.sets || ""}×${plan.reps || (plan.duration_s ? plan.duration_s + "s" : "")} ${plan.load_kg ? "· " + plan.load_kg + " kg" : ""} · descanso ${plan.rest_s || 90}s`}
    ${lastTxt ? `<br>Última: ${esc(lastTxt)}` : ""}${it.suggestion && it.suggestion.note ? `<br><b>${esc(it.suggestion.note)}</b>` : ""}${plan.notes ? `<br>${esc(plan.notes)}` : ""}</div></div></div>`;
  if (it.kind === "cardio") {
    return `<div class="card">${head}<div class="grid2" style="margin-top:8px">
      <div><label>Minutos</label><input type="number" data-c="duration_min" data-i="${i}" value="${esc(it.duration_min ?? "")}"></div>
      <div><label>Zona</label><select data-c="zone" data-i="${i}">${zoneOpts(it.zone)}</select></div>
      <div><label>FC média</label><input type="number" data-c="avg_hr" data-i="${i}" value="${esc(it.avg_hr ?? "")}"></div>
      <div><label>Feito</label><button class="check ${it.done ? "on" : ""}" data-cdone="${i}">✓</button></div></div></div>`;
  }
  const timed = it.timed && it.sets.some((x) => x.duration_s);
  return `<div class="card">${head}
    <table class="set-table" style="margin-top:8px"><thead><tr><th>#</th><th>${timed ? "Tempo s" : "Reps"}</th><th>kg</th><th>RPE</th><th></th></tr></thead><tbody>
    ${it.sets.map((x, j) => `<tr class="${x.done ? "done" : ""}"><td>${j + 1}</td>
      <td><input class="num" inputmode="numeric" data-s="${timed ? "duration_s" : "reps"}" data-i="${i}" data-j="${j}" value="${esc(timed ? x.duration_s ?? "" : x.reps ?? "")}"></td>
      <td><input class="num" inputmode="decimal" data-s="load_kg" data-i="${i}" data-j="${j}" value="${esc(x.load_kg ?? "")}"></td>
      <td><select class="num" data-s="rpe" data-i="${i}" data-j="${j}"><option value=""></option>${[6, 7, 8, 9, 10].map((r) => `<option ${+x.rpe === r ? "selected" : ""}>${r}</option>`).join("")}</select></td>
      <td><button class="check ${x.done ? "on" : ""}" data-done="${i}:${j}" aria-label="Série feita">✓</button></td></tr>`).join("")}
    </tbody></table>
    <div class="row gap" style="margin-top:6px"><button class="btn sm ghost" data-addset="${i}">+ série</button><button class="btn sm ghost" data-rmset="${i}">− série</button></div></div>`;
}

function bindSession() {
  const s = state.session;
  const save = () => store("gg_session", s);
  view.oninput = (e) => {
    const el = e.target;
    if (el.id === "s-date") s.date = el.value;
    if (el.id === "s-notes") s.notes = el.value;
    if (el.dataset.s) { const v = el.value === "" ? null : parseFloat(el.value.replace(",", ".")); s.items[+el.dataset.i].sets[+el.dataset.j][el.dataset.s] = v; }
    if (el.dataset.c) { s.items[+el.dataset.i][el.dataset.c] = el.value === "" ? null : +el.value; }
    save();
  };
  view.onchange = view.oninput;
  view.onclick = (e) => {
    const t = e.target;
    if (t.dataset.ex) return showExercise(t.dataset.ex);
    if (t.dataset.done) {
      const [i, j] = t.dataset.done.split(":").map(Number); const set = s.items[i].sets[j]; set.done = !set.done; save();
      t.classList.toggle("on", set.done); t.closest("tr").classList.toggle("done", set.done);
      if (set.done) startTimer((s.items[i].plan || {}).rest_s || 90);
    }
    if (t.dataset.cdone) { const it = s.items[+t.dataset.cdone]; it.done = !it.done; t.classList.toggle("on", it.done); save(); }
    if (t.dataset.pdone) { const it = s.items[+t.dataset.pdone]; it.done = !it.done; t.classList.toggle("on", it.done); save(); }
    if (t.dataset.addset) { const it = s.items[+t.dataset.addset]; const last = it.sets[it.sets.length - 1] || {}; it.sets.push({ ...last, done: false }); renderSession(); }
    if (t.dataset.rmset) { const it = s.items[+t.dataset.rmset]; it.sets.pop(); renderSession(); }
  };
  $("#s-add").onclick = () => pickExercise(null, (ex) => {
    s.items.push(ex.kind === "cardio" ? { exercise_id: ex.id, name: ex.name, kind: "cardio", plan: {}, duration_min: 10, zone: 2, images: ex.images, limitation: ex.limitation }
      : { exercise_id: ex.id, name: ex.name, kind: ex.kind, plan: {}, sets: [1, 2, 3].map(() => ({ reps: 10, load_kg: null, done: false })), images: ex.images, limitation: ex.limitation, timed: ex.timed });
    renderSession();
  });
  $("#s-cancel").onclick = () => { if (confirm("Descartar este treino?")) { store("gg_session", null); state.session = null; location.hash = "#hoje"; } };
  $("#s-finish").onclick = async () => {
    const main = s.items.filter((it) => (it.phase || "principal") === "principal");
    let anyDone = main.some((it) => it.kind === "cardio" ? it.done : (it.sets || []).some((x) => x.done));
    if (!anyDone) {
      if (!confirm("Nenhuma série marcada como feita. Salvar todas as séries preenchidas como feitas?")) return;
      main.forEach((it) => { if (it.kind === "cardio") it.done = true; else (it.sets || []).forEach((x) => { if (x.reps || x.duration_s) x.done = true; }); });
    }
    const payload = {
      date: s.date, plan_id: s.plan_id || null, day_name: s.day_name, notes: s.notes, schedule_id: s.schedule_id || null,
      items: s.items.filter((it) => it.kind === "cardio" || (it.phase || "principal") === "principal").map((it) => it.kind === "cardio"
        ? (it.done ? { exercise_id: it.exercise_id, duration_min: it.duration_min, zone: it.zone, avg_hr: it.avg_hr } : null)
        : { exercise_id: it.exercise_id, sets: it.sets.filter((x) => x.done).map(({ reps, load_kg, rpe, duration_s }) => ({ reps, load_kg, rpe, duration_s })) })
        .filter((it) => it && (it.duration_min || (it.sets && it.sets.length))),
    };
    await api("/api/sessions", { method: "POST", body: payload });
    store("gg_session", null); state.session = null; stopTimer();
    toast("Treino salvo! 🎉"); location.hash = s.schedule_id ? "#hoje" : "#analise";
  };
}

/* ---- timer de descanso ---- */
let timerEnd = 0, timerInt;
function startTimer(sec) {
  timerEnd = Date.now() + sec * 1000; $("#timer").hidden = false; tick();
  clearInterval(timerInt); timerInt = setInterval(tick, 250);
}
function stopTimer() { clearInterval(timerInt); $("#timer").hidden = true; }
function tick() {
  const left = Math.max(0, Math.round((timerEnd - Date.now()) / 1000));
  $("#timer-value").textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  if (left <= 0) { stopTimer(); try { navigator.vibrate && navigator.vibrate([200, 100, 200]); } catch (_) { /* sem vibração */ } toast("Descanso terminado — próxima série!"); }
}
$("#timer").addEventListener("click", (e) => {
  const a = e.target.dataset.timer; if (!a) return;
  if (a === "stop") return stopTimer();
  timerEnd += (+a) * 1000; tick();
});

/* ------------------------------------------------------------- ANÁLISE -- */
async function viewAnalise() {
  const [rep, sessions] = await Promise.all([api("/api/report/weekly"), api("/api/sessions?limit=30")]);
  const c = rep.cardio;
  view.innerHTML = `
  <div class="card"><h2>Volume por músculo — últimos 7 dias</h2>${muscleBars(rep.muscles, "sets_7d")}
    <details><summary class="small">Detalhes por músculo</summary><ul class="list small">${rep.muscles.map((m) => `<li><b>${esc(m.pt)}</b>: ${m.sets_7d} séries (${m.direct_7d} diretas, ${m.indirect_7d} indiretas) · média 4 sem. ${m.sets_avg_4w} · ${m.frequency_7d}× na semana · ${m.days_since === null ? "nunca treinado" : `há ${m.days_since} dias`} · tonelagem ${m.tonnage_7d} kg</li>`).join("")}</ul></details></div>
  <div class="card"><h2>O que ajustar</h2>${recsHtml(rep.recommendations)}</div>
  <div class="card"><h2>Equilíbrio muscular</h2>${balanceHtml(rep.balance)}</div>
  <div class="card"><h2>Cardio</h2>
    <div class="grid2"><div><div class="kpi">${c.minutes_total} min</div><div class="small muted">total na semana</div></div>
      <div><div class="kpi">${c.kcal_estimate} kcal</div><div class="small muted">estimativa (MET)</div></div></div>
    <p class="small">Leve ${c.light} · Moderado ${c.moderate} · Vigoroso ${c.vigorous} min → <b>${c.who_equivalent}</b> min equivalentes.<br>${esc(c.message)}</p>
    <div class="meter"><span style="width:${Math.min(100, c.who_equivalent / 3)}%"></span></div>
    <p class="small muted">${Object.entries(c.by_machine).map(([k, v]) => `${esc(k)}: ${v} min`).join(" · ")}</p></div>
  <div class="card"><h2>Histórico</h2>${sessions.length ? `<ul class="list">${sessions.map((s) => `<li class="row between gap"><span><b>${esc(s.day_name || "Treino")}</b> <span class="small muted">${fmtDate(s.date)} ${s.source === "garmin" ? "· Garmin" : ""}</span><br>
      <span class="small muted">${s.items.length} exercícios · ${s.items.reduce((a, it) => a + (it.sets ? it.sets.length : 0), 0)} séries</span></span>
      <button class="icon-btn" data-del="${s.id}" title="Excluir">🗑</button></li>`).join("")}</ul>` : `<p class="empty">Nenhum treino registrado.</p>`}</div>`;
  view.onclick = async (e) => {
    if (e.target.dataset.ex) return showExercise(e.target.dataset.ex);
    if (e.target.dataset.del && confirm("Excluir este registro?")) { await api(`/api/sessions/${e.target.dataset.del}`, { method: "DELETE" }); viewAnalise(); }
  };
}

/* ---------------------------------------------------------- EXERCÍCIOS -- */
async function viewExercicios() {
  view.innerHTML = `<div class="card"><div class="stack">
    <input id="ex-q" placeholder="Buscar: supino, agachamento, remada, esteira…">
    <div class="row gap wrap">
      <select id="ex-m" style="width:auto"><option value="">Todos os músculos</option>${state.meta.muscles.map((m) => `<option value="${m.key}">${esc(m.pt)}</option>`).join("")}</select>
      <select id="ex-e" style="width:auto"><option value="">Todo equipamento</option>${state.meta.equipment.map((e) => `<option value="${esc(e.key)}">${esc(e.pt)}</option>`).join("")}</select>
      <select id="ex-k" style="width:auto"><option value="">Tudo</option><option value="strength">Musculação</option><option value="cardio">Cardio</option><option value="stretching">Alongamento</option></select>
      <label class="row gap small" style="margin:0"><input type="checkbox" id="ex-hide"> ocultar “evitar”</label>
    </div></div></div>
    <div class="card"><ul class="list" id="ex-list"></ul></div>`;
  let t;
  const load = async () => {
    const qs = new URLSearchParams({ q: $("#ex-q").value, limit: 80, hide_avoid: $("#ex-hide").checked });
    [["muscle", "#ex-m"], ["equipment", "#ex-e"], ["kind", "#ex-k"]].forEach(([k, s]) => { if ($(s).value) qs.set(k, $(s).value); });
    const res = await api(`/api/exercises?${qs}`);
    $("#ex-list").innerHTML = res.map((ex) => `<li class="ex-item" data-ex="${esc(ex.id)}">
      ${ex.images[0] ? `<img class="thumb" loading="lazy" src="${esc(ex.images[0])}" alt="">` : `<span class="thumb"></span>`}
      <span class="grow" data-ex="${esc(ex.id)}"><b data-ex="${esc(ex.id)}">${esc(ex.name)}</b> ${limBadge(ex.limitation)}<br><span class="small muted" data-ex="${esc(ex.id)}">${esc(ex.primary_pt.join(", "))} · ${esc(ex.equipment_pt || "")} · ${esc(ex.category_pt)}</span></span></li>`).join("") || `<li class="empty">Nada encontrado.</li>`;
  };
  view.oninput = () => { clearTimeout(t); t = setTimeout(load, 200); };
  view.onchange = view.oninput;
  view.onclick = (e) => { const li = e.target.closest("[data-ex]"); if (li) showExercise(li.dataset.ex); };
  load();
}

/* -------------------------------------------------------------- PERFIL -- */
async function viewPerfil() {
  const p = await api("/api/profile");
  state.profile = p;
  const ul = Object.fromEntries((p.limitations || []).map((l) => [l.key, l]));
  const custom = (p.limitations || []).find((l) => l.key === "personalizado") || {};
  const g = await api("/api/garmin/status");
  const num = (k, label, extra = "") => `<div class="field"><label>${label}</label><input type="number" data-p="${k}" value="${esc(p[k] ?? "")}" ${extra}></div>`;
  view.innerHTML = `
  <div class="card"><h2>Seus dados</h2>
    <div class="grid2">
      <div class="field"><label>Nome</label><input data-p="name" value="${esc(p.name || "")}"></div>
      ${num("age", "Idade")}${num("weight_kg", "Peso (kg)", 'step="0.1"')}${num("height_cm", "Altura (cm)")}
      ${num("hr_max", `FC máx. medida <span class="muted">(calculada: ${p.hr_max_calc ?? "—"})</span>`)}${num("hr_rest", "FC repouso")}
      <div class="field"><label>Sexo</label><select data-p="sex"><option value=""></option><option value="M" ${p.sex === "M" ? "selected" : ""}>Masculino</option><option value="F" ${p.sex === "F" ? "selected" : ""}>Feminino</option></select></div>
      <div class="field"><label>Nível</label><select data-p="level">${[["iniciante", "Iniciante (< 1 ano)"], ["intermediario", "Intermediário"], ["avancado", "Avançado (> 3 anos)"]].map(([v, t]) => `<option value="${v}" ${p.level === v ? "selected" : ""}>${t}</option>`).join("")}</select></div>
      <div class="field"><label>Objetivo</label><select data-p="goal">${[["hipertrofia", "Hipertrofia"], ["forca", "Força"], ["emagrecimento", "Emagrecimento"], ["condicionamento", "Condicionamento"], ["saude", "Saúde geral"], ["hibrido", "Híbrido (força + resistência)"]].map(([v, t]) => `<option value="${v}" ${p.goal === v ? "selected" : ""}>${t}</option>`).join("")}</select></div>
      ${num("days_per_week", "Dias de treino/semana", 'min="1" max="14"')}
      ${num("volume_factor", "Ajuste de volume (×)", 'step="0.05" min="0.5" max="1.5"')}
    </div>
    <p class="small muted">“Ajuste de volume” multiplica as faixas de séries por músculo — reduza se não estiver recuperando, aumente se estiver estagnado e recuperando bem.</p>
  </div>
  <div class="card"><h2>Rotina (para o treino automático)</h2>
    <div class="grid2">
      <div class="field"><label>Tempo de prática</label><select data-p="training_months"><option value="">—</option>${MONTH_OPTS.map(([v, t]) => `<option value="${v}" ${+p.training_months === v ? "selected" : ""}>${t}</option>`).join("")}</select></div>
      ${num("session_minutes", "Minutos por sessão", 'min="20" max="180" step="5"')}
      ${num("cardio_target_min", "Meta de cardio (min/semana)", 'min="0" step="10" placeholder="pelo objetivo"')}
    </div>
    <label>Dias disponíveis</label>
    <div class="chips">${["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"].map((w, i) => `<label class="chip"><input type="checkbox" data-wd="${i}" ${(p.available_days || []).includes(i) ? "checked" : ""}> ${w}</label>`).join("")}</div>
    <label style="margin-top:8px">Equipamentos da sua academia</label>
    <div class="chips">${[["barbell", "Barra"], ["dumbbell", "Halteres"], ["machine", "Máquinas"], ["cable", "Polia/cabo"], ["kettlebells", "Kettlebell"], ["bands", "Elástico"], ["e-z curl bar", "Barra W"]].map(([k, t]) => `<label class="chip"><input type="checkbox" data-eq="${k}" ${!(p.equipment || []).length || p.equipment.includes(k) ? "checked" : ""}> ${t}</label>`).join("")}</div>
    <label style="margin-top:8px">Aparelhos de cardio preferidos</label>
    <div class="chips" id="cm-list"></div>
    <p class="small muted">Nível é calculado pelo tempo de prática. Depois de salvar, use “Gerar treino” na tela Hoje.</p>
  </div>
  <div class="card"><h2>Limitações e lesões</h2>
    <p class="small muted">O app marca exercícios como <span class="pill warn">cuidado</span> ou <span class="pill bad">evitar</span>, troca exercícios dos modelos e limita a zona de FC do cardio. Não substitui orientação de médico/fisioterapeuta.</p>
    <ul class="list">${state.meta.limitations.map((l) => `<li><label class="row gap" style="color:var(--ink);font-size:15px;margin:0"><input type="checkbox" data-lim="${l.id}" ${ul[l.id] ? "checked" : ""}> <span class="grow">${esc(l.label)}</span>
      <select data-sev="${l.id}" style="width:auto"><option value="evitar" ${(ul[l.id] || {}).severity !== "cuidado" ? "selected" : ""}>significativa</option><option value="cuidado" ${(ul[l.id] || {}).severity === "cuidado" ? "selected" : ""}>leve</option></select></label>
      <div class="small muted">${esc(l.note)}</div></li>`).join("")}</ul>
    <div class="field"><label>Outros exercícios a evitar (separe por vírgula, PT ou EN)</label><input id="custom-avoid" value="${esc(custom.custom_avoid || "")}" placeholder="ex.: leg press, burpee, levantamento terra"></div>
    <button class="btn" id="save-profile">Salvar perfil</button>
  </div>
  <div class="card"><h2>Backup dos seus dados</h2>
    <p class="small muted">Perfil, planilhas, treinos registrados e agenda em um arquivo .json.</p>
    <div class="row gap wrap"><a class="btn sm ghost" href="/api/backup">Baixar backup</a>
      <label class="btn sm ghost" style="margin:0">Restaurar backup<input type="file" id="restore" accept=".json,application/json" hidden></label></div>
  </div>
  <div class="card"><h2>Garmin Connect</h2>
    ${!g.library_installed ? `<p class="pill bad">Biblioteca garminconnect não instalada no servidor: <code>pip install "garminconnect>=0.3.20"</code></p>` : ""}
    ${g.connected ? `<p><span class="pill ok">conectado</span> ${esc(g.display_name || "")}</p>
      <div class="row gap wrap"><button class="btn sm ghost" id="g-acts">Importar treinos do relógio</button><button class="btn sm danger" id="g-logout">Desconectar</button></div><div id="g-acts-box"></div>`
      : `<p class="small muted">Login feito uma vez; depois só os tokens ficam salvos neste computador (<code>~/.gymgarmin/garmin_tokens</code>). Sua senha não é armazenada.</p>
      <div class="grid2"><div class="field"><label>E-mail Garmin</label><input id="g-email" type="email" autocomplete="username"></div>
      <div class="field"><label>Senha</label><input id="g-pass" type="password" autocomplete="current-password"></div></div>
      <div id="g-mfa" hidden class="field"><label>Código MFA (e-mail/app)</label><input id="g-code" inputmode="numeric"></div>
      <button class="btn" id="g-login">Conectar</button>${g.error ? `<p class="small pill bad">${esc(g.error)}</p>` : ""}`}
    <div class="hr"></div>
    <details><summary class="small">Como funciona o envio para o Garmin?</summary><div class="small">
      <p><b>1. Arquivo .FIT (offline):</b> em cada treino da planilha, “Baixar .FIT”. Conecte o relógio no USB e copie para <code>GARMIN/NewFiles</code>. O relógio importa ao desconectar e mostra em Treinos. Gerado com o SDK oficial da Garmin.</p>
      <p><b>2. Garmin Connect (direto):</b> “Enviar ao Garmin Connect” cria o treino na sua biblioteca (mesmo formato do editor web) e agenda na data escolhida; o relógio recebe na próxima sincronização. Usa a API não-oficial (biblioteca <code>garminconnect</code>), pois a API oficial de treinos é restrita a empresas parceiras.</p>
      <p><b>3. Volta do relógio:</b> “Importar treinos do relógio” traz séries/reps/carga registradas no relógio para a análise muscular.</p></div></details>
  </div>`;
  api("/api/generator/options").then((o) => {
    const el = $("#cm-list"); if (!el) return;
    el.innerHTML = o.cardio_machines.map((m) => `<label class="chip"><input type="checkbox" data-cm="${m.id}" ${(p.cardio_machines || []).includes(m.id) ? "checked" : ""}> ${esc(m.name)}</label>`).join("");
  });
  $("#save-profile").onclick = async () => {
    const body = {};
    $$("[data-p]").forEach((el) => { const v = el.value; body[el.dataset.p] = (el.type === "number" || el.dataset.p === "training_months") ? (v === "" ? null : +v) : v; });
    body.available_days = $$("[data-wd]").filter((c) => c.checked).map((c) => +c.dataset.wd);
    const eq = $$("[data-eq]"); body.equipment = eq.every((c) => c.checked) ? [] : eq.filter((c) => c.checked).map((c) => c.dataset.eq);
    body.cardio_machines = $$("[data-cm]").filter((c) => c.checked).map((c) => c.dataset.cm);
    body.limitations = $$("[data-lim]").filter((c) => c.checked).map((c) => ({ key: c.dataset.lim, severity: $(`[data-sev="${c.dataset.lim}"]`).value }));
    const ca = $("#custom-avoid").value.trim();
    if (ca) body.limitations.push({ key: "personalizado", label: "Restrição pessoal", severity: "evitar", custom_avoid: ca });
    state.profile = await api("/api/profile", { method: "PUT", body });
    state.meta = await api("/api/meta");
    toast("Perfil salvo");
  };
  $("#restore").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try {
      const r = await api("/api/backup", { method: "POST", body: JSON.parse(await f.text()) });
      state.meta = null; toast(`Restaurado: ${r.sessions || 0} treinos, ${r.plans || 0} planilhas`); route();
    } catch (err) { toast(err.message, 5000); }
  };
  const gl = $("#g-login");
  if (gl) gl.onclick = async () => {
    gl.disabled = true;
    try {
      if (!$("#g-mfa").hidden) await api("/api/garmin/mfa", { method: "POST", body: { code: $("#g-code").value } });
      else {
        const r = await api("/api/garmin/login", { method: "POST", body: { email: $("#g-email").value, password: $("#g-pass").value } });
        if (r.mfa_required) { $("#g-mfa").hidden = false; gl.textContent = "Confirmar código"; toast("Digite o código MFA enviado pela Garmin"); return; }
      }
      toast("Conectado ao Garmin Connect"); viewPerfil(); updateGarminPill();
    } catch (e) { toast(e.message, 6000); } finally { gl.disabled = false; }
  };
  const lo = $("#g-logout"); if (lo) lo.onclick = async () => { await api("/api/garmin/logout", { method: "POST" }); viewPerfil(); updateGarminPill(); };
  const ga = $("#g-acts");
  if (ga) ga.onclick = async () => {
    const box = $("#g-acts-box"); box.innerHTML = `<p class="small muted">Carregando…</p>`;
    try {
      const acts = await api("/api/garmin/activities?days=21");
      box.innerHTML = `<ul class="list small">${acts.map((a) => `<li class="row between gap"><span><b>${esc(a.name)}</b> <span class="muted">${esc(a.type)} · ${fmtDate(a.start)} · ${a.duration_min} min</span></span><button class="btn sm ghost" data-imp="${a.activity_id}">Importar</button></li>`).join("") || "<li>Nenhuma atividade.</li>"}</ul>`;
      box.onclick = async (e) => {
        const id = e.target.dataset.imp; if (!id) return;
        try { await api(`/api/garmin/import/${id}`, { method: "POST" }); e.target.textContent = "Importado ✔"; e.target.disabled = true; } catch (err) { toast(err.message); }
      };
    } catch (e) { box.innerHTML = `<p class="pill bad">${esc(e.message)}</p>`; }
  };
}

async function updateGarminPill() {
  try { const g = await api("/api/garmin/status"); const el = $("#garmin-pill"); el.textContent = g.connected ? "Garmin: conectado" : "Garmin: offline"; el.className = `pill ${g.connected ? "ok" : "muted"}`; } catch (_) { /* ignora */ }
}

/* --------------------------------------------------------------- router -- */
async function route() {
  const [name, a, b] = (location.hash.slice(1) || "hoje").split("/");
  $$(".tabbar a").forEach((el) => el.classList.toggle("active", el.dataset.tab === (name === "plano" ? "planilhas" : name)));
  view.oninput = view.onchange = view.onclick = null;
  view.innerHTML = `<p class="muted">Carregando…</p>`;
  try {
    if (!state.meta) {
      [state.meta, state.profile] = await Promise.all([api("/api/meta"), api("/api/profile")]);
      if (state.meta.ephemeral_storage && !$("#eph")) {
        document.querySelector(".topbar").insertAdjacentHTML("afterend", `<div id="eph" class="rec atencao small" style="margin:8px 16px 0">Versão de teste online: os dados podem ser apagados pelo servidor. Use <a href="#perfil">Perfil → Backup</a> para salvar e restaurar.</div>`);
      }
    }
    const routes = { hoje: () => viewHoje(a), planilhas: viewPlanilhas, plano: () => viewPlano(a), treinar: () => viewTreinar(a, b), analise: viewAnalise, exercicios: viewExercicios, perfil: viewPerfil };
    await (routes[name] || viewHoje)();
    if (!view.onclick) view.onclick = (e) => { const id = e.target.dataset && e.target.dataset.ex; if (id) showExercise(id); };
  } catch (e) { view.innerHTML = `<div class="card"><p class="pill bad">Erro: ${esc(e.message)}</p></div>`; }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
route();
updateGarminPill();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
