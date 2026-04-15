"""
Módulos de recolección de datos: BCRA API v4, Ambito scraping, MATBA-ROFEX via pyRofex, IOL API.
"""

import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from dotenv import load_dotenv
import pandas as pd
import pyRofex

load_dotenv()


class BCRACollector:
    """Datos del BCRA: tipo de cambio oficial, tasas, reservas. API v4.0."""

    BASE_URL = "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias"
    VARIABLES = {
        "tc_oficial": 4,       # Tipo de cambio minorista
        "tc_mayorista": 5,     # Tipo de cambio mayorista (billete)
        "badlar": 7,           # BADLAR bancos privados
        "reservas": 1,         # Reservas internacionales
        "base_monetaria": 15,  # Base monetaria
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        })

    def get_variable(self, name: str, days_back: int = 30) -> pd.DataFrame:
        var_id = self.VARIABLES.get(name)
        if not var_id:
            raise ValueError(f"Variable desconocida: {name}. Opciones: {list(self.VARIABLES.keys())}")

        url = f"{self.BASE_URL}/{var_id}?limit={days_back}&offset=0"

        resp = self.session.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        # v4 structure: {"results": [{"idVariable": N, "detalle": [{"fecha": ..., "valor": ...}]}]}
        results = data.get("results", [])
        if not results or "detalle" not in results[0]:
            return pd.DataFrame()

        detalle = results[0]["detalle"]
        if not detalle:
            return pd.DataFrame()

        df = pd.DataFrame(detalle)
        df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
        df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
        df = df.dropna(subset=["valor"])
        df = df.sort_values("fecha").reset_index(drop=True)
        return df[["fecha", "valor"]]

    def get_all_current(self) -> dict:
        result = {}
        for name in self.VARIABLES:
            try:
                df = self.get_variable(name, days_back=5)
                if not df.empty:
                    result[name] = df.iloc[-1]["valor"]
            except Exception as e:
                result[name] = f"Error: {e}"
        return result


class AmbitoCollector:
    """Dólar blue, MEP, CCL desde Ambito Financiero."""

    URLS = {
        "blue": "https://mercados.ambito.com//dolar/informal/variacion",
        "mep": "https://mercados.ambito.com//dolarrava/mep/variacion",
        "ccl": "https://mercados.ambito.com//dolarrava/cl/variacion",
        "oficial": "https://mercados.ambito.com//dolar/oficial/variacion",
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        })

    def get_dollar(self, tipo: str) -> dict | None:
        url = self.URLS.get(tipo)
        if not url:
            raise ValueError(f"Tipo desconocido: {tipo}. Opciones: {list(self.URLS.keys())}")

        try:
            resp = self.session.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return {
                "tipo": tipo,
                "compra": float(data.get("compra", "0").replace(",", ".")),
                "venta": float(data.get("venta", "0").replace(",", ".")),
                "variacion": data.get("variacion", "0"),
                "fecha": data.get("fecha", ""),
            }
        except Exception:
            return None

    def get_all(self) -> dict:
        result = {}
        for tipo in self.URLS:
            data = self.get_dollar(tipo)
            if data:
                result[tipo] = data
        return result

    def get_brecha(self) -> dict | None:
        oficial = self.get_dollar("oficial")
        blue = self.get_dollar("blue")
        mep = self.get_dollar("mep")
        ccl = self.get_dollar("ccl")

        if not oficial or not blue:
            return None

        venta_oficial = oficial["venta"]
        return {
            "blue_vs_oficial": round((blue["venta"] / venta_oficial - 1) * 100, 2),
            "mep_vs_oficial": round((mep["venta"] / venta_oficial - 1) * 100, 2) if mep else None,
            "ccl_vs_oficial": round((ccl["venta"] / venta_oficial - 1) * 100, 2) if ccl else None,
        }


