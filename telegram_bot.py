"""
Sistema de alertas por Telegram.
"""

import os
import requests
from dotenv import load_dotenv
from analysis import Signal

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Envía un mensaje al chat configurado."""
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


def format_signal(signal: Signal, posicion: dict | None = None) -> str:
    """Formatea una señal de trading para Telegram."""
    emoji_tipo = {"COMPRA": "🟢", "VENTA": "🔴", "CALENDAR SPREAD": "🔵"}
    emoji_fuerza = {"DEBIL": "⚪", "MODERADA": "🟡", "FUERTE": "🔥"}

    strat_names = {
        "z-score": "Z-Score Tasa",
        "calendar-spread": "Calendar Spread",
        "oi-divergence": "OI Divergencia",
        "vol-spike": "Vol Spike",
        "macro-brecha": "Macro Brecha",
        "macro-reservas": "Macro Reservas",
    }
    strat_label = strat_names.get(getattr(signal, "estrategia", ""), "")

    pos_label = "SHORT" if signal.tipo == "VENTA" else "LONG"

    msg = f"""{emoji_tipo.get(signal.tipo, "⚪")} <b>ALERTA {signal.tipo}</b> {emoji_fuerza.get(signal.fuerza, "")}"""

    if signal.precio_entrada > 0:
        contratos = posicion["contratos"] if posicion else 1
        pnl_tp = abs(signal.take_profit - signal.precio_entrada) * 1000 * contratos
        pnl_sl = abs(signal.stop_loss - signal.precio_entrada) * 1000 * contratos
        ratio = pnl_tp / pnl_sl if pnl_sl > 0 else 0
        tp_pct = abs(signal.take_profit - signal.precio_entrada) / signal.precio_entrada * 100
        sl_pct = abs(signal.stop_loss - signal.precio_entrada) / signal.precio_entrada * 100

        msg += f"""

<b>📊 OPERACION:</b>
  Ticker: {signal.ticker}
  Entrada: ${signal.precio_entrada:,.2f}
  Stop Loss: ${signal.stop_loss:,.2f} ({sl_pct:.1f}%)
  Take Profit: ${signal.take_profit:,.2f} ({tp_pct:.1f}%)

<b>🔻 POSICION: {pos_label}</b>
  Contratos: {contratos}"""

        if posicion:
            msg += f"""
  Margen requerido: ${posicion['margen_requerido']:,.0f}
  Capital libre: ${posicion['capital_libre']:,.0f}"""

        msg += f"""

<b>💵 P&amp;L ESTIMADO:</b>
  ✅ Si toca TP: <b>+${pnl_tp:,.0f} ARS</b>
  ❌ Si toca SL: <b>-${pnl_sl:,.0f} ARS</b>
  📐 Ratio R/B: 1:{ratio:.1f}

<b>📋 INFO DEL TRADE:</b>
  Estrategia: {strat_label}
  Fuerza: {signal.fuerza}"""

        if signal.tasa_implicita:
            msg += f"\n  Tasa implicita: {signal.tasa_implicita:.1f}%"

        msg += f"\n  {signal.motivo}"

    else:
        msg += f"\n\n<b>Ticker:</b> {signal.ticker}\n<b>Motivo:</b> {signal.motivo}"

    return msg.strip()


def format_market_summary(data: dict) -> str:
    """Formatea resumen de mercado diario."""
    msg = "<b>📈 RESUMEN DE MERCADO</b>\n"
    msg += f"<i>{data.get('fecha', '')}</i>\n\n"

    if "dolares" in data:
        msg += "<b>💵 Dólares:</b>\n"
        for tipo, vals in data["dolares"].items():
            msg += f"  {tipo.upper()}: ${vals['venta']:,.2f}"
            if vals.get("variacion"):
                msg += f" ({vals['variacion']})"
            msg += "\n"

    if "brechas" in data:
        msg += "\n<b>📊 Brechas:</b>\n"
        for k, v in data["brechas"].items():
            if v is not None:
                msg += f"  {k}: {v:.1f}%\n"

    if "bcra" in data:
        msg += "\n<b>🏦 BCRA:</b>\n"
        for k, v in data["bcra"].items():
            if isinstance(v, (int, float)):
                msg += f"  {k}: {v:,.2f}\n"

    if "futuros" in data:
        msg += "\n<b>📋 Futuros DLR:</b>\n"
        for fut in data["futuros"]:
            tasa = fut.get("tasa_implicita", 0)
            msg += f"  {fut['ticker']}: ${fut['precio']:,.2f} (TNA: {tasa:.1f}%)\n"

    return msg.strip()


def send_signal(signal: Signal, posicion: dict | None = None) -> bool:
    """Envía una alerta de señal."""
    text = format_signal(signal, posicion)
    return send_message(text)


def send_market_summary(data: dict) -> bool:
    """Envía resumen diario de mercado."""
    text = format_market_summary(data)
    return send_message(text)


def send_startup_message() -> bool:
    """Envía mensaje de inicio del bot."""
    msg = "🤖 <b>Bot ROFEX iniciado</b>\n\nMonitoreando mercado de futuros de dólar.\nRecibirás alertas cuando detecte oportunidades."
    return send_message(msg)
