"""
ТикерСкан — бот биржевой аналитики для мессенджера MAX.
Источники данных: Yahoo Finance, MOEX ISS, Twelve Data, Finnhub.
"""
import os, asyncio, time, math
from typing import Optional
import numpy as np
import pandas as pd
import requests
import yfinance as yf
from curl_cffi import requests as cffi_requests
from maxapi import Bot, Dispatcher

# ---------- Конфигурация ----------
BOT_TOKEN = os.environ["MAX_BOT_TOKEN"]
TWELVEDATA_KEY = os.environ.get("TWELVEDATA_API_KEY", "")
FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

_SESSION = cffi_requests.Session(impersonate="chrome120")

_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL = 900  # 15 минут


def _cache_get(key: str) -> Optional[str]:
    item = _CACHE.get(key)
    if item and (time.time() - item[0]) < _CACHE_TTL:
        return item[1]
    return None


def _cache_set(key: str, value: str) -> None:
    _CACHE[key] = (time.time(), value)


# ---------- Провайдеры данных ----------
class YahooProvider:
    name = "yahoo"

    def _sync(self, ticker: str) -> Optional[pd.DataFrame]:
        try:
            t = yf.Ticker(ticker, session=_SESSION)
            df = t.history(period="1y", interval="1d", auto_adjust=True, timeout=6)
            return df if df is not None and not df.empty else None
        except Exception:
            return None

    async def fetch(self, ticker: str) -> Optional[pd.DataFrame]:
        return await asyncio.to_thread(self._sync, ticker)


class MoexProvider:
    name = "moex"
    BASE = "https://iss.moex.com/iss"

    def _sync(self, ticker: str) -> Optional[pd.DataFrame]:
        try:
            secid = ticker.replace(".ME", "").upper()
            url = f"{self.BASE}/engines/stock/markets/shares/boards/TQBR/securities/{secid}/candles.json"
            r = requests.get(url, params={"from": "2025-01-01", "interval": 24, "limit": 365}, timeout=6)
            data = r.json().get("candles", {}).get("data", [])
            if not data:
                return None
            cols = ["open", "close", "high", "low", "value", "volume", "begin", "end"]
            df = pd.DataFrame(data, columns=cols)
            df["begin"] = pd.to_datetime(df["begin"])
            df = df.set_index("begin").rename(columns={
                "open": "Open", "close": "Close", "high": "High",
                "low": "Low", "volume": "Volume",
            })
            return df[["Open", "High", "Low", "Close", "Volume"]]
        except Exception:
            return None

    async def fetch(self, ticker: str) -> Optional[pd.DataFrame]:
        return await asyncio.to_thread(self._sync, ticker)


