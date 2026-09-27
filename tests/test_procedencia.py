"""
Tests de `ConteoProcedencia` y su validacion en `LoteComunidad`.

El campo existe para que n8n pueda preguntar "¿esto es de fiar?" sin
volver a correr el pipeline. Si puede mentir sin ruido, no cumple.

Offline: no toca red ni modelo.
"""

from __future__ import annotations

import pytest

from sieve.analysis.base import ResultadoAnalisis, ResumenAnalisis
from sieve.models import (
    Analisis,
    ConteoProcedencia,
    Interaccion,
    LoteComunidad,
    Sentimiento,
)


def _inter(mid: str) -> Interaccion:
    return Interaccion(autor="X", canal="c", texto="t", mensaje_id=mid)


def _anal(mid: str) -> Analisis:
    return Analisis(
        mensaje_id=mid,
        sentimiento=Sentimiento.NEUTRO,
        score_sentimiento=0.0,
    )


def _lote(n: int = 3, **extra: object) -> LoteComunidad:
    ids = [f"m{i}" for i in range(1, n + 1)]
    return LoteComunidad(
        origen_comunidad="c",
        periodo_referencia="p",
        interacciones=[_inter(i) for i in ids],
        analisis=[_anal(i) for i in ids],
        **extra,  # type: ignore[arg-type]
    )


class TestElCampoEsOpcional:
    """Un contrato que se rompe con cada archivo viejo no es un contrato."""

    def test_un_lote_sin_procedencia_carga(self) -> None:
        lote = _lote()
        assert not lote.procedencia.informado

    def test_un_lote_viejo_con_analisis_sigue_cargando(self) -> None:
        """Los JSON escritos antes de que el campo existiera."""
        lote = LoteComunidad.model_validate({
            "origen_comunidad": "c",
            "periodo_referencia": "p",
            "interacciones": [{"autor": "X", "canal": "c", "texto": "t",
                               "mensaje_id": "m1"}],
            "analisis": [{"mensaje_id": "m1", "sentimiento": "neutro",
                          "score_sentimiento": 0.0}],
        })
        assert len(lote.analisis) == 1
        assert not lote.procedencia.informado

    def test_lote_sin_analisis_tampoco_declara(self) -> None:
        lote = LoteComunidad(
            origen_comunidad="c",
            periodo_referencia="p",
            interacciones=[_inter("m1")],
        )
        assert not lote.procedencia.informado
        assert lote.analisis == []


class TestLaProcedenciaNoPuedeMentir:
    """La senal de confianza que no puede mentir no sirve como senal."""

    def test_llm_que_no_cuadra_falla(self) -> None:
        with pytest.raises(ValueError, match="procedencia declara"):
            _lote(3, procedencia=ConteoProcedencia(llm=7))

    def test_suma_correcta_pasa(self) -> None:
        lote = _lote(3, procedencia=ConteoProcedencia(llm=2, heuristico=1))
        assert lote.procedencia.total == 3

    def test_citas_descartadas_sin_llm_falla(self) -> None:
        """Solo el LLM puede inventar una cita. Si no hay LLM, el numero
        no puede ser otro que cero: o el conteo esta mal o miente."""
        with pytest.raises(ValueError, match="solo el LLM puede inventar"):
            _lote(3, procedencia=ConteoProcedencia(
                heuristico=3, con_citas_descartadas=1,
            ))


class TestDeFiar:
    def test_todo_llm_sin_citas_malas_es_de_fiar(self) -> None:
        c = ConteoProcedencia(llm=7)
        assert c.de_fiar

    def test_un_fallback_lo_podesta(self) -> None:
        assert not ConteoProcedencia(llm=6, heuristico=1).de_fiar

    def test_una_cita_inventada_lo_podesta(self) -> None:
        """El caso que motiva el campo: llm=7 suena bien y no lo es."""
        assert not ConteoProcedencia(llm=7, con_citas_descartadas=1).de_fiar

    def test_no_informado_no_es_de_fiar(self) -> None:
        assert not ConteoProcedencia().de_fiar


class TestConteoDesdeElResumen:
    """Un solo lugar que sabe contar. Si la CLI contara por su cuenta,
    un dia los dos numeros dejan de coincidir."""

    def _resumen(self, *procedencias: str, malas: int = 0) -> ResumenAnalisis:
        r = ResumenAnalisis()
        for i, proc in enumerate(procedencias, 1):
            r.agregar(ResultadoAnalisis(
                interaccion=_inter(f"m{i}"),
                analisis=_anal(f"m{i}"),
                procedencia=proc,  # type: ignore[arg-type]
                citas_descartadas=tuple(f"x{j}" for j in range(malas if i == 1 else 0)),
            ))
        return r

    def test_cuenta_las_tres(self) -> None:
        c = self._resumen("llm", "llm", "heuristico").conteo_procedencia()
        assert (c.llm, c.heuristico, c.mixto) == (2, 1, 0)
        assert c.total == 3

    def test_cuenta_analisis_no_citas(self) -> None:
        """3 citas inventadas en UN analisis son 1 analisis sospechoso,
        no 3. La pregunta que se le hace al campo es 'cuantos tengo que
        mirar antes de publicar'."""
        c = self._resumen("llm", "llm", malas=3).conteo_procedencia()
        assert c.con_citas_descartadas == 1
        assert c.llm == 2

    def test_el_lote_persiste_el_conteo(self) -> None:
        resumen = self._resumen("llm", "heuristico", "mixto")
        lote = LoteComunidad(
            origen_comunidad="c",
            periodo_referencia="p",
            interacciones=[_inter(f"m{i}") for i in (1, 2, 3)],
            analisis=resumen.a_analisis(),
            procedencia=resumen.conteo_procedencia(),
        )
        assert lote.procedencia.llm == 1
        assert not lote.procedencia.de_fiar

    def test_va_y_vuelve_por_json(self) -> None:
        resumen = self._resumen("llm", "llm", "llm")
        lote = LoteComunidad(
            origen_comunidad="c",
            periodo_referencia="p",
            interacciones=[_inter(f"m{i}") for i in (1, 2, 3)],
            analisis=resumen.a_analisis(),
            procedencia=resumen.conteo_procedencia(),
        )
        otro = LoteComunidad.model_validate(lote.model_dump())
        assert otro.procedencia == lote.procedencia
        assert otro.procedencia.de_fiar
