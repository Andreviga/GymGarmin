/* Hybrid Lab – app web (vanilla JS, sem build). */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const view = $("#view");
const state = { meta: null, profile: null, draftPlan: null, planTab: 0, session: null };

function actAs() { try { return JSON.parse(sessionStorage.getItem("gg_act") || "null"); } catch (_) { return null; } }
async function api(path, opts = {}) {
  const act = actAs();
  const o = { ...opts, headers: { "Content-Type": "application/json", ...(act ? { "X-GG-Athlete": String(act.id) } : {}), ...(opts.headers || {}) } };
  if (o.body && typeof o.body !== "string") o.body = JSON.stringify(o.body);
  let r;
  try { r = await fetch(path, o); } catch (_) {
    const err = new Error("Sem conexão com a internet. Tente de novo quando o sinal voltar.");
    err.offline = true; throw err;
  }
  if (!r.ok) {
    let msg = r.statusText; let j = null;
    try { j = await r.json(); msg = j.detail || msg; } catch (_) { /* sem corpo */ }
    if (r.status === 401 && j && j.login) { showAuth("login"); const e = new Error(msg); e.auth = true; throw e; }
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
const PHASES = [["preparo", "Aquecimento: elevar → ativar → potencializar"], ["principal", "Treino"], ["pos", "Pós-treino (alongamento)"]];
const RAMP_TXT = { elevar: "elevar", ativar: "ativar", potencializar: "potencializar" };
const RIR_TIP = "RIR = repetições que ainda sobrariam no fim da série (0 = falha). Perto da falha estimula mais o músculo.";
const ddmm = (iso) => { const [y, m, d] = iso.slice(0, 10).split("-"); return `${d}/${m}`; };
// data/hora local (toISOString é UTC: depois das 21h no Brasil já seria amanhã)
const localISO = (d = new Date()) => { const p = (x) => String(x).padStart(2, "0"); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`; };
const addDays = (iso, n) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n); return localISO(d).slice(0, 10); };

function timeBar(t) {
  if (!t) return "";
  const segs = [["preparo", "Preparo", "var(--c-prep)"], ["forca_trabalho", "Força", "var(--c-work)"], ["forca_descanso", "Descanso", "var(--c-rest)"], ["cardio", "Cardio", "var(--c-cardio)"], ["pos", "Alongamento", "var(--c-post)"]];
  const tot = Math.max(t.total, t.orcamento || 0, 1);
  const on = segs.filter((x) => t[x[0]] > 0);
  return `<div class="tbar">${on.map((x) => `<span style="width:${(t[x[0]] / tot) * 100}%;background:${x[2]}" title="${x[1]}: ${t[x[0]]} min"></span>`).join("")}</div>
    <div class="small muted">${on.map((x) => `<span class="dot" style="background:${x[2]}"></span>${x[1]} ${Math.round(t[x[0]])}′`).join(" ")} · <b>${Math.round(t.total)} min</b>${t.orcamento ? ` de ${t.orcamento}` : ""}</div>`;
}

/* ---- mídia dos exercícios: 2 quadros alternando (como um GIF) ou pictograma desenhado */
const PICTO_SVG = {
  run: '<circle cx="15" cy="4.5" r="2"/><path d="M13 8l-3 3 3 2.5-1.5 5M10 11l-3.5.5M13 8l3.5 2.5 3-1M13.5 13.5l3 3.5 2.5-.5"/>',
  bike: '<circle cx="6" cy="16" r="3.5"/><circle cx="18" cy="16" r="3.5"/><circle cx="14" cy="4.5" r="1.8"/><path d="M6 16l4-7h5l3 7M10 9l4 7M12.5 6.5l1.5 2.5h3"/>',
  swim: '<circle cx="16" cy="6" r="2"/><path d="M3 15c2 1.5 4 1.5 6 0s4-1.5 6 0 4 1.5 6 0M3 19c2 1.5 4 1.5 6 0s4-1.5 6 0 4 1.5 6 0M6 12l5-4 3 2.5 3-1"/>',
  row: '<circle cx="8" cy="6" r="2"/><path d="M3 18h18M8 9l2 5h5M10 14l-3 4M15 14l4-6M19 8l2-1"/>',
  walk: '<circle cx="13" cy="4.5" r="2"/><path d="M13 8l-2 5 2.5 3.5-1 4.5M11 13l-2.5 6.5M12.5 8.5l3 2.5 2 0M12 9l-3 2v3"/>',
  hike: '<circle cx="12" cy="4.5" r="2"/><path d="M12 8l-2 5 2.5 3.5-1 4.5M10 13l-2.5 6.5M12 8.5l3 2v3M17 10v11M3 21l5-6 3 3 4-5 6 8"/>',
  ski: '<circle cx="12" cy="4.5" r="2"/><path d="M8 8v12M16 8v12M12 7.5v6l-3 3M12 13.5l3 3M6 8h4M14 8h4"/>',
  elliptical: '<circle cx="12" cy="4.5" r="2"/><path d="M12 7.5v6M12 9l4-2M12 9l-3 3M12 13.5l-3 5M12 13.5l3 5M5 20h14M17 4l1 6"/>',
  stairs: '<path d="M3 20h4v-4h4v-4h4V8h4V4h2"/><circle cx="9" cy="8" r="1.8"/>',
  rope: '<circle cx="12" cy="4.5" r="2"/><path d="M12 7.5v7M12 10l-4 2M12 10l4 2M12 14.5l-2 6M12 14.5l2 6M8 12c-5 3-5 10 4 10s9-7 4-10"/>',
  kettlebell: '<path d="M8.5 9a3.5 3.5 0 117 0"/><path d="M6.5 11h11l1 6a4 4 0 01-4 4h-5a4 4 0 01-4-4z"/>',
  dumbbell: '<path d="M3 10v4M6 8v8M18 8v8M21 10v4M6 12h12"/>',
};
let MEDIA = null;
async function loadMedia() {
  if (MEDIA) return MEDIA;
  try { MEDIA = store("gg_media_v1"); } catch (_) { MEDIA = null; }
  if (!MEDIA || !MEDIA.f) {
    try { MEDIA = await api("/api/media"); store("gg_media_v1", MEDIA); } catch (_) { MEDIA = { base: "", f: {}, p: {} }; }
  }
  return MEDIA;
}
function frames(id) {
  const M = MEDIA; if (!M || !(id in M.f)) return null;
  const folder = M.f[id] || id; return [`${M.base}${folder}/0.jpg`, `${M.base}${folder}/1.jpg`];
}
function pictoHtml(key, cls = "") {
  return `<span class="picto ${cls}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PICTO_SVG[key] || PICTO_SVG.dumbbell}</svg></span>`;
}
// size: "s" (lista), "m" (cartão), "l" (detalhe). Animada alternando início/fim do movimento.
function mediaHtml(id, size = "s", fallback = null) {
  const f = frames(id);
  if (f) return `<span class="demo ${size}"><img loading="lazy" src="${esc(f[0])}" alt=""><img loading="lazy" src="${esc(f[1])}" alt="" class="f2"></span>`;
  const p = (MEDIA && MEDIA.p[id]) || fallback || "dumbbell";
  return pictoHtml(p, size);
}

function itemLine(it) {
  if (it.endurance) {
    const en = it.endurance;
    return `<li class="mline">${mediaHtml(it.exercise_id, "s", { corrida: "run", ciclismo: "bike", natacao: "swim", remo: "row", caminhada: "walk", trilha: "hike" }[en.sport])}<span>${SPORT_ICON[en.sport] || ""} <b>${esc(en.title)}</b> <span class="muted">${esc(endSummary(en))}</span>
      ${en.hard ? `<span class="pill warn small">forte</span>` : ""}${en.role === "longo" ? `<span class="pill small">longo</span>` : ""}
      ${en.note ? `<div class="small muted">${esc(en.note)}</div>` : ""}${endStepsHtml(en)}</span></li>`;
  }
  const name = esc(it._name || it._ex?.name || it.exercise_id);
  let d = "";
  if (it.intervals) d = `${it.warmup_min || 0}′ + ${it.intervals.repeats}× (${it.intervals.work_s}s Z${it.intervals.work_zone} / ${it.intervals.rest_s}s) + ${it.cooldown_min || 0}′`;
  else if (it.duration_min) d = `${it.duration_min} min${it.zone ? " Z" + it.zone : ""}`;
  else if (it.sets) d = `${it.sets}×${it.reps || (it.duration_s ? it.duration_s + "s" : "")}${it.rest_s && it.phase !== "preparo" && it.phase !== "pos" ? ` · ${it.rest_s}s` : ""}${it.rir && !it.deload ? ` · RIR ${String(it.rir).split(" ")[0]}` : ""}`;
  const tags = [it.ramp && RAMP_TXT[it.ramp] ? `<span class="pill muted small">${RAMP_TXT[it.ramp]}</span>` : it.ramp ? `<span class="pill">+${it.ramp} série</span>` : "", it.deload ? `<span class="pill warn">deload</span>` : "", it.added ? `<span class="pill warn">+${it.added}</span>` : "", it.added_from ? `<span class="pill warn">compensação</span>` : "", it.finisher ? `<span class="pill muted">finalizador</span>` : "", it.superset ? `<span class="pill small" title="Bi-set: faça uma série de cada, emendadas, e descanse depois do segundo">bi-set ${it.superset}</span>` : "", it.rotated_from ? `<span class="pill muted small" title="Variação deste ciclo: estímulo novo para o músculo; no próximo ciclo volta o exercício de antes">variação do ciclo</span>` : ""].join(" ");
  return `<li class="mline">${mediaHtml(it.exercise_id, "s")}<span><span data-ex="${esc(it.exercise_id)}" style="cursor:pointer">${name}</span> <span class="muted">${esc(d)}</span> ${tags}</span></li>`;
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
  const [sch, rd] = await Promise.all([api(`/api/schedule${weekDate ? `?date=${weekDate}` : ""}`),
    weekDate ? Promise.resolve(null) : api("/api/readiness").catch(() => null)]);
  if (!sch.plan) {
    view.innerHTML = `<section class="hero rest"><div class="hero-txt"><div class="kick">Bem-vindo ao Hybrid Lab</div>
      <div class="ttl">Monte seu treino</div>
      <div class="meta">Diga há quanto tempo treina, os dias e o tempo que tem. O app monta musculação e corrida juntas, respeita suas limitações e reorganiza a semana quando você faltar.</div>
      <div class="row gap wrap" style="margin-top:12px"><button class="btn light" id="gen-open">Gerar meu treino</button><a class="btn ghost-light" href="#planilhas">Montar manualmente</a></div></div></section>`;
    $("#gen-open").onclick = () => openGenerator();
    return;
  }
  const g = sch.goals;
  const missed = sch.entries.filter((e) => e.status === "perdido");
  const outdated = sch.entries.some((e) => e.garmin_outdated && e.status === "planejado");
  const cyc = sch.cycle;
  const athleteHtml = weekDate ? "" : await athleteCards();
  const todays = weekDate ? [] : sch.entries.filter((e) => e.date === sch.today);
  const heroE = todays.find((e) => e.status === "planejado") || todays[0] || null;
  const adjusted = todays.some((e) => e.status === "planejado" && e.readiness_backup);
  view.innerHTML = `${weekDate ? "" : heroHtml(heroE, sch) + statsHtml(rd, g, adjusted, !!(heroE && heroE.status === "planejado"))}${athleteHtml}
  ${missed.length ? `<div class="rec alerta"><b>Treino perdido:</b> ${missed.map((e) => `${e.weekday} ${ddmm(e.date)} – ${esc(e.day_name)}`).join(", ")}.
     <div class="row gap wrap" style="margin-top:6px">${missed.map((e) => `<button class="btn sm" data-skip="${e.id}">Ajustar a semana (${e.weekday})</button>`).join("")}</div></div>` : ""}
  ${(sch.time_warnings || []).length ? `<details class="rec atencao small"><summary><b>⏱ ${sch.time_warnings.length} ${sch.time_warnings.length > 1 ? "dias passam" : "dia passa"} do seu tempo de treino</b></summary><ul class="plain">${sch.time_warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></details>` : ""}
  ${heroE && heroE.status !== "pulado" ? todayListHtml(heroE) : ""}
  <section class="sec">
    <div class="sec-head"><button class="icon-btn" data-week="${addDays(sch.week_start, -7)}" title="Semana anterior" aria-label="Semana anterior">‹</button>
      <div class="grow" style="text-align:center"><h2>Semana de ${ddmm(sch.week_start)}</h2>
        <div class="small muted">${esc(sch.plan.name)}${cyc ? ` · ciclo ${cyc.cycle}, semana ${cyc.week}/${cyc.length}${cyc.deload ? " — <b>DELOAD</b>" : ""}` : ""}</div></div>
      <button class="icon-btn" data-week="${addDays(sch.week_start, 7)}" title="Próxima semana" aria-label="Próxima semana">›</button></div>
    ${cyc && cyc.deload ? `<p class="small rec">Semana de deload: menos séries, carga −10% e RPE ~6 para o corpo assimilar o ciclo.</p>` : ""}
    <div class="days">${sch.entries.length ? sch.entries.map((e) => dayCard(e, sch.today)).join("") : `<p class="empty">Nenhum treino nesta semana.</p>`}</div>
    <div class="row gap wrap" style="margin-top:12px">
      <button class="btn sm ${outdated ? "" : "ghost"}" id="push-week">${outdated ? "Reenviar alterações ao Garmin" : "Enviar semana ao Garmin"}</button>
      <button class="btn sm ghost" id="gen-open">Regerar treino</button>
      <button class="btn sm ghost" id="rebuild">Refazer pela planilha</button>
    </div>
  </section>
  <section class="card"><h2>Metas da semana</h2><div class="stack">
    ${meter("Treinos", g.sessions.done, g.sessions.planned)}
    ${meter("Cardio (min equivalentes OMS)", g.cardio.done, g.cardio.target)}
    ${meter("Séries de força", g.sets.done, g.sets.planned)}
    ${meter("Tempo treinado", g.minutes.done, g.minutes.planned, " min")}
    ${Object.entries(g.endurance || {}).map(([k, x]) => meter(`${SPORT_ICON[k] || ""} ${esc(x.name)}`, x.done, x.planned, " " + x.unit)).join("")}
  </div></section>
  <section class="card" id="hoje-recs"><h2>O que ajustar</h2><p class="muted small">Carregando…</p></section>`;
  sch._rd = rd;
  bindAgenda(sch);
  api("/api/report/weekly").then((rep) => { const b = $("#hoje-recs"); if (b) b.innerHTML = `<h2>O que ajustar</h2>${recsHtml(rep.recommendations.slice(0, 6))}`; }).catch(() => {});
}

/* ------------------------------------------------- HOJE: foto, números e lista -- */
const SPORT_PICTO = { corrida: "run", ciclismo: "bike", natacao: "swim", remo: "row", caminhada: "walk", trilha: "hike" };
const SPORT_WORD = { corrida: "corrida", ciclismo: "bike", natacao: "natação", remo: "remo", caminhada: "caminhada", trilha: "trilha" };
const WD_LONG = ["domingo", "segunda", "terça", "quarta", "quinta", "sexta", "sábado"];
function mainIds(e) {
  return (e.items || []).filter((i) => (i.phase || "principal") === "principal").map((i) => i.exercise_id);
}
function dayKind(e) {
  const end = (e.items || []).filter((i) => i.endurance);
  const str = (e.items || []).some((i) => (i.phase || "principal") === "principal" && !i.endurance);
  if (end.length && str) return `musculação + ${SPORT_WORD[end[0].endurance.sport] || "cardio"}`;
  return end.length ? end.map((i) => i.endurance.title).join(" + ") : "musculação";
}
function heroHtml(e, sch) {
  const d = new Date(sch.today + "T12:00:00");
  const dateTxt = `${WD_LONG[d.getDay()]}, ${ddmm(sch.today)}`;
  if (!e) {
    const nx = sch.entries.find((x) => x.date > sch.today && x.status === "planejado");
    return `<section class="hero rest"><div class="hero-txt"><div class="kick">Hoje · ${dateTxt}</div><div class="ttl">Descanso</div>
      <div class="meta">${nx ? `Próximo: ${esc(nx.weekday)} · ${esc(nx.day_name)}` : "Caminhar, alongar e dormir bem também contam."}</div></div></section>`;
  }
  const id = e.sport ? null : mainIds(e).find((x) => frames(x));
  const f = id ? frames(id) : null;
  const st = STATUS_INFO[e.status] || [e.status, ""];
  const mins = e.time ? `~${Math.round(e.time.total)} min` : "";
  return `<section class="hero ${f ? "photo" : "rest"} ${e.status === "planejado" ? "" : "done"}">
    ${f ? `<img class="hero-img" src="${esc(f[0])}" alt="">` : `<span class="hero-picto">${pictoHtml(SPORT_PICTO[e.sport] || "dumbbell", "hp")}</span>`}
    <div class="hero-txt"><div class="kick">Hoje · ${dateTxt}${mins ? ` · ${mins}` : ""}</div>
      <div class="ttl">${esc(e.day_name)}</div>
      <div class="meta">${esc(dayKind(e))}${e.endurance_phase ? ` · ${esc(e.endurance_phase)}` : ""}${e.race ? " · PROVA" : ""}</div>
      <div class="row gap wrap" style="margin-top:12px">${e.status === "planejado"
        ? `<a class="btn light" href="#treinar/agenda/${e.id}">Começar treino</a>`
        : `<span class="pill ${st[1]}">${esc(st[0])}</span>`}</div></div></section>`;
}
function ringSvg(score, cls) {
  const v = Math.max(0, Math.min(100, +score || 0)), c = 2 * Math.PI * 15.5;
  return `<svg class="ring-svg ${cls}" viewBox="0 0 36 36" aria-hidden="true"><circle cx="18" cy="18" r="15.5" class="ring-bg"/>
    <circle cx="18" cy="18" r="15.5" class="ring-fg" stroke-dasharray="${(c * v / 100).toFixed(1)} ${c.toFixed(1)}"/></svg>`;
}
const RD_LEVEL = { alta: "Alta", moderada: "Moderada", baixa: "Baixa", muito_baixa: "Muito baixa" };
function statsHtml(rd, g, adjusted, hasToday) {
  const has = rd && rd.score != null;
  const cls = has ? ({ seguir: "ok", moderar: "warn", trocar: "warn", descanso: "bad" }[rd.action] || "ok") : "";
  const sleep = rd && rd.sleep_h ? `${String(Math.floor(rd.sleep_h))}h${String(Math.round((rd.sleep_h % 1) * 60)).padStart(2, "0")}` : "—";
  const s = g && g.sessions ? `${g.sessions.done}/${g.sessions.planned}` : "—";
  const act = has && rd.action !== "seguir" && hasToday;
  return `<section class="stats">
    <button class="stat ready" id="rd-open" aria-label="Prontidão: ver detalhes">
      <span class="ring-box">${ringSvg(has ? rd.score : 0, cls)}<b>${has ? rd.score : "—"}</b></span>
      <span class="stat-txt"><span class="lbl">Prontidão</span><b class="${cls}">${has ? esc(RD_LEVEL[rd.level] || rd.level) : "sem dados"}</b></span></button>
    <a class="stat" href="#saude"><b>${sleep}</b><span class="lbl">Sono</span></a>
    <a class="stat" href="#analise"><b>${s}</b><span class="lbl">Semana</span></a>
  </section>
  ${act ? `<div class="rec ${cls === "bad" ? "alerta" : "atencao"} small row between gap"><span class="grow">${esc(rd.text)}</span>
    ${adjusted ? `<span class="pill ok">ajustado</span>` : `<button class="btn sm" id="rd-apply">Ajustar hoje</button>`}</div>` : ""}`;
}
function todayListHtml(e) {
  const main = (e.items || []).filter((i) => (i.phase || "principal") === "principal");
  if (!main.length) return "";
  const extra = (e.items || []).filter((i) => (i.phase || "principal") !== "principal");
  return `<section class="sec"><h2 class="sec-title">Treino de hoje</h2>
    <ul class="exlist">${main.map((it) => {
      const en = it.endurance;
      const name = en ? en.title : (it._name || it._ex?.name || it.exercise_id);
      const sub = en ? endSummary(en) : `${it.sets ? `${it.sets} × ${it.reps || (it.duration_s ? it.duration_s + "s" : "")}` : it.duration_min ? `${it.duration_min} min` : ""}${it.rir && !it.deload ? ` · sobrar ${String(it.rir).split(" ")[0]}` : ""}`;
      return `<li data-ex="${esc(it.exercise_id)}">${mediaHtml(it.exercise_id, "s", en ? SPORT_PICTO[en.sport] : null)}
        <span class="grow" data-ex="${esc(it.exercise_id)}"><b data-ex="${esc(it.exercise_id)}">${esc(name)}</b><span class="small muted" data-ex="${esc(it.exercise_id)}">${esc(sub)}</span></span>
        ${it.superset ? `<span class="pill small">bi-set ${it.superset}</span>` : en && en.hard ? `<span class="pill warn small">forte</span>` : ""}</li>`;
    }).join("")}</ul>
    ${extra.length ? `<details class="small"><summary>Aquecimento e alongamento (${extra.length})</summary>${itemsByPhase(extra)}</details>` : ""}</section>`;
}
async function openReadiness(rd, sch) {
  const body = openModal(rd && rd.score != null ? readinessCard(rd, true, false).replace('class="card readiness', 'class="readiness-modal')
    : `<h2>Prontidão de hoje</h2><p class="muted">Ainda sem dados do relógio para hoje. Ela usa sono, variabilidade da FC e FC de repouso.</p>
       ${state.garmin && state.garmin.connected ? `<button class="btn" id="rd-refresh">Buscar no Garmin</button>` : `<a class="btn" href="#perfil">Conectar relógio</a>`}`);
  body.onclick = async (ev) => {
    const t = ev.target.closest("button, a"); if (!t) return;
    if (t.tagName === "A") return closeModal();
    try {
      if (t.id === "rd-apply") { t.disabled = true; const r = await api("/api/readiness/apply", { method: "POST", body: {} }); closeModal(); toast(r.changed.length ? `Ajustado: ${r.changed.join(", ")}` : "Nada para ajustar hoje", 5000); return viewHoje(); }
      if (t.id === "rd-refresh") { t.disabled = true; await api("/api/readiness/refresh", { method: "POST" }); closeModal(); return viewHoje(); }
    } catch (err) { t.disabled = false; toast(err.message, 5000); }
  };
}

function dayCard(e, today) {
  const [label, cls] = STATUS_INFO[e.status] || [e.status, ""];
  const active = e.status === "planejado" || e.status === "perdido";
  const d = new Date(e.date + "T12:00:00");
  return `<details class="day ${e.date === today ? "today" : ""} ${active ? "" : "dim"}">
    <summary class="day-sum"><span class="dnum"><small>${esc(e.weekday)}</small><b>${d.getDate()}</b></span>
      <span class="grow"><b>${esc(e.day_name)}</b>${e.race ? ` <span class="pill bad">PROVA</span>` : ""}
        <span class="small muted">${e.time ? `~${Math.round(e.time.total)} min · ` : ""}${esc(dayKind(e))}</span></span>
      ${e.status === "planejado" ? (e.over_budget_min ? `<span class="pill warn">+${e.over_budget_min} min</span>` : "") : `<span class="pill ${cls}">${label}</span>`}
      <span class="chev" aria-hidden="true">›</span></summary>
    <div class="day-body">
    ${active ? timeBar(e.time) : ""}
    ${e.changes && e.changes.length ? `<ul class="small changes">${e.changes.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}
    ${e.skip_result ? `<ul class="small changes">${e.skip_result.changes.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}
    ${e.over_budget_min ? `<div class="small over">Passa ${e.over_budget_min} min do seu tempo de sessão — reserve ~${Math.round(e.time.total / 5) * 5} min</div>` : ""}
    ${e.garmin_outdated ? `<span class="pill warn">Garmin desatualizado</span>` : e.garmin_workout_id ? `<span class="pill ok">no Garmin</span>` : ""}
    ${itemsByPhase(e.items)}
    <div class="row gap wrap" style="margin-top:6px">
      ${e.status === "planejado" ? `<a class="btn sm" href="#treinar/agenda/${e.id}">Treinar</a>
        <button class="btn sm ghost" data-skip="${e.id}">Não vou treinar</button>
        <button class="btn sm ghost" data-move="${e.id}" data-date="${e.date}">Mover</button>
        <button class="btn sm ghost" data-push="${e.id}">Garmin</button>
        <a class="btn sm ghost" href="/api/schedule/${e.id}/fit">.FIT</a>` : ""}
      ${e.status === "perdido" ? `<button class="btn sm" data-skip="${e.id}">Ajustar a semana</button><a class="btn sm ghost" href="#treinar/agenda/${e.id}">Registrar mesmo assim</a>` : ""}
      ${e.status === "pulado" && e.can_undo ? `<button class="btn sm ghost" data-undo="${e.id}">Desfazer ajuste</button>` : ""}
      ${e.readiness_backup && e.status === "planejado" ? `<button class="btn sm ghost" data-rundo="${e.id}">Desfazer ajuste de prontidão</button>` : ""}
    </div></div></details>`;
}

function bindAgenda(sch) {
  bindAthleteCards();
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
      if (d.rundo) { await api(`/api/readiness/undo/${d.rundo}`, { method: "POST" }); toast("Treino original de volta"); return viewHoje(sch.week_start); }
      if (t.id === "rd-open") return openReadiness(sch._rd, sch);
      if (t.id === "rd-apply") { t.disabled = true; const r = await api("/api/readiness/apply", { method: "POST", body: {} }); toast(r.changed.length ? `Ajustado: ${r.changed.join(", ")}` : "Nada para ajustar hoje", 5000); return viewHoje(); }
      if (t.id === "rd-refresh") { t.disabled = true; await api("/api/readiness/refresh", { method: "POST" }); return viewHoje(); }
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
  const [opts, eopts, fresh] = await Promise.all([api("/api/generator/options"), api("/api/endurance/options"), api("/api/profile")]);
  state.profile = fresh;
  const p = state.profile || {};
  const ecfg = JSON.parse(JSON.stringify(p.endurance || {})); ecfg.sports = ecfg.sports || {};
  const avail = p.available_days || [0, 2, 4];
  const nearest = (m) => MONTH_OPTS.reduce((a, b) => (Math.abs(b[0] - (m ?? 12)) < Math.abs(a[0] - (m ?? 12)) ? b : a))[0];
  let seed = 0;
  const body = openModal(`<h2>Gerar treino automático</h2>
    <div class="grid2">
      <div class="field"><label>Tempo de prática</label><select id="gm-months">${MONTH_OPTS.map(([v, t]) => `<option value="${v}" ${nearest(p.training_months) === v ? "selected" : ""}>${t}</option>`).join("")}</select></div>
      <div class="field"><label>Objetivo</label><select id="gm-goal">${opts.goals.map((g) => `<option value="${g.id}" ${p.goal === g.id ? "selected" : ""}>${esc(g.label)}</option>`).join("")}</select></div>
      <div class="field"><label>Minutos de treino por dia</label><input id="gm-min" type="number" min="20" max="180" step="5" value="${p.session_minutes || 60}"></div>
      <div class="field"><label>Meta de cardio (min/semana, vazio = pelo objetivo)</label><input id="gm-cardio" type="number" min="0" step="10" value="${p.cardio_target_min ?? ""}"></div>
    </div>
    <label>Dias que você pode treinar</label>
    <div class="chips" id="gm-days">${opts.weekdays.map((w, i) => `<label class="chip"><input type="checkbox" value="${i}" ${avail.includes(i) ? "checked" : ""}> ${w}</label>`).join("")}</div>
    <label style="margin-top:12px">Junto com a musculação</label>
    <div class="chips" id="gm-sports">${eopts.sports.map((sp) => `<label class="chip"><input type="checkbox" data-sp="${sp.id}" ${(ecfg.sports[sp.id] || {}).enabled ? "checked" : ""}> ${SPORT_ICON[sp.id] || ""} ${esc(sp.name)}</label>`).join("")}</div>
    <div class="stack" id="gm-sport-opts" style="margin-top:8px">${eopts.sports.map((sp) => { const c = ecfg.sports[sp.id] || {}; return `<div class="gm-sport" data-sp="${sp.id}" ${c.enabled ? "" : "hidden"}>
      <div class="grid2"><div class="field"><label>${SPORT_ICON[sp.id] || ""} ${esc(sp.name)}: onde</label><select data-k="env">${sp.envs.map((e) => `<option value="${e.id}" ${(c.env || sp.envs[0].id) === e.id ? "selected" : ""}>${esc(e.name)}</option>`).join("")}</select></div>
      <div class="field"><label>Vezes por semana</label><input type="number" min="1" max="7" data-k="sessions" value="${c.sessions || sp.sessions}"></div></div></div>`; }).join("")}</div>
    <label class="row gap" id="gm-same-wrap" style="margin-top:8px" ${Object.values(ecfg.sports).some((c) => c.enabled) ? "" : "hidden"}><input type="checkbox" id="gm-same" ${p.same_session !== false ? "checked" : ""}>
      Musculação e esporte no mesmo treino (os minutos acima valem para o dia todo: força primeiro, esporte logo depois)</label>
    <p class="small muted">Entra na mesma semana da musculação: longe dos dias de perna pesada, longo no fim de semana, ~80% leve.
      Metas de prova, triathlon e zonas ficam em <a href="#esportes" id="gm-esportes">Esportes</a>. Limitações e equipamentos vêm do Perfil.</p>
    <div class="row gap wrap"><button class="btn ghost" id="gm-prev">Pré-visualizar</button><button class="btn ghost" id="gm-var" hidden>Outra variação</button><button class="btn" id="gm-apply" hidden>Usar e montar minha semana</button></div>
    <div id="gm-out" style="margin-top:12px"></div>`);
  const read = () => ({
    training_months: +$("#gm-months", body).value, goal: $("#gm-goal", body).value,
    session_minutes: +$("#gm-min", body).value || 60,
    cardio_target_min: $("#gm-cardio", body).value === "" ? null : +$("#gm-cardio", body).value,
    available_days: $$("#gm-days input", body).filter((c) => c.checked).map((c) => +c.value),
    endurance: readSports(),
    same_session: $("#gm-same", body).checked,
  });
  const readSports = () => {
    const out = JSON.parse(JSON.stringify(ecfg));
    $$(".gm-sport", body).forEach((row) => {
      const id = row.dataset.sp; const c = out.sports[id] = { ...(out.sports[id] || {}) };
      c.enabled = $(`#gm-sports input[data-sp="${id}"]`, body).checked;
      c.env = $("[data-k=env]", row).value;
      c.sessions = Math.max(1, Math.min(7, +$("[data-k=sessions]", row).value || 1));
    });
    return out;
  };
  $("#gm-sports", body).onchange = (e) => {
    const id = e.target.dataset.sp;
    if (id) $(`.gm-sport[data-sp="${id}"]`, body).hidden = !e.target.checked;
    $("#gm-same-wrap", body).hidden = !$$("#gm-sports input", body).some((c) => c.checked);
  };
  const preview = async () => {
    const prof = read();
    if (!prof.available_days.length) return toast("Escolha pelo menos um dia");
    $("#gm-out", body).innerHTML = `<p class="muted">Montando…</p>`;
    const plan = await api("/api/generator/preview", { method: "POST", body: { profile: prof, seed } });
    $("#gm-out", body).innerHTML = `<h3>Por que assim</h3><ul class="small">${plan.rationale.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
      ${previewDays(plan)}
      <h3>Volume semanal planejado</h3>${muscleBars(plan.analysis.muscles, "sets_week")}`;
    // nomes dos exercícios
    const ids = [...new Set(plan.days.flatMap((d) => d.items.map((i) => i.exercise_id)))];
    const names = {}; await Promise.all(ids.map(async (id) => { try { names[id] = (await api(`/api/exercises/${encodeURIComponent(id)}`)).name; } catch (_) { /* */ } }));
    $$("#gm-out [data-ex]", body).forEach((el) => { if (names[el.dataset.ex]) el.textContent = names[el.dataset.ex]; });
    $("#gm-var", body).hidden = false; $("#gm-apply", body).hidden = false;
  };
  $("#gm-esportes", body).onclick = () => closeModal();
  $("#gm-prev", body).onclick = () => preview().catch((e) => toast(e.message));
  $("#gm-var", body).onclick = () => { seed += 1; preview().catch((e) => toast(e.message)); };
  $("#gm-apply", body).onclick = async () => {
    await api("/api/generator/apply", { method: "POST", body: { profile: read(), seed } });
    state.profile = await api("/api/profile");
    closeModal(); toast("Treino gerado e semana montada!");
    if (location.hash.startsWith("#hoje") || !location.hash) route(); else location.hash = "#hoje";
  };
}

// dias com musculação + esporte no mesmo treino aparecem juntos, com o tempo total do dia
function previewDays(plan) {
  const same = plan.generator && plan.generator.same_session; const budget = plan.generator && plan.generator.session_minutes;
  const groups = [];
  plan.days.forEach((d) => { const g = groups[groups.length - 1]; if (g && g[0].weekday === d.weekday) g.push(d); else groups.push([d]); });
  const one = (d, inGroup) => `<div class="day"><b>${esc(inGroup ? d.name.split(" – ").slice(1).join(" – ") : d.name)}</b>${timeBar(inGroup ? { ...d.time, orcamento: null } : d.time)}${itemsByPhase(d.items.map((i) => ({ ...i, _name: i._name })))}</div>`;
  return groups.map((g) => {
    if (g.length < 2) return one(g[0], false);
    const tot = Math.round(g.reduce((a, d) => a + d.time.total, 0));
    const head = `<b>${esc(g[0].name.split(" – ")[0])}</b> · ${same ? "mesmo treino" : "dois treinos"}: <b>${tot} min</b>${budget ? ` de ${budget}` : ""}${same ? " — musculação primeiro, corrida logo depois" : ""}`;
    return `<div class="day-group"><div class="small" style="margin:10px 0 4px">${head}</div>${g.map((d) => one(d, true)).join("")}</div>`;
  }).join("");
}

/* ---------------------------------------------------------------- COACH -- */
const LVL = { agir: ["bad", "Agir"], atencao: ["warn", "Atenção"] };
const WELL_ICON = ["", "😀", "🙂", "😐", "😕", "😣"];
function coachChrome() {
  const me = state.me; const isCoach = me && me.logged_in && me.user.is_coach; const act = actAs();
  let pill = $("#coach-pill");
  if (isCoach && !pill) { $(".topbar").insertAdjacentHTML("beforeend", `<a id="coach-pill" class="pill ok" href="#coach" style="text-decoration:none;margin-left:6px">Coach</a>`); }
  if (!isCoach && pill) pill.remove();
  let bar = $("#act-bar");
  if (act && !bar) {
    $(".topbar").insertAdjacentHTML("afterend", `<div id="act-bar" class="rec atencao small row between gap" style="margin:8px 16px 0">
      <span>👤 Você está vendo <b>${esc(act.name)}</b> como coach: tudo que mudar vale para essa conta.</span>
      <button class="btn sm" id="act-exit">Voltar ao painel</button></div>`);
    $("#act-exit").onclick = () => { sessionStorage.removeItem("gg_act"); $("#act-bar").remove(); state.profile = null; location.hash = "#coach"; route(); };
  }
  if (!act && bar) bar.remove();
}
async function viewCoach() {
  if (actAs()) { sessionStorage.removeItem("gg_act"); coachChrome(); }
  const d = await api("/api/coach/athletes");
  const c = d.counts;
  const regions = d.regions;
  const stat = (label, val, cls = "") => `<div class="cstat ${cls}"><b>${val ?? "—"}</b><span>${label}</span></div>`;
  const pct = (v) => v == null ? "—" : `${v}%`;
  const fmt1 = (v) => v == null ? "—" : (+v).toLocaleString("pt-BR", { maximumFractionDigits: 1 });
  const card = (a) => {
    const worst = a.flags.some((f) => f.level === "agir") ? "bad" : a.flags.length ? "warn" : "ok";
    const lims = Object.fromEntries((a.limitations || []).map((l) => [l.key, l.severity]));
    const w = a.wellness || {};
    return `<div class="card coach-card ${worst}" data-aid="${a.id}">
      <div class="row between gap"><h3 style="margin:0"><span class="dot-lg ${worst}"></span>${esc(a.name)}</h3>
        <span class="small muted">${a.last_training ? `último treino ${ddmm(a.last_training)}` : "sem treinos"}</span></div>
      ${a.flags.length ? `<ul class="flags">${a.flags.map((f) => `<li><span class="pill ${LVL[f.level][0]}">${LVL[f.level][1]}</span> ${esc(f.text)}
        <button class="icon-btn mute" title="Silenciar este alerta para este atleta" data-mute="${esc(f.code)}">×</button></li>`).join("")}</ul>`
        : `<p class="small" style="margin:6px 0">✅ Tudo dentro do normal.</p>`}
      <div class="cstats">${stat("adesão 7 dias", pct(a.compliance_7d), (a.compliance_7d ?? 100) < 70 ? "warn" : "")}
        ${stat("prontidão", a.readiness ?? "—")}${stat("sono 7 dias", a.sleep_7d == null ? "—" : `${fmt1(a.sleep_7d)} h`)}
        ${stat("carga semana", a.load_change == null ? "—" : `${a.load_change > 0 ? "+" : ""}${a.load_change}%`)}</div>
      ${a.health_shared === false ? `<p class="small muted">Não compartilha saúde/sono/check-in com você.</p>` : a.checkin_date ? `<p class="small">Check-in ${ddmm(a.checkin_date)}: ${Object.entries(w).map(([k, v]) => `${WELL_ICON[v]} ${esc(({ sleep_q: "sono", fatigue: "cansaço", soreness: "dor muscular", stress: "estresse", mood: "humor" })[k])}`).join(" · ")}
        ${a.checkin_note ? `<br><i>“${esc(a.checkin_note)}”</i>` : ""}</p>` : `<p class="small muted">Sem check-in recente.</p>`}
      ${(a.suggestions || []).map((sg) => `<div class="rec ${sg.severity === "evitar" ? "alerta" : "atencao"} small">Dor ${sg.pain}/10 em <b>${esc(sg.label.toLowerCase())}</b>${sg.worse ? " (piorando)" : ""} →
        sugestão: <b>${sg.severity}</b> e trocar os exercícios de risco${sg.refer ? ". Indique avaliação com fisioterapeuta/médico." : "."}
        <button class="btn sm" data-tension="${sg.region}" data-sev="${sg.severity}">Aplicar</button></div>`).join("")}
      <div class="row gap wrap" style="margin-top:8px">
        <button class="btn sm" data-open="${a.id}" data-name="${esc(a.name)}">Abrir</button>
        <button class="btn sm ghost" data-adjust="hoje">Hoje mais leve</button>
        <button class="btn sm ghost" data-adjust="semana">Semana leve</button>
        <button class="btn sm ghost" data-note>Recado</button></div>
      <details class="tension"><summary class="small"><b>Áreas de tensão</b>${Object.keys(lims).length ? ` · ${Object.entries(lims).filter(([k]) => regions[k]).map(([k, v]) => `${esc(regions[k].toLowerCase())} (${v})`).join(", ")}` : ""}</summary>
        <p class="small muted">Toque para alternar: livre → cuidado → evitar. O app troca na hora os exercícios de risco da planilha e da semana.</p>
        <div class="chips">${Object.entries(regions).map(([k, lbl]) => { const sev = lims[k] || ""; const pain = (a.pain || {})[k];
          return `<button class="chip tchip ${sev}" data-region="${k}" data-cur="${sev}">${esc(lbl)}${pain ? ` <b>${pain}/10</b>` : ""}${sev ? ` · ${sev}` : ""}</button>`; }).join("")}</div></details>
    </div>`;
  };
  view.innerHTML = `<div class="card"><div class="row between gap wrap"><h2 style="margin:0">Coach</h2>
      <button class="btn sm ghost" id="coach-cfg">Alertas ⚙</button></div>
    <div class="row gap wrap" style="margin-top:8px"><span class="pill bad">Agir: ${c.agir}</span><span class="pill warn">Atenção: ${c.atencao}</span><span class="pill ok">Ok: ${c.ok}</span></div>
    <p class="small muted">Quem precisa de você aparece primeiro. Os alertas comparam cada atleta com ele mesmo (últimas 4 semanas) e servem para puxar conversa, não para diagnosticar.</p></div>
    ${d.athletes.length ? d.athletes.map(card).join("") : `<div class="card"><p>Nenhum atleta ainda.</p><p class="small">Cada pessoa autoriza você em <b>Perfil → Meu coach</b> (convide pelo Perfil → Pessoas). Ela escolhe se compartilha saúde e sono e pode remover o acesso quando quiser.</p></div>`}`;
  view.onclick = async (e) => {
    const t = e.target.closest("button"); if (!t) return;
    const cardEl = t.closest("[data-aid]"); const aid = cardEl && cardEl.dataset.aid;
    try {
      if (t.id === "coach-cfg") return coachSettings();
      if (t.dataset.open) { sessionStorage.setItem("gg_act", JSON.stringify({ id: +t.dataset.open, name: t.dataset.name })); state.profile = null; location.hash = "#hoje"; return; }
      if (t.dataset.mute) { await api(`/api/coach/athletes/${aid}/mute`, { method: "POST", body: { code: t.dataset.mute } }); toast("Alerta silenciado para este atleta"); return viewCoach(); }
      if (t.dataset.adjust) {
        const r = await api(`/api/coach/athletes/${aid}/adjust`, { method: "POST", body: { scope: t.dataset.adjust, action: "moderar" } });
        toast(r.changed.length ? `Mais leve: ${r.changed.join(", ")}` : "Nada planejado para ajustar", 4000); return;
      }
      if (t.dataset.note !== undefined) {
        const text = prompt("Recado para o atleta (aparece no Hoje dele):"); if (!text) return;
        await api(`/api/coach/athletes/${aid}/note`, { method: "POST", body: { text } }); toast("Recado enviado"); return;
      }
      if (t.dataset.region || t.dataset.tension) {
        const region = t.dataset.region || t.dataset.tension;
        const next = t.dataset.tension ? t.dataset.sev : ({ "": "cuidado", cuidado: "evitar", evitar: "nenhuma" })[t.dataset.cur];
        t.disabled = true;
        const r = await api(`/api/coach/athletes/${aid}/tension`, { method: "POST", body: { region, severity: next } });
        toast(r.changes.length ? `Trocados: ${r.changes.slice(0, 3).join("; ")}${r.changes.length > 3 ? "…" : ""}` : `${regions[region]}: ${next === "nenhuma" ? "livre" : next}`, 6000);
        return viewCoach();
      }
    } catch (err) { toast(err.message, 5000); }
  };
}
async function coachSettings() {
  const { settings: s, defaults } = await api("/api/coach/settings");
  const rows = [["compliance_warn", "Adesão em 7 dias: atenção abaixo de (%)"], ["compliance_act", "Adesão: agir abaixo de (%)"],
    ["inactive_warn", "Dias sem treinar: atenção a partir de"], ["inactive_act", "Dias sem treinar: agir a partir de"],
    ["readiness_warn", "Prontidão: atenção abaixo de"], ["readiness_act", "Prontidão: agir abaixo de"],
    ["sleep_warn", "Sono (média 7 noites): atenção abaixo de (h)"], ["sleep_act", "Sono: agir abaixo de (h)"],
    ["rhr_warn", "FC de repouso acima do normal: atenção (+bpm)"], ["rhr_act", "FC de repouso: agir (+bpm)"],
    ["load_jump_warn", "Pico de carga semanal: atenção (+%)"], ["load_jump_act", "Pico de carga: agir (+%)"],
    ["wellness_warn", "Check-in: item ruim a partir de (1–5)"],
    ["pain_warn", "Dor: atenção a partir de (0–10)"], ["pain_act", "Dor: trocar/reduzir a partir de"], ["pain_avoid", "Dor: evitar + profissional a partir de"]];
  const body = openModal(`<h2>Alertas do coach</h2>
    <p class="small muted">Valores iniciais baseados em evidência: sono &lt; 7 h (consenso BJSM 2021), pico de carga semana a semana em vez de ACWR (críticas de Impellizzeri/Lolli),
      VFC e check-in comparados com o normal do próprio atleta (Plews, Buchheit, Saw 2016) e dor pelo modelo de Silbernagel (até 5/10, passando até a manhã seguinte e sem piorar).</p>
    <div class="grid2">${rows.map(([k, l]) => `<div class="field"><label>${l} <span class="muted">(padrão ${defaults[k]})</span></label><input type="number" step="any" data-k="${k}" value="${s[k]}"></div>`).join("")}</div>
    <button class="btn" id="cfg-save">Salvar</button>`);
  $("#cfg-save", body).onclick = async () => {
    const out = {}; $$("[data-k]", body).forEach((i) => { out[i.dataset.k] = i.value; });
    await api("/api/coach/settings", { method: "PUT", body: out }); closeModal(); toast("Alertas salvos"); viewCoach();
  };
}
// ---- lado do atleta: check-in e recados
async function athleteCards() {
  if (!(state.me && state.me.logged_in) || actAs()) return "";
  const [ck, inbox] = await Promise.all([api("/api/checkin").catch(() => null), api("/api/coach/inbox").catch(() => null)]);
  let html = "";
  if (inbox && (inbox.notes.length || inbox.changes.length) && (inbox.unread || inbox.changes.length)) {
    html += `<div class="card coach-inbox"><h3>Do seu coach</h3>
      ${inbox.notes.slice(0, 2).map((n) => `<p class="small"><b>${esc(n.coach)}</b> · ${fmtDate(n.at)}<br>${esc(n.text)}</p>`).join("")}
      ${inbox.changes.length ? `<ul class="plain small muted">${inbox.changes.map((x) => `<li>${fmtDate(x.at)}: ${esc(x.text)}</li>`).join("")}</ul>` : ""}
      <button class="btn sm ghost" id="inbox-ok">Ok, vi</button></div>`;
  }
  if (ck && !ck.today) {
    const opts = (k) => [1, 2, 3, 4, 5].map((v) => `<button type="button" class="wbtn" data-w="${k}" data-v="${v}" title="${v}">${WELL_ICON[v]}</button>`).join("");
    html += `<div class="card" id="checkin"><h3>Check-in de hoje <span class="small muted">(10 segundos)</span></h3>
      ${Object.entries(ck.wellness).map(([k, l]) => `<div class="row between gap wrow"><span class="small">${esc(l)}</span><span>${opts(k)}</span></div>`).join("")}
      <p class="small" style="margin:10px 0 4px">Alguma dor ou tensão? Toque na região e dê uma nota de 0 a 10.</p>
      <div class="chips">${Object.entries(ck.regions).map(([k, l]) => `<button type="button" class="chip pchip" data-p="${k}">${esc(l)}</button>`).join("")}</div>
      <div class="row gap" style="margin-top:8px"><input id="ck-note" placeholder="Observação (opcional)" style="flex:1"><button class="btn" id="ck-send">Enviar</button></div></div>`;
  }
  return html;
}
function bindAthleteCards() {
  const ok = $("#inbox-ok"); if (ok) ok.onclick = async () => { await api("/api/coach/inbox/read", { method: "POST" }); ok.closest(".card").remove(); };
  const box = $("#checkin"); if (!box) return;
  const val = {}; const pain = {};
  box.onclick = async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.w) { val[b.dataset.w] = +b.dataset.v; $$(`[data-w="${b.dataset.w}"]`, box).forEach((x) => x.classList.toggle("on", x === b)); return; }
    if (b.dataset.p) {
      const cur = pain[b.dataset.p] || 0; const v = prompt(`Dor em ${b.textContent.replace(/ \d+\/10$/, "")} (0 = nenhuma, 10 = máxima):`, cur || 3);
      if (v === null) return; const n = Math.max(0, Math.min(10, parseInt(v, 10) || 0));
      pain[b.dataset.p] = n; b.textContent = b.textContent.replace(/ \d+\/10$/, "") + (n ? ` ${n}/10` : ""); b.classList.toggle("on", n > 0); return;
    }
    if (b.id === "ck-send") {
      if (!Object.keys(val).length && !Object.keys(pain).length) return toast("Toque nas carinhas ou numa região");
      const r = await api("/api/checkin", { method: "POST", body: { ...val, pain, note: $("#ck-note").value } });
      box.innerHTML = `<p class="small">Check-in enviado ✔${r.suggestions.length ? `<br>${r.suggestions.map((sg) => `Dor ${sg.pain}/10 em ${esc(sg.label.toLowerCase())}: ${sg.severity === "evitar" ? "evite os movimentos que doem e procure um profissional" : "reduza a carga dessa região e troque o que doer"}${sg.worse ? " (piorou)" : ""}.`).join("<br>")} Seu coach foi avisado.` : ""}</p>`;
    }
  };
}
async function coachMineCard() {
  const me = state.me; if (!(me && me.logged_in) || actAs()) return "";
  const m = await api("/api/coach/mine").catch(() => null);
  if (!m || !m.coaches.length) return "";
  return `<div class="card"><h2>Meu coach</h2><p class="small muted">O coach vê seus treinos, planilha e agenda e pode ajustá-los (você vê o que ele mudou no Hoje).
    Saúde, sono e check-in só se você deixar. Ele nunca vê sua senha, seu login do Garmin ou seu código pessoal.</p>
    <ul class="list">${m.coaches.map((c) => `<li class="row between gap wrap"><span><b>${esc(c.name)}</b>${c.linked ? ` <span class="pill ok">tem acesso</span>` : ""}</span>
      <span class="row gap">${c.linked ? `<label class="small row gap" style="margin:0"><input type="checkbox" data-share="${c.id}" ${c.share_health ? "checked" : ""}> saúde e sono</label>
        <button class="btn sm ghost" data-unlink="${c.id}">Remover acesso</button>` : `<button class="btn sm" data-link="${c.id}">Autorizar</button>`}</span></li>`).join("")}</ul></div>`;
}
function bindCoachMine() {
  $$("[data-link]").forEach((b) => { b.onclick = async () => { await api(`/api/coach/mine/${b.dataset.link}`, { method: "POST" }); toast("Coach autorizado"); viewPerfil(); }; });
  $$("[data-unlink]").forEach((b) => { b.onclick = async () => { if (!confirm("Remover o acesso deste coach?")) return; await api(`/api/coach/mine/${b.dataset.unlink}`, { method: "DELETE" }); viewPerfil(); }; });
  $$("[data-share]").forEach((b) => { b.onchange = async () => { await api(`/api/coach/mine/${b.dataset.share}/share`, { method: "POST", body: { health: b.checked } }); toast(b.checked ? "Saúde e sono compartilhados" : "Saúde e sono não compartilhados"); }; });
  $$("[data-coach]").forEach((b) => { b.onclick = async () => { await api(`/api/admin/users/${b.dataset.coach}/coach`, { method: "POST", body: { on: b.dataset.on === "1" } }); viewPerfil(); }; });
}

/* ---------------------------------------------------------------- CONTA -- */
async function showAuth(mode, code) {
  document.body.classList.add("auth-mode");
  $("#modal").hidden = true;
  let inv = null;
  if (mode === "convite") { try { inv = await api(`/api/auth/invite/${encodeURIComponent(code)}`); } catch (_) { inv = { valid: false }; } }
  const isReg = mode === "convite" && inv && inv.valid;
  view.innerHTML = `<div class="card auth-card"><h2>${isReg ? "Criar sua conta" : "Entrar"}</h2>
    ${mode === "convite" && !isReg ? `<p class="pill bad">Convite inválido ou já usado. Peça um novo a quem te convidou.</p>` : ""}
    ${isReg ? `<p class="small muted">Você foi convidado${inv.note ? ` (${esc(inv.note)})` : ""} para o Hybrid Lab. Seus treinos, planilhas e Garmin ficam só na sua conta.</p>` : ""}
    <form id="auth-form" class="stack">
      ${isReg ? `<div class="field"><label>Seu nome</label><input name="name" autocomplete="name" required></div>` : ""}
      <div class="field"><label>E-mail</label><input name="email" type="email" autocomplete="email" required></div>
      <div class="field"><label>Senha${isReg ? " (mín. 8 caracteres)" : ""}</label><input name="password" type="password" minlength="${isReg ? 8 : 1}" autocomplete="${isReg ? "new-password" : "current-password"}" required></div>
      <button class="btn" type="submit">${isReg ? "Criar conta" : "Entrar"}</button>
    </form>
    ${!isReg ? `<p class="small muted">Esqueceu a senha? Peça ao administrador uma senha provisória.</p>` : ""}</div>`;
  $("#auth-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    const btn = e.target.querySelector("button"); btn.disabled = true;
    try {
      const me = isReg ? await api("/api/auth/register", { method: "POST", body: { ...f, code } })
        : await api("/api/auth/login", { method: "POST", body: f });
      state.me = me; state.profile = null; state.meta = null;
      clearLocalDrafts();
      if (location.hash.startsWith("#convite")) history.replaceState(null, "", isReg ? "#perfil" : "#hoje");
      startApp();
    } catch (err) { toast(err.message, 5000); btn.disabled = false; }
  };
}
function clearLocalDrafts() {
  // rascunhos de treino ficam no aparelho: não passam de uma conta para outra
  try { Object.keys(localStorage).filter((k) => k.startsWith("gg_")).forEach((k) => localStorage.removeItem(k)); } catch (_) { /* */ }
}
async function accountCard() {
  const me = state.me || await api("/api/auth/me").catch(() => null) || {};
  if (me.setup_needed) {
    return `<div class="card"><h2>Sua conta</h2><p class="small">Crie seu login para proteger o app e poder convidar família e amigos.
      Os treinos e planilhas que já estão aqui ficam na sua conta.</p>
      <form id="acc-setup" class="stack"><div class="grid2"><div class="field"><label>E-mail</label><input name="email" type="email" required></div>
      <div class="field"><label>Senha (mín. 8)</label><input name="password" type="password" minlength="8" autocomplete="new-password" required></div></div>
      <button class="btn" type="submit">Criar minha conta</button></form></div>`;
  }
  if (!me.logged_in) return "";
  const u = me.user;
  let admin = "";
  if (u.is_admin) {
    const a = await api("/api/admin/users");
    const base = location.origin + location.pathname;
    admin = `<div class="card"><h2>Pessoas</h2>
      <ul class="list">${a.users.map((x) => `<li class="row between gap"><span><b>${esc(x.name || x.email)}</b> <span class="small muted">${esc(x.email)}${x.is_admin ? " · admin" : x.is_coach ? " · coach" : ""}</span></span>
        ${x.user_id !== u.id ? `<span class="row gap"><button class="btn sm ghost" data-coach="${x.user_id}" data-on="${x.is_coach ? 0 : 1}">${x.is_coach ? "Tirar coach" : "Tornar coach"}</button><button class="btn sm ghost" data-reset="${x.user_id}">Senha provisória</button></span>` : ""}</li>`).join("")}</ul>
      <h3>Convites</h3>
      ${a.invites.length ? `<ul class="list">${a.invites.map((i) => `<li class="row between gap wrap"><span class="small"><b>${esc(i.note || "Convite")}</b><br><span class="muted" style="word-break:break-all">${esc(base)}#convite/${esc(i.code)}</span></span>
        <span class="row gap"><button class="btn sm" data-copy="${esc(base)}#convite/${esc(i.code)}">Copiar link</button><button class="btn sm ghost" data-delinv="${esc(i.code)}">Apagar</button></span></li>`).join("")}</ul>` : `<p class="small muted">Nenhum convite pendente.</p>`}
      <form id="inv-form" class="row gap wrap"><input name="note" placeholder="Para quem? (ex.: Maria)" style="flex:1;min-width:160px"><button class="btn sm" type="submit">Criar convite</button></form>
      <p class="small muted">Cada convite vale para uma pessoa. Mande o link por WhatsApp: ela cria e-mail e senha e começa com o app vazio, com seu próprio Garmin.</p></div>`;
  }
  return `<div class="card"><h2>Sua conta</h2><p><b>${esc(u.name || "")}</b> <span class="small muted">${esc(u.email)}${u.is_admin ? " · administrador" : ""}</span></p>
    <details><summary class="small">Trocar senha</summary><form id="pw-form" class="stack"><div class="grid2">
      <div class="field"><label>Senha atual</label><input name="current" type="password" autocomplete="current-password" required></div>
      <div class="field"><label>Nova senha (mín. 8)</label><input name="new" type="password" minlength="8" autocomplete="new-password" required></div></div>
      <button class="btn sm" type="submit">Salvar senha</button></form></details>
    <p><button class="btn ghost sm" id="logout">Sair</button></p></div>${admin}`;
}
async function devicesCard() {
  const me = state.me || {};
  if (!me.logged_in) return "";
  const st = await api("/api/account/ingest").catch(() => ({}));
  const last = st.last;
  const url = `${location.origin}/api/ingest`;
  return `<div class="card" id="devices"><h2>Outros relógios (iPhone / Android)</h2>
    <p class="small">Apple Watch, Xiaomi, Amazfit, Galaxy, Pixel…: o celular manda treinos, sono, FC de repouso e passos para a sua conta.
      Primeiro gere seu <b>código pessoal</b> (é como uma senha só para isso).</p>
    <div class="row gap wrap"><button class="btn sm" id="ing-new">${st.has_token ? "Gerar código novo" : "Gerar código pessoal"}</button>
      ${st.has_token ? `<button class="btn sm ghost" id="ing-del">Desligar</button>` : ""}</div>
    <div id="ing-token"></div>
    <p class="small ${last ? "" : "muted"}">${last ? `Último envio: <b>${fmtDate(last.at)}</b> (${last.source === "android" ? "Android" : "iPhone"}) · ${last.days.length} dia(s) de saúde · ${last.workouts} treino(s) novo(s)${last.skipped ? ` · ${last.skipped} ignorado(s) (tipo não usado, ex.: ioga)` : ""}` : "Nenhum envio recebido ainda."}</p>
    <details><summary><b>iPhone (Apple Saúde) — app Atalhos</b></summary><div class="small">
      <ol>
        <li>Abra o app <b>Atalhos</b> → <b>+</b> (novo atalho) → nome "Hybrid Lab".</li>
        <li>Adicione <b>Encontrar Amostras de Saúde</b>: Tipo <b>Análise do Sono</b>, Data de Início <b>nas últimas 24 horas</b>. Depois <b>Obter Detalhes das Amostras de Saúde → Duração</b> e <b>Calcular Estatísticas → Soma</b> (resultado em horas).</li>
        <li>Repita para <b>Frequência Cardíaca em Repouso</b> (estatística <b>Média</b>), <b>Variabilidade da Frequência Cardíaca</b> (Média) e <b>Passos</b> (Soma, hoje).</li>
        <li>Adicione <b>Dicionário</b> com as chaves: <code>source</code> = apple · <code>date</code> = Data Atual formatada como <b>ISO 8601</b> (só a data) · <code>sleep_h</code> · <code>rhr</code> · <code>hrv</code> · <code>hrv_kind</code> = sdnn · <code>steps</code> (cada uma com o resultado do passo correspondente).</li>
        <li>Opcional (treinos do Apple Watch ou de outro relógio que grava no Apple Saúde): <b>Encontrar Amostras de Saúde</b> do tipo <b>Treinos</b> nos últimos 2 dias → <b>Repetir com Cada</b> → um Dicionário por treino com <code>id</code>, <code>type</code>, <code>start</code> (ISO 8601), <code>duration_min</code>, <code>distance_km</code>, <code>kcal</code>, <code>avg_hr</code> → junte numa lista e ponha em <code>workouts</code>.</li>
        <li>Adicione <b>Obter Conteúdo do URL</b>: URL <code style="word-break:break-all">${esc(url)}</code>, Método <b>POST</b>, Cabeçalho <code>Authorization</code> = <code>Bearer SEU-CÓDIGO</code>, Corpo da Solicitação <b>JSON</b> = o Dicionário.</li>
        <li>Em <b>Automação</b> → <b>Hora do Dia</b> (ex.: 9h, todo dia) → executar o atalho, sem perguntar.</li>
      </ol>
      <p class="muted">Rode uma vez à mão e confira aqui em "Último envio". Os nomes dos menus mudam um pouco entre versões do iOS. Xiaomi (Mi Fitness) e Amazfit (Zepp) no iPhone: ligue a opção <b>Apple Saúde</b> no app do relógio, e o atalho leva os dados junto.</p></div></details>
    <details><summary><b>Android (Health Connect)</b></summary><div class="small">
      <p>Xiaomi (Mi Fitness), Samsung Health, Google Fit, Amazfit (Zepp), Fitbit…: no app do relógio, ligue o compartilhamento com o <b>Health Connect</b>.
        Depois instale o app <b>Hybrid Lab Sync</b> (Android), cole o código pessoal e toque em Sincronizar. Ele envia sozinho a cada poucas horas.</p>
      <p><a class="btn sm ghost" href="/static/gymgarmin-sync.apk" download>Baixar app Android (APK)</a></p>
      <p class="muted">Como o app não está na Play Store, o Android pede para permitir "instalar apps desta fonte".</p></div></details>
  </div>`;
}
function bindDevices() {
  const n = $("#ing-new");
  if (n) n.onclick = async () => {
    if ($("#ing-del") && !confirm("Gerar um código novo? O antigo para de funcionar (atualize o atalho/app).")) return;
    const r = await api("/api/account/ingest-token", { method: "POST" });
    $("#ing-token").innerHTML = `<p class="rec atencao small">Seu código (aparece só agora — copie e guarde): <br><code style="word-break:break-all;font-size:15px">${esc(r.token)}</code>
      <br><button class="btn sm" id="ing-copy">Copiar código</button></p>`;
    $("#ing-copy").onclick = async () => { try { await navigator.clipboard.writeText(r.token); toast("Código copiado"); } catch (_) { prompt("Copie o código:", r.token); } };
  };
  const d = $("#ing-del");
  if (d) d.onclick = async () => { if (!confirm("Desligar? O atalho/app para de enviar.")) return; await api("/api/account/ingest-token", { method: "DELETE" }); viewPerfil(); };
}
function bindAccount() {
  bindDevices();
  bindCoachMine();
  const st = $("#acc-setup");
  if (st) st.onsubmit = async (e) => {
    e.preventDefault();
    try { state.me = await api("/api/auth/setup", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }); toast("Conta criada: o app agora pede login."); viewPerfil(); }
    catch (err) { toast(err.message, 5000); }
  };
  const lo = $("#logout");
  if (lo) lo.onclick = async () => { await api("/api/auth/logout", { method: "POST" }); state.me = null; clearLocalDrafts(); showAuth("login"); };
  const pw = $("#pw-form");
  if (pw) pw.onsubmit = async (e) => {
    e.preventDefault();
    try { await api("/api/account/password", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }); toast("Senha trocada. Os outros aparelhos vão pedir login de novo."); e.target.reset(); }
    catch (err) { toast(err.message, 5000); }
  };
  const inv = $("#inv-form");
  if (inv) inv.onsubmit = async (e) => {
    e.preventDefault();
    const r = await api("/api/admin/invites", { method: "POST", body: Object.fromEntries(new FormData(e.target)) });
    const link = `${location.origin}${location.pathname}#convite/${r.code}`;
    try { await navigator.clipboard.writeText(link); toast("Convite criado e link copiado"); } catch (_) { toast("Convite criado"); }
    viewPerfil();
  };
  $$("[data-copy]").forEach((b) => { b.onclick = async () => { try { await navigator.clipboard.writeText(b.dataset.copy); toast("Link copiado"); } catch (_) { prompt("Copie o link:", b.dataset.copy); } }; });
  $$("[data-delinv]").forEach((b) => { b.onclick = async () => { await api(`/api/admin/invites/${b.dataset.delinv}`, { method: "DELETE" }); viewPerfil(); }; });
  $$("[data-reset]").forEach((b) => { b.onclick = async () => {
    if (!confirm("Gerar uma senha provisória para esta pessoa? A senha atual dela deixa de valer.")) return;
    const r = await api(`/api/admin/users/${b.dataset.reset}/reset-password`, { method: "POST" });
    prompt("Senha provisória (envie para a pessoa; ela troca no Perfil):", r.temporary_password);
  }; });
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
      <input type="date" id="push-date" style="width:auto" value="${localISO().slice(0, 10)}">
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
      ${mediaHtml(ex.id, "s")}
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
    <div class="ex-hero">${mediaHtml(ex.id, "l")}</div>
    ${ex.images.length > 1 ? `<div class="imgs">${ex.images.map((s, i) => `<figure><img loading="lazy" src="${esc(s)}" alt=""><figcaption class="small muted">${i ? "fim do movimento" : "início"}</figcaption></figure>`).join("")}</div>` : ""}
    <p><span class="pill">${esc(ex.category_pt)}</span> <span class="pill muted">${esc(ex.equipment_pt || "")}</span> <span class="pill muted">${esc(ex.level_pt || "")}</span>
      ${ex.mechanic_pt && ex.mechanic_pt !== "—" ? `<span class="pill muted">${esc(ex.mechanic_pt)}</span>` : ""} ${ex.impact ? `<span class="pill muted">impacto ${esc(ex.impact)}</span>` : ""}</p>
    <p><b>Primários:</b> ${esc(ex.primary_pt.join(", ") || "—")}<br><b>Secundários:</b> ${esc(ex.secondary_pt.join(", ") || "—")}</p>
    ${ex.limitation.status !== "ok" ? `<div class="rec ${ex.limitation.status === "evitar" ? "alerta" : "atencao"}"><b>${LIM_TXT[ex.limitation.status].toUpperCase()}</b><br>${ex.limitation.reasons.map(esc).join("<br>")}</div>` : ex.limitation.reasons.length ? `<div class="rec">${ex.limitation.reasons.map(esc).join("<br>")}</div>` : ""}
    ${ex.last ? `<p class="small"><b>Última vez</b> (${fmtDate(ex.last.date)}): ${ex.last.sets ? ex.last.sets.map((s) => `${s.reps ?? "-"}×${s.load_kg ?? 0}kg`).join(", ") : `${ex.last.duration_min} min Z${ex.last.zone}`}</p>` : ""}
    ${ex.last && ex.last.sets ? `<p><a class="btn sm ghost" href="#evolucao/${encodeURIComponent(ex.id)}" data-close>📈 Ver evolução</a></p>` : ""}
    ${ex.instructions && ex.instructions.length ? `<details><summary>Execução</summary><ol class="small">${ex.instructions.map((s) => `<li>${esc(s)}</li>`).join("")}</ol></details>` : ""}
    <div class="hr"></div>
    <div class="row between gap wrap"><div class="small">No Garmin: <b>${esc(g.garmin_name || g.category)}</b> <span class="muted">(${esc(g.category)} / ${esc(g.exercise)})</span>
      <span class="pill ${g.confidence === "alta" || g.confidence === "manual" ? "ok" : "warn"}">${esc(g.confidence)}</span></div>
      ${ex.machine ? "" : `<button class="btn sm ghost" id="g-edit">Alterar</button>`}</div>
    <div id="g-box"></div>`);
  const evo = $("[data-close]", body); if (evo) evo.onclick = () => closeModal();
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
    $("#free").onclick = () => { state.session = { date: localISO(), day_name: "Treino livre", items: [] }; renderSession(); };
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
      <button class="check ${it.done ? "on" : ""}" data-pdone="${i}">✓</button>${mediaHtml(it.exercise_id, "m")}
      <span class="grow"><b data-ex="${esc(it.exercise_id)}" style="cursor:pointer">${esc(it.name)}</b><br><span class="small muted">${it.kind === "cardio" ? `${it.duration_min} min Z${it.zone}` : it.plan && it.plan.reps ? `${it.plan.sets > 1 ? it.plan.sets + " × " : ""}${esc(it.plan.reps)} reps` : `${(it.plan && it.plan.duration_s) || 30}s${(it.plan && it.plan.sets) > 1 ? ` × ${it.plan.sets}` : ""}`}${it.plan && it.plan.notes ? " · " + esc(it.plan.notes) : ""}</span></span></li>`).join("")}</ul></div>` : "";
  view.innerHTML = `<div class="card"><h2>${esc(s.day_name || "Treino")}</h2>
      <div class="field" style="margin:0"><label for="s-date">Data e hora</label><input type="datetime-local" id="s-date" value="${esc((s.date || "").slice(0, 16))}"></div>
      ${s.deload ? `<p class="small rec">Semana de deload: menos séries, carga −10%, RPE ~6.</p>` : ""}</div>
    ${compactCard("preparo", "Aquecimento — elevar, ativar, potencializar")}
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
  const head = `<div class="row gap">${mediaHtml(it.exercise_id, "m")}
    <div class="grow"><b data-ex="${esc(it.exercise_id)}" style="cursor:pointer">${esc(it.name)}</b> ${limBadge(it.limitation)}
    <div class="small muted">${it.kind === "cardio" ? `${plan.duration_min || ""} min · Z${plan.zone || ""}` : `${plan.sets || ""}×${plan.reps || (plan.duration_s ? plan.duration_s + "s" : "")} ${plan.load_kg ? "· " + plan.load_kg + " kg" : ""} · descanso ${plan.rest_s || 90}s${plan.rir && !plan.deload ? ` · <span title="${RIR_TIP}">sobrar ${esc(plan.rir)} reps</span>` : ""}`}
    ${lastTxt ? `<br>Última: ${esc(lastTxt)}` : ""}${it.suggestion && it.suggestion.note ? `<br><b>${esc(it.suggestion.note)}</b>` : ""}${plan.notes ? `<br>${esc(plan.notes)}` : ""}</div></div></div>`;
  if (it.endurance) {
    const en = it.endurance; const meters = ["natacao", "remo"].includes(en.sport);
    return `<div class="card"><div><b>${SPORT_ICON[en.sport] || ""} ${esc(en.title)}</b> <span class="small muted">${esc(endSummary(en))}</span>
      ${en.note ? `<div class="small">${esc(en.note)}</div>` : ""}${endStepsHtml(en, true)}</div>
      <div class="grid2" style="margin-top:8px">
      <div><label>Distância (${meters ? "m" : "km"})</label><input inputmode="decimal" data-c="dist_in" data-i="${i}" value="${esc(it.dist_in ?? "")}" placeholder="${meters ? (en.dist_m || "") : (en.dist_m ? (en.dist_m / 1000).toFixed(1) : "")}"></div>
      <div><label>Tempo (h:mm:ss ou mm:ss)</label><input data-t="time_str" data-i="${i}" value="${esc(it.time_str ?? "")}" placeholder="${fmtS(en.dur_s)}"></div>
      <div><label>FC média</label><input type="number" data-c="avg_hr" data-i="${i}" value="${esc(it.avg_hr ?? "")}"></div>
      ${en.sport === "ciclismo" ? `<div><label>Potência média (W)</label><input type="number" data-c="avg_power" data-i="${i}" value="${esc(it.avg_power ?? "")}"></div>` : ""}
      <div><label>Feito</label><button class="check ${it.done ? "on" : ""}" data-cdone="${i}">✓</button></div></div>
      <p class="small muted" id="pace-${i}">${esc(paceTxt(it))}</p></div>`;
  }
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
    if (el.dataset.c) { s.items[+el.dataset.i][el.dataset.c] = el.value === "" ? null : +String(el.value).replace(",", "."); }
    if (el.dataset.t) { s.items[+el.dataset.i][el.dataset.t] = el.value; }
    if (el.dataset.c || el.dataset.t) { const pe = $(`#pace-${el.dataset.i}`); if (pe) pe.textContent = paceTxt(s.items[+el.dataset.i]); }
    save();
  };
  view.onchange = view.oninput;
  view.onclick = (e) => {
    const t = e.target;
    if (t.dataset.ex) return showExercise(t.dataset.ex);
    if (t.dataset.done) {
      const [i, j] = t.dataset.done.split(":").map(Number); const set = s.items[i].sets[j]; set.done = !set.done; save();
      t.classList.toggle("on", set.done); t.closest("tr").classList.toggle("done", set.done);
      if (set.done) startTimer((s.items[i].plan || {}).rest_s || 90, t);
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
      items: s.items.filter((it) => it.kind === "cardio" || (it.phase || "principal") === "principal").map((it) => it.endurance
        ? (it.done ? endPayload(it) : null)
        : it.kind === "cardio"
        ? (it.done ? { exercise_id: it.exercise_id, duration_min: it.duration_min, zone: it.zone, avg_hr: it.avg_hr } : null)
        : { exercise_id: it.exercise_id, ...((it.plan && it.plan.deload) ? { deload: true } : {}), sets: it.sets.filter((x) => x.done).map(({ reps, load_kg, rpe, duration_s }) => ({ reps, load_kg, rpe, duration_s })) })
        .filter((it) => it && (it.duration_min || (it.sets && it.sets.length))),
    };
    payload.client_id = s.client_id || (s.client_id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
    save();
    const btn = $("#s-finish"); btn.disabled = true;
    let saved;
    try { saved = await api("/api/sessions", { method: "POST", body: payload }); } catch (err) {
      btn.disabled = false;
      if (!err.offline) return toast(err.message, 5000);
      // academia sem sinal: guarda no celular e envia sozinho quando a conexão voltar
      const box = store("gg_outbox") || []; if (!box.some((x) => x.client_id === payload.client_id)) box.push(payload); store("gg_outbox", box);
      store("gg_session", null); state.session = null; stopTimer();
      toast("Sem internet: o treino ficou guardado no celular e será enviado sozinho quando a conexão voltar.", 7000);
      location.hash = "#hoje"; return;
    }
    store("gg_session", null); state.session = null; stopTimer();
    const prs = (saved && saved.prs) || [];
    toast(prs.length ? `Treino salvo! 🏆 Recorde em ${prs.map((p) => `${p.name} (${prText(p)})`).join(", ")}` : "Treino salvo! 🎉", prs.length ? 6000 : 2600);
    location.hash = s.schedule_id ? "#hoje" : "#analise";
  };
}

