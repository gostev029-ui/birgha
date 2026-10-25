import os, asyncio, time, math
from typing import Optional
import numpy as np
import pandas as pd
import yfinance as yf
from maxapi import Bot, Dispatcher

BOT_TOKEN = os.environ["MAX_BOT_TOKEN"]

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL = 900  # 15 минут


def _cache_get(key: str) -> Optional[str]:
    item = _CACHE.get(key)
    if item and (time.time() - item[0]) < _CACHE_TTL:
        return item[1]
    return None


def _cache_set(key: str, value: str) -> None:
    _CACHE[key] = (time.time(), value)


# ---------- Индикаторы (быстрые, на numpy/pandas) ----------
def _rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    val = rsi.iloc[-1]
    return float(val) if pd.notna(val) else float("nan")


def _macd(close: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1]), float(hist.iloc[-1])


def _bollinger(close: pd.Series, period=20, num_std=2):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return float(upper.iloc[-1]), float(mid.iloc[-1]), float(lower.iloc[-1])


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return float(atr) if pd.notna(atr) else float("nan")


def _forecast(close: pd.Series, horizon: int):
    log_ret = np.log(close / close.shift(1)).dropna()
    if len(log_ret) < 20:
        return None
    mu = float(log_ret.mean())
    sigma = float(log_ret.std())
    last = float(close.iloc[-1])
    fc = last * math.exp(mu * horizon)
    lo = last * math.exp(mu * horizon - 1.96 * sigma * math.sqrt(horizon))
    hi = last * math.exp(mu * horizon + 1.96 * sigma * math.sqrt(horizon))
    return {"forecast": fc, "lower": lo, "upper": hi}


# ---------- Загрузка: Ticker.history вместо download ----------
def _fetch(ticker: str) -> Optional[pd.DataFrame]:
    try:
        t = yf.Ticker(ticker)
        df = t.history(period="1y", interval="1d", auto_adjust=True)
        if df is None or df.empty:
            return None
        return df
    except Exception:
        return None


# ---------- Отчёт ----------
def _build_report(ticker: str, df: pd.DataFrame) -> str:
    close = df["Close"].squeeze()
    if len(close) < 30:
        return f"📊 {ticker.upper()}\nНедостаточно данных для анализа."

    last = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    chg_day = (last / prev - 1) * 100

    def chg(days: int):
        if len(close) <= days:
            return None
        return (last / float(close.iloc[-days - 1]) - 1) * 100

    lines = [f"📊 {ticker.upper()}", f"Цена: {last:.2f} ({chg_day:+.2f}% за день)"]
    for label, days in [("неделю", 5), ("месяц", 21), ("год", 252)]:
        v = chg(days)
        if v is not None:
            lines.append(f"  за {label}: {v:+.2f}%")

    rsi = _rsi(close)
    if pd.notna(rsi):
        zone = "перекуплен" if rsi > 70 else "перепродан" if rsi < 30 else "нейтрален"
        lines.append(f"\nRSI(14): {rsi:.1f} ({zone})")

    macd, sig, hist = _macd(close)
    trend = "бычий" if hist > 0 else "медвежий"
    lines.append(f"MACD hist: {hist:+.3f} ({trend})")

    up, mid, low = _bollinger(close)
    pos = "у верхней" if last > up else "у нижней" if last < low else "внутри"
    lines.append(f"Bollinger: {low:.2f} — {mid:.2f} — {up:.2f} (цена {pos})")

    atr = _atr(df)
    if pd.notna(atr):
        lines.append(f"ATR(14): {atr:.2f} ({atr / last * 100:.2f}% от цены)")

    log_ret = np.log(close / close.shift(1)).dropna()
    vol_annual = float(log_ret.std() * math.sqrt(252) * 100)
    lines.append(f"Волатильность (годовая): {vol_annual:.1f}%")

    sma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
    sma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None
    if pd.notna(sma50):
        lines.append(f"SMA50: {sma50:.2f}")
    if pd.notna(sma200):
        lines.append(f"SMA200: {sma200:.2f}")
    if pd.notna(sma50) and pd.notna(sma200):
        cross = "золотой крест" if sma50 > sma200 else "мёртвый крест"
        lines.append(f"  {cross}")

    year = close.tail(252)
    y_low, y_high = float(year.min()), float(year.max())
    if y_high > y_low:
        pos_pct = (last - y_low) / (y_high - y_low) * 100
        lines.append(f"\n52-нед. диапазон: {y_low:.2f} — {y_high:.2f} (позиция {pos_pct:.0f}%)")

    if "Volume" in df.columns:
        vol = df["Volume"].squeeze()
        if len(vol) >= 20:
            avg20 = float(vol.tail(20).mean())
            last_vol = float(vol.iloc[-1])
            if avg20 > 0:
                lines.append(f"Объём: {last_vol:,.0f} ({last_vol / avg20:.1f}× среднего за 20 дн.)")

    recent = close.tail(20)
    lines.append(f"Поддержка/сопротивление (20 дн.): {float(recent.min()):.2f} / {float(recent.max()):.2f}")

    lines.append("\n📈 Прогноз")
    for h in (1, 5, 20):
        f = _forecast(close, h)
        if f:
            lines.append(f"  {h} дн.: {f['forecast']:.2f} [{f['lower']:.2f} — {f['upper']:.2f}]")

    lines.append("\n⚠️ Не инвестиционная рекомендация.")
    return "\n".join(lines)


# ---------- Асинхронный анализ ----------
async def analyze_async(tickers: list[str]) -> str:
    key = ",".join(sorted(tickers))
    cached = _cache_get(key)
    if cached:
        return cached

    # Параллельная загрузка — все тикеры одновременно
    dfs = await asyncio.gather(
        *[asyncio.to_thread(_fetch, t) for t in tickers],
        return_exceptions=True,
    )

    parts = []
    for t, df in zip(tickers, dfs):
        if isinstance(df, Exception) or df is None:
            parts.append(f"📊 {t.upper()}\nТикер не найден или недоступен.")
        else:
            parts.append(_build_report(t, df))

    result = "\n\n———\n\n".join(parts)
    _cache_set(key, result)
    return result


# ---------- Хендлеры ----------
@dp.message_created()
async def on_message(event):
    text = (event.message.body.text or "").strip()

    if text == "/start":
        await event.message.answer(
            "Привет! Пришлите тикер — сразу покажу расширенную аналитику.\n"
            "Примеры: AAPL, MSFT, TSLA\n"
            "Можно несколько через запятую: AAPL, MSFT"
        )
        return

    if not text or text.startswith("/"):
        return

    raw = text.replace(";", ",").replace(" ", ",")
    tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]
    tickers = list(dict.fromkeys(tickers))[:5]

    try:
        # Жёсткий таймаут 20 секунд — бот не висит
        result = await asyncio.wait_for(analyze_async(tickers), timeout=20)
    except asyncio.TimeoutError:
        result = "Анализ занял слишком долго — Yahoo Finance не ответил. Попробуйте позже."
    except Exception as e:
        result = f"Ошибка при анализе: {e}"

    await event.message.answer(result)


if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))