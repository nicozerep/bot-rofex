"""
Bot ROFEX - Orquestador principal.
Recolecta datos reales, analiza y envía alertas por Telegram.
"""

import time
import schedule
from datetime import datetime

from collectors import BCRACollector, AmbitoCollector, RofexCollector
from analysis import AnalysisEngine, calcular_tasa_implicita, dias_al_vencimiento, is_macro_day
from telegram_bot import (
    send_signal, send_market_summary, send_startup_message, send_message,
)
from journal import add_trade
from tracker import register_signal, update_signals
from adaptive import recalibrate, is_strategy_enabled, format_performance_report
from market_intel import collect_all_intel


bcra = BCRACollector()
ambito = AmbitoCollector()
rofex = RofexCollector()
engine = AnalysisEngine(capital=600_000, riesgo_max_pct=0.048)


def recolectar_datos() -> dict:
    """Recolecta todos los datos del mercado."""
    print(f"[{datetime.now():%H:%M:%S}] Recolectando datos...")

    # BCRA
    bcra_data = bcra.get_all_current()
    print(f"  BCRA: TC oficial=${bcra_data.get('tc_oficial', '?')}, "
          f"mayorista=${bcra_data.get('tc_mayorista', '?')}, "
          f"BADLAR={bcra_data.get('badlar', '?')}%, "
          f"reservas=USD {bcra_data.get('reservas', '?')}M")

    # Dólares
    dolares = ambito.get_all()
    brechas = ambito.get_brecha()
    print(f"  Dólares: {list(dolares.keys())}")
    print(f"  Brechas: {brechas}")

    # Futuros DLR via pyRofex (reMarkets)
    futuros = rofex.get_market_data()
    print(f"  Futuros DLR: {len(futuros)} contratos")
    for f in futuros:
        print(f"    {f['ticker']}: ${f['precio']:,.1f} (bid=${f.get('bid', '-')} ask=${f.get('ask', '-')} vol={f['volumen']})")

    # Market intelligence (todas las variables que mueven futuros)
    intel = collect_all_intel()

    return {
        "bcra": bcra_data,
        "dolares": dolares,
        "brechas": brechas,
        "futuros": futuros,
        "intel": intel,
        "timestamp": datetime.now(),
    }


def analizar_y_alertar(datos: dict):
    """Ejecuta análisis y envía alertas si hay señales."""

    spot = datos["bcra"].get("tc_mayorista", 0)
    badlar = datos["bcra"].get("badlar", 0)

    if isinstance(spot, str) or isinstance(badlar, str):
        print("  !! Error obteniendo datos BCRA, saltando analisis de tasa implicita")
        spot = 0
        badlar = 0

    futuros = datos.get("futuros", [])
    all_signals = []

    # Ejecutar todas las estrategias con market intelligence
    intel = datos.get("intel", {})
    if futuros and spot > 0 and badlar > 0:
        signals_strat = engine.run_all(futuros, spot, badlar, intel=intel)
        all_signals.extend(signals_strat)

    # Señales macro
    if datos.get("brechas"):
        signals_brecha = engine.analizar_brecha(datos["brechas"])
        all_signals.extend(signals_brecha)
        if signals_brecha:
            print(f"  [macro-brecha] {len(signals_brecha)} senal(es)")

    try:
        reservas_df = bcra.get_variable("reservas", days_back=5)
        signals_reservas = engine.analizar_reservas(reservas_df)
        all_signals.extend(signals_reservas)
        if signals_reservas:
            print(f"  [macro-reservas] {len(signals_reservas)} senal(es)")
    except Exception as e:
        print(f"  !! Error reservas: {e}")

    if is_macro_day(datetime.now()):
        print("  !! ATENCION: dia cercano a dato macro, senales con cautela")

    # Filtrar señales de estrategias desactivadas por el sistema adaptativo
    active_signals = []
    for signal in all_signals:
        strat = getattr(signal, "estrategia", "")
        if strat and not is_strategy_enabled(strat):
            print(f"  [SKIP] {strat} desactivada por bajo rendimiento")
            continue
        active_signals.append(signal)

    # Enviar alertas y guardar en journal (solo señales nuevas)
    for signal in active_signals:
        key = signal_key(signal)
        if key in sent_signals_today:
            continue  # Ya enviada hoy, saltar

        posicion = None
        if signal.precio_entrada > 0:
            posicion = engine.calcular_posicion(signal.precio_entrada)
        sent = send_signal(signal, posicion)
        status = "OK" if sent else "FAIL"
        print(f"  [{status}] Alerta enviada: {signal.tipo} {signal.ticker}")

        sent_signals_today.add(key)

        # Registrar en tracker para seguimiento de resultado
        if signal.precio_entrada > 0:
            register_signal(signal, posicion)

        # Guardar en trade journal
        if signal.precio_entrada > 0:
            try:
                add_trade(signal, posicion)
            except Exception as e:
                print(f"  !! Error guardando en journal: {e}")

    return all_signals


