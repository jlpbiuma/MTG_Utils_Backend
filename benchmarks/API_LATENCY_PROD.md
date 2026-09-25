# Latencia API en producción — 2026-09-24

Medido contra `http://192.168.0.4:8000` con usuario real (colección grande).
Harness: `benchmarks/api_latency.py` (5 corridas). Artefactos:
`api_latency_baseline_prod.json` (post-instrumentación / mid-opt) y
`api_latency_post_prod.json` (tras historial SQL + restart).

## Comparativa mediana / p95

| Endpoint | Baseline med | Baseline p95 | Post med | Post p95 | Δ med |
| --- | ---: | ---: | ---: | ---: | ---: |
| health | 8 ms | 27 ms | 10 ms | 23 ms | ≈ |
| GET /api/decks | 987 ms | 2800 ms | 816 ms | 1897 ms | −17% |
| GET /api/decks/{id} | 937 ms | 978 ms | ~900 ms* | — | (detalle legacy) |
| GET /api/decks/{id}/view | **67 ms** | 76 ms | ~76–113 ms* | — | Swift/listado ligero |
| GET /api/collection/query (grouped default) | 1683 ms | 5149 ms | 1525 ms | 1799 ms | −9% / p95 −65% |
| GET …/query?grouped=false&page=1&limit=200 | — | — | **~1.4 s*** | — | payload ~210 KB vs 4.3 MB |
| GET /api/wants/query | 905 ms | 966 ms | 874 ms | 945 ms | −3% |
| GET /api/priorities | 1037 ms | 4316 ms | 928 ms | 4389 ms | −11% (p95 frío) |
| GET /api/scryfall/search | 245 ms | 264 ms | 219 ms | 264 ms | −11% (pg_trgm) |
| POST /api/pricing (colección) | **438 ms** | 522 ms | 496 ms | 550 ms | ≈500 ms usable |
| GET /api/pricing/collection/history | 500× ~552 ms | — | **200× 1807 ms** | 2003 ms | OK (antes 500; ORM ~13 s) |
| GET /api/whatsapp/matches | 9 ms | 10 ms | 8 ms | 12 ms | ≈ |

\* remuestra puntual tras restart (3 corridas), no en el harness JSON.

## UX checklist

- [x] “Calculando precios…” / pending en selector de precios y vistas calientes
- [x] Colección SSR con `grouped=false&page=1&limit=200`
- [x] Tabs como subrutas: `/collection/*`, `/decks/[id]/*`, `/priorities/*` (back/forward)
- [x] Historial de valor colección responde 200 en ~1.8 s (antes error o ~13 s)

## Cambios desplegados

1. Middleware timing (`X-Response-Time-Ms` + logs >200 ms).
2. Pricing batch: SQL cheapest-per-name + historial DISTINCT ON; sin fan-out de reimpresiones.
3. Migración `20260924_perf_indexes` (`pg_trgm` + GIN) en prod.
4. Priorities vía helper SQL de precios baratos.
5. Lista mazos: `asyncio.gather` decks + colección.
6. Colección/wants: `page`/`limit` en flat query; cargas paralela imágenes/demanda.
7. Historial valor: agregación SQL + ventana LOCF sobre `cm_price_history`.
8. UX loading + tabs App Router.

## Notas / siguientes

- Detalle web sigue en `GET /api/decks/{id}` (~900 ms) porque el editor necesita tags/ownership ricos; `/view` (~70 ms) basta para pantallas de solo lectura (Swift).
- `collection/query` agrupado sigue cargando el grafo completo (~4.3 MB); el inventario paginado flat es el camino rápido.
- Historial ~1.8 s: si molesta, materializar snapshot diario.
- E/F/G (tuning Postgres / Redis / batch imports) no justificadas aún tras A–D.
