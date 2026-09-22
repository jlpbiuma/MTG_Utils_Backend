# Lecturas para las vistas nativas

Swift utiliza estos endpoints para cargar una pantalla sin peticiones adicionales de precios o enriquecimiento. Los endpoints anteriores siguen disponibles para la web y otros consumidores.

| Vista | Petición inicial | Contenido |
| --- | --- | --- |
| Lista de mazos | `GET /api/decks` | Resúmenes; no detalles individuales. |
| Detalle de un mazo | `GET /api/decks/{id}/view?provider=cardmarket` | Cartas del mazo, propiedad, asignaciones relevantes, identidad del comandante del catálogo local y precios almacenados. |
| Colección | `GET /api/collection/view?provider=cardmarket` | Cartas y precios almacenados, en una respuesta. |

Los endpoints `/view` son de solo lectura. No consultan Scryfall, no solicitan enriquecimiento y no actualizan cartas. Los precios faltantes quedan sin cotización; la carga inicial no espera a obtenerlos. `provider` acepta `cardmarket`, `cardtrader` y `mtggoldfish`.

El detalle verifica la propiedad del mazo antes de leer colección, asignaciones o precios. Sus consultas de propiedad devuelven cantidades agrupadas solo para los nombres presentes en ese mazo. Se mantiene la normalización por nombre/cara frontal y la equivalencia entre reimpresiones. Las tierras básicas y el sideboard conservan las reglas del detalle existente.

La colección sigue siendo una respuesta completa, porque Swift agrupa, ordena y filtra localmente. Estos endpoints no implementan paginación. El historial del valor se consulta al desplegarlo; las pestañas no visitadas no se construyen.

Desplegar el backend con estos endpoints antes de distribuir la app actualizada. No se realiza un fallback silencioso a las lecturas antiguas, que volvería a introducir enriquecimiento durante la carga.

Pruebas de contrato: `tests/test_deck_view.py`, `tests/test_collection_view.py` y `frontend_swift/mtg-utilsTests/BackendLoadingTests.swift` en el repositorio raíz. Las pruebas HTTP usan mocks; no sustituyen mediciones en el servidor y el dispositivo reales.
