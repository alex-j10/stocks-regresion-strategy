"""
Universo: acciones cotizadas en EE. UU. con capitalización >= mínimo,
sin repetir empresa (GOOG/GOOGL, BRK-A/BRK-B... se quedan en una).

Fuente principal: screener de Nasdaq (NYSE + NASDAQ + AMEX, solo acciones).
Deduplicación: CIK de la SEC (un CIK = una empresa). Si un ticker no tiene
CIK, se agrupa por nombre normalizado.
Fuente de respaldo: componentes del S&P 500 + capitalización de Yahoo.
"""
from __future__ import annotations

import io
import os
import re
import time

import requests

UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
SEC_UA = os.environ.get("SEC_USER_AGENT") or "portal-bandas contacto@example.com"

ADR_PAT = re.compile(r"american depositary|depositary shares|\bads\b|\badr\b|new york registry", re.I)
NAME_NOISE = re.compile(
    r"\b(class [a-z]|series [a-z]|common stock|capital stock|ordinary shares?|common shares?|"
    r"subordinate voting shares|voting shares|shares|stock|\(the\)|new)\b", re.I)


def norm_ticker(sym: str) -> str:
    return sym.strip().upper().replace("/", "-").replace(".", "-")


def norm_name(name: str) -> str:
    n = NAME_NOISE.sub(" ", name.lower())
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def _get(url, headers, tries=3, **kw):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=headers, timeout=60, **kw)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"No se pudo descargar {url}: {last}")


def _num(x):
    try:
        return float(str(x).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None


def sec_cik_map() -> dict[str, int]:
    r = _get("https://www.sec.gov/files/company_tickers.json", {"User-Agent": SEC_UA})
    return {norm_ticker(v["ticker"]): int(v["cik_str"]) for v in r.json().values()}


def nasdaq_rows() -> list[dict]:
    url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&download=true"
    r = _get(url, {"User-Agent": UA_BROWSER, "Accept": "application/json"})
    return r.json()["data"]["rows"]


def from_nasdaq(cfg: dict) -> list[dict]:
    rows = nasdaq_rows()
    out = []
    for row in rows:
        sym = (row.get("symbol") or "").strip()
        if not sym or "^" in sym or sym.endswith(("/WS", "/U", "/R")):
            continue  # preferentes, warrants, units, rights
        mcap = _num(row.get("marketCap"))
        if not mcap or mcap < cfg["min_market_cap_usd"]:
            continue
        name = row.get("name") or sym
        if cfg.get("exclude_adrs") and ADR_PAT.search(name):
            continue
        if cfg.get("only_us_country") and (row.get("country") or "") != "United States":
            continue
        price, vol = _num(row.get("lastsale")), _num(row.get("volume"))
        out.append(dict(ticker=norm_ticker(sym), name=name, market_cap=mcap,
                        sector=row.get("sector") or "", industry=row.get("industry") or "",
                        country=row.get("country") or "",
                        dollar_volume=(price or 0) * (vol or 0)))
    return out


def from_sp500(cfg: dict) -> list[dict]:
    """Respaldo si el screener de Nasdaq no responde."""
    import pandas as pd
    import yfinance as yf

    html = _get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                {"User-Agent": UA_BROWSER}).text
    df = pd.read_html(io.StringIO(html))[0]
    out = []
    for _, r in df.iterrows():
        t = norm_ticker(str(r["Symbol"]))
        try:
            fi = yf.Ticker(t).fast_info
            mcap = float(fi["marketCap"])
            dv = float(fi["lastPrice"]) * float(fi.get("tenDayAverageVolume") or 0)
        except Exception:  # noqa: BLE001
            continue
        if mcap >= cfg["min_market_cap_usd"]:
            out.append(dict(ticker=t, name=str(r["Security"]), market_cap=mcap,
                            sector=str(r.get("GICS Sector", "")),
                            industry=str(r.get("GICS Sub-Industry", "")),
                            country="United States", dollar_volume=dv))
    return out


def dedupe(rows: list[dict], cik_map: dict[str, int]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        cik = cik_map.get(r["ticker"])
        r["cik"] = cik
        key = f"cik:{cik}" if cik else f"name:{norm_name(r['name'])}"
        groups.setdefault(key, []).append(r)
    out = []
    for members in groups.values():
        best = max(members, key=lambda m: (m["dollar_volume"], m["market_cap"]))
        best = dict(best)
        best["other_classes"] = sorted(m["ticker"] for m in members if m is not best and m["ticker"] != best["ticker"])
        out.append(best)
    return sorted(out, key=lambda r: -r["market_cap"])


def build_universe(cfg: dict, log=print) -> tuple[list[dict], str]:
    try:
        rows, source = from_nasdaq(cfg), "Nasdaq screener"
        if len(rows) < 50:
            raise RuntimeError(f"solo {len(rows)} filas")
    except Exception as e:  # noqa: BLE001
        log(f"[universo] Nasdaq falló ({e}); uso S&P 500 como respaldo")
        rows, source = from_sp500(cfg), "S&P 500 (respaldo)"
    try:
        cik_map = sec_cik_map()
    except Exception as e:  # noqa: BLE001
        log(f"[universo] SEC no disponible ({e}); deduplico por nombre")
        cik_map = {}
    uni = dedupe(rows, cik_map)
    log(f"[universo] {len(rows)} tickers -> {len(uni)} empresas únicas ({source})")
    return uni, source