/* ---- timer de descanso ---- */
let timerEnd = 0, timerInt;
function startTimer(sec, from) {
  timerEnd = Date.now() + sec * 1000; $("#timer").hidden = false; document.body.classList.add("timer-on"); tick();
  clearInterval(timerInt); timerInt = setInterval(tick, 250);
  // deixa a próxima série (ou o próximo exercício) visível acima do cronômetro
  requestAnimationFrame(() => {
    const all = $$("#view [data-done]"); const k = from ? all.indexOf(from) : -1;
    const next = all.slice(k + 1).find((b) => !b.classList.contains("on"));
    if (!next) return;
    const row = next.closest("tr") || next; const rb = row.getBoundingClientRect(); const top = $("#timer").getBoundingClientRect().top;
    if (rb.bottom > top - 12 || rb.top < 60) window.scrollBy({ top: rb.bottom - (top - 12), behavior: "smooth" });
  });
}
function stopTimer() { clearInterval(timerInt); $("#timer").hidden = true; document.body.classList.remove("timer-on"); }
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
  const [hist, ld] = await Promise.all([api("/api/history").catch(() => ({ exercises: [], recent_prs: [] })),
    api("/api/load?days=90").catch(() => null)]);
  view.innerHTML = `
  <a class="card link-card row between gap" href="#saude"><span><b>😴 Saúde e sono</b><br><span class="small muted">Sono, recuperação, FC de repouso, HRV, VO2máx e o efeito dos treinos — pelo relógio</span></span><span class="chev">›</span></a>
  ${loadCard(ld)}
  ${historyCard(hist)}
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
  <div class="card"><h2>Histórico</h2>${sessions.length ? `<ul class="list">${sessions.map((s) => `<li class="row between gap"><span><b>${esc(s.day_name || s.notes || "Treino")}</b> <span class="small muted">${fmtDate(s.date)} ${s.source === "garmin" ? "· Garmin" : ""}</span><br>
      <span class="small muted">${s.items.length} exercícios · ${s.items.reduce((a, it) => a + (it.sets ? it.sets.length : 0), 0)} séries</span></span>
      <button class="icon-btn" data-del="${s.id}" title="Excluir">🗑</button></li>`).join("")}</ul>` : `<p class="empty">Nenhum treino registrado.</p>`}</div>`;
  view.onclick = async (e) => {
    const h = e.target.closest("[data-hist]"); if (h) { location.hash = `#evolucao/${encodeURIComponent(h.dataset.hist)}`; return; }
    if (e.target.dataset.ex) return showExercise(e.target.dataset.ex);
    if (e.target.dataset.del && confirm("Excluir este registro?")) { await api(`/api/sessions/${e.target.dataset.del}`, { method: "DELETE" }); viewAnalise(); }
  };
}

