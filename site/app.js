(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (x, d = 2) => (x == null || Number.isNaN(x) ? "n/d" : new Intl.NumberFormat("es-ES", { minimumFractionDigits: d, maximumFractionDigits: d }).format(x));
  const pct = (x, d = 1) => (x == null ? "n/d" : (x >= 0 ? "+" : "−") + fmt(Math.abs(x) * 100, d) + " %");
  const money = (x) => {
    if (x == null) return "n/d";
    const a = Math.abs(x);
    if (a >= 1e12) return fmt(x / 1e12, 2) + " bill. $";
    if (a >= 1e9) return fmt(x / 1e9, 1) + " mil M $";
    if (a >= 1e6) return fmt(x / 1e6, 0) + " M $";
    return fmt(x, 0) + " $";
  };
  const sigCls = (s) => (s == null ? "" : s <= -4 ? "s4" : s <= -3 ? "s3" : s >= 1 ? "sp" : "s0");
  const sigTxt = (s) => (s == null ? "—" : (s >= 0 ? "+" : "−") + fmt(Math.abs(s)));
  const today = () => new Date().toISOString().slice(0, 10);
  const uid = () => (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2));
  const toast = (msg, ms = 2400) => { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms); };
  const store = {
    get(k, def) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : def; } catch { return def; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch { return false; } },
  };
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); }
    catch { const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); }
    toast("Copiado: pégalo en tu IA (con búsqueda web activada)");
  }
  const getJSON = async (url) => { const r = await fetch(url, { cache: "no-store" }); if (!r.ok) throw new Error(r.status + " " + url); return r.json(); };

  const state = { tab: "signals", q: "", sector: "", sort: "sigma", asc: true, data: null, byTicker: {}, cfg: null };

  // ================================================================ watchlist storage
  const WL = {
    data: { positions: [] }, sha: null, mode: "local", status: {}, error: null,
    settings() { return store.get("pb.settings", {}); },
    async load() {
      this.status = await getJSON("data/watchlist_status.json").catch(() => ({}));
      const s = this.settings();
      if (s.repo && s.token) {
        try {
          const r = await this.api("GET");
          this.data = JSON.parse(b64decode(r.content)); this.sha = r.sha; this.mode = "github"; this.error = null;
          this.data.positions ||= [];
          return;
        } catch (e) { this.error = e.message; }
      }
      this.mode = "local";
      const local = store.get("pb.watchlist", null);
      this.data = local || (await getJSON("data/watchlist.json").catch(() => ({ positions: [] })));
    },
    async api(method, body) {
      const s = this.settings();
      const url = `https://api.github.com/repos/${s.repo}/contents/watchlist.json` + (method === "GET" ? `?ref=${s.branch || "main"}&t=${Date.now()}` : "");
      const r = await fetch(url, {
        method, headers: { Accept: "application/vnd.github+json", Authorization: `Bearer ${s.token}`, "X-GitHub-Api-Version": "2022-11-28" },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!r.ok) {
        if (r.status === 404 && method === "GET") return { content: b64encode('{"positions":[]}'), sha: null };
        throw new Error(`GitHub ${r.status}: ${(await r.json().catch(() => ({}))).message || ""}`);
      }
      return r.json();
    },
    async save(message) {
      this.data.updated = new Date().toISOString();
      if (this.mode === "github") {
        const text = JSON.stringify(this.data, null, 2) + "\n";
        const body = { message, content: b64encode(text), branch: this.settings().branch || "main" };
        if (this.sha) body.sha = this.sha;
        try {
          const r = await this.api("PUT", body);
          this.sha = r.content.sha;
          toast("Guardado en GitHub. Las métricas se recalculan en unos minutos.");
        } catch (e) {
          if (/409|does not match/.test(e.message)) { toast("Había cambios en otro dispositivo; recargo y vuelve a intentarlo"); await this.load(); }
          else toast("No se pudo guardar en GitHub: " + e.message, 5000);
          throw e;
        }
      } else {
        store.set("pb.watchlist", this.data);
        toast("Guardado en este navegador");
      }
    },
    open() { return this.data.positions.filter((p) => p.status !== "closed"); },
    closed() { return this.data.positions.filter((p) => p.status === "closed"); },
    byTicker(t) { return this.open().find((p) => p.ticker === t); },
  };
  function b64encode(str) { return btoa(unescape(encodeURIComponent(str))); }
  function b64decode(b64) { return decodeURIComponent(escape(atob(String(b64).replace(/\s/g, "")))); }

  // Estado de una posición: el calculado cada noche (servidor) o, si es nueva, aproximado con los datos de hoy
  function posStatus(p) {
    const srv = WL.status[p.id];
    const row = state.byTicker[p.ticker];
    const st = srv && !srv.error && srv.ticker === p.ticker && sameEntry(srv, p) ? { ...srv } : { pending: true };
    if (row) {
      st.close ??= row.close; st.base ??= row.base; st.sigma ??= row.sigma;
      st.dist_to_base_pct ??= row.base / row.close - 1;
      if (st.pending) {
        st.pnl_pct = p.status === "closed" && p.exit_price ? p.exit_price / p.entry_price - 1 : row.close / p.entry_price - 1;
        if (p.status !== "closed" && row.high >= row.base && p.entry_price < row.base) { st.target_hit_date = row.last_date; st.target_hit_price = row.base; }
      }
    } else if (srv?.error) st.error = srv.error;
    if (p.status === "closed" && p.exit_price) st.pnl_pct = p.exit_price / p.entry_price - 1;
    const lr = new Date(p.last_review || p.entry_date);
    const next = new Date(lr.getTime() + (state.cfg?.review_days || 90) * 864e5);
    st.next_review = next.toISOString().slice(0, 10);
    st.review_due = p.status !== "closed" && new Date() >= next;
    return st;
  }
  function sameEntry(srv, p) { return srv.entry_date === p.entry_date && Math.abs(Number(srv.entry_price) - Number(p.entry_price)) < 1e-6 && (srv.status || "open") === (p.status || "open"); }

  // ================================================================ carga
  async function load() {
    state.data = await getJSON("data/summary.json");
    const { meta, rows } = state.data;
    state.cfg = meta.config;
    rows.forEach((r) => (state.byTicker[r.ticker] = r));
    const d = new Date(meta.updated);
    $("#meta").textContent = `Actualizado ${d.toLocaleString("es-ES", { dateStyle: "medium", timeStyle: "short" })} · cierre ${rows[0]?.last_date ?? ""} · fuente: ${meta.source}`;
    $("#demo-banner").hidden = !meta.demo;
    const sectors = [...new Set(rows.map((r) => r.sector).filter(Boolean))].sort();
    $("#sector").insertAdjacentHTML("beforeend", sectors.map((s) => `<option>${esc(s)}</option>`).join(""));
    const short = meta.short_history || [];
    if (short.length) {
      $("#short-box").hidden = false;
      $("#short-box summary").textContent = `${short.length} empresas sin 2500 sesiones de historia (no se analizan)`;
      $("#short-list").textContent = short.map((s) => `${s.ticker} (${s.bars})`).join(", ");
    }
    await WL.load();
    renderAll();
    route();
  }

  function renderAll() { renderKpis(); renderCounts(); render(); renderWatch(); }

  function renderKpis() {
    const rows = state.data.rows;
    const open = WL.open();
    const hits = open.filter((p) => posStatus(p).target_hit_date).length;
    $("#kpis").innerHTML = [
      [state.data.meta.computed, "empresas"],
      [rows.filter((r) => r.signal === "compra").length, "compra"],
      [rows.filter((r) => r.signal === "vigilar").length, "vigilar"],
      [open.length, "en cartera"],
      [hits, "en la recta 🎯"],
    ].map(([v, l]) => `<div class="kpi"><b>${v}</b><span>${l}</span></div>`).join("");
  }
  function renderCounts() {
    const rows = state.data.rows;
    $("#n-signals").textContent = rows.filter((r) => r.signal === "compra" || r.signal === "vigilar").length;
    $("#n-all").textContent = rows.length;
    $("#n-excluded").textContent = rows.filter((r) => r.excluded?.length).length;
    $("#n-watch").textContent = WL.open().length;
  }

  // ================================================================ tablas de empresas
  const HELP = {
    signals: "Compra: Top 10 por capitalización en ≤ −3σ, el resto en ≤ −4σ. Vigilar: resto de empresas entre −3σ y −4σ. Antes de comprar, pasa el análisis a tu IA.",
    all: "Todas las empresas con al menos 2500 sesiones, ordenadas por desviación.",
    excluded: "No cumplen las reglas: canal plano o bajista, o banda −3σ/−4σ por debajo de 0. Aunque estén baratas, no son oportunidad.",
  };
  const signalBadge = (r, detail = false) => {
    if (!detail && state.tab === "excluded") return `<span class="muted small">${esc(r.excluded.join(" · "))}</span>`;
    if (r.signal === "compra") return `<span class="badge buy">COMPRA</span>`;
    if (r.signal === "vigilar") return `<span class="badge watch" title="Esperar a ${fmt(r.buy_sigma, 0)}σ">VIGILAR</span>`;
    if (r.signal === "descartada") return `<span class="badge off" title="${esc(r.excluded.join(" · "))}">DESCARTADA</span>`;
    return `<span class="muted">—</span>`;
  };

  function render() {
    if (state.tab === "watch") return;
    const { rows } = state.data;
    let list = state.tab === "signals" ? rows.filter((r) => r.signal === "compra" || r.signal === "vigilar")
      : state.tab === "excluded" ? rows.filter((r) => r.excluded?.length) : rows.slice();
    const q = state.q.trim().toLowerCase();
    if (q) list = list.filter((r) => r.ticker.toLowerCase().includes(q) || r.name.toLowerCase().includes(q));
    if (state.sector) list = list.filter((r) => r.sector === state.sector);
    const k = state.sort, dir = state.asc ? 1 : -1;
    list.sort((a, b) => {
      const x = a[k], y = b[k];
      if (x == null) return 1; if (y == null) return -1;
      return (typeof x === "string" ? x.localeCompare(y) : x - y) * dir;
    });
    $$("#tbl th[data-k]").forEach((th) => { th.classList.toggle("sorted", th.dataset.k === k); th.classList.toggle("asc", th.dataset.k === k && state.asc); });
    $("#tab-help").textContent = HELP[state.tab];
    $("#tbl tbody").innerHTML = list.map((r) => {
      const pos = Math.max(0, Math.min(100, ((r.sigma + 5) / 8) * 100));
      const risk = r.riesgo_quiebra ? `<span class="pill ${esc(r.riesgo_quiebra.split(" ")[0])}">${esc(r.riesgo_quiebra)}</span>` : `<span class="muted">—</span>`;
      const inWl = !!WL.byTicker(r.ticker);
      const tags = (r.top_tier ? `<span class="tag top" title="Top ${state.cfg.top_tier_count} por capitalización: compra en ${state.cfg.buy_sigma_top_tier}σ">TOP</span>` : "")
        + (r.other_classes?.length ? `<span class="tag" title="Otras clases: ${esc(r.other_classes.join(", "))}">+${r.other_classes.length}</span>` : "");
      return `<tr data-t="${esc(r.ticker)}">
        <td class="star-col"><button class="star ${inWl ? "on" : ""}" data-star="${esc(r.ticker)}" title="${inWl ? "En tu watchlist" : "Añadir al watchlist"}">${inWl ? "★" : "☆"}</button></td>
        <td class="tk">${esc(r.ticker)}${tags}</td>
        <td class="name hide-s" title="${esc(r.name)}">${esc(r.name)}</td>
        <td class="num hide-m muted">#${r.tier_rank > 9999 ? "—" : r.tier_rank}</td>
        <td class="num">${fmt(r.close)}</td>
        <td class="num sig ${sigCls(r.sigma)}">${sigTxt(r.sigma)}</td>
        <td class="hide-m"><div class="gauge"><i style="left:calc(${pos}% - 1px)"></i></div></td>
        <td class="num hide-s">${fmt(r.base)}</td>
        <td class="num hide-m ${r.trend_pct <= 0 ? "neg" : ""}">${pct(r.trend_pct / 100)}</td>
        <td>${signalBadge(r)}</td>
        <td class="vcol">${verdictIcon(r.ticker)}</td>
        <td class="hide-s">${risk}</td></tr>`;
    }).join("");
    const empty = list.length === 0;
    $("#empty").hidden = !empty;
    $("#empty").textContent = state.tab === "signals" && !q && !state.sector
      ? "Hoy no hay ninguna señal. «Cuando no hay nada que comprar, se espera: la liquidez también es una posición»."
      : "No hay resultados.";
  }

  // ================================================================ watchlist
  function renderWatch() {
    const s = WL.settings();
    $("#sync-bar").innerHTML = WL.mode === "github"
      ? `<span class="dot ok"></span> Sincronizado con GitHub (<b>${esc(s.repo)}</b>): lo ves igual en el móvil y en el ordenador.`
      : `<span class="dot warn"></span> Guardado solo en este navegador${WL.error ? ` (error de GitHub: ${esc(WL.error)})` : ""}. <a href="#" id="sync-link">Activa la sincronización</a> para verlo en todos tus dispositivos y recibir avisos por email.`;
    $("#sync-link")?.addEventListener("click", (e) => { e.preventDefault(); openSettings(); });

    const open = WL.open().map((p) => ({ p, st: posStatus(p) }));
    const w = open.reduce((a, { p }) => a + (Number(p.weight_pct) || 0), 0);
    const warn = [];
    if (open.length > state.cfg.max_positions) warn.push(`más de ${state.cfg.max_positions} posiciones`);
    const heavy = open.filter(({ p }) => Number(p.weight_pct) > state.cfg.max_weight_pct).map(({ p }) => p.ticker);
    if (heavy.length) warn.push(`peso > ${state.cfg.max_weight_pct} % en ${heavy.join(", ")}`);
    if (w > 100) warn.push("los pesos suman más de 100 %");
    $("#wl-kpis").innerHTML = [
      [open.length, "posiciones abiertas"],
      [fmt(w, 1) + " %", "invertido"],
      [fmt(Math.max(0, 100 - w), 1) + " %", "liquidez"],
      [open.filter((o) => o.st.target_hit_date).length, "en la recta 🎯"],
      [open.filter((o) => o.st.review_due).length, "revisión pendiente"],
    ].map(([v, l]) => `<div class="kpi"><b>${v}</b><span>${l}</span></div>`).join("")
      + (warn.length ? `<p class="warnline">⚠ Gestión del riesgo (5–10 % por empresa, 10–20 empresas): ${esc(warn.join("; "))}</p>` : "");

    $("#wl-open tbody").innerHTML = open.map(({ p, st }) => {
      const status = st.error ? `<span class="muted small">${esc(st.error)}</span>`
        : st.target_hit_date ? `<span class="badge buy" title="Tocó la recta a ${fmt(st.target_hit_price)} el ${esc(st.target_hit_date)}">🎯 Recta<span class="hide-s"> ${esc(st.target_hit_date)}</span></span>`
        : st.note ? `<span class="muted small">${esc(st.note)}</span>` : `<span class="muted">En curso</span>`;
      const rev = (st.review_due ? `<span class="badge watch">Pendiente</span>` : `<span class="muted small">${esc(st.next_review)}</span>`) + " " + (savedVerdict(p.ticker) ? verdictIcon(p.ticker) : "");
      return `<tr data-t="${esc(p.ticker)}" class="${st.target_hit_date ? "hit" : ""}">
        <td class="tk">${esc(p.ticker)}${st.pending ? `<span class="tag" title="Métricas aproximadas hasta la próxima actualización">nuevo</span>` : ""}</td>
        <td class="num hide-s">${fmt(p.entry_price)}<br><span class="muted small">${esc(p.entry_date)}</span></td>
        <td class="num">${fmt(st.close)}</td>
        <td class="num ${st.pnl_pct >= 0 ? "pos" : "neg"}">${pct(st.pnl_pct)}</td>
        <td class="num hide-s sig ${sigCls(st.sigma)}">${sigTxt(st.sigma)}</td>
        <td class="num hide-m">${fmt(st.base)}</td>
        <td class="num hide-s">${st.dist_to_base_pct == null ? "n/d" : st.dist_to_base_pct <= 0 ? "✓" : pct(st.dist_to_base_pct)}</td>
        <td>${status}</td>
        <td class="hide-m">${rev}</td>
        <td class="num hide-m">${p.weight_pct ? fmt(Number(p.weight_pct), 1) + " %" : "—"}</td>
        <td class="actions"><button class="btn sm" data-act="menu" data-id="${esc(p.id)}">⋯</button></td></tr>`;
    }).join("");
    $("#wl-empty").hidden = open.length > 0;

    const closed = WL.closed();
    $("#wl-closed tbody").innerHTML = closed.length ? closed.map((p) => {
      const r = p.exit_price ? p.exit_price / p.entry_price - 1 : null;
      const days = p.exit_date ? Math.round((new Date(p.exit_date) - new Date(p.entry_date)) / 864e5) : "—";
      return `<tr data-t="${esc(p.ticker)}"><td class="tk">${esc(p.ticker)}</td>
        <td class="num">${fmt(p.entry_price)}<br><span class="muted small">${esc(p.entry_date)}</span></td>
        <td class="num">${fmt(p.exit_price)}<br><span class="muted small">${esc(p.exit_date || "")}</span></td>
        <td class="num ${r >= 0 ? "pos" : "neg"}">${pct(r)}</td><td class="num hide-s">${days}</td>
        <td class="hide-s muted small">${esc(p.notes || "")}</td>
        <td class="actions"><button class="btn sm" data-act="menu" data-id="${esc(p.id)}">⋯</button></td></tr>`;
    }).join("") : `<tr><td colspan="7" class="muted">Sin posiciones cerradas.</td></tr>`;
  }

  // ================================================================ formularios
  function modal(html, onSubmit) {
    const f = $("#modal-form");
    f.innerHTML = html;
    $("#modal").hidden = false;
    f.onsubmit = async (e) => {
      e.preventDefault();
      const data = Object.fromEntries(new FormData(f).entries());
      const btn = f.querySelector("button[type=submit]");
      if (btn) { btn.disabled = true; btn.textContent = "Guardando…"; }
      try { await onSubmit(data, e.submitter?.value); closeModal(); }
      catch (err) { if (btn) { btn.disabled = false; btn.textContent = "Reintentar"; } }
    };
    f.querySelectorAll("[data-cancel]").forEach((b) => (b.onclick = closeModal));
    f.querySelector("input:not([type=hidden])")?.focus();
  }
  function closeModal() { $("#modal").hidden = true; $("#modal-form").innerHTML = ""; }

  function positionForm(p, prefill = {}) {
    const isNew = !p;
    p = p || { id: uid(), ticker: prefill.ticker || "", entry_date: today(), entry_price: prefill.price ?? "", weight_pct: 5, notes: prefill.notes || "", status: "open" };
    const tickers = state.data.rows.map((r) => `<option value="${esc(r.ticker)}">${esc(r.name)}</option>`).join("");
    modal(`
      <h3>${isNew ? "Añadir al watchlist" : "Editar posición"}</h3>
      <label>Ticker<input name="ticker" list="tickers" required value="${esc(p.ticker)}" ${isNew ? "" : "readonly"} style="text-transform:uppercase"></label>
      <datalist id="tickers">${tickers}</datalist>
      <div class="row2">
        <label>Fecha de entrada<input name="entry_date" type="date" required value="${esc(p.entry_date)}"></label>
        <label>Precio de entrada ($)<input name="entry_price" type="number" step="0.01" min="0" required value="${esc(p.entry_price)}"></label>
      </div>
      <div class="row2">
        <label>Peso en cartera (%)<input name="weight_pct" type="number" step="0.5" min="0" max="100" value="${esc(p.weight_pct ?? "")}"></label>
        <label>Última revisión fundamental<input name="last_review" type="date" value="${esc(p.last_review || p.entry_date)}"></label>
      </div>
      <label>Notas<textarea name="notes" rows="2" placeholder="Por qué entras, qué dijo la IA…">${esc(p.notes || "")}</textarea></label>
      <p class="muted small">La estrategia recomienda entre el 5 % y el 10 % por empresa. El objetivo de salida es la recta de regresión.</p>
      <div class="modal-actions"><button type="button" class="btn" data-cancel>Cancelar</button><button type="submit" class="btn primary">Guardar</button></div>`,
      async (d) => {
        const t = d.ticker.trim().toUpperCase();
        Object.assign(p, { ticker: t, entry_date: d.entry_date, entry_price: Number(d.entry_price),
          weight_pct: d.weight_pct === "" ? null : Number(d.weight_pct), last_review: d.last_review || d.entry_date, notes: d.notes.trim() });
        if (isNew) WL.data.positions.push(p);
        await WL.save(`${isNew ? "Añadir" : "Editar"} ${t} en el watchlist`).catch((e) => { if (isNew) WL.data.positions.pop(); throw e; });
        renderAll();
      });
  }

  function closeForm(p) {
    const row = state.byTicker[p.ticker];
    const st = posStatus(p);
    modal(`
      <h3>Cerrar ${esc(p.ticker)}</h3>
      <p class="muted small">Entrada ${fmt(p.entry_price)} $ el ${esc(p.entry_date)}.${st.target_hit_date ? ` Tocó la recta el ${esc(st.target_hit_date)} a ${fmt(st.target_hit_price)} $.` : ""}</p>
      <div class="row2">
        <label>Fecha de salida<input name="exit_date" type="date" required value="${esc(today())}"></label>
        <label>Precio de salida ($)<input name="exit_price" type="number" step="0.01" min="0" required value="${esc(row ? row.close.toFixed(2) : "")}"></label>
      </div>
      <label>Notas<textarea name="notes" rows="2">${esc(p.notes || "")}</textarea></label>
      <p class="muted small">Si solo vendes una parte, cierra esta posición y añade otra con el resto, o anótalo en las notas.</p>
      <div class="modal-actions"><button type="button" class="btn" data-cancel>Cancelar</button><button type="submit" class="btn primary">Cerrar posición</button></div>`,
      async (d) => {
        const prev = { ...p };
        Object.assign(p, { status: "closed", exit_date: d.exit_date, exit_price: Number(d.exit_price), notes: d.notes.trim() });
        await WL.save(`Cerrar ${p.ticker}`).catch((e) => { Object.assign(p, prev); throw e; });
        renderAll();
      });
  }

  function menuFor(id) {
    const p = WL.data.positions.find((x) => x.id === id);
    if (!p) return;
    const open = p.status !== "closed";
    modal(`
      <h3>${esc(p.ticker)} <span class="muted small">${open ? "abierta" : "cerrada"}</span></h3>
      <div class="menu">
        <button type="button" class="btn" data-m="view">Ver gráfico y fundamentales</button>
        ${open ? `<button type="button" class="btn" data-m="review">Copiar prompt de revisión para la IA</button>
        <button type="button" class="btn" data-m="paste">Pegar la revisión de la IA</button>
        <button type="button" class="btn" data-m="reviewed">Marcar revisada hoy (sin pegar)</button>
        <button type="button" class="btn" data-m="close">Cerrar posición (vender)</button>` : `<button type="button" class="btn" data-m="reopen">Reabrir</button>`}
        <button type="button" class="btn" data-m="edit">Editar</button>
        <button type="button" class="btn danger" data-m="delete">Eliminar</button>
        <button type="button" class="btn" data-cancel>Cancelar</button>
      </div>`, async () => {});
    $$("#modal-form [data-m]").forEach((b) => (b.onclick = async () => {
      const m = b.dataset.m;
      if (m === "view") { closeModal(); location.hash = "#/t/" + encodeURIComponent(p.ticker); }
      if (m === "edit") positionForm(p);
      if (m === "close") closeForm(p);
      if (m === "review") { closeModal(); await copyReview(p.ticker); }
      if (m === "paste") { closeModal(); location.hash = "#/t/" + encodeURIComponent(p.ticker); setTimeout(() => $("#v-text")?.focus(), 900); }
      if (m === "reviewed") { const prev = p.last_review; p.last_review = today(); closeModal(); await WL.save(`Revisión de ${p.ticker}`).catch(() => (p.last_review = prev)); renderAll(); }
      if (m === "reopen") { const prev = { ...p }; p.status = "open"; delete p.exit_date; delete p.exit_price; closeModal(); await WL.save(`Reabrir ${p.ticker}`).catch(() => Object.assign(p, prev)); renderAll(); }
      if (m === "delete") {
        b.textContent = "Pulsa otra vez para eliminar"; b.dataset.m = "delete2";
        b.onclick = async () => { const i = WL.data.positions.indexOf(p); WL.data.positions.splice(i, 1); closeModal();
          await WL.save(`Eliminar ${p.ticker}`).catch(() => WL.data.positions.splice(i, 0, p)); renderAll(); };
      }
    }));
  }

  async function copyReview(ticker) {
    const r = state.byTicker[ticker];
    if (!r?.has_fundamentals) { toast("Aún no hay datos de esta empresa: aparecerán tras la próxima actualización"); return; }
    const f = await getJSON(`data/f/${encodeURIComponent(ticker)}.json`);
    copy(f.ai_prompt_review || f.ai_prompt);
  }


  // ================================================================ veredictos de la IA
  const norm = (x) => String(x || "").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[*_`#>]/g, "").trim().toUpperCase();
  const pick = (val, opts) => { const n = norm(val); return opts.find((o) => n.startsWith(o)) || opts.find((o) => n.includes(o)) || null; };
  const yes = (val) => { const n = norm(val); if (!n) return null; if (/^(SI|YES|S$)/.test(n)) return true; if (/^NO/.test(n)) return false; return null; };

  function parseVerdicts(text, fallbackTicker) {
    const out = [];
    const re = /\[\[\s*VEREDICTO\s*\]\]([\s\S]*?)(?=\[\[\s*FIN\s*\]\]|\[\[\s*VEREDICTO\s*\]\]|$)/gi;
    let m;
    while ((m = re.exec(text)) && m[0].length) {
      const v = {};
      m[1].split(/\r?\n/).forEach((line) => {
        const mm = line.match(/^[\s*_>\-•]*([A-Za-zÁÉÍÓÚÑáéíóúñ_ ]{3,20}?)[\s*_]*:\s*(.+)$/);
        if (mm) v[norm(mm[1]).replace(/[\s_]+/g, "")] = mm[2].replace(/[*`]/g, "").trim();
      });
      const t = norm(v.TICKER || fallbackTicker).replace(/[^A-Z0-9.\-]/g, "");
      if (!t) continue;
      out.push({ ticker: t, caida: pick(v.CAIDA, ["TEMPORAL", "PERMANENTE", "DUDOSA"]), quiebra: pick(v.QUIEBRA, ["BAJO", "MEDIO", "ALTO"]),
        luz: yes(v.LUZVERDE), mantener: yes(v.MANTENER), cambio: yes(v.CAMBIO), motivo: (v.MOTIVO || "").trim() });
    }
    if (!out.length && fallbackTicker) {  // la IA no añadió el bloque: busco "luz verde: sí/no"
      const lv = text.match(/luz\s*verde[^a-zA-Z\n]{0,6}(s[ií]|no)(?![a-zA-Z])/i);
      const mt = text.match(/mantener[^a-zA-Z\n]{0,6}(s[ií]|no)(?![a-zA-Z])/i);
      if (lv || mt) out.push({ ticker: fallbackTicker, caida: null, quiebra: null, luz: lv ? yes(lv[1]) : null,
        mantener: mt ? yes(mt[1]) : null, cambio: null, motivo: "", approx: true });
    }
    return out;
  }

  // Cruza el veredicto con el indicador (o con la posición abierta) y lo resume en 1-2 frases
  function decide(v, row, pos) {
    const desc = [v.caida && `caída ${v.caida.toLowerCase()}`, v.quiebra && `riesgo de quiebra ${v.quiebra.toLowerCase()}`].filter(Boolean).join(", ");
    const bad = v.caida === "PERMANENTE" || v.quiebra === "ALTO";
    const caution = v.caida === "DUDOSA" || v.quiebra === "MEDIO";
    if (pos) {
      if (v.mantener === false || (v.mantener == null && (bad || v.luz === false)) || bad)
        return { level: "bad", icon: "🔴", title: "Vender", text: `Tu IA cree que la tesis ha cambiado${desc ? ` (${desc})` : ""}. Según la estrategia, es el único motivo para vender aunque sea con pérdidas.` };
      const target = row ? ` Sigue el objetivo: la recta en ${fmt(row.base)} $ (${pct(row.base / row.close - 1)}).` : "";
      if (caution) return { level: "warn", icon: "🟠", title: "Mantener, pero vigila", text: `No hay motivo para vender, aunque hay dudas${desc ? ` (${desc})` : ""}; adelanta la próxima revisión.${target}` };
      return { level: "good", icon: "🟢", title: "Mantener", text: `La tesis sigue intacta${desc ? ` (${desc})` : ""}.${target}` };
    }
    if (v.luz === false || bad)
      return { level: "bad", icon: "❌", title: "No comprar", text: `Aunque esté barata, tu IA ve ${desc || "señales de daño permanente o de riesgo financiero"}: puede ser una empresa en declive, no una oportunidad.` };
    const ok = v.luz === true || (v.luz == null && v.caida === "TEMPORAL" && v.quiebra === "BAJO");
    if (!ok) return { level: "warn", icon: "⚠️", title: "Veredicto poco claro", text: "La respuesta no deja claro si hay luz verde. Pídele a tu IA que complete el bloque [[VEREDICTO]]." };
    if (!row) return { level: "warn", icon: "⏳", title: "Fundamentales bien", text: "Tu IA da luz verde, pero esta empresa no está en el portal y no puedo comprobar el indicador." };
    const where = `${sigTxt(row.sigma)}σ`;
    if (row.signal === "compra") {
      if (caution) return { level: "warn", icon: "⚠️", title: "Comprar con cautela", text: `Está en zona de compra (${where}) y tu IA da luz verde, pero con dudas (${desc}). Si entras, mejor con una posición pequeña (≈5 %).` };
      return { level: "good", icon: "✅", title: "Comprar", text: `Está en zona de compra (${where}; compra en ${fmt(row.buy_sigma, 0)}σ) y tu IA ve una caída temporal sin riesgo de quiebra. Entre el 5 % y el 10 % de la cartera; objetivo: la recta en ${fmt(row.base)} $ (${pct(row.base / row.close - 1)}).` };
    }
    if (row.signal === "vigilar")
      return { level: "wait", icon: "⏳", title: "Esperar a −4σ", text: `${caution ? `Luz verde con dudas (${desc}): conviene confirmar la tesis antes de entrar.` : "Los fundamentales dan luz verde."} Al no ser Top ${state.cfg.top_tier_count}, la estrategia espera a −4σ: ${fmt(row.bands["-4"])} $ (${pct(row.bands["-4"] / row.close - 1)} desde el precio actual)${caution ? "; si llega, mejor con una posición pequeña (≈5 %)" : ""}.` };
    if (row.signal === "descartada" || row.excluded?.length)
      return { level: "bad", icon: "⛔", title: "No cumple las reglas", text: `Los fundamentales están bien, pero se descarta por: ${row.excluded.join("; ")}.` };
    return { level: "wait", icon: "⏳", title: "Sin señal", text: `Los fundamentales están bien, pero el precio (${where}) no está en zona de compra (${fmt(row.buy_sigma, 0)}σ).` };
  }

  const savedVerdict = (t) => (WL.data.verdicts || {})[t];
  function verdictIcon(t) {
    const v = savedVerdict(t);
    if (!v) return `<span class="muted">—</span>`;
    const d = decide(v, state.byTicker[t], v.kind === "revision" ? WL.byTicker(t) : null);
    const old = (Date.now() - new Date(v.date)) / 864e5 > (state.cfg.review_days || 90);
    return `<span class="vicon ${old ? "old" : ""}" title="${esc(d.title)} · ${esc(v.date)}${v.motivo ? " · " + esc(v.motivo) : ""}">${d.icon}</span>`;
  }

  function verdictHTML(v, d, row, pos) {
    return `<div class="verdict ${d.level}">
      <div class="v-title">${d.icon} ${esc(d.title)} <span class="muted small">${esc(v.ticker)}${v.date ? " · " + esc(v.date) : ""}${v.approx ? " · interpretado sin bloque" : ""}</span></div>
      <p>${esc(d.text)}</p>
      ${v.motivo ? `<p class="muted small">IA: «${esc(v.motivo)}»</p>` : ""}
    </div>`;
  }

  async function saveVerdicts(list, kind) {
    WL.data.verdicts ||= {};
    const date = today();
    list.forEach((v) => {
      WL.data.verdicts[v.ticker] = { ...v, kind, date };
      const p = WL.byTicker(v.ticker);
      if (p && kind === "revision") {
        p.last_review = date;
        (p.reviews ||= []).push({ date, caida: v.caida, quiebra: v.quiebra, mantener: v.mantener, motivo: v.motivo });
      }
    });
    await WL.save(`Veredicto de la IA: ${list.map((v) => v.ticker).join(", ")}`);
    renderAll();
  }

  // Ficha de empresa: caja para pegar la respuesta
  function renderVerdictBox(r) {
    const pos = WL.byTicker(r.ticker);
    const kind = pos ? "revision" : "entrada";
    const sv = savedVerdict(r.ticker);
    $("#d-verdict").innerHTML = `
      <div class="card vbox">
        <h3>${pos ? "Revisión de tu IA" : "Veredicto de tu IA"}</h3>
        <div id="v-saved">${sv ? verdictHTML(sv, decide(sv, r, sv.kind === "revision" ? pos : null), r, pos) : `<p class="muted small">1) Copia el análisis con el botón de arriba · 2) pégalo en tu IA con búsqueda web · 3) pega aquí su respuesta completa.</p>`}</div>
        <textarea id="v-text" rows="4" placeholder="Pega aquí la respuesta de tu IA…"></textarea>
        <div class="ai-row"><button class="btn primary" id="v-go">Interpretar</button><span id="v-msg" class="muted small"></span></div>
        <div id="v-result"></div>
      </div>`;
    $("#v-go").onclick = () => {
      const list = parseVerdicts($("#v-text").value, r.ticker).filter((v) => v.ticker === r.ticker);
      if (!list.length) { $("#v-msg").textContent = "No encuentro el bloque [[VEREDICTO]]. Copia la respuesta completa o pide a tu IA que lo añada."; return; }
      const v = list[list.length - 1];
      const d = decide(v, r, pos);
      $("#v-msg").textContent = "";
      $("#v-result").innerHTML = verdictHTML({ ...v, date: "" }, d, r, pos) + `<div class="ai-row">
        <button class="btn" id="v-save">${pos ? "Guardar y marcar revisada" : "Guardar veredicto"}</button>
        ${!pos && d.level === "good" || !pos && d.level === "warn" && d.title.startsWith("Comprar") ? `<button class="btn primary" id="v-add">Guardar y añadir al watchlist</button>` : ""}
        ${pos && d.level === "bad" ? `<button class="btn danger" id="v-close">Guardar y cerrar posición</button>` : ""}</div>`;
      $("#v-save").onclick = async () => { await saveVerdicts([v], kind); renderVerdictBox(r); };
      $("#v-add")?.addEventListener("click", async () => {
        await saveVerdicts([v], kind);
        positionForm(null, { ticker: r.ticker, price: r.close.toFixed(2), notes: v.motivo });
      });
      $("#v-close")?.addEventListener("click", async () => { await saveVerdicts([v], kind); closeForm(pos); });
    };
  }

  // Pegar la respuesta con varias empresas (señales o revisión trimestral)
  function pasteModal(kind) {
    modal(`
      <h3>${kind === "revision" ? "Pegar la revisión de tu IA" : "Pegar la respuesta de tu IA"}</h3>
      <p class="small muted">Pega la respuesta completa. Leo los bloques [[VEREDICTO]] de cada empresa y los cruzo con el indicador${kind === "revision" ? " y con tus posiciones" : ""}.</p>
      <textarea name="text" rows="8" placeholder="Pega aquí…"></textarea>
      <div id="pm-result"></div>
      <div class="modal-actions"><button type="button" class="btn" data-cancel>Cerrar</button>
        <button type="button" class="btn" id="pm-go">Interpretar</button>
        <button type="submit" class="btn primary" id="pm-save" disabled>Guardar veredictos</button></div>`,
      async () => { await saveVerdicts(pending, kind); });
    let pending = [];
    $("#pm-go").onclick = () => {
      pending = parseVerdicts($("#modal-form textarea").value);
      const res = $("#pm-result");
      if (!pending.length) { res.innerHTML = `<p class="neg small">No encuentro ningún bloque [[VEREDICTO]]. ¿Has copiado la respuesta completa?</p>`; return; }
      res.innerHTML = pending.map((v) => {
        const pos = kind === "revision" ? WL.byTicker(v.ticker) : null;
        return verdictHTML({ ...v, date: "" }, decide(v, state.byTicker[v.ticker], pos), state.byTicker[v.ticker], pos);
      }).join("");
      $("#pm-save").disabled = false;
    };
  }

  function openSettings() {
    const s = WL.settings();
    modal(`
      <h3>Sincronizar el watchlist con GitHub</h3>
      <p class="small muted">El watchlist se guarda en el archivo <code>watchlist.json</code> de tu repositorio. Así lo ves igual en todos tus dispositivos, el proceso diario calcula si tus posiciones han tocado la recta y te avisa por email.</p>
      <label>Repositorio (usuario/nombre)<input name="repo" placeholder="alex-j10/stocks-regresion-strategy" value="${esc(s.repo || "")}" required></label>
      <label>Token de GitHub<input name="token" type="password" placeholder="github_pat_…" value="${esc(s.token || "")}" required></label>
      <p class="small muted">Crea un <b>fine-grained token</b> en GitHub → Settings → Developer settings → Personal access tokens: acceso solo a este repositorio y permiso <b>Contents: Read and write</b>. Se guarda solo en este navegador; hay que ponerlo una vez en cada dispositivo.</p>
      <div class="modal-actions">
        ${s.token ? `<button type="submit" class="btn danger" value="off">Desconectar</button>` : ""}
        <button type="button" class="btn" data-cancel>Cancelar</button><button type="submit" class="btn primary" value="on">Conectar</button></div>`,
      async (d, action) => {
        if (action === "off") { store.set("pb.settings", {}); await WL.load(); renderAll(); toast("Desconectado"); return; }
        const local = WL.mode === "local" ? WL.data : null;
        store.set("pb.settings", { repo: d.repo.trim(), token: d.token.trim(), branch: "main" });
        await WL.load();
        if (WL.mode !== "github") { toast("No se pudo conectar: " + WL.error, 5000); throw new Error(WL.error); }
        if (local?.positions?.length && !WL.data.positions.length) { WL.data = local; await WL.save("Importar watchlist local"); }
        renderAll(); toast("Conectado a GitHub");
      });
  }

  // ================================================================ detalle
  let charts = [];
  async function openDetail(ticker) {
    const r = state.byTicker[ticker];
    if (!r) { toast(`${ticker} no tiene datos todavía`); return; }
    $("#detail").hidden = false;
    document.body.style.overflow = "hidden";
    const pos = WL.byTicker(ticker);
    $("#d-title").innerHTML = `${esc(r.ticker)} <span class="muted" style="font-weight:400">· ${esc(r.name)}</span>`;
    $("#d-sub").textContent = [r.sector, r.industry, r.market_cap ? money(r.market_cap) : "", r.tier_rank <= 9999 ? `#${r.tier_rank} por capitalización` : "fuera del universo", r.other_classes?.length ? "otras clases: " + r.other_classes.join(", ") : ""].filter(Boolean).join(" · ");
    const ok = (b) => (b ? `<b class="pos">✓</b>` : `<b class="neg">✗</b>`);
    const b3 = r.bands["-3"], b4 = r.bands["-4"];
    $("#d-rules").innerHTML = `
      <div class="rule">${signalBadge({ ...r, excluded: r.excluded || [] }, true)}</div>
      <div class="rule">${r.top_tier ? `<span class="tag top">TOP ${state.cfg.top_tier_count}</span> compra en ${fmt(r.buy_sigma, 0)}σ` : `Compra en ${fmt(r.buy_sigma, 0)}σ`}</div>
      <div class="rule">${ok(r.trend_pct > state.cfg.min_trend_annual_pct)} Canal alcista (${pct(r.trend_pct / 100)}/año)</div>
      <div class="rule">${ok(b3 > 0 && b4 > 0)} −3σ y −4σ por encima de 0</div>
      <div class="rule">${ok(true)} ${r.length} sesiones</div>
      <div class="rule grow"></div>
      ${pos ? `<button class="btn" id="d-wl">★ En tu watchlist · ${pct(posStatus(pos).pnl_pct)}</button>` : `<button class="btn primary" id="d-wl">☆ Añadir al watchlist</button>`}`;
    $("#d-wl").onclick = () => (pos ? menuFor(pos.id) : positionForm(null, { ticker: r.ticker, price: r.close.toFixed(2) }));
    $("#d-stats").innerHTML = [
      ["Precio", fmt(r.close)], ["Desviación", `<span class="${sigCls(r.sigma)}">${sigTxt(r.sigma)} σ</span>`],
      ["Recta (objetivo)", `${fmt(r.base)} <span class="muted small">${pct(r.base / r.close - 1)}</span>`],
      ["+2σ", fmt(r.bands["+2"])], ["+1σ", fmt(r.bands["+1"])], ["−3σ", fmt(b3)], ["−4σ", fmt(b4)],
      ["σ ($)", fmt(r.std_dev)], ["Pearson R", fmt(r.pearson_r)],
    ].map(([k, v]) => `<div class="stat"><span>${k}</span><b>${v}</b></div>`).join("");
    $("#d-tv").textContent = `Comprobación en TradingView: gráfico diario de ${r.ticker}, «Standard Deviation Channel» con Length 2500 y desviaciones inferiores 3 y 4.`;
    $("#d-fund").innerHTML = "";
    renderVerdictBox(r);

    const ts = await getJSON(`data/t/${encodeURIComponent(r.ticker)}.json`);
    drawCharts(ts, pos);
    if (r.has_fundamentals) renderFund(await getJSON(`data/f/${encodeURIComponent(r.ticker)}.json`), !!pos);
    else if (r.signal) $("#d-fund").innerHTML = `<p class="muted">No se pudieron obtener los fundamentales hoy.</p>`;
  }

  const BANDS = [
    { k: 2, color: "#3fb950", label: "+2σ" }, { k: 1, color: "#58a6ff", label: "+1σ" },
    { k: 0, color: "#f0883e", label: "Recta" },
    { k: -3, color: "#f0883e", label: "−3σ" }, { k: -4, color: "#f85149", label: "−4σ" },
  ];
  function baseChart(el) {
    const LC = window.LightweightCharts;
    return LC.createChart(el, {
      autoSize: true,
      layout: { background: { color: "#0d1117" }, textColor: "#8b98a7", fontFamily: "inherit" },
      grid: { vertLines: { color: "#161c24" }, horzLines: { color: "#161c24" } },
      rightPriceScale: { borderColor: "#2a333f" }, timeScale: { borderColor: "#2a333f", minBarSpacing: 0.01 },
      crosshair: { mode: LC.CrosshairMode.Normal },
    });
  }
  function drawCharts(ts, pos) {
    charts.forEach((c) => c.remove()); charts = [];
    const LC = window.LightweightCharts;
    const c1 = baseChart($("#chart"));
    const n = ts.dates.length, slope = (ts.end_price - ts.start_price) / (n - 1);
    BANDS.forEach((b) => {
      const s = c1.addLineSeries({ color: b.color, lineWidth: b.k === 0 ? 2 : 1, lineStyle: LC.LineStyle.Dashed, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false, title: b.label });
      s.setData(ts.dates.map((d, i) => ({ time: d, value: ts.start_price + slope * i + b.k * ts.std_dev })));
    });
    const price = c1.addLineSeries({ color: "#e6edf3", lineWidth: 2, priceLineVisible: false });
    price.setData(ts.dates.map((d, i) => ({ time: d, value: ts.close[i] })));
    const markers = [];
    if (pos) {
      price.createPriceLine({ price: Number(pos.entry_price), color: "#a371f7", lineWidth: 1, lineStyle: LC.LineStyle.Solid, axisLabelVisible: true, title: "Entrada" });
      const t = ts.dates.find((d) => d >= pos.entry_date);
      if (t) markers.push({ time: t, position: "belowBar", color: "#a371f7", shape: "arrowUp", text: "Entrada" });
      const st = WL.status[pos.id];
      if (st?.target_hit_date) markers.push({ time: st.target_hit_date, position: "aboveBar", color: "#3fb950", shape: "arrowDown", text: "🎯 Recta" });
      price.setMarkers(markers.sort((a, b) => (a.time < b.time ? -1 : 1)));
    }
    c1.timeScale().fitContent();
    charts.push(c1);
    $("#d-legend").innerHTML = `<span><i class="solid" style="border-color:#e6edf3"></i>Cierre</span>` +
      BANDS.map((b) => `<span><i style="border-color:${b.color}"></i>${b.label}</span>`).join("") +
      (pos ? `<span><i class="solid" style="border-color:#a371f7"></i>Tu entrada</span>` : "");

    const c2 = baseChart($("#chart-sigma"));
    const sig = c2.addLineSeries({ color: "#c9d1d9", lineWidth: 1, priceLineVisible: false });
    sig.setData(ts.hist_dates.map((d, i) => ({ time: d, value: ts.hist_sigma[i] })));
    [[0, "#f0883e", "Recta"], [-3, "#f0883e", "−3σ"], [-4, "#f85149", "−4σ"], [2, "#3fb950", "+2σ"]].forEach(([v, color, title]) =>
      sig.createPriceLine({ price: v, color, lineWidth: 1, lineStyle: LC.LineStyle.Dashed, axisLabelVisible: true, title }));
    if (markers.length) sig.setMarkers(markers.filter((m) => m.time >= ts.hist_dates[0]));
    c2.timeScale().fitContent();
    charts.push(c2);
  }

  const kv = (label, value) => `<div class="kv"><span>${label}</span><b>${value}</b></div>`;
  function renderFund(f, inWl) {
    const q = f.quiebra, t = f.temporal, fl = f.flags;
    const riskCls = (fl.riesgo_quiebra || "").split(" ")[0];
    const sig = (fl.senales_temporal || []).map(([s, txt]) => `<li class="${s === "+" ? "p" : "m"}">${esc(txt)}</li>`).join("");
    const news = (f.news || []).map((n) => `<li><span class="muted small">${esc(n.date)} · ${esc(n.publisher || "")}</span><br>${n.url ? `<a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a>` : esc(n.title)}</li>`).join("");
    $("#d-fund").innerHTML = `
      <div class="ai-row">
        <button class="btn primary" id="copy-one">Copiar análisis para mi IA</button>
        ${inWl && f.ai_prompt_review ? `<button class="btn" id="copy-rev">Copiar revisión trimestral</button>` : ""}
        <span class="muted small">Las dos preguntas: ¿caída temporal? ¿riesgo de quiebra?</span>
      </div>
      <div class="cards">
        <div class="card">
          <h3>¿Riesgo de quiebra? <span class="pill ${esc(riskCls)}">${esc(fl.riesgo_quiebra)}</span></h3>
          ${kv("Altman Z", q.altman_z == null ? "n/d" : `${fmt(q.altman_z)} (${esc(q.altman_zone)})`)}
          ${kv("Deuda neta", money(q.net_debt))}
          ${kv("EBITDA", money(q.ebitda))}
          ${kv("Deuda neta / EBITDA", q.net_debt_to_ebitda == null ? "n/d" : fmt(q.net_debt_to_ebitda) + "x")}
          ${kv("Cobertura de intereses", q.interest_coverage == null ? "n/d" : fmt(q.interest_coverage, 1) + "x")}
          ${kv("Flujo de caja libre TTM", money(q.free_cash_flow_ttm))}
          ${kv("Liquidez corriente", fmt(q.current_ratio))}
          ${q.credit_note ? `<p class="muted small">${esc(q.credit_note)}</p>` : ""}
          <ul class="flags">${fl.red.map((x) => `<li class="r">${esc(x)}</li>`).join("")}${fl.green.map((x) => `<li class="g">${esc(x)}</li>`).join("")}</ul>
        </div>
        <div class="card">
          <h3>¿Caída temporal o permanente?</h3>
          ${kv("Ventas TTM", `${money(t.revenue_ttm)} <span class="muted">${pct(t.revenue_growth_ttm)}</span>`)}
          ${kv("CAGR anual ventas", pct(t.revenue_cagr_annual))}
          ${kv("Margen operativo TTM", pct(t.op_margin_ttm))}
          ${kv("vs su media histórica", t.op_margin_vs_hist_pp == null ? "n/d" : (t.op_margin_vs_hist_pp >= 0 ? "+" : "−") + fmt(Math.abs(t.op_margin_vs_hist_pp) * 100, 1) + " pp")}
          ${kv("Mediana del sector", t.sector_median_sigma == null ? "n/d" : sigTxt(t.sector_median_sigma) + " σ")}
          ${kv("3 m / 12 m", `${pct(t.return_3m)} / ${pct(t.return_12m)}`)}
          ${kv("Desde máx. 52 sem.", pct(t.drawdown_from_52w_high))}
          ${kv("PER actual / futuro", `${fmt(t.trailing_pe, 1)} / ${fmt(t.forward_pe, 1)}`)}
          ${kv("Analistas (1 compra – 5 venta)", t.analyst_recommendation == null ? "n/d" : `${fmt(t.analyst_recommendation, 1)} · ${t.analyst_count ?? "?"} an.`)}
          ${kv("Potencial a precio objetivo", pct(t.target_mean_upside))}
          <ul class="flags">${sig}</ul>
        </div>
        ${news ? `<div class="card"><h3>Titulares recientes</h3><ul class="news">${news}</ul></div>` : ""}
        ${f.business_summary ? `<div class="card"><h3>Negocio</h3><p class="small muted" style="margin:0">${esc(f.business_summary)}</p></div>` : ""}
      </div>`;
    $("#copy-one").onclick = () => copy(f.ai_prompt);
    $("#copy-rev")?.addEventListener("click", () => copy(f.ai_prompt_review));
  }

  function closeDetail() {
    $("#detail").hidden = true; document.body.style.overflow = "";
    charts.forEach((c) => c.remove()); charts = [];
    if (location.hash) history.replaceState(null, "", location.pathname + location.search);
  }
  function route() {
    const m = location.hash.match(/^#\/t\/(.+)$/);
    if (m) openDetail(decodeURIComponent(m[1])); else if (!$("#detail").hidden) closeDetail();
  }

  // ================================================================ eventos
  $$(".tab").forEach((b) => b.addEventListener("click", () => {
    $$(".tab").forEach((x) => x.classList.toggle("active", x === b));
    state.tab = b.dataset.tab;
    $("#view-list").hidden = state.tab === "watch";
    $("#view-watch").hidden = state.tab !== "watch";
    render();
  }));
  $("#q").addEventListener("input", (e) => { state.q = e.target.value; render(); });
  $("#sector").addEventListener("change", (e) => { state.sector = e.target.value; render(); });
  $$("#tbl th[data-k]").forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.k;
    state.asc = state.sort === k ? !state.asc : ["ticker", "name", "sigma", "tier_rank", "signal"].includes(k);
    state.sort = k; render();
  }));
  document.addEventListener("click", (e) => {
    const star = e.target.closest("[data-star]");
    if (star) {
      e.stopPropagation();
      const t = star.dataset.star, p = WL.byTicker(t);
      if (p) menuFor(p.id); else positionForm(null, { ticker: t, price: state.byTicker[t].close.toFixed(2) });
      return;
    }
    const act = e.target.closest("[data-act=menu]");
    if (act) { e.stopPropagation(); menuFor(act.dataset.id); return; }
    const tr = e.target.closest("tbody tr[data-t]");
    if (tr && !e.target.closest("button,a")) location.hash = "#/t/" + encodeURIComponent(tr.dataset.t);
  });
  $("#wl-add").addEventListener("click", () => positionForm(null));
  $("#paste-signals").addEventListener("click", () => pasteModal("entrada"));
  $("#paste-review").addEventListener("click", () => pasteModal("revision"));
  $("#wl-settings").addEventListener("click", openSettings);
  $("#copy-review").addEventListener("click", async () => copy(await (await fetch("data/prompt_revision.txt", { cache: "no-store" })).text()));
  $("#copy-signals").addEventListener("click", async () => copy(await (await fetch("data/prompt_senales.txt", { cache: "no-store" })).text()));
  $("#d-close").addEventListener("click", closeDetail);
  $("#detail").addEventListener("click", (e) => { if (e.target.id === "detail") closeDetail(); });
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { if (!$("#modal").hidden) closeModal(); else closeDetail(); } });
  window.addEventListener("hashchange", route);

  load().catch((e) => { $("#meta").textContent = "Error cargando datos: " + e.message; console.error(e); });
})();
