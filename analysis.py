"""
Motor de análisis v2: multi-estrategia con confirmaciones.
Validado con datos reales MATBA-ROFEX (oct25-abr26).

Estrategias:
1. Z-score tasa implícita (solo VENTAS) — validada WR 77.8%
2. Calendar spread mean reversion — nueva
3. OI divergence (precio sube + OI baja = debilidad) — nueva
4. Volumen spike — nueva

Confirmaciones: OI change, volatilidad opciones, calendario macro.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


HISTORY_FILE = Path(__file__).parent / "data" / "tasa_history.json"

# Calendario macro Argentina 2026 (fechas aproximadas)
# Formato: lista de (mes, dia, evento)
MACRO_EVENTS = [
    # IPC INDEC (generalmente 2da/3ra semana del mes)
    (1, 15), (2, 13), (3, 13), (4, 11), (5, 14), (6, 12),
    (7, 15), (8, 13), (9, 11), (10, 15), (11, 13), (12, 11),
]


def is_macro_day(fecha: datetime, buffer_days: int = 1) -> bool:
    """Verifica si una fecha está cerca de un evento macro importante."""
    for mes, dia in MACRO_EVENTS:
        try:
            evento = datetime(fecha.year, mes, dia)
            diff = abs((fecha - evento).days)
            if diff <= buffer_days:
                return True
        except ValueError:
            continue
    return False


@dataclass
class Signal:
    tipo: str           # "VENTA", "CALENDAR SPREAD"
    ticker: str
    motivo: str
    fuerza: str         # "MODERADA", "FUERTE"
    precio_entrada: float
    stop_loss: float
    take_profit: float
    estrategia: str     # "z-score", "calendar", "oi-divergence", "vol-spike"
    tasa_implicita: float | None = None
    datos_extra: dict | None = None


def calcular_tasa_implicita(precio_futuro: float, spot: float, dias_vto: int) -> float:
    if spot <= 0 or dias_vto <= 0:
        return 0.0
    return ((precio_futuro / spot) - 1) * (365 / dias_vto) * 100


def dias_al_vencimiento(ticker: str) -> int:
    meses = {
        "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12,
    }
    try:
        parts = ticker.split("/")[-1]
        mes_str = parts[:3].upper()
        anio = int("20" + parts[3:])
        mes = meses.get(mes_str, 0)
        if mes == 0:
            return 0
        if mes == 12:
            vto = datetime(anio + 1, 1, 1)
        else:
            vto = datetime(anio, mes + 1, 1)
        dias = (vto - datetime.now()).days
        return max(dias, 1)
    except Exception:
        return 0


class AnalysisEngine:
    """Motor de análisis multi-estrategia para futuros de dólar."""

    def __init__(self, capital: float = 400_000, riesgo_max_pct: float = 0.02):
        self.capital = capital
        self.riesgo_max = capital * riesgo_max_pct
        self.tasa_history: dict[str, list[float]] = {}
        self.price_history: dict[str, list[float]] = {}
        self.vol_history: dict[str, list[int]] = {}
        self.oi_history: dict[str, list[float]] = {}
        self.spread_history: dict[str, list[float]] = {}
        self._load_history()

    def _load_history(self):
        if HISTORY_FILE.exists():
            try:
                with open(HISTORY_FILE) as f:
                    data = json.load(f)
                self.tasa_history = data.get("tasa", {})
                self.price_history = data.get("price", {})
                self.vol_history = data.get("vol", {})
                self.oi_history = data.get("oi", {})
                self.spread_history = data.get("spread", {})
            except Exception:
                pass

    def _save_history(self):
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_FILE, "w") as f:
            json.dump({
                "tasa": self.tasa_history,
                "price": self.price_history,
                "vol": self.vol_history,
                "oi": self.oi_history,
                "spread": self.spread_history,
            }, f)

    def _append_history(self, store: dict, key: str, value: float, max_len: int = 30):
        hist = store.get(key, [])
        hist.append(value)
        if len(hist) > max_len:
            hist = hist[-max_len:]
        store[key] = hist
        return hist

    def _z_score(self, values: list[float], current: float, window: int = 10) -> float | None:
        if len(values) < window + 1:
            return None
        prev = values[-(window + 1):-1]
        mean = np.mean(prev)
        std = np.std(prev)
        if std <= 0:
            return None
        return (current - mean) / std

    # =========================================================================
    # ESTRATEGIA 1: Z-score tasa implícita (VENTAS) — validada WR 77.8%
    # =========================================================================
    def estrategia_zscore(
        self, futures: list[dict], spot: float, badlar: float
    ) -> list[Signal]:
        signals = []

        for fut in futures:
            ticker = fut["ticker"]
            precio = fut["precio"]
            volumen = fut.get("volumen", 0)
            oi = fut.get("oi", 0)
            oi_change = fut.get("oi_change", None)
            dias = dias_al_vencimiento(ticker)

            if dias <= 5:
                continue

            tasa = calcular_tasa_implicita(precio, spot, dias)
            tasa_hist = self._append_history(self.tasa_history, ticker, tasa)
            self._append_history(self.price_history, ticker, precio)
            self._append_history(self.oi_history, ticker, oi)

            z = self._z_score(tasa_hist, tasa, window=10)
            if z is None:
                continue

            desvio_badlar = ((tasa - badlar) / badlar * 100) if badlar > 0 else 0

            # Filtro de liquidez: solo contratos con volumen real
            if volumen < 5000:
                continue

            if z >= 2.5:
                # Confirmación OI
                oi_confirma = self._check_oi_confirmation(ticker, oi_change)

                # Confirmación calendario macro
                macro_risk = is_macro_day(datetime.now())

                # Calcular fuerza con confirmaciones
                score = 0
                score += 2 if z >= 3.0 else 1  # z-score
                score += 1 if oi_confirma == "confirma" else 0
                score -= 1 if macro_risk else 0

                if score < 2:
                    continue  # Solo enviar señales con score >= 2

                fuerza = "FUERTE" if score >= 3 else "MODERADA"
                sl_pct = 0.02
                tp_pct = 0.04

                motivo = f"Z-score {z:.1f} | Tasa {tasa:.1f}% vs BADLAR {badlar:.1f}%"
                if oi_confirma:
                    motivo += f" | OI {oi_confirma}"
                if macro_risk:
                    motivo += " | ATENCION: dato macro cercano"

                signals.append(Signal(
                    tipo="VENTA",
                    ticker=ticker,
                    motivo=motivo,
                    fuerza=fuerza,
                    precio_entrada=precio,
                    stop_loss=round(precio * (1 + sl_pct), 2),
                    take_profit=round(precio * (1 - tp_pct), 2),
                    estrategia="z-score",
                    tasa_implicita=tasa,
                    datos_extra={
                        "z_score": round(z, 2),
                        "desvio_badlar": round(desvio_badlar, 1),
                        "oi": oi,
                        "oi_change": oi_change,
                        "oi_confirma": oi_confirma,
                        "macro_risk": macro_risk,
                        "volumen": volumen,
                        "dias_vto": dias,
                    },
                ))

        return signals

    # =========================================================================
    # ESTRATEGIA 2: Calendar spread mean reversion
    # Cuando el spread entre dos vencimientos se desvía mucho de su media
    # =========================================================================
    def estrategia_calendar_spread(
        self, futures: list[dict], spot: float
    ) -> list[Signal]:
        signals = []

        if len(futures) < 2:
            return signals

        futs_sorted = sorted(futures, key=lambda f: dias_al_vencimiento(f["ticker"]))

        for i in range(len(futs_sorted) - 1):
            corto = futs_sorted[i]
            largo = futs_sorted[i + 1]

            dias_corto = dias_al_vencimiento(corto["ticker"])
            dias_largo = dias_al_vencimiento(largo["ticker"])

            if dias_corto <= 10 or dias_largo <= 15:
                continue

            tasa_corto = calcular_tasa_implicita(corto["precio"], spot, dias_corto)
            tasa_largo = calcular_tasa_implicita(largo["precio"], spot, dias_largo)
            spread_tasa = tasa_largo - tasa_corto

            spread_key = f"{corto['ticker']}_{largo['ticker']}"
            spread_hist = self._append_history(self.spread_history, spread_key, spread_tasa)

            z = self._z_score(spread_hist, spread_tasa, window=10)
            if z is None:
                continue

            # Spread se invierte mucho (corto paga más que largo): vender corto
            if z < -2.0 and spread_tasa < -3:
                signals.append(Signal(
                    tipo="VENTA",
                    ticker=corto["ticker"],
                    motivo=(
                        f"Calendar spread invertido z={z:.1f} | "
                        f"{corto['ticker']} tasa {tasa_corto:.1f}% > "
                        f"{largo['ticker']} tasa {tasa_largo:.1f}% "
                        f"(spread {spread_tasa:.1f}%)"
                    ),
                    fuerza="FUERTE" if z < -3.0 else "MODERADA",
                    precio_entrada=corto["precio"],
                    stop_loss=round(corto["precio"] * 1.015, 2),
                    take_profit=round(corto["precio"] * 0.97, 2),
                    estrategia="calendar-spread",
                    tasa_implicita=tasa_corto,
                    datos_extra={
                        "z_score": round(z, 2),
                        "spread_tasa": round(spread_tasa, 2),
                        "ticker_largo": largo["ticker"],
                        "tasa_corto": round(tasa_corto, 2),
                        "tasa_largo": round(tasa_largo, 2),
                    },
                ))

            # Spread se amplía mucho (largo paga mucho más que corto): vender largo
            elif z > 2.0 and spread_tasa > 5:
                signals.append(Signal(
                    tipo="VENTA",
                    ticker=largo["ticker"],
                    motivo=(
                        f"Calendar spread amplio z={z:.1f} | "
                        f"{largo['ticker']} tasa {tasa_largo:.1f}% >> "
                        f"{corto['ticker']} tasa {tasa_corto:.1f}% "
                        f"(spread {spread_tasa:.1f}%)"
                    ),
                    fuerza="FUERTE" if z > 3.0 else "MODERADA",
                    precio_entrada=largo["precio"],
                    stop_loss=round(largo["precio"] * 1.015, 2),
                    take_profit=round(largo["precio"] * 0.97, 2),
                    estrategia="calendar-spread",
                    tasa_implicita=tasa_largo,
                    datos_extra={
                        "z_score": round(z, 2),
                        "spread_tasa": round(spread_tasa, 2),
                        "ticker_corto": corto["ticker"],
                        "tasa_corto": round(tasa_corto, 2),
                        "tasa_largo": round(tasa_largo, 2),
                    },
                ))

        return signals

    # =========================================================================
    # ESTRATEGIA 3: OI divergence (precio sube pero OI baja = debilidad)
    # =========================================================================
    def estrategia_oi_divergence(
        self, futures: list[dict], spot: float
    ) -> list[Signal]:
        signals = []

        for fut in futures:
            ticker = fut["ticker"]
            precio = fut["precio"]
            oi = fut.get("oi", 0)
            oi_change = fut.get("oi_change", None)
            volumen = fut.get("volumen", 0)
            dias = dias_al_vencimiento(ticker)

            if dias <= 10 or oi_change is None:
                continue

            price_hist = self.price_history.get(ticker, [])
            oi_hist = self.oi_history.get(ticker, [])

            if len(price_hist) < 5 or len(oi_hist) < 5:
                continue

            # Precio subió en los últimos 3 días
            price_change_3d = (precio / price_hist[-4] - 1) * 100 if len(price_hist) >= 4 else 0

            # OI bajó en los últimos 3 días
            oi_change_3d = oi - oi_hist[-4] if len(oi_hist) >= 4 else 0

            # Divergencia bajista: precio sube + OI baja (posiciones se cierran, no hay convicción)
            if price_change_3d > 1.5 and oi_change_3d < -10000 and volumen > 20000:
                tasa = calcular_tasa_implicita(precio, spot, dias)

                signals.append(Signal(
                    tipo="VENTA",
                    ticker=ticker,
                    motivo=(
                        f"OI divergencia bajista | Precio subio {price_change_3d:.1f}% "
                        f"pero OI cayo {oi_change_3d:,.0f} contratos en 3d"
                    ),
                    fuerza="MODERADA",
                    precio_entrada=precio,
                    stop_loss=round(precio * 1.015, 2),
                    take_profit=round(precio * 0.03 + precio, 2) if False else round(precio * 0.97, 2),
                    estrategia="oi-divergence",
                    tasa_implicita=tasa,
                    datos_extra={
                        "price_change_3d": round(price_change_3d, 2),
                        "oi_change_3d": oi_change_3d,
                        "volumen": volumen,
                        "oi": oi,
                    },
                ))

        return signals

    # =========================================================================
    # ESTRATEGIA 4: Volume spike (volumen anormalmente alto = algo pasa)
    # =========================================================================
    def estrategia_volume_spike(
        self, futures: list[dict], spot: float, badlar: float
    ) -> list[Signal]:
        signals = []

        for fut in futures:
            ticker = fut["ticker"]
            precio = fut["precio"]
            volumen = fut.get("volumen", 0)
            dias = dias_al_vencimiento(ticker)

            if dias <= 5 or volumen == 0:
                continue

            vol_hist = self._append_history(self.vol_history, ticker, volumen)

            if len(vol_hist) < 11:
                continue

            # Z-score del volumen
            prev_vol = vol_hist[-11:-1]
            vol_mean = np.mean(prev_vol)
            vol_std = np.std(prev_vol)

            if vol_std <= 0 or vol_mean <= 0:
                continue

            vol_z = (volumen - vol_mean) / vol_std

            # Volumen > 3x desviaciones = spike
            if vol_z >= 3.0:
                tasa = calcular_tasa_implicita(precio, spot, dias)
                tasa_hist = self.tasa_history.get(ticker, [])

                # Si además la tasa implícita subió, es señal de VENTA
                tasa_z = self._z_score(tasa_hist, tasa, window=10) if len(tasa_hist) >= 11 else None

                if tasa_z is not None and tasa_z > 1.0:
                    signals.append(Signal(
                        tipo="VENTA",
                        ticker=ticker,
                        motivo=(
                            f"Volumen spike z={vol_z:.1f} ({volumen:,} vs media {vol_mean:,.0f}) "
                            f"+ tasa subiendo (z={tasa_z:.1f})"
                        ),
                        fuerza="FUERTE" if vol_z >= 4.0 else "MODERADA",
                        precio_entrada=precio,
                        stop_loss=round(precio * 1.02, 2),
                        take_profit=round(precio * 0.96, 2),
                        estrategia="vol-spike",
                        tasa_implicita=tasa,
                        datos_extra={
                            "vol_z": round(vol_z, 2),
                            "tasa_z": round(tasa_z, 2),
                            "volumen": volumen,
                            "vol_media": round(vol_mean),
                        },
                    ))

        return signals

    # =========================================================================
    # Señales macro (informativas, no generan trade directo)
    # =========================================================================
    def analizar_brecha(self, brecha: dict) -> list[Signal]:
        signals = []
        blue_brecha = brecha.get("blue_vs_oficial", 0)

        if blue_brecha > 40:
            signals.append(Signal(
                tipo="VENTA",
                ticker="DLR (vto mas cercano con liquidez)",
                motivo=f"Brecha blue/oficial en {blue_brecha:.1f}% - posible overreaction",
                fuerza="MODERADA" if blue_brecha < 60 else "FUERTE",
                precio_entrada=0, stop_loss=0, take_profit=0,
                estrategia="macro-brecha",
                datos_extra={"brecha_blue": blue_brecha},
            ))
        return signals

    def analizar_reservas(self, reservas_df: pd.DataFrame) -> list[Signal]:
        signals = []
        if len(reservas_df) < 2:
            return signals

        ultimo = reservas_df.iloc[-1]["valor"]
        anterior = reservas_df.iloc[-2]["valor"]
        cambio = ultimo - anterior

        if cambio < -200:
            signals.append(Signal(
                tipo="VENTA",
                ticker="DLR (vto mas cercano con liquidez)",
                motivo=f"Reservas BCRA cayeron USD {abs(cambio):.0f}M (actual: USD {ultimo:.0f}M)",
                fuerza="MODERADA" if cambio > -500 else "FUERTE",
                precio_entrada=0, stop_loss=0, take_profit=0,
                estrategia="macro-reservas",
                datos_extra={"reservas": ultimo, "cambio": cambio},
            ))
        return signals

    # =========================================================================
    # Helpers
    # =========================================================================
    def _check_oi_confirmation(self, ticker: str, oi_change: float | None) -> str:
        """Verifica OI como confirmación de señal de VENTA."""
        if oi_change is None:
            return ""
        if oi_change > 0:
            return "confirma"   # OI sube = nuevas posiciones, overreaction real
        elif oi_change < -5000:
            return "debil"      # OI baja mucho = se cierran posiciones
        return ""

    def run_all(
        self, futures: list[dict], spot: float, badlar: float
    ) -> list[Signal]:
        """Ejecuta todas las estrategias y retorna señales combinadas."""
        all_signals = []

        # Estrategia 1: Z-score tasa implícita
        s1 = self.estrategia_zscore(futures, spot, badlar)
        all_signals.extend(s1)

        # Estrategia 2: Calendar spread
        s2 = self.estrategia_calendar_spread(futures, spot)
        all_signals.extend(s2)

        # Estrategia 3: OI divergence
        s3 = self.estrategia_oi_divergence(futures, spot)
        all_signals.extend(s3)

        # Estrategia 4: Volume spike
        s4 = self.estrategia_volume_spike(futures, spot, badlar)
        all_signals.extend(s4)

        self._save_history()

        # Log
        counts = {}
        for s in all_signals:
            counts[s.estrategia] = counts.get(s.estrategia, 0) + 1
        for strat, count in counts.items():
            print(f"  [{strat}] {count} senal(es)")

        return all_signals

    def calcular_posicion(self, precio_futuro: float) -> dict:
        margen_estimado = precio_futuro * 1000 * 0.03
        max_contratos_por_margen = int(self.capital * 0.6 / margen_estimado)
        max_contratos_por_riesgo = max(1, int(self.riesgo_max / (precio_futuro * 0.02 * 1000)))
        contratos = min(max_contratos_por_margen, max_contratos_por_riesgo, 2)

        return {
            "contratos": contratos,
            "margen_requerido": round(margen_estimado * contratos, 0),
            "riesgo_maximo": round(precio_futuro * 0.02 * 1000 * contratos, 0),
            "capital_libre": round(self.capital - margen_estimado * contratos, 0),
        }
