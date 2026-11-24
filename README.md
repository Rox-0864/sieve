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

### El perfil de la organización: el motor no sabe de quién habla

Para que el check de entidades sirva de algo, el motor necesita saber qué palabras son
institucionales **para una organización concreta**: si no, `LangChain` en un post sobre un
proyecto de LangChain se reporta como entidad inventada, y `Felicitaciones` se reporta como
una empresa. El motor no puede ser tonto con la marca. Pero tampoco puede saber cuál es la
marca.

Esa tensión estaba resuelta de la peor manera posible: los datos de ONE estaban escritos en
`grounding.py` y `prompts.py`. El motor sabía que *"los miembros de la comunidad son
estudiantes del programa"*. Eso no es una regla de grounding, es el dato de un cliente. El
día que hubiera un segundo cliente, el motor habría mentido sobre uno de los dos, y el
error sería invisible: un check que rechaza un post legítimo de la organización equivocada.

**La regla que gobierna el diseño:**

> Un supuesto institucional autoriza decir cosas sobre la **organización**. Nunca sobre una
> **persona**.
>
> "Somos el programa ONE" → se puede, está escrito. — "Mariana es nuestra estudiante" → NO:
> sale del mensaje de Mariana, no del perfil.

Con eso, el conocimiento de marca pasó a ser un **dato**: `perfiles/one.toml`, cargado por
`sieve.perfil`. Hay tres capas, y confundirlas era el error:

| Capa | ¿De quién es? |
|---|---|
| **Contrato** (`PostLinkedin`, `destreza_newsletter_semanal`) | De ONE, legítimamente. Es su documento. |
| **Perfil** (vocabulario, supuestos, voz, constantes) | Del cliente. Hoy: un archivo. Mañana: otro. |
| **Motor** (los siete checks) | De nadie. No debería saber el nombre de nadie. |

El invariante, verificado:

```python
copy = "…un proyecto de LangChain en OCI y Alura. Felicitaciones, Mariana!"

verificar_copy(copy, fuente, perfil=cargar_perfil("por_defecto")).veredicto
#> 'rechazado'    ← entidades: 'OCI Alura' no esta en la fuente ni en el vocabulario
verificar_copy(copy, fuente, perfil=cargar_perfil("one")).veredicto
#> 'aprobado'     ← "Alura" es la marca, y de ONE sabemos que es cierta
```

Mismo texto, mismo motor, veredictos distintos. El motor es genérico; ONE es un dato.

**Y el perfil también declara lo que no sabe.** Un motor con supuestos silenciosos es un
motor que miente sobre lo que sabe. `verificar_perfil()` lista los huecos.

Eran seis antes de la investigación web. La web contestó cuatro, y quedan **cinco** de otra
naturaleza: ya no son *"qué significa esta palabra"* sino *"qué autorizo a decir sobre una
persona"*. Eso no lo contesta ningún sitio público — es de la reunión con ONE.

Las cinco están en `perfiles/one.toml`, en `pendiente_de_definir`, y **no se repiten acá a
propósito**. Un README que copia la lista es un segundo lugar donde la lista se desactualiza
en silencio, que es exactamente la clase de bug que este diseño vino a matar. La fuente es
el perfil; la CLI las imprime con `sieve perfiles`; el README explica por qué existen.

Junto a ellas, `resuelto_por_web` guarda lo que la web **sí** contestó, para que nadie vuelva
a preguntar lo que ya se sabe.

#### Y el perfil dice de dónde sale

Un perfil lleno de datos y sin fuentes es un perfil de opinión: se ve igual de bien escrito, y
por eso es el que más engaña. La sección `[verificacion]` —fuente primaria, fuente de voz,
fuente de estructura, fecha de lectura y las citas textuales literales— **está en el modelo**,
como `PerfilOrganizacion.verificacion`, no en un comentario.

Antes esas fuentes vivían en el archivo y el motor las tiraba al cargar. `de_toml` armaba un
diccionario con las claves que conocía, `extra="forbid"` rechazaba el resto, y el perfil
afirmaba estar verificado contra una URL que el código no guardaba. Nadie podía revisar si el
dato seguía en pie.

