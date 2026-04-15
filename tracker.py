"""
Tracker de señales: monitorea resultados y alimenta el sistema adaptativo.

Cada señal enviada se registra como "abierta". En cada escaneo posterior,
el tracker verifica si el precio tocó TP o SL y registra el resultado.
"""

import json
from datetime import datetime
from pathlib import Path

TRACKER_FILE = Path(__file__).parent / "data" / "signal_tracker.json"


def _load() -> list[dict]:
    if TRACKER_FILE.exists():
        try:
            with open(TRACKER_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save(signals: list[dict]):
    TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TRACKER_FILE, "w", encoding="utf-8") as f:
        json.dump(signals, f, ensure_ascii=False, indent=2)


def register_signal(signal, posicion: dict | None = None):
    """Registra una señal nueva como abierta para trackear."""
    signals = _load()
    signals.append({
        "id": len(signals) + 1,
        "fecha_entrada": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "ticker": signal.ticker,
        "tipo": signal.tipo,
        "estrategia": getattr(signal, "estrategia", ""),
        "fuerza": signal.fuerza,
        "precio_entrada": signal.precio_entrada,
        "stop_loss": signal.stop_loss,
        "take_profit": signal.take_profit,
        "estado": "ABIERTA",  # ABIERTA, TP, SL, TIMEOUT
        "precio_salida": None,
        "pnl": None,
        "dias_abierta": 0,
        "fecha_cierre": None,
    })
    _save(signals)


def update_signals(futures: list[dict]):
    """Actualiza señales abiertas con precios actuales. Cierra las que tocaron TP/SL."""
    signals = _load()
    if not signals:
        return []

    # Mapear precios actuales por ticker
    precios = {}
    for fut in futures:
        precios[fut["ticker"]] = {
            "precio": fut["precio"],
            "high": fut.get("ask", fut["precio"]),
            "low": fut.get("bid", fut["precio"]),
        }

    cerradas = []

    for sig in signals:
        if sig["estado"] != "ABIERTA":
            continue

        ticker = sig["ticker"]
        if ticker not in precios:
            continue

        p = precios[ticker]
        sig["dias_abierta"] += 1

        if sig["tipo"] == "VENTA":
            # TP se toca si el precio baja hasta el TP
            if p["low"] <= sig["take_profit"]:
                sig["estado"] = "TP"
                sig["precio_salida"] = sig["take_profit"]
                sig["pnl"] = round((sig["precio_entrada"] - sig["take_profit"]) * 1000)
            # SL se toca si el precio sube hasta el SL
            elif p["high"] >= sig["stop_loss"]:
                sig["estado"] = "SL"
                sig["precio_salida"] = sig["stop_loss"]
                sig["pnl"] = round((sig["precio_entrada"] - sig["stop_loss"]) * 1000)
            # Timeout a 20 días
            elif sig["dias_abierta"] >= 20:
                sig["estado"] = "TIMEOUT"
                sig["precio_salida"] = p["precio"]
                sig["pnl"] = round((sig["precio_entrada"] - p["precio"]) * 1000)
        else:  # COMPRA
            if p["high"] >= sig["take_profit"]:
                sig["estado"] = "TP"
                sig["precio_salida"] = sig["take_profit"]
                sig["pnl"] = round((sig["take_profit"] - sig["precio_entrada"]) * 1000)
            elif p["low"] <= sig["stop_loss"]:
                sig["estado"] = "SL"
                sig["precio_salida"] = sig["stop_loss"]
                sig["pnl"] = round((sig["stop_loss"] - sig["precio_entrada"]) * 1000)
            elif sig["dias_abierta"] >= 20:
                sig["estado"] = "TIMEOUT"
                sig["precio_salida"] = p["precio"]
                sig["pnl"] = round((p["precio"] - sig["precio_entrada"]) * 1000)

        if sig["estado"] != "ABIERTA" and sig["fecha_cierre"] is None:
            sig["fecha_cierre"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            cerradas.append(sig)

    _save(signals)
    return cerradas


def get_performance(estrategia: str | None = None, last_n: int = 20) -> dict:
    """Calcula performance de las últimas N señales cerradas."""
    signals = _load()
    cerradas = [s for s in signals if s["estado"] in ("TP", "SL", "TIMEOUT")]

    if estrategia:
        cerradas = [s for s in cerradas if s["estrategia"] == estrategia]

    cerradas = cerradas[-last_n:]

    if not cerradas:
        return {"trades": 0, "win_rate": 0, "pnl_total": 0, "profit_factor": 0}

    wins = [s for s in cerradas if s["estado"] == "TP"]
    losses = [s for s in cerradas if s["estado"] == "SL"]

    total_pnl = sum(s["pnl"] for s in cerradas if s["pnl"])
    gross_profit = sum(s["pnl"] for s in cerradas if s["pnl"] and s["pnl"] > 0)
    gross_loss = abs(sum(s["pnl"] for s in cerradas if s["pnl"] and s["pnl"] < 0))

    return {
        "trades": len(cerradas),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(cerradas) * 100 if cerradas else 0,
        "pnl_total": total_pnl,
        "pnl_promedio": total_pnl / len(cerradas) if cerradas else 0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
    }


def get_all_performance() -> dict:
    """Performance por estrategia."""
    signals = _load()
    estrategias = set(s["estrategia"] for s in signals if s["estrategia"])
    result = {"global": get_performance()}
    for e in estrategias:
        result[e] = get_performance(e)
    return result
