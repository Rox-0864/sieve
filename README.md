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

Y un detalle que conviene no esconder: **en CPU el mismo modelo no es determinista.** Con
temperatura 0.0 y `seed` fijo, dos llamadas idénticas devuelven texto distinto — es
aritmética de punto flotante no asociativa en las multiplicaciones por matriz, no sampling.
Medido sobre los 7 mensajes de muestra, dos corridas: 4 de 7 voltean `es_logro` y los 7
cambian `relevant`.

La respuesta no es intentar volver determinista el LLM, sino no depender de él en el
momento de la decisión: **M2 persiste su análisis y M3 lo lee, no lo recalcula.** Un post
publicado traza hasta el `Analisis` exacto que lo produjo. Re-correr M2 es una acción
explícita y separada.

El timeout de red es configurable (`LLM_TIMEOUT_SECONDS`, 900s por defecto) porque uno corto
corta antes de que el modelo termine: peor que todo, porque se pierde el LLM y además saltea
el error.

`--modelo` existe para comparar dos modelos en la misma corrida. Comparar prompts exige
que **solo** cambie el modelo.

---

## Puntuación y router (M3)

Cada mensaje recibe un score y una ruta. El score se explica término por término, y eso no
es un lujo: es la diferencia entre *"entró con 0.82 porque es una contratación y el tema
Contratación aparece 3 veces en este lote"* — un argumento de dueño de producto — y
`el LLM dijo 0.82`, que no es un argumento.

```python
from sieve.scoring import puntuar_lote, enrutar_lote

puntajes = puntuar_lote(resumen)      # mismo orden que resumen.resultados
for p in puntajes:
    print(p.explicacion())
    # 0.67 por sentimiento (0.97 x peso 0.37) [sin recencia: pesos renormalizados a 0.80]
```

### Un término que falta no vale cero

El 20% del score es *recencia*, y necesita timestamps. Los datos de muestra no los tienen.
La tentación es poner `0.5` para todos, y eso está mal por una razón concreta: el término
deja de distinguir y **diluye los otros cuatro en silencio**. Dos lotes con contenido
igual separados por dos horas dan el mismo score, y nadie se entera.

Lo que hace Sieve es renormalizar: si un término no se puede calcular, **no está**, y su
peso se reparte entre los que sí. El `Puntaje` declara cuáles faltaró.

El precio, dicho con todas las letras: **dos lotes con datos distintos se puntúan con reglas
distintas y no son comparables entre sí.** Por eso `terminos_ausentes` es un campo de
primer nivel, no un detalle interno — quien compare dos puntajes ve la diferencia antes de
comparar los números.

La invariante que hace que esto sea confiable: los pesos *aplicados* siempre suman 1,
pase lo que pase. Un test lo verifica para todas las combinaciones de disponibilidad.

### El router tiene un hueco y el código lo dice

Los umbrales del README cubren `score ≥ 0.65`, `pregunta y score ≥ 0.35` y `score < 0.35`.
No cubren el caso *score entre 0.35 y 0.65, sin señal de logro y sin pregunta*.

Ese caso cae a `highlight`, y no por arbitrariedad: es el activo de menor riesgo. Un
resumen semanal publica conversación real; un post de LinkedIn publica **un testimonio con
el nombre de alguien**. Cuando la especificación no dice, el destino por defecto tiene que
ser el que menos puede hacer daño. Publicar de más es un problema de reputación; publicar
de menos, uno de métricas.

El orden de las reglas también es una decisión: si un mensaje es a la vez un hito y una
pregunta, gana el hito. Un logro es la señal más cara de convertir en post, y mandarlo a
FAQ tira el mejor material del lote.

`caso_exito` y `faq` salen con `es_revision_manual=True`: no se publican sin que alguien
los firme.

---

---

## Cómo evitamos los inventos (M4)

### El problema, medido

Una llamada a `qwen2.5:3b`, una fuente de 159 caracteres, cinco problemas:

| Qué inventó | ¿Lo atrapa algo hoy? |
|---|---|
| `potencial_engagement: 120` | **Sí, el tipo.** `Literal["Alto","Medio","Bajo"]` |
| Los 5 hashtags en **un solo string** | **No.** Pasa `list[str]` con un elemento |
| `#Emprendimiento`, `#TrabajoDeInnovacion` | **No.** Ella fue dev junior, no emprendedora |
| `"un tiempo"` en vez de `"tres meses"` | No inventó: **borró** el dato concreto |
| `"Mi Travesía"`, `"Estudiante Solitario"` | **Imposible** mecánicamente |

El primero ordenó todo lo demás:

> **Todo campo que no puede estar vacío es un slot que el modelo llena con ficción.**

El modelo no puso `120` por estupidez: el campo no tenía forma de decir "no lo sé". Por eso
`potencial_engagement` es un enum de tres palabras y no un número. Quien escribió eso
entendía el problema, y el mismo criterio se aplica al resto del copy.

### El brief falla su propio ejemplo

De la **misma** fuente salen dos piezas con distinto comportamiento:

| | Fuente | Post del brief | Newsletter del brief |
|---|---|---|---|
| Estado laboral | *"queda seleccionada"* | *"acaba de ser contratada"* | *"consigue empleo"* |
| Historial | no dice nada | no dice nada | **"su primera oportunidad"** |

