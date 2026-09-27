"""
El prompt de analisis, versionado.

Versionarlo no es mania de metadatos. Es la unica forma de responder
"¿por que el mes que viene el modelo clasifica peor?". Sin el numero de
version en cada resultado, cuando cambias una palabra del prompt no
sabes si mejoro o empeoro, y en un sistema que decide que se publica no
se puede permitir ese "no saber".

Reglas que este prompt sigue a proposito, aunque sean obvias:

1. **El modelo no ve ground truth.** Nunca. Si lo viera, el scoring
   aprende a copiar el examen. `GroundTruth` se calcula aparte y solo se
   usa para medir.

2. **Se le da el enum exacto, no una descripcion.** "Respondé con uno de:
   muy_negativo, negativo, neutro, positivo, muy_positivo" funciona
   infinitamente mejor que "evaluá el sentimiento". Cada palabra que se
   le pide al modelo tiene que ser una palabra que pueda copiar.

3. **Pocas, buenas, y textuales.** Dos ejemplos exactos, en el formato
   exacto de la salida. Un ejemplo mal escrito enseña el error; un
   ejemplo bien escrito ancla el formato.

4. **Se le dice explicitamente que no invente citas.** No por confianza
   en que lo haga bien: porque lo hace mal, y `citas.py` lo verifica
   igual. La instruccion reduce la tasa de invencion; la verificacion es
   la garantia. Las dos cosas hacen falta.
"""

from __future__ import annotations

from sieve.analysis.base import lote_a_texto
from sieve.models import Interaccion

#: Sube esto cuando cambie el prompt. Va en cada resultado, asi que un
#: analisis de marzo y uno de junio se distinguen sin adivinar.
VERSION_PROMPT_ANALISIS = "1.0.0"

#: Los valores exactos que el modelo puede devolver. Copiar esta lista
#: al prompt es parte del contrato, no una constante muerta.
_VALORES_SENTIMIENTO = (
    "muy_negativo, negativo, neutro, positivo, muy_positivo"
)

_SISTEMA = f"""\
Sos un analista de comunidades de habla hispana. Tu trabajo es leer \
mensajes de grupos tecnicos y starters, y devolver un analisis \
estructurado en JSON.

Devolves UN objeto JSON por mensaje, y nada mas. Sin texto antes, sin \
explicaciones despues, sin markdown.

FORMATO EXACTO de salida:

{{
  "resultados": [
    {{
      "indice": 1,
      "sentimiento": "<uno de: {_VALORES_SENTIMIENTO}>",
      "score_sentimiento": <numero entre -1.0 y 1.0>,
      "temas": ["<tema>", "..."],
      "es_logro": <true o false>,
      "es_pregunta": <true o false>,
      "citas": ["<fragmento EXACTO del mensaje>"],
      "relevant": <numero entre 0.0 y 1.0>,
      "razon_relevante": "<una frase, maximo 25 palabras>"
    }}
  ]
}}

REGLAS:

- "indice" es el numero del MENSAJE que estas analizando. Es lo unico \
que permite volver a pairear la respuesta con su entrada.
- "citas" van COPIADAS, palabra por palabra, del mensaje. Si no podes \
copiar un fragmento exacto, es mejor dejar la lista vacia. NO \
parafrases, NO corrijas la ortografia, NO inventes. Una cita que no \
existe en el texto es un error grave, peor que no citar nada.
- "temas" son conceptos, no palabras sueltas. Usa 0 a 4.
- "es_logro" es true SOLO para un logro concreto y verificable: \
consigui un trabajo, publique algo, termine un proyecto, obtuve una \
certificacion. "Estoy aprendiendo" NO es un logro.
- "sentimiento" es el tono del mensaje, no tu opinion sobre la persona. \
Un mensaje que se queja es negativo, aunque sea educado.
- Si no estas seguro de algo, usa el valor conservador. No adivines \
para sonar preciso.

EJEMPLOS:

Mensaje 1
texto: "Despues de tres meses de estudiar solo, me contrataron como \
dev junior. El portfolio abierto me sirvio mas que el titulo."
Salida:
{{"resultados":[{{"indice":1,"sentimiento":"muy_positivo",\
"score_sentimiento":0.9,"temas":["empleo","proyectos"],"es_logro":true,\
"es_pregunta":false,"citas":["me contrataron como dev junior"],\
"relevant":0.92,"razon_relevante":"Contratacion como dev junior tras \
tres meses de estudio autodidacta"}}]}}

Mensaje 2
texto: "hola buenos dias a todos"
Salida:
{{"resultados":[{{"indice":2,"sentimiento":"neutro","score_sentimiento":0.0,\
"temas":[],"es_logro":false,"es_pregunta":false,"citas":[],\
"relevant":0.05,"razon_relevante":"Saludo sin contenido relevante"}}]}}
"""


def construir_prompt(interacciones: list[Interaccion]) -> str:
    """Arma el prompt completo: instrucciones + los mensajes a analizar.

    El numero de version va en el prompt, no en un log aparte. Va en el
    prompt porque es lo que se puede archivar junto a la respuesta y
    comparar despues; un log del lado del servidor se pierde en el primer
    cleanup y no se puede associar a un analisis viejo.

    Interacciones en blanco van a quedar mal. Un lote vacio no tiene
    respuesta util y hace gastar tokens para obtener `{"resultados":[]}`.
    """
    cuerpo = lote_a_texto(interacciones)
    return (
        f"{_SISTEMA}\n\n"
        f"<!-- prompt_version: {VERSION_PROMPT_ANALISIS} -->\n\n"
        f"---\n\n{cuerpo}\n\n---\n\nAnaliza los mensajes de arriba."
    )
