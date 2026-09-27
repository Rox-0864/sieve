"""
Verificacion de citas contra el texto original.

Este es el modulo mas importante del proyecto, y no es el que parece.

`Analisis.citas` pide quotes textuales del mensaje. Un LLM genera
texto plausible, y "generar texto plausible" es exactamente lo que hace
una cita inventada. El modelo no distingue entre "esto lo dijo alguien" y
"esto suena a algo que alguien diria", y el resultado es una cita
atribuida a una persona real que jamas dijo eso.

Para Sieve eso no es un error de calidad: es un problema legal y de
reputacion. Una cita falsa en un post de LinkedIn con tu nombre es una
afirmacion falsa firmada por vos, publicada por una maquina que
"decidio" que valia la pena.

La regla es simple y no negociable: **una cita existe si y solo si es
un substring del texto original.** No se corrige, no se aproxima, no se
acepta "es que el modelo lo dijo". Si no esta, no esta.

Lo que se hace con las que fallan NO es tirarlas: es contarlas y
reportarlas. Un proveedor que inventa citas el 30% de las veces es un
dato de calidad del proveedor, y sin medirlo no hay forma de saber si
hay que cambiar de modelo.
"""

from __future__ import annotations

import re
import unicodedata

#: Las comillas y comillas angulares que un modelo le pone a una cita y
#: que el texto original no tiene. Hay que sacarlas ANTES de comparar:
#: si no, la cita se descarta por tener comillas y el conteo de
#: descartadas miente.
_ENVOLTORIOS = '"\'“”‘’`«»'

#: Debajo de esta longitud, una cita no es evidencia. Ver `verificar_citas`.
LONGITUD_MINIMA_CITA = 12

#: Espacios colapsables, para comparar "a  b" con "a b".
_ESPACIOS = re.compile(r"\s+")


def verificar_citas(citas: list[str], texto_original: str) -> tuple[list[str], list[str]]:
    """Separa las citas reales de las inventadas.

    Devuelve `(verificadas, descartadas)`. Las dos listas conservan el
    orden original para que el resultado sea reproducible.

    Tres criterios, de mas estricto a mas permisivo:

      1. La cita es substring EXACTA del texto. Caso normal.
      2. La cita es substring ignorando mayusculas. Un modelo que
         capitaliza la primera letra no esta inventando.
      3. La cita tiene "..." y cada fragmento aparece. Un modelo que
         resume un par de partes con puntos suspensivos esta citando
         dos excerpts reales, no inventando.

    Todo lo demas se descarta. En particular, la cita reescrita con otras
    palabras. Que el modelo haya "arreglado" la ortografia significa que
    ya no es una cita: es una parafrasis, y las parafrasis van en
    `temas`, no en `citas`.
    """
    if not citas:
        return [], []
    if not texto_original or not texto_original.strip():
        return [], [c for c in citas if c.strip()]

    original = texto_original
    original_minuscula = original.lower()
    original_espacios = _colapsar(original).lower()

    verificadas: list[str] = []
    descartadas: list[str] = []

    for cita in citas:
        limpia = cita.strip().strip(_ENVOLTORIOS).strip()
        if not limpia:
            continue
        if len(limpia) < LONGITUD_MINIMA_CITA:
            # Una cita de una o dos palabras no es evidencia de nada.
            # "de" y "a" aparecen en cualquier texto, asi que el filtro
            # las acepta SIEMPRE: cualquier alucinacion que acierte una
            # palabra comun pasa la verificacion y termina backing un
            # post firmado por otra persona. Descartarlas no cuesta nada
            # real, porque una cita tan corta no dice nada que la fuente
            # no diga mejor.
            descartadas.append(limpia)
            continue
        if _es_substring(limpia, original, original_minuscula, original_espacios):
            verificadas.append(limpia)
        else:
            descartadas.append(limpia)

    return verificadas, descartadas


def _es_substring(
    cita: str, original: str, original_minuscula: str, original_espacios: str
) -> bool:
    if cita in original:
        return True
    if cita.lower() in original_minuscula:
        return True
    if _colapsar(cita).lower() in original_espacios:
        return True

    # Sin acentos. Un modelo que devuelve "me sirvio" donde el mensaje
    # dice "me sirvió" esta citando, no inventando. Comparar solo por
    # minusculas reportaria como inventada una cita perfectamente real, y
    # eso hace que el conteo de citas_descartadas mienta sobre la
    # calidad del proveedor.
    cita_plana = _sin_acentos(cita)
    if cita_plana and cita_plana in _sin_acentos(original):
        return True

    # Cita con puntos suspensivos: cada fragmento tiene que existir.
    if "..." in cita or "…" in cita:
        fragmentos = [f for f in re.split(r"\.\.\.|…", cita) if len(f.strip()) >= 8]
        if fragmentos:
            return all(
                _colapsar(f).lower() in original_espacios
                for f in fragmentos
            )
    return False


def _sin_acentos(texto: str) -> str:
    """Quita diacriticos conservando el resto del texto."""
    return "".join(
        c for c in unicodedata.normalize("NFD", texto.lower())
        if unicodedata.category(c) != "Mn"
    )


def _colapsar(texto: str) -> str:
    """Normaliza espacios y acentos para comparar dos formas del mismo texto."""
    return _ESPACIOS.sub(" ", texto).strip()


def citas_de_ventana(
    texto: str, *, ancho: int = 160, solape: int = 40
) -> list[str]:
    """Trocea el texto, para cuando no hay citas que verificar.

    Sirve para que la heuristica tambien pueda proponer citas reales: si
    el LLM no esta disponible, el `relevant` del mensaje tiene que
    apoyarse en una cita que EXISTS, no en una frase que el pipeline se
    invento. Una cita que no se puede citar no puede estar en un activo.

    Devolver un texto corto tal cual ("hola") era una inconsistencia:
    `verificar_citas` descarta toda cita bajo `LONGITUD_MINIMA_CITA`
    porque no prueba nada, y esta funcion proponia exactamente esas. Las
    dos reglas tienen que decir lo mismo, o un `Analisis` heuristico
    lleva citas que el mismo sistema rechazaria si vinieran de un LLM.
    """
    limpio = _colapsar(texto)
    if len(limpio) < LONGITUD_MINIMA_CITA:
        return []
    if len(limpio) <= ancho:
        return [limpio]
    fragmentos: list[str] = []
    inicio = 0
    while inicio < len(limpio):
        corte = min(inicio + ancho, len(limpio))
        if corte < len(limpio):
            # Intentar cortar en un limite de palabra, no a la mitad.
            espacio = limpio.rfind(" ", inicio + ancho // 2, corte)
            if espacio > inicio:
                corte = espacio
        fragmentos.append(limpio[inicio:corte].strip())
        inicio = corte - solape if corte - solape > inicio else corte
    return [f for f in fragmentos if len(f) >= LONGITUD_MINIMA_CITA]


def normalizar_para_citar(texto: str) -> str:
    """Deja el texto listo para compararse. Util para tests y depuracion."""
    return unicodedata.normalize("NFC", _colapsar(texto))
