import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from indicator import pine_reference, std_dev_channel  # noqa: E402


def test_matches_pine_loop():
    rng = np.random.default_rng(7)
    for _ in range(20):
        n = int(rng.integers(300, 3000))
        prices = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, n)))
        length = int(rng.integers(50, n))
        ref = pine_reference(prices, length)
        ch = std_dev_channel(prices, length)
        for k in ("std_dev", "pearson_r", "start_price", "end_price"):
            assert np.isclose(getattr(ch, k), ref[k], rtol=1e-9, atol=1e-9), k
        # el último cierre está a sigma desviaciones de la recta
        assert np.isclose(prices[-1], ch.end_price + ch.sigma * ch.std_dev)
        # la recta va de start a end con la pendiente por barra
        assert np.isclose(ch.start_price + ch.slope_per_bar * (length - 1), ch.end_price)


def test_perfect_line():
    prices = np.arange(1, 501, dtype=float)
    ch = std_dev_channel(prices, 500)
    assert np.isclose(ch.end_price, 500) and np.isclose(ch.start_price, 1)
    assert ch.std_dev < 1e-9


if __name__ == "__main__":
    test_matches_pine_loop()
    test_perfect_line()
    print("OK: el canal coincide con el script de Pine")


def test_rolling_matches_static():
    from indicator import rolling_channel
    rng = np.random.default_rng(3)
    prices = 300 * np.exp(np.cumsum(rng.normal(0.0004, 0.018, 3400)))
    L = 2500
    base, std, sig = rolling_channel(prices, L)
    assert np.isnan(base[L - 2]) and not np.isnan(base[L - 1])
    for end in (L, 2700, 3000, 3399):
        ch = std_dev_channel(prices[:end], L)
        assert np.isclose(base[end - 1], ch.end_price, rtol=1e-7)
        assert np.isclose(std[end - 1], ch.std_dev, rtol=1e-6)
        assert np.isclose(sig[end - 1], ch.sigma, rtol=1e-6, atol=1e-6)


if __name__ == "__main__":
    test_rolling_matches_static()
    print("OK: canal móvil coincide con el canal del día")
