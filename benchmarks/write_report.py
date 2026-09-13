"""Render the measured results; refuses to report an unfinished SQL run."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
r = json.loads((HERE / "results.json").read_text())
b = json.loads((HERE / "bulk_catalog_results.json").read_text())
w = json.loads((ROOT / "worker/benchmarks/bulk_priority_results.json").read_text())
assert r["cleaned_up"] and len(r["trials"]) == 45
assert all(t["quantity_verified"] for t in r["trials"])
rows = []
for case in dict.fromkeys(t["case"] for t in r["trials"]):
    values = {m["variant"]: m["seconds"] for m in r["medians"] if m["case"] == case}
    rows.append(f'| {case} | {values["current"]:.3f} | {values["grouped"]:.3f} | {values["batched"]:.3f} |')
worker_rows = []
for s in w:
    c = s["counts"]
    worker_rows.append(f'| {s["label"]} | {c["collection_http"]} | {s["stored_unique"]} | {s["pending_unique"]} |')
text = f'''# Evaluación de importaciones masivas — 2026-09-12

## Conclusión

Para miles de cartas hay dos mejoras independientes: importar la colección con
operaciones SQL por lotes y reducir/gestionar el enriquecimiento remoto. El
limitador de Scryfall evita ráfagas, pero no elimina miles de consultas por carta.
Las pruebas favorecen lotes y catálogo local; agrupar repetidas no ayuda de forma
consistente cuando todas las entradas son distintas.

Son prototipos de evaluación, no cambios instalados en los endpoints de la app.
No se han subido miles de consultas a la API de Scryfall ni se han modificado
colecciones de usuarios. El esquema temporal se eliminó al terminar.

## 1. PostgreSQL real: importación de texto

Se seleccionaron 5.000 nombres reales del catálogo local (orden determinista por
MD5 del nombre), entre ellos Helium Squirter, Epic Experiment, Desert, Prognostic
Sphinx y Temple of Epiphany. La lista exacta está en `card_names.json`.

Tres repeticiones por escenario y alternativa, con orden rotatorio: 45 ejecuciones.
Se comprobó la cantidad de cada carta almacenada, no solo el total. Cada prueba
empezó con una colección vacía. `warm` significa que el catálogo contiene los
nombres del caso; `cold`, que el catálogo de prueba está vacío. No son estados de
la caché del sistema operativo. La prueba de repetidas tiene 5.000 líneas de
cantidad 1 distribuidas entre 1.000 nombres, cinco apariciones de cada uno.

El caso `current` ejecuta `CollectionService.import_collection_text` real con
Prisma y PostgreSQL reales. Solo se sustituye el envío al worker por un contador.
Los otros dos casos son prototipos dentro de `bulk_import.py`:

- `grouped`: agrupa cantidades por nombre normalizado y llama al flujo existente.
- `batched`: agrupa, consulta catálogo en lotes de 500 y hace INSERT/ON CONFLICT
  en lotes de 500 dentro de una transacción, con incremento atómico. Valida
  cantidades antes de escribir. Solo solicita resolución urgente de ausentes.

**Mediana de segundos; no incluye procesamiento posterior del worker:**

| Escenario | Actual | Agrupar | SQL por lotes |
|---|---:|---:|---:|
{chr(10).join(rows)}

Las diferencias incluyen menos viajes a PostgreSQL, menos materialización de
respuestas ORM y transacciones agrupadas. No atribuimos toda la ganancia a una
única optimización. Los servicios de la aplicación seguían activos: el esquema
estaba aislado, el servidor y la máquina no. No son pruebas de carga multiusuario
ni latencias HTTP de extremo a extremo. Tres repeticiones no estiman un p99.
El catálogo de prueba conserva ID, nombre, normalización, coste, tipo, imagen,
set y número de coleccionista; omite los JSON extensos de detalles y relaciones.
Una consulta por lotes de producción debería seleccionar solo los campos que
necesita. No se ha medido el coste de cargar todos los detalles reales.

Con catálogo completo, la implementación actual sigue enviando todos los nombres
al worker. El prototipo envía cero **resoluciones urgentes**: no significa que las
imágenes, traducciones, precios y rulings estén completos o actualizados. Esas
tareas necesitan su propia política de enriquecimiento y caducidad.
El cliente actual ya elimina nombres repetidos dentro de una solicitud. Agrupar
las líneas mejora la importación SQL, pero no reduce por sí solo las llamadas
remotas de una única subida. La deduplicación entre subidas es otra mejora.
En el caso de 5.000 líneas/1.000 nombres, el contador `uniqueCards` actual devuelve
5.000 aunque se guardan 1.000 filas: cuenta entradas, no cartas únicas.

Al incluir una línea válida y otra con cantidad cero:
```json
{json.dumps(r['invalid_input'], indent=2)}
```
El flujo actual puede dejar una importación parcial tras devolver un error. Si el
usuario repite el fichero, se pueden volver a sumar esas cantidades. La versión
final debe validar todo y registrar un identificador de importación idempotente.

## 2. Worker real con dependencias simuladas

`worker/benchmarks/bulk_priority.py` ejecuta el cliente y el flujo del worker con
los mismos nombres. HTTP, almacenamiento de imágenes y base de datos son dobles
de prueba. Los objetos devueltos son sintéticos; no se miden tiempos de red.
Se supone un Oracle ID distinto por nombre y ninguna edición de prueba existente
en CardSet. Los contadores reflejan ese escenario, no una medición de producción.

| Caso | Llamadas collection | Nombres guardados | Pendientes |
|---|---:|---:|---:|
{chr(10).join(worker_rows)}

Hallazgos:

- Con 5.000 nombres únicos hay 67 lotes collection, pero además se solicitan
  5.000 búsquedas de español y 5.000 cargas de rulings, y se invoca 5.000 veces
  el almacenamiento de imágenes. Una llamada de almacenamiento no equivale a
  una descarga: MinIO ya evita descargar un original existente.
- Si el lote 67 falla tras agotar reintentos, el flujo actual no persiste los
  66 anteriores. El prototipo llama al worker y guarda después de cada lote:
  conserva 4.950 nombres y deja 50 pendientes. Falta implementar la cola durable
  para que esos 50 se reanuden automáticamente.
- Dos subidas solapadas de 1.000 nombres provocan 2.000 enriquecimientos. Al
  entregar una sola lista compartida quedan 1.000. Esto prueba el ahorro posible
  de deduplicar el trabajo, no implementa ni valida una cola distribuida.
- Si Scryfall devuelve un nombre en `not_found`, el resultado actual puede
  indicar `errors: 0` aunque falte una carta. Hay que informar por separado
  completadas, pendientes, no encontradas y fallidas.

## 3. Descarga bulk real e índice local

Se descargó una sola vez el archivo oficial Oracle Cards y se recorrió su gzip
JSONL en streaming, guardando en memoria un índice reducido de nombre e ID.
Después se buscaron los mismos 5.000 nombres tres veces. No se importó este archivo
en PostgreSQL. Es una prueba de resolución local y del coste inicial de descarga,
no una comparación directa contra el tiempo SQL de la tabla anterior.

- URL: {b['source_url']}
- Tamaño comprimido: {b['compressed_bytes'] / 1_000_000:.2f} MB.
- Descarga observada: {b['download_seconds']:.3f} s (una ejecución).
- Lectura y construcción del índice: {b['index_seconds']:.3f} s.
- Registros leídos: {b['source_records']}; claves: {b['indexed_keys']}.
- Colisiones al normalizar nombres: {b['normalized_collisions']}.
- Nombres solicitados con clave ambigua: {len(b['ambiguous_names'])}. Ejemplos:
  {json.dumps(b['ambiguous_names'][:8], ensure_ascii=False)}. Lista completa en el JSON.
- Nombres con coincidencia: {b['resolved_names']}/{b['requested_names']}; sin
  colisión de clave: {b['resolved_names'] - len(b['ambiguous_names'])}.
- Mediana de las 5.000 búsquedas: {b['lookup_median_seconds']:.6f} s.
- No resueltos: {json.dumps(b['missing_names'], ensure_ascii=False)}.

Oracle Cards contiene una representación por Oracle ID: no basta para preservar
la identidad de cartas cuyo nombre normalizado sea ambiguo; el prototipo conserva
la última coincidencia y esas claves necesitan desambiguación. Tampoco basta para
la edición de una colección física. Para ediciones usar Default Cards; para todos
los idiomas, All Cards; para rulings, el archivo correspondiente. Las imágenes
siguen descargándose aparte bajo demanda. La documentación actual describe
archivos `.jsonl.gz`; no conviene diseñar el importador suponiendo un array JSON.
Una coincidencia de nombre no valida la edición física ni su idioma. Por tanto,
**no afirmamos haber identificado correctamente las 5.000 cartas**, ni recomendamos
usar este índice simplificado en producción sin resolver las ambigüedades.
[Fuente oficial](https://scryfall.com/docs/api/bulk-data).

## Opciones recomendadas

1. **Importación SQL por lotes y transacción.** Validar antes, sumar cantidades
   atómicamente, preservar edición/acabado y registrar la identidad de la subida.
   El prototipo solo valida nombres y cantidades; no está listo para sustituir
   todas las reglas de metadatos, variantes y respuestas del endpoint.
2. **Cola persistente en PostgreSQL y progreso por fases.** Guardar la colección y
   los trabajos en la misma transacción; responder con un ID de importación.
   Deduplicar trabajos de enriquecimiento, nunca cantidades de colecciones.
   Procesar lotes pequeños, conservar avance, respetar Retry-After y reintentar
   solo pendientes. La cola del backend y las tareas asyncio actuales son volátiles.
3. **Catálogo bulk local.** Resolver la mayoría de nombres/ediciones localmente;
   reservar API para ausentes y cambios recientes. Separar caducidad de precios
   de la de metadatos. Prever actualización, validación del snapshot y sustitución
   sin dejar el catálogo vacío si falla una descarga.
4. **Enriquecimiento diferido.** Primero disponibilidad de la colección; después
   español, imágenes y rulings, priorizando cartas visibles y evitando trabajo
   ya completo. Más consumidores solo con límites globales compartidos.

VPN, más reintentos o más procesos no atacan los costes observados. No se han
medido proveedores alternativos, migraciones de infraestructura ni rendimiento
de una futura cola persistente; no atribuimos mejoras a opciones no probadas.

## Repetición y evidencias

Desde `backend`:
```sh
.venv/bin/python benchmarks/bulk_import.py
.venv/bin/python benchmarks/bulk_catalog.py --url '{b['source_url']}'
```
Desde `worker`:
```sh
.venv/bin/python benchmarks/bulk_priority.py
```
La primera prueba requiere PostgreSQL local y permisos para crear/eliminar su
esquema temporal. `BENCHMARK_DATABASE_URL` permite apuntar a otro entorno sin
parámetros de URL. Lee exclusivamente el catálogo de public. El JSON final
confirma `cleaned_up: true`. La URL bulk es el snapshot medido: para futuras
pruebas obtener la URL publicada ese día en la documentación/API oficial.

Resultados: `results.json`, `run.log`, `bulk_catalog_results.json` y
`worker/benchmarks/bulk_priority_results.json`. Lista de nombres: `card_names.json`.
'''
(HERE / "RESULTS.md").write_text(text)
manifest = {}
for path in [HERE / "bulk_import.py", HERE / "bulk_catalog.py", ROOT / "worker/benchmarks/bulk_priority.py",
             ROOT / "backend/src/services/collection_service.py", ROOT / "worker/src/services/scryfall.py",
             ROOT / "worker/src/worker.py"]:
    manifest[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
(HERE / "source_hashes.json").write_text(json.dumps(manifest, indent=2))
print(HERE / "RESULTS.md")
