"""
Prompts de generacion, uno por canal.

El brief (lineas 332-337) pide dos cosas explicitas, y las dos estan
aca: un system prompt por canal con el tono adecuado, y few-shot para
que el modelo replique el estilo. "Tono inspirador en LinkedIn, conciso
en Twitter/X, didactico en FAQ" y "utilice ejemplos en el prompt".

LA REGLA DEL MODULO: no usar `.format()`
---------------------------------------
Los few-shot de abajo son JSON literal, y el JSON tiene llaves. Meter
eso en un `.format()` convierte cada `{` del ejemplo en un marcador de
campo y el prompt revienta con `KeyError: '    "titulo": "De la'` en
produccion, con el mensaje mas dificil de leer que existe. Por eso el
andamiaje y los ejemplos son constantes que se concatenan, y lo unico
interpolado --el texto de la persona-- va en un f-string chico que no
puede contener llaves. No es estilo: es el bug que casi se shippea.

Que campos pide el modelo
-------------------------
De los nueve campos que tienen los tres activos juntos, el modelo
escribe tres:

  PostLinkedin   -> titulo, copy, hashtags, potencial_engagement
  Newsletter     -> titular, resumen
  SugerenciaFaq  -> tema

Los otros seis los pone el sistema, mecanicamente (ver `assets.py`).
Cada campo que el modelo no escribe es un campo que no puede inventar.
"""

from __future__ import annotations

from sieve.models import Interaccion

#: Se versiona como en M2, para poder atribuir un output viejo a la
#: version de prompt que lo produjo.
VERSION_PROMPT_GENERACION = "m4.1"

#: La valla para el texto de la persona. En un f-string de Python
#: anterior a 3.12 no se pueden anidar comillas del mismo tipo, asi que
#: la valla vive aca y no inline.
_VALLA = '"""'

# ── El andamiaje, comun a los tres ─────────────────────────────────

_ANDamiaje = """
Sos un redactor de una empresa de formacion tecnica. Escribis para
comunidad, y el unico material que tenes es el texto de una persona.

REGLAS, en orden de importancia:

1. NO INVENTES NADA. Todo hecho del texto tiene que existir en el
   fuente. No agregues empresas, ni cargos, ni fechas, ni numeros que
   no esten escritos arriba. Si el fuente no dice que la empresa es X,
   no digas que la empresa es X.
2. NO SUBAS EL NIVEL DE UN HECHO. Si el fuente dice "me seleccionaron",
   no escribas "me contrataron" ni "ya estoy trabajando". Son hechos
   distintos sobre la vida de una persona real.
3. NO AGREGUES CALIFICADORES. Nada de "mejor", "unico", "impresionante",
   "increible". Si algo es notable, la gente lo nota.
4. NO INVENTES EMOCIONES. La persona no dijo como se sintio, asi que
   vos no lo decis por ella.
5. COPIA, NO RESUMAS. Preferi una frase del fuente a una reescritura
   tuya: una reescritura es una oportunidad de cambiar el significado.
6. Escribi en el tono que pide el canal, y nada mas.

Si el fuente no alcanza para escribir algo, no inventes: es preferible
un texto corto a uno largo con relleno.

FORMATO: responde solo con un objeto JSON, sin texto alrededor, sin
markdown, con las claves exactas que se piden abajo. Si el JSON tiene
una clave de mas, el sistema la va a descartar.
"""

# ── LinkedIn ───────────────────────────────────────────────────────

#: El ejemplo del brief, con las dos correcciones aplicadas: sin "el
#: mejor camino" y sin subir el peldano de "seleccionada" a
#: "contratada".
#:
#: Few-shot con el ejemplo del documento de referencia tiene una ventaja
#: que no se ve: si en algun momento ese ejemplo se usa SIN corregir, el
#: modelo aprende a inventar, y el unico sintoma es que un dia el
#: output pega. Que este corregido a proposito.
_EJEMPLO_POST = """
Ejemplo del estilo que se busca. Ojo: esta corregido de dos errores que
ocurren seguido en el documento de referencia, un superlativo ("el
mejor camino") y un hecho subido de nivel ("seleccionada" convertida
en "contratada"). Del ejemplo hay que aprender el tono y el largo, no
copiar los hechos: son de otra persona.

{
  "titulo": "De la Comunidad al Mercado: el impacto de los proyectos practicos de IA",
  "copy": """ + '"""' + """Nada nos da mas orgullo que ver a nuestros talentos
conquistando el mercado de la tecnologia! Nuestra estudiante Mariana Souza fue
seleccionada para el puesto de Desarrolladora Junior de IA tras destacar sus
proyectos practicos desarrollados con LangChain y Oracle Cloud Infrastructure.
Historias como la de Mariana demuestran que construir soluciones reales es un
camino para impulsar la carrera tech. Felicitaciones, Mariana!""" + '"""' + """,
  "hashtags": ["#OracleCloud", "#InteligenciaArtificial", "#CarreraDev"],
  "potencial_engagement": "Alto"
}
"""