/* ------------------------------------------------- EVOLUÇÃO DE CARGAS -- */
const METRIC = {
  carga: { label: "1RM estimado", unit: " kg", fmt: (v) => `${(+v).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} kg` },
  reps: { label: "Reps máximas", unit: " reps", fmt: (v) => `${v} reps` },
  tempo: { label: "Tempo máximo", unit: " s", fmt: (v) => fmtSec(v) },
};
const PR_TXT = { e1rm: "1RM est.", top_load: "carga", rep_load: "carga × reps", volume: "volume", max_reps: "reps", max_duration_s: "tempo" };
const HIST_STATUS = { progredindo: ["Progredindo", "ok"], estavel: ["Estável", ""], estagnado: ["Estagnado", "warn"], caindo: ["Caindo", "bad"], inicio: ["Começando", "muted"], irregular: ["Dados irregulares", "warn"] };
const fmtKg = (v) => `${(+v || 0).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} kg`;
function fmtSec(v) { v = Math.round(v || 0); return v >= 60 ? `${Math.floor(v / 60)}:${String(v % 60).padStart(2, "0")} min` : `${v} s`; }
function prText(p) {
  if (p.rep_pr && p.prs && p.prs.length === 1 && p.prs[0] === "rep_load") return `${fmtKg(p.rep_pr.load_kg)} para ${p.rep_pr.reps} reps`;
  if (p.metric === "carga" && p.best_set) return `${fmtKg(p.best_set.load_kg)} × ${p.best_set.reps}`;
  if (p.metric === "reps") return `${p.max_reps} reps`;
  return fmtSec(p.max_duration_s);
}
function pctTxt(v) { return v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toLocaleString("pt-BR")}%`; }
function spark(vals) {
  if (!vals || vals.length < 2) return `<svg class="spark" viewBox="0 0 80 24" aria-hidden="true"></svg>`;
  const mn = Math.min(...vals), mx = Math.max(...vals), r = mx - mn || 1;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1)) * 78 + 1},${22 - ((v - mn) / r) * 20}`).join(" ");
  return `<svg class="spark" viewBox="0 0 80 24" aria-hidden="true"><polyline points="${pts}" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}
function historyCard(h) {
  if (!h.exercises.length) return `<div class="card"><h2>Evolução de cargas</h2><p class="empty">Registre treinos (no app ou importando do Garmin) para ver a evolução de cada exercício.</p></div>`;
  const prs = h.recent_prs.slice(0, 6);
  return `<div class="card"><h2>Evolução de cargas</h2>
    ${prs.length ? `<div class="pr-strip">${prs.map((p) => `<button class="pr-chip" data-hist="${esc(p.exercise_id)}">🏆 <b>${esc(p.name)}</b><span class="small muted">${ddmm(p.date)} · ${esc(prText(p))}</span></button>`).join("")}</div>` : ""}
    <ul class="list hist-list">${h.exercises.map((r) => { const sm = r.summary; const st = HIST_STATUS[sm.status] || ["", ""]; const m = METRIC[sm.metric] || METRIC.carga;
      return `<li class="hist-row" data-hist="${esc(r.exercise_id)}">
        <span class="grow"><b>${esc(r.name)}</b> <span class="pill small ${st[1]}">${st[0]}</span><br>
        <span class="small muted">${sm.sessions} treino${sm.sessions > 1 ? "s" : ""} · último ${ddmm(sm.last_date)} · 30 dias ${pctTxt(sm.change_30d_pct)}</span></span>
        <span class="hist-val">${spark(r.spark)}<b>${sm.last_value == null ? "—" : esc(m.fmt(sm.last_value))}</b></span></li>`; }).join("")}</ul>
    <p class="small muted">Valor principal: 1RM estimado (fórmula de Epley) para exercícios com carga; reps máximas no peso corporal; tempo máximo nos isométricos.</p></div>`;
}
function historyChart(points, metric) {
  if (points.length < 2) return `<p class="empty">O gráfico aparece a partir do segundo treino deste exercício.</p>`;
  const W = 340, H = 180, L = 40, R = 10, T = 12, B = 26;
  const day = (iso) => new Date(iso + "T12:00:00").getTime();
  const x0 = day(points[0].date), x1 = day(points[points.length - 1].date) || x0 + 1;
  const series = [points.map((p) => p.value)];
  if (metric === "carga") series.push(points.map((p) => p.top_load));
  const all = series.flat().filter((v) => v > 0);
  let mn = Math.min(...all), mx = Math.max(...all); const pad = (mx - mn) * 0.12 || mx * 0.05 || 1; mn = Math.max(0, mn - pad); mx += pad;
  const X = (iso) => L + ((day(iso) - x0) / ((x1 - x0) || 1)) * (W - L - R);
  const Y = (v) => T + (1 - (v - mn) / (mx - mn)) * (H - T - B);
  const path = (vals) => vals.map((v, i) => `${i ? "L" : "M"}${X(points[i].date).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
  const ticks = [mn, (mn + mx) / 2, mx];
  const m = METRIC[metric];
  return `<svg class="hist-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Gráfico de ${esc(m.label)} ao longo do tempo">
    ${ticks.map((t) => `<line x1="${L}" x2="${W - R}" y1="${Y(t)}" y2="${Y(t)}" class="grid"/><text x="${L - 4}" y="${Y(t) + 3}" text-anchor="end" class="axis">${Math.round(t)}</text>`).join("")}
    <text x="${L}" y="${H - 6}" class="axis">${ddmm(points[0].date)}</text><text x="${W - R}" y="${H - 6}" text-anchor="end" class="axis">${ddmm(points[points.length - 1].date)}</text>
    ${series[1] ? `<path d="${path(series[1])}" class="line2"/>` : ""}
    <path d="${path(series[0])}" class="line1"/>
    ${points.map((p) => `<circle cx="${X(p.date)}" cy="${Y(p.value)}" r="${p.prs.length ? 4.5 : 3}" class="${p.prs.length ? "dot pr" : "dot"}"><title>${ddmm(p.date)}: ${m.fmt(p.value)}${p.best_set ? ` (${fmtKg(p.best_set.load_kg)} × ${p.best_set.reps})` : ""}${p.prs.length ? " — recorde" : ""}</title></circle>`).join("")}
  </svg>
  <div class="row gap small muted"><span><i class="lg lg1"></i>${esc(m.label)}</span>${series[1] ? `<span><i class="lg lg2"></i>Carga máxima</span>` : ""}<span><i class="lg lgpr"></i>Recorde</span></div>`;
}
async function viewEvolucao(id) {
  const h = await api(`/api/history/${encodeURIComponent(id)}`);
  const sm = h.summary;
  if (!sm.sessions) { view.innerHTML = `<div class="card"><a href="#analise" class="small">← Análise</a><h2>${esc(h.name)}</h2><p class="empty">Ainda não há treinos registrados com este exercício.</p></div>`; return; }
  const m = METRIC[sm.metric] || METRIC.carga; const st = HIST_STATUS[sm.status] || ["", ""];
  const kpis = sm.metric === "carga"
    ? [["1RM estimado (melhor)", m.fmt(sm.best.e1rm)], ["Carga máxima", fmtKg(sm.best.top_load)], ["Maior volume", `${Math.round(sm.best.volume).toLocaleString("pt-BR")} kg`], ["Desde o início", pctTxt(sm.change_total_pct)]]
    : sm.metric === "reps" ? [["Reps máximas", `${sm.best.max_reps}`], ["Desde o início", pctTxt(sm.change_total_pct)], ["30 dias", pctTxt(sm.change_30d_pct)], ["Treinos", sm.sessions]]
    : [["Tempo máximo", fmtSec(sm.best.max_duration_s)], ["Desde o início", pctTxt(sm.change_total_pct)], ["30 dias", pctTxt(sm.change_30d_pct)], ["Treinos", sm.sessions]];
  const rows = [...h.points].reverse();
  view.innerHTML = `<div class="card"><a href="#analise" class="small">← Análise</a>
    <h2>${esc(h.name)}</h2><p class="small muted">${esc(h.primary_pt.join(", "))}</p>
    <p><span class="pill ${st[1]}">${st[0]}</span> <span class="small">${esc(sm.message)}</span></p>
    <div class="grid2 kpis">${kpis.map(([k, v]) => `<div><div class="kpi">${esc(v)}</div><div class="small muted">${esc(k)}</div></div>`).join("")}</div></div>
  ${sm.bodyweight ? `<p class="small muted card">O 1RM estimado soma o peso do corpo (${fmtKg(sm.bodyweight)}, do Perfil) com a anilha — assim barra só com o corpo e barra com peso entram na mesma curva.</p>` : ""}
  <div class="card"><h3>${esc(m.label)} ao longo do tempo</h3>${historyChart(h.points.filter((p) => !p.suspect), sm.metric)}</div>
  <div class="card"><h3>Treinos</h3><div class="table-wrap"><table class="hist-table"><thead><tr><th>Data</th><th>Melhor série</th><th>${sm.metric === "carga" ? "1RM est." : "Máx."}</th><th>Volume</th><th>Séries</th></tr></thead><tbody>
    ${rows.map((p) => `<tr${p.prs.length ? ' class="pr-row"' : p.suspect ? ' class="suspect-row"' : ""}><td>${ddmm(p.date)}${p.prs.length ? ` <span title="Recorde: ${p.prs.map((k) => PR_TXT[k]).join(", ")}">🏆</span>` : ""}${p.suspect ? (p.suspect_why === "sem_carga" ? ' <span title="Sem carga registrada (o relógio grava 0 kg quando o peso não é digitado). Não entra em recordes nem na tendência.">⚠</span>' : ' <span title="Fora da curva: parece erro de digitação ou exercício trocado pelo relógio. Não entra em recordes nem na tendência — corrija o treino se for erro.">⚠</span>') : ""}${p.source === "garmin" ? ' <span class="small muted" title="Importado do Garmin">G</span>' : ""}</td>
      <td>${p.best_set ? `${fmtKg(p.best_set.load_kg)} × ${p.best_set.reps}` : sm.metric === "tempo" ? fmtSec(p.max_duration_s) : `${p.max_reps} reps`}</td>
      <td>${esc(m.fmt(p.value))}${p.e1rm_weak ? '<span class="small muted" title="Muitas reps: estimativa menos precisa">*</span>' : ""}</td>
      <td>${p.volume ? Math.round(p.volume).toLocaleString("pt-BR") : "—"}</td>
      <td>${p.sets}</td></tr>`).join("")}
  </tbody></table></div><p class="small muted">🏆 = recorde · G = importado do Garmin${rows.some((p) => p.suspect) ? " · ⚠ = fora da curva ou sem carga registrada, ignorado nos recordes e na tendência" : ""}</p>${rows.some((p) => p.e1rm_weak) ? `<p class="small muted">* série com mais de 12 reps: o 1RM estimado é menos preciso.</p>` : ""}</div>`;
}

