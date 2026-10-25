import os, sys, time
import requests

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")
TWELVEDATA_KEY = os.environ.get("TWELVEDATA_API_KEY", "")
TIINGO_KEY = os.environ.get("TIINGO_API_KEY", "")
MASSIVE_KEY = os.environ.get("MASSIVE_API_KEY", "")
FMP_KEY = os.environ.get("FMP_API_KEY", "")
ALPACA_KEY = os.environ.get("ALPACA_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET", "")


def timed(fn, *args):
    t0 = time.time()
    try:
        r = fn(*args)
        return r, time.time() - t0, None
    except Exception as e:
        return None, time.time() - t0, repr(e)


def check_finnhub(t):
    if not FINNHUB_KEY: return "SKIP (нет ключа)"
    r = requests.get("https://finnhub.io/api/v1/quote",
                     params={"symbol": t, "token": FINNHUB_KEY}, timeout=5)
    js = r.json()
    return f"OK цена={js['c']}" if js.get("c") else f"FAIL {js}"


def check_twelvedata(t):
    if not TWELVEDATA_KEY: return "SKIP (нет ключа)"
    r = requests.get("https://api.twelvedata.com/time_series",
                     params={"symbol": t, "interval": "1day",
                             "outputsize": 30, "apikey": TWELVEDATA_KEY}, timeout=8)
    js = r.json()
    return f"OK {len(js['values'])} точек" if "values" in js else f"FAIL {js}"


def check_tiingo(t):
    if not TIINGO_KEY: return "SKIP (нет ключа)"
    r = requests.get(f"https://api.tiingo.com/tiingo/daily/{t}/prices",
                     params={"startDate": "2025-01-01", "token": TIINGO_KEY}, timeout=8)
    js = r.json()
    return f"OK {len(js)} точек" if isinstance(js, list) and js else f"FAIL {js}"


def check_massive(t):
    if not MASSIVE_KEY: return "SKIP (нет ключа)"
    r = requests.get(f"https://api.massive.com/v2/aggs/ticker/{t}/range/1/day/2025-01-01/2026-12-31",
                     params={"adjusted": "true", "limit": 10},
                     headers={"Authorization": f"Bearer {MASSIVE_KEY}"}, timeout=8)
    js = r.json()
    return f"OK {len(js['results'])} точек" if js.get("results") else f"FAIL {js}"


def check_fmp(t):
    if not FMP_KEY: return "SKIP (нет ключа)"
    r = requests.get(f"https://financialmodelingprep.com/api/v3/historical-price-full/{t}",
                     params={"apikey": FMP_KEY, "from": "2025-01-01"}, timeout=8)
    js = r.json()
    hist = js.get("historical")
    return f"OK {len(hist)} точек" if hist else f"FAIL {js}"


def check_alpaca(t):
    if not ALPACA_KEY or not ALPACA_SECRET: return "SKIP (нет ключей)"
    r = requests.get(f"https://data.alpaca.markets/v2/stocks/{t}/bars",
                     params={"timeframe": "1Day", "start": "2025-01-01", "feed": "iex"},
                     headers={"APCA-API-KEY-ID": ALPACA_KEY,
                              "APCA-API-SECRET-KEY": ALPACA_SECRET}, timeout=8)
    js = r.json()
    bars = js.get("bars")
    return f"OK {len(bars)} баров" if bars else f"FAIL {js}"


def check_moex(t):
    secid = t.replace(".ME", "").upper()
    r = requests.get(
        f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{secid}/candles.json",
        params={"from": "2025-01-01", "interval": 24, "limit": 30}, timeout=8)
    data = r.json().get("candles", {}).get("data", [])
    return f"OK {len(data)} точек" if data else "FAIL пусто (тикер есть на MOEX?)"


CHECKS = [
    ("MOEX ISS", check_moex),
    ("Finnhub", check_finnhub),
    ("Twelve Data", check_twelvedata),
    ("Tiingo", check_tiingo),
    ("Massive/Polygon", check_massive),
    ("FMP", check_fmp),
    ("Alpaca", check_alpaca),
]

tickers = sys.argv[1:] or ["AAPL", "SBER"]
for t in tickers:
    print(f"\n===== {t} =====")
    for name, fn in CHECKS:
        result, dt, err = timed(fn, t)
        status = result if err is None else f"EXC {err}"
        print(f"  {name:18s}  {dt*1000:6.0f} мс  →  {status}")