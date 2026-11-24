"""
El puente entre el router (M3) y el generador (M4).

El router decide QUE activo sale de cada mensaje; el generador lo
produce. Antes el generador producia los TRES formatos por mensaje y el
router se quedaba mirando: un mensaje que el router mandaba a FAQ igual
gastaba tokens en un post de LinkedIn y en un newsletter que nunca iban
a salir. Aca se cierra ese hueco: se puntua, se enruta, y se genera SOLO
el formato que el router eligio.

El mapeo `FORMATO_POR_TIPO` es 1:1. El README dice que un `caso_exito`
es "post de LinkedIn + bloque de newsletter": aca el mensaje produce el
POST, y el "bloque de newsletter" es una agregacion posterior (n8n junta
los logros de la semana en un destaque), no una segunda pieza por
mensaje.
"""

from __future__ import annotations

from sieve.analysis.base import ResultadoAnalisis, ResumenAnalisis
from sieve.curation import Formato
from sieve.models import LoteComunidad, TipoActivo
from sieve.scoring import DecisionRuta, enrutar_lote, puntuar_lote

#: Del tipo de activo del router al formato que el generador produce.
#: Un `caso_exito` es un post de LinkedIn (el testimonio con el nombre de
#: alguien, y por eso sale con curacion obligatoria). Un `faq` es una
#: sugerencia para mentoría. Un `highlight` es el destaque del resumen
#: semanal.
FORMATO_POR_TIPO: dict[TipoActivo, Formato] = {
    TipoActivo.CASO_EXITO: "post_linkedin",
    TipoActivo.FAQ: "faq",
    TipoActivo.HIGHLIGHT: "newsletter",
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


def reconstruir_resumen(lote: LoteComunidad) -> ResumenAnalisis:
    """Reconstruye el `ResumenAnalisis` desde el lote persistido.

    `LoteComunidad` persiste `interacciones` y `analisis` por separado y
    los enlaza por `mensaje_id`. El scoring (`puntuar_lote`) trabaja
    sobre `ResumenAnalisis`, asi que aca se rearman los pares. Un mensaje
    sin `mensaje_id` o sin analisis se omite: no se puede enlazar y no se
    adivina por posicion.
    """
    por_id = {a.mensaje_id: a for a in lote.analisis if a.mensaje_id}
    resultados: list[ResultadoAnalisis] = []
    for interaccion in lote.interacciones:
        if interaccion.mensaje_id is None:
            continue
        analisis = por_id.get(interaccion.mensaje_id)
        if analisis is None:
            continue
        # La procedencia por item no se persiste (solo el conteo por
        # lote), asi que se declara "llm" como hasta ahora: es una
        # simplificacion heredada, no un hecho nuevo de este modulo.
        resultados.append(
            ResultadoAnalisis(
                interaccion=interaccion, analisis=analisis, procedencia="llm"
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
