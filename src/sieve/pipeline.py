"""
El puente entre el router (M3) y el generador (M4).

El router decide QUE activo sale de cada mensaje; el generador lo
produce. Antes el generador producia los TRES formatos por mensaje y el
router se quedaba mirando: un mensaje que el router mandaba a FAQ igual
gastaba tokens en un post de LinkedIn y en un newsletter que nunca iban
a salir. Aca se cierra ese hueco: se puntua, se enruta, y se genera solo
lo que el router eligio.

El mapeo `FORMATO_POR_TIPO` no es 1:1: un `caso_exito` produce el post de
LinkedIn Y su bloque de newsletter ("Logro de la Semana"), como dice el
README ("post + bloque de newsletter"). El resto produce una pieza.
"""

from __future__ import annotations

from sieve.analysis.base import Procedencia, ResultadoAnalisis, ResumenAnalisis
from sieve.curation import Formato
from sieve.models import LoteComunidad, TipoActivo
from sieve.scoring import DecisionRuta, enrutar_lote, puntuar_lote

#: Del tipo de activo del router a los formatos que el generador produce.
#: Un `caso_exito` produce dos piezas: el post de LinkedIn (el testimonio
#: con el nombre de alguien, y por eso sale con curacion obligatoria) y
#: su bloque de newsletter. Un `faq` produce la sugerencia; un `highlight`
#: produce solo el destaque del resumen semanal.
FORMATO_POR_TIPO: dict[TipoActivo, tuple[Formato, ...]] = {
    TipoActivo.CASO_EXITO: ("post_linkedin", "newsletter"),
    TipoActivo.FAQ: ("faq",),
    TipoActivo.HIGHLIGHT: ("newsletter",),
}

#: Formato interno del generador -> nombre de campo del CONTRATO
#: (`ActivosGenerados`). El contrato de ONE escribe
#: "destreza_newsletter_semanal" (con la falta de ortografia) y
#: "sugerencia_contenido_faq", y el JSON que sale tiene que llevar esos
#: nombres, no los internos.
ETIQUETA_POR_FORMATO: dict[Formato, str] = {
    "post_linkedin": "post_linkedin",
    "newsletter": "destreza_newsletter_semanal",
    "faq": "sugerencia_contenido_faq",
}


def procedencia_del_lote(lote: LoteComunidad) -> Procedencia:
    """La procedencia por item, derivada del conteo por lote.

    `LoteComunidad` persiste SOLO el conteo (`llm`/`heuristico`/`mixto`),
    no que item cayo a cada una. Asi que se deriva: todo-llm es "llm",
    todo-heuristico es "heuristico", y la mezcla es "mixto" — la senal
    honesta de "hay de todo" cuando no se puede saber por item.

    Un lote sin procedencia declarada (viejo) cae en "heuristico": es el
    piso conservador. Declarar "llm" ahi sobrestima la confianza de algo
    de lo que no hay registro, que es justo el bug que esta funcion
    viene a cerrar.
    """
    conteo = lote.procedencia
    total = conteo.total
    if total <= 0:
        return "heuristico"
    if conteo.llm == total:
        return "llm"
    if conteo.heuristico == total:
        return "heuristico"
    return "mixto"


def reconstruir_resumen(lote: LoteComunidad) -> ResumenAnalisis:
    """Reconstruye el `ResumenAnalisis` desde el lote persistido.

    `LoteComunidad` persiste `interacciones` y `analisis` por separado y
    los enlaza por `mensaje_id`. El scoring (`puntuar_lote`) trabaja
    sobre `ResumenAnalisis`, asi que aca se rearman los pares. Un mensaje
    sin `mensaje_id` o sin analisis se omite: no se puede enlazar y no se
    adivina por posicion.
    """
    por_id = {a.mensaje_id: a for a in lote.analisis if a.mensaje_id}
    procedencia = procedencia_del_lote(lote)
    resultados: list[ResultadoAnalisis] = []
    for interaccion in lote.interacciones:
        if interaccion.mensaje_id is None:
            continue
        analisis = por_id.get(interaccion.mensaje_id)
        if analisis is None:
            continue
        resultados.append(
            ResultadoAnalisis(
                interaccion=interaccion, analisis=analisis, procedencia=procedencia
            )
        )
    return ResumenAnalisis(resultados=resultados)


def enrutar_lote_analizado(
    lote: LoteComunidad,
) -> list[tuple[ResultadoAnalisis, DecisionRuta]]:
    """Puntua y enruta el lote, devolviendo (resultado, decision) en orden.

    El orden se hereda de `puntuar_lote`/`enrutar_lote`, y el emparejado
    es por posicion, que es lo que hace auditable el ruteo: el score 0.82
    va pegado a SU mensaje, no al de al lado.
    """
    resumen = reconstruir_resumen(lote)
    puntajes = puntuar_lote(resumen)
    decisiones = enrutar_lote(resumen.resultados, puntajes)
    return list(zip(resumen.resultados, decisiones, strict=True))
