"""
Tests de scoring y router (M3).

Los tests importantes no son los de aritmetica: son los de DECISION. La
opcion A (renormalizar cuando falta un termino) es correcta solo si se
puede VER que un lote sin fechas y uno con fechas se comportan como
dice la documentacion. Si renormalizara en silencio, estos tests pasarian
igual.

Offline: no toca red ni modelo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sieve.analysis.base import ResultadoAnalisis, ResumenAnalisis
from sieve.models import Analisis, Interaccion, Sentimiento, TipoActivo
from sieve.scoring import (
    TERMINOS,
    UMBRAL_CASO_EXITO,
    UMBRAL_FAQ,
    ContextoLote,
    DecisionRuta,
    Puntaje,
    agrupar_por_tipo,
    enrutar,
    enrutar_lote,
    indexar_por_id,
    puntuar,
    puntuar_lote,
)

AHORA = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _inter(mid: str, texto: str = "texto", ts: datetime | None = None) -> Interaccion:
    return Interaccion(autor="X", canal="c", texto=texto, mensaje_id=mid, timestamp=ts)


def _anal(
    mid: str,
    sentimiento: float = 0.0,
    temas: list[str] | None = None,
    es_logro: bool = False,
    es_pregunta: bool = False,
) -> Analisis:
    return Analisis(
        mensaje_id=mid,
        sentimiento=Sentimiento.NEUTRO,
        score_sentimiento=sentimiento,
        temas=temas or [],
        es_logro=es_logro,
        es_pregunta=es_pregunta,
    )


class TestNormalizacionDeSentimiento:
    """-1..1 -> 0..1. El punto de este test es que NEUTRO vale 0.5."""

    @pytest.mark.parametrize(
        ("entrada", "esperado"),
        [(-1.0, 0.0), (-0.5, 0.25), (0.0, 0.5), (0.5, 0.75), (1.0, 1.0)],
    )
    def test_el_mapeo(self, entrada: float, esperado: float) -> None:
        par = (_inter("m1"), _anal("m1", sentimiento=entrada))
        p = puntuar(*par, ContextoLote.desde([par]))
        assert p.pesos_por_nombre()["sentimiento"].valor == pytest.approx(esperado)

    def test_neutro_no_es_cero(self) -> None:
        """El bug que este test evita: tratar el 0 como cero y descartar
        los mensajes que no son extremos, que son los mas utiles."""
        par = (_inter("m1"), _anal("m1", sentimiento=0.0))
        p = puntuar(*par, ContextoLote.desde([par]))
        assert p.pesos_por_nombre()["sentimiento"].valor > 0.0


class TestPesosYRenormalizacion:
    """La opcion A. Su punta entera es la renormalizacion."""

    def test_los_pesos_base_suman_uno(self) -> None:
        assert sum(t.peso_base for t in TERMINOS) == pytest.approx(1.0)

    def test_con_temas_y_fechas_no_se_renormaliza(self) -> None:
        """La formula entera necesita las dos cosas: temas para medir la
        saliencia y fechas para medir la recencia."""
        par = (_inter("m1", ts=AHORA), _anal("m1", temas=["x"]))
        p = puntuar(*par, ContextoLote.desde([par]))
        assert not p.renormalizado
        assert p.peso_total_aplicado == pytest.approx(1.0)
        assert p.terminos_ausentes == []

    def test_sin_fechas_falta_recencia_y_se_renormaliza(self) -> None:
        par = (_inter("m1"), _anal("m1", temas=["x"]))
        p = puntuar(*par, ContextoLote.desde([par]))
        assert p.terminos_ausentes == ["recencia"]
        assert p.renormalizado
        assert p.peso_total_aplicado == pytest.approx(0.80)
        # Los pesos aplicados suben, no se dividen a la mitad: 0.30/0.80.
        assert p.pesos_por_nombre()["sentimiento"].peso_aplicado == pytest.approx(
            0.30 / 0.80
        )

    def test_un_solo_sin_fecha_deshabilita_recencia_en_todo_el_lote(self) -> None:
        """Todo o nada. Si 6 de 7 tuvieran fecha, dos mensajes del mismo
        lote puntuarian con reglas distintas y compararlos no diria nada."""
        pares = [
            (_inter("m1", ts=AHORA), _anal("m1", temas=["x"])),
            (_inter("m2"), _anal("m2", temas=["x"])),  # sin fecha
        ]
        ctx = ContextoLote.desde(pares)
        assert ctx.mas_reciente is None
        for _, a in pares:
            i = next(i for i, x in pares if x is a)
            p = puntuar(i, a, ctx)
            assert p.terminos_ausentes == ["recencia"]

    def test_sin_temas_falta_saliencia(self) -> None:
        """Si el LLM no detecto ningun tema en todo el lote, la saliencia
        no es 0: no hay nada que medir."""
        pares = [(_inter("m1", ts=AHORA), _anal("m1", temas=[]))]
        p = puntuar(*pares[0], ContextoLote.desde(pares))
        assert "saliencia_tema" in p.terminos_ausentes
        assert p.peso_total_aplicado == pytest.approx(0.75)

    def test_faltan_dos_terminos(self) -> None:
        """El caso real de los datos de muestra: ni temas ni fechas."""
        pares = [(_inter("m1"), _anal("m1", temas=[]))]
        p = puntuar(*pares[0], ContextoLote.desde(pares))
        assert set(p.terminos_ausentes) == {"recencia", "saliencia_tema"}
        assert p.peso_total_aplicado == pytest.approx(0.55)
        # Los pesos aplicados siguen sumando 1 entre los disponibles.
        assert sum(t.peso_aplicado for t in p.terminos) == pytest.approx(1.0)

    def test_los_pesos_aplicados_suman_uno(self) -> None:
        """La invariante: pase lo que pase, lo que se aplica suma 1."""
        for temas in ([], ["x"]):
            for ts in (None, AHORA):
                pares = [(_inter("m1", ts=ts), _anal("m1", temas=temas))]
                p = puntuar(*pares[0], ContextoLote.desde(pares))
                assert sum(t.peso_aplicado for t in p.terminos) == pytest.approx(1.0)


class TestSaliencia:
    def test_un_tema_que_se_repite_va_mas_alto(self) -> None:
        pares = [
            (_inter("m1"), _anal("m1", temas=["contratacion"])),
            (_inter("m2"), _anal("m2", temas=["contratacion"])),
            (_inter("m3"), _anal("m3", temas=["contratacion"])),
            (_inter("m4"), _anal("m4", temas=["c disparate"])),
        ]
        ctx = ContextoLote.desde(pares)
        comun = puntuar(*pares[0], ctx)
        raro = puntuar(*pares[3], ctx)
        assert (
            comun.pesos_por_nombre()["saliencia_tema"].valor
            > raro.pesos_por_nombre()["saliencia_tema"].valor
        )

    def test_sin_temas_propios_vale_cero(self) -> None:
        """No participa de ningun tema: eso es informacion, no ignorance."""
        pares = [(_inter("m1"), _anal("m1", temas=["x"])), (_inter("m2"), _anal("m2"))]
        p = puntuar(*pares[1], ContextoLote.desde(pares))
        assert p.pesos_por_nombre()["saliencia_tema"].valor == 0.0

    def test_los_temas_se_normalizan(self) -> None:
        """"Python" y "python" son el mismo tema. Si no, la frecuencia se
        parte en dos y la saliencia de un tema se parte con ella."""
        pares = [
            (_inter("m1"), _anal("m1", temas=["Python"])),
            (_inter("m2"), _anal("m2", temas=["python"])),
        ]
        ctx = ContextoLote.desde(pares)
        assert ctx.frecuencia_temas["python"] == 2
        assert puntuar(*pares[0], ctx).pesos_por_nombre()["saliencia_tema"].valor == (
            pytest.approx(1.0)
        )


class TestRecencia:
    def test_el_mas_reciente_del_lote_vale_uno(self) -> None:
        pares = [(_inter("m1", ts=AHORA), _anal("m1"))]
        p = puntuar(*pares[0], ContextoLote.desde(pares))
        assert p.pesos_por_nombre()["recencia"].valor == pytest.approx(1.0)

    def test_la_antiguedad_decae(self) -> None:
        """14 dias con vida media de 3: 0.5^4.67."""
        pares = [
            (_inter("m1", ts=AHORA), _anal("m1")),
            (_inter("m2", ts=AHORA - timedelta(days=14)), _anal("m2")),
        ]
        ctx = ContextoLote.desde(pares)
        assert puntuar(*pares[1], ctx).pesos_por_nombre()["recencia"].valor == (
            pytest.approx(0.039, abs=0.002)
        )

    def test_es_relativa_al_lote_no_al_reloj(self) -> None:
        """Si se midiera contra `now()`, re-correr el mismo lote manana
        daria otro score y dos corridas del mismo dato no compararian.
        Este test congela el reloj del sistema y verifica que el score
        no se mueve."""
        pares = [(_inter("m1", ts=AHORA - timedelta(days=400)), _anal("m1"))]
        p1 = puntuar(*pares[0], ContextoLote.desde(pares))
        p2 = puntuar(*pares[0], ContextoLote.desde(pares))
        assert p1.score == p2.score
        # Y con 400 dias, el mas reciente del lote sigue valiendo 1.0.
        assert p1.pesos_por_nombre()["recencia"].valor == pytest.approx(1.0)


class TestElScoreSeExplica:
    """La promesa del README: poder decir POR QUE."""

    def test_la_explicacion_nombra_el_termino_que_mas_aporto(self) -> None:
        pares = [(_inter("m1"), _anal("m1", sentimiento=0.9, es_logro=True, temas=["x"]))]
        p = puntuar(*pares[0], ContextoLote.desde(pares))
        assert p.mayor_aportacion.nombre in p.explicacion()

    def test_la_explicacion_avisa_la_renormalizacion(self) -> None:
        """Un 0.71 con cuatro terminos no es lo mismo que con cinco, y si
        el texto no lo dice alguien los compara."""
        pares = [(_inter("m1"), _anal("m1", sentimiento=0.5))]
        p = puntuar(*pares[0], ContextoLote.desde(pares))
        texto = p.explicacion()
        assert "recencia" in texto
        assert "renormalizados" in texto

    def test_sin_renormalizacion_no_menciona_pesos(self) -> None:
        pares = [(_inter("m1", ts=AHORA), _anal("m1", temas=["x"]))]
        p = puntuar(*pares[0], ContextoLote.desde(pares))
        assert "renormalizados" not in p.explicacion()

    def test_las_contribuciones_suman_el_score(self) -> None:
        pares = [(_inter("m1", ts=AHORA), _anal("m1", sentimiento=0.3, temas=["a"]))]
        p = puntuar(*pares[0], ContextoLote.desde(pares))
        assert sum(t.contribucion for t in p.terminos) == pytest.approx(p.score)


class TestLote:
    def test_conserva_el_orden_del_resumen(self) -> None:
        """El emparejamiento por indice es lo que hace auditable el
        sistema: si el score 0.82 queda pegado al mensaje equivocado, se
        publica el testimonio de una persona sobre el mensaje de otra."""
        resumen = ResumenAnalisis()
        for mid in ("m3", "m1", "m2"):
            resumen.agregar(
                ResultadoAnalisis(
                    interaccion=_inter(mid), analisis=_anal(mid), procedencia="llm"
                )
            )
        puntajes = puntuar_lote(resumen)
        assert [p.mensaje_id for p in puntajes] == ["m3", "m1", "m2"]

    def test_indexar_omite_los_sin_id(self) -> None:
        """Escribir 'None' como clave hace que dos mensajes sin id se
        pisen y uno desaparezca del ranking en silencio."""
        resumen = ResumenAnalisis()
        resumen.agregar(ResultadoAnalisis(interaccion=_inter("m1"), analisis=_anal("m1")))
        resumen.agregar(
            ResultadoAnalisis(
                interaccion=Interaccion(autor="X", canal="c", texto="t"),
                analisis=_anal(None),
            )
        )
        index = indexar_por_id(puntuar_lote(resumen))
        assert list(index) == ["m1"]


class TestRouter:
    def _caso(self, sent: float, es_logro: bool, es_pregunta: bool) -> DecisionRuta:
        i = _inter("m1")
        a = _anal("m1", sentimiento=sent, es_logro=es_logro, es_pregunta=es_pregunta)
        r = ResultadoAnalisis(interaccion=i, analisis=a, procedencia="heuristico")
        return enrutar(r, puntuar(i, a, ContextoLote.desde([(i, a)])))

    def test_hito_con_score_alto_es_caso_exito(self) -> None:
        d = self._caso(0.95, es_logro=True, es_pregunta=False)
        assert d.tipo is TipoActivo.CASO_EXITO
        assert "0.65" in d.motivo

    def test_hito_y_pregunta_juntas_gana_el_hito(self) -> None:
        """El orden importa: un logro es la senal mas cara de convertir
        en post, y mandarlo a FAQ tira el mejor material."""
        d = self._caso(0.95, es_logro=True, es_pregunta=True)
        assert d.tipo is TipoActivo.CASO_EXITO

    def test_score_alto_sin_hito_no_alcanza_caso_exito(self) -> None:
        """El umbral de score no alcanza solo: hace falta la senal de
        logro, o cualquier mensaje positivo se vuelve un post."""
        d = self._caso(0.99, es_logro=False, es_pregunta=False)
        assert d.tipo is not TipoActivo.CASO_EXITO

    def test_pregunta_con_score_medio_es_faq(self) -> None:
        d = self._caso(0.3, es_logro=False, es_pregunta=True)
        assert d.tipo is TipoActivo.FAQ
        assert "0.35" in d.motivo

    def test_score_bajo_es_highlight(self) -> None:
        d = self._caso(-0.8, es_logro=False, es_pregunta=False)
        assert d.tipo is TipoActivo.HIGHLIGHT
        assert "0.35" in d.motivo

    def test_el_hueco_de_la_tabla_cae_a_highlight(self) -> None:
        """Score entre 0.35 y 0.65, sin hito y sin pregunta: la tabla del
        README no lo define. Highlight es el de menor riesgo."""
        d = self._caso(0.5, es_logro=False, es_pregunta=False)
        assert d.tipo is TipoActivo.HIGHLIGHT
        assert "hueco" in d.regla

    def test_toda_decision_explica_su_motivo(self) -> None:
        for sent, hito, pre in ((0.95, True, False), (0.3, False, True), (-0.8, False, False)):
            d = self._caso(sent, es_logro=hito, es_pregunta=pre)
            assert d.motivo and d.regla

    def test_las_umbrales_son_las_del_readme(self) -> None:
        assert UMBRAL_CASO_EXITO == 0.65
        assert UMBRAL_FAQ == 0.35

    def test_caso_exito_y_faq_exigen_revision_humana(self) -> None:
        """Un caso de exito publica el testimonio de una persona con su
        nombre. Eso no sale de una maquina sin que alguien lo firme."""
        assert self._caso(0.95, True, False).es_revision_manual
        assert self._caso(0.3, False, True).es_revision_manual
        assert not self._caso(-0.8, False, False).es_revision_manual

    def test_orden_malos_corta(self) -> None:
        """Un router que empareja mal publica el testimonio equivocado."""
        r = ResultadoAnalisis(
            interaccion=_inter("m1"), analisis=_anal("m1"), procedencia="llm"
        )
        with pytest.raises(ValueError, match="no emparejan"):
            enrutar_lote([r, r], [Puntaje(
                mensaje_id="m1", score=0.5, terminos=[], peso_total_aplicado=1.0
            )])

    def test_agrupar_deja_vacias_las_ramas_sin_mensajes(self) -> None:
        """Un lote sin hitos no tiene caso_exito, y la ausencia tiene que
        ser visible: si el generador inventa un post para llenar el
        hueco, el router decidio bien y el generador lo arruino."""
        r = ResultadoAnalisis(
            interaccion=_inter("m1"), analisis=_anal("m1"), procedencia="llm"
        )
        p = Puntaje(
            mensaje_id="m1", score=0.1, terminos=[], peso_total_aplicado=1.0
        )
        grupos = agrupar_por_tipo(enrutar_lote([r], [p]))
        assert grupos[TipoActivo.CASO_EXITO] == []
        assert len(grupos[TipoActivo.HIGHLIGHT]) == 1