/* ------------------------------------------------- RESISTÊNCIA / ESPORTES -- */
const SPORT_ICON = { corrida: "🏃", ciclismo: "🚴", natacao: "🏊", remo: "🚣", caminhada: "🚶", trilha: "🥾", multi: "🏅" };
const STEP_PT = { warmup: "Aquecimento", cooldown: "Desaquecimento", interval: "Tiro", steady: "Contínuo", recovery: "Recuperação", rest: "Descanso" };
const UNIT_TXT = { km: "km", h: "h", m: "m" };
function fmtS(sec) { sec = Math.round(sec || 0); const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), x = sec % 60; return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`; }
function parseDur(v) {
  if (v === null || v === undefined || v === "") return null;
  const parts = String(v).trim().replace(",", ".").split(":").map(Number);
  if (parts.some((x) => Number.isNaN(x))) return null;
  if (parts.length === 1) return parts[0] * 60; // só número = minutos
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  return parts[0] * 3600 + parts[1] * 60 + parts[2];
}
function distTxt(m, sport) { if (!m) return ""; return ["natacao", "remo"].includes(sport) ? `${Math.round(m)} m` : `${(m / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} km`; }
function endSummary(en) { return [distTxt(en.dist_m, en.sport), en.dur_s ? `~${fmtS(en.dur_s)}` : ""].filter(Boolean).join(" · "); }
function stepTxt(st, sport) {
  if (st.kind === "repeat") return `${st.n}× [ ${st.steps.map((x) => stepTxt(x, sport)).join(" | ")} ]`;
  const amount = st.dist_m && !(st.kind === "recovery" && sport !== "natacao") && sport !== "ciclismo" && sport !== "trilha" ? distTxt(st.dist_m, sport) : fmtS(st.dur_s);
  const tgt = st.target && st.target.label ? ` · ${st.target.label}` : "";
  return `${STEP_PT[st.kind] || st.kind} ${amount}${tgt}${st.note ? ` (${st.note})` : ""}`;
}
function endStepsHtml(en, open) {
  if (!en.steps) return "";
  return `<details ${open ? "open" : ""}><summary class="small">Passos e alvos</summary><ol class="small steps">${en.steps.map((st) => `<li>${esc(stepTxt(st, en.sport))}</li>`).join("")}</ol></details>`;
}
function paceTxt(it) {
  const en = it.endurance || {}; const secs = parseDur(it.time_str); const d = +it.dist_in;
  if (!secs || !d) return "";
  if (en.sport === "natacao") return `Ritmo: ${fmtS(secs / (d / 100))}/100 m`;
  if (en.sport === "remo") return `Split: ${fmtS(secs / (d / 500))}/500 m`;
  if (["ciclismo"].includes(en.sport)) return `Velocidade: ${(d / (secs / 3600)).toFixed(1)} km/h`;
  return `Ritmo: ${fmtS(secs / d)}/km`;
}
function endPayload(it) {
  const en = it.endurance; const meters = ["natacao", "remo"].includes(en.sport);
  const secs = parseDur(it.time_str) || en.dur_s;
  const out = { exercise_id: it.exercise_id, sport: en.sport, duration_min: Math.round((secs / 60) * 10) / 10, zone: en.zone };
  const d = it.dist_in !== null && it.dist_in !== undefined && it.dist_in !== "" ? +it.dist_in : (en.dist_m ? (meters ? en.dist_m : en.dist_m / 1000) : null);
  if (d) out.distance_km = meters ? d / 1000 : d;
  if (it.avg_hr) out.avg_hr = it.avg_hr;
  if (it.avg_power) out.avg_power = it.avg_power;
  return out;
}
function readinessCard(rd, hasToday = true, adjusted = false) {
  if (!rd) return "";
  if (rd.score === null || rd.score === undefined) {
    return state.garmin && state.garmin.connected ? `<div class="card flat small row between gap"><span>Prontidão de hoje: sem dados do relógio ainda.</span><button class="btn sm ghost" id="rd-refresh">Buscar no Garmin</button></div>` : "";
  }
  const cls = { seguir: "ok", moderar: "warn", trocar: "warn", descanso: "bad" }[rd.action] || "";
  return `<div class="card readiness ${cls}"><div class="row between gap"><h2 style="margin:0">Prontidão de hoje</h2><span class="kpi">${rd.score}</span></div>
    <p><span class="pill ${cls}">${esc({ alta: "Alta", moderada: "Moderada", baixa: "Baixa", muito_baixa: "Muito baixa" }[rd.level] || rd.level)}</span> ${esc(rd.text)}</p>
    <p class="small muted">${esc((rd.reasons || []).join(" · "))}${rd.sleep_h ? ` · ${rd.sleep_h} h de sono` : ""}${rd.rhr ? ` · FC repouso ${rd.rhr}` : ""}</p>
    <p class="small"><a href="#saude">😴 ${rd.night ? `Noite: ${esc(rd.night.text)} — ` : ""}ver saúde e sono ›</a></p>
    <div class="row gap wrap">${rd.action !== "seguir" && hasToday && !adjusted ? `<button class="btn sm" id="rd-apply">Ajustar o treino de hoje</button>` : ""}${adjusted ? `<span class="pill ok">Treino de hoje já ajustado</span>` : ""}<button class="btn sm ghost" id="rd-refresh">Atualizar</button></div></div>`;
}
function loadChart(series) {
  if (!series || series.length < 7) return "";
  const W = 340, H = 170, L = 30, R = 8, T = 10, B = 22;
  const vals = series.flatMap((p) => [p.ctl, p.atl, p.tsb]);
  const mn = Math.min(0, ...vals), mx = Math.max(10, ...vals);
  const X = (i) => L + (i / (series.length - 1)) * (W - L - R);
  const Y = (v) => T + (1 - (v - mn) / (mx - mn)) * (H - T - B);
  const path = (k) => series.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p[k]).toFixed(1)}`).join(" ");
  return `<svg class="hist-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Condicionamento, fadiga e forma nos últimos ${series.length} dias">
    <line x1="${L}" x2="${W - R}" y1="${Y(0)}" y2="${Y(0)}" class="grid"/>
    ${[mn, mx].map((t) => `<text x="${L - 4}" y="${Y(t) + 3}" text-anchor="end" class="axis">${Math.round(t)}</text>`).join("")}
    <text x="${L}" y="${H - 6}" class="axis">${ddmm(series[0].date)}</text><text x="${W - R}" y="${H - 6}" text-anchor="end" class="axis">${ddmm(series[series.length - 1].date)}</text>
    <path d="${path("tsb")}" class="line-tsb"/><path d="${path("atl")}" class="line2"/><path d="${path("ctl")}" class="line1"/></svg>
    <div class="row gap small muted"><span><i class="lg lg1"></i>Condicionamento</span><span><i class="lg lg2"></i>Fadiga</span><span><i class="lg lgtsb"></i>Forma</span></div>`;
}
function loadCard(ld) {
  if (!ld) return "";
  const st = { risco: "bad", subida_rapida: "warn", produtivo: "ok", equilibrado: "ok", descansado: "", inicio: "muted" }[ld.status] || "";
  const by = Object.entries(ld.week.by_sport || {});
  return `<div class="card"><h2>Carga de treino (todas as modalidades)</h2>
    <div class="grid2 kpis"><div><div class="kpi">${Math.round(ld.ctl)}</div><div class="small muted">condicionamento (42 dias)</div></div>
      <div><div class="kpi">${Math.round(ld.atl)}</div><div class="small muted">fadiga (7 dias)</div></div>
      <div><div class="kpi">${ld.form > 0 ? "+" : ""}${Math.round(ld.form)}</div><div class="small muted">forma</div></div>
      <div><div class="kpi">${ld.ramp_week > 0 ? "+" : ""}${(+ld.ramp_week).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}</div><div class="small muted">variação em 7 dias</div></div></div>
    <p><span class="pill ${st}">${esc({ risco: "Risco", subida_rapida: "Subida rápida", produtivo: "Produtivo", equilibrado: "Equilibrado", descansado: "Descansado", inicio: "Começando" }[ld.status] || ld.status)}</span> <span class="small">${esc(ld.message)}</span></p>
    ${loadChart(ld.series)}
    ${by.length ? `<p class="small">Últimos 7 dias: ${by.map(([k, v]) => `${SPORT_ICON[k] || (k === "musculacao" ? "🏋️" : "❤️")} ${Math.round(v.tss)}${v.km ? ` (${v.km.toLocaleString("pt-BR")} km)` : ""}`).join(" · ")}</p>` : ""}
    <p class="small muted">Cada treino vira uma carga (100 = 1 h no limiar), por potência, ritmo, FC ou RPE da musculação. <a href="#esportes">Esportes e zonas</a></p></div>`;
}

