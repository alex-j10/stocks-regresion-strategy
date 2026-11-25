"""
Cotizaciones diarias de Yahoo Finance (cierre y máximo).

Se usan precios ajustados por splits pero NO por dividendos (auto_adjust=False),
que es lo que muestra TradingView por defecto. Así el canal coincide con el
que ves en tu gráfico.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import pandas as pd
import yfinance as yf


def download_prices(tickers: list[str], years: int, batch: int = 40, log=print) -> dict[str, pd.DataFrame]:
    """Devuelve {ticker: DataFrame con columnas close, high} en orden cronológico."""
    start = date.today() - timedelta(days=int(years * 365.25) + 10)
    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers), batch):
        chunk = tickers[i:i + batch]
        df = None
        for attempt in range(3):
            try:
                df = yf.download(chunk, start=start.isoformat(), interval="1d",
                                 auto_adjust=False, group_by="ticker", threads=True,
                                 progress=False)
                break
            except Exception as e:  # noqa: BLE001
                log(f"[precios] reintento {attempt + 1} lote {i}: {e}")
                time.sleep(10 * (attempt + 1))
        if df is None:
            continue
        for t in chunk:
            try:
                sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                d = pd.DataFrame({"close": sub["Close"], "high": sub["High"]}).dropna(subset=["close"])
            except KeyError:
                continue
            if len(d):
                d.index = pd.to_datetime(d.index).tz_localize(None)
                d["high"] = d["high"].fillna(d["close"])
                out[t] = d.astype(float)
        log(f"[precios] {min(i + batch, len(tickers))}/{len(tickers)}")
        time.sleep(1)
    return out
