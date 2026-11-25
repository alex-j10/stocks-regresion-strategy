"""
Genera el portal estático en --out (por defecto dist/).

  python scripts/build.py             # datos reales (GitHub Actions)
  python scripts/build.py --demo      # datos inventados para probar la web
  python scripts/build.py --limit 30  # prueba rápida con las 30 mayores

Reglas (vídeo "Stock Investing Strategy"):
  - Universo: EE. UU., capitalización > 50.000 M$, una fila por empresa.
  - Canal: Standard Deviation Channel, Length = 2500 barras diarias, bandas +1, +2, −3, −4.
  - Se descarta: menos de 2500 barras, canal plano o bajista, banda −3σ o −4σ ≤ 0.
  - Compra: Top 10 por capitalización en ≤ −3σ; el resto en ≤ −4σ (entre −3 y −4 = vigilar).
  - Salida: cuando el precio toca la recta de regresión. Sin stop loss.
  - Revisión de fundamentales cada 3 meses; vender con pérdida solo si cambian.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from indicator import rolling_channel, std_dev_channel  # noqa: E402


def log(*a):
    print(*a, flush=True)


def _fix(o):
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, dict):
        return {k: _fix(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_fix(v) for v in o]
    if isinstance(o, np.floating):
        return _fix(float(o))
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def jdump(obj, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_fix(obj), ensure_ascii=False, separators=(",", ":")))


# ------------------------------------------------------------------ texto para IA
def fmt_money(x):
    if x is None: return "n/d"
    a = abs(x)
    for d, s in ((1e12, " bill."), (1e9, " mil M"), (1e6, " M")):
        if a >= d: return f"{x/d:,.1f}{s} $"
    return f"{x:,.0f} $"


def fmt_pct(x): return "n/d" if x is None else f"{x:+.1%}"
def fmt_x(x, suf="x"): return "n/d" if x is None else f"{x:.2f}{suf}"


PROMPT_HEADER = """Activa la búsqueda web para consultar noticias y resultados recientes.

Haz un análisis fundamental de cada empresa, pero NO un análisis genérico: ya sé que está en tendencia bajista, por eso la estoy mirando. Responde a estas dos preguntas concretas:
1) ¿La acción cae por un motivo TEMPORAL (pánico del mercado, problema sectorial o cíclico, hecho puntual del que la empresa puede recuperarse) o por algo que puede dañar el negocio de forma PERMANENTE (pérdida de ventaja competitiva, disrupción, deterioro estructural de márgenes, regulación, fraude)? Explica cuál es la causa de la caída.
2) ¿Tiene la empresa riesgo de quiebra o de problemas graves de solvencia? ¿Tiene fuerza financiera para superar el problema?
Si el daño no es permanente y no hay riesgo de quiebra, es luz verde.
Termina con una tabla: ticker | causa de la caída | temporal/permanente | riesgo de quiebra (bajo/medio/alto) | luz verde (sí/no) | confianza.

MUY IMPORTANTE: al final de la respuesta, añade para CADA empresa este bloque exacto, sin cambiar las claves ni los corchetes y eligiendo uno de los valores indicados:
[[VEREDICTO]]
TICKER: <ticker>
CAIDA: TEMPORAL | PERMANENTE | DUDOSA
QUIEBRA: BAJO | MEDIO | ALTO
LUZ_VERDE: SI | NO
MOTIVO: <una o dos frases con la causa de la caída y por qué>
[[FIN]]

Datos (generados automáticamente; "n/d" = no disponible):
"""

REVIEW_HEADER = """Activa la búsqueda web para consultar noticias y resultados recientes.

Revisión trimestral de posiciones abiertas (estrategia de reversión a la recta de regresión de 10 años, sin stop loss). Solo se vende con pérdida si cambia la respuesta a alguna de estas dos preguntas. Para cada empresa:
1) ¿Sigue siendo TEMPORAL el motivo de la caída, o ahora parece un daño PERMANENTE al negocio?
2) ¿Ha aparecido riesgo de quiebra o de problemas graves de solvencia?
Termina con una tabla: ticker | ¿ha cambiado algo desde la compra? | temporal/permanente | riesgo de quiebra | mantener/vender | confianza.

