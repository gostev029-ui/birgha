"""
ТикерСкан Admin — веб-оболочка для управления ботом.
Запуск: streamlit run admin.py
"""
import os, time
from datetime import datetime

import numpy as np
import pandas as pd
import requests
import streamlit as st

# Импортируем логику из bot.py
from bot import (
    PROVIDERS,
    _process,
    _CACHE,
    _CACHE_TTL,
    BOT_TOKEN,
    TINKOFF_TOKEN,
    ALOR_TOKEN,
    BANNER_URL,
)

st.set_page_config(
    page_title="ТикерСкан · Admin",
    page_icon="📊",
    layout="wide",
)

# ---------- Стили ----------
st.markdown("""
<style>
    .main { background-color: #0f172a; color: #e2e8f0; }
    .stButton>button { background-color: #38bdf8; color: #0f172a; font-weight: bold; }
    .metric-card { background: #1e293b; padding: 16px; border-radius: 10px; }
    h1, h2, h3 { color: #38bdf8; }
</style>
""", unsafe_allow_html=True)

st.title("📊 ТикерСкан · Панель управления")
st.caption(f"Последнее обновление: {datetime.now().strftime('%H:%M:%S')}")

# ---------- Верхняя панель: конфигурация ----------
st.header("⚙️ Конфигурация")

c1, c2, c3 = st.columns(3)
with c1:
    st.metric("MAX Bot Token", "✅ задан" if BOT_TOKEN else "❌ нет")
with c2:
    st.metric("T-Invest", "✅ задан" if TINKOFF_TOKEN else "— не подключён")
with c3:
    st.metric("ALOR", "✅ задан" if ALOR_TOKEN else "— не подключён")

st.info(f"**Источников подключено:** {len(PROVIDERS)} → "
        f"{', '.join(p.name for p in PROVIDERS)}")

st.divider()

# ---------- Статус источников ----------
st.header("📡 Статус источников")

status_ticker = st.text_input("Проверить источник на тикере:", value="SBER", key="status_ticker")

if st.button("🔄 Проверить источники", type="primary"):
    results = []
    progress = st.progress(0)
    for i, p in enumerate(PROVIDERS):
        t0 = time.time()
        try:
            import asyncio
            df = asyncio.run(p.fetch(status_ticker))
            dt = (time.time() - t0) * 1000
            if df is None or df.empty:
                results.append({
                    "Источник": p.name, "Статус": "❌ нет данных",
                    "Время (мс)": f"{dt:.0f}", "Точек": 0,
                })
            else:
                results.append({
                    "Источник": p.name, "Статус": "✅ OK",
                    "Время (мс)": f"{dt:.0f}", "Точек": len(df),
                })
        except Exception as e:
            dt = (time.time() - t0) * 1000
            results.append({
                "Источник": p.name, "Статус": f"❌ {type(e).__name__}",
                "Время (мс)": f"{dt:.0f}", "Точек": 0,
            })
        progress.progress((i + 1) / len(PROVIDERS))
    progress.empty()
    st.dataframe(pd.DataFrame(results), use_container_width=True, hide_index=True)

st.divider()

# ---------- Тест анализа ----------
st.header("🧪 Тест анализа")

col1, col2 = st.columns([3, 1])
with col1:
    test_ticker = st.text_input("Тикер для анализа:", value="SBER", key="test_ticker")
with col2:
    test_mode = st.selectbox("Режим:", ["forecast", "target"], key="test_mode")

if st.button("▶️ Запустить анализ"):
    import asyncio
    t0 = time.time()
    with st.spinner("Считаем…"):
        try:
            result = asyncio.run(_process([test_ticker.upper()], test_mode))
            dt = (time.time() - t0) * 1000
            st.success(f"Готово за {dt:.0f} мс")
            st.markdown(result.replace("<b>", "**").replace("</b>", "**")
                              .replace("<i>", "_").replace("</i>", "_")
                              .replace("<code>", "`").replace("</code>", "`"))
        except Exception as e:
            st.error(f"Ошибка: {e}")

st.divider()

# ---------- Кэш ----------
st.header("💾 Кэш")

c1, c2, c3 = st.columns(3)
with c1:
    st.metric("Записей в кэше", len(_CACHE))
with c2:
    st.metric("TTL", f"{_CACHE_TTL // 60} мин")
with c3:
    if st.button("🗑️ Очистить кэш"):
        _CACHE.clear()
        st.success("Кэш очищен")
        st.rerun()

if _CACHE:
    now = time.time()
    rows = []
    for key, (ts, value) in _CACHE.items():
        age = int(now - ts)
        rows.append({
            "Ключ": key,
            "Возраст (сек)": age,
            "Живёт ещё (сек)": max(0, _CACHE_TTL - age),
            "Размер (симв.)": len(value),
        })
    st.dataframe(pd.DataFrame(rows).sort_values("Возраст (сек)"),
                 use_container_width=True, hide_index=True)
else:
    st.info("Кэш пуст")

st.divider()

# ---------- Справка ----------
st.header("📖 Справка")

with st.expander("Как получить ключи"):
    st.markdown("""
**T-Invest API:**
1. Открыть брокерский счёт в Т-Инвестициях.
2. Зайти на `tbank.ru/invest/settings/api`.
3. Выпустить токен.
4. Добавить на сервер переменную `TINKOFF_TOKEN`.

**ALOR OpenAPI:**
1. Зарегистрироваться на `alor.dev`.
2. Получить токен в личном кабинете.
3. Добавить на сервер переменную `ALOR_TOKEN`.

**MOEX ISS** — не требует ключа. Работает сразу.
    """)

with st.expander("Что означают статусы"):
    st.markdown("""
- ✅ **OK** — источник вернул данные, всё в порядке.
- ❌ **нет данных** — источник ответил, но по тикеру ничего нет (проверьте тикер).
- ❌ **Timeout** — источник не ответил за отведённое время.
- ❌ **ConnectionError** — сеть недоступна или блокировка.
    """)

st.divider()
st.caption("ТикерСкан Admin · работает на Streamlit · не является инвестиционной рекомендацией")