async function viewEsportes() {
  const [opts, ov] = await Promise.all([api("/api/endurance/options"), api("/api/endurance/overview")]);
  const cfg = ov.config || {}; const sc = cfg.sports || {}; const ev = cfg.event || {};
  const wd = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];
  const sportBox = (sp) => {
    const c = sc[sp.id] || {}; const g = c.goal || {}; const unit = UNIT_TXT[sp.unit];
    const recent = ov.recent && ov.recent[sp.id];
    return `<details class="sport-box" ${c.enabled ? "open" : ""}><summary><label class="row gap" style="margin:0;display:inline-flex"><input type="checkbox" data-sp="${sp.id}" data-k="enabled" ${c.enabled ? "checked" : ""}> ${SPORT_ICON[sp.id]} <b>${esc(sp.name)}</b></label></summary>
      <div class="grid2">
        <div class="field"><label>Onde</label><select data-sp="${sp.id}" data-k="env">${sp.envs.map((e) => `<option value="${e.id}" ${c.env === e.id ? "selected" : ""}>${esc(e.name)}</option>`).join("")}</select></div>
        <div class="field"><label>Sessões por semana</label><input type="number" min="1" max="7" data-sp="${sp.id}" data-k="sessions" value="${c.sessions || sp.sessions}"></div>
        <div class="field"><label>Volume atual (${unit}/semana)</label><input type="number" step="any" min="0" data-sp="${sp.id}" data-k="current_weekly" value="${c.current_weekly ?? ""}" placeholder="${recent ? `Garmin: ${(+recent).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}` : "pelo nível"}"></div>
        <div class="field"><label>Dia do longo</label><select data-sp="${sp.id}" data-k="long_day"><option value="">automático</option>${wd.map((w, i) => `<option value="${i}" ${String(c.long_day) === String(i) ? "selected" : ""}>${w}</option>`).join("")}</select></div>
        ${sp.id === "natacao" ? `<div class="field"><label>Piscina (m)</label><select data-sp="${sp.id}" data-k="pool_m">${[25, 50, 20, 33].map((x) => `<option ${+(c.pool_m || 25) === x ? "selected" : ""}>${x}</option>`).join("")}</select></div>` : ""}
        <div class="field"><label>Meta</label><select data-sp="${sp.id}" data-k="goal_type">
          <option value="" ${!g.type ? "selected" : ""}>Sem meta (manter)</option><option value="volume" ${g.type === "volume" ? "selected" : ""}>Volume semanal</option>
          ${sp.race_distances.length ? `<option value="prova" ${g.type === "prova" ? "selected" : ""}>Prova (distância × tempo)</option>` : ""}</select></div>
        <div class="field goal-volume"><label>Meta de volume (${unit}/semana)</label><input type="number" step="any" data-sp="${sp.id}" data-k="weekly" value="${g.weekly ?? ""}"></div>
        <div class="field goal-race"><label>Distância da prova (${sp.id === "natacao" || sp.id === "remo" ? "m" : "km"})</label><input type="number" step="any" list="dl-${sp.id}" data-sp="${sp.id}" data-k="distance" value="${g.distance ?? ""}"><datalist id="dl-${sp.id}">${sp.race_distances.map((d) => `<option value="${d}">`).join("")}</datalist></div>
        <div class="field goal-race"><label>Tempo alvo (h:mm:ss)</label><input data-sp="${sp.id}" data-k="target" value="${esc(g.target ?? "")}" placeholder="ex.: 48:00"></div>
        <div class="field goal-race"><label>Data da prova</label><input type="date" data-sp="${sp.id}" data-k="date" value="${esc(g.date ?? "")}"></div>
      </div></details>`;
  };
  const pr = ov.predictions || {}; const prs = pr.sports || {};
  const anchorsHtml = ov.anchors.map((a) => `<li class="row between gap"><span><b>${esc(a.label)}</b>: ${esc(a.text)}${a.date ? ` <span class="small muted">(${esc(a.source || "")}, ${ddmm(a.date)})</span>` : ""}</span></li>`).join("");
  const sugHtml = (ov.suggestions || []).map((x, i) => `<li class="row between gap"><span><b>${esc(x.label)}</b>: ${esc(x.current_txt)} → <b>${esc(x.value_txt)}</b><br><span class="small muted">${esc(x.source)}</span></span><button class="btn sm" data-sug="${i}">Aplicar</button></li>`).join("");
  const zonesHtml = Object.entries(ov.zones || {}).map(([sp, zs]) => `<details><summary>${SPORT_ICON[sp]} Zonas – ${esc(opts.sports.find((x) => x.id === sp).name)}</summary>
      <table class="hist-table"><tbody>${zs.map((z) => `<tr><td>Z${z.zone}</td><td>${esc(z.name)}</td><td>${esc(z.label)}</td></tr>`).join("")}</tbody></table></details>`).join("");
  const goalTxt = (gx) => gx ? `Meta ${esc(gx.target || "—")} · previsão hoje <b>${esc(gx.predicted)}</b> — ${gx.status === "no_alvo" ? "já no alvo ✅" : gx.status === "possivel" ? "alcançável até a prova 👍" : `ambicioso: realista ~${esc(gx.realistic || "")}`}` : "";
  const evp = pr.event;
  view.innerHTML = `
  <div class="card"><h2>Esportes de resistência</h2><p class="small muted">Escolha as modalidades e metas: o gerador distribui junto com a musculação (longo no fim de semana, sessões fortes longe do longo e de perna pesada, ~80% leve).</p>
    <div class="stack" id="sp-list">${opts.sports.map(sportBox).join("")}</div></div>
  <div class="card"><h2>Prova multiesporte</h2><div class="grid2">
    <div class="field"><label>Evento</label><select id="ev-type"><option value="">Nenhum</option>${opts.events.map((e) => `<option value="${e.id}" ${ev.type === e.id ? "selected" : ""}>${esc(e.name)}</option>`).join("")}</select></div>
    <div class="field"><label>Data</label><input type="date" id="ev-date" value="${esc(ev.date || "")}"></div>
    <div class="field"><label>Tempo alvo (h:mm:ss)</label><input id="ev-target" value="${esc(ev.target || "")}" placeholder="ex.: 2:45:00"></div></div>
    <p class="small muted">O evento define as metas de natação, bike e corrida, a fase de cada semana e o brick (corrida logo após o pedal).</p></div>
  <div class="card"><h2>Opções</h2>
    <label class="row gap"><input type="checkbox" id="opt-tests" ${cfg.tests !== false ? "checked" : ""}> Agendar testes para calibrar as zonas (a cada ~8 semanas)</label>
    <label class="row gap"><input type="checkbox" id="opt-ready" ${cfg.readiness_auto ? "checked" : ""}> Ajustar o treino do dia sozinho pela prontidão do relógio (dá para desfazer)</label>
    <div class="row gap wrap" style="margin-top:8px"><button class="btn" id="end-save">Salvar</button><button class="btn ghost" id="end-gen">Salvar e regerar o treino</button></div></div>
  <div class="card"><h2>Referências de intensidade</h2><ul class="list small">${anchorsHtml}</ul>
    ${sugHtml ? `<h3>Sugestões de atualização</h3><ul class="list small">${sugHtml}</ul>` : ""}
    <details><summary>Registrar teste / valor</summary><div class="stack small">
      <div class="row gap wrap"><b>🏃 Corrida</b><input id="t-run-d" type="number" step="any" placeholder="km (ex. 5)" style="width:90px"><input id="t-run-t" placeholder="tempo 24:30" style="width:110px"><button class="btn sm" data-test="corrida">Calcular</button></div>
      <div class="row gap wrap"><b>🚴 Ciclismo</b><input id="t-bike-p" type="number" placeholder="média 20 min (W)" style="width:150px"><span>ou FTP</span><input id="t-bike-f" type="number" placeholder="W" style="width:80px"><button class="btn sm" data-test="ciclismo">Salvar</button></div>
      <div class="row gap wrap"><b>🏊 Natação</b><input id="t-sw-4" placeholder="400 m 7:20" style="width:110px"><input id="t-sw-2" placeholder="200 m 3:25" style="width:110px"><button class="btn sm" data-test="natacao">Calcular CSS</button></div>
      <div class="row gap wrap"><b>🚣 Remo</b><input id="t-row" placeholder="2 km 7:40" style="width:110px"><button class="btn sm" data-test="remo">Salvar</button></div>
      <div class="row gap wrap"><b>❤️ FC</b><input id="t-lthr" type="number" placeholder="limiar" style="width:90px"><button class="btn sm" data-test="lthr">Salvar</button><input id="t-hrmax" type="number" placeholder="máxima" style="width:90px"><button class="btn sm" data-test="hrmax">Salvar</button></div>
    </div></details>
    ${zonesHtml}</div>
  <div class="card"><h2>Previsões</h2>
    ${prs.corrida ? `<p>🏃 VDOT ${prs.corrida.vdot}: ${Object.entries(prs.corrida.times).map(([d, t]) => `${d} km ${esc(t)}`).join(" · ")}${prs.corrida.goal ? `<br><span class="small">${goalTxt(prs.corrida.goal)}</span>` : ""}</p>` : ""}
    ${prs.natacao ? `<p>🏊 CSS ${esc(prs.natacao.css)}: ${Object.entries(prs.natacao.times).map(([d, t]) => `${d} m ${esc(t)}`).join(" · ")}</p>` : ""}
    ${prs.ciclismo ? `<p>🚴 FTP ${prs.ciclismo.ftp} W (${prs.ciclismo.w_kg} W/kg): ${Object.entries(prs.ciclismo.speed_kmh).map(([k, v]) => `${esc(k)} ${v} km/h`).join(" · ")}</p>` : ""}
    ${prs.remo ? `<p>🚣 2 km ${esc(prs.remo["2k"])} (${esc(prs.remo.split)})</p>` : ""}
    ${evp ? `<p>🏅 <b>${esc(evp.name)}</b>${evp.date ? ` em ${ddmm(evp.date)}` : ""}: ${evp.predicted ? `previsão <b>${esc(evp.predicted)}</b>${evp.target ? ` (alvo ${esc(evp.target)}${evp.gap_s !== null ? `, ${evp.gap_s > 0 ? "+" : ""}${fmtS(Math.abs(evp.gap_s))}${evp.gap_s > 0 ? " acima" : " abaixo"}` : ""})` : ""}` : `faltam referências: ${esc(evp.missing_anchors.join(", "))}`}
      <br><span class="small muted">${evp.legs.map((l) => `${SPORT_ICON[l.sport]} ${l.distance}${l.sport === "natacao" ? " m" : " km"} ${esc(l.time || "—")}`).join(" · ")} · transições ${esc(evp.transitions)}</span></p>` : ""}
    ${!Object.keys(prs).length && !evp ? `<p class="empty">Registre um teste ou sincronize o Garmin para ver previsões.</p>` : ""}</div>`;
  const toggleGoals = () => $$(".sport-box").forEach((box) => {
    const t = $("[data-k=goal_type]", box).value;
    $$(".goal-volume", box).forEach((x) => { x.hidden = t !== "volume"; });
    $$(".goal-race", box).forEach((x) => { x.hidden = t !== "prova"; });
  });
  toggleGoals();
  const read = () => {
    const sportsOut = {};
    opts.sports.forEach((sp) => {
      const v = (k) => { const el = $(`[data-sp="${sp.id}"][data-k="${k}"]`); return el ? (el.type === "checkbox" ? el.checked : el.value) : undefined; };
      const gt = v("goal_type");
      const num = (x) => (x === "" || x === undefined ? null : +String(x).replace(",", "."));
      const goal = gt === "volume" ? { type: "volume", weekly: num(v("weekly")) } : gt === "prova" ? { type: "prova", distance: num(v("distance")), target: v("target") || null, date: v("date") || null } : {};
      sportsOut[sp.id] = { enabled: v("enabled"), env: v("env"), sessions: num(v("sessions")), current_weekly: num(v("current_weekly")),
        long_day: v("long_day") === "" ? null : +v("long_day"), goal, ...(sp.id === "natacao" ? { pool_m: num(v("pool_m")) } : {}) };
    });
    const et = $("#ev-type").value;
    return { sports: sportsOut, event: et ? { type: et, date: $("#ev-date").value || null, target: $("#ev-target").value || null } : null,
      tests: $("#opt-tests").checked, readiness_auto: $("#opt-ready").checked };
  };
  view.onchange = (e) => { if (e.target.dataset.k === "goal_type") toggleGoals(); };
  view.onclick = async (e) => {
    const t = e.target.closest("button"); if (!t) return;
    try {
      if (t.id === "end-save" || t.id === "end-gen") {
        const body = read();
        if (body.event && !body.event.date) return toast("Informe a data do evento");
        await api("/api/endurance", { method: "PUT", body });
        state.profile = await api("/api/profile");
        toast("Esportes salvos");
        if (t.id === "end-gen") return openGenerator();
        return;
      }
      if (t.dataset.sug !== undefined) {
        const x = ov.suggestions[+t.dataset.sug];
        await api("/api/anchors", { method: "POST", body: { key: x.key, value: x.value, source: x.source } });
        toast(`${x.label} atualizado: ${x.value_txt}`); return viewEsportes();
      }
      if (t.dataset.test) {
        const k = t.dataset.test; let data = {};
        if (k === "corrida") data = { distance_km: +$("#t-run-d").value || 5, time: $("#t-run-t").value };
        if (k === "ciclismo") data = $("#t-bike-f").value ? { ftp: +$("#t-bike-f").value } : { power_20min: +$("#t-bike-p").value };
        if (k === "natacao") data = { t400: $("#t-sw-4").value, t200: $("#t-sw-2").value };
        if (k === "remo") data = { t2k: $("#t-row").value };
        if (k === "lthr") data = { value: +$("#t-lthr").value };
        if (k === "hrmax") data = { value: +$("#t-hrmax").value };
        const r = await api("/api/anchors/test", { method: "POST", body: { sport: k, data } });
        toast(`Referência salva: ${r.text}`); return viewEsportes();
      }
    } catch (err) { toast(err.message, 5000); }
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
    const list = $("#ex-list"); if (!list) return;  // saiu da tela antes da resposta chegar (4G lento)
    list.classList.add("ex-grid");
    list.innerHTML = res.map((ex) => `<li class="tile" data-ex="${esc(ex.id)}" title="${esc(ex.name)}">
      ${mediaHtml(ex.id, "tile")}${ex.limitation && ex.limitation.status !== "ok" ? `<span class="tile-lim ${ex.limitation.status}" data-ex="${esc(ex.id)}">${ex.limitation.status === "evitar" ? "evitar" : "cuidado"}</span>` : ""}
      <span class="tile-cap" data-ex="${esc(ex.id)}"><b data-ex="${esc(ex.id)}">${esc(ex.name)}</b><span data-ex="${esc(ex.id)}">${esc(ex.primary_pt.slice(0, 2).join(", "))}</span></span></li>`).join("") || `<li class="empty">Nada encontrado.</li>`;
  };
  view.oninput = () => { clearTimeout(t); t = setTimeout(load, 200); };
  view.onchange = view.oninput;
  view.onclick = (e) => { const li = e.target.closest("[data-ex]"); if (li) showExercise(li.dataset.ex); };
  load();
}

