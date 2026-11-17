/* Simulação das telas e do uso no celular (Playwright).

   Uso:  node scripts/simulate_ui.js [--shots DIR] [--quick]

   Para cada persona (novo, musculacao, hibrido, extremo — bancos criados por scripts/ui_seed.py)
   sobe o app localmente e percorre todas as telas em vários aparelhos (iPhone SE, iPhone 14,
   Pixel 7, Galaxy Z Fold capa, iPad Mini, iPhone SE deitado), em tema claro e escuro.

   Confere em cada tela:
     • erros de JavaScript, erros no console e requisições com falha;
     • rolagem lateral / elementos saindo da tela;
     • alvos de toque pequenos (< 36 px) e campos com fonte < 16 px (o iPhone dá zoom ao tocar);
     • texto minúsculo (< 11 px) e contraste baixo (< 4,5:1, ou 3:1 em texto grande);
     • conteúdo escondido atrás da barra de abas; tela em branco ou travada em "Carregando…";
     • tempo até renderizar.
   E fluxos de uso: gerar treino (novo usuário), ver treino, mover, "não vou treinar" e desfazer,
   treinar (preencher séries, cronômetro de descanso, salvar), perfil, esportes, busca de
   exercícios, modal em tela pequena, voltar do navegador e uso sem internet (service worker).
*/
"use strict";
const { chromium, devices } = require("playwright");
const { spawn, execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const args = process.argv.slice(2);
const SHOTS = args.includes("--shots") ? args[args.indexOf("--shots") + 1] : null;
const QUICK = args.includes("--quick");
const TMP = fs.mkdtempSync(path.join(require("os").tmpdir(), "gg-ui-"));

const DEVICES = [
  ["iPhone SE", devices["iPhone SE"]],
  ["iPhone 14", devices["iPhone 14"]],
  ["Pixel 7", devices["Pixel 7"]],
  ["Fold capa", devices["Galaxy Z Fold 6 Cover"] || { viewport: { width: 280, height: 653 }, isMobile: true, hasTouch: true, deviceScaleFactor: 3 }],
  ["iPad Mini", devices["iPad Mini"]],
  ["iPhone SE deitado", devices["iPhone SE landscape"]],
].filter((d) => !QUICK || ["iPhone SE", "Pixel 7"].includes(d[0]));
const THEMES = QUICK ? ["light"] : ["light", "dark"];
const PERSONAS = ["novo", "musculacao", "hibrido", "extremo"];

const ISSUES = new Map();
const STATS = { render_ms: [] };
function issue(kind, ex) {
  if (!ISSUES.has(kind)) ISSUES.set(kind, { n: 0, ex: [] });
  const it = ISSUES.get(kind); it.n++;
  const s = typeof ex === "string" ? ex : JSON.stringify(ex);
  if (it.ex.length < 5 && !it.ex.includes(s)) it.ex.push(s);
}

async function startServer(persona, port) {
  const db = path.join(TMP, `${persona}.sqlite3`);
  const env = { ...process.env, GYMGARMIN_DB: db, GYMGARMIN_HOME: TMP, GARMIN_TOKEN_KEY: "ui-sim-key" };
  delete env.DATABASE_URL; delete env.VERCEL;
  execFileSync("python3", [path.join(ROOT, "scripts/ui_seed.py"), persona, db], { env, cwd: ROOT, stdio: "pipe" });
  const p = spawn("python3", ["-m", "uvicorn", "gymgarmin.app:app", "--port", String(port), "--log-level", "warning"], { env, cwd: ROOT, stdio: ["ignore", "pipe", "pipe"] });
  p.stderr.on("data", (d) => { const t = String(d); if (/Traceback|Error/.test(t)) issue("erro no servidor", t.slice(-300)); });
  for (let i = 0; i < 80; i++) {
    try { const r = await fetch(`http://127.0.0.1:${port}/api/meta`); if (r.ok) return p; } catch (_) { /* subindo */ }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("servidor não subiu");
}

// ---------------------------------------------------------------- checagens na página
async function audit(page, where) {
  const r = await page.evaluate(() => {
    const vw = window.innerWidth, vh = window.innerHeight;
    const desc = (el) => {
      let s = el.tagName.toLowerCase();
      if (el.id) s += "#" + el.id;
      else if (el.className && typeof el.className === "string") s += "." + el.className.trim().split(/\s+/).slice(0, 2).join(".");
      const t = (el.textContent || el.value || "").trim().replace(/\s+/g, " ").slice(0, 40);
      return t ? `${s} «${t}»` : s;
    };
    const visible = (el) => {
      const cs = getComputedStyle(el);
      if (cs.visibility === "hidden" || cs.display === "none" || +cs.opacity === 0) return false;
      const rc = el.getBoundingClientRect();
      return rc.width > 0 && rc.height > 0 && !el.closest("[hidden]") && !el.closest("details:not([open]) > :not(summary)");
    };
    const clippedX = (el) => { for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) { if (/(auto|scroll|hidden|clip)/.test(getComputedStyle(p).overflowX)) return true; } return false; };
    const out = { hscroll: document.documentElement.scrollWidth - vw, offscreen: [], small: [], zoom: [], tiny: [], contrast: [], blank: false, loading: false, behindTab: false };
    const all = [...document.querySelectorAll("body *")].filter(visible);
    for (const el of all) {
      const rc = el.getBoundingClientRect();
      if ((rc.right > vw + 1 || rc.left < -1) && !clippedX(el) && !el.closest(".modal[hidden]")) {
        const pr = el.parentElement && el.parentElement.getBoundingClientRect();
        if (!pr || !(pr.right > vw + 1 || pr.left < -1)) out.offscreen.push(`${desc(el)} (${Math.round(rc.left)}…${Math.round(rc.right)} de ${vw})`);
      }
      const tag = el.tagName;
      const interactive = /^(A|BUTTON|SELECT|SUMMARY|INPUT|TEXTAREA)$/.test(tag) || el.matches("label.chip, [data-ex], [role=button]");
      if (interactive && !(tag === "INPUT" && /checkbox|radio|hidden/.test(el.type))) {
        // [data-ex] dentro de uma linha inteira tocável (mesmo data-ex) vale pela linha
        let host = el;
        if (el.dataset && el.dataset.ex) for (let p = el.parentElement; p; p = p.parentElement) if (p.dataset && p.dataset.ex === el.dataset.ex) host = p;
        if (host !== el) { const hr = host.getBoundingClientRect(); if (hr.height >= 36) continue; }
        const inline = (tag === "A" || el.dataset.ex !== undefined) && getComputedStyle(el).display === "inline" && el.closest("p, li, .small");
        if (!inline && (rc.height < 36 || rc.width < 36)) out.small.push(`${desc(el)} ${Math.round(rc.width)}×${Math.round(rc.height)}`);
      }
      if (/^(INPUT|SELECT|TEXTAREA)$/.test(tag) && !/checkbox|radio|hidden|range/.test(el.type || "")) {
        const fs = parseFloat(getComputedStyle(el).fontSize);
        if (fs < 16) out.zoom.push(`${desc(el)} ${fs}px`);
      }
      // texto direto do elemento
      const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
      if (own) {
        const cs = getComputedStyle(el);
        const fs = parseFloat(cs.fontSize);
        if (fs < 11) out.tiny.push(`${desc(el)} ${fs}px`);
        // contraste: cor do texto × primeiro fundo opaco
        const rgb = (s) => (s.match(/[\d.]+/g) || []).map(Number);
        let bg = null;
        for (let p = el; p; p = p.parentElement) { const c = rgb(getComputedStyle(p).backgroundColor); if (c.length >= 3 && (c[3] === undefined || c[3] > 0.5)) { bg = c; break; } }
        if (!bg) bg = rgb(getComputedStyle(document.body).backgroundColor);
        const fg = rgb(cs.color);
        const lum = (c) => { const v = c.slice(0, 3).map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
        if (fg.length >= 3 && bg && bg.length >= 3) {
          const a = fg[3] === undefined ? 1 : fg[3];
          const mix = fg.slice(0, 3).map((x, i) => x * a + bg[i] * (1 - a));
          const L1 = lum(mix), L2 = lum(bg);
          const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
          const big = fs >= 18.66 || (fs >= 14 && +cs.fontWeight >= 700);
          if (ratio < (big ? 3 : 4.5) && !el.disabled && !el.closest("[disabled]")) out.contrast.push(`${desc(el)} ${ratio.toFixed(2)}:1`);
        }
      }
    }
    // sobreposição: texto/campo/botão em cima de outro (fora os fixos: abas, cronômetro, aviso, modal)
    const fixedish = (el) => el.closest(".tabbar, #timer, #toast, .topbar, .modal");
    const leaves = all.filter((el) => !fixedish(el) && (/^(INPUT|SELECT|TEXTAREA|BUTTON|IMG)$/.test(el.tagName) ||
      ([...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()) && !el.querySelector("input,select,button,textarea"))));
    // texto em linha que quebra: usa as caixas de cada linha (getClientRects), não o retângulo total
    const rects = leaves.map((el) => [...el.getClientRects()].filter((r) => r.width > 0 && r.height > 0));
    out.overlap = [];
    outer: for (let i = 0; i < leaves.length && out.overlap.length < 5; i++) for (let j = i + 1; j < leaves.length; j++) {
      if (leaves[i].contains(leaves[j]) || leaves[j].contains(leaves[i])) continue;
      for (const a of rects[i]) for (const b of rects[j]) {
        const w = Math.min(a.right, b.right) - Math.max(a.left, b.left), h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (w <= 2 || h <= 2) continue;
        const area = w * h, small = Math.min(a.width * a.height, b.width * b.height);
        if (area > 40 && area > 0.25 * small) { out.overlap.push(`${desc(leaves[i])} × ${desc(leaves[j])}`); continue outer; }
      }
    }
    // texto de gráfico (SVG) pequeno de verdade na tela
    out.svgTiny = [...document.querySelectorAll("#view svg text")].filter(visible).map((t) => t.getBoundingClientRect().height).filter((h) => h < 10).length;
    const v = document.getElementById("view");
    out.blank = !v || v.innerText.trim().length < 10;
    out.loading = !!v && /^(Carregando|Montando)…?$/.test(v.innerText.trim());
    const tab = document.querySelector(".tabbar"); const tr = tab && tab.getBoundingClientRect();
    const padB = parseFloat(getComputedStyle(document.body).paddingBottom);
    out.behindTab = !!tr && tr.height > padB + 1;
    out.tabH = tr ? tr.height : 0;
    return out;
  });
  if (r.hscroll > 1) issue("rolagem lateral na página", `${where}: +${r.hscroll}px`);
  r.offscreen.slice(0, 3).forEach((x) => issue("elemento saindo da tela", `${where}: ${x}`));
  if (r.small.length) issue("alvo de toque pequeno (< 36 px)", `${where}: ${r.small.length}× ex. ${r.small.slice(0, 3).join(" | ")}`);
  if (r.zoom.length) issue("campo com fonte < 16 px (iPhone dá zoom ao tocar)", `${where}: ${r.zoom.length}× ex. ${r.zoom.slice(0, 2).join(" | ")}`);
  if (r.tiny.length) issue("texto < 11 px", `${where}: ${r.tiny.slice(0, 3).join(" | ")}`);
  if (r.contrast.length) issue("contraste baixo", `${where}: ${r.contrast.length}× ex. ${r.contrast.slice(0, 3).join(" | ")}`);
  if (r.blank) issue("tela em branco", where);
  if (r.overlap.length) issue("elementos sobrepostos", `${where}: ${r.overlap.slice(0, 2).join(" | ")}`);
  if (r.svgTiny) issue("texto de gráfico < 10 px na tela", `${where}: ${r.svgTiny}×`);
  if (r.loading) issue("travou em Carregando…", where);
  if (r.behindTab) issue("conteúdo escondido atrás da barra de abas", `${where}: barra ${r.tabH}px`);
  return r;
}

async function go(page, hash, where) {
  const t0 = Date.now();
  await page.evaluate((h) => { location.hash = h; }, hash);
  await page.waitForTimeout(80);
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForFunction(() => { const v = document.getElementById("view"); return v && v.innerText.trim().length > 10 && !/^Carregando/.test(v.innerText.trim()); }, null, { timeout: 8000 }).catch(() => {});
  STATS.render_ms.push(Date.now() - t0);
  await page.waitForTimeout(150);
  return audit(page, where);
}

function overlap(a, b) { return a && b && a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height; }

// ---------------------------------------------------------------- fluxos
async function flows(page, base, persona, tag) {
  const W = (s) => `${tag} ${s}`;
  const click = async (sel, what) => {
    const el = page.locator(sel).first();
    if (!(await el.count())) { issue("botão esperado não encontrado", W(`${what} (${sel})`)); return false; }
    await el.scrollIntoViewIfNeeded().catch(() => {});
    try { await el.click({ timeout: 4000 }); } catch (e) { issue("não deu para tocar no botão", W(`${what}: ${String(e.message).split("\n")[0].slice(0, 140)}`)); return false; }
    await page.waitForLoadState("networkidle").catch(() => {});
    await page.waitForTimeout(250);
    return true;
  };
  const modalCheck = async (what) => {
    const m = await page.evaluate(() => {
      const c = document.querySelector("#modal:not([hidden]) .modal-card"); if (!c) return null;
      const r = c.getBoundingClientRect(); const x = c.querySelector(".modal-close").getBoundingClientRect();
      return { h: r.height, top: r.top, bottom: r.bottom, vh: innerHeight, vw: innerWidth, left: r.left, right: r.right, scroll: c.scrollHeight > c.clientHeight + 2,
        overflowY: getComputedStyle(c).overflowY, closeVisible: x.top >= 0 && x.bottom <= innerHeight, closeSize: [x.width, x.height] };
    });
    if (!m) { issue("modal não abriu", W(what)); return; }
    if (m.bottom > m.vh + 1 && !/(auto|scroll)/.test(m.overflowY)) issue("modal maior que a tela sem rolagem", W(`${what}: ${Math.round(m.h)}px de ${m.vh}`));
    if (m.left < -1 || m.right > m.vw + 1) issue("modal saindo da tela na largura", W(what));
    if (!m.closeVisible) issue("botão fechar do modal fora da tela", W(what));
    if (m.closeSize[0] < 36 || m.closeSize[1] < 36) issue("botão fechar do modal pequeno", W(`${what}: ${m.closeSize.map(Math.round).join("×")}`));
    await audit(page, W(`modal ${what}`));
  };

  if (persona === "novo") {
    await go(page, "#hoje", W("hoje (novo)"));
    if (await click("#gen-open", "Gerar meu treino")) {
      await modalCheck("gerador");
      await click("#gm-prev", "Pré-visualizar");
      await page.waitForSelector("#gm-apply:not([hidden])", { timeout: 15000 }).catch(() => issue("pré-visualização não terminou", W("gerador")));
      await modalCheck("gerador com prévia");
      await click("#gm-apply", "Usar e montar minha semana");
      await page.waitForTimeout(600);
      const ok = await page.evaluate(() => /Semana de/.test(document.getElementById("view").innerText));
      if (!ok) issue("gerar treino não montou a semana", W("novo"));
      await audit(page, W("hoje depois de gerar"));
    }
    return;
  }

  // Hoje: ver treino, mover, não vou treinar + desfazer
  await go(page, "#hoje", W("hoje"));
  const sum = page.locator(".day:not(.dim) details summary").first();
  if (await sum.count()) { await sum.scrollIntoViewIfNeeded(); await sum.click(); await page.waitForTimeout(100); await audit(page, W("hoje com treino aberto")); }
  if (await page.locator("[data-move]").count()) {
    await click("[data-move]", "Mover");
    await modalCheck("mover");
    await page.click(".modal-close").catch(() => issue("não fechou o modal", W("mover")));
  }
  const skipBtn = page.locator(".day [data-skip]").first();
  if (await skipBtn.count()) {
    await click(".day [data-skip]", "Não vou treinar");
    await modalCheck("reajuste");
    await click("#ok-close", "Ok do reajuste");
    if (await page.locator("[data-undo]").count()) await click("[data-undo]", "Desfazer ajuste");
  }
  if (await page.locator("#rd-apply").count()) await audit(page, W("cartão de prontidão"));

  // Treinar a partir da agenda
  const sch = await (await fetch(`${base}/api/schedule`)).json();
  const e = sch.entries.find((x) => x.status === "planejado" && x.kind === "forca") || sch.entries.find((x) => x.status === "planejado");
  if (e) {
    await go(page, `#treinar/agenda/${e.id}`, W(`treinar ${e.kind}`));
    const inp = page.locator("input[data-s='reps']").first();
    if (await inp.count()) {
      await inp.scrollIntoViewIfNeeded();
      await inp.fill("10");
      await page.locator("input[data-s='load_kg']").first().fill("42.5");
      await click("button[data-done]", "marcar série feita");
      const t = await page.evaluate(() => { const el = document.getElementById("timer"); if (!el || el.hidden) return null; const r = el.getBoundingClientRect(); const tb = document.querySelector(".tabbar").getBoundingClientRect();
        return { timer: { x: r.x, y: r.y, width: r.width, height: r.height }, tab: { x: tb.x, y: tb.y, width: tb.width, height: tb.height }, vh: innerHeight }; });
      if (!t) issue("cronômetro de descanso não apareceu", W("treinar"));
      else {
        if (overlap(t.timer, t.tab)) issue("cronômetro cobre a barra de abas", W(`treinar: timer y=${Math.round(t.timer.y)} h=${Math.round(t.timer.height)}, abas y=${Math.round(t.tab.y)}`));
        if (t.timer.y + t.timer.height > t.vh + 1) issue("cronômetro sai da tela", W("treinar"));
        // o cronômetro não pode esconder o próximo campo a preencher
        const nxt = await page.locator("input[data-s='reps']").nth(1).boundingBox().catch(() => null);
        if (nxt && overlap(t.timer, nxt)) issue("cronômetro cobre o campo da próxima série", W("treinar"));
        await audit(page, W("treinar com cronômetro"));
        await click("[data-timer='stop']", "Pular descanso");
      }
    } else if (e.kind === "resistencia" && !(await page.locator("[data-cdone]").count())) issue("treino de resistência sem campos para registrar", W("treinar"));
    page.once("dialog", (d) => d.accept());
    if (persona === "musculacao" && tag.includes("iPhone SE")) {
      // academia sem sinal na hora de salvar
      const before = (await (await fetch(`${base}/api/sessions?limit=2000`)).json()).length;
      await page.context().setOffline(true);
      await click("#s-finish", "Finalizar e salvar (sem internet)");
      await page.waitForTimeout(400);
      const st = await page.evaluate(() => ({ toast: (document.getElementById("toast") || {}).innerText || "", box: JSON.parse(localStorage.getItem("gg_outbox") || "[]").length }));
      if (!st.box) issue("sem internet: treino perdido ao salvar", W(st.toast));
      if (!/internet/i.test(st.toast)) issue("sem internet ao salvar: sem aviso", W(st.toast));
      await page.context().setOffline(false);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      await page.waitForTimeout(1200);
      const after = (await (await fetch(`${base}/api/sessions?limit=2000`)).json()).length;
      const left = await page.evaluate(() => JSON.parse(localStorage.getItem("gg_outbox") || "[]").length);
      if (after !== before + 1 || left) issue("treino guardado sem internet não foi enviado (ou duplicou)", W(`antes ${before}, depois ${after}, na fila ${left}`));
    } else await click("#s-finish", "Finalizar e salvar");
    await page.waitForTimeout(500);
    await audit(page, W("depois de salvar o treino"));
  }

  // Perfil: editar e salvar
  await go(page, "#perfil", W("perfil"));
  const save = page.locator("#view button", { hasText: /Salvar/ }).first();
  if (await save.count()) { await save.scrollIntoViewIfNeeded(); await save.click(); await page.waitForTimeout(300);
    const t = await page.evaluate(() => { const t = document.getElementById("toast"); return t && !t.hidden ? t.innerText : null; });
    if (!t) issue("salvar perfil sem confirmação visível", W("perfil"));
    else if (/erro|falha|invalid/i.test(t)) issue("salvar perfil deu erro", W(t));
    const tb = await page.evaluate(() => { const t = document.getElementById("toast"); if (!t || t.hidden) return null; const r = t.getBoundingClientRect(); const a = document.querySelector(".tabbar").getBoundingClientRect(); return [r.y, r.height, a.y]; });
    if (tb && tb[0] + tb[1] > tb[2] + 1) issue("aviso (toast) atrás da barra de abas", W(`perfil: ${tb.map(Math.round)}`));
  }
  // Esportes
  await go(page, "#esportes", W("esportes"));
  // Exercícios: busca
  await go(page, "#exercicios", W("exercícios"));
  const q = page.locator("#view input[type='search'], #view input").first();
  if (await q.count()) { await q.fill("supino"); await page.waitForTimeout(700); const n = await page.locator("#view [data-ex]").count(); if (!n) issue("busca por 'supino' sem resultado", W("exercícios"));
    else { await page.locator("#view [data-ex]").first().click(); await page.waitForTimeout(500); await modalCheck("detalhe do exercício"); await page.click(".modal-close").catch(() => {}); } }
  // voltar do navegador
  await page.goBack(); await page.waitForTimeout(400);
  const back = await page.evaluate(() => location.hash);
  if (!back || back === "#exercicios") issue("voltar do navegador não mudou de tela", W(back));
}

async function offline(context, page, base, tag) {
  await page.goto(`${base}/#hoje`); await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(800); // service worker instala
  const sw = await page.evaluate(async () => !!(navigator.serviceWorker && (await navigator.serviceWorker.getRegistration())));
  if (!sw) issue("service worker não registrado (app não abre sem internet)", tag);
  await context.setOffline(true);
  try {
    await page.reload({ timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(1200);
    const s = await page.evaluate(() => ({ top: !!document.querySelector(".topbar"), text: (document.getElementById("view") || {}).innerText || "" }));
    if (!s.top) issue("sem internet: app não abre", tag);
    else if (!s.text.trim()) issue("sem internet: tela em branco (sem aviso)", tag);
    else if (!/internet|conex|offline|rede/i.test(s.text)) issue("sem internet: sem aviso claro", `${tag}: «${s.text.trim().slice(0, 80)}»`);
  } finally { await context.setOffline(false); }
}

(async () => {
  const browser = await chromium.launch();
  let port = 8811;
  for (const persona of PERSONAS) {
    const srv = await startServer(persona, ++port);
    const base = `http://127.0.0.1:${port}`;
    try {
      const plans = await (await fetch(`${base}/api/plans`)).json();
      const hist = await (await fetch(`${base}/api/history`)).json().catch(() => ({ exercises: [] }));
      const routes = ["#hoje", "#planilhas", "#treinar", "#analise", "#evolucao", "#saude", "#esportes", "#exercicios", "#perfil"];
      if (plans.length) routes.push(`#plano/${plans[0].id}`, `#treinar/${plans[0].id}/0`);
      if ((hist.exercises || []).length) routes.push(`#evolucao/${encodeURIComponent(hist.exercises[0].exercise_id)}`);
      for (const [dname, dev] of DEVICES) {
        for (const theme of THEMES) {
          const context = await browser.newContext({ ...dev, colorScheme: theme, locale: "pt-BR", timezoneId: "America/Sao_Paulo", serviceWorkers: "allow" });
          const page = await context.newPage();
          const tag = `[${persona} · ${dname} · ${theme}]`;
          page.on("pageerror", (e) => issue("erro de JavaScript", `${tag} ${String(e.message).slice(0, 120)} @ ${String(e.stack || "").split("\n").slice(1, 3).join(" ").replace(/http:\/\/127.0.0.1:\d+/g, "").slice(0, 200)}`));
          page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) issue("erro no console", `${tag} ${m.text().slice(0, 200)}`); });
          page.on("response", (r) => { if (r.status() >= 500 || (r.status() >= 400 && ![401, 404, 409, 501].includes(r.status()))) issue(`requisição com erro ${r.status()}`, `${tag} ${r.url().replace(base, "")}`); });
          const slow = dname === "Pixel 7" && theme === "dark";  // 4G fraco de academia: 300 ms de latência, ~1,6 Mbit/s
          if (slow) {
            const cdp = await context.newCDPSession(page);
            await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 300, downloadThroughput: 200 * 1024, uploadThroughput: 100 * 1024 });
          }
          await page.goto(`${base}/`); await page.waitForLoadState("networkidle").catch(() => {});
          for (const h of routes) {
            const t0 = Date.now();
            await go(page, h, `${tag} ${h}`);
            if (slow) { const ms = Date.now() - t0; (STATS.slow_ms = STATS.slow_ms || []).push(ms); if (ms > 4000) issue("tela lenta no 4G fraco (> 4 s)", `${tag} ${h}: ${ms} ms`); }
            if (SHOTS && (dname === "iPhone SE" || dname === "Fold capa")) {
              fs.mkdirSync(SHOTS, { recursive: true });
              await page.screenshot({ path: path.join(SHOTS, `${persona}-${dname.replace(/\s/g, "")}-${theme}-${h.replace(/[#/%]/g, "_")}.png`), fullPage: true });
            }
          }
          if (theme === "light" && !dname.includes("iPad")) {
            await flows(page, base, persona, tag).catch((e) => issue("fluxo interrompido", `${tag} ${String(e.message).split("\n")[0].slice(0, 200)}`));
            if (persona === "musculacao" && dname === "iPhone SE") await offline(context, page, base, tag);
          }
          await context.close();
          // estado limpo para o próximo aparelho (fluxos mexem no banco)
        }
        if (persona !== "novo") { /* mesmo banco: fluxos acumulam, como um usuário real */ }
      }
    } finally { srv.kill(); }
  }
  await browser.close();
  console.log(`\n=== telas: ${PERSONAS.length} personas × ${DEVICES.length} aparelhos × ${THEMES.length} temas ===`);
  const sorted = [...ISSUES.entries()].sort((a, b) => b[1].n - a[1].n);
  for (const [k, v] of sorted) { console.log(`${String(v.n).padStart(5)}  ${k}`); v.ex.forEach((x) => console.log(`         ${x.slice(0, 260)}`)); }
  if (STATS.slow_ms) { const q = STATS.slow_ms.sort((a, b) => a - b); console.log(`--- 4G fraco: mediana ${q[q.length >> 1]} ms · máx ${q[q.length - 1]} ms`); }
  const s = STATS.render_ms.sort((a, b) => a - b);
  console.log(`\n--- tempo até renderizar a tela: mediana ${s[s.length >> 1]} ms · p90 ${s[Math.floor(s.length * 0.9)]} ms · máx ${s[s.length - 1]} ms · n ${s.length}`);
})();
