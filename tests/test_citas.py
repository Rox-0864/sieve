"""
Tests de la verificacion de citas. Esta es la garantia de que el sistema
no publica mentiras, asi que los tests son duros a proposito.

La regla del proyecto: una cita que no se puede verificar contra el texto
original NO llega al `Analisis`. No es un filtro, es una garantia. Y una
garantia sin tests es una promesa, no una garantia.
"""

from __future__ import annotations

import pytest

from sieve.analysis.citas import (
    citas_de_ventana,
    normalizar_para_citar,
    verificar_citas,
)

TEXTO = (
    "Después de tres meses de estudiar solo, me contrataron como dev junior. "
    "El portfolio abierto me sirvió más que el título."
)


# ═══════════════════════════════════════════════════════════════════
#  Normalizacion
# ═══════════════════════════════════════════════════════════════════


class TestNormalizacion:
    """`normalizar_para_citar` normaliza SOLO espacios y forma Unicode.

    No baja mayusculas ni quita acentos, y no deberia: es un helper de
    comparacion de espacios. La tolerancia real a acentos y mayusculas
    vive en `_es_substring`, y se prueba abajo a traves de
    `verificar_citas`, que es donde importa. Probar acentos contra este
    helper seria probar la funcion equivocada.
    """

    def test_colapsa_los_espacios(self) -> None:
        assert normalizar_para_citar("a  b   c") == normalizar_para_citar("a b c")

    def test_normaliza_la_forma_unicode(self) -> None:
        """NFC, para que "cafe\u0301" (combinante) y "caf\u00e9" sean lo mismo."""
        assert normalizar_para_citar("café") == normalizar_para_citar("café")

    def test_el_guion_bajo_no_es_espacio(self) -> None:
        """"snake_case" no es "snake case".

        Es un caso raro pero real: un modelo que cita codigo. Colapsar el
        guion en espacio haria que dos identifiers distintos se
        consideren el mismo fragmento.
        """
        assert normalizar_para_citar("mi_variable") != normalizar_para_citar(
            "mi variable"
        )


class TestToleranciaAlFormateo:
    """La comparacion real de una cita contra el texto.

    Todo esto se prueba por `verificar_citas` y no por el helper, porque
    es la garantia que importa: una cita real no puede perderse por una
    diferencia de tildes o de capitalizacion.
    """

    def test_una_mayuscula_de_mas_pasa(self) -> None:
        """Un modelo que capitaliza la primera letra esta citando."""
        verificadas, _ = verificar_citas(["Después de tres meses"], TEXTO)
        assert verificadas == ["Después de tres meses"]

    def test_una_cita_sin_tilde_pasa(self) -> None:
        """"me sirvio" donde el texto dice "me sirvió" es la misma cita.

        Reportar esto como inventada hace que el conteo de
        `citas_descartadas` mienta sobre la calidad del proveedor, que
        es justo el numero que sirve para decidir si cambiar de modelo.
        """
        verificadas, _ = verificar_citas(["me sirvio mas que el titulo"], TEXTO)
        assert verificadas == ["me sirvio mas que el titulo"]

    def test_los_espacios_irrelevantes_pasan(self) -> None:
        verificadas, _ = verificar_citas(["El  portfolio   abierto"], TEXTO)
        assert verificadas

    def test_una_cita_con_puntos_suspensivos_pasa(self) -> None:
        """Dos excerpts reales unidos por "..." siguen siendo dos excerpts reales."""
        cita = "me contrataron como dev junior... el portfolio abierto me sirvió"
        verificadas, descartadas = verificar_citas([cita], TEXTO)
        assert verificadas == [cita]
        assert descartadas == []


# ═══════════════════════════════════════════════════════════════════
#  Verificacion: la garantia central
# ═══════════════════════════════════════════════════════════════════


