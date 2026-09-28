# Simulación de carritos de Cardmarket

En Colecciones simuladas, pegar el carrito en el campo de cartas y pulsar
«Simular y Analizar Lote». Se aceptan texto de portapapeles, enlaces Markdown y
tablas Markdown, con cantidades `1x`, precios unitarios EUR y metadatos en líneas
separadas. Las listas de mazos sin precio conservan el comportamiento anterior.

Se conserva el texto original en `simulated_collections.raw_text` para volver a
analizarlo al abrir una simulación. Las compras no modifican wants, inventario,
asignaciones ni mazos. Las simulaciones antiguas sin texto siguen siendo legibles;
su antiguo campo `price` es una valoración, no un precio de compra.

## Cálculos

- Coste: precio unitario leído × cantidad. Los cálculos nuevos usan Decimal.
- Mercado: tendencia Cardmarket local, con fallback a `priceEur`. Si edición
  y número coinciden se usa esa impresión; sin coincidencia se usa la impresión
  más barata con precio positivo, marcada como aproximada. Una edición
  identificada sin cotización no toma el precio de otra edición. Foil explícito
  utiliza `priceEurFoil`. No se infiere foil de comentarios como `MF` o `0852F`.
- Diferencia: mercado menos coste, sobre las mismas copias con ambos precios.
  Porcentaje: diferencia / mercado comparable × 100. Los valores ausentes no
  representan cero y quedan excluidos de esta comparación.
- Wants: coincidencia por nombre normalizado, independientemente de la edición.
  Cobertura = copias del carrito que satisfacen wants / copias solicitadas en
  todos los wants del usuario. Se limita a la cantidad solicitada, descontando
  las coincidencias ya consumidas por filas previas del carrito. No se resta
  inventario de wants: wants es la lista de cantidades que se desea adquirir.
- Grupos excluyentes: copias que cubren wants; después, mazos activos; después,
  colección; copias extra de un nombre en wants; finalmente, cartas cuyo nombre
  no está en wants, mazos ni colección. Así, los grupos suman el coste total.
- Valor de wants menos todo el carrito: referencia de las copias que cubren wants
  menos el coste de toda la compra. Solo disponible cuando todos los costes y
  los precios de mercado de esas copias están presentes.

No se incluyen envío, comisiones ni ajustes por estado o idioma. Las cotizaciones
son las disponibles en el catálogo local al analizar, no precios de reventa
asegurados. Las métricas anteriores del lote siguen utilizando su impresión más
barata; pueden diferir del nuevo bloque que compara la edición del carrito.

## Persistencia y validación

La migración aditiva `20260926_simulated_cart_source` añade `raw_text` nullable.
Aplicarla con el flujo habitual `prisma migrate deploy` antes de desplegar los
clientes regenerados. No usar `db push`. No requiere actualizar datos antiguos.
Los tres esquemas Prisma incluyen el campo; la respuesta HTTP añade campos
opcionales, por lo que los clientes Swift existentes pueden seguir decodificándola.

Los cuatro ejemplos del usuario están en `tests/fixtures/cardmarket/`, con copia
en `frontend/tests/fixtures/cardmarket/` para que cada submódulo pueda probarse
independientemente. Se conservan nombres, cantidades, precios, comentarios,
ediciones, números, entidades HTML y formatos; el espaciado de las tablas no es
significativo. Totales de referencia:

| Fixture | Copias | Nombres únicos | Compra |
| --- | ---: | ---: | ---: |
| cart-markdown.txt | 26 | 25 | 25,19 € |
| list-1.txt | 44 | 44 | 25,17 € |
| list-2.txt | 9 | 9 | 21,25 € |
| list-3.txt | 5 | 5 | 3,45 € |

`frontend/tests/fixtures/cardmarket/list-3-analysis.json` es una respuesta de
backend calculada con cotizaciones ficticias de 2 € por carta y wants de una
copia de Hero of Precinct One y dos de Nicol Bolas, Dragon-God. No contiene
cotizaciones reales ni datos de un usuario.

Desde backend: `uv run pytest tests/test_simulated_cart.py tests/test_simulated_collections.py tests/test_import_parser.py`.
Desde frontend: `npm test -- tests/components/simulated-purchase.test.tsx tests/components/simulated-collection.test.tsx`.

## Selección de versión

Desde el detalle de una carta de la simulación se puede elegir otra impresión.
La selección se aplica a todas sus copias (incluidas varias filas del carrito con
el mismo nombre) y recalcula cotización, valor del lote, valor vendible y
comparativa de compra. El precio de compra original, cantidades y wants se
conservan. Una versión sin cotización permanece sin precio; no se sustituye por
la impresión más barata.

En el análisis temporal, `printingOverrides` contiene nombres normalizados y
IDs de impresiones; se transmite también al guardar. En simulaciones guardadas,
`PUT /api/simulated-collections/{id}/card-version` recibe `cardName` y `printingId`.
El backend comprueba propietario, presencia de la carta y pertenencia de la
impresión antes de modificar `simulated_cards.selected_printing_id`. No modifica
inventario real, wants ni mazos. La migración aditiva
`20260926_simulated_selected_printing` debe aplicarse antes de desplegar este
cambio. Los clientes Prisma de backend, worker y frontend están actualizados.
