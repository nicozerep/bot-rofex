# Setup Google Sheets para Bot ROFEX

El bot envía los trades que marcás como "Ejecutar" a una Google Sheet.
Setup una sola vez, son 5 minutos.

## Paso 1: Crear la Google Sheet

1. Andá a https://sheets.google.com
2. Creá una nueva sheet vacía
3. Renombrala: "Bot ROFEX - Trades"
4. En la fila 1 poné estos headers (uno por columna):

```
Fecha | Tipo | Posicion | Ticker | Estrategia | Fuerza | Entrada | SL | TP | Contratos | Margen | Motivo | Estado | Entrada Real | Salida Real | P&L Real | Notas
```

## Paso 2: Crear el Apps Script (webhook)

1. Con la sheet abierta, andá a **Extensiones > Apps Script**
2. Borrá todo el código que haya
3. Pegá esto:

```javascript
function doPost(e) {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  const data = JSON.parse(e.postData.contents);

  sheet.appendRow([
    data.fecha,
    data.tipo,
    data.posicion,
    data.ticker,
    data.estrategia,
    data.fuerza,
    data.entrada,
    data.sl,
    data.tp,
    data.contratos,
    data.margen,
    data.motivo,
    data.estado,
    '', '', '', ''  // campos vacíos para completar manualmente
  ]);

  return ContentService.createTextOutput(JSON.stringify({ok: true}))
    .setMimeType(ContentService.MimeType.JSON);
}
```

4. Guardá con el botón de disquete (o Ctrl+S)
5. Ponele un nombre al proyecto: "Bot ROFEX Webhook"

## Paso 3: Publicar como Web App

1. Arriba a la derecha: **Implementar > Nueva implementación**
2. Click en el ícono del engranaje y elegí **"Aplicación web"**
3. Configuración:
   - **Descripción**: Bot ROFEX Webhook
   - **Ejecutar como**: Tú
   - **Tienen acceso**: Cualquier persona (si te pregunta)
4. Click **Implementar**
5. Te va a pedir autorizar → Autorizar (dale a "Avanzado" si salta advertencia, luego "Ir a Bot ROFEX Webhook")
6. Copia la URL que te muestra — algo tipo:
   `https://script.google.com/macros/s/AKfycby.../exec`

## Paso 4: Pasarme la URL

Pasame esa URL y la configuro en el bot. A partir de ahí, cada vez que toques "Ejecutar" en Telegram, el trade aparece automáticamente en tu Google Sheet.
