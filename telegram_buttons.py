"""
Sistema de botones interactivos de Telegram + integración Google Sheets.

Cuando el bot manda una alerta, incluye botones:
- "Ejecutar" → guarda el trade en Google Sheets
- "Ignorar" → descarta el trade

Requiere que el usuario configure un Google Apps Script como webhook
(ver SHEETS_SETUP.md para instrucciones).
"""

import json
import os
import threading
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SHEETS_WEBHOOK = os.getenv("SHEETS_WEBHOOK_URL", "")

PENDING_FILE = Path(__file__).parent / "data" / "pending_signals.json"


def _load_pending() -> dict:
    if PENDING_FILE.exists():
        try:
            with open(PENDING_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_pending(data: dict):
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PENDING_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def send_signal_with_buttons(signal, posicion: dict | None = None) -> int | None:
    """Envía señal con botones Ejecutar/Ignorar. Retorna message_id."""
    from telegram_bot import format_signal

    text = format_signal(signal, posicion)

    # Guardar señal como pendiente
    pending = _load_pending()
    signal_id = f"{signal.estrategia}_{signal.ticker}_{signal.precio_entrada}"
    pending[signal_id] = {
        "tipo": signal.tipo,
        "ticker": signal.ticker,
        "estrategia": signal.estrategia,
        "fuerza": signal.fuerza,
        "precio_entrada": signal.precio_entrada,
        "stop_loss": signal.stop_loss,
        "take_profit": signal.take_profit,
        "motivo": signal.motivo,
        "contratos": posicion.get("contratos", 1) if posicion else 1,
        "margen": posicion.get("margen_requerido", 0) if posicion else 0,
    }
    _save_pending(pending)

    keyboard = {
        "inline_keyboard": [[
            {"text": "Ejecutar", "callback_data": f"exec_{signal_id}"},
            {"text": "Ignorar", "callback_data": f"ignore_{signal_id}"},
        ]]
    }

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": json.dumps(keyboard),
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return resp.json().get("result", {}).get("message_id")
    except Exception as e:
        print(f"Error enviando con botones: {e}")
    return None


def answer_callback(callback_id: str, text: str = "OK"):
    """Responde a un callback query."""
    url = f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery"
    try:
        requests.post(url, json={
            "callback_query_id": callback_id,
            "text": text,
            "show_alert": False,
        }, timeout=10)
    except Exception:
        pass


def edit_message(message_id: int, new_text: str):
    """Edita un mensaje existente (para sacar los botones después)."""
    url = f"https://api.telegram.org/bot{TOKEN}/editMessageText"
    try:
        requests.post(url, json={
            "chat_id": CHAT_ID,
            "message_id": message_id,
            "text": new_text,
            "parse_mode": "HTML",
        }, timeout=10)
    except Exception:
        pass


def send_to_sheets(trade: dict) -> bool:
    """Envía un trade al Google Sheet via webhook."""
    if not SHEETS_WEBHOOK:
        print("SHEETS_WEBHOOK_URL no configurada")
        return False

    from datetime import datetime
    motivo_completo = f"[{trade['estrategia']}] {trade.get('motivo', '')}"
    detalle = (
        f"Fuerza: {trade.get('fuerza', '')}/10 | "
        f"Contratos: {trade.get('contratos', 1)} | "
        f"Margen: ${trade.get('margen', 0):,.0f} | "
        f"R:R 1:1.5 (SL 2% / TP 3%)"
    )
    payload = {
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "posicion": "SHORT" if trade["tipo"] == "VENTA" else "LONG",
        "ticker": trade["ticker"],
        "entrada": trade["precio_entrada"],
        "sl": trade["stop_loss"],
        "tp": trade["take_profit"],
        "motivo": motivo_completo,
        "detalle": detalle,
    }
    try:
        resp = requests.post(SHEETS_WEBHOOK, json=payload, timeout=15)
        return resp.status_code == 200
    except Exception as e:
        print(f"Error enviando a Sheets: {e}")
        return False


def handle_callback(callback: dict):
    """Procesa un callback query de Telegram."""
    callback_id = callback["id"]
    data = callback.get("data", "")
    message_id = callback.get("message", {}).get("message_id")

    action, _, signal_id = data.partition("_")

    pending = _load_pending()
    trade = pending.get(signal_id)

    if not trade:
        answer_callback(callback_id, "Señal no encontrada")
        return

    if action == "exec":
        ok = send_to_sheets(trade)
        if ok:
            answer_callback(callback_id, "Guardado en Google Sheets ✓")
            from telegram_bot import format_signal
            from analysis import Signal
            sig = Signal(
                tipo=trade["tipo"], ticker=trade["ticker"], motivo=trade["motivo"],
                fuerza=trade["fuerza"], precio_entrada=trade["precio_entrada"],
                stop_loss=trade["stop_loss"], take_profit=trade["take_profit"],
                estrategia=trade["estrategia"],
            )
            new_text = format_signal(sig) + "\n\n✅ <b>EJECUTADO</b> - Guardado en Sheets"
            if message_id:
                edit_message(message_id, new_text)
        else:
            answer_callback(callback_id, "Error guardando en Sheets")
    elif action == "ignore":
        answer_callback(callback_id, "Señal ignorada")
        from telegram_bot import format_signal
        from analysis import Signal
        sig = Signal(
            tipo=trade["tipo"], ticker=trade["ticker"], motivo=trade["motivo"],
            fuerza=trade["fuerza"], precio_entrada=trade["precio_entrada"],
            stop_loss=trade["stop_loss"], take_profit=trade["take_profit"],
            estrategia=trade["estrategia"],
        )
        new_text = format_signal(sig) + "\n\n⏭ <b>IGNORADO</b>"
        if message_id:
            edit_message(message_id, new_text)

    # Remover de pending
    del pending[signal_id]
    _save_pending(pending)


def listen_for_callbacks():
    """Poll de Telegram para procesar callbacks. Corre en thread."""
    last_update_id = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
            resp = requests.get(url, params={
                "offset": last_update_id + 1,
                "timeout": 30,
                "allowed_updates": ["callback_query"],
            }, timeout=35)
            if resp.status_code == 200:
                updates = resp.json().get("result", [])
                for update in updates:
                    last_update_id = update["update_id"]
                    if "callback_query" in update:
                        handle_callback(update["callback_query"])
        except Exception as e:
            print(f"Error en listen_for_callbacks: {e}")
            time.sleep(5)


def start_callback_listener():
    """Inicia el listener en un thread separado."""
    t = threading.Thread(target=listen_for_callbacks, daemon=True)
    t.start()
    print("  Listener de botones Telegram iniciado")
