"""
Sistema adaptativo: ajusta umbrales y habilita/deshabilita estrategias
basado en la performance real del bot.

El bot se vuelve más exigente con estrategias que pierden y mantiene
las que ganan. Se recalibra cada vez que hay una señal cerrada nueva.
"""

import json
from datetime import datetime
from pathlib import Path

from tracker import get_performance, get_all_performance

CONFIG_FILE = Path(__file__).parent / "data" / "adaptive_config.json"

# Configuración base (backtest)
DEFAULT_CONFIG = {
    "z-score": {
        "enabled": True,
        "z_threshold": 2.0,         # Umbral base
        "z_threshold_min": 1.8,     # Mínimo permitido (si rinde bien, baja)
        "z_threshold_max": 3.5,     # Máximo permitido (si rinde mal, sube)
        "sl_pct": 0.02,
        "tp_pct": 0.04,
    },
    "calendar-spread": {
        "enabled": True,
        "z_threshold": 2.0,
        "z_threshold_min": 1.8,
        "z_threshold_max": 3.5,
        "sl_pct": 0.015,
        "tp_pct": 0.03,
    },
    "oi-divergence": {
        "enabled": True,
        "price_change_min": 1.0,    # % mínimo de suba del precio
        "oi_drop_min": -5000,       # Caída mínima de OI
        "sl_pct": 0.015,
        "tp_pct": 0.03,
    },
    "vol-spike": {
        "enabled": True,
        "vol_z_threshold": 3.0,
        "tasa_z_threshold": 1.0,
        "sl_pct": 0.02,
        "tp_pct": 0.04,
    },
    "last_recalibration": None,
    "version": 1,
}

# Reglas de adaptación
MIN_TRADES_TO_ADAPT = 5      # Mínimo de trades cerrados para ajustar
WR_DISABLE_THRESHOLD = 35.0  # WR% debajo del cual se desactiva la estrategia
WR_TIGHTEN_THRESHOLD = 45.0  # WR% debajo del cual se ajustan umbrales (más exigente)
WR_LOOSEN_THRESHOLD = 70.0   # WR% arriba del cual se pueden relajar umbrales
WR_REENABLE_THRESHOLD = 50.0 # WR% para re-habilitar (calculado sobre backtest)


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                config = json.load(f)
                # Merge con defaults para campos nuevos
                for key in DEFAULT_CONFIG:
                    if key not in config:
                        config[key] = DEFAULT_CONFIG[key]
                return config
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config: dict):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def recalibrate() -> dict:
    """
    Recalibra los parámetros basado en la performance real.

    Reglas:
    - WR < 35%  → desactivar estrategia
    - WR < 45%  → subir umbral z-score (más exigente, menos señales)
    - WR > 70%  → mantener o bajar umbral ligeramente (permitir más señales)
    - WR 45-70% → mantener parámetros actuales

    Returns: dict con cambios realizados
    """
    config = load_config()
    changes = []
    perf = get_all_performance()

    for strat in ["z-score", "calendar-spread", "oi-divergence", "vol-spike"]:
        if strat not in perf or strat not in config:
            continue

        p = perf[strat]
        c = config[strat]

        if p["trades"] < MIN_TRADES_TO_ADAPT:
            continue  # No hay suficientes datos para ajustar

        wr = p["win_rate"]

        # Desactivar si WR es muy bajo
        if wr < WR_DISABLE_THRESHOLD and c["enabled"]:
            c["enabled"] = False
            changes.append(f"DESACTIVADA {strat} (WR {wr:.0f}% < {WR_DISABLE_THRESHOLD}%)")

        # Ajustar umbrales si WR es bajo
        elif wr < WR_TIGHTEN_THRESHOLD and "z_threshold" in c:
            old = c["z_threshold"]
            new = min(old + 0.3, c.get("z_threshold_max", 3.5))
            if new != old:
                c["z_threshold"] = round(new, 1)
                changes.append(f"MAS EXIGENTE {strat}: z {old} → {new} (WR {wr:.0f}%)")

        # Relajar si WR es alto
        elif wr > WR_LOOSEN_THRESHOLD and "z_threshold" in c:
            old = c["z_threshold"]
            new = max(old - 0.1, c.get("z_threshold_min", 1.8))
            if new != old:
                c["z_threshold"] = round(new, 1)
                changes.append(f"MAS PERMISIVA {strat}: z {old} → {new} (WR {wr:.0f}%)")

        # Re-habilitar si estaba desactivada y la performance mejora
        if not c["enabled"] and wr >= WR_REENABLE_THRESHOLD and p["trades"] >= MIN_TRADES_TO_ADAPT:
            c["enabled"] = True
            changes.append(f"RE-ACTIVADA {strat} (WR {wr:.0f}% >= {WR_REENABLE_THRESHOLD}%)")

    config["last_recalibration"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    save_config(config)

    return {"changes": changes, "config": config, "performance": perf}


def get_strategy_params(estrategia: str) -> dict:
    """Obtiene los parámetros actuales de una estrategia."""
    config = load_config()
    return config.get(estrategia, {})


def is_strategy_enabled(estrategia: str) -> bool:
    """Verifica si una estrategia está habilitada."""
    config = load_config()
    strat_config = config.get(estrategia, {})
    return strat_config.get("enabled", True)


def format_performance_report() -> str:
    """Genera reporte de performance para Telegram."""
    perf = get_all_performance()
    config = load_config()

    msg = "<b>📊 REPORTE SEMANAL DE PERFORMANCE</b>\n\n"

    g = perf.get("global", {})
    if g.get("trades", 0) > 0:
        msg += f"<b>Global:</b> {g['trades']} trades | WR {g['win_rate']:.0f}% | PF {g['profit_factor']:.2f}\n"
        msg += f"P&L: ${g['pnl_total']:,.0f} ARS\n\n"
    else:
        msg += "Sin trades cerrados todavia.\n\n"

    strat_names = {
        "z-score": "Z-Score Tasa",
        "calendar-spread": "Calendar Spread",
        "oi-divergence": "OI Divergencia",
        "vol-spike": "Vol Spike",
    }

    for strat in ["z-score", "calendar-spread", "oi-divergence", "vol-spike"]:
        p = perf.get(strat, {})
        c = config.get(strat, {})
        enabled = "✅" if c.get("enabled", True) else "❌"
        name = strat_names.get(strat, strat)

        if p.get("trades", 0) > 0:
            msg += f"{enabled} <b>{name}:</b> {p['trades']} trades, WR {p['win_rate']:.0f}%"
            if "z_threshold" in c:
                msg += f" (z={c['z_threshold']})"
            msg += f"\n   P&L: ${p['pnl_total']:,.0f}\n"
        else:
            msg += f"{enabled} <b>{name}:</b> sin trades\n"

    last = config.get("last_recalibration", "nunca")
    msg += f"\n<i>Ultima recalibracion: {last}</i>"

    return msg
