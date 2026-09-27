# Sieve

> Convierte conversaciones crudas de comunidad en contenido publicable.

Los equipos de comunidad pierden su mejor material en el scroll. Un estudiante consigue
su primer empleo en `#logros-y-empleos` y desaparece en una semana. Alguien hace
exactamente la pregunta que tres personas iban a hacer. Las palabras que harían un buen
post de LinkedIn ya existen: nadie tiene tiempo de ir a buscarlas.

Sieve lee la conversación, decide qué momentos vale la pena sacar a la superficie, y los
convierte en activos terminados: un post de LinkedIn, undestacado de newsletter, una
entrada de FAQ.

**Un tamiz no inventa nada. Deja pasar lo valioso.**

---

## Arquitectura

Siete etapas. Cada una es un módulo aparte, testeable por separado, y ninguna sabe nada
de los detalles internos de la siguiente.

```
   ① INGESTA ──────►  ② NORMALIZAR ──────►  ③ ANALIZAR ──────►  ④ PUNTUAR
   JSON/CSV         esquema Pydantic      LLM: sentimiento   fórmula ponderada
   Webhook          dedupe, validación    temas, señales     (explicable)
                                    │
                                    ▼
   ⑦ PUBLICAR ◄──  ⑥ GENERAR ◄──  ⑤ ENRUTAR ◄──────────────────┘
   OCI Object       un prompt por         rama
   Storage          canal + few-shot      condicional
```

**Por qué la lógica de dominio vive en Python y no en n8n.** n8n es el gatillo y el
pegamento (`Webhook → HTTP → IF → OCI → Response`). La lógica vive en Python porque la
lógica necesita diffs, tests unitarios y stack traces. Un workflow es un único JSON
opaco: no se puede revisar, y no se puede testear la rama condicional. El brief permite
explícitamente `n8n, Python, or equivalents`; acá se toma esa opción y el repo queda
legible.

---

## El problema de la puntuación, y cómo se resuelve

Sin ordenar, la salida suena a que la escribió un robot. La fórmula es deliberadamente
simple y **deliberadamente explicable**:

```
score = 0.30 · sentimiento         análisis LLM
      + 0.25 · saliencia de tema    cuántas veces vuelve el tema en el lote
      + 0.20 · recencia             decaimiento exponencial
      + 0.15 · señal de logro       "contratado", "primer empleo", "egresado"…
      + 0.10 · señal de interacción  longitud, preguntas, reacciones
```

Explicable le gana a ingenioso. En una demo se puede señalar un mensaje y decir *"entró
con 0.82 porque es un anuncio de contratación y el tema 'Contratación' aparece tres
veces en este lote"*. Eso es un argumento de dueño de producto, no de ingeniero.

Después el router ramifica sobre ese score:

| Condición | Activo |
|---|---|
| `score ≥ 0.65` **y** señal de logro alta | `caso_exito` → post de LinkedIn + bloque de newsletter |
| parece pregunta **y** `score ≥ 0.35` | `faq` → tutorial / tip |
| `score < 0.35` | `highlight` → resumen semanal de la comunidad |

Las preguntas se detectan con una heurística léxica barata, no con el LLM. El modelo
gasta tokens en juzgar; no los gasta en `str.endswith("?")`.

---

## El contrato

`src/sieve/models.py` es el centro del proyecto. El brief de ONE define una forma exacta
de entrada y salida en JSON; si los modelos no validan contra él, el entregable no está
cumplido por más bueno que sea el copy generado.

Pydantic convierte ese documento en algo que el intérprete hace cumplir en runtime:

```python
from sieve import validar_contrato_documento

respuesta = validar_contrato_documento(payload)   # levanta si la forma está mal
```

`tests/test_contrato.py` valida contra los propios payloads JSON del brief, carácter por
carácter. Si el contrato cambia, el test se rompe y nombra el campo.

---

## Ingesta (M1)

La ingesta es la etapa donde un proyecto como este suele morir, y siempre por lo mismo: en
el momento en que necesita internet. Así que la regla es estructural.

**Todos los tests de este repo corren con la red bloqueada.** No "usan fixtures":
`socket.connect` está reemplazado en `tests/conftest.py` y levanta si algo intenta abrir
una conexión. Un test que en silencio le pega a una API de terceros no es un test, es una
mina: pasa en tu máquina, falla en CI, y puede quemar un rate limit que nadie presupuestó.