class RofexCollector:
    """Datos de futuros DLR via CEM API (principal) o pyRofex (fallback)."""

    CEM_URL = "https://apicem.matbarofex.com.ar/api/v2/closing-prices"

    def __init__(self):
        self._pyrofex_initialized = False

    def _ensure_pyrofex(self):
        if self._pyrofex_initialized:
            return True
        try:
            pyRofex.initialize(
                user=os.getenv("REMARKETS_USER", ""),
                password=os.getenv("REMARKETS_PASSWORD", ""),
                account=os.getenv("REMARKETS_ACCOUNT", ""),
                environment=pyRofex.Environment.REMARKET,
            )
            self._pyrofex_initialized = True
            return True
        except Exception as e:
            print(f"  pyRofex no disponible: {e}")
            return False

    def get_from_cem(self) -> list[dict]:
        """Obtiene datos del día de CEM API (datos reales oficiales)."""
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        try:
            resp = requests.get(self.CEM_URL, params={
                "product": "DLR",
                "from": yesterday,
                "to": today,
                "pageSize": 200,
            }, timeout=15)
            resp.raise_for_status()
            data = resp.json().get("data", [])

            # Filtrar solo futuros (sin opciones)
            data = [d for d in data if "Call" not in d.get("symbol", "") and "Put" not in d.get("symbol", "")]

            # Agrupar por symbol, tomar el registro más reciente
            latest = {}
            for d in data:
                sym = d["symbol"]
                if sym not in latest or d["dateTime"] > latest[sym]["dateTime"]:
                    latest[sym] = d

            # Convertir al formato estándar
            meses_map = {
                "01": "ENE", "02": "FEB", "03": "MAR", "04": "ABR",
                "05": "MAY", "06": "JUN", "07": "JUL", "08": "AGO",
                "09": "SEP", "10": "OCT", "11": "NOV", "12": "DIC",
            }

            futures = []
            for sym, d in latest.items():
                settle = d.get("settlement", 0)
                if not settle or settle <= 0:
                    continue

                # Convertir DLR062026 -> DLR/JUN26
                raw = sym.replace("DLR", "")
                if len(raw) >= 6:
                    mes_num = raw[:2]
                    anio = raw[4:6] if len(raw) >= 6 else raw[2:4]
                    mes_str = meses_map.get(mes_num, mes_num)
                    ticker = f"DLR/{mes_str}{anio}"
                else:
                    ticker = sym

                futures.append({
                    "ticker": ticker,
                    "precio": settle,
                    "bid": d.get("low", None),
                    "ask": d.get("high", None),
                    "settlement": settle,
                    "volumen": d.get("volume", 0) or 0,
                    "oi": d.get("openInterest", 0) or 0,
                    "oi_change": d.get("openInterestChange", 0) or 0,
                    "tasa_implicita_oficial": d.get("impliedRate", None),
                })

            return futures
        except Exception as e:
            print(f"  Error CEM API: {e}")
            return []

    def get_dlr_tickers(self) -> list[str]:
        """Obtiene tickers de futuros DLR activos (sin opciones ni spreads)."""
        if not self._ensure_pyrofex():
            return []
        try:
            instruments = pyRofex.get_all_instruments()
            tickers = []
            for inst in instruments.get("instruments", []):
                sym = inst["instrumentId"]["symbol"]
                if (sym.startswith("DLR/")
                        and sym.count("/") == 1
                        and not any(c in sym for c in ["P", "C", "M", "A", " "])):
                    tickers.append(sym)
            tickers.sort()
            return tickers
        except Exception as e:
            print(f"Error obteniendo tickers: {e}")
            return []

    def get_market_data(self, tickers: list[str] | None = None) -> list[dict]:
        """Obtiene market data de todos los futuros DLR.

        Intenta CEM API primero (datos reales oficiales).
        Si falla, usa pyRofex (reMarkets) como fallback.
        """
        # Intentar CEM API primero
        cem_data = self.get_from_cem()
        if cem_data:
            return cem_data

        # Fallback: pyRofex
        if not self._ensure_pyrofex():
            return []

        if tickers is None:
            tickers = self.get_dlr_tickers()

        futures = []
        for sym in tickers:
            try:
                md = pyRofex.get_market_data(
                    sym,
                    entries=[
                        pyRofex.MarketDataEntry.BIDS,
                        pyRofex.MarketDataEntry.OFFERS,
                        pyRofex.MarketDataEntry.LAST,
                        pyRofex.MarketDataEntry.SETTLEMENT_PRICE,
                        pyRofex.MarketDataEntry.TRADE_VOLUME,
                        pyRofex.MarketDataEntry.OPEN_INTEREST,
                    ],
                )
                mkt = md.get("marketData", {})

                bid = mkt["BI"][0]["price"] if mkt.get("BI") else None
                ask = mkt["OF"][0]["price"] if mkt.get("OF") else None
                last = mkt.get("LA", {}).get("price") if isinstance(mkt.get("LA"), dict) else mkt.get("LA")
                settle = mkt["SE"]["price"] if mkt.get("SE") and isinstance(mkt["SE"], dict) else None
                vol = mkt.get("TV", 0) or 0
                oi = mkt.get("OI", 0) or 0

                # Precio = last > settlement > midpoint bid/ask
                precio = last or settle or ((bid + ask) / 2 if bid and ask else None)

                if precio:
                    futures.append({
                        "ticker": sym,
                        "precio": precio,
                        "bid": bid,
                        "ask": ask,
                        "settlement": settle,
                        "volumen": vol,
                        "oi": oi,
                    })
            except Exception as e:
                print(f"Error obteniendo MD de {sym}: {e}")

        return futures


class IOLCollector:
    """Datos de ROFEX via InvertirOnline API (backup, datos reales con 15min delay)."""

    TOKEN_URL = "https://api.invertironline.com/token"
    BASE_URL = "https://api.invertironline.com/api/v2"

    def __init__(self):
        self._token = None
        self._token_expiry = None

    def _authenticate(self) -> bool:
        if self._token and self._token_expiry and datetime.now() < self._token_expiry:
            return True
        try:
            resp = requests.post(self.TOKEN_URL, data={
                "username": os.getenv("IOL_USER", ""),
                "password": os.getenv("IOL_PASSWORD", ""),
                "grant_type": "password",
            }, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            self._token = data["access_token"]
            self._token_expiry = datetime.now() + timedelta(seconds=data.get("expires_in", 1100))
            return True
        except Exception as e:
            print(f"Error auth IOL: {e}")
            return False

    def _get(self, endpoint: str) -> dict | list | None:
        if not self._authenticate():
            return None
        try:
            resp = requests.get(
                f"{self.BASE_URL}{endpoint}",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception:
            return None

    def get_futures(self) -> list[dict]:
        """Intenta obtener futuros desde IOL."""
        data = self._get("/Cotizaciones/futuros/argentina/Todos")
        if not data or not data.get("titulos"):
            return []

        futures = []
        for t in data["titulos"]:
            sym = t.get("simbolo", "")
            if "DLR" in sym.upper():
                futures.append({
                    "ticker": sym,
                    "precio": t.get("ultimoPrecio", 0),
                    "volumen": t.get("volumenNominal", 0),
                    "oi": t.get("interesesAbiertos", 0),
                })
        return futures
