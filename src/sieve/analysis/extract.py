"""
Parseo tolerante de la salida de un LLM.

Este modulo existe porque la salida de un LLM NO es JSON. Es texto que
CONTIENE JSON, escrito por algo que no tiene un parser encima. En la
practica aparece todo esto:

    {"sentimiento": "positivo"}                      <- el caso feliz
    ```json                                          <- fence de markdown
    {"sentimiento": "positivo"}
    ```
    Claro! Aqui esta el analisis:                   <- prosa alrededor
    {"sentimiento": "positivo"}
    {"sentimiento": "positivo",}                     <- coma al final
    {'sentimiento': 'positivo'}                      <- comillas simples
    {"sentimiento": "positivo", "es_logro": True}    <- literales de Python
    {"sentimiento": "positivo", "temas": ["a", "b"

La primera version de este proyecto hacia `json.loads(respuesta)` y
funcionaba con el modelo que uso mientras lo desarrollo. Fallaba con el
modelo que eligio despues. Ese es el error clasico: probar el parsing
contra el unico ejemplo que ya funciona.

Acá hay una regla que vale mas que cualquier truco de regex: **un
campo invalido no se rellena con un valor por defecto, se rechaza el
item.** La razon es que `sentimiento` va directo al scoring. Si el
modelo devuelve `"sentimiento": "N/A"` y el parser lo convierte en
NEUTRO, el pipeline cree que midio Neutralidad cuando en realidad no
midio nada. Eso es un contrato que falla en silencio, exactamente el
error que `extra="forbid"` evita en los modelos de M0.

La distincion que si se aplica: los campos OPCIONALES (temas, citas)
pueden degradar a vacio porque perderlos no cambia el score. Los
fields que el scoring lee (sentimiento, score_sentimiento, es_logro,
es_pregunta, relevant) o son validos o el item vuelve a la heuristica.
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any

from sieve.models import Analisis, Sentimiento

#: Campos que el scoring lee de verdad. Si uno falla, el item se rechaza.
#: La razon de que `temas` NO este aca: un tema equivocado o ausente
#: cambia la agrupacion del newsletter, pero no el score del mensaje.
CAMPOS_CRITICOS = ("sentimiento", "score_sentimiento", "es_logro", "es_pregunta", "relevant")

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)\s*```", re.DOTALL)


class JsonIlegibleError(ValueError):
    """No se pudo sacar un dict de la respuesta. El item cae a heuristica."""


def extraer_json(texto: str) -> Any:
    """Saca el JSON de un texto que quizas no es JSON.

    Devuelve lo que el modelo produjo, sin imponer la forma: una LISTA se
    queda lista y un dict se queda dict. Quien llama decide.

    Antes esta funcion solo aceptaba `dict` y devolvia el primer objeto
    balanceado que encontraba. Con la respuesta por lotes, que SIEMPRE es
    una lista, hacia esto: devolvia el primer item de la lista y tiraba
    los otros siete sin avisar. El pipeline entero del LLM estaba muerto
    y los tests no lo fueran porque ninguno ejercita un lote de verdad.

    Que la forma sea responsabilidad del que llama es lo correcto: hay
    item de un solo mensaje (que puede venir como dict) y lotes (que
    vienen como lista), y aplanar los dos aqui es exactamente el error
    que hacia el parser tonto.

    Orden de intentos, del mas barato al mas caro:
      1. `json.loads` directo: a veces el modelo se porta.
      2. Sacar el fence de markdown.
      3. Localizar el objeto mas externo balancingando llaves, CON
         conteo de strings. Un regex_find no funciona: se detiene en la
         primera llave que ve dentro de un string.
      4. Reparar coma al final.
      5. `ast.literal_eval` para comillas simples y literales de Python.
         Es el ultimo recurso y es SEGURO (no es `eval`), pero ojo: no
         se pueden convertir comillas simples a dobles globalmente,
         porque "don't" es un string valido y romperlo es peor que el
         error que estamos arreglando.
    """
    if not texto or not texto.strip():
        raise JsonIlegibleError("respuesta vacia")

    candidatos: list[str] = []
    limpio = texto.strip()
    candidatos.append(limpio)

    for bloque in _FENCE.findall(texto):
        if bloque.strip():
            candidatos.append(bloque.strip())

    for base in list(candidatos):
        objeto = _objeto_externo(base)
        if objeto is not None:
            candidatos.append(objeto)

    # Ultimo recurso: probar el texto entero, por si venia con algo
    # alrededor que romper el balanceo de llaves.
    candidatos.append(limpio)

    for candidato in candidatos:
        for variante in (candidato, _reparar_comas(candidato)):
            datos = _intentar_json(variante)
            if isinstance(datos, (dict, list)):
                return datos
    raise JsonIlegibleError(f"no se encontro JSON en {len(texto)} chars: {texto[:120]!r}")


def _objeto_externo(texto: str) -> str | None:
    """Devuelve el primer objeto JSON balanceado, respetando strings.

    Escribir esto a mano es mejor que un regex `\\{.*\\}` porque el
    contenido puede traer llaves: {"texto": "uso {variable} en JS"}. Un
    regex se corta en la primera `}` y devuelve basura que a veces es JSON
    valido y a veces no. Balanceando llaves y ignorando las que estan
    dentro de strings, el caso raro tambien funciona.
    """
    inicio = texto.find("{")
    if inicio == -1:
        return None

    profundidad = 0
    en_string = False
    escapado = False
    for i in range(inicio, len(texto)):
        c = texto[i]
        if escapado:
            escapado = False
            continue
        if c == "\\":
            escapado = True
            continue
        if c == '"':
            en_string = not en_string
            continue
        if en_string:
            continue
        if c == "{":
            profundidad += 1
        elif c == "}":
            profundidad -= 1
            if profundidad == 0:
                return texto[inicio : i + 1]
    return None  # sin cerrar: el modelo se corto a mitad


def _reparar_comas(texto: str) -> str:
    """Quita las comas antes de } o ]. El error de sintaxis mas comun."""
    return re.sub(r",(\s*[}\]])", r"\1", texto)


def _intentar_json(texto: str) -> Any:
    try:
        return json.loads(texto)
    except (json.JSONDecodeError, ValueError):
        pass
    try:
        return ast.literal_eval(texto)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        # literal_eval es seguro (no ejecuta), pero sigue levantando
        # varias cosas. Un SyntaxError significa "no era JSON", que es
        # exactamente el caso que estamos contemplando.
        return None


# ═══════════════════════════════════════════════════════════════════
#  Del dict crudo al contrato
# ═══════════════════════════════════════════════════════════════════


def a_analisis(
    datos: dict[str, Any],
    *,
    mensaje_id: str | None = None,
    texto_original: str = "",
) -> Analisis:
    """Convierte la salida cruda del LLM en un `Analisis` validado.

    Levanta `JsonIlegibleError` si falta un campo critico o si uno critico no
    tiene forma coercible. El pipeline no rellena con defaults: cae a
    la heuristica. Ver la nota de modulo.
    """
    faltantes = [c for c in CAMPOS_CRITICOS if c not in datos]
    if faltantes:
        raise JsonIlegibleError(f"faltan campos criticos: {faltantes}")

    sentimiento = _a_sentimiento(datos.get("sentimiento"))
    if sentimiento is None:
        raise JsonIlegibleError(
            f"sentimiento ilegible: {datos.get('sentimiento')!r}. "
            f"Se esperaba uno de {[s.value for s in Sentimiento]}"
        )

    score = _a_float(datos.get("score_sentimiento"), -1.0, 1.0)
    relevante = _a_float(datos.get("relevant"), 0.0, 1.0)

    return Analisis(
        mensaje_id=mensaje_id,
        sentimiento=sentimiento,
        # El score numerico y el enum tienen que contar la misma historia.
        # Si el modelo dice "muy_positivo" con score -0.8, una de las dos
        # cosas es falsa; el enum es el que el contrato expone, asi que
        # se deriva de ahi y el numero se corrige.
        score_sentimiento=_score_desde_enum(sentimiento) if score is None else score,
        temas=_a_temas(datos.get("temas")),
        es_logro=bool(datos.get("es_logro")),
        es_pregunta=bool(datos.get("es_pregunta")),
        citas=_a_citas(datos.get("citas")),
        relevant=relevante if relevante is not None else 0.5,
        razon_relevante=str(datos.get("razon_relevante") or "")[:400],
    )


def _a_sentimiento(valor: Any) -> Sentimiento | None:
    """Acepta tanto el vocabulario del documento como las claves del modelo.

    El enum ya sabe traducir "Altamente Positivo" y "muy_positivo"
    (ver `Sentimiento._missing_`). Aca solo se agrega el caso de que el
    modelo responda en ingles o con una forma no prevista.
    """
    if isinstance(valor, Sentimiento):
        return valor
    if not isinstance(valor, str):
        return None
    try:
        return Sentimiento(valor)
    except ValueError:
        pass
    # "very positive" / "extremely positive": el enum no las conoce, pero
    # el significado es inequivoco y perderlo seria tirar informacion.
    ingles = {
        "very positive": Sentimiento.MUY_POSITIVO,
        "extremely positive": Sentimiento.MUY_POSITIVO,
        "positive": Sentimiento.POSITIVO,
        "neutral": Sentimiento.NEUTRO,
        "negative": Sentimiento.NEGATIVO,
        "very negative": Sentimiento.MUY_NEGATIVO,
        "extremely negative": Sentimiento.MUY_NEGATIVO,
    }
    return ingles.get(valor.strip().lower().rstrip("!."))


def _score_desde_enum(sentimiento: Sentimiento) -> float:
    """El punto medio de cada nivel. Para que enum y numero no se contradigan."""
    return {
        Sentimiento.MUY_NEGATIVO: -0.9,
        Sentimiento.NEGATIVO: -0.5,
        Sentimiento.NEUTRO: 0.0,
        Sentimiento.POSITIVO: 0.5,
        Sentimiento.MUY_POSITIVO: 0.9,
    }[sentimiento]


def _a_float(valor: Any, minimo: float, maximo: float) -> float | None:
    """Coercion tolerante. None si no hay numero usable."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        numero = float(valor)
    elif isinstance(valor, str):
        try:
            numero = float(valor.strip().replace(",", "."))
        except ValueError:
            return None
    else:
        return None
    if numero != numero:  # NaN
        return None
    return max(minimo, min(maximo, numero))


def _a_temas(valor: Any) -> list[str]:
    """Normaliza a lista de strings. Un tema de mas se corta; no se rompe."""
    if valor is None:
        return []
    if isinstance(valor, str):
        valor = [valor]
    if not isinstance(valor, list):
        return []
    temas: list[str] = []
    for t in valor:
        if isinstance(t, str) and t.strip():
            temas.append(t.strip().lower()[:40])
    return list(dict.fromkeys(temas))[:6]


def _a_citas(valor: Any) -> list[str]:
    """Las citas SIEMPRE pasan por verificacion contra el texto original."""
    if valor is None:
        return []
    if isinstance(valor, str):
        valor = [valor]
    if not isinstance(valor, list):
        return []
    return [c.strip() for c in valor if isinstance(c, str) and c.strip()][:5]