def enviar_resumen(datos: dict):
    """Envía resumen diario del mercado."""
    spot = datos["bcra"].get("tc_mayorista", 0)
    futuros = datos.get("futuros", [])

    summary_data = {
        "fecha": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "dolares": datos.get("dolares", {}),
        "brechas": datos.get("brechas", {}),
        "bcra": datos.get("bcra", {}),
    }

    # Agregar futuros con tasa implícita
    if futuros and isinstance(spot, (int, float)) and spot > 0:
        futuros_con_tasa = []
        for fut in futuros:
            dias = dias_al_vencimiento(fut["ticker"])
            tasa = calcular_tasa_implicita(fut["precio"], spot, dias)
            futuros_con_tasa.append({**fut, "tasa_implicita": tasa})
        summary_data["futuros"] = futuros_con_tasa

    sent = send_market_summary(summary_data)
    print(f"  {'OK' if sent else 'FAIL'} Resumen enviado")


sent_signals_today: set = set()


def signal_key(signal) -> str:
    """Genera una clave unica para evitar enviar la misma señal dos veces."""
    return f"{signal.estrategia}|{signal.ticker}|{signal.tipo}"


def run_scan():
    """Escanea el mercado y envía solo señales NUEVAS."""
    global sent_signals_today

    # Reset diario a las 9:55
    now = datetime.now()
    if now.hour == 9 and now.minute < 56:
        sent_signals_today = set()

    datos = recolectar_datos()

    # Trackear señales abiertas con precios actuales
    futuros = datos.get("futuros", [])
    if futuros:
        cerradas = update_signals(futuros)
        for c in cerradas:
            emoji = "✅" if c["estado"] == "TP" else "❌" if c["estado"] == "SL" else "⏰"
            msg = (
                f"{emoji} <b>TRADE CERRADO: {c['estado']}</b>\n\n"
                f"Ticker: {c['ticker']}\n"
                f"Entrada: ${c['precio_entrada']:,.2f}\n"
                f"Salida: ${c['precio_salida']:,.2f}\n"
                f"P&L: <b>${c['pnl']:,.0f} ARS</b>\n"
                f"Dias: {c['dias_abierta']}"
            )
            send_message(msg)
            print(f"  Trade cerrado: {c['ticker']} {c['estado']} ${c['pnl']:,.0f}")

        # Recalibrar después de cada cierre
        if cerradas:
            result = recalibrate()
            for change in result.get("changes", []):
                print(f"  [ADAPTIVE] {change}")
                send_message(f"🔧 <b>Bot recalibrado:</b> {change}")

    signals = analizar_y_alertar(datos)

    new_count = sum(1 for s in signals if signal_key(s) not in sent_signals_today)
    if signals:
        print(f"  {len(signals)} senal(es), {new_count} nueva(s)")

    return datos, signals


def send_weekly_report():
    """Envía reporte semanal de performance por Telegram."""
    report = format_performance_report()
    send_message(report)
    print("  Reporte semanal enviado")


def run_once():
    """Ejecuta un ciclo completo con resumen."""
    datos, signals = run_scan()
    enviar_resumen(datos)
    return datos, signals


def run_scheduled():
    """Modo automático: escanea cada 15 min durante rueda."""
    print("Bot ROFEX iniciado en modo scheduled")
    print("Escaneo cada 5 min (10:00-17:15) | Alertas instantaneas")
    send_startup_message()

    # Resumen de apertura y cierre
    schedule.every().day.at("10:00").do(run_once)
    schedule.every().day.at("15:15").do(run_once)

    # Reporte semanal de performance (viernes 15:30)
    schedule.every().friday.at("15:30").do(send_weekly_report)

    # Escaneo cada 5 minutos durante la rueda DLR (10:00-15:00)
    for h in range(10, 15):
        for m in range(0, 60, 5):
            hora = f"{h:02d}:{m:02d}"
            if hora not in ["10:00"]:
                schedule.every().day.at(hora).do(run_scan)
    schedule.every().day.at("15:00").do(run_scan)

    print("Esperando proxima ejecucion...\n")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--scheduled":
        run_scheduled()
    else:
        print("=== Ejecucion manual con datos REALES ===\n")
        datos, signals = run_once()

        if not signals:
            print("\nSin senales de trading en este momento.")
        else:
            print(f"\n{len(signals)} senal(es) detectada(s).")
