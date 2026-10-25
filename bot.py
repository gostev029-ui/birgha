"""
Биржевой радар — бот биржевой аналитики для MAX.
Источники: MOEX ISS, Binance Public API, Yahoo Finance (yfinance).
"""
import os, asyncio, time, math
from typing import Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from maxapi import Bot, Command, Dispatcher, InlineKeyboardBuilder

# ---------- Конфигурация ----------
BOT_TOKEN = os.environ["f9LHodD0cOIufBeN0Ltbm_vtj-m01OQdGy_4_M2c_j2Sjk7TVXjBpkc3KclsoQhv0l5ECQc2s4yd5UzVn671"]
BANNER_URL = "https://placehold.co/1024x512/0f172a/38bdf8?text=TickerScan&font=roboto"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL = 900
_USER_STATE: dict[int, str] = {}


def _cache_get(key: str) -> Optional[str]:
    item = _CACHE.get(key)
    if item and (time.time() - item[0]) < _CACHE_TTL:
        return item[1]
    return None


def _cache_set(key: str, value: str) -> None:
    _CACHE[key] = (time.time(), value)


# ================= ПРОВАЙДЕР: MOEX ISS (РФ акции, без ключа) =================
class MoexProvider:
    name = "moex"

    def _sync(self, ticker: str) -> Optional[pd.DataFrame]:
        try:
            secid = ticker.replace(".ME", "").upper()
            for board in ("TQBR", "TQTF", "TQIF"):
                url = (
                    f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/"
                    f"{board}/securities/{secid}/candles.json"
                )
                r = requests.get(
                    url,
                    params={"from": "2025-01-01", "interval": 24, "limit": 365},
                    timeout=8,
                )
                data = r.json().get("candles", {}).get("data", [])
                if data:
                    cols = ["open", "close", "high", "low", "value", "volume", "begin", "end"]
                    df = pd.DataFrame(data, columns=cols)
                    df["begin"] = pd.to_datetime(df["begin"])
                    df = df.set_index("begin").rename(columns={
                        "open": "Open", "close": "Close",
                        "high": "High", "low": "Low", "volume": "Volume",
                    })
                    return df[["Open", "High", "Low", "Close", "Volume"]]
            return None
        except Exception:
            return None

    async def fetch(self, ticker: str) -> Optional[pd.DataFrame]:
        return await asyncio.to_thread(self._sync, ticker)


# ================= ПРОВАЙДЕР: Binance Public (крипта, без ключа) =================
class BinanceProvider:
    name = "binance"

    def _sync(self, ticker: str) -> Optional[pd.DataFrame]:
        try:
            # Приводим тикер к формату Binance: BTC -> BTCUSDT, ETH -> ETHUSDT
            t = ticker.upper().replace("-", "").replace("/", "")
            if not t.endswith("USDT") and not t.endswith("BUSD"):
                t = t + "USDT"

            r = requests.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": t, "interval": "1d", "limit": 365},
                timeout=8,
            )
            data = r.json()
            if not isinstance(data, list) or not data:
                return None

            rows = []
            for k in data:
                rows.append({
                    "time": pd.to_datetime(k[0], unit="ms"),
                    "Open": float(k[1]),
                    "High": float(k[2]),
                    "Low": float(k[3]),
                    "Close": float(k[4]),
                    "Volume": float(k[5]),
                })
            df = pd.DataFrame(rows).set_index("time").sort_index()
            return df[["Open", "High", "Low", "Close", "Volume"]]
        except Exception:
            return None

    async def fetch(self, ticker: str) -> Optional[pd.DataFrame]:
        return await asyncio.to_thread(self._sync, ticker)


# ================= ПРОВАЙДЕР: Yahoo Finance (US-акции, без ключа) =================
class YahooProvider:
    name = "yahoo"

    def _sync(self, ticker: str) -> Optional[pd.DataFrame]:
        try:
            df = yf.download(ticker, period="1y", interval="1d",
                             auto_adjust=True, progress=False, timeout=10)
            if df is None or df.empty:
                return None
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        except Exception:
            return None

    async def fetch(self, ticker: str) -> Optional[pd.DataFrame]:
        return await asyncio.to_thread(self._sync, ticker)


