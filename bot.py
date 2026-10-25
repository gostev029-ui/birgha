import os, sqlite3, asyncio
from datetime import datetime, timedelta
import yfinance as yf
import pandas as pd
from maxapi import Bot, Dispatcher

BOT_TOKEN = os.environ["Mf9LHodD0cOJaSej0PJ158OVJ4kfyL5hrieilI7qEEY4GQvpr2vbMYE1qGLN4dERuHMxbhwS3IJkvYv9Ao5QT"]
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
DB = "users.db"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def db_init():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        plan TEXT DEFAULT 'free',
        expires_at TEXT)""")
    con.commit(); con.close()

def get_plan(user_id: int) -> str:
    con = sqlite3.connect(DB)
    row = con.execute("SELECT plan, expires_at FROM users WHERE user_id=?",
                      (user_id,)).fetchone()
    con.close()
    if not row:
        return "free"
    plan, exp = row
    if plan == "pro" and exp and datetime.fromisoformat(exp) > datetime.utcnow():
        return "pro"
    return "free"

def set_pro(user_id: int, days: int = 30):
    exp = (datetime.utcnow() + timedelta(days=days)).isoformat()
    con = sqlite3.connect(DB)
    con.execute("INSERT OR REPLACE INTO users(user_id, plan, expires_at) VALUES(?,?,?)",
                (user_id, "pro", exp))
    con.commit(); con.close()


def analyze(ticker: str, pro: bool) -> str:
    df = yf.download(ticker, period="3mo", auto_adjust=True, progress=False)
    if df.empty:
        return f"Не нашёл тикер {ticker}. Проверьте написание."
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    close = df["Close"].squeeze()
    last = float(close.iloc[-1])
    chg = (last / float(close.iloc[-2]) - 1) * 100
    text = f"📊 {ticker.upper()}\nЦена: {last:.2f} ({chg:+.2f}%)"

    if not pro:
        text += "\n\n🔒 RSI и прогноз — в Pro. Напишите /subscribe"
        return text

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    rs = gain / loss
    rsi = float(100 - 100 / (1 + rs.iloc[-1]))
    text += f"\nRSI(14): {rsi:.1f}"

    rets = close.pct_change().dropna()
    mu, sigma = rets.mean(), rets.std()
    h = 5
    fc = last * (1 + mu * h)
    lo = last * (1 + mu * h - 1.96 * sigma * (h ** 0.5))
    hi = last * (1 + mu * h + 1.96 * sigma * (h ** 0.5))
    text += f"\n\n📈 Прогноз на {h} дн.: {fc:.2f}"
    text += f"\n95% интервал: {lo:.2f} — {hi:.2f}"
    text += "\n\n⚠️ Не инвестиционная рекомендация."
    return text


@dp.message_created()
async def on_message(event):
    text = (event.message.body.text or "").strip()
    user_id = event.message.sender.user_id

    if text == "/start":
        await event.message.answer(
            "Привет! Пришлите тикер — покажу цену.\n"
            "Примеры: AAPL, MSFT, TSLA\n"
            "Pro: /subscribe"
        )
        return

    if text == "/subscribe":
        await event.message.answer(
            "Подписка Pro — 30 дней: RSI + прогноз на 5 дней.\n"
            "Для оплаты напишите администратору: @ваш_ник"
        )
        return

    if text.startswith("/grant") and user_id == ADMIN_ID:
        parts = text.split()
        if len(parts) == 2 and parts[1].isdigit():
            set_pro(int(parts[1]), days=30)
            await event.message.answer(f"Pro активирован для {parts[1]} на 30 дней.")
        else:
            await event.message.answer("Формат: /grant <user_id>")
        return

    plan = get_plan(user_id)
    try:
        result = analyze(text, pro=(plan == "pro"))
    except Exception as e:
        result = f"Ошибка: {e}"
    await event.message.answer(result)


if __name__ == "__main__":
    db_init()
    asyncio.run(dp.start_polling(bot))