class TwelveDataProvider:
    name = "twelvedata"
    BASE = "https://api.twelvedata.com/time_series"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _sync(self, ticker: str) -> Optional[pd.DataFrame]:
        if not self.api_key:
            return None
        try:
            params = {"symbol": ticker, "interval": "1day", "outputsize": 365,
                      "apikey": self.api_key, "format": "JSON"}
            js = requests.get(self.BASE, params=params, timeout=6).json()
            if "values" not in js:
                return None
            df = pd.DataFrame(js["values"])
            df["datetime"] = pd.to_datetime(df["datetime"])
            df = df.set_index("datetime").sort_index().rename(columns={
                "open": "Open", "high": "High", "low": "Low",
                "close": "Close", "volume": "Volume",
            })
            for c in ["Open", "High", "Low", "Close", "Volume"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            return df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        except Exception:
            return None

    async def fetch(self, ticker: str) -> Optional[pd.DataFrame]:
        return await asyncio.to_thread(self._sync, ticker)


class FinnhubQuoteProvider:
    """Быстрый резерв: только текущая цена, без истории."""
    name = "finnhub"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _sync(self, ticker: str) -> Optional[pd.DataFrame]:
        if not self.api_key:
            return None
        try:
            r = requests.get("https://finnhub.io/api/v1/quote",
                             params={"symbol": ticker, "token": self.api_key}, timeout=6)
            js = r.json()
            price, prev = js.get("c"), js.get("pc")
            if not price or price <= 0:
                return None
            idx = pd.to_datetime([pd.Timestamp.today() - pd.Timedelta(days=1), pd.Timestamp.today()])
            return pd.DataFrame({"Close": [prev, price], "Open": [prev, price],
                                 "High": [prev, price], "Low": [prev, price],
                                 "Volume": [0, 0]}, index=idx)
        except Exception:
            return None

    async def fetch(self, ticker: str) -> Optional[pd.DataFrame]:
        return await asyncio.to_thread(self._sync, ticker)


PROVIDERS = [YahooProvider(), MoexProvider()]
if TWELVEDATA_KEY:
    PROVIDERS.append(TwelveDataProvider(TWELVEDATA_KEY))
if FINNHUB_KEY:
    PROVIDERS.append(FinnhubQuoteProvider(FINNHUB_KEY))


async def fetch_any(ticker: str) -> tuple[Optional[pd.DataFrame], str]:
    """Параллельный опрос всех провайдеров — берём первый успешный."""
    tasks = {asyncio.create_task(p.fetch(ticker)): p.name for p in PROVIDERS}
    pending = set(tasks)
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                try:
                    df = task.result()
                except Exception:
                    continue
                if df is not None and not df.empty:
                    for p in pending:
                        p.cancel()
                    return df, tasks[task]
        return None, "none"
    finally:
        for p in pending:
            p.cancel()


# ---------- Индикаторы ----------
def _rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    val = (100 - 100 / (1 + rs)).iloc[-1]
    return float(val) if pd.notna(val) else float("nan")


def _macd(close: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return float((macd_line - signal_line).iloc[-1])


def _bollinger(close: pd.Series, period=20, num_std=2):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    return (float((mid + num_std * std).iloc[-1]),
            float(mid.iloc[-1]),
            float((mid - num_std * std).iloc[-1]))


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    val = tr.rolling(period).mean().iloc[-1]
    return float(val) if pd.notna(val) else float("nan")


def _forecast(close: pd.Series, horizon: int):
    log_ret = np.log(close / close.shift(1)).dropna()
    if len(log_ret) < 20:
        return None
    mu, sigma = float(log_ret.mean()), float(log_ret.std())
    last = float(close.iloc[-1])
    return {
        "forecast": last * math.exp(mu * horizon),
        "lower": last * math.exp(mu * horizon - 1.96 * sigma * math.sqrt(horizon)),
        "upper": last * math.exp(mu * horizon + 1.96 * sigma * math.sqrt(horizon)),
    }


# ---------- Отчёт ----------
def _build_report(ticker: str, df: pd.DataFrame, source: str) -> str:
    close = df["Close"].squeeze()
    if len(close) < 5:
        return (f"📊 {ticker.upper()}  ·  {source}\n"
                f"Цена: {float(close.iloc[-1]):.2f}\n"
                f"Мало истории для полного анализа.")

    last = float(close.iloc[-1])
    chg_day = (last / float(close.iloc[-2]) - 1) * 100
    lines = [f"📊 {ticker.upper()}  ·  {source}",
             f"Цена: {last:.2f} ({chg_day:+.2f}% за день)"]

    if len(close) >= 30:
        for label, days in [("неделю", 5), ("месяц", 21), ("год", 252)]:
            if len(close) > days:
                v = (last / float(close.iloc[-days - 1]) - 1) * 100
                lines.append(f"  за {label}: {v:+.2f}%")

    rsi = _rsi(close)
    if pd.notna(rsi):
        zone = "перекуплен" if rsi > 70 else "перепродан" if rsi < 30 else "нейтрален"
        lines.append(f"\nRSI(14): {rsi:.1f} ({zone})")

    if len(close) >= 26:
        hist = _macd(close)
        lines.append(f"MACD hist: {hist:+.3f} ({'бычий' if hist > 0 else 'медвежий'})")

    if len(close) >= 20:
        up, mid, low = _bollinger(close)
        lines.append(f"Bollinger: {low:.2f} — {mid:.2f} — {up:.2f}")

    atr = _atr(df)
    if pd.notna(atr) and last:
        lines.append(f"ATR(14): {atr:.2f} ({atr / last * 100:.2f}% от цены)")

    if len(close) >= 30:
        log_ret = np.log(close / close.shift(1)).dropna()
        lines.append(f"Волатильность (годовая): {float(log_ret.std() * math.sqrt(252) * 100):.1f}%")

    if len(close) >= 200:
        sma50 = close.rolling(50).mean().iloc[-1]
        sma200 = close.rolling(200).mean().iloc[-1]
        if pd.notna(sma50) and pd.notna(sma200):
            cross = "золотой" if sma50 > sma200 else "мёртвый"
            lines.append(f"SMA50: {sma50:.2f}  ·  SMA200: {sma200:.2f}  ({cross} крест)")

    if "Volume" in df.columns and len(close) >= 20:
        vol = df["Volume"].squeeze()
        avg20 = float(vol.tail(20).mean())
        last_vol = float(vol.iloc[-1])
        if avg20 > 0:
            lines.append(f"Объём: {last_vol:,.0f} ({last_vol / avg20:.1f}× среднего за 20 дн.)")

    if len(close) >= 20:
        recent = close.tail(20)
        lines.append(f"Поддержка/сопротивление: {float(recent.min()):.2f} / {float(recent.max()):.2f}")

    if len(close) >= 30:
        lines.append("\n📈 Прогноз")
        for h in (1, 5, 20):
            f = _forecast(close, h)
            if f:
                lines.append(f"  {h} дн.: {f['forecast']:.2f} [{f['lower']:.2f} — {f['upper']:.2f}]")

    lines.append("\n⚠️ Не инвестиционная рекомендация.")
    return "\n".join(lines)


async def analyze_async(tickers: list[str]) -> str:
    key = ",".join(sorted(tickers))
    cached = _cache_get(key)
    if cached:
        return cached

    parts = []
    for t in tickers:
        df, source = await fetch_any(t)
        if df is None:
            parts.append(f"📊 {t.upper()}\nНе удалось получить данные ни из одного источника.")
        else:
            parts.append(_build_report(t, df, source))

    result = "\n\n———\n\n".join(parts)
    _cache_set(key, result)
    return result


# ---------- Хендлеры ----------
@dp.message_created()
async def on_message(event):
    text = (event.message.body.text or "").strip()

    if text == "/start":
        await event.message.answer(
            "Привет! Пришлите тикер — покажу аналитику.\n"
            "Примеры: AAPL, MSFT, SBER, GAZP\n"
            "Можно несколько через запятую: AAPL, SBER"
        )
        return

    if not text or text.startswith("/"):
        return

    raw = text.replace(";", ",").replace(" ", ",")
    tickers = list(dict.fromkeys(
        t.strip().upper() for t in raw.split(",") if t.strip()
    ))[:5]

    await event.message.answer("⏳ Запрашиваю данные…")
    try:
        result = await asyncio.wait_for(analyze_async(tickers), timeout=15)
    except asyncio.TimeoutError:
        result = "Источники не ответили за 15 секунд. Попробуйте позже."
    except Exception as e:
        result = f"Ошибка: {e}"

    await event.message.answer(result)


async def _warmup():
    """Прогрев кэша популярных тикеров при старте."""
    try:
        await analyze_async(["AAPL", "SBER"])
        print("Кэш прогрет: AAPL, SBER")
    except Exception as e:
        print("Warmup error:", e)


if __name__ == "__main__":
    async def main():
        asyncio.create_task(_warmup())
        await dp.start_polling(bot)
    asyncio.run(main())