MUY IMPORTANTE: al final de la respuesta, añade para CADA empresa este bloque exacto, sin cambiar las claves ni los corchetes y eligiendo uno de los valores indicados:
[[VEREDICTO]]
TICKER: <ticker>
CAIDA: TEMPORAL | PERMANENTE | DUDOSA
QUIEBRA: BAJO | MEDIO | ALTO
CAMBIO: SI | NO
MANTENER: SI | NO
MOTIVO: <una o dos frases: qué ha cambiado (o no) desde la compra y por qué>
[[FIN]]

Datos (generados automáticamente; "n/d" = no disponible):
"""


def ai_block(c: dict, m: dict, position: dict | None = None) -> str:
    q, tp = m["quiebra"], m["temporal"]
    lines = [
        f"## {c['ticker']} — {c['name']}",
        f"Sector: {c['sector']} / {c['industry']}. Capitalización: {fmt_money(c['market_cap'])}"
        + (f" (Top {c['tier_rank']} por capitalización)" if c.get("top_tier") else "") + ".",
        f"Precio {c['close']:.2f} $, a {c['sigma']:+.2f} desviaciones típicas de su recta de regresión "
        f"de {c['length']} sesiones (recta {c['base']:.2f} $, −3σ {c['bands']['-3']:.2f} $, −4σ {c['bands']['-4']:.2f} $). "
        f"Tendencia del canal: {c['trend_pct']:+.1f} % anual.",
    ]
    if position:
        lines.append(f"Posición abierta desde {position['entry_date']} a {position['entry_price']:.2f} $ "
                     f"({fmt_pct(position.get('pnl_pct'))} hasta hoy).")
    lines += [
        "",
        "### Solvencia (riesgo de quiebra)",
        f"- Altman Z: {fmt_x(q['altman_z'], '')} ({q['altman_zone'] or 'n/d'})",
        f"- Deuda neta: {fmt_money(q['net_debt'])}; EBITDA: {fmt_money(q['ebitda'])}; Deuda neta/EBITDA: {fmt_x(q['net_debt_to_ebitda'])}",
        f"- Cobertura de intereses (EBIT/intereses): {fmt_x(q['interest_coverage'])}",
        f"- Flujo de caja libre TTM: {fmt_money(q['free_cash_flow_ttm'])}",
        f"- Liquidez corriente: {fmt_x(q['current_ratio'], '')}; Deuda/patrimonio: {fmt_x(q['debt_to_equity'], '')}",
    ]
    if q.get("credit_note"):
        lines.append(f"- Nota: {q['credit_note']}")
    lines += [
        "",
        "### ¿Caída temporal o permanente?",
        f"- Ventas TTM: {fmt_money(tp['revenue_ttm'])} ({fmt_pct(tp['revenue_growth_ttm'])} interanual); CAGR anual de ventas: {fmt_pct(tp['revenue_cagr_annual'])}",
        "- Ventas por año: " + (", ".join(f"{r['year']}: {fmt_money(r['revenue'])}" for r in tp["revenue_history"]) or "n/d"),
        f"- Margen operativo TTM: {fmt_pct(tp['op_margin_ttm'])}; vs media histórica: "
        + ("n/d" if tp["op_margin_vs_hist_pp"] is None else f"{tp['op_margin_vs_hist_pp']*100:+.1f} pp"),
        "- Margen operativo por año: " + (", ".join(f"{r['year']}: {r['op_margin']:.1%}" for r in tp["margin_history"]) or "n/d"),
        "- Mediana del sector en el indicador: " + ("n/d" if tp["sector_median_sigma"] is None else f"{tp['sector_median_sigma']:+.2f}σ"),
        f"- Rentabilidad 3 meses: {fmt_pct(tp['return_3m'])}; 12 meses: {fmt_pct(tp['return_12m'])}; desde máximo 52 semanas: {fmt_pct(tp['drawdown_from_52w_high'])}",
        f"- PER actual: {fmt_x(tp['trailing_pe'], '')}; PER futuro: {fmt_x(tp['forward_pe'], '')}",
        f"- Recomendación media analistas (1=compra fuerte, 5=venta): {fmt_x(tp['analyst_recommendation'], '')} "
        f"({tp['analyst_count'] or 'n/d'} analistas); potencial al precio objetivo medio: {fmt_pct(tp['target_mean_upside'])}",
    ]
    if m.get("news"):
        lines += ["", "### Titulares recientes"]
        lines += [f"- {n['date']} · {n['publisher'] or ''} · {n['title']}" for n in m["news"]]
    return "\n".join(lines)


# ------------------------------------------------------------------ indicador + reglas
def compute(universe, prices, cfg):
    L = cfg["length_bars"]
    hist_n = int(cfg["sigma_history_years"] * 252)
    top_n = cfg["top_tier_count"]
    rank = {u["ticker"]: i + 1 for i, u in enumerate(sorted(universe, key=lambda u: -u["market_cap"]))}
    rows, series, short = [], {}, []
    for u in universe:
        d = prices.get(u["ticker"])
        if d is None:
            continue
        if len(d) < L:
            short.append(dict(ticker=u["ticker"], name=u["name"], bars=len(d)))
            continue
        ch = std_dev_channel(d["close"].values[-L:], L)
        rb, rs, rsig = rolling_channel(d["close"].values, L)
        series[u["ticker"]] = dict(d=d, ch=ch, rbase=rb, rsigma=rsig)
        rows.append(make_row(u, d, ch, rank[u["ticker"]], top_n, cfg))
    return rows, series, short, hist_n


def make_row(u, d, ch, tier_rank, top_n, cfg):
    bands = {f"+{k}": ch.band(k) for k in cfg["bands_up"]}
    bands.update({f"-{k}": ch.band(-k) for k in cfg["bands_down"]})
    trend = ch.slope_per_bar * 252 / ch.end_price * 100 if ch.end_price > 0 else -999.0
    top = tier_rank <= top_n
    buy_sigma = cfg["buy_sigma_top_tier"] if top else cfg["buy_sigma_other"]
    reasons = []
    if trend <= cfg["min_trend_annual_pct"]:
        reasons.append(f"Canal plano o bajista ({trend:+.1f} %/año)")
    if cfg["exclude_if_buy_band_below_zero"]:
        low = [k for k in ("-3", "-4") if k in bands and bands[k] <= 0]
        if low:
            reasons.append("Banda " + " y ".join(k.replace("-", "−") + "σ" for k in low) + " por debajo de 0")
    s = ch.sigma
    if s <= cfg["watch_sigma"] and reasons:
        signal = "descartada"
    elif s <= buy_sigma and not reasons:
        signal = "compra"
    elif s <= cfg["watch_sigma"]:
        signal = "vigilar"
    else:
        signal = None
    return dict(
        ticker=u["ticker"], name=u["name"], sector=u["sector"], industry=u["industry"],
        country=u.get("country", ""), market_cap=u["market_cap"], other_classes=u.get("other_classes", []),
        tier_rank=tier_rank, top_tier=top, buy_sigma=buy_sigma,
        close=ch.last_close, high=float(d["high"].iloc[-1]), sigma=s, base=ch.end_price, std_dev=ch.std_dev,
        pearson_r=ch.pearson_r, length=ch.length, trend_pct=trend, bands=bands,
        excluded=reasons, signal=signal, last_date=d.index[-1].strftime("%Y-%m-%d"),
    )


def sector_medians(rows):
    by = {}
    for r in rows:
        by.setdefault(r["sector"] or "—", []).append(r["sigma"])
    return {k: statistics.median(v) for k, v in by.items() if len(v) >= 3}


# ------------------------------------------------------------------ watchlist
def load_watchlist(path: Path) -> dict:
    if path.exists():
        try:
            wl = json.loads(path.read_text(encoding="utf-8"))
            wl.setdefault("positions", [])
            return wl
        except json.JSONDecodeError as e:
            log(f"[watchlist] JSON inválido: {e}")
    return {"positions": []}


def position_status(p: dict, s: dict | None, cfg: dict, today: date) -> dict:
    st = dict(id=p["id"], ticker=p["ticker"], entry_date=p["entry_date"], entry_price=float(p["entry_price"]),
              status=p.get("status", "open"))
    if not s:
        st["error"] = "Sin datos de cotización o con menos de 2500 sesiones"
        return st
    d, rb, rsig = s["d"], s["rbase"], s["rsigma"]
    entry = pd.Timestamp(p["entry_date"])
    end = pd.Timestamp(p["exit_date"]) if p.get("status") == "closed" and p.get("exit_date") else d.index[-1]
    idx = np.where((d.index > entry) & (d.index <= end))[0]
    ep = float(p["entry_price"])
    close = float(d["close"].iloc[-1])
    st.update(close=close, base=float(rb[-1]), sigma=float(rsig[-1]),
              dist_to_base_pct=float(rb[-1]) / close - 1 if close else None,
              last_date=d.index[-1].strftime("%Y-%m-%d"))
    if p.get("status") == "closed" and p.get("exit_price"):
        st["pnl_pct"] = float(p["exit_price"]) / ep - 1
    else:
        st["pnl_pct"] = close / ep - 1
    if len(idx):
        closes = d["close"].values[idx]
        st["max_drawdown_pct"] = min(0.0, float(closes.min()) / ep - 1)
        st["max_gain_pct"] = max(0.0, float(d["high"].values[idx].max()) / ep - 1)
        hit = [i for i in idx if not np.isnan(rb[i]) and d["high"].values[i] >= rb[i]]
        ei0 = np.where(d.index <= entry)[0]
        above = len(ei0) and not np.isnan(rb[ei0[-1]]) and ep >= rb[ei0[-1]]
        if above:
            st["note"] = "Entrada por encima de la recta: el objetivo de salida no aplica"
            hit = []
        if hit:
            i = hit[0]
            st["target_hit_date"] = d.index[i].strftime("%Y-%m-%d")
            st["target_hit_price"] = float(rb[i])
            st["target_hit_pnl_pct"] = float(rb[i]) / ep - 1
    st["days_held"] = (min(end, pd.Timestamp(today)) - entry).days
    last_rev = pd.Timestamp(p.get("last_review") or p["entry_date"])
    st["next_review"] = (last_rev + pd.Timedelta(days=cfg["review_days"])).strftime("%Y-%m-%d")
    st["review_due"] = p.get("status") != "closed" and pd.Timestamp(today) >= last_rev + pd.Timedelta(days=cfg["review_days"])
    st["entry_sigma"] = None
    ei = np.where(d.index <= entry)[0]
    if len(ei) and not np.isnan(rsig[ei[-1]]):
        st["entry_sigma"] = float(rsig[ei[-1]])
    return st


# ------------------------------------------------------------------ demo
def demo_inputs():
    rng = np.random.default_rng(42)
    sectors = ["Technology", "Health Care", "Finance", "Consumer Discretionary", "Energy", "Industrials"]
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=3600)
    universe, prices = [], {}
    for i in range(48):
        t = f"DEM{i:02d}"
        drift, vol = rng.normal(0.00045, 0.0003), rng.uniform(0.012, 0.022)
        p = 50 * np.exp(np.cumsum(rng.normal(drift, vol, len(dates))))
        if i in (0, 9, 18, 27, 36, 45, 30):
            p[-120:] *= np.linspace(1, 0.2 if i == 30 else rng.uniform(0.3, 0.55), 120)
        if i == 20:
            p = 80 * np.exp(np.cumsum(rng.normal(-0.0002, 0.015, len(dates))))
            p[-80:] *= np.linspace(1, 0.5, 80)
        if i == 2:   # Top 10 que entra en −3σ
            p[-70:] *= np.linspace(1, 0.3, 70)
        if i == 5:
            p = p[-1800:]
        if i == 7:   # cae a −3σ hace ~8 meses y vuelve a la recta
            p[-170:-120] *= np.linspace(1, 0.55, 50)
            p[-120:] *= np.linspace(0.55, 1.08, 120)
        idx = dates[-len(p):]
        high = p * (1 + np.abs(rng.normal(0, 0.006, len(p))))
        prices[t] = pd.DataFrame({"close": p, "high": high}, index=idx)
        universe.append(dict(ticker=t, name=f"Empresa Demo {i:02d} Inc.", sector=sectors[i % 6], industry="Demo",
                             country="United States", market_cap=float(3e12 / (i + 1) ** 0.9),
                             other_classes=["DEM%02dB" % i] if i == 3 else []))
    return universe, prices


def demo_watchlist(prices, L=2500):
    """Posiciones de ejemplo en fechas donde el canal móvil estaba en ≤ −3σ."""
    def low_point(t, back_from, back_to):
        d = prices[t]
        _, _, sig = rolling_channel(d["close"].values, L)
        seg = sig[-back_from:-back_to] if back_to else sig[-back_from:]
        i = int(np.nanargmin(seg)) + len(sig) - back_from
        return d, i
    pos = []
    d, i = low_point("DEM07", 200, 60)
    pos.append(dict(id="demo-1", ticker="DEM07", entry_date=d.index[i].strftime("%Y-%m-%d"),
                    entry_price=round(float(d["close"].iloc[i]), 2), weight_pct=7.5, notes="Entrada en −3σ (Top 10)",
                    last_review=d.index[i].strftime("%Y-%m-%d"), status="open"))
    d = prices["DEM45"]; i = len(d) - 8
    pos.append(dict(id="demo-2", ticker="DEM45", entry_date=d.index[i].strftime("%Y-%m-%d"),
                    entry_price=round(float(d["close"].iloc[i]), 2), weight_pct=5, notes="",
                    last_review=d.index[i].strftime("%Y-%m-%d"), status="open"))
    best = None
    for t in ("DEM12", "DEM16", "DEM23", "DEM33", "DEM39"):
        dd, j = low_point(t, 1000, 300)
        _, _, sig = rolling_channel(dd["close"].values, L)
        if best is None or sig[j] < best[2]:
            best = (t, dd, sig[j], j)
    t, dd, _, j = best
    rb, _, _ = rolling_channel(dd["close"].values, L)
    k = next((k for k in range(j + 1, len(dd)) if dd["high"].iloc[k] >= rb[k]), len(dd) - 1)
    pos.append(dict(id="demo-3", ticker=t, entry_date=dd.index[j].strftime("%Y-%m-%d"),
                    entry_price=round(float(dd["close"].iloc[j]), 2), weight_pct=6, notes="Posición cerrada de ejemplo",
                    last_review=dd.index[j].strftime("%Y-%m-%d"), status="closed",
                    exit_date=dd.index[k].strftime("%Y-%m-%d"), exit_price=round(float(rb[k]), 2)))
    return {"positions": pos}


def demo_metrics(rng):
    def r(a, b): return float(rng.uniform(a, b))
    z = r(0.8, 5)
    return dict(is_financial=False,
                quiebra=dict(altman_z=z, altman_zone="segura" if z > 2.99 else "gris" if z >= 1.81 else "peligro",
                             net_debt=r(-1e10, 6e10), ebitda=r(2e9, 2e10), net_debt_to_ebitda=r(-1, 6),
                             interest_coverage=r(0.8, 25), free_cash_flow_ttm=r(-2e9, 1e10),
                             current_ratio=r(0.6, 2.5), debt_to_equity=r(20, 250), credit_note=None),
                temporal=dict(revenue_ttm=r(1e10, 2e11), revenue_growth_ttm=r(-0.2, 0.2), revenue_cagr_annual=r(-0.05, 0.15),
                              op_margin_ttm=r(0.02, 0.35), op_margin_vs_hist_pp=r(-0.12, 0.03),
                              revenue_history=[dict(year=str(y), revenue=r(1e10, 2e11)) for y in range(2022, 2026)],
                              margin_history=[dict(year=str(y), op_margin=r(0.05, 0.3)) for y in range(2022, 2026)],
                              sector_median_sigma=None, return_3m=r(-0.5, -0.1), return_12m=r(-0.6, 0),
                              drawdown_from_52w_high=r(-0.6, -0.2), trailing_pe=r(8, 40), forward_pe=r(7, 30),
                              analyst_recommendation=r(1.5, 3.5), analyst_count=int(r(5, 40)), target_mean_upside=r(0, 0.8)),
                business_summary="Empresa ficticia generada para probar el portal.", website=None,
                news=[dict(title="Titular de ejemplo (datos de demostración)", publisher="Demo", date="2026-11-20", url=None)])


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist"))
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    cfg = json.loads((ROOT / "config.json").read_text())
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "site", out)
    today = date.today()

    if args.demo:
        universe, prices = demo_inputs()
        wl = demo_watchlist(prices)
        source = "DEMO (datos inventados)"
    else:
        from universe import build_universe
        from prices import download_prices
        universe, source = build_universe(cfg, log)
        if args.limit:
            universe = universe[:args.limit]
        wl = load_watchlist(ROOT / "watchlist.json")
        tickers = list(dict.fromkeys([u["ticker"] for u in universe] +
                                     [p["ticker"].upper() for p in wl["positions"]]))
        prices = download_prices(tickers, cfg["download_years"], log=log)

    rows, series, short, hist_n = compute(universe, prices, cfg)
    by_ticker = {r["ticker"]: r for r in rows}
    med = sector_medians(rows)
    for r in rows:
        r["sector_median_sigma"] = med.get(r["sector"] or "—")

    # Posiciones del watchlist cuyo ticker no está en el universo (p. ej. bajó de 50.000 M$)
    L = cfg["length_bars"]
    for p in wl["positions"]:
        t = p["ticker"] = p["ticker"].upper()
        if t not in series and t in prices and len(prices[t]) >= L:
            d = prices[t]
            ch = std_dev_channel(d["close"].values[-L:], L)
            rb, _, rsig = rolling_channel(d["close"].values, L)
            series[t] = dict(d=d, ch=ch, rbase=rb, rsigma=rsig)
            u = dict(ticker=t, name=t, sector="", industry="", market_cap=0)
            extra = make_row(u, d, ch, 10 ** 6, cfg["top_tier_count"], cfg)
            extra["outside_universe"] = True
            rows.append(extra)
            by_ticker[t] = extra

    statuses = {p["id"]: position_status(p, series.get(p["ticker"]), cfg, today) for p in wl["positions"]}
    open_pos = {p["ticker"]: p for p in wl["positions"] if p.get("status") != "closed"}

    signals = sorted([r for r in rows if r["signal"] in ("compra", "vigilar")], key=lambda r: r["sigma"])
    log(f"[indicador] {len(rows)} calculadas; {sum(r['signal']=='compra' for r in rows)} compra, "
        f"{sum(r['signal']=='vigilar' for r in rows)} vigilar, {sum(r['signal']=='descartada' for r in rows)} descartadas, "
        f"{len(short)} con menos de {L} sesiones")

    # Fundamentales: señales + posiciones abiertas
    rng = np.random.default_rng(1)
    near = [r["ticker"] for r in sorted(rows, key=lambda r: r["sigma"]) if r["sigma"] <= cfg.get("fundamentals_sigma", -2)]
    need = list(dict.fromkeys([r["ticker"] for r in signals] + [t for t in open_pos if t in by_ticker] + near))
    prompts_sig, prompts_rev = [], []
    for t in need:
        c = by_ticker[t]
        if args.demo:
            from fundamentals import flags
            m = demo_metrics(rng)
            m["temporal"]["sector_median_sigma"] = c.get("sector_median_sigma")
            m["flags"] = flags(m)
        else:
            from fundamentals import analyze
            try:
                m = analyze(t, c["sector"], series[t]["d"]["close"], c.get("sector_median_sigma"),
                            cfg["max_news_per_company"])
            except Exception as e:  # noqa: BLE001
                log(f"[fundamentales] {t}: {e}")
                m = None
        if not m:
            continue
        pos = open_pos.get(t)
        pos_info = dict(pos, pnl_pct=statuses[pos["id"]].get("pnl_pct")) if pos else None
        m["ai_prompt"] = PROMPT_HEADER + "\n" + ai_block(c, m)
        if pos:
            m["ai_prompt_review"] = REVIEW_HEADER + "\n" + ai_block(c, m, pos_info)
            prompts_rev.append(ai_block(c, m, pos_info))
        if c["signal"] in ("compra", "vigilar"):
            prompts_sig.append(ai_block(c, m))
        c["riesgo_quiebra"] = m["flags"]["riesgo_quiebra"]
        c["has_fundamentals"] = True
        jdump(m, out / "data" / "f" / f"{t}.json")

    # Serie de cada ticker (canal actual + σ histórica del canal móvil)
    for t, s in series.items():
        d, ch = s["d"], s["ch"]
        w = d.iloc[-L:]
        rs = s["rsigma"][-hist_n:]
        rbase = s["rbase"][-hist_n:]
        hd = d.index[-hist_n:]
        ok = ~np.isnan(rs)
        jdump(dict(ticker=t, dates=[x.strftime("%Y-%m-%d") for x in w.index],
                   close=[round(float(v), 4) for v in w["close"].values],
                   start_price=ch.start_price, end_price=ch.end_price, std_dev=ch.std_dev,
                   hist_dates=[x.strftime("%Y-%m-%d") for x in hd[ok]],
                   hist_sigma=[round(float(v), 3) for v in rs[ok]],
                   hist_base=[round(float(v), 3) for v in rbase[ok]]),
              out / "data" / "t" / f"{t}.json")

    # Alertas (issues de GitHub -> email)
    alerts = []
    for p in wl["positions"]:
        st = statuses[p["id"]]
        if p.get("status") == "closed":
            continue
        if st.get("target_hit_date"):
            alerts.append(dict(title=f"🎯 {p['ticker']} ha tocado la recta de regresión (entrada {p['entry_date']})",
                               body=f"{p['ticker']} tocó la recta el {st['target_hit_date']} a {st['target_hit_price']:.2f} $ "
                                    f"({st['target_hit_pnl_pct']:+.1%} desde la entrada a {p['entry_price']} $). "
                                    "Toma de beneficios según la estrategia (total o parcial)."))
        if st.get("review_due"):
            q = f"{today.year}-T{(today.month - 1) // 3 + 1}"
            alerts.append(dict(title=f"🔎 Revisión trimestral de {p['ticker']} ({q})",
                               body="Han pasado 3 meses desde la última revisión. Copia el prompt de revisión desde el "
                                    "watchlist, pásalo a tu IA y marca la posición como revisada."))
    for r in rows:
        if r["signal"] == "compra" and r["ticker"] not in open_pos:
            alerts.append(dict(title=f"🟢 Señal de compra: {r['ticker']} ({today:%Y-%m})",
                               body=f"{r['ticker']} ({r['name']}) está a {r['sigma']:+.2f}σ (compra en ≤ {r['buy_sigma']}σ). "
                                    "Antes de comprar, pasa el análisis fundamental a tu IA."))

    meta = dict(updated=datetime.now(timezone.utc).isoformat(timespec="minutes"), source=source,
                prompt_header=PROMPT_HEADER, review_header=REVIEW_HEADER,
                demo=args.demo, config=cfg, universe_count=len(universe), computed=len(rows),
                short_history=short, missing=sorted({u["ticker"] for u in universe} - set(prices)))
    rows_sorted = sorted(rows, key=lambda r: r["sigma"])
    jdump(dict(meta=meta, rows=rows_sorted), out / "data" / "summary.json")
    jdump(wl, out / "data" / "watchlist.json")
    jdump(statuses, out / "data" / "watchlist_status.json")
    jdump(alerts, out / "data" / "alerts.json")
    (out / "data" / "prompt_senales.txt").write_text(
        PROMPT_HEADER + "\n" + "\n\n".join(prompts_sig) if prompts_sig else "Hoy no hay señales.\n", encoding="utf-8")
    (out / "data" / "prompt_revision.txt").write_text(
        REVIEW_HEADER + "\n" + "\n\n".join(prompts_rev) if prompts_rev else "No hay posiciones abiertas.\n", encoding="utf-8")
    pd.DataFrame([{k: v for k, v in r.items() if k not in ("bands", "other_classes", "excluded")}
                  | {f"banda_{k}": v for k, v in r["bands"].items()} | {"descartes": "; ".join(r["excluded"])}
                  for r in rows_sorted]).to_csv(out / "data" / "resumen.csv", index=False)
    if cfg.get("custom_domain"):
        (out / "CNAME").write_text(cfg["custom_domain"].strip() + "\n")
    (out / ".nojekyll").write_text("")
    log(f"[ok] portal generado en {out} · {len(alerts)} alertas")


if __name__ == "__main__":
    main()
