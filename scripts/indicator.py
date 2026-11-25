"""
Canal de desviación estándar: réplica del indicador de TradingView
"Standard Deviation Channel" (Pine v5) que usa el portal.

Comportamiento idéntico al script de Pine:
- Regresión lineal por mínimos cuadrados del cierre contra el índice de barra,
  sobre las últimas `length` barras.
- Desviación típica = sqrt( sum(residuo^2) / (length - 1) ), con residuo
  = cierre - valor de la recta en esa barra.
- Bandas = recta ± multiplicador * desviación típica.
- Pearson's R calculado igual que en el script (correlación precio vs recta).

Convención de Pine: el índice 0 es la barra más reciente. `end_price` es el
valor de la recta en la última barra y `start_price` en la primera del canal.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np


@dataclass
class Channel:
    length: int
    slope_per_bar: float      # pendiente en sentido cronológico (por barra hacia delante)
    start_price: float        # recta en la barra más antigua del canal
    end_price: float          # recta en la barra actual
    std_dev: float
    pearson_r: float
    last_close: float
    sigma: float              # (cierre - recta) / desviación típica

    def band(self, mult: float, at_end: bool = True) -> float:
        base = self.end_price if at_end else self.start_price
        return base + mult * self.std_dev

    def to_dict(self) -> dict:
        return asdict(self)


def pine_reference(closes_chrono, length: int):
    """Traducción literal, bucle a bucle, del script de Pine. Solo para tests."""
    src = list(closes_chrono)[::-1]  # src[0] = barra actual
    sumX = sumY = sumXSqr = sumXY = 0.0
    for i in range(length):
        val = src[i]
        per = i + 1.0
        sumX += per
        sumY += val
        sumXSqr += per * per
        sumXY += val * per
    slope = (length * sumXY - sumX * sumY) / (length * sumXSqr - sumX * sumX)
    average = sumY / length
    intercept = average - slope * sumX / length + slope

    stdDevAcc = dsxx = dsyy = dsxy = 0.0
    periods = length - 1
    daY = intercept + slope * periods / 2
    val = intercept
    for j in range(periods + 1):
        price = src[j]
        dxt = price - average
        dyt = val - daY
        price -= val
        stdDevAcc += price * price
        dsxx += dxt * dxt
        dsyy += dyt * dyt
        dsxy += dxt * dyt
        val += slope
    stdDev = (stdDevAcc / (periods if periods != 0 else 1)) ** 0.5
    pearsonR = 0 if dsxx == 0 or dsyy == 0 else dsxy / (dsxx * dsyy) ** 0.5
    st_price = intercept + slope * (length - 1)
    end_price = intercept
    return dict(slope=slope, average=average, intercept=intercept, std_dev=stdDev,
                pearson_r=pearsonR, start_price=st_price, end_price=end_price)


def std_dev_channel(closes_chrono, length: int) -> Channel:
    """Versión vectorizada (mismo resultado que pine_reference)."""
    y_all = np.asarray(closes_chrono, dtype=float)
    if length < 2 or len(y_all) < length:
        raise ValueError(f"Se necesitan al menos {length} cierres (hay {len(y_all)})")
    src = y_all[-length:][::-1]               # src[0] = barra actual
    per = np.arange(1, length + 1, dtype=float)

    n = float(length)
    sumX, sumY = per.sum(), src.sum()
    sumXSqr, sumXY = (per * per).sum(), (src * per).sum()
    slope = (n * sumXY - sumX * sumY) / (n * sumXSqr - sumX * sumX)
    average = sumY / n
    intercept = average - slope * sumX / n + slope

    fitted = intercept + slope * np.arange(length)   # recta en src[j]
    resid = src - fitted
    periods = length - 1
    std_dev = float(np.sqrt((resid ** 2).sum() / (periods if periods else 1)))

    daY = intercept + slope * periods / 2
    dxt, dyt = src - average, fitted - daY
    dsxx, dsyy, dsxy = (dxt ** 2).sum(), (dyt ** 2).sum(), (dxt * dyt).sum()
    pearson = 0.0 if dsxx == 0 or dsyy == 0 else float(dsxy / np.sqrt(dsxx * dsyy))

    end_price = float(intercept)
    start_price = float(intercept + slope * (length - 1))
    last = float(src[0])
    sigma = (last - end_price) / std_dev if std_dev > 0 else 0.0
    return Channel(length=length, slope_per_bar=float(-slope), start_price=start_price,
                   end_price=end_price, std_dev=std_dev, pearson_r=pearson,
                   last_close=last, sigma=float(sigma))


def rolling_channel(closes_chrono, length: int):
    """
    Canal calculado para CADA día, con las `length` barras anteriores
    (lo que verías con el replay de TradingView en esa fecha).
    Devuelve arrays del mismo tamaño que `closes` (NaN antes de tener `length` barras):
      base  = recta en ese día
      std   = desviación típica de los residuos
      sigma = (cierre - recta) / std
    Usa sumas acumuladas: O(n) en lugar de O(n·length).
    """
    y = np.asarray(closes_chrono, dtype=float)
    n, L = len(y), length
    base = np.full(n, np.nan); std = np.full(n, np.nan); sigma = np.full(n, np.nan)
    if n < L or L < 2:
        return base, std, sigma
    g = np.arange(n, dtype=float)
    c_y = np.concatenate([[0.0], np.cumsum(y)])
    c_yy = np.concatenate([[0.0], np.cumsum(y * y)])
    c_gy = np.concatenate([[0.0], np.cumsum(g * y)])
    t = np.arange(L - 1, n)              # último índice de cada ventana
    s = t - L + 1                        # primer índice
    Sy = c_y[t + 1] - c_y[s]
    Syy = c_yy[t + 1] - c_yy[s]
    Sxy = (c_gy[t + 1] - c_gy[s]) - s * Sy   # x local = g - s
    Sx = L * (L - 1) / 2.0
    Sxx = (L - 1) * L * (2 * L - 1) / 6.0
    b = (L * Sxy - Sx * Sy) / (L * Sxx - Sx * Sx)
    a = (Sy - b * Sx) / L
    ssr = np.maximum(Syy - a * Sy - b * Sxy, 0.0)
    sd = np.sqrt(ssr / (L - 1))
    end = a + b * (L - 1)
    base[t], std[t] = end, sd
    with np.errstate(divide="ignore", invalid="ignore"):
        sigma[t] = np.where(sd > 0, (y[t] - end) / sd, 0.0)
    return base, std, sigma
