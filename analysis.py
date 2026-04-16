"""
Motor de análisis v3: estrategias validadas con datos reales corregidos.
Backtest oct25-abr26 sobre 906 records reales de CEM MATBA-ROFEX.

Estrategias (por P&L y WR en backtest real):
1. Z-Score tasa 5d: z>=2.0 solo VENTAS (WR 63%, 0.23/dia)
2. Settlement Gap: gap diario >=0.5% (WR 56%, 0.88/dia) — NUEVA
3. OI Momentum: OI sube >=5% diario (WR 66%, 0.87/dia) — NUEVA
4. Volume Spike: vol ratio >=2x (WR 63%, 1.08/dia) — NUEVA

Combinadas: ~1.2 trades/dia, WR ~60%, SL 2% / TP 3%
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

HISTORY_FILE = Path(__file__).parent / "data" / "tasa_history.json"

MACRO_EVENTS = [
    (1, 15), (2, 13), (3, 13), (4, 11), (5, 14), (6, 12),
    (7, 15), (8, 13), (9, 11), (10, 15), (11, 13), (12, 11),
]


def is_macro_day(fecha: datetime = None, buffer_days: int = 1) -> bool:
    # Usar hora Argentina (UTC-3) para chequear calendario macro
    from datetime import timedelta
    if fecha is None:
        fecha = datetime.utcnow() - timedelta(hours=3)
    for mes, dia in MACRO_EVENTS:
        try:
            evento = datetime(fecha.year, mes, dia)
            if abs((fecha - evento).days) <= buffer_days:
                return True
        except ValueError:
            continue
    return False


@dataclass
class Signal:
    tipo: str
    ticker: str
    motivo: str
    fuerza: str
    precio_entrada: float
    stop_loss: float
    take_profit: float
    estrategia: str
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
        vto = datetime(anio, mes + 1, 1) if mes < 12 else datetime(anio + 1, 1, 1)
        return max((vto - datetime.now()).days, 1)
    except Exception:
        return 0


class AnalysisEngine:
    """Motor de análisis v3 — estrategias validadas con backtest real corregido."""

    SL_PCT = 0.02  # Stop Loss 2%
    TP_PCT = 0.03  # Take Profit 3% (optimizado, antes era 4%)
    MIN_VOL = 5000  # Volumen mínimo para operar

    def __init__(self, capital: float = 600_000, riesgo_max_pct: float = 0.048):
        self.capital = capital
        self.riesgo_max = capital * riesgo_max_pct
        # Historiales por ticker
        self.tasa_history: dict[str, list[float]] = {}
        self.price_history: dict[str, list[float]] = {}
        self.vol_history: dict[str, list[float]] = {}
        self.oi_history: dict[str, list[float]] = {}
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
            }, f)

    def _append(self, store: dict, key: str, value: float, max_len: int = 30) -> list[float]:
        hist = store.get(key, [])
        hist.append(value)
        if len(hist) > max_len:
            hist = hist[-max_len:]
        store[key] = hist
        return hist

    def _z_score(self, values: list[float], current: float, window: int) -> float | None:
        if len(values) < window + 1:
            return None
        prev = values[-(window + 1):-1]
        mean = np.mean(prev)
        std = np.std(prev)
        if std <= 0:
            return None
        return (current - mean) / std

    def _make_signal(self, ticker, precio, estrategia, motivo, fuerza, spot, datos_extra=None):
        dias = dias_al_vencimiento(ticker)
        tasa = calcular_tasa_implicita(precio, spot, dias) if spot > 0 and dias > 0 else None
        return Signal(
            tipo="VENTA",
            ticker=ticker,
            motivo=motivo,
            fuerza=fuerza,
            precio_entrada=precio,
            stop_loss=round(precio * (1 + self.SL_PCT), 2),
            take_profit=round(precio * (1 - self.TP_PCT), 2),
            estrategia=estrategia,
            tasa_implicita=tasa,
            datos_extra=datos_extra,
        )

    # =========================================================================
    # ESTRATEGIA 1: Z-Score Tasa 5d (WR 63%, backtest)
    # Vender cuando la tasa implícita sube 2+ desvíos en 5 días
    # =========================================================================
    def estrategia_zscore(self, futures, spot, badlar, intel=None):
        signals = []
        intel = intel or {}
        season = intel.get("seasonality", {})
        z_adj = season.get("z_adjustment", 0)
        z_threshold = 2.0 + z_adj

        for fut in futures:
            ticker, precio, vol = fut["ticker"], fut["precio"], fut.get("volumen", 0)
            oi_change = fut.get("oi_change")
            dias = dias_al_vencimiento(ticker)
            if dias <= 5 or vol < self.MIN_VOL:
                continue

            tasa = calcular_tasa_implicita(precio, spot, dias)
            tasa_hist = self._append(self.tasa_history, ticker, tasa)
            z = self._z_score(tasa_hist, tasa, window=5)
            if z is None or z < z_threshold:
                continue

            # Scoring
            score = 2 if z >= 3.0 else 1
            if oi_change and oi_change > 0:
                score += 1
            if is_macro_day(datetime.now()):
                score -= 1
            if score < 1:
                continue

            fuerza = "FUERTE" if score >= 3 else "MODERADA"
            motivo = f"Z-score {z:.1f} (5d) | Tasa {tasa:.1f}% vs BADLAR {badlar:.1f}%"

            signals.append(self._make_signal(
                ticker, precio, "z-score", motivo, fuerza, spot,
                {"z_score": round(z, 2), "volumen": vol, "dias_vto": dias},
            ))
        return signals

    # =========================================================================
    # ESTRATEGIA 2: Settlement Gap (WR 56-62%, 0.88/dia, backtest)
    # Vender cuando el settlement sube >= 0.5% en un día
    # =========================================================================
    def estrategia_gap(self, futures, spot):
        signals = []

        for fut in futures:
            ticker, precio, vol = fut["ticker"], fut["precio"], fut.get("volumen", 0)
            dias = dias_al_vencimiento(ticker)
            if dias <= 5 or vol < self.MIN_VOL:
                continue

            price_hist = self._append(self.price_history, ticker, precio)
            if len(price_hist) < 2:
                continue

            prev_price = price_hist[-2]
            if prev_price <= 0:
                continue

            gap_pct = (precio - prev_price) / prev_price * 100

            if gap_pct >= 0.5:
                fuerza = "FUERTE" if gap_pct >= 1.0 else "MODERADA"
                motivo = f"Gap diario +{gap_pct:.1f}% | ${prev_price:,.0f} -> ${precio:,.0f}"

                signals.append(self._make_signal(
                    ticker, precio, "gap", motivo, fuerza, spot,
                    {"gap_pct": round(gap_pct, 2), "prev_price": prev_price, "volumen": vol},
                ))
        return signals

    # =========================================================================
    # ESTRATEGIA 3: OI Momentum (WR 66%, 0.87/dia, backtest)
    # Vender cuando OI sube >= 5% en un día (nuevas posiciones = overreaction)
    # =========================================================================
    def estrategia_oi_momentum(self, futures, spot):
        signals = []

        for fut in futures:
            ticker, precio, vol = fut["ticker"], fut["precio"], fut.get("volumen", 0)
            oi = fut.get("oi", 0)
            dias = dias_al_vencimiento(ticker)
            if dias <= 5 or vol < self.MIN_VOL or oi <= 0:
                continue

            oi_hist = self._append(self.oi_history, ticker, oi)
            if len(oi_hist) < 2:
                continue

            prev_oi = oi_hist[-2]
            if prev_oi <= 0:
                continue

            oi_change_pct = (oi - prev_oi) / prev_oi * 100

            if oi_change_pct >= 5.0:
                fuerza = "FUERTE" if oi_change_pct >= 10.0 else "MODERADA"
                motivo = f"OI sube +{oi_change_pct:.1f}% | {prev_oi:,.0f} -> {oi:,.0f} contratos"

                signals.append(self._make_signal(
                    ticker, precio, "oi-momentum", motivo, fuerza, spot,
                    {"oi_change_pct": round(oi_change_pct, 2), "oi": oi, "volumen": vol},
                ))
        return signals

    # =========================================================================
    # ESTRATEGIA 4: Volume Spike (WR 63%, 1.08/dia, backtest)
    # Vender cuando el volumen es >= 2x su media de 10 días
    # =========================================================================
    def estrategia_volume(self, futures, spot):
        signals = []

        for fut in futures:
            ticker, precio, vol = fut["ticker"], fut["precio"], fut.get("volumen", 0)
            dias = dias_al_vencimiento(ticker)
            if dias <= 5 or vol < 10000:  # Mínimo más alto para vol spike
                continue

            vol_hist = self._append(self.vol_history, ticker, vol)
            if len(vol_hist) < 11:
                continue

            prev_vol = vol_hist[-11:-1]
            vol_mean = np.mean(prev_vol)
            if vol_mean <= 0:
                continue

            vol_ratio = vol / vol_mean

            if vol_ratio >= 2.0:
                fuerza = "FUERTE" if vol_ratio >= 3.0 else "MODERADA"
                motivo = f"Volumen {vol_ratio:.1f}x media | {vol:,} vs media {vol_mean:,.0f}"

                signals.append(self._make_signal(
                    ticker, precio, "vol-spike", motivo, fuerza, spot,
                    {"vol_ratio": round(vol_ratio, 2), "volumen": vol, "vol_media": round(vol_mean)},
                ))
        return signals

    # =========================================================================
    # Señales macro (informativas)
    # =========================================================================
    def analizar_brecha(self, brecha):
        signals = []
        blue = brecha.get("blue_vs_oficial", 0)
        if blue > 40:
            signals.append(Signal(
                tipo="VENTA", ticker="DLR (vto con liquidez)",
                motivo=f"Brecha blue/oficial en {blue:.1f}%",
                fuerza="FUERTE" if blue > 60 else "MODERADA",
                precio_entrada=0, stop_loss=0, take_profit=0,
                estrategia="macro-brecha",
            ))
        return signals

    def analizar_reservas(self, reservas_df):
        signals = []
        if len(reservas_df) < 2:
            return signals
        ultimo = reservas_df.iloc[-1]["valor"]
        anterior = reservas_df.iloc[-2]["valor"]
        cambio = ultimo - anterior
        if cambio < -200:
            signals.append(Signal(
                tipo="VENTA", ticker="DLR (vto con liquidez)",
                motivo=f"Reservas BCRA cayeron USD {abs(cambio):.0f}M",
                fuerza="FUERTE" if cambio < -500 else "MODERADA",
                precio_entrada=0, stop_loss=0, take_profit=0,
                estrategia="macro-reservas",
            ))
        return signals

    # =========================================================================
    # Run All
    # =========================================================================
    def run_all(self, futures, spot, badlar, intel=None):
        all_signals = []

        s1 = self.estrategia_zscore(futures, spot, badlar, intel)
        all_signals.extend(s1)

        s2 = self.estrategia_gap(futures, spot)
        all_signals.extend(s2)

        s3 = self.estrategia_oi_momentum(futures, spot)
        all_signals.extend(s3)

        s4 = self.estrategia_volume(futures, spot)
        all_signals.extend(s4)

        self._save_history()

        counts = {}
        for s in all_signals:
            counts[s.estrategia] = counts.get(s.estrategia, 0) + 1
        for strat, count in counts.items():
            print(f"  [{strat}] {count} senal(es)")

        return all_signals

    def calcular_posicion(self, precio_futuro):
        # Margen real Argentina Clearing: ~20% del nocional
        nocional = precio_futuro * 1000  # 1 contrato = USD 1.000
        margen = nocional * 0.20  # 20% margen inicial
        max_margen = int(self.capital * 0.85 / margen)  # Usar max 85% del capital en margen
        max_riesgo = max(1, int(self.riesgo_max / (precio_futuro * self.SL_PCT * 1000)))
        # Max contratos depende del capital actual (dinámico)
        max_abs = 1 if self.capital < 1_200_000 else 2
        contratos = min(max_margen, max_riesgo, max_abs)
        return {
            "contratos": contratos,
            "margen_requerido": round(margen * contratos),
            "riesgo_maximo": round(precio_futuro * self.SL_PCT * 1000 * contratos),
            "capital_libre": round(self.capital - margen * contratos),
        }
