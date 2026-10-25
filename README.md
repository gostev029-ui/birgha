# ТикерСкан — бот биржевой аналитики для MAX

Показывает цену, изменение, RSI, MACD, Bollinger, волатильность и вероятностный прогноз
по тикерам акций. Работает с несколькими источниками одновременно. Yahoo Finance исключён.

## Возможности
- Цена, дневное изменение, изменения за неделю/месяц/год
- RSI(14), MACD(12,26,9), Bollinger(20,2), ATR(14)
- SMA50/SMA200 и сигналы «золотой/мёртвый крест»
- Годовая волатильность, 52-недельный диапазон, объём
- Прогноз на 1, 5 и 20 дней с 95%-м интервалом
- Параллельный опрос Finnhub, Twelve Data, Tiingo, Massive, FMP, Alpaca, MOEX ISS
- TTL-кэш 15 минут, таймауты, прогрев популярных тикеров

## Источники данных
| Источник | Лимит free | Ключ |
|---|---|---|
| Finnhub | 60 запросов/мин | `FINNHUB_API_KEY` |
| Twelve Data | 8/мин, 800/день | `TWELVEDATA_API_KEY` |
| Tiingo | 1000/день, 50/час | `TIINGO_API_KEY` |
| Massive (Polygon) | 5/мин, EOD | `MASSIVE_API_KEY` |
| FMP | 250/день | `FMP_API_KEY` |
| Alpaca | 200/мин, IEX | `ALPACA_KEY` + `ALPACA_SECRET` |
| MOEX ISS | без ключа | не нужен |

Без ключей бот работает на MOEX ISS. С ключами — на всех семи источниках.

## Переменные окружения
| Переменная | Обязательна | Описание |
|---|---|---|
| `MAX_BOT_TOKEN` | ✅ | Токен из `business.max.ru` |
| `FINNHUB_API_KEY` | ⬜ | Ключ Finnhub |
| `TWELVEDATA_API_KEY` | ⬜ | Ключ Twelve Data |
| `TIINGO_API_KEY` | ⬜ | Ключ Tiingo |
| `MASSIVE_API_KEY` | ⬜ | Ключ Massive (Polygon) |
| `FMP_API_KEY` | ⬜ | Ключ FMP |
| `ALPACA_KEY` / `ALPACA_SECRET` | ⬜ | Ключи Alpaca |

## Запуск локально
```bash
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # Linux/macOS

pip install -r requirements.txt

set MAX_BOT_TOKEN=f9LHodD0cOIufBeN0Ltbm_vtj-m01OQdGy_4_M2c_j2Sjk7TVXjBpkc3KclsoQhv0l5ECQc2s4yd5UzVn671      # Windows
# export MAX_BOT_TOKEN=...       # Linux/macOS
## Оформление
- Баннер при `/start` — задаётся константой `BANNER_URL` в `bot.py`.
- Две inline-кнопки быстрого доступа: `📊 AAPL` и `🇷🇺 SBER`.
- После каждого отчёта — кнопки `📊 Ещё тикер` и `🏠 Меню`.
- Чтобы заменить баннер: положите `banner.png` в репозиторий и обновите `BANNER_URL` на raw-URL GitHub.

python bot.py