"""
Métricas para las empresas filtradas (≤ -3σ):
  1) Riesgo de quiebra: Altman Z, deuda neta/EBITDA, cobertura de intereses,
     flujo de caja libre, liquidez corriente.
  2) ¿Caída temporal o estructural?: tendencia de ventas y márgenes,
     contexto sectorial, velocidad de la caída, previsiones de analistas y
     titulares recientes.

No se emite veredicto final: las métricas y el texto para la IA se generan
para que el análisis lo haga Alex con su IA.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

FINANCIAL_SECTORS = {"finance", "financial services", "financials"}


def _row(df: pd.DataFrame | None, names: list[str]) -> pd.Series | None:
    if df is None or df.empty:
        return None
    for n in names:
        if n in df.index:
            s = df.loc[n]
            s = pd.to_numeric(s, errors="coerce").dropna()
            if len(s):
                return s.sort_index()  # cronológico
    return None


def _last(df, names):
    s = _row(df, names)
    return float(s.iloc[-1]) if s is not None else None


def _ttm(qdf, names):
    s = _row(qdf, names)
    if s is None or len(s) < 4:
        return None
    return float(s.iloc[-4:].sum())


def _ttm_prev(qdf, names):
    s = _row(qdf, names)
    if s is None or len(s) < 8:
        return None
    return float(s.iloc[-8:-4].sum())


def _div(a, b):
    try:
        if a is None or b in (None, 0) or (isinstance(b, float) and math.isnan(b)):
            return None
        r = a / b
        return None if math.isnan(r) or math.isinf(r) else r
    except (TypeError, ZeroDivisionError):
        return None


def _clean(x):
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return x


def altman_z(wc, re_, ebit, mve, tl, sales, ta):
    parts = [_div(wc, ta), _div(re_, ta), _div(ebit, ta), _div(mve, tl), _div(sales, ta)]
    if any(p is None for p in parts):
        return None
    return 1.2 * parts[0] + 1.4 * parts[1] + 3.3 * parts[2] + 0.6 * parts[3] + 1.0 * parts[4]


def _news(t: yf.Ticker, limit: int) -> list[dict]:
    out = []
    try:
        items = t.news or []
    except Exception:  # noqa: BLE001
        return out
    for it in items[:limit]:
        c = it.get("content", it)
        title = c.get("title")
        if not title:
            continue
        prov = c.get("provider")
        pub = (prov or {}).get("displayName") if isinstance(prov, dict) else c.get("publisher")
        when = c.get("pubDate") or c.get("displayTime")
        if not when and it.get("providerPublishTime"):
            when = datetime.fromtimestamp(it["providerPublishTime"], tz=timezone.utc).isoformat()
        url = (c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict) else c.get("link")
        out.append(dict(title=title, publisher=pub, date=(when or "")[:10], url=url))
    return out


def analyze(ticker: str, sector: str, closes: pd.Series, sector_median_sigma: float | None,
            max_news: int = 8) -> dict:
    t = yf.Ticker(ticker)
    info = {}
    try:
        info = t.info or {}
    except Exception:  # noqa: BLE001
        pass

    def safe(attr):
        try:
            return getattr(t, attr)
        except Exception:  # noqa: BLE001
            return None

    bs, inc, cf = safe("balance_sheet"), safe("financials"), safe("cashflow")
    qinc, qcf = safe("quarterly_financials"), safe("quarterly_cashflow")

    REV = ["Total Revenue", "Operating Revenue"]
    EBIT = ["EBIT", "Operating Income"]
    mcap = info.get("marketCap")
    revenue_ttm = _ttm(qinc, REV) or _last(inc, REV)
    revenue_prev = _ttm_prev(qinc, REV)
    ebit_ttm = _ttm(qinc, EBIT) or _last(inc, EBIT)
    ebitda = info.get("ebitda") or _ttm(qinc, ["EBITDA", "Normalized EBITDA"]) or _last(inc, ["EBITDA"])
    interest = _ttm(qinc, ["Interest Expense"]) or _last(inc, ["Interest Expense"])
    fcf_ttm = _ttm(qcf, ["Free Cash Flow"]) or _last(cf, ["Free Cash Flow"])

    ta = _last(bs, ["Total Assets"])
    tl = _last(bs, ["Total Liabilities Net Minority Interest", "Total Liab"])
    ca, cl = _last(bs, ["Current Assets"]), _last(bs, ["Current Liabilities"])
    wc = _last(bs, ["Working Capital"]) or (ca - cl if ca is not None and cl is not None else None)
    re_ = _last(bs, ["Retained Earnings"])
    debt = info.get("totalDebt") or _last(bs, ["Total Debt"])
    cash = info.get("totalCash") or _last(bs, ["Cash Cash Equivalents And Short Term Investments",
                                               "Cash And Cash Equivalents"])
    net_debt = (debt - cash) if debt is not None and cash is not None else None

    is_fin = (sector or info.get("sector") or "").strip().lower() in FINANCIAL_SECTORS
    z = None if is_fin else altman_z(wc, re_, ebit_ttm, mcap, tl, revenue_ttm, ta)

    # Tendencia anual de ventas y margen operativo
    rev_a, ebit_a = _row(inc, REV), _row(inc, EBIT)
    rev_hist, margin_hist = [], []
    if rev_a is not None:
        for d, v in rev_a.items():
            e = ebit_a.get(d) if ebit_a is not None else None
            rev_hist.append(dict(year=str(pd.Timestamp(d).year), revenue=float(v)))
            if e is not None and v:
                margin_hist.append(dict(year=str(pd.Timestamp(d).year), op_margin=float(e) / float(v)))
    rev_cagr = None
    if rev_a is not None and len(rev_a) >= 3 and rev_a.iloc[0] > 0 and rev_a.iloc[-1] > 0:
        yrs = len(rev_a) - 1
        rev_cagr = (rev_a.iloc[-1] / rev_a.iloc[0]) ** (1 / yrs) - 1
    op_margin_ttm = _div(ebit_ttm, revenue_ttm)
    prev_margins = [m["op_margin"] for m in margin_hist[:-1]]
    margin_vs_hist = (op_margin_ttm - sum(prev_margins) / len(prev_margins)) if prev_margins and op_margin_ttm is not None else None

    # Comportamiento del precio
    last = float(closes.iloc[-1])
    def ret(days):
        cut = closes.index[-1] - pd.Timedelta(days=days)
        prev = closes[closes.index <= cut]
        return last / float(prev.iloc[-1]) - 1 if len(prev) else None
    one_year = closes[closes.index > closes.index[-1] - pd.Timedelta(days=365)]
    high_52w = float(one_year.max()) if len(one_year) else None

    metrics = dict(
        is_financial=is_fin,
        quiebra=dict(
            altman_z=_clean(z),
            altman_zone=(None if z is None else "segura" if z > 2.99 else "gris" if z >= 1.81 else "peligro"),
            net_debt=_clean(net_debt), ebitda=_clean(ebitda),
            net_debt_to_ebitda=_clean(_div(net_debt, ebitda)) if ebitda and ebitda > 0 else None,
            interest_coverage=_clean(_div(ebit_ttm, abs(interest) if interest else None)),
            free_cash_flow_ttm=_clean(fcf_ttm),
            current_ratio=_clean(info.get("currentRatio") or _div(ca, cl)),
            debt_to_equity=_clean(info.get("debtToEquity")),
            credit_note=("Altman Z no aplica a bancos/aseguradoras: revisar capital (CET1), "
                         "calidad de activos y liquidez" if is_fin else None),
        ),
        temporal=dict(
            revenue_ttm=_clean(revenue_ttm),
            revenue_growth_ttm=_clean(_div(revenue_ttm - revenue_prev, revenue_prev)) if revenue_ttm and revenue_prev else None,
            revenue_cagr_annual=_clean(rev_cagr),
            op_margin_ttm=_clean(op_margin_ttm),
            op_margin_vs_hist_pp=_clean(margin_vs_hist),
            revenue_history=rev_hist, margin_history=margin_hist,
            sector_median_sigma=_clean(sector_median_sigma),
            return_3m=_clean(ret(91)), return_12m=_clean(ret(365)),
            drawdown_from_52w_high=_clean(last / high_52w - 1) if high_52w else None,
            trailing_pe=_clean(info.get("trailingPE")), forward_pe=_clean(info.get("forwardPE")),
            analyst_recommendation=_clean(info.get("recommendationMean")),
            analyst_count=info.get("numberOfAnalystOpinions"),
            target_mean_upside=_clean(_div(info.get("targetMeanPrice"), last) - 1) if info.get("targetMeanPrice") else None,
        ),
        business_summary=info.get("longBusinessSummary"),
        website=info.get("website"),
        news=_news(t, max_news),
    )
    metrics["flags"] = flags(metrics)
    return metrics


def flags(m: dict) -> dict:
    """Semáforo orientativo (no es una recomendación)."""
    q, tp = m["quiebra"], m["temporal"]
    red, green = [], []
    if q["altman_zone"] == "peligro": red.append("Altman Z en zona de peligro (<1,81)")
    elif q["altman_zone"] == "segura": green.append("Altman Z en zona segura (>2,99)")
    nde = q["net_debt_to_ebitda"]
    if nde is not None and nde > 4: red.append(f"Deuda neta/EBITDA alta ({nde:.1f}x)")
    if q["ebitda"] is not None and q["ebitda"] <= 0: red.append("EBITDA negativo")
    ic = q["interest_coverage"]
    if ic is not None and ic < 2: red.append(f"Cobertura de intereses baja ({ic:.1f}x)")
    if q["free_cash_flow_ttm"] is not None:
        (red if q["free_cash_flow_ttm"] < 0 else green).append(
            "Flujo de caja libre " + ("negativo" if q["free_cash_flow_ttm"] < 0 else "positivo"))
    cr = q["current_ratio"]
    if cr is not None and cr < 1: red.append(f"Liquidez corriente < 1 ({cr:.2f})")
    n = len(red)
    risk = "alto" if n >= 3 else "medio" if n >= 1 else "bajo"
    if m["is_financial"] and q["altman_z"] is None:
        risk = risk if n else "n/d (financiera)"

    sig = []
    g = tp["revenue_growth_ttm"]
    if g is not None: sig.append(("+" if g >= 0 else "−", f"Ventas TTM {g:+.1%} interanual"))
    mv = tp["op_margin_vs_hist_pp"]
    if mv is not None: sig.append(("+" if mv >= -0.03 else "−", f"Margen operativo {mv*100:+.1f} pp vs su histórico"))
    sm = tp["sector_median_sigma"]
    if sm is not None:
        sig.append(("+" if sm <= -1 else "−",
                     f"Sector en {sm:+.1f}σ (mediana)" + (" → caída sectorial/cíclica" if sm <= -1 else " → caída propia de la empresa")))
    tpe, fpe = tp["trailing_pe"], tp["forward_pe"]
    if tpe and fpe and tpe > 0 and fpe > 0:
        sig.append(("+" if fpe < tpe else "−", f"PER futuro {fpe:.1f} vs actual {tpe:.1f}"))
    return dict(riesgo_quiebra=risk, red=red, green=green, senales_temporal=sig)