Ahora un perfil sin `fuente_primaria` es un hueco que `verificar_perfil()` declara, y el perfil
neutro no puede citar a nadie — un default con fuente puesta sería otra forma de que "el
neutro" dejara de ser neutro sin que nadie lo notara.

#### Un perfil por pipeline, no tres

El bug que cerró este trabajo no era un dato equivocado: era que **el perfil no llegaba a
todas las capas**. `grounding` recibía el perfil del cliente, pero el prompt y las constantes
del contrato se armaban con constantes de módulo congeladas al importar. Se podía correr
`sieve generar --perfil one` y obtener un activo evaluado con el vocabulario de ONE y escrito
como *"Sos un redactor. Escribís para una comunidad"*. El desajuste era invisible: los checks
no miran el prompt.

Con ONE no se notaba, y esa es la parte incómoda. El valor declarado en `perfiles/one.toml` y
el fallback hardcodeado en el motor coinciden en `"LinkedIn Oficial"`. El bug estaba tapado
exactamente en el cliente con el que se escribieron los tests. Un segundo cliente habría
publicado con el canal del primero, en silencio y sin un solo test rojo.

La regla que sale de ahí:

> **Un perfil manda a las tres capas o no es un perfil.** Si el prompt, las constantes y el
> grounding leen de fuentes distintas, hay dos clientes hablando, y solo uno lo sabe.

Hoy `GeneradorActivos.perfil` es el único lugar del que salen la identidad del prompt, las
constantes del contrato y los checks. El invariante está en `tests/test_assets.py::TestUnSoloPerfil`.

#### Un error que el perfil destapó

El supuesto global de `estudiante` declaraba *"los miembros de la comunidad son estudiantes
del programa"*. **Eso es falso en el propio documento de ONE**: el ejemplo del brief tiene
a Mariana, que sí dice *"el proyecto del curso"*, y a Lucas, que pregunta por LangGraph y no
dice en ningún lado que sea estudiante. Un supuesto global sobre "los miembros" declara algo
que el documento desmiente.

Corregido: los supuestos se declaran **por vocablo**, y lo que declaran es lo que esa
palabra sugiere, no un hecho sobre toda una población. Que la justificación exacta la
escriba la organización y no este módulo es el punto: son datos, y `perfiles/one.toml` los
marca como `[PENDIENTE]` cuando nadie los respondió.

#### Y una lista que era demasiado generosa

El vocabulario institucional tenía 57 palabras y mezclaba la marca con el ecosistema:
`kubernetes`, `postgresql`, `openai`, `gemini`. Eso hacía que el motor aceptara en silencio
que un post sobre LangChain *"compartió su proyecto con Docker"*. Son nombres reales de
cosas reales, pero **nombrarlos sigue siendo una afirmación que necesita respaldo**. El
perfil separa las tres clases, y el resultado son 19 palabras: la marca, el stack que la
organización declara propio, y el encuadre. El resto del ecosistema tiene que justificarse.

### Los siete checks

El modelo propone, el código lo interroga. Se verifica **el artefacto**, nunca lo que el
modelo declara que usó: si el gate dependiera de las declaraciones, un modelo que no
declara nada pasaría todos los checks.

| Check | Atrapa |
|---|---|
| `cifras` | `120`, "6 meses" si la fuente dice 3 |
| `entidades` | `Globant`, `Nubank`, `#Emprendimiento` |
| `escalera de compromiso` | `seleccionada` → `contratada` |
| `nivel profesional` | `Desarrolladora Junior` → `Senior` |
| `ordinales` | `su primera oportunidad` |
| `superlativos` | `el mejor camino` |
| `citas textuales` | `"me ascendieron a directora"` |

La escalera es la que más daño evita, y compara peldaños en vez de llevar una lista de
palabras prohibidas. El estado laboral de alguien es el dato que se comparte, se cita y
dura; y `seleccionada` y `contratada` describen situaciones parecidas, así que el modelo
sube un nivel sin querer. Las formas van enumeradas completas (`contratad[oa]s?`,
`contrataron`) porque el español no tiene stem: con un prefijo `"seleccionada"` no matchea
nunca, y con un stem `"empleadora"` matchea `"empleado"`.