/* --------------------------------------------------------------- SAÚDE -- */
const LEVEL_PILL = { ok: ["bom", "ok"], atencao: ["atenção", "warn"], alerta: ["alerta", "bad"] };
const NIGHT_PILL = { bom: "ok", regular: "warn", ruim: "bad", sem_dados: "muted" };
const fmtH = (h) => { if (h == null) return "—"; const t = Math.round(h * 60); return `${Math.floor(t / 60)}h${String(t % 60).padStart(2, "0")}`; };
const nf = (v, d = 0) => (v == null ? "—" : (+v).toLocaleString("pt-BR", { maximumFractionDigits: d }));
const STATUS_TXT = { PRODUCTIVE: "Produtivo", MAINTAINING: "Mantendo", RECOVERY: "Recuperação", PEAKING: "No pico", OVERREACHING: "Sobrecarga",
  UNPRODUCTIVE: "Improdutivo", STRAINED: "Esgotado", DETRAINING: "Perdendo forma", NO: "Sem status" };

function stagesBar(sl) {
  const segs = [["deep_pct", "Profundo", "#3b5bdb"], ["light_pct", "Leve", "#74c0fc"], ["rem_pct", "REM", "#b197fc"]];
  const on = segs.filter((x) => sl[x[0]] != null);
  if (!on.length) return "";
  return `<div class="tbar" style="height:14px">${on.map((x) => `<span style="width:${sl[x[0]]}%;background:${x[2]}" title="${x[1]} ${nf(sl[x[0]])}%"></span>`).join("")}</div>
    <div class="small muted">${on.map((x) => `<span class="dot" style="background:${x[2]}"></span>${x[1]} ${nf(sl[x[0]])}%`).join(" ")}${sl.awake_min != null ? ` · acordado ${sl.awake_min} min` : ""}</div>`;
}

