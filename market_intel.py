"""
Market Intelligence: recolecta todas las variables que afectan futuros de dólar.

Fuentes:
- REM API: expectativas de tipo de cambio e inflación (consenso 46 analistas)
- BRL/USD: leading indicator del peso (correlación alta)
- Soja: liquidación de divisas del agro (estacionalidad)
- RSS Noticias: Ámbito, Cronista (eventos que mueven el mercado)
- Inflación: datos.gob.ar (ajuste de bandas cambiarias)
- PyOBD: LECAPs/BONCAPs (arbitraje principal vs futuros)
- Estacionalidad: abril-junio calma, julio-diciembre presión
"""

import os
from datetime import datetime, timedelta
from dataclasses import dataclass

import feedparser
import requests
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    yf = None

try:
    from pyobd import BymaData
except ImportError:
    BymaData = None


# =====================================================================
# REM API — Expectativas de tipo de cambio del BCRA
# =====================================================================

def get_rem_exchange_rate() -> dict | None:
    """Obtiene expectativas de tipo de cambio del REM (46 analistas).

    Retorna mediana, p25, p75 por período futuro.
    La comparación REM vs precio futuro indica si el mercado está por
    encima o debajo del consenso.
    """
    try:
        r = requests.get(
            "https://bcra-rem-api.facujallia.workers.dev/api/tipo_cambio",
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        datos = data.get("datos", [])
        if not datos:
            return None

        # Mapear por período
        result = {}
        for d in datos:
            periodo = d.get("periodo", "")
            if periodo:
                result[periodo] = {
                    "mediana": d.get("mediana"),
                    "p25": d.get("percentil_25"),
                    "p75": d.get("percentil_75"),
                }
        return result
    except Exception as e:
        print(f"  Error REM: {e}")
        return None


def get_rem_inflation() -> dict | None:
    """Obtiene expectativas de inflación mensual."""
    try:
        r = requests.get(
            "https://bcra-rem-api.facujallia.workers.dev/api/ipc_general",
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        datos = data.get("datos", [])
        if not datos:
            return None
        result = {}
        for d in datos:
            periodo = d.get("periodo", "")
            if periodo:
                result[periodo] = d.get("mediana")
        return result
    except Exception:
        return None


# =====================================================================
# BRL/USD — Leading indicator (correlación con peso argentino)
# =====================================================================

def get_brl_usd() -> dict | None:
    """Obtiene cotización BRL/USD y variación reciente.

    El real brasileño es leading indicator: si el real se deprecia,
    el peso suele seguir 1-3 días después.
    """
    if yf is None:
        return None
    try:
        brl = yf.Ticker("BRL=X")
        hist = brl.history(period="10d")
        if hist.empty:
            return None

        current = hist["Close"].iloc[-1]
        prev = hist["Close"].iloc[-2] if len(hist) > 1 else current
        week_ago = hist["Close"].iloc[0] if len(hist) >= 5 else current

        return {
            "current": round(current, 4),
            "change_1d": round((current / prev - 1) * 100, 2),
            "change_5d": round((current / week_ago - 1) * 100, 2),
            "trend": "depreciacion" if current > week_ago else "apreciacion",
        }
    except Exception as e:
        print(f"  Error BRL: {e}")
        return None


# =====================================================================
# Soja — Liquidación de divisas del agro
# =====================================================================

def get_soy_price() -> dict | None:
    """Obtiene precio de soja (CME ZS=F).

    Soja alta = más dólares entrando por exportaciones = presión bajista
    sobre el tipo de cambio = menos presión en futuros.
    """
    if yf is None:
        return None
    try:
        soja = yf.Ticker("ZS=F")
        hist = soja.history(period="10d")
        if hist.empty:
            return None

        current = hist["Close"].iloc[-1]
        prev = hist["Close"].iloc[-2] if len(hist) > 1 else current
        month_start = hist["Close"].iloc[0]

        return {
            "price_usd": round(current, 2),
            "change_1d": round((current / prev - 1) * 100, 2),
            "change_period": round((current / month_start - 1) * 100, 2),
        }
    except Exception as e:
        print(f"  Error soja: {e}")
        return None


# =====================================================================
# RSS Noticias — Detección de eventos que mueven el mercado
# =====================================================================

KEYWORDS_ALCISTA = [
    "devaluacion", "devalua", "corrida", "crisis", "cepo", "restriccion",
    "reservas caen", "reservas bajan", "fuga", "riesgo pais sube",
    "dolar sube", "brecha sube", "inflacion sube", "inflacion acelera",
    "fmi rechaza", "fmi posterga", "default", "margin call",
]

KEYWORDS_BAJISTA = [
    "reservas suben", "reservas acumula", "bcra compro", "bcra compra dolares",
    "dolar baja", "dolar calma", "brecha baja", "inflacion baja", "inflacion desacelera",
    "fmi aprueba", "fmi desembolso", "superavit", "cosecha record",
    "liquidacion agro", "carry trade",
]

KEYWORDS_NEUTRAL_IMPORTANT = [
    "licitacion", "lecap", "boncap", "tasa", "banda", "crawling",
    "milei", "caputo", "bcra", "tipo de cambio", "futuros",
]


@dataclass
class NewsSignal:
    titulo: str
    fuente: str
    fecha: str
    sentimiento: str  # "alcista", "bajista", "neutral"
    relevancia: int   # 0-3
    keywords_found: list[str]


def get_news_signals() -> list[NewsSignal]:
    """Obtiene noticias recientes y las clasifica por impacto en futuros.

    alcista = presión al alza en futuros (malo para VENTA)
    bajista = presión a la baja en futuros (bueno para VENTA)
    """
    feeds = [
        ("Ambito Economia", "https://www.ambito.com/rss/pages/economia.xml"),
        ("Ambito Finanzas", "https://www.ambito.com/rss/pages/finanzas.xml"),
        ("El Cronista", "https://www.cronista.com/files/rss/news.xml"),
    ]

    signals = []
    cutoff = datetime.now() - timedelta(hours=12)

    for nombre, url in feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:15]:
                title = entry.get("title", "").lower()
                published = entry.get("published", "")

                # Clasificar sentimiento
                found_alcista = [k for k in KEYWORDS_ALCISTA if k in title]
                found_bajista = [k for k in KEYWORDS_BAJISTA if k in title]
                found_important = [k for k in KEYWORDS_NEUTRAL_IMPORTANT if k in title]

                if not found_alcista and not found_bajista and not found_important:
                    continue

                if found_alcista and not found_bajista:
                    sentimiento = "alcista"
                elif found_bajista and not found_alcista:
                    sentimiento = "bajista"
                else:
                    sentimiento = "neutral"

                relevancia = len(found_alcista) + len(found_bajista) + len(found_important)

                signals.append(NewsSignal(
                    titulo=entry.get("title", ""),
                    fuente=nombre,
                    fecha=published[:25],
                    sentimiento=sentimiento,
                    relevancia=min(relevancia, 3),
                    keywords_found=found_alcista + found_bajista + found_important,
                ))
        except Exception:
            continue

    # Ordenar por relevancia
    signals.sort(key=lambda s: s.relevancia, reverse=True)
    return signals


# =====================================================================
# Inflación — Para proyectar bandas cambiarias
# =====================================================================

def get_inflation_data() -> list[dict] | None:
    """Obtiene últimos datos de inflación de datos.gob.ar."""
    try:
        r = requests.get("https://apis.datos.gob.ar/series/api/series/", params={
            "ids": "103.1_I2N_2016_M_15",
            "limit": 12,
            "sort": "desc",
            "format": "json",
        }, timeout=15)
        r.raise_for_status()
        data = r.json().get("data", [])
        result = []
        for i in range(len(data) - 1):
            current = data[i][1]
            prev = data[i + 1][1]
            if current and prev:
                monthly = (current / prev - 1) * 100
                result.append({
                    "periodo": data[i][0],
                    "ipc": current,
                    "inflacion_mensual": round(monthly, 2),
                })
        return result
    except Exception as e:
        print(f"  Error inflacion: {e}")
        return None


# =====================================================================
# LECAPs via PyOBD — Tasa de referencia principal
# =====================================================================

def get_lecap_rates() -> list[dict] | None:
    """Obtiene tasas de LECAPs del mercado secundario.

    Las LECAPs son el ancla de arbitraje principal para futuros.
    Si tasa LECAP > tasa implícita futuro → futuro barato → no vender
    Si tasa LECAP < tasa implícita futuro → futuro caro → oportunidad VENTA
    """
    if BymaData is None:
        return None
    try:
        client = BymaData()
        data = client.get_short_term_government_bonds()
        if data is None or data.empty:
            return None

        lecaps = []
        for _, row in data.iterrows():
            symbol = row.get("symbol", "")
            close = row.get("closingPrice", 0) or row.get("previousClosingPrice", 0)
            days = row.get("daysToMaturity", 0)
            ccy = row.get("denominationCcy", "")

            if close > 0 and days > 0 and ccy == "ARS" and symbol.startswith("S"):
                # Calcular TNA de la LECAP
                # LECAPs se compran a descuento, valor nominal 1000
                # TNA = ((1000/precio) - 1) * (365/dias) * 100
                tna = ((1000 / close) - 1) * (365 / days) * 100
                lecaps.append({
                    "symbol": symbol,
                    "price": close,
                    "days_to_maturity": days,
                    "tna": round(tna, 2),
                    "maturity": row.get("maturityDate", ""),
                })

        lecaps.sort(key=lambda x: x["days_to_maturity"])
        return lecaps if lecaps else None
    except Exception as e:
        print(f"  Error LECAPs: {e}")
        return None


# =====================================================================
# Estacionalidad — Ajuste de umbrales según época del año
# =====================================================================

def get_seasonality_factor() -> dict:
    """Retorna factor de ajuste estacional para señales.

    Abril-Junio: liquidación cosecha gruesa → más dólares → mercado calmo
    → subir umbrales (ser más exigente, menos señales falsas)

    Julio-Octubre: déficit estacional FX + riesgo político
    → bajar umbrales (capturar más oportunidades)

    Noviembre-Marzo: variable, depende del contexto
    """
    month = datetime.now().month

    if month in (4, 5, 6):
        return {
            "period": "cosecha",
            "z_adjustment": 0.3,  # Subir umbral z (más exigente)
            "description": "Liquidacion cosecha gruesa - mercado calmo",
        }
    elif month in (7, 8, 9, 10):
        return {
            "period": "presion",
            "z_adjustment": -0.2,  # Bajar umbral z (más sensible)
            "description": "Deficit estacional FX + riesgo politico",
        }
    else:
        return {
            "period": "neutral",
            "z_adjustment": 0.0,
            "description": "Sin ajuste estacional",
        }


# =====================================================================
# Bandas cambiarias — Proyección basada en inflación
# =====================================================================

def get_band_levels() -> dict | None:
    """Calcula niveles actuales de las bandas cambiarias.

    Desde enero 2026, las bandas se ajustan por inflación mensual.
    Banda inicial (abril 2025): piso $1000, techo $1400.
    """
    try:
        inflation = get_inflation_data()
        if not inflation:
            return None

        # Valores base abril 2025
        floor = 1000.0
        ceiling = 1400.0

        # Ajuste fijo 1% mensual (abril-diciembre 2025)
        for _ in range(9):  # may-25 a ene-26 = 9 meses de ajuste fijo
            floor *= (1 - 0.01)
            ceiling *= (1 + 0.01)

        # Desde enero 2026, ajuste por inflación
        for inf_data in reversed(inflation):
            periodo = inf_data["periodo"]
            if periodo >= "2026-01":
                monthly_inf = inf_data["inflacion_mensual"] / 100
                floor *= (1 - monthly_inf)
                ceiling *= (1 + monthly_inf)

        return {
            "floor": round(floor, 2),
            "ceiling": round(ceiling, 2),
            "midpoint": round((floor + ceiling) / 2, 2),
            "width_pct": round((ceiling - floor) / ((ceiling + floor) / 2) * 100, 1),
        }
    except Exception as e:
        print(f"  Error bandas: {e}")
        return None


# =====================================================================
# Función consolidada
# =====================================================================

def collect_all_intel() -> dict:
    """Recolecta toda la inteligencia de mercado."""
    print("  Recolectando market intelligence...")

    intel = {}

    # REM
    rem_tc = get_rem_exchange_rate()
    if rem_tc:
        intel["rem_exchange_rate"] = rem_tc
        print(f"    REM: {len(rem_tc)} periodos de expectativas TC")

    rem_inf = get_rem_inflation()
    if rem_inf:
        intel["rem_inflation"] = rem_inf

    # BRL
    brl = get_brl_usd()
    if brl:
        intel["brl_usd"] = brl
        print(f"    BRL/USD: {brl['current']} ({brl['change_1d']:+.2f}% 1d, {brl['trend']})")

    # Soja
    soy = get_soy_price()
    if soy:
        intel["soy"] = soy
        print(f"    Soja: USD {soy['price_usd']} ({soy['change_1d']:+.2f}% 1d)")

    # Noticias
    news = get_news_signals()
    if news:
        intel["news"] = news
        alcistas = sum(1 for n in news if n.sentimiento == "alcista")
        bajistas = sum(1 for n in news if n.sentimiento == "bajista")
        print(f"    Noticias: {len(news)} relevantes ({alcistas} alcistas, {bajistas} bajistas)")

    # Inflación
    inflation = get_inflation_data()
    if inflation:
        intel["inflation"] = inflation
        print(f"    Inflacion: {inflation[0]['inflacion_mensual']}% mensual ({inflation[0]['periodo']})")

    # LECAPs
    lecaps = get_lecap_rates()
    if lecaps:
        intel["lecaps"] = lecaps
        print(f"    LECAPs: {len(lecaps)} con precio")

    # Estacionalidad
    season = get_seasonality_factor()
    intel["seasonality"] = season
    print(f"    Estacionalidad: {season['period']} (z adj: {season['z_adjustment']:+.1f})")

    # Bandas
    bands = get_band_levels()
    if bands:
        intel["bands"] = bands
        print(f"    Bandas: piso ${bands['floor']:,.0f} / techo ${bands['ceiling']:,.0f}")

    return intel