`seleccionada` → `contratada` → `consigue empleo`. Cada peldaño es un hecho nuevo sobre el
empleo de una persona real, y el documento sube dos de una. "Su primera oportunidad" no está
en ningún lado de la fuente: el historial de carrera de esa persona queda inventado de punta
a punta.

Es el caso más caro de la lista, y es invisible para un validador de esquema: un esquema
dice "este campo es un string", no dice "este string contradice a la fuente". Y el
fallback heurístico tiene el riesgo espejo: la fuente decía *"apenas 3 semanas
aprendiendo"* y quedó `sentimiento=neutro`. La procedencia lo delata
(`llm=0, heuristico=6`), pero si nadie lee la procedencia se lleva una impresión que nadie
midió.

Los dos casos son tests del proyecto, con el texto del brief como fixture.

### Los seis checks

El modelo propone, el código lo interroga. Se verifica **el artefacto**, nunca lo que el
modelo declara que usó: si el gate dependiera de las declaraciones, un modelo que no
declara nada pasaría todos los checks.

| Check | Atrapa |
|---|---|
| `cifras` | `120`, "6 meses" si la fuente dice 3 |
| `entidades` | `Globant`, `Nubank`, `#Emprendimiento` |
| `escalera de compromiso` | `seleccionada` → `contratada` |
| `ordinales` | `su primera oportunidad` |
| `superlativos` | `el mejor camino` |
| `citas textuales` | `"me ascendieron a directora"` |

La escalera es la que más daño evita, y compara peldaños en vez de llevar una lista de
palabras prohibidas. El estado laboral de alguien es el dato que se comparte, se cita y
dura; y `seleccionada` y `contratada` describen situaciones parecidas, así que el modelo
sube un nivel sin querer. Las formas van enumeradas completas (`contratad[oa]s?`,
`contrataron`) porque el español no tiene stem: con un prefijo `"seleccionada"` no matchea
nunca, y con un stem `"empleadora"` matchea `"empleado"`.

### Tres detalles que parecen menores y no lo son

**`"un camino"` no es el número 1.** La primera versión del check de cifras reportaba
*"1 no aparece en la fuente"* sobre un post perfectamente legítimo. Un check que llora lobo
por un artículo indefinido se desacredita solo: el humano aprende a saltear el renglón y
con él se van los hallazgos que sí importan. `"un"`, `"una"` y `"uno"` salieron de la tabla
de números, y `"treinta y uno"` sigue funcionando por la vía de los compuestos.

**Los hashtags no son hechos.** `"#CarreraDev"` no afirma nada refutable, y el brief trae
cuatro hashtags de los cuales dos no están en la fuente. Que uno no verificable **no** sea
un fallo también sería mentir: sería prometer una verificación que no existe. Van como
*marcos* al reporte, que es el trabajo del panel de curaduría.

**El nombre de la persona está fundamentado aunque el texto no lo diga.** La fuente del
brief nunca dice "Mariana": el nombre solo vive en el campo `autor`. Un check que rechaza el
ejemplo de referencia por eso tiene plumbing roto, no criterio. El corpus de verificación es
`autor + canal + texto`.

### Qué NO se verifica, y por qué está igual

`"Estudiante Solitario"` — ella no lo dijo, así que no hay nada contra qué compararlo. Las
palabras de emoción no se verifican contra una fuente que no las contiene. Ningún regex del
mundo resuelve eso.

La defensa restante no es un filtro: es un proceso. Se reduce el espacio (un copy corto y
pegado a la fuente deja menos lugar donde meterse) y hay un humano delante. Por eso el brief
llama "Panel de Curaduría" a la revisión y el equipo lo dejó como *diferencial opcional*:
con la curaduría obligatoria, este módulo es la primera capa, no la única.

`InformeGrounding` dice cuál de las dos capas está en juego: `aprobado` si no hay nada que
mirar, `aprobado_con_supuestos` si hay supuestos declarados o marcos, `rechazado` si algo no
se sostiene. Y `puede_publicarse(informe, curado)` exige **las dos** condiciones, en una
función y no en un campo, para que sea imposible publicar saltándosela por error.

### Un límite que conviene decir en voz alta

El gate vigila que no se **agregue**. No vigila que se **mantenga**: Qwen cambió `"tres
meses"` por `"un tiempo"`, que no es una invención sino una omisión, y ningún check de
grounding debería marcarla. El costo es que un post puede ser completamente cierto e
inútil. Completitud y veracidad son problemas distintos, y confundirlos es como un
fallback heurístico que no lo dice.

Lo que queda pendiente, y está anotado como tal: el panel de curaduría, el generador de
activos, y decidir si hace falta un *sanitizador* que recorte en vez de reprobar. Nada de
eso está implementado.

---

| Hito | Qué produce | Listo |
|---|---|---|
| **M0** Cimientos | repo, estructura, contrato Pydantic | ✅ |
| **M1** Ingesta | loaders JSON/CSV, fuente Stack Exchange, cache offline | ✅ |
| **M2** Análisis | sentimiento y temas con LLM, heurística como piso | ✅ |
| **M3** Puntuación y router | fórmula ponderada + ramas, con tests | ✅ |
| **M4** Grounding | 6 checks mecánicos + gate humano obligatorio | ✅ |
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