function healthChart(series, key, { label, unit = "", band = null, ref = null, bars = false, color = "var(--brand)" } = {}) {
  const pts = series.map((p, i) => [i, p[key]]).filter((p) => p[1] != null);
  if (pts.length < 3) return "";
  const W = 340, H = 120, L = 34, R = 8, T = 10, B = 20;
  const vals = pts.map((p) => p[1]).concat(band || []).concat(ref != null ? [ref] : []);
  const lo = bars ? 0 : Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.12 || 1;
  const mn = bars ? 0 : lo - pad, mx = hi + pad;
  const X = (i) => L + (i / Math.max(1, series.length - 1)) * (W - L - R);
  const Y = (v) => T + (1 - (v - mn) / (mx - mn)) * (H - T - B);
  const bw = Math.max(3, (W - L - R) / series.length - 3);
  return `<div class="small" style="margin-top:8px"><b>${esc(label)}</b></div>
  <svg class="hist-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)} nos últimos ${series.length} dias">
    ${band ? `<rect x="${L}" width="${W - L - R}" y="${Y(band[1])}" height="${Math.max(1, Y(band[0]) - Y(band[1]))}" fill="var(--ok-bg)"/>` : ""}
    ${ref != null ? `<line x1="${L}" x2="${W - R}" y1="${Y(ref)}" y2="${Y(ref)}" class="grid" stroke-dasharray="4 3"/>` : ""}
    ${bars ? pts.map(([i, v]) => `<rect x="${(X(i) - bw / 2).toFixed(1)}" y="${Y(v).toFixed(1)}" width="${bw.toFixed(1)}" height="${(Y(0) - Y(v)).toFixed(1)}" rx="2" fill="${ref != null && v < ref ? "var(--warn)" : color}"/>`).join("")
    : `<path d="${pts.map(([i, v], k) => `${k ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ")}" class="line1" style="stroke:${color}"/>`}
    <text x="${L - 4}" y="${Y(hi) + 4}" text-anchor="end" class="axis">${nf(hi, 1)}</text>
    <text x="${L - 4}" y="${Y(lo) + 4}" text-anchor="end" class="axis">${nf(lo, 1)}</text>
    <text x="${L}" y="${H - 4}" class="axis">${ddmm(series[0].date)}</text><text x="${W - R}" y="${H - 4}" text-anchor="end" class="axis">${ddmm(series[series.length - 1].date)}</text>
  </svg>${unit ? `<div class="small muted">${esc(unit)}</div>` : ""}`;
}

async function viewSaude() {
  const h = await api("/api/health");
  const head = `<div class="card"><div class="row between gap wrap"><h2 style="margin:0">Saúde e sono</h2>
      ${h.garmin.connected ? `<button class="btn sm ghost" id="h-refresh">Atualizar do relógio</button>` : ""}</div>
    <p class="small muted">Sono, recuperação e condicionamento lidos do seu relógio Garmin — e como o treino está mexendo com eles.
      Não substitui avaliação médica: sintomas ou valores fora do normal por vários dias, procure um médico.</p></div>`;
  if (!h.has_data) {
    view.innerHTML = head + `<div class="card"><p>${h.garmin.connected
      ? "Ainda sem dados de saúde do relógio. Toque em <b>Atualizar do relógio</b> — o app busca as últimas semanas aos poucos."
      : "Conecte o Garmin em <a href=\"#perfil\">Perfil → Garmin</a>. Com o relógio no pulso também à noite, o app passa a mostrar sono, HRV, FC de repouso, estresse e VO2máx."}</p>
      <p class="small muted">HRV, Training Readiness e idade física dependem do modelo do relógio.</p></div>`;
  } else {
    const n = h.last_night;
    const wk = h.weekly || {};
    const extra = n ? [n.hrv != null ? `HRV ${nf(n.hrv)} ms` : "", n.rhr != null ? `FC de repouso ${nf(n.rhr)} bpm` : "",
      n.resp != null ? `respiração ${nf(n.resp, 1)}/min` : "", n.spo2 != null ? `SpO₂ ${nf(n.spo2)}%` : ""].filter(Boolean).join(" · ") : "";
    view.innerHTML = head + `
    ${(h.alerts || []).map((a) => `<div class="rec alerta">⚠ ${esc(a)}</div>`).join("")}
    ${n ? `<div class="card"><div class="row between gap"><h2 style="margin:0">Noite de ${ddmm(n.date)}</h2>
        <span class="kpi">${n.score != null ? nf(n.score) : fmtH(n.duration_h)}</span></div>
      <p><span class="pill ${NIGHT_PILL[n.level] || "muted"}">${esc({ bom: "Sono bom", regular: "Sono regular", ruim: "Sono ruim", sem_dados: "Sem dados" }[n.level] || n.level)}</span>
        ${fmtH(n.duration_h)} dormindo${n.start ? ` · ${esc(n.start)}${n.end ? `–${esc(n.end)}` : ""}` : ""} <span class="muted small">(ideal ${nf(h.need[0])}–${nf(h.need[1])} h)</span></p>
      ${stagesBar(n)}
      <ul class="small plain">${(n.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
      ${extra ? `<p class="small muted">${extra}</p>` : ""}
      ${(n.tips || []).map((t) => `<p class="small rec">${esc(t)}</p>`).join("")}</div>` : ""}
    ${h.areas.length ? `<div class="card"><h2>Como você está</h2><div class="stack">
      ${h.areas.map((a) => `<div class="area"><div class="row between gap"><b>${esc(a.title)}</b><span class="pill ${LEVEL_PILL[a.level][1]}">${LEVEL_PILL[a.level][0]}</span></div>
        <div class="small">${esc(a.text)}</div>${a.tip ? `<div class="small muted">${esc(a.tip)}</div>` : ""}</div>`).join("")}
    </div></div>` : ""}
    <div class="card"><h2>Efeitos do seu treino</h2>${(h.effects || []).length ? `<ul class="small">${h.effects.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : `<p class="small muted">Sem dados suficientes ainda.</p>`}
      <p class="small muted">Compara as noites depois de dias de treino pesado com as noites depois de dias leves ou de folga (últimas 8 semanas).</p></div>
    <div class="card"><h2>Últimas 4 semanas</h2>
      ${healthChart(h.series, "sleep_h", { label: "Horas de sono", bars: true, ref: h.need[0], unit: `Tracejado: mínimo recomendado (${nf(h.need[0])} h). Laranja = noite curta.` })}
      ${healthChart(h.series, "sleep_score", { label: "Nota do sono", ref: 80 })}
      ${healthChart(h.series, "hrv", { label: "HRV noturna (ms)", band: h.hrv && h.hrv.low != null && h.hrv.high != null ? [h.hrv.low, h.hrv.high] : null, unit: h.hrv && h.hrv.low != null ? "Faixa verde: o seu normal. Abaixo dela vários dias seguidos = recuperação incompleta." : "" })}
      ${healthChart(h.series, "rhr", { label: "FC de repouso (bpm)", ref: h.rhr ? h.rhr.base : null, color: "var(--bad)", unit: "Subir 5+ bpm acima da sua base (tracejado) costuma indicar fadiga, estresse ou início de doença." })}
      ${healthChart(h.series, "stress", { label: "Estresse médio (0–100)", color: "var(--warn)" })}
    </div>
    ${Object.keys(wk).length ? `<div class="card"><h2>Indicadores do relógio</h2><div class="grid2">
      ${wk.vo2max ? `<div><div class="kpi">${nf(wk.vo2max, 1)}</div><div class="small muted">VO2máx${wk.vo2max_bike ? ` (bike ${nf(wk.vo2max_bike, 1)})` : ""}</div></div>` : ""}
      ${wk.fitness_age ? `<div><div class="kpi">${nf(wk.fitness_age, 1)}</div><div class="small muted">idade física${wk.chron_age ? ` (real ${nf(wk.chron_age)})` : ""}</div></div>` : ""}
      ${wk.training_status ? `<div><div class="kpi" style="font-size:18px">${esc(STATUS_TXT[wk.training_status] || wk.training_status)}</div><div class="small muted">status de treino</div></div>` : ""}
      ${wk.weight_kg ? `<div><div class="kpi">${nf(wk.weight_kg, 1)} kg</div><div class="small muted">peso${wk.body_fat ? ` · ${nf(wk.body_fat, 1)}% gordura` : ""}</div></div>` : ""}
    </div></div>` : ""}`;
  }
  const rb = $("#h-refresh");
  if (rb) rb.onclick = async () => { rb.disabled = true; try { await api("/api/health/refresh", { method: "POST" }); toast("Dados do relógio atualizados"); viewSaude(); } catch (e) { rb.disabled = false; toast(e.message, 5000); } };
}