# ---------- Реестр провайдеров ----------
PROVIDERS = [MoexProvider(), BinanceProvider(), YahooProvider()]


async def fetch_best(ticker: str):
    tasks = {asyncio.create_task(p.fetch(ticker)): p.name for p in PROVIDERS}
    pending = set(tasks)
    fallback = None
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                try:
                    df = task.result()
                except Exception:
                    continue
                if df is None or df.empty:
                    continue
                name = tasks[task]
                if len(df) >= 30:
                    for p in pending:
                        p.cancel()
                    return df, name
                if fallback is None:
                    fallback = (df, name)
        return fallback if fallback else (None, "none")
    finally:
        for p in pending:
            p.cancel()


# ================= ИНДИКАТОРЫ =================
def _rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    val = (100 - 100 / (1 + rs)).iloc[-1]
    return float(val) if pd.notna(val) else float("nan")


def _macd_hist(close, fast=12, slow=26, signal=9):
    ef = close.ewm(span=fast, adjust=False).mean()
    es = close.ewm(span=slow, adjust=False).mean()
    ml = ef - es
    sl = ml.ewm(span=signal, adjust=False).mean()
    return float((ml - sl).iloc[-1])


def _bollinger(close, period=20, num_std=2):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    return (float((mid + num_std * std).iloc[-1]),
            float(mid.iloc[-1]),
            float((mid - num_std * std).iloc[-1]))


def _atr(df, period=14):
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    val = tr.rolling(period).mean().iloc[-1]
    return float(val) if pd.notna(val) else float("nan")


def _forecast(close, horizon):
    lr = np.log(close / close.shift(1)).dropna()
    if len(lr) < 20:
        return None
    mu, sigma = float(lr.mean()), float(lr.std())
    last = float(close.iloc[-1])
    return {
        "forecast": last * math.exp(mu * horizon),
        "lower": last * math.exp(mu * horizon - 1.96 * sigma * math.sqrt(horizon)),
        "upper": last * math.exp(mu * horizon + 1.96 * sigma * math.sqrt(horizon)),
    }


def _arrow(v: float) -> str:
    return "🟢" if v > 0 else "🔴" if v < 0 else "⚪"


# ================= ОТЧЁТ: ПРОГНОЗ =================
def _build_forecast_report(ticker: str, df: pd.DataFrame, source: str) -> str:
    close = df["Close"].squeeze()
    if len(close) < 5:
        return (f"📊 <b>{ticker.upper()}</b>\n━━━━━━━━━━━━━━\n"
                f"💰 Цена: <b>{float(close.iloc[-1]):.2f}</b>\n"
                f"<i>источник: {source} · мало истории</i>")

    last = float(close.iloc[-1])
    chg_day = (last / float(close.iloc[-2]) - 1) * 100

    lines = [
        f"📈 <b>{ticker.upper()}</b>  ·  <i>{source}</i>",
        "━━━━━━━━━━━━━━",
        f"💰 Цена: <b>{last:.2f}</b>  {_arrow(chg_day)} {chg_day:+.2f}% за день",
    ]

    if len(close) >= 30:
        changes = []
        for label, days in [("нед", 5), ("мес", 21), ("год", 252)]:
            if len(close) > days:
                v = (last / float(close.iloc[-days - 1]) - 1) * 100
                changes.append(f"{_arrow(v)} {label} {v:+.1f}%")
        if changes:
            lines.append("   " + "  ·  ".join(changes))

    lines.append("\n📊 <b>Индикаторы</b>")
    lines.append("━━━━━━━━━━━━━━")

    rsi = _rsi(close)
    if pd.notna(rsi):
        zone = "перекуплен" if rsi > 70 else "перепродан" if rsi < 30 else "нейтрален"
        lines.append(f"RSI(14):   <b>{rsi:.1f}</b>  ·  {zone}")

    if len(close) >= 26:
        hist = _macd_hist(close)
        lines.append(f"MACD:      <b>{hist:+.3f}</b>  ·  {'бычий' if hist > 0 else 'медвежий'}")

    if len(close) >= 20:
        up, mid, low = _bollinger(close)
        lines.append(f"Bollinger: {low:.2f} — <b>{mid:.2f}</b> — {up:.2f}")

    atr = _atr(df)
    if pd.notna(atr) and last:
        lines.append(f"ATR(14):   <b>{atr:.2f}</b>  ({atr / last * 100:.2f}% от цены)")

    if len(close) >= 30:
        lr = np.log(close / close.shift(1)).dropna()
        lines.append(f"Волатильность: <b>{float(lr.std() * math.sqrt(252) * 100):.1f}%</b> годовых")

    if len(close) >= 200:
        s50 = close.rolling(50).mean().iloc[-1]
        s200 = close.rolling(200).mean().iloc[-1]
        if pd.notna(s50) and pd.notna(s200):
            cross = "✨ золотой" if s50 > s200 else "💀 мёртвый"
            lines.append(f"SMA50/200: {s50:.2f} / {s200:.2f}  ·  {cross} крест")

    if "Volume" in df.columns and len(close) >= 20:
        vol = df["Volume"].squeeze()
        avg20 = float(vol.tail(20).mean())
        lv = float(vol.iloc[-1])
        if avg20 > 0:
            lines.append(f"Объём:     {lv:,.0f}  ·  {lv / avg20:.1f}× среднего")

    if len(close) >= 20:
        recent = close.tail(20)
        lines.append(f"Уровни:    поддержка <b>{float(recent.min()):.2f}</b> · сопротивление <b>{float(recent.max()):.2f}</b>")

    if len(close) >= 30:
        lines.append("\n🎯 <b>Прогноз</b>")
        lines.append("━━━━━━━━━━━━━━")
        for h, label in [(1, "1 день "), (5, "5 дней "), (20, "20 дней")]:
            f = _forecast(close, h)
            if f:
                lines.append(f"{label}: <b>{f['forecast']:.2f}</b>  [{f['lower']:.2f} — {f['upper']:.2f}]")

    lines.append("\n⚠️ <i>Не инвестиционная рекомендация.</i>")
    return "\n".join(lines)


# ================= ОТЧЁТ: ЦЕЛЬ =================
def _build_target_report(ticker: str, df: pd.DataFrame, source: str) -> str:
    close = df["Close"].squeeze()
    if len(close) < 30:
        return (f"🎯 <b>{ticker.upper()}</b>\n━━━━━━━━━━━━━━\n"
                f"💰 Цена: <b>{float(close.iloc[-1]):.2f}</b>\n"
                f"<i>источник: {source} · мало истории для целей</i>")

    last = float(close.iloc[-1])
    lines = [
        f"🎯 <b>Цель: {ticker.upper()}</b>  ·  <i>{source}</i>",
        "━━━━━━━━━━━━━━",
        f"💰 Текущая цена: <b>{last:.2f}</b>",
        "",
    ]

    targets = []
    for h, label in [(1, "1 день"), (5, "5 дней"), (20, "20 дней")]:
        f = _forecast(close, h)
        if f:
            potential = (f["forecast"] / last - 1) * 100
            targets.append((label, f, potential))
            lines.append(
                f"📅 <b>{label}</b>: <b>{f['forecast']:.2f}</b>  "
                f"({_arrow(potential)} {potential:+.2f}%)\n"
                f"      диапазон: {f['lower']:.2f} — {f['upper']:.2f}"
            )

    if targets:
        main_label, main_fc, main_pot = targets[-1]
        lines.append("\n━━━━━━━━━━━━━━")
        lines.append(f"🎯 <b>Целевая цена ({main_label})</b>: <b>{main_fc['forecast']:.2f}</b>")
        lines.append(f"📊 Потенциал: {_arrow(main_pot)} <b>{main_pot:+.2f}%</b>")

    lr = np.log(close / close.shift(1)).dropna()
    sigma_annual = float(lr.std() * math.sqrt(252))
    lines.append(f"\n📉 Волатильность: <b>{sigma_annual * 100:.1f}%</b> годовых")
    lines.append(f"📐 Средний дневной ход: <b>{float(lr.std() * 100):.2f}%</b>")

    lines.append("\n⚠️ <i>Не инвестиционная рекомендация. Цель — статистическая, не гарантия.</i>")
    return "\n".join(lines)


# ================= ОРКЕСТРАЦИЯ =================
async def _process(tickers: list[str], mode: str) -> str:
    cache_key = f"{mode}:{','.join(sorted(tickers))}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    parts = []
    for t in tickers:
        df, source = await fetch_best(t)
        if df is None:
            parts.append(f"❌ <b>{t.upper()}</b>\nДанные не найдены. Проверьте тикер (SBER, BTC, AAPL).")
            continue
        if mode == "target":
            parts.append(_build_target_report(t, df, source))
        else:
            parts.append(_build_forecast_report(t, df, source))

    result = "\n\n".join(parts)
    _cache_set(cache_key, result)
    return result


# ================= КЛАВИАТУРЫ =================
def _main_menu_keyboard():
    return (
        InlineKeyboardBuilder()
        .callback("📈 Получить прогноз", "menu:forecast")
        .callback("🎯 Цель", "menu:target")
        .adjust(1)
    )


def _after_report_keyboard():
    return (
        InlineKeyboardBuilder()
        .callback("📈 Ещё прогноз", "menu:forecast")
        .callback("🎯 Ещё цель", "menu:target")
        .callback("🏠 Меню", "menu:home")
        .adjust(2, 1)
    )


# ================= ХЕНДЛЕРЫ =================
async def _show_menu(event):
    _USER_STATE.pop(event.message.sender.user_id, None)
    await event.message.answer(
        attachments=[{"type": "image", "payload": {"url": BANNER_URL}}],
    )
    await event.message.answer(
        "<b>ТикерСкан</b> — аналитика акций и криптовалют.\n"
        "━━━━━━━━━━━━━━\n"
        "Выберите действие:",
        keyboard=_main_menu_keyboard(),
    )


@dp.message_created(Command("start"))
async def on_start(event):
    await _show_menu(event)


@dp.message_created()
async def on_message(event):
    text = (event.message.body.text or "").strip()

    if text.startswith("/"):
        return

    raw = text.replace(";", ",").replace(" ", ",")
    tickers = list(dict.fromkeys(
        t.strip().upper() for t in raw.split(",") if t.strip()
    ))[:5]

    if not tickers:
        return

    user_id = event.message.sender.user_id
    mode = _USER_STATE.pop(user_id, None) or "forecast"

    await event.message.answer(f"⏳ <i>Считаю {'цель' if mode == 'target' else 'прогноз'}…</i>")
    try:
        result = await asyncio.wait_for(_process(tickers, mode), timeout=20)
    except asyncio.TimeoutError:
        result = "⏱ Источники не ответили за 20 секунд. Попробуйте ещё раз."
    except Exception as e:
        result = f"❌ Ошибка: {e}"

    await event.message.answer(result, keyboard=_after_report_keyboard())


@dp.message_callback()
async def on_callback(event):
    payload = getattr(event.callback, "payload", "") or ""
    user_id = event.message.sender.user_id

    if payload == "menu:home":
        await _show_menu(event)
        return

    if payload == "menu:forecast":
        _USER_STATE[user_id] = "forecast"
        await event.message.answer(
            "📈 <b>Прогноз</b>\n━━━━━━━━━━━━━━\n"
            "Напишите тикер: <code>SBER</code>, <code>BTC</code>, <code>AAPL</code>."
        )
        return

    if payload == "menu:target":
        _USER_STATE[user_id] = "target"
        await event.message.answer(
            "🎯 <b>Цель по цене</b>\n━━━━━━━━━━━━━━\n"
            "Напишите тикер — покажу целевые уровни на 1/5/20 дней и потенциал."
        )
        return


async def _warmup():
    try:
        await _process(["SBER"], "forecast")
        print("Кэш прогрет: SBER")
    except Exception as e:
        print("Warmup error:", e)


if __name__ == "__main__":
    async def main():
        asyncio.create_task(_warmup())
        await dp.start_polling(bot)
    asyncio.run(main())