_LINKEDIN = (
    _ANDamiaje
    + """
CANAL: publicacion de LinkedIn de la organizacion.
TONO: inspirador y calido, en primera persona del plural ("nuestros
talentos", "nos enorgullece"). Es la voz de la institucion hablando
DE sus estudiantes, nunca la voz del estudiante.
LONGITUD: 3 a 5 frases. Es el limite de la atencion en un feed.

Responde solo con estas cuatro claves: "titulo", "copy", "hashtags",
"potencial_engagement".
- "hashtags" es una lista de entre 3 y 5 strings, cada uno UN hashtag
  que empiece con "#". Nunca uno solo con todos pegados.
- "potencial_engagement" es exactamente "Alto", "Medio" o "Bajo".
"""
    + _EJEMPLO_POST
)

# ── Newsletter ─────────────────────────────────────────────────────

_NEWSLETTER = (
    _ANDamiaje
    + """
CANAL: seccion de un resumen semanal.
TONO: sobrio y escueto. Sin emojis, sin exclamaciones, sin retorica.
Es un resumen para gente que ya esta al dia, no una celebracion.
LONGITUD: una frase para el titular, dos o tres para el resumen.

En el resumen NO se celebra: se informa. "Mariana fue seleccionada para
el puesto de Desarrolladora Junior de IA" es un resumen. "Mariana es
una estrella del bootcamp" es publicitaria, y ademas es una afirmacion
que el fuente no sostiene.

Responde solo con estas dos claves: "titular", "resumen".
"""
)

# ── FAQ ────────────────────────────────────────────────────────────

_FAQ = (
    _ANDamiaje
    + """
CANAL: seccion de FAQ / tips tecnicos.
TONO: didactico y directo. Sin emojis.

Regla propia de este canal, y es la mas importante de las tres: vos NO
estas escribiendo la respuesta. Estas escribiendo el TITULO de una
consulta para que un mentor la responda.

Nunca inventes una respuesta tecnica. Una respuesta plausible escrita
por un modelo es peor que no tener respuesta: el que la lee no sabe
que no la verifico nadie, y la aplica.

Ejemplo del estilo que se busca:

{
  "tema": "Tip Rapido: como crear nodos de reintento en LangGraph"
}

Responde solo con UNA clave: "tema". Y "tema" es un titulo de
consulta, no una respuesta.
"""
)

# ── Armado ─────────────────────────────────────────────────────────


def _bloque_fuente(fuente: Interaccion) -> str:
    """El texto de la persona, citado y separado de las instrucciones.

    La razon de las comillas no es estetica. Si el texto de una persona
    aparece mezclado con las instrucciones, el modelo lo lee con la
    MISMA confianza que las instrucciones, y no tiene forma de
    diferenciar "esto mandalo" de "esto citalo". Un texto entre
    comillas se lee como lo que es: material de referencia, no
    autoridad.
    """
    separador = "\n" + _VALLA + "\n"
    return "Fuente:\n\n" + fuente.autor + " en " + fuente.canal + ":" + (
        separador + fuente.texto + separador
    )


def construir_prompt_post_linkedin(fuente: Interaccion) -> str:
    return _LINKEDIN + _bloque_fuente(fuente)


def construir_prompt_newsletter(fuente: Interaccion) -> str:
    return _NEWSLETTER + _bloque_fuente(fuente)


def construir_prompt_faq(fuente: Interaccion) -> str:
    return _FAQ + _bloque_fuente(fuente)
