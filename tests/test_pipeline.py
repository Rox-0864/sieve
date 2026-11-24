"""
Tests del puente router -> generador (`sieve.pipeline`).

Verifica que el mapeo tipo->formato es una biyeccion (tres tipos, tres
formatos, sin repetidos) y que un lote analizado enruta a UN formato por
mensaje, no a los tres.
"""

from __future__ import annotations

from sieve.models import (
    Analisis,
    ConteoProcedencia,
    Interaccion,
    LoteComunidad,
    Sentimiento,
    TipoActivo,
)
from sieve.pipeline import (
    ETIQUETA_POR_FORMATO,
    FORMATO_POR_TIPO,
    enrutar_lote_analizado,
    procedencia_del_lote,
    reconstruir_resumen,
)


def _anal(
    mid: str,
    sentimiento: float = 0.0,
    es_logro: bool = False,
    es_pregunta: bool = False,
) -> Analisis:
    return Analisis(
        mensaje_id=mid,
        sentimiento=Sentimiento.NEUTRO,
        score_sentimiento=sentimiento,
        es_logro=es_logro,
        es_pregunta=es_pregunta,
    )


def _lote(*pares: tuple[str, Analisis]) -> LoteComunidad:
    return LoteComunidad(
        origen_comunidad="t",
        periodo_referencia="s",
        interacciones=[
            Interaccion(autor="X", canal="c", texto="texto", mensaje_id=mid)
            for mid, _ in pares
        ],
        analisis=[a for _, a in pares],
    )


def test_formato_por_tipo_cubre_todos_los_tipos() -> None:
    assert set(FORMATO_POR_TIPO) == set(TipoActivo)


def test_caso_exito_produce_post_y_newsletter() -> None:
    """El README dice 'post + bloque de newsletter': un logro sale en dos
    piezas, no en una."""
    assert FORMATO_POR_TIPO[TipoActivo.CASO_EXITO] == ("post_linkedin", "newsletter")
    assert FORMATO_POR_TIPO[TipoActivo.FAQ] == ("faq",)
    assert FORMATO_POR_TIPO[TipoActivo.HIGHLIGHT] == ("newsletter",)


def test_etiqueta_por_formato_usa_los_nombres_del_contrato() -> None:
    """El JSON de salida lleva los nombres de ONE, no los internos."""
    assert ETIQUETA_POR_FORMATO["post_linkedin"] == "post_linkedin"
    assert ETIQUETA_POR_FORMATO["newsletter"] == "destreza_newsletter_semanal"
    assert ETIQUETA_POR_FORMATO["faq"] == "sugerencia_contenido_faq"


def test_cada_tipo_enruta_a_su_formato() -> None:
    lote = _lote(
        ("m1", _anal("m1", sentimiento=0.95, es_logro=True)),
        ("m2", _anal("m2", sentimiento=0.3, es_pregunta=True)),
        ("m3", _anal("m3", sentimiento=-0.8)),
    )
    rutas = {r.mensaje_id: d.tipo for r, d in enrutar_lote_analizado(lote)}
    assert rutas == {
        "m1": TipoActivo.CASO_EXITO,
        "m2": TipoActivo.FAQ,
        "m3": TipoActivo.HIGHLIGHT,
    }


def test_reconstruir_resumen_omite_sin_id() -> None:
    lote = LoteComunidad(
        origen_comunidad="t",
        periodo_referencia="s",
        interacciones=[
            Interaccion(autor="X", canal="c", texto="texto", mensaje_id="m1"),
            # sin mensaje_id: no se puede enlazar
            Interaccion(autor="X", canal="c", texto="texto"),
        ],
        analisis=[
            _anal("m1"),
            Analisis(sentimiento=Sentimiento.NEUTRO, score_sentimiento=0.0),
        ],
    )
    resumen = reconstruir_resumen(lote)
    assert [r.mensaje_id for r in resumen.resultados] == ["m1"]


def test_procedencia_todo_llm() -> None:
    lote = _lote(("m1", _anal("m1")))
    lote = lote.model_copy(update={"procedencia": ConteoProcedencia(llm=1)})
    assert procedencia_del_lote(lote) == "llm"


def test_procedencia_todo_heuristico() -> None:
    lote = _lote(("m1", _anal("m1")))
    lote = lote.model_copy(update={"procedencia": ConteoProcedencia(heuristico=1)})
    assert procedencia_del_lote(lote) == "heuristico"


def test_procedencia_mezclada_es_mixto() -> None:
    lote = _lote(("m1", _anal("m1")), ("m2", _anal("m2")))
    lote = lote.model_copy(
        update={"procedencia": ConteoProcedencia(llm=1, heuristico=1)}
    )
    assert procedencia_del_lote(lote) == "mixto"


def test_procedencia_sin_informar_es_heuristico() -> None:
    """Un lote viejo sin procedencia no puede declarar 'llm': sobrestima."""
    assert procedencia_del_lote(_lote(("m1", _anal("m1")))) == "heuristico"


def test_reconstruir_resumen_deriva_la_procedencia() -> None:
    lote = _lote(("m1", _anal("m1")))
    lote = lote.model_copy(update={"procedencia": ConteoProcedencia(heuristico=1)})
    resumen = reconstruir_resumen(lote)
    assert resumen.resultados[0].procedencia == "heuristico"
