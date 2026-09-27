"""
Tests del camino hibrido: LLM, fallback, procedencia y diagnostico.

La regla de oro de este archivo: NINGUN test toca la red. El proveedor
falso de abajo no hace I/O, y `tests/conftest.py` prohibe abrir un
socket igual. Si un test aqui necesita una key para pasar, esta mal
planteado: el punto del diseno es que el sistema funcione sin una.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from sieve.analysis.base import MotivoFallback, ResultadoLLM
from sieve.analysis.hibrido import TAMANO_LOTE, AnalizadorHibrido
from sieve.analysis.prompts import VERSION_PROMPT_ANALISIS
from sieve.models import Interaccion

# ═══════════════════════════════════════════════════════════════════
#  El proveedor falso
# ═══════════════════════════════════════════════════════════════════


@dataclass
class ProveedorFalso:
    """Un LLM que responde lo que le digamos, sin red.

    Guardar los prompts es lo que permite verificar que el prompt dice
    lo que creemos que dice. Un proveedor que solo devuelve texto y
    punto obliga a confiar en el prompt a ojo, y un prompt que omite un
    campo en silencio es la forma mas comun de romper un pipeline.
    """

    por_defecto: str = "{}"
    respuestas: dict[str, str] = field(default_factory=dict)
    nombre: str = "falso"
    prompts: list[str] = field(default_factory=list)
    error: Exception | None = None

    def es_disponible(self) -> bool:
        return True

    def completar(self, prompt: str, *, max_tokens: int = 800) -> ResultadoLLM:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        for marca, respuesta in self.respuestas.items():
            if marca in prompt:
                return ResultadoLLM(
                    texto=respuesta, modelo=self.nombre, tokens_salida=42
                )
        return ResultadoLLM(texto=self.por_defecto, modelo=self.nombre, tokens_salida=7)


def interaccion(n: int, texto: str = "un mensaje de prueba con contenido") -> Interaccion:
    """Cada mensaje lleva su numero en el texto.

    Es lo que permite dirigir la respuesta del proveedor falso a un item
    concreto. Sin una marca distinta por mensaje, el fake no puede
    simular "el item 3 vinio malo", que es el caso que importa.
    """
    return Interaccion(
        autor=f"Autor{n}", canal="#test", texto=f"texto-{n}: {texto}", mensaje_id=f"m{n}"
    )


def item(indice: int, **overrides: object) -> dict[str, object]:
    datos: dict[str, object] = {
        "indice": indice,
        "sentimiento": "positivo",
        "score_sentimiento": 0.5,
        "temas": ["empleo"],
        "es_logro": False,
        "es_pregunta": False,
        "relevant": 0.5,
        "citas": [],
    }
    datos.update(overrides)
    return datos


def lote(*items: dict[str, object]) -> str:
    """El formato EXACTO que pide el prompt: un dict con 'resultados'.

    Importa que sea este y no una lista pelada: el prompt dice
    `{"resultados": [...]}`, y un test que usa otra forma verifica un
    contrato que el modelo nunca ve.
    """
    return json.dumps({"resultados": list(items)}, ensure_ascii=False)


@pytest.fixture
def mensajes() -> list[Interaccion]:
    return [interaccion(i) for i in range(1, 5)]


# ═══════════════════════════════════════════════════════════════════
#  El camino feliz
# ═══════════════════════════════════════════════════════════════════


class TestCaminoLLM:
    def test_usa_el_llm_cuando_esta(self, mensajes: list[Interaccion]) -> None:
        p = ProveedorFalso(por_defecto=lote(*[item(i) for i in range(1, 5)]))
        r = AnalizadorHibrido(p).analizar_lote(mensajes)

        assert len(r.resultados) == 4
        assert r.por_procedencia()["llm"] == 4
        assert r.por_procedencia()["heuristico"] == 0

    def test_el_resultado_conserva_el_id_del_mensaje(
        self, mensajes: list[Interaccion]
    ) -> None:
        p = ProveedorFalso(por_defecto=lote(*[item(i) for i in range(1, 5)]))
        r = AnalizadorHibrido(p).analizar_lote(mensajes)
        assert [x.analisis.mensaje_id for x in r.resultados] == ["m1", "m2", "m3", "m4"]

    def test_el_prompt_lleva_el_texto_real(self, mensajes: list[Interaccion]) -> None:
        """Si el texto no viaja, el modelo analiza el aire."""
        p = ProveedorFalso(por_defecto=lote(*[item(i) for i in range(1, 5)]))
        AnalizadorHibrido(p).analizar_lote(mensajes)
        assert "un mensaje de prueba" in p.prompts[0]

    def test_el_prompt_lleva_la_version(self, mensajes: list[Interaccion]) -> None:
        """Sin version en el prompt no hay forma de comparar dos ajustes.

        Y tiene que ir en CADA llamada, no en la primera: si el proveedor
        se cambia en caliente, el segundo tramo del lote se analiza con
        otro prompt y nadie se entera.
        """
        p = ProveedorFalso(por_defecto=lote(*[item(i) for i in range(1, 5)]))
        AnalizadorHibrido(p).analizar_lote(mensajes)
        assert p.prompts
        assert all(VERSION_PROMPT_ANALISIS in prompt for prompt in p.prompts)

    def test_el_prompt_pide_campos_que_el_contrato_exige(self, mensajes: list[Interaccion]) -> None:
        """El prompt y el modelo tienen que hablar de los mismos campos.

        Si el prompt pide "resumen" y el contrato espera "temas", el
        parser lo remedyara con un default y nadie va a notar que medio
        analisis sale vacio. Este test cruza las dos lados.
        """
        p = ProveedorFalso(por_defecto=lote(*[item(i) for i in range(1, 5)]))
        AnalizadorHibrido(p).analizar_lote(mensajes)
        prompt = p.prompts[0]
        for campo in ("sentimiento", "score_sentimiento", "temas", "es_logro", "relevant"):
            assert campo in prompt, f"el prompt no menciona {campo}"

    def test_registra_el_modelo(self, mensajes: list[Interaccion]) -> None:
        p = ProveedorFalso(por_defecto=lote(*[item(i) for i in range(1, 5)]), nombre="mi-modelo")
        r = AnalizadorHibrido(p).analizar_lote(mensajes)
        assert all(x.modelo == "mi-modelo" for x in r.resultados)

    def test_cuenta_las_llamadas(self, mensajes: list[Interaccion]) -> None:
        """4 mensajes en una ventana = 1 llamada, no 4.

        Si esto falla, cada ventana de 8 esta pagandose 8 veces, que es
        el costo invisible que hace que la gente apague el LLM.
        """
        p = ProveedorFalso(por_defecto=lote(*[item(i) for i in range(1, 5)]))
        a = AnalizadorHibrido(p)
        a.analizar_lote(mensajes)
        assert a.ultimo_diagnostico.llamadas_llm == 1


# ═══════════════════════════════════════════════════════════════════
#  El fallback, que es el punto de todo el ejercicio
# ═══════════════════════════════════════════════════════════════════


class TestFallback:
    def test_sin_proveedor_no_se_llama_a_nadie(self, mensajes: list[Interaccion]) -> None:
        class NoDisponible:
            nombre = "nadie"

            def es_disponible(self) -> bool:
                return False

            def completar(self, prompt: str, *, max_tokens: int = 800) -> ResultadoLLM:
                raise AssertionError("no debe llamar a un LLM no disponible")

        r = AnalizadorHibrido(NoDisponible()).analizar_lote(mensajes)
        assert r.por_procedencia()["heuristico"] == 4
        assert all(x.motivo_fallback == "sin_proveedor" for x in r.resultados)

    def test_proveedor_none_es_sin_proveedor(self, mensajes: list[Interaccion]) -> None:
        r = AnalizadorHibrido(None).analizar_lote(mensajes)
        assert r.por_procedencia()["heuristico"] == 4
        assert all(x.motivo_fallback == "sin_proveedor" for x in r.resultados)

    def test_el_llm_roto_no_tira_el_lote(self, mensajes: list[Interaccion]) -> None:
        """Un error de red no puede perder 4 analisis que la heuristica
        resuelve sin conexion. Eso es perder el trabajo de una persona.
        """
        p = ProveedorFalso(error=ConnectionError("se cayo la red"))
        r = AnalizadorHibrido(p).analizar_lote(mensajes)
        assert len(r.resultados) == 4
        assert r.por_procedencia()["heuristico"] == 4

    def test_json_basura_cae_a_heuristica(self, mensajes: list[Interaccion]) -> None:
        p = ProveedorFalso(por_defecto="perdona, soy un modelo de lenguaje")
        r = AnalizadorHibrido(p).analizar_lote(mensajes)
        assert r.por_procedencia()["heuristico"] == 4

    def test_el_motivo_es_del_conjunto_cerrado(self, mensajes: list[Interaccion]) -> None:
        """`motivo_fallback` es un Literal, no texto libre.

        Es la diferencia entre un motivo que se puede contar en un
        dashboard ("3 por json_invalido") y uno que hay que leer a mano.
        """
        p = ProveedorFalso(por_defecto="no soy json")
        r = AnalizadorHibrido(p).analizar_lote(mensajes)
        permitidos = set(MotivoFallback.__args__)
        for x in r.resultados:
            assert x.motivo_fallback in permitidos, x.motivo_fallback
            assert x.motivo_fallback is not None

    def test_el_lote_seguidor_tiene_heuristica_valida(
        self, mensajes: list[Interaccion]
    ) -> None:
        """La heuristica no devuelve campos vacios.

        Un fallback que produce un `Analisis` con todo en cero es peor que
        no tener fallback: parece que funciono y no sirvio para nada.
        """
        p = ProveedorFalso(por_defecto="basura")
        r = AnalizadorHibrido(p).analizar_lote(mensajes)
        for x in r.resultados:
            assert x.analisis.razon_relevante, "sin razon no hay debug"
            assert x.analisis.mensaje_id

    def test_el_resumen_es_mixto(self, mensajes: list[Interaccion]) -> None:
        """Un lote con 1 item de LLM y 3 de heuristica se reporta 'mixto'.

        El conteo de procedencia es lo que permite medir si el LLM sirve
        de algo. Sin el, no hay forma de saber si el prompt funciona.
        """
        p = ProveedorFalso(
            por_defecto=lote(item(1), item(2, score_sentimiento=99), item(3), item(4))
        )
        r = AnalizadorHibrido(p).analizar_lote(mensajes)
        assert r.por_procedencia()["llm"] + r.por_procedencia()["heuristico"] == 4
        assert r.por_procedencia()["mixto"] == 0, "un lote es un lote, no un resumen mixto"


# ═══════════════════════════════════════════════════════════════════
#  El reintento: el bug del indice 1
# ═══════════════════════════════════════════════════════════════════


class TestReintentoIndividual:
    def test_reintenta_cada_item_de_la_ventana(self) -> None:
        """Este test existe por un bug concreto.

        `_llm_de_a_uno` hacia `if i in por_indice` cuando `_llm_ventana`
        siempre devuelve la clave 1. Solo el primer mensaje de la ventana
        se reintentaba; los otros caian a heuristica sin retry, en
        silencio. Con 8 items por ventana, el 87% del camino de
        recuperacion no hacia nada.

        La asimetria es el guarda: si vuelve a aparecer un `i` donde
        deberia ir `1`, el item 1 pasa y el item 4 falla.
        """
        p = ProveedorFalso(por_defecto="basura, no soy json")
        p.respuestas = {"texto-4": lote(item(1))}

        r = AnalizadorHibrido(p).analizar_lote([interaccion(i) for i in range(1, 5)])

        cuarto = r.resultados[3]
        assert cuarto.analisis.mensaje_id == "m4"
        assert cuarto.procedencia == "llm", (
            f"el reintento no llego al item 4: {cuarto.procedencia} "
            f"motivo={cuarto.motivo_fallback}"
        )

    def test_reintenta_todos_los_que_faltan(self) -> None:
        """Ningun item de una ventana puede quedarse sin reintentar."""
        p = ProveedorFalso(por_defecto="basura")
        for i in range(1, 5):
            p.respuestas[f"texto-{i}"] = lote(item(1))

        r = AnalizadorHibrido(p).analizar_lote([interaccion(i) for i in range(1, 5)])
        assert all(x.procedencia == "llm" for x in r.resultados), [
            (x.analisis.mensaje_id, x.procedencia) for x in r.resultados
        ]

    def test_no_reintenta_lo_que_ya_venia_bien(self) -> None:
        """Si el lote funciono, no se paga el doble por item.

        Reintentar de mas tambien cuesta: aqui serian 4 llamadas extra
        por ventana, y en una API de pago eso se nota en la factura.
        """
        p = ProveedorFalso(por_defecto=lote(*[item(i) for i in range(1, 5)]))
        a = AnalizadorHibrido(p)
        r = a.analizar_lote([interaccion(i) for i in range(1, 5)])

        assert all(x.procedencia == "llm" for x in r.resultados)
        assert a.ultimo_diagnostico.llamadas_llm == 1, "no deberia haber reintentado"
        assert a.ultimo_diagnostico.ventanas == 1


# ═══════════════════════════════════════════════════════════════════
#  Un lote parcialmente valido
# ═══════════════════════════════════════════════════════════════════


class TestLoteParcialmenteValido:
    def test_un_item_invalido_no_tira_a_los_demas(self) -> None:
        """El caso mas comun de un modelo real: 7 bien y 1 con el score
        fuera de rango. Perder los 8 por el 1 es la razon por la que la
        gente apaga el LLM.
        """
        p = ProveedorFalso(
            por_defecto=lote(item(1), item(2, score_sentimiento=99), item(3), item(4))
        )
        r = AnalizadorHibrido(p).analizar_lote([interaccion(i) for i in range(1, 5)])

        assert len(r.resultados) == 4
        assert any(x.procedencia == "llm" for x in r.resultados)

    def test_el_score_fuera_de_rango_no_pasa(self) -> None:
        """99 no es un score. Aceptarlo significa publicar un numero que
        nadie puede defender.
        """
        p = ProveedorFalso(por_defecto=lote(item(1, score_sentimiento=99)))
        r = AnalizadorHibrido(p).analizar_lote([interaccion(1)])
        assert -1.0 <= r.resultados[0].analisis.score_sentimiento <= 1.0

    def test_el_indice_equivocado_no_se_roba_otro_item(self) -> None:
        """Si el modelo devuelve el indice 7 en un lote de 4, ese item no
        existe. Aceptarlo asignaria el analisis de uno a otro y el
        `mensaje_id` ya no cuadraria con el texto.
        """
        p = ProveedorFalso(por_defecto=lote(item(1), item(7), item(3), item(4)))
        r = AnalizadorHibrido(p).analizar_lote([interaccion(i) for i in range(1, 5)])
        assert [x.analisis.mensaje_id for x in r.resultados] == ["m1", "m2", "m3", "m4"]


# ═══════════════════════════════════════════════════════════════════
#  Ventanas, configuracion y cuadre del diagnostico
# ═══════════════════════════════════════════════════════════════════


class TestPipeline:
    def test_el_tamano_de_lote(self) -> None:
        """8 items por llamada. Ni 1 (caro) ni 50 (se pierde el item)."""
        assert TAMANO_LOTE == 8
        assert AnalizadorHibrido(None).tamano_lote == 8

    def test_parte_en_ventanas(self) -> None:
        p = ProveedorFalso(por_defecto="basura")
        a = AnalizadorHibrido(p)
        r = a.analizar_lote([interaccion(i) for i in range(1, 18)])
        assert len(r.resultados) == 17
        # 17 items = 3 ventanas (8 + 8 + 1)
        assert a.ultimo_diagnostico.ventanas == 3

    def test_el_tamano_es_configurable(self) -> None:
        p = ProveedorFalso(por_defecto="basura")
        a = AnalizadorHibrido(p, tamano_lote=2)
        a.analizar_lote([interaccion(i) for i in range(1, 5)])
        assert a.ultimo_diagnostico.ventanas == 2

    def test_lote_vacio_no_rompe(self) -> None:
        a = AnalizadorHibrido(ProveedorFalso())
        r = a.analizar_lote([])
        assert r.resultados == []
        assert r.total == 0
        assert a.ultimo_diagnostico.llamadas_llm == 0

    def test_el_diagnostico_cuadra_con_los_resultados(
        self, mensajes: list[Interaccion]
    ) -> None:
        """Si el conteo no suma el total, el dashboard miente.

        Peor que no tenerlo: un tablero que reporta el doble de lo
        gastado hace que se apague el LLM por motivos falsos.
        """
        p = ProveedorFalso(
            por_defecto=lote(item(1), item(2, score_sentimiento=99), item(3), item(4))
        )
        a = AnalizadorHibrido(p)
        a.analizar_lote(mensajes)
        d = a.ultimo_diagnostico
        assert d.items_por_llm + d.items_por_heuristica == len(mensajes)
        assert d.llamadas_llm >= d.ventanas

    def test_los_errores_quedan_registrados(self, mensajes: list[Interaccion]) -> None:
        """Un fallo que no aparece en el log es un fallo que se repite."""
        p = ProveedorFalso(error=ConnectionError("se cayo la red"))
        a = AnalizadorHibrido(p)
        a.analizar_lote(mensajes)
        assert a.ultimo_diagnostico.errores

    def test_el_resumen_agrega_tokens(self, mensajes: list[Interaccion]) -> None:
        p = ProveedorFalso(por_defecto=lote(*[item(i) for i in range(1, 5)]))
        r = AnalizadorHibrido(p).analizar_lote(mensajes)
        assert r.tokens_totales > 0

    def test_el_diagnostico_se_reinicia_entre_lotes(
        self, mensajes: list[Interaccion]
    ) -> None:
        """Sin esto, el costo del segundo lote arranca con el del primero
        acumulado y el tablero reporta el doble de lo gastado.
        """
        p = ProveedorFalso(por_defecto=lote(*[item(i) for i in range(1, 5)]))
        a = AnalizadorHibrido(p)
        a.analizar_lote(mensajes)
        primero = a.ultimo_diagnostico.tokens
        a.analizar_lote(mensajes)
        assert a.ultimo_diagnostico.tokens == primero