El séptimo check (`nivel profesional`) apareció al escribir el generador, no antes.
`senior` y `junior` estaban en el vocabulario institucional para que `Sr` y `Jr` no
produjeran falsos positivos, y cumplen — pero como efecto colateral **el nivel del cargo
dejó de verificarse en ningún lado**: un post podía publicar "Desarrolladora Senior"
sobre alguien que la fuente dice Junior. Es el mismo bug que `seleccionada` →
`contratada`, y peor, porque el cargo de alguien es un hecho verificable y publicarlo
inflado no se ve como publicarlo inflado. El trade-off está escrito en el código: "reunión
con el equipo senior" es un falso positivo que se acepta a propósito, porque un hallazgo
de más cuesta que un humano mire una frase.

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

Lo que queda pendiente, y está anotado como tal: el panel de curaduría. Y decidir si hace
falta un *sanitizador* que recorte en vez de reprobar. Eso último **no** está implementado,
y la respuesta por ahora es que no: el sanitizador es una decisión de negocio disfrazada de
técnica, y primero hay que ver cuántos rechazos son de un superlativo de más.

---

## Generación de activos (M4)

### Por qué el generador no tiene verificaciones propias

`assets.py` no tiene ni una línea de lógica de verificación. La reimplementaría en peor, y
lo peor de una reimplementación parcial es que el día que se agrega un check a
`grounding.py` este módulo sigue verificando sin él, en silencio, y nadie se entera hasta
que publica un activo con un número inventado. La pipeline es: `prompt` → `extraer_json` →
`model_validate` → `verificar_activo` → gate.

La defensa real no está en el prompt, que es una petición y no se puede testear. Está en
**cuántos campos escribe el modelo**: de los nueve que tienen los tres activos, escribe
tres.

| Formato | Escribe el modelo | Escribe el sistema |
|---|---|---|
| `PostLinkedin` | `titulo`, `copy`, `hashtags`, `potencial_engagement` | `canal_recomendado`, `confianza`, `curado`, `fuentes` |
| `Newsletter` | `titular`, `resumen` | `seccion`, `confianza`, `curado`, `fuentes` |
| `SugerenciaFaq` | `tema` | `origen`, `status`, `confianza`, `curado`, `fuentes` |

Cada campo que el modelo no escribe es un campo que no puede inventar. Dos consecuencias
concretas:

- **`origen` no lo escribe el modelo.** Sale del `Interaccion`. Si lo escribiera el modelo y
  se equivocara de nombre, el nombre de una persona va al activo de otra, y el texto fluye
  igual porque el nombre es el único token que el lector no chequea.
- **`status` siempre es `derivado_a_mentoria`.** El contrato declara
  `listo_para_publicar` como default, o sea que el default del contrato es el estado
  peligroso: cualquier cosa que arme un `SugerenciaFaq` sin decir el status produce una FAQ
  que aparenta estar lista y no tiene respuesta revisada por nadie. Ese overwrite es el
  motivo por el que el generador arma el activo entero en vez de solo validarlo.

### El generador puede no devolver nada

`ResultadoGeneracion.activo` es `T | None`. Si el grounding rechaza, el activo es `None` y el
informe explica por qué. No se repara, no se reintenta a ciegas, no se devuelve "lo mejor que
pudo salir". Un pipeline que no puede devolver nada está obligado a inventar para llenar el
hueco, y el hueco se llena con lo más riesgos: un testimonio con el nombre de una persona
real.

Un rechazo por grounding y un fallo de proveedor son cosas distintas y el tipo los separa:
`rechazado_por_grounding` es `True` solo en el primero. Si se confunden, un pipeline
"reintenta hasta que pase" termina publicando el activo de otra fuente. Tampoco reintenta a
ciegas porque la segunda respuesta con otra semilla es **otro** activo, no una mejor versión
del mismo, y como el LLM en CPU no es determinista eso hace imposible afirmar que el rechazo
fue del grounding y no de la muestra.

### La corrida real, y el idioma que se le escapó