class TestVerificacion:
    def test_una_cita_exacta_pasa(self) -> None:
        verificadas, descartadas = verificar_citas(["me contrataron"], TEXTO)
        assert verificadas == ["me contrataron"]
        assert descartadas == []

    def test_una_cita_inventada_se_descarta(self) -> None:
        """El caso que motiva todo el modulo."""
        verificadas, descartadas = verificar_citas(
            ["me ascendieron a tech lead"], TEXTO
        )
        assert verificadas == []
        assert len(descartadas) == 1

    def test_lo_verbatim_manda_sobre_el_tono(self) -> None:
        """Un resumen fiel NO es una cita.

        "Consigue su primer trabajo" describe el mensaje perfectamente y
        no aparece en el. Si pasara, un activo podria citar una frase que
        el autor nunca dijo, y eso es exactamente lo que este modulo
        existe para impedir.
        """
        verificadas, _ = verificar_citas(["Consigue su primer trabajo"], TEXTO)
        assert verificadas == []

    def test_las_citas_validas_y_las_inventadas_no_se_mezclan(self) -> None:
        verificadas, descartadas = verificar_citas(
            ["el portfolio abierto", "una frase imposible", "me contrataron"], TEXTO
        )
        assert verificadas == ["el portfolio abierto", "me contrataron"]
        assert len(descartadas) == 1

    def test_una_cita_larga_que_no_esta_no_pasa(self) -> None:
        """Si la cita es larga, es muy dificil que sea casual.

        Que sea larga y no este es la senal de que el modelo invento una
        cita plausible en lugar de copiar.
        """
        larga = "despues de tres meses de estudiar solo me contrataron como developed"
        verificadas, descartadas = verificar_citas([larga], TEXTO)
        assert verificadas == []
        assert descartadas

    def test_texto_vacio_no_acepta_citas(self) -> None:
        """Con un mensaje vacio, cualquier cita es inventada."""
        verificadas, descartadas = verificar_citas(["algo"], "")
        assert verificadas == []
        assert descartadas

    def test_sin_citas_no_falla(self) -> None:
        """Dejar la lista vacia es la conducta CORRECTA del modelo.

        El prompt lo dice, y no puede ser un error: castigar al modelo
        por no citar lo hace inventar.
        """
        assert verificar_citas([], TEXTO) == ([], [])

    def test_una_cita_breve_no_alcanza_para_justificar(self) -> None:
        """"de" no es evidencia de nada.

        Aceptar una cita de una palabra deja pasar cualquier
        alucinacion que acierte una palabra comun. Un minimo de
        longitud es barato y corta el ruido.
        """
        verificadas, descartadas = verificar_citas(["de", "a", "y"], TEXTO)
        assert verificadas == []
        assert len(descartadas) == 3


# ═══════════════════════════════════════════════════════════════════
#  Ventanas: las citas de la heuristica
# ═══════════════════════════════════════════════════════════════════


class TestCitasDeVentana:
    def test_son_fragmentos_reales_del_texto(self) -> None:
        """La garantia aqui es mas fuerte: no hay nada que verificar.

        Salen por corte de la ventana, no de un modelo. Por eso pueden
        pasarse sin verificacion, y por eso un test que las compare con
        el original es suficiente.
        """
        for cita in citas_de_ventana(TEXTO):
            assert cita in TEXTO, f"cita inventada: {cita!r}"

    def test_un_texto_corto_no_produce_basura(self) -> None:
        """Un mensaje de cuatro palabras no tiene ventana que valga.

        Y una cita de menos de `LONGITUD_MINIMA_CITA` no sirve de nada,
        asi que se espera lista vacia.
        """
        assert citas_de_ventana("hola") == []

    def test_texto_vacio(self) -> None:
        assert citas_de_ventana("") == []

    def test_el_texto_mas_largo_produce_una_cita_util(self) -> None:
        citas = citas_de_ventana(TEXTO)
        assert len(citas) == 1
        assert len(citas[0]) >= 30

    def test_no_devuelve_el_texto_entero_como_cita(self) -> None:
        """Una cita igual al mensaje no es una cita: es un placeholder.

        Un LLM que devuelve el texto completo como "cita" pasa todos los
        tests de verificacion y no aporta nada. La ventana tiene que ser
        un recorte de tamanho util.
        """
        largo = TEXTO * 3
        for cita in citas_de_ventana(largo):
            assert cita != largo
            assert len(cita) < len(largo)

    def test_es_determinista(self) -> None:
        assert citas_de_ventana(TEXTO) == citas_de_ventana(TEXTO)

    def test_las_citas_heuristicas_pasan_la_verificacion_del_sistema(self) -> None:
        """Las dos reglas tienen que decir lo mismo.

        `verificar_citas` descarta toda cita bajo `LONGITUD_MINIMA_CITA`
        porque una palabra comun no prueba nada. `citas_de_ventana` tiene
        que aplicar la MISMA regla, no una propia: siemas una
        inconsistencia, un `Analisis` heuristico lleva citas que el mismo
        sistema rechazaria si vinieran de un LLM, y el conteo de
        `citas_descartadas` empieza a mentir sobre quien es mas preciso.
        """
        for texto in (TEXTO, "hola", "corto", TEXTO * 3, TEXTO * 6):
            for cita in citas_de_ventana(texto):
                verificadas, descartadas = verificar_citas([cita], texto)
                assert verificadas, (
                    f"la heuristica propuso una cita que el sistema "
                    f"descartaria: {cita!r}"
                )
                assert descartadas == []

    @pytest.mark.parametrize("texto", ["", "   ", "hola", "a", "corto"])
    def test_textos_demasiado_cortos_no_producen_citas(self, texto: str) -> None:
        assert citas_de_ventana(texto) == []
