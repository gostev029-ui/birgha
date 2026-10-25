import os, asyncio, time, math
from typing import Optional
import numpy as np
import pandas as pd
import yfinance as yf
from maxapi import Bot, Dispatcher

BOT_TOKEN = os.environ["MAX_BOT_TOKEN"]

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ---------- TTL-кэш ----------
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 300  # 5 минут


def _cache_get(key: str):
    item = _CACHE.get(key)
    if not item:
        return None
    ts, value = item
    if time.time() - ts > _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: dict):
    _CACHE[key] = (time.time(), value)


# ---------- Индикаторы (чистый pandas/numpy, без pandas-ta) ----------
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
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return float(atr) if pd.notna(atr) else float("nan")


def _forecast(close: pd.Series, horizon: int):
    """Прогноз на лог-доходностях: корректнее арифметических."""
    log_ret = np.log(close / close.shift(1)).dropna()
    if len(log_ret) < 20:
        return None
    mu = float(log_ret.mean())
    sigma = float(log_ret.std())
    last = float(close.iloc[-1])
    fc = last * math.exp(mu * horizon)
    lo = last * math.exp(mu * horizon - 1.96 * sigma * math.sqrt(horizon))
    hi = last * math.exp(mu * horizon + 1.96 * sigma * math.sqrt(horizon))
    return {"forecast": fc, "lower": lo, "upper": hi, "mu": mu, "sigma": sigma}


# ---------- Загрузка и анализ ----------
def _fetch_one(ticker: str) -> Optional[pd.DataFrame]:
    """Синхронная загрузка одного тикера — вызывается через to_thread."""
    try:
        df = yf.download(ticker, period="1y", auto_adjust=True,
                         progress=False, threads=False)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception:
        return None