Ese guard existe por un bug que ahora previene. Un test reconstruía a mano la clave del
cache, no coincidía con lo que generaba el cliente, y en vez de fallar bajó 100 preguntas
reales en silencio. El arreglo fue arquitectónico, no un parche: la llamada HTTP vive
detrás de un transporte inyectable, así que un test offline es un argumento de
constructor y no una promesa en la documentación.

```python
from sieve.ingestion import ClienteStackExchange, clave_de_preguntas

# Pre-calentar una vez, desde una máquina con red. Lee 1 request, no 100.
cliente = ClienteStackExchange()
cliente.preguntas(paginas=1, minimo_score=1)

# Todo lo que sigue se sirve del disco.
cliente.preguntas(paginas=1, minimo_score=1)
```

### Por qué la API y no el dump

`es.stackoverflow` está disponible como un dump comprimido de 528 MB. Este proyecto nunca
lo baja. La API devuelve los mismos campos en JSON por HTTP, no cuesta disco, y la
máquina del brief tiene 5 GB libres al 96% de uso. El dump lo llenaría con datos que la
API regala. También sería mala pinta en un repo de portfolio: 528 MB de preguntas
scrapadas al lado de un reclamo de respetar las fuentes de datos.

La única restricción dura que impone la API son **300 requests por día**, anunciado en el
header `quota_remaining`. Por eso el cliente es cache-first, lleva su propia cuota, y
`--cache-only` se niega a tocar la red antes que gastar un request que no querías gastar.

### El ground truth no es una feature

`Interaccion` es lo que alguien dijo. `GroundTruth` es lo que los otros humanos de la
plataforma concluyeron sobre eso: upvotes, cantidad de respuestas, y `accepted_answer_id`.
Son tipos separados a propósito, porque mezclarlos es el error que vuelve infalsificable
un sistema de ranking: si el producto lee la columna de score, está calificando su propia
tarea.

`GroundTruth` es **solo para evaluación**. El producto nunca lo consume. Lo que compra es
la otra mitad de la pregunta — no *"¿mi score queda lindo?"* sino *"¿mi score ordena las
cosas como lo hacen las personas?"*. `accepted_answer_id` es un juicio humano tan honesto
como el que una API te da, y solo el 43% de las preguntas en español de Stack Overflow
tiene una, lo que vuelve valiosas esas justamente porque son escasas.

`GroundTruth.intensidad()` trata la aceptación como un **piso, no un peso**. Una pregunta
cuya respuesta aceptó un humano le gana a una con 100 upvotes y sin respuesta aceptada,
sin importar cómo se ordenen los otros términos. Al revés, se estaría ordenando por
popularidad mientras se afirma ordenar por valor.

### CLI

```bash
sieve ingest data/samples/lote_demo.json          # sintético, offline
sieve ingest exports/chat.csv --salida out.json   # CSV, delimitador autodetectado
sieve fetch-se --paginas 2 --cache-only           # lee cache o falla, nunca la red
sieve cache --limpiar
```

Los delimitadores de CSV se autodetectan porque un archivo separado por comas exportado
por Excel en locale español usa `;`, y parsearlo con coma produce una única columna
llamada `autor;canal;texto` — un fallo que aparece tres etapas después, cuando el LLM
reporta que nadie tiene autor.

---

## Análisis (M2)

Cada mensaje recibe sentimiento, temas, señales de logro, detección de pregunta, relevancia
y una razón explicada. El análisis es **opcionalmente LLM, nunca dependiente**: sin
proveedor, corre la heurística y punto.

```bash
sieve analyze data/samples/lote_demo.json                     # heurística pura, cero red
sieve analyze datos.json --provider ollama --modelo qwen2.5:3b # LLM local
sieve analyze datos.json --provider ollama --salida out.json  # persiste el lote
```

### El LLM es un escalón, no el piso

La degradación tiene tres escalones y baja el costo en cada uno:

1. La ventana entera sale bien → se usan todos sus mensajes.
2. Faltan mensajes → se reintentan **solo esos**, de a uno.
3. Uno sigue faltando → heurística para ese mensaje, y el lote sigue completo.