`qwen2.5:3b` en CPU, contra el mensaje del brief. Salida literal:

```
POST       -> rechazado
   titulo/entidades :: 'Estudiantes Transforman Ideas' no esta en la fuente
   titulo/entidades :: 'Soluciones' no esta en la fuente
   titulo/entidades :: 'Impacto Proyectos Practicos' no esta en la fuente

NEWSLETTER -> rechazado
   titular/entidades :: 'Desenvolvedora Júnior' no esta en la fuente

FAQ        -> aprobado   (publicable=False)
   tema: Cómo fue seleccionada como Desarrolladora Junior de IA en Retail
   status: derivado_a_mentoria
   origen: Duda planteada por Mariana Souza en LinkedIn
```

Tres de tres pasan por el mismo tubo. Dos cosas para mirar:

**El modelo escribió portugués.** "Desenvolvedora Júnior", con la palabra cambiada y el
acento puesto. Es el único sintoma de esa corrida que casi no se ve a ojo: un titular con
"Desenvolvedora" se lee perfecto para quien no fala portugués, suena igual de profesional, y
no se nota. Lo que lo delata es que la palabra **no estaba en la fuente**, y ese es el
trabajo del grounding: comparar, no leer. El caso general es peor que un idioma — un 3B puede
inventar un nombre propio que suena exactamente a un nombre propio. Esto es la versión
ruidosa de un problema silencioso.

**El FAQ pasó y no es publicable igual.** El grounding aprueba el tema, `status` y `origen`
los pone el sistema, y `publicable` sigue en `False` porque la firma es obligatoria y el panel
no existe. Que eso sea cierto *por construcción* y no por costumbre es lo que hace segura la
etapa siguiente.

Tres de los cuatro checks que fallaron fueron en el **título** o el **titular**, no en el
cuerpo. El cuerpo es largo y el modelo copia; el titular es corto, es donde se pone el
adjetivo, y es donde nadie compara contra la fuente. Un check que solo mirara el `copy` se
saltaría la mitad de los inventos.

### El bug que apareció al escribir esto

`verificar_activo` corría el check de entidades sobre `seccion = "Logro de la Semana"`, que
escribe el **sistema**, no el modelo — y reportaba que "Semana" no estaba en la fuente. El
resultado era que **ningún newsletter podía publicarse, nunca**. Ningún test de M4 lo vio
porque todos llamaban a `verificar_copy` y no a `verificar_activo`.

El fix no fue agregar `seccion` a una lista de exclusión hardcodeada: esa lista es
exactamente la que ya paró el bug del `120`, y hardcodearla se desincroniza en silencio la
próxima vez que se agrega un campo. El fix es que **quien sabe de dónde vino cada campo es el
generador**, así que ahora `verificar_activo` recibe `campos_del_modelo` y verifica
exactamente eso. Un check de contenido no puede pasar por campos que no son contenido
inventable.

### El prompt y su `.format()` prohibido

Los few-shot son JSON literal, y el JSON tiene llaves. Meter eso en un `.format()` convierte
cada `{` del ejemplo en un marcador de campo y el prompt revienta con
`KeyError: '    "titulo": "De la'` en producción. El andamiaje y los ejemplos son constantes
que se concatenan; lo único interpolado es el texto de la persona. Hay un test que falla si
alguien reintroduce el `.format()`.

El ejemplo del brief va en el few-shot **corregido**: sin "el mejor camino" y sin subir
"seleccionada" a "contratada". Few-shot con el ejemplo sin corregir entrena al modelo a
inventar, y el único síntoma es que un día el output pega.

### Lo que el generador no tiene

- No hay sanitizador que recorte. Ver la nota de arriba.
- No hay reintentos ni cache. `version_prompt` viaja en el resultado para que se vea cuándo se
  mezclaron dos versiones.
- No firma nada: `curado` siempre sale en `False`. La firma es del panel (M6), no del generador.

---

## Curaduría (M6)

El generador produce activos con `curado: False`, y así tienen que quedar hasta que un humano
los mire. El panel de curaduría es esa firma.

### Por qué el panel no cura el activo pelado

