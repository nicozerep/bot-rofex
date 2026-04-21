"""
Envia una senal de prueba a Telegram con botones Ejecutar/Ignorar.
Uso: python test_signal.py
"""
from telegram_buttons import send_signal_with_buttons
from analysis import Signal

fake = Signal(
    tipo="VENTA",
    ticker="DLR/MAY26",
    motivo="PRUEBA - no es una senal real, solo para probar el flujo de botones",
    fuerza=7,
    precio_entrada=1394.0,
    stop_loss=1421.88,
    take_profit=1352.18,
    estrategia="TEST",
)
posicion = {"contratos": 1, "margen": 278800, "margen_requerido": 278800}
msg_id = send_signal_with_buttons(fake, posicion)
print(f"Senal de prueba enviada. message_id={msg_id}")
print("Tocá 'Ejecutar' en Telegram para ver si aparece en la sheet")
