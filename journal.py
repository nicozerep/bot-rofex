"""
Trade Journal: guarda cada señal en un HTML publicado en GitHub Pages.
Accesible desde cualquier dispositivo via URL.
"""

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from analysis import Signal

JOURNAL_SITE = Path(__file__).parent / "journal-site"
JOURNAL_DATA = JOURNAL_SITE / "trades.json"
JOURNAL_HTML = JOURNAL_SITE / "index.html"


def _load_trades() -> list[dict]:
    if JOURNAL_DATA.exists():
        try:
            with open(JOURNAL_DATA, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save_trades(trades: list[dict]):
    with open(JOURNAL_DATA, "w", encoding="utf-8") as f:
        json.dump(trades, f, ensure_ascii=False, indent=2)


def add_trade(signal: Signal, posicion: dict | None = None):
    """Agrega una señal al journal y publica en GitHub Pages."""
    trades = _load_trades()

    contratos = posicion["contratos"] if posicion else 1
    pnl_tp = abs(signal.take_profit - signal.precio_entrada) * 1000 * contratos
    pnl_sl = abs(signal.stop_loss - signal.precio_entrada) * 1000 * contratos

    trade = {
        "id": len(trades) + 1,
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "ticker": signal.ticker,
        "tipo": signal.tipo,
        "estrategia": getattr(signal, "estrategia", ""),
        "fuerza": signal.fuerza,
        "entrada": signal.precio_entrada,
        "stop_loss": signal.stop_loss,
        "take_profit": signal.take_profit,
        "tasa_implicita": signal.tasa_implicita,
        "motivo": signal.motivo,
        "contratos": contratos,
        "pnl_tp": round(pnl_tp),
        "pnl_sl": round(pnl_sl),
        "datos_extra": signal.datos_extra,
        "estado": "PENDIENTE",
        "precio_real_entrada": None,
        "precio_real_salida": None,
        "pnl_real": None,
        "notas": "",
    }

    trades.append(trade)
    _save_trades(trades)
    _update_html(trades)
    _push_to_github()
    return trade


def _update_html(trades: list[dict]):
    """Inyecta los trades en el HTML, reemplazando el array existente."""
    html = JOURNAL_HTML.read_text(encoding="utf-8")
    trades_json = json.dumps(trades, ensure_ascii=False)

    # Reemplazar el contenido entre los marcadores
    import re
    html = re.sub(
        r"let TRADES = .+?;\n// === END TRADES DATA ===",
        f"let TRADES = {trades_json};\n// === END TRADES DATA ===",
        html,
        flags=re.DOTALL,
    )
    JOURNAL_HTML.write_text(html, encoding="utf-8")


def _push_to_github():
    """Hace commit y push al repo de GitHub Pages."""
    try:
        cwd = str(JOURNAL_SITE)
        subprocess.run(["git", "add", "-A"], cwd=cwd, capture_output=True, timeout=15)
        subprocess.run(
            ["git", "commit", "-m", f"update {datetime.now():%Y-%m-%d %H:%M}"],
            cwd=cwd, capture_output=True, timeout=15,
        )
        result = subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=cwd, capture_output=True, timeout=30,
        )
        if result.returncode == 0:
            print("  Journal publicado en GitHub Pages")
        else:
            print(f"  Push failed: {result.stderr.decode()[:200]}")
    except Exception as e:
        print(f"  Error publicando journal: {e}")


def regenerate():
    """Regenera el HTML y lo publica."""
    trades = _load_trades()
    if trades:
        _update_html(trades)
        _push_to_github()