Un lote de 100 donde 40 cayeron a heurística **no es** un lote de 100 análisis con LLM: es
otra cosa. Por eso cada resultado declara su `procedencia` y el lote persiste el conteo:

```json
"procedencia": { "llm": 7, "heuristico": 0, "mixto": 0, "con_citas_descartadas": 0 }
```

`con_citas_descartadas` va separada del conteo a propósito. Un lote puede decir `llm=7` y
tener 2 análisis con citas inventadas: eso no es lo mismo que 7 análisis de fiar. Por eso
el validador rechaza un lote cuya procedencia no sume, y `de_fiar` exige las dos cosas —
que venga del LLM y que nadie haya inventado una cita.

### Las citas se verifican o no existen

`Analisis.citas` pide comillas textuales del mensaje. Un LLM alucina, así que toda cita se
verifica contra el texto original antes de persistirse: si no aparece, se descarta y se
cuenta. Una cita que no se puede verificar contra el original no puede terminar en un post
publicado con el nombre de otra persona.

### Rendimiento medido, no supuesto

El modelo por defecto es `qwen2.5:3b` corriendo en Ollama. Tres corridas reales de
**7 mensajes** en **CPU** (sin GPU), una sola llamada, ~2040 tokens:

| corrida | latencia |
|---|---|
| 1 | 211s (3m31s) |
| 2 | 238s (3m58s) |
| 3 | 280s (4m40s) |

El rango es ancho porque en CPU la velocidad depende de la carga de la máquina, y por eso
el timeout es holgado en vez de ajustado al promedio: un timeout que se cae una vez de cada
tres es peor que uno que nunca se cae, porque el promedio esconde el fallo. Con GPU el
mismo lote baja de 5 minutos a menos de uno.

El timeout de red es configurable (`LLM_TIMEOUT_SECONDS`, 900s por defecto) porque uno corto
corta antes de que el modelo termine: peor que todo, porque se pierde el LLM y además saltea
el error.

`--modelo` existe para comparar dos modelos en la misma corrida. Comparar prompts exige
que **solo** cambie el modelo.

---

## Estado

| Hito | Qué produce | Listo |
|---|---|---|
| **M0** Cimientos | repo, estructura, contrato Pydantic | ✅ |
| **M1** Ingesta | loaders JSON/CSV, fuente Stack Exchange, cache offline | ✅ |
| **M2** Análisis | sentimiento y temas con LLM, heurística como piso | ✅ |
| **M3** Puntuación y router | fórmula ponderada + ramas, con tests | |
| **M4** Activos | 3 formatos, prompts few-shot, JSON validado | |
| **M5** OCI | bucket, PAR, política IAM acotada | |
| **M6** Interfaz | panel de curaduría Streamlit con aprobar/rechazar | |
| **M7** Orquestación | flujo n8n sobre la API | |
| **M8** Pulido | README, diagrama, demo, tag `v1.0` | |

Cada hito termina en algo que corre de punta a punta. Ningún hito es "el 80% del módulo
funciona".

---

## Quickstart

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env      # después cargar tus API keys

pytest                    # contrato + ingesta + análisis, todo offline
mypy src/sieve            # strict
ruff check .
```

---

## Política de datos

**Ningún dato real de comunidad de plataformas privadas entra a este repositorio.**

Mensajes de Discord y Slack son datos personales de personas identificables. Mandarlos a
una API de LLM de terceros es un procesamiento sin consentimiento ni base legal, y
publicarlos es peor. El dataset en `data/samples/` es enteramente sintético — personas
ficticias, mensajes escritos a mano.

La única fuente viva es la API de Stack Exchange, sobre `es.stackoverflow`, porque su
contenido tiene licencia abierta y su API trae el nombre del autor, un link a su perfil,
el link a la publicación, y la licencia por publicación en la respuesta. Las cuatro se
guardan, y [`ATTRIBUTION.md`](ATTRIBUTION.md) es un archivo real, no una promesa.

---

## Stack

Python 3.11 · Pydantic v2 · adaptador de LLM agnóstico de proveedor (Gemini / OpenAI /
Ollama) · OCI Object Storage (Always Free) · Streamlit · n8n · pytest · Ruff · mypy

---

## Licencia

Código: MIT. Datos de terceros: ver [`ATTRIBUTION.md`](ATTRIBUTION.md).
