# Setup Google Sheets para Bot ROFEX

El bot envía los trades que marcás como "Ejecutar" a una Google Sheet.
Setup una sola vez, son 5 minutos.

## Paso 1: Crear la Google Sheet

1. Andá a https://sheets.google.com
2. Creá una nueva sheet vacía
3. Renombrala: "Bot ROFEX - Trades"
4. Renombrá la pestaña de abajo a **"Trades"** (doble click en "Hoja 1")
5. En la fila 1 poné estos headers (uno por columna):

```
Fecha | Posicion | Ticker | Entrada | SL | TP | P&L | Motivo | Detalle
```

## Paso 2: Crear el Apps Script (webhook)

1. Con la sheet abierta, andá a **Extensiones > Apps Script**
2. Borrá todo el código que haya
3. Pegá esto:

```javascript
function doPost(e) {
  const data = JSON.parse(e.postData.contents);
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('Trades');
  sheet.appendRow([
    data.fecha,
    data.posicion,
    data.ticker,
    data.entrada,
    data.sl,
    data.tp,
    '',  // P&L se completa manualmente al cerrar
    data.motivo,
    data.detalle,
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

## Paso 4: Crear solapa "Performance"

1. Abajo, al lado de la pestaña "Trades", click en el **+** para crear otra hoja
2. Renombrala: **"Performance"**
3. Pegá esto en A1 (todo de una, se autocompleta):

| Celda | Contenido |
|-------|-----------|
| A1 | `Métrica` |
| B1 | `Valor` |
| A2 | `Capital inicial` |
| B2 | `=600000` |
| A3 | `Trades totales` |
| B3 | `=COUNTA(Trades!C2:C)` |
| A4 | `Trades cerrados` |
| B4 | `=COUNT(Trades!G2:G)` |
| A5 | `Trades abiertos` |
| B5 | `=B3-B4` |
| A6 | `Wins` |
| B6 | `=COUNTIF(Trades!G2:G,">0")` |
| A7 | `Losses` |
| B7 | `=COUNTIF(Trades!G2:G,"<0")` |
| A8 | `Win Rate` |
| B8 | `=IFERROR(B6/B4,0)` |
| A9 | `P&L total` |
| B9 | `=SUM(Trades!G2:G)` |
| A10 | `P&L promedio` |
| B10 | `=IFERROR(B9/B4,0)` |
| A11 | `Mejor trade` |
| B11 | `=IFERROR(MAX(Trades!G2:G),0)` |
| A12 | `Peor trade` |
| B12 | `=IFERROR(MIN(Trades!G2:G),0)` |
| A13 | `Profit Factor` |
| B13 | `=IFERROR(SUMIF(Trades!G2:G,">0")/ABS(SUMIF(Trades!G2:G,"<0")),0)` |
| A14 | `Capital actual` |
| B14 | `=B2+B9` |
| A15 | `Retorno %` |
| B15 | `=IFERROR(B9/B2,0)` |

4. Formato:
   - B8 y B15 → formato **Porcentaje** (botón `%` en la barra)
   - B2, B9, B10, B11, B12, B14 → formato **Moneda ARS** ($)

5. (Opcional) **Performance por estrategia** — pegá en D1:

| Celda | Contenido |
|-------|-----------|
| D1 | `Estrategia` |
| E1 | `Trades` |
| F1 | `Win Rate` |
| G1 | `P&L` |
| D2 | `OI_MOMENTUM` |
| D3 | `GAP` |
| D4 | `VOLUME` |
| D5 | `ZSCORE` |
| E2 | `=COUNTIF(Trades!H:H,"*OI_MOMENTUM*")` (arrastrar hacia abajo cambiando el texto) |
| F2 | `=IFERROR(COUNTIFS(Trades!H:H,"*OI_MOMENTUM*",Trades!G:G,">0")/COUNTIFS(Trades!H:H,"*OI_MOMENTUM*",Trades!G:G,"<>"),0)` |
| G2 | `=SUMIFS(Trades!G:G,Trades!H:H,"*OI_MOMENTUM*")` |

## Paso 5: Pasarme la URL

Pasame la URL del Apps Script y la configuro en el bot. A partir de ahí, cada vez que toques "Ejecutar" en Telegram, el trade aparece automáticamente en la solapa "Trades" y la solapa "Performance" se actualiza sola.