`generar --salida` escribe el activo del contrato (`ActivosGenerados`) y punto. Eso no alcanza
para decidir: aprobar sin ver de qué mensaje salió el activo, ni por qué el grounding lo
aprobó, es firmar sin leer.

Por eso `generar --curar-dir` escribe, además, un **paquete de curaduría** por activo: la
fuente, el activo, el informe de grounding y la respuesta cruda del modelo. El directorio es
el estado — no hay base de datos propia, y la decisión se persiste en el mismo archivo del
paquete.

```bash
sieve generar datos.json --provider ollama --perfil one --curar-dir data/curacion
streamlit run src/sieve/ui/curar.py -- data/curacion
```

### La firma, no la reimplementación

El panel aprueba o rechaza. Aprobar da vuelta `curado` a `True`; rechazar exige un motivo,
porque un rechazo sin razón no se puede auditar. En ambos casos quedan el curador y la fecha.

Lo que el panel **no** hace importa tanto como lo que hace: no genera, no analiza, no publica.
`puede_publicarse(informe, curado)` sigue siendo el único gate — un activo aprobado con
grounding rechazado no es publicable, y el panel no puede saltarse eso.

---

## Publicación (M5)

La curaduría aprueba; la publicación da destino. `sieve publicar` sube los activos aprobados
(`curado=True` **y** grounding limpio) a OCI Object Storage y genera un Pre-Authenticated
Request (PAR) para cada uno.

### Solo sale lo firmado

El guard es el mismo de siempre: `publicable()` exige las dos condiciones, y `PublicadorOci`
rechaza cualquier paquete que no las cumpla. Un activo pendiente o rechazado se saltea, no se
publica "por las dudas".

### Simulado por defecto

```bash
sieve publicar --curar-dir data/curacion                 # simulado: no toca la red
sieve publicar --curar-dir data/curacion --no-simulado   # sube de verdad
```

El modo simulado es el default porque un comando no debería pegarle a una nube sin que se lo
pidas explícito. Para subir de verdad hace falta el extra `oci` y credenciales válidas en
`~/.oci/config`. El cliente real se importa lazy: instalar el SDK sigue siendo opcional, y
ningún test toca la red.

El resultado de cada subida es honesto: `guardado_con_exito`, `fallido` o `simulado`. Un
fallo de escritura se reporta como `fallido`, no se esconde para que la demo se vea linda.

### Política IAM acotada

Las credenciales del pipeline tienen que poder escribir en el bucket y nada más. Una policy
de OCI acotada al bucket:

```text
Allow group <grupo-sieve> to manage objects in compartment <compartment>
  where target.bucket.name = 'communitylab-activos-marketing'
```

Sin la cláusula `where`, la policy otorga acceso a todos los buckets del compartment — eso es
lo que "acotada" viene a evitar. El `url_preautenticada` de cada subida es un PAR de solo
lectura (`ObjectRead`) con vencimiento, para compartir el objeto sin exponer el bucket entero.

---

| Hito | Qué produce | Listo |
|---|---|---|
| **M0** Cimientos | repo, estructura, contrato Pydantic | ✅ |
| **M1** Ingesta | loaders JSON/CSV, fuente Stack Exchange, cache offline | ✅ |
| **M2** Análisis | sentimiento y temas con LLM, heurística como piso | ✅ |
| **M3** Puntuación y router | fórmula ponderada + ramas, con tests | ✅ |
| **M4** Grounding | 7 checks mecánicos + gate humano obligatorio | ✅ |
| **M4** Activos | 3 formatos, prompts few-shot, JSON validado, 0 checks propios | ✅ |
| **M5** OCI | bucket, PAR, política IAM acotada | ✅ |
| **M6** Interfaz | panel de curaduría Streamlit con aprobar/rechazar | ✅ |
| **M7** Orquestación | flujo n8n sobre la API | |
| **M8** Pulido | README, diagrama, demo, tag `v1.0` | |

Cada hito termina en algo que corre de punta a punta. Ningún hito es "el 80% del módulo
funciona".

---

## Quickstart

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"        # tests, lint, types
pip install -e ".[ui]"         # para el panel de curaduría (M6)

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