def _fetch_many(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Батч-загрузка: один запрос на несколько тикеров."""
    if len(tickers) == 1:
        df = _fetch_one(tickers[0])
        return {tickers[0]: df} if df is not None else {}
    try:
        raw = yf.download(tickers, period="1y", auto_adjust=True,
                          progress=False, threads=True, group_by="ticker")
    except Exception:
        return {t: _fetch_one(t) for t in tickers}
    out = {}
    for t in tickers:
        try:
            df = raw[t].dropna(how="all")
            if not df.empty:
                out[t] = df
        except Exception:
            single = _fetch_one(t)
            if single is not None:
                out[t] = single
    return out


def _build_report(ticker: str, df: pd.DataFrame) -> str:
    close = df["Close"].squeeze()
    if len(close) < 30:
        return f"📊 {ticker.upper()}\nНедостаточно данных для анализа."

    last = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    chg_day = (last / prev - 1) * 100

    def chg(days: int) -> Optional[float]:
        if len(close) <= days:
            return None
        return (last / float(close.iloc[-days - 1]) - 1) * 100

    lines = [f"📊 <b>{ticker.upper()}</b>", f"Цена: <b>{last:.2f}</b> ({chg_day:+.2f}% за день)"]

    for label, days in [("неделю", 5), ("месяц", 21), ("год", 252)]:
        v = chg(days)
        if v is not None:
            lines.append(f"  за {label}: {v:+.2f}%")

    # RSI
    rsi = _rsi(close)
    if pd.notna(rsi):
        zone = "перекуплен" if rsi > 70 else "перепродан" if rsi < 30 else "нейтрален"
        lines.append(f"\nRSI(14): <b>{rsi:.1f}</b> ({zone})")

    # MACD
    macd, sig, hist = _macd(close)
    trend = "бычий" if hist > 0 else "медвежий"
    lines.append(f"MACD hist: {hist:+.3f} ({trend})")

    # Bollinger
    up, mid, low = _bollinger(close)
    pos = "у верхней" if last > up else "у нижней" if last < low else "внутри"
    lines.append(f"Bollinger: {low:.2f} — {mid:.2f} — {up:.2f} (цена {pos})")

    # ATR и волатильность
    atr = _atr(df)
    if pd.notna(atr):
        lines.append(f"ATR(14): {atr:.2f} ({atr / last * 100:.2f}% от цены)")
    log_ret = np.log(close / close.shift(1)).dropna()
    vol_annual = float(log_ret.std() * math.sqrt(252) * 100)
    lines.append(f"Волатильность (годовая): {vol_annual:.1f}%")

    # SMA 50/200
    sma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
    sma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None
    if sma50 is not None:
        lines.append(f"SMA50: {sma50:.2f}")
    if sma200 is not None:
        lines.append(f"SMA200: {sma200:.2f}")
    if sma50 is not None and sma200 is not None:
        cross = "золотой крест" if sma50 > sma200 else "мёртвый крест"
        lines.append(f"  {cross} (SMA50 {'>' if sma50 > sma200 else '<'} SMA200)")

    # 52-недельный диапазон
    year = close.tail(252)
    y_low, y_high = float(year.min()), float(year.max())
    if y_high > y_low:
        pos_pct = (last - y_low) / (y_high - y_low) * 100
        lines.append(f"\n52-нед. диапазон: {y_low:.2f} — {y_high:.2f} (позиция {pos_pct:.0f}%)")

    # Объём
    if "Volume" in df.columns:
        vol = df["Volume"].squeeze()
        if len(vol) >= 20:
            avg20 = float(vol.tail(20).mean())
            last_vol = float(vol.iloc[-1])
            ratio = last_vol / avg20 if avg20 else float("nan")
            if pd.notna(ratio):
                lines.append(f"Объём: {last_vol:,.0f} ({ratio:.1f}× среднего за 20 дн.)")

    # Уровни за 20 дней
    recent = close.tail(20)
    lines.append(f"Поддержка/сопротивление (20 дн.): {float(recent.min()):.2f} / {float(recent.max()):.2f}")

    # Прогнозы
    lines.append("\n📈 <b>Прогноз</b>")
    for h in (1, 5, 20):
        f = _forecast(close, h)
        if f:
            lines.append(
                f"  {h} дн.: <b>{f['forecast']:.2f}</b> "
                f"[{f['lower']:.2f} — {f['upper']:.2f}]"
            )

    lines.append("\n⚠️ Не инвестиционная рекомендация.")
    return "\n".join(lines)


def _get_fundamentals(ticker: str) -> Optional[dict]:
    """Быстрые фундаментальные поля через fast_info. Может отсутствовать."""
    try:
        fi = yf.Ticker(ticker).fast_info
        out = {}
        for attr, label in [
            ("market_cap", "Капитализация"),
            ("last_price", "Цена (fast)"),
            ("year_high", "52-нед. максимум"),
            ("year_low", "52-нед. минимум"),
        ]:
            v = getattr(fi, attr, None)
            if v is not None:
                out[label] = v
        return out or None
    except Exception:
        return None


async def analyze_async(tickers: list[str]) -> str:
    """Загружает данные в потоке, чтобы не блокировать event loop."""
    cache_key = ",".join(sorted(tickers))
    cached = _cache_get(cache_key)
    if cached:
        return cached

    data = await asyncio.to_thread(_fetch_many, tickers)
    if not data:
        return "Не удалось получить данные. Проверьте тикеры и попробуйте позже."

    parts = []
    for t in tickers:
        df = data.get(t)
        if df is None:
            parts.append(f"📊 {t.upper()}\nТикер не найден.")
            continue
        parts.append(_build_report(t, df))

    result = "\n\n———\n\n".join(parts)
    _cache_set(cache_key, result)
    return result


# ---------- Хендлеры ----------
@dp.message_created()
async def on_message(event):
    text = (event.message.body.text or "").strip()

    if text == "/start":
        await event.message.answer(
            "Привет! Пришлите тикер — сразу покажу расширенную аналитику.\n"
            "Примеры: <code>AAPL</code>, <code>MSFT</code>, <code>TSLA</code>\n"
            "Можно несколько через запятую: <code>AAPL, MSFT</code>"
        )
        return

    if not text or text.startswith("/"):
        return

    # Разбор тикеров: запятая, пробел, точка с запятой
    raw = text.replace(";", ",").replace(" ", ",")
    tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]
    # Убираем дубли, сохраняя порядок
    tickers = list(dict.fromkeys(tickers))[:5]  # максимум 5 за раз

    try:
        result = await analyze_async(tickers)
    except Exception as e:
        result = f"Ошибка при анализе: {e}"
    await event.message.answer(result, format="html")


if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))