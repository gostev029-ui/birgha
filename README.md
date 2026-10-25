# ТикерСкан — бот биржевой аналитики для MAX

Показывает цену, изменение, RSI, MACD, Bollinger, волатильность и вероятностный прогноз
по тикерам акций. Работает с несколькими источниками одновременно.

## Возможности
- Цена, дневное изменение, изменения за неделю/месяц/год
- RSI(14), MACD(12,26,9), Bollinger(20,2), ATR(14)
- SMA50/SMA200 и сигналы «золотой/мёртвый крест»
- Годовая волатильность, 52-недельный диапазон, объём
- Прогноз на 1, 5 и 20 дней с 95%-м интервалом
- Параллельный опрос Yahoo Finance, MOEX ISS, Twelve Data, Finnhub
- TTL-кэш 15 минут, таймауты, прогрев популярных тикеров

## Источники данных
| Источник | Что даёт | Ключ |
|---|---|---|
| Yahoo Finance | US/EU акции, ETF, индексы | не нужен |
| MOEX ISS | Российские акции (SBER, GAZP, LKOH) | не нужен |
| Twelve Data | Резерв для US/EU | `TWELVEDATA_API_KEY` (бесплатно, 800/день) |
| Finnhub | Быстрая цена, резерв | `FINNHUB_API_KEY` (бесплатно, 60/мин) |

Без ключей бот работает на Yahoo + MOEX. Ключи добавляют резервные каналы.

## Переменные окружения
| Переменная | Обязательна | Описание |
|---|---|---|
| `MAX_BOT_TOKEN` | ✅ | Токен из `business.max.ru` |
| `TWELVEDATA_API_KEY` | ⬜ | Ключ Twelve Data |
| `FINNHUB_API_KEY` | ⬜ | Ключ Finnhub |

## Запуск локально
```bash
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # Linux/macOS

pip install -r requirements.txt

set MAX_BOT_TOKEN=ваш_токен      # Windows
# export MAX_BOT_TOKEN=...       # Linux/macOS

python bot.py