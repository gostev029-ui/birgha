import os, asyncio
import yfinance as yf
import pandas as pd
from maxapi import Bot, Dispatcher

BOT_TOKEN = os.environ["MAX_BOT_TOKEN"]

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def analyze(ticker: str) -> str:
    df = yf.download(ticker, period="3mo", auto_adjust=True, progress=False)
    if df.empty:
        return f"Не нашёл тикер {ticker}. Проверьте написание."
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    close = df["Close"].squeeze()
    last = float(close.iloc[-1])
    chg = (last / float(close.iloc[-2]) - 1) * 100

    text = f"📊 {ticker.upper()}\n"
    text += f"Цена: {last:.2f} ({chg:+.2f}%)\n"

    # RSI(14)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    rs = gain / loss
    rsi = float(100 - 100 / (1 + rs.iloc[-1]))
    text += f"RSI(14): {rsi:.1f}\n"

    # Прогноз: дрейф доходности + волатильность на 5 дней
    rets = close.pct_change().dropna()
    mu, sigma = rets.mean(), rets.std()
    h = 5
    fc = last * (1 + mu * h)
    lo = last * (1 + mu * h - 1.96 * sigma * (h ** 0.5))
    hi = last * (1 + mu * h + 1.96 * sigma * (h ** 0.5))

    text += f"\n📈 Прогноз на {h} дн.: {fc:.2f}\n"
    text += f"95% интервал: {lo:.2f} — {hi:.2f}\n"
    text += "\n⚠️ Не инвестиционная рекомендация."
    return text


@dp.message_created()
async def on_message(event):
    text = (event.message.body.text or "").strip()

    if text == "/start":
        await event.message.answer(
            "Привет! Пришлите тикер — сразу покажу аналитику.\n"
            "Примеры: AAPL, MSFT, TSLA"
        )
        return

    if not text or text.startswith("/"):
        return

    try:
        result = analyze(text)
    except Exception as e:
        result = f"Ошибка: {e}"
    await event.message.answer(result)


if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))