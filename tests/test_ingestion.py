"""
Tests de ingesta. TODOS OFFLINE.

Ningun test toca la red. La API real se prueba a mano; lo que se
prueba aca es el parseo, el mapeo y el cache, que es donde viven los
bugs de verdad. Un test que necesita internet es un test que falla un
viernes a las 23:50 y te hace dudar del codigo en vez de tu wifi.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sieve.ingestion import (
    CacheDisco,
    GroundTruth,
    cargar_csv,
    cargar_json,
    desescapar_tags,
    html_a_texto,
    mensaje_id,
    truncar,
)
from sieve.ingestion.stackexchange import ClienteStackExchange, _etiqueta_canal
from sieve.models import TipoInteraccion

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_SE = FIXTURES / "stackexchange_questions.json"


@pytest.fixture
def payload_api() -> dict:
    return json.loads(FIXTURE_SE.read_text(encoding="utf-8"))


class _TransporteFalso:
    """Transporte que devuelve el fixture y cuenta las llamadas.

    Inyectarlo es lo que garantiza que estos tests no tocan la red. Un
    test que depende de la cuota diaria de 300 requests no es un test.
    """

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.llamadas: list[tuple[str, dict]] = []

    def __call__(self, ruta: str, params: dict) -> tuple[dict, int | None]:
        self.llamadas.append((ruta, params))
        return self.payload, 291


@pytest.fixture
def falso(payload_api: dict) -> _TransporteFalso:
    return _TransporteFalso(payload_api)


@pytest.fixture
def cliente(tmp_path: Path, falso: _TransporteFalso) -> ClienteStackExchange:
    return ClienteStackExchange(cache_dir=str(tmp_path / "cache"), transporte=falso)


# ═══════════════════════════════════════════════════════════════════
#  LIMPIEZA DE TEXTO
# ═══════════════════════════════════════════════════════════════════


class TestLimpiezaTexto:
    def test_quita_tags_y_conserva_parrafos(self) -> None:
        html = "<p>Primer parrafo.</p><p>Segundo parrafo.</p>"
        texto = html_a_texto(html)
        assert texto == "Primer parrafo.\n\nSegundo parrafo."

    def test_conserva_la_indentacion_del_codigo(self) -> None:
        """La indentacion del codigo es informacion.

        Sin ella, un ejemplo de Python deja de tener sentido y el LLM
        genera basura a partir de basura. Es el caso donde 'limpiar de
        mas' es peor que no limpiar.
        """
        html = "<pre><code>def sumar(a, b):\n    return a + b</code></pre>"
        texto = html_a_texto(html)
        assert "    return a + b" in texto

    def test_unescape_de_entidades_en_el_cuerpo(self) -> None:
        assert "solución" in html_a_texto("<p>La soluci&oacute;n es esta</p>")

    def test_descarta_script_y_estilo(self) -> None:
        html = "<p>Visible</p><script>alert('x')</script><style>p{}</style>"
        texto = html_a_texto(html)
        assert "Visible" in texto
        assert "alert" not in texto
        assert "p{}" not in texto

    def test_html_malformado_no_rompe(self) -> None:
        """Un <p> sin cerrar no puede tumbar la ingesta.

        Este archivo vive en el borde del sistema: se degrada, no se
        rompe. Perder 99 de 100 registros por uno raro es peor que
        tener un registro con texto de mas.
        """
        texto = html_a_texto("<p>Texto antes <b>sin cerrar y despues")
        assert "Texto antes" in texto

    def test_vacio_devuelve_vacio(self) -> None:
        assert html_a_texto("") == ""


class TestTags:
    def test_desescape_los_tags_de_la_api(self) -> None:
        """`inyecci&#243;n-sql` tiene que terminar en `inyección-sql`.

        Sin esto, los tags se usan como criterio contra un valor que no
        existe, el filtro no matchea nada, y el pipeline entero no
        encuentra resultados sin disparar una sola alarma.
        """
        tags = desescapar_tags(["inyecci&#243;n-sql", "automatizaci&#243;n", "php"])
        assert tags == ["inyección-sql", "automatización", "php"]

    def test_no_rompe_tags_sanos(self) -> None:
        assert desescapar_tags(["python", "rag"]) == ["python", "rag"]


class TestTruncado:
    def test_no_trunca_si_esta_dentro_del_limite(self) -> None:
        assert truncar("corto", 100) == "corto"

    def test_trunca_en_palabra_no_a_la_mitad(self) -> None:
        texto = "palabra " * 100
        resultado = truncar(texto, 50)
        assert len(resultado) < 60
        assert resultado.endswith("…")
        # no debe cortar en medio de una palabra
        assert not resultado[:-1].rstrip().endswith(("pala", "palabr"))


# ═══════════════════════════════════════════════════════════════════
#  CACHE
# ═══════════════════════════════════════════════════════════════════


class TestCache:
    def test_escribe_y_lee(self, tmp_path: Path) -> None:
        cache = CacheDisco(tmp_path, ttl_segundos=999)
        cache.escribir("clave", {"hola": "mundo"})
        assert cache.leer("clave") == {"hola": "mundo"}

    def test_clave_inexistente_devuelve_none(self, tmp_path: Path) -> None:
        assert CacheDisco(tmp_path).leer("nada") is None

    def test_vence_por_ttl(self, tmp_path: Path) -> None:
        """TTL=0 significa 'siempre vencido': hay que pegarle a la API."""
        cache = CacheDisco(tmp_path, ttl_segundos=0)
        cache.escribir("clave", 1)
        assert cache.leer("clave") is None

    def test_json_corrupto_no_propaga_excepcion(self, tmp_path: Path) -> None:
        """Un cache nunca puede ser la razon por la que el pipeline cae."""
        cache = CacheDisco(tmp_path, ttl_segundos=999)
        cache.escribir("clave", {"ok": True})
        for archivo in cache.raiz.glob("*.json"):
            archivo.write_text("{truncado", encoding="utf-8")
        assert cache.leer("clave") is None

    def test_limpiar(self, tmp_path: Path) -> None:
        cache = CacheDisco(tmp_path, ttl_segundos=999)
        cache.escribir("a", 1)
        cache.escribir("b", 2)
        assert cache.limpiar() == 2
        assert len(cache) == 0


# ═══════════════════════════════════════════════════════════════════
#  CLIENTE DE STACK EXCHANGE (con fixture, sin red)
# ═══════════════════════════════════════════════════════════════════


class TestMapeoStackExchange:
    def test_mapea_una_pregunta_completa(self, cliente) -> None:
        resultado = cliente.preguntas(paginas=1)
        assert len(resultado) == 3
        primera = resultado[0]
        assert primera.interaccion.autor == "Alvaro Montoro"
        assert primera.interaccion.tipo == TipoInteraccion.DUDA
        assert "inyección SQL" in primera.interaccion.texto

    def test_pide_el_cuerpo_completo(self, cliente, falso) -> None:
        """Sin `filter=withbody` no llega el cuerpo y no hay nada que limpiar.

        Es el parametro mas importante de la llamada y el mas facil de
        olvidar: el cliente funciona perfecto y devuelve texto vacio.
        """
        cliente.preguntas(paginas=1)
        assert falso.llamadas[0][1]["filter"] == "withbody"

    def test_mapea_el_codigo_conservando_la_indentacion(self, cliente) -> None:
        texto = cliente.preguntas(paginas=1)[0].interaccion.texto
        assert "$stmt = $pdo->prepare(" in texto

    def test_captura_el_ground_truth(self, cliente) -> None:
        gt = cliente.preguntas(paginas=1)[0].ground_truth
        assert gt is not None
        assert gt.score == 237
        assert gt.answer_count == 6
        assert gt.aceptado is True
        assert "CC BY-SA 3.0" in (gt.content_license or "")

    def test_conserva_la_atribucion_obligatoria(self, cliente) -> None:
        """Sin url y author_url no se puede cumplir CC BY-SA.

        No es un detalle: sin esos dos links el uso del dato no es
        licito. El modelo los expone como opcionales porque la
        mayoria de los fuentes no los tiene, pero cuando estan, se
        guardan.
        """
        gt = cliente.preguntas(paginas=1)[0].ground_truth
        assert gt.url and gt.url.startswith("https://es.stackoverflow.com/questions/")
        assert gt.author_url and "/users/" in gt.author_url

    def test_registra_la_cuota_restante(self, cliente) -> None:
        cliente.preguntas(paginas=1)
        assert cliente.cuota_restante == 291

    def test_sin_respuesta_aceptada_es_ground_truth_negativo(self, cliente) -> None:
        """La pregunta #2 no tiene accepted answer: es un caso NEGATIVO.

        Sirve para medir precision, no solo recall. Un evaluador que
        solo mira aciertos positivos no esta midiendo nada.
        """
        etiquetadas = cliente.preguntas(paginas=1)
        no_aceptada = [e for e in etiquetadas if e.ground_truth and not e.ground_truth.aceptado]
        assert no_aceptada, "el fixture debe traer al menos un caso negativo"
        assert no_aceptada[0].ground_truth.score < 237

    def test_filtra_por_score_minimo(self, cliente) -> None:
        todos = cliente.preguntas(paginas=1, minimo_score=0)
        solo_alto = cliente.preguntas(paginas=1, minimo_score=100)
        assert len(solo_alto) < len(todos)
        assert all(e.ground_truth.score >= 100 for e in solo_alto)

    def test_descarta_el_score_cero_por_defecto(self, cliente, payload_api) -> None:
        """Sin score no fue preguntada: entra al pipeline solo para gastar cuota."""
        payload_api["items"][1]["score"] = 0
        assert len(cliente.preguntas(paginas=1)) == 2

    def test_segunda_consulta_no_gasta_red(self, cliente, falso) -> None:
        cliente.preguntas(paginas=1)
        cliente.preguntas(paginas=1)
        assert len(falso.llamadas) == 1, "la segunda debe salir del cache"
        assert cliente.requests_hechos == 1
        assert cliente.aciertos_cache == 1

    def test_ttl_cero_siempre_pide_red(self, tmp_path, falso, payload_api) -> None:
        cliente = ClienteStackExchange(
            cache_dir=str(tmp_path / "c"), ttl_segundos=0, transporte=falso
        )
        cliente.preguntas(paginas=1)
        cliente.preguntas(paginas=1)
        assert len(falso.llamadas) == 2

    def test_para_por_pagina_si_no_hay_mas(self, cliente, falso) -> None:
        """El fixture trae has_more=false: pedir la segunda pagina seria
        un request contra una API que ya nos dijo que no hay mas."""
        cliente.preguntas(paginas=5)
        assert len(falso.llamadas) == 1

    def test_body_vacio_no_rompe_la_ingesta(self, cliente, payload_api) -> None:
        """Un registro raro no puede abortar los otros dos."""
        payload_api["items"][0]["body"] = ""
        assert len(cliente.preguntas(paginas=1)) == 2

    def test_owner_ausente_no_rompe(self, cliente, payload_api) -> None:
        """La API devuelve items sin owner cuando el usuario fue borrado."""
        payload_api["items"][0]["owner"] = None
        etiquetada = cliente.preguntas(paginas=1)[0]
        assert etiquetada.interaccion.autor == "desconocido"
        assert etiquetada.ground_truth.author_url is None


# ═══════════════════════════════════════════════════════════════════
#  GROUND TRUTH
# ═══════════════════════════════════════════════════════════════════


class TestGroundTruth:
    def test_sin_juicio_humano(self) -> None:
        assert GroundTruth().tiene_judicio_humano is False

    def test_upvote_alto_es_juicio_humano(self) -> None:
        assert GroundTruth(score=5).tiene_judicio_humano is True

    def test_aceptada_domin_la_señal(self) -> None:
        """La aceptacion es la señal mas fuerte y la mas escasa."""
        solo_aceptada = GroundTruth(score=0, answer_count=0, aceptado=True)
        muchos_upvotes = GroundTruth(score=40, answer_count=5, aceptado=False)
        assert solo_aceptada.intensidad() > muchos_upvotes.intensidad()

    def test_intensidad_esta_normalizada(self) -> None:
        extremo = GroundTruth(score=10_000, answer_count=900, aceptado=True, view_count=10**6)
        assert 0.0 <= extremo.intensidad() <= 1.0

    def test_es_inmutable(self) -> None:
        """Una referencia que se puede mutar no es una referencia."""
        gt = GroundTruth(score=10)
        with pytest.raises((AttributeError, TypeError)):
            gt.score = 50  # type: ignore[misc]


class TestMensajeId:
    def test_es_estable_entre_ejecuciones(self) -> None:
        from sieve.models import Interaccion

        a = Interaccion(autor="Ana", canal="#c", texto="hola")
        b = Interaccion(autor="Ana", canal="#c", texto="hola")
        assert mensaje_id(a) == mensaje_id(b)

    def test_cambia_con_el_contenido(self) -> None:
        from sieve.models import Interaccion

        a = Interaccion(autor="Ana", canal="#c", texto="hola")
        b = Interaccion(autor="Ana", canal="#c", texto="adios")
        assert mensaje_id(a) != mensaje_id(b)

    def test_es_corto_y_legible(self) -> None:
        from sieve.models import Interaccion

        assert len(mensaje_id(Interaccion(autor="A", canal="c", texto="x"))) == 12


# ═══════════════════════════════════════════════════════════════════
#  CARGA LOCAL
# ═══════════════════════════════════════════════════════════════════


class TestCargaLocal:
    def test_carga_el_formato_del_documento(self, tmp_path: Path) -> None:
        archivo = tmp_path / "lote.json"
        archivo.write_text(
            json.dumps(
                {
                    "origen_comunidad": "Discord_Grupo_ONE_G10",
                    "periodo_referencia": "Semana_04",
                    "interacciones": [
                        {
                            "autor": "Mariana Souza",
                            "canal": "#logros-y-empleos",
                            "tipo": "testimonio",
                            "texto": "Me contrataron como dev junior!",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        lote = cargar_json(archivo)
        assert lote.origen_comunidad == "Discord_Grupo_ONE_G10"
        assert lote.interacciones[0].tipo == TipoInteraccion.TESTIMONIO

    def test_acepta_una_lista_suelta(self, tmp_path: Path) -> None:
        archivo = tmp_path / "lista.json"
        archivo.write_text(
            json.dumps([{"autor": "A", "canal": "c", "texto": "hola"}]),
            encoding="utf-8",
        )
        assert len(cargar_json(archivo).interacciones) == 1

    def test_asigna_mensaje_id_a_lo_que_viene_sin_el(self, tmp_path: Path) -> None:
        archivo = tmp_path / "l.json"
        archivo.write_text(
            json.dumps([{"autor": "A", "canal": "c", "texto": "hola"}]),
            encoding="utf-8",
        )
        assert cargar_json(archivo).interacciones[0].mensaje_id is not None

    def test_csv_con_coma(self, tmp_path: Path) -> None:
        archivo = tmp_path / "datos.csv"
        archivo.write_text(
            "autor,canal,texto\nAna,#logros,me_SINGLE_graduei\nBeto,#dudas,como hago X\n",
            encoding="utf-8",
        )
        lote = cargar_csv(archivo)
        assert len(lote.interacciones) == 2
        assert lote.interacciones[0].autor == "Ana"

    def test_csv_con_punto_y_coma(self, tmp_path: Path) -> None:
        """El CSV de Excel en locale europeo usa ';'.

        Parseado con ',' produce UNA columna llamada
        'autor;canal;texto' y el pipeline entero no encuentra autores.
        """
        archivo = tmp_path / "excel.csv"
        archivo.write_text(
            "autor;canal;texto\nAna;#logros;hola\nBeto;#dudas;adios\n", encoding="utf-8"
        )
        lote = cargar_csv(archivo)
        assert len(lote.interacciones) == 2
        assert lote.interacciones[0].canal == "#logros"

    def test_csv_con_headers_en_ingles(self, tmp_path: Path) -> None:
        archivo = tmp_path / "en.csv"
        archivo.write_text("author,channel,content\nAna,general,hola\n", encoding="utf-8")
        lote = cargar_csv(archivo)
        assert lote.interacciones[0].autor == "Ana"
        assert lote.interacciones[0].texto == "hola"

    def test_csv_vacio_da_error_util(self, tmp_path: Path) -> None:
        """El error tiene que decir WHICH headers esperaba, no solo 'error'."""
        archivo = tmp_path / "malo.csv"
        archivo.write_text("foo,bar\n1,2\n", encoding="utf-8")
        with pytest.raises(ValueError, match="autor/author/user"):
            cargar_csv(archivo)

    def test_filas_sin_texto_se_descartan(self, tmp_path: Path) -> None:
        archivo = tmp_path / "datos.csv"
        archivo.write_text("autor,canal,texto\nAna,#c,hola\nBeto,#d,\n", encoding="utf-8")
        assert len(cargar_csv(archivo).interacciones) == 1


class TestCanalSintetico:
    def test_incluye_sitio_y_tags(self) -> None:
        canal = _etiqueta_canal("es.stackoverflow", ("php", "sql", "seguridad", "extra"))
        assert canal.startswith("#es.stackoverflow")
        assert "php" in canal and "sql" in canal

    def test_limita_a_tres_tags(self) -> None:
        canal = _etiqueta_canal("es.stackoverflow", ("a", "b", "c", "d", "e"))
        assert canal.count("·") == 3

    def test_sin_tags_usa_el_sitio(self) -> None:
        assert _etiqueta_canal("es.stackoverflow", ()) == "#es.stackoverflow"