/* -------------------------------------------------------------- PERFIL -- */
async function viewPerfil() {
  const p = await api("/api/profile");
  state.profile = p;
  const ul = Object.fromEntries((p.limitations || []).map((l) => [l.key, l]));
  const custom = (p.limitations || []).find((l) => l.key === "personalizado") || {};
  const g = await api("/api/garmin/status");
  const num = (k, label, extra = "") => `<div class="field"><label>${label}</label><input type="number" data-p="${k}" value="${esc(p[k] ?? "")}" ${extra}></div>`;
  state.me = await api("/api/auth/me").catch(() => state.me);
  const accHtml = (await accountCard()) + (await coachMineCard()) + (await devicesCard());
  const canBackup = !(state.me && state.me.logged_in && !state.me.user.is_admin);
  view.innerHTML = `${accHtml}
  <div class="card"><h2>Seus dados</h2>
    <div class="grid2">
      <div class="field"><label>Nome</label><input data-p="name" value="${esc(p.name || "")}"></div>
      ${num("age", `Idade${(p._from_garmin || []).includes("age") ? ' <span class="muted">(do Garmin)</span>' : ""}`)}${num("weight_kg", `Peso (kg)${(p._from_garmin || []).includes("weight_kg") ? ' <span class="muted">(do Garmin)</span>' : ""}`, 'step="0.1"')}${num("height_cm", "Altura (cm)")}
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
  ${canBackup ? `<div class="card"><h2>Backup${state.me && state.me.logged_in ? " (todas as contas)" : " dos seus dados"}</h2>
    <p class="small muted">Perfil, planilhas, treinos registrados e agenda em um arquivo .json.</p>
    <div class="row gap wrap"><a class="btn sm ghost" href="/api/backup">Baixar backup</a>
      <label class="btn sm ghost" style="margin:0">Restaurar backup<input type="file" id="restore" accept=".json,application/json" hidden></label></div>
  </div>` : ""}
  <div class="card"><h2>Esportes de resistência</h2><p class="small">Corrida, ciclismo, natação, remo, caminhada e trilha — com meta de volume, prova com tempo alvo ou triathlon/duathlon; zonas por ritmo, potência ou FC; prontidão diária do relógio.</p>
    <a class="btn sm" href="#esportes">Configurar esportes</a></div>
  <div class="card"><h2>Garmin Connect</h2>
    ${!g.library_installed ? `<p class="pill bad">Biblioteca garminconnect não instalada no servidor: <code>pip install "garminconnect>=0.3.20"</code></p>` : ""}
    ${g.storage_error ? `<p class="pill bad">${esc(g.storage_error)}</p>` : ""}
    ${g.connected ? `<p><span class="pill ok">conectado</span> ${esc(g.display_name || "")}</p>
      ${syncInfo(g.sync)}
      <div class="row gap wrap"><button class="btn sm" id="g-sync">Sincronizar agora</button><button class="btn sm ghost" id="g-acts">Escolher treinos para importar</button><button class="btn sm danger" id="g-logout">Desconectar</button></div><div id="g-acts-box"></div>
      <p class="small muted">Sincroniza sozinho todo dia à noite e quando você abre o app (se a última tiver mais de 6 h): traz musculação com séries/reps/carga e cardio de academia.</p>`
      : `${g.needs_reconnect ? `<div class="rec atencao small"><b>Reconecte o Garmin.</b> O login expirou ou foi revogado pela Garmin; a sincronização automática está parada até você entrar de novo.</div>` : ""}
      <p class="small muted">Login feito uma vez: o app guarda só os tokens (criptografados no banco), que se renovam sozinhos. Sua senha não é armazenada.</p>
      <div class="grid2"><div class="field"><label>E-mail Garmin</label><input id="g-email" type="email" autocomplete="username"></div>
      <div class="field"><label>Senha</label><input id="g-pass" type="password" autocomplete="current-password"></div></div>
      <div id="g-mfa" ${g.mfa_pending ? "" : "hidden"} class="field"><label>Código de verificação (e-mail/app)</label><input id="g-code" inputmode="numeric" autocomplete="one-time-code"></div>
      <button class="btn" id="g-login">${g.mfa_pending ? "Confirmar código" : "Conectar"}</button>${g.sync && g.sync.last_error && !g.needs_reconnect ? `<p class="small muted">${esc(g.sync.last_error)}</p>` : ""}`}
    <div class="hr"></div>
    <details><summary class="small">Como funciona o envio para o Garmin?</summary><div class="small">
      <p><b>1. Arquivo .FIT (offline):</b> em cada treino da planilha, “Baixar .FIT”. Conecte o relógio no USB e copie para <code>GARMIN/NewFiles</code>. O relógio importa ao desconectar e mostra em Treinos. Gerado com o SDK oficial da Garmin.</p>
      <p><b>2. Garmin Connect (direto):</b> “Enviar ao Garmin Connect” cria o treino na sua biblioteca (mesmo formato do editor web) e agenda na data escolhida; o relógio recebe na próxima sincronização. Usa a API não-oficial (biblioteca <code>garminconnect</code>), pois a API oficial de treinos é restrita a empresas parceiras.</p>
      <p><b>3. Volta do relógio:</b> a sincronização automática (todo dia à noite, ao abrir o app ou em “Sincronizar agora”) traz séries/reps/carga registradas no relógio para o histórico de cargas e a análise muscular. Corridas e pedais na rua ficam de fora.</p></div></details>
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
  if ($("#restore")) $("#restore").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try {
      const r = await api("/api/backup", { method: "POST", body: JSON.parse(await f.text()) });
      state.meta = null; toast(`Restaurado: ${r.sessions || 0} treinos, ${r.plans || 0} planilhas`); route();
    } catch (err) { toast(err.message, 5000); }
  };
  bindAccount();
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
  const gs = $("#g-sync");
  if (gs) gs.onclick = async () => {
    gs.disabled = true; gs.textContent = "Sincronizando…";
    try { const r = await syncAll(""); toast(syncToast(r) || (r.ok ? "Nada novo no Garmin" : r.error), 5000); viewPerfil(); updateGarminPill(); }
    catch (e) { toast(e.message, 6000); gs.disabled = false; gs.textContent = "Sincronizar agora"; }
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

function syncInfo(sy) {
  if (!sy || !sy.last_run) return `<p class="small muted">Ainda não sincronizou.</p>`;
  const when = new Date(sy.last_run).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  const how = { cron: "automática (noite)", auto: "ao abrir o app", manual: "manual" }[sy.trigger] || "";
  const imp = sy.last_imported || [];
  return `<p class="small">Última sincronização: <b>${esc(when)}</b> ${how ? `<span class="muted">· ${esc(how)}</span>` : ""}
    ${sy.last_ok ? `<span class="pill ok small">ok</span>` : `<span class="pill warn small">falhou</span>`}
    ${imp.length ? `<br>Trouxe: ${imp.map((i) => `${esc(i.name || "atividade")} (${ddmm(i.date)})`).join(", ")}` : ""}
    ${sy.last_error ? `<br><span class="muted">${esc(sy.last_error)}</span>` : ""}</p>`;
}
function syncToast(r) {
  if (!r || !r.imported || !r.imported.length) return "";
  return `Garmin: ${r.imported.length} treino${r.imported.length > 1 ? "s" : ""} importado${r.imported.length > 1 ? "s" : ""} (${r.imported.map((i) => (i.name || "atividade") + (i.merged ? " — juntado ao treino que você registrou" : "")).join(", ")})`;
}
async function updateGarminPill() {
  try {
    const g = await api("/api/garmin/status"); state.garmin = g; const el = $("#garmin-pill");
    const [txt, cls] = g.connected ? ["Garmin: conectado", "ok"] : g.needs_reconnect ? ["Garmin: reconectar", "warn"] : ["Garmin: offline", "muted"];
    el.textContent = txt; el.className = `pill ${cls}`;
    if (g.needs_reconnect) { el.onclick = () => { location.hash = "#perfil"; }; el.style.cursor = "pointer"; }
    return g;
  } catch (_) { return null; }
}
// cada chamada importa até 15 atividades dentro do prazo do servidor: repete enquanto sobrar fila
async function syncAll(qs) {
  let r = await api(`/api/garmin/sync${qs}`, { method: "POST" });
  const imported = [...(r.imported || [])];
  for (let i = 0; i < 4 && r && r.ok && r.pending > 0; i++) {
    r = await api("/api/garmin/sync", { method: "POST" });
    imported.push(...(r.imported || []));
  }
  return { ...r, imported, ran: r.ran !== false };
}
async function autoSync() {
  try {
    const r = await syncAll("?auto=true");
    const msg = syncToast(r);
    if (msg) { toast(`${msg} 🎉`, 6000); if (["analise", "hoje", "evolucao"].includes((location.hash.slice(1) || "hoje").split("/")[0])) route(); }
    if (r && r.ran) updateGarminPill();
  } catch (_) { /* silencioso: o usuário pode sincronizar no Perfil */ }
}

/* --------------------------------------------------------------- router -- */
async function route() {
  const [name, a, b] = (location.hash.slice(1) || "hoje").split("/");
  $$(".tabbar a").forEach((el) => el.classList.toggle("active", el.dataset.tab === ({ plano: "planilhas", evolucao: "analise", saude: "analise", esportes: "perfil" }[name] || name)));
  coachChrome();
  view.oninput = view.onchange = view.onclick = null;
  view.innerHTML = `<p class="muted">Carregando…</p>`;
  try {
    await loadMedia();
    if (!state.meta) {
      [state.meta, state.profile] = await Promise.all([api("/api/meta"), api("/api/profile")]);
      if (state.meta.ephemeral_storage && !$("#eph")) {
        document.querySelector(".topbar").insertAdjacentHTML("afterend", `<div id="eph" class="rec atencao small" style="margin:8px 16px 0">Versão de teste online: os dados podem ser apagados pelo servidor. Use <a href="#perfil">Perfil → Backup</a> para salvar e restaurar.</div>`);
      }
    }
    const routes = { hoje: () => viewHoje(a), planilhas: viewPlanilhas, plano: () => viewPlano(a), treinar: () => viewTreinar(a, b), analise: viewAnalise, evolucao: () => viewEvolucao(decodeURIComponent(a || "")), esportes: viewEsportes, saude: viewSaude, exercicios: viewExercicios, perfil: viewPerfil, coach: viewCoach };
    await (routes[name] || viewHoje)();
    if (!view.onclick) view.onclick = (e) => { const id = e.target.dataset && e.target.dataset.ex; if (id) showExercise(id); };
  } catch (e) {
    view.innerHTML = e.offline
      ? `<div class="card"><h2>Sem internet</h2><p>${esc(e.message)}</p><p class="small muted">Um treino em andamento continua salvo no celular (aba Treinar).</p><button class="btn" id="retry">Tentar de novo</button></div>`
      : `<div class="card"><p class="pill bad">Erro: ${esc(e.message)}</p><p><button class="btn ghost" id="retry">Tentar de novo</button></p></div>`;
    const rb = $("#retry"); if (rb) rb.onclick = route;
  }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
async function flushOutbox() {
  if (flushOutbox.busy) return;  // "online" pode disparar mais de uma vez: um envio por vez
  const box = store("gg_outbox") || []; if (!box.length) return;
  flushOutbox.busy = true;
  try { await sendOutbox(box); } finally { flushOutbox.busy = false; }
}
async function sendOutbox(box) {
  const left = [];
  for (const p of box) { try { await api("/api/sessions", { method: "POST", body: p }); } catch (e) { if (e.offline) left.push(p); } }
  store("gg_outbox", left.length ? left : null);
  const sent = box.length - left.length;
  if (sent) { toast(`${sent} treino${sent > 1 ? "s" : ""} guardado${sent > 1 ? "s" : ""} sem internet enviado${sent > 1 ? "s" : ""} ✔`); if (!location.hash || location.hash.startsWith("#hoje")) route(); }
}
window.addEventListener("online", flushOutbox);
(async () => {
  const conv = location.hash.match(/^#convite\/([\w-]+)/);
  let me = null;
  try { me = await api("/api/auth/me"); } catch (_) { /* offline: segue com o que houver */ }
  state.me = me;
  if (conv && !(me && me.logged_in)) return showAuth("convite", conv[1]);
  if (me && !me.logged_in && !me.setup_needed) return showAuth("login");
  startApp();
})();
function startApp() {
  document.body.classList.remove("auth-mode");
  flushOutbox();
  route();
  updateGarminPill().then((g) => { if (g && g.connected) autoSync(); });
}
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
