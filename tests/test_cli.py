"""
Tests de la CLI.

Una CLI sin tests es codigo que nadie ejecuta hasta que rompe en la
maquina de otra persona. Estos tests usan `capsys` y un `tmp_path`, no
la red ni el home del usuario.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sieve.analysis.base import ResultadoLLM
from sieve.cli import main
from sieve.ingestion.cache import CacheDisco
from sieve.ingestion.stackexchange import ClienteStackExchange, clave_de_preguntas

LOTE = {
    "origen_comunidad": "comunidad-test",
    "periodo_referencia": "Semana_01",
    "interacciones": [
        {"autor": "Ana", "canal": "#c", "tipo": "logro", "texto": "termine algo"},
        {"autor": "Beto", "canal": "#c", "tipo": "duda", "texto": "no se como"},
    ],
}


@pytest.fixture
def lote(tmp_path: Path) -> Path:
    ruta = tmp_path / "lote.json"
    ruta.write_text(json.dumps(LOTE, ensure_ascii=False), encoding="utf-8")
    return ruta


def test_ingest_resume_el_lote(lote: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["ingest", str(lote)]) == 0
    salida = capsys.readouterr().out
    assert "2 interacciones" in salida
    assert "logro=1" in salida
    assert "duda=1" in salida


def test_ingest_deja_asignar_ids_de_mensaje(lote: Path, capsys: pytest.CaptureFixture) -> None:
    main(["ingest", str(lote)])
    assert "2 con mensaje_id" in capsys.readouterr().out


def test_ingest_escribe_salida(lote: Path, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    destino = tmp_path / "salida" / "normalizado.json"
    assert main(["ingest", str(lote), "--salida", str(destino)]) == 0
    assert destino.exists()
    datos = json.loads(destino.read_text(encoding="utf-8"))
    assert datos["origen_comunidad"] == "comunidad-test"


def test_ingest_acepta_csv(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    ruta = tmp_path / "datos.csv"
    ruta.write_text("autor,canal,texto\nAna,#c,hola\n", encoding="utf-8")
    assert main(["ingest", str(ruta)]) == 0
    assert "1 interacciones" in capsys.readouterr().out


def test_ingest_falla_limpiamente_si_no_existe(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    assert main(["ingest", str(tmp_path / "nada.json")]) == 1
    assert "no existe" in capsys.readouterr().err


def test_ingest_propaga_csv_invalido(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Un CSV con headers inservibles da codigo 1, no una traza."""
    ruta = tmp_path / "malo.csv"
    ruta.write_text("foo,bar\n1,2\n", encoding="utf-8")
    assert main(["ingest", str(ruta)]) == 1
    assert "no tiene filas utilizables" in capsys.readouterr().err


def test_cache_reporta_estado(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["cache", "--cache-dir", str(tmp_path)]) == 0
    assert "0 archivos" in capsys.readouterr().out


def test_cache_limpia(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    CacheDisco(tmp_path).escribir("a", {"x": 1})
    assert main(["cache", "--cache-dir", str(tmp_path), "--limpiar"]) == 0
    assert "borrados: 1" in capsys.readouterr().out


def test_fetch_se_desde_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Precalentado el cache, fetch no toca la red y lo dice."""
    payload = {
        "has_more": False,
        "items": [
            {
                "body": "<p>hola</p>",
                "score": 3,
                "answer_count": 1,
                "accepted_answer_id": 1,
                "link": "https://es.stackoverflow.com/questions/1/x",
                "owner": {"display_name": "Ana", "link": "https://x/u/1"},
                "content_license": "CC BY-SA 3.0",
                "creation_date": 1700000000,
                "tags": ["python"],
                "question_id": 1,
            }
        ],
    }
    # Se escribe la clave exacta que el cliente va a pedir.
    CacheDisco(tmp_path).escribir(clave_de_preguntas(), payload)

    assert main(["fetch-se", "--cache-dir", str(tmp_path), "--cache-only"]) == 0
    salida = capsys.readouterr().out
    assert "1 preguntas" in salida
    assert "1 con respuesta aceptada" in salida


def test_fetch_se_cache_only_falla_sin_cache(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """--cache-only es una promesa: sin cache, falla. No sale a la red."""
    assert main(["fetch-se", "--cache-dir", str(tmp_path), "--cache-only"]) == 1
    assert "cache-only" in capsys.readouterr().err


def test_subcomando_requerido() -> None:
    with pytest.raises(SystemExit):
        main([])


def test_comando_desconocido() -> None:
    with pytest.raises(SystemExit):
        main(["no-existe"])


def test_cliente_usa_el_mismo_thumbprint(tmp_path: Path) -> None:
    """La clave que el test precalienta tiene que ser la que pide la CLI.

    Este test existe para que un cambio futuro en la forma de la clave
    rompa aca, con un mensaje claro, y no en produccion con un cache
    vacio que nadie puede explicar.
    """
    cliente = ClienteStackExchange(cache_dir=str(tmp_path))
    assert cliente.clave("questions", {"site": "x"}) == "questions?[('site', 'x')]"


# ═══════════════════════════════════════════════════════════════════
#  `generar`
# ═══════════════════════════════════════════════════════════════════

LOTE_ANALIZADO = {
    "origen_comunidad": "comunidad-test",
    "periodo_referencia": "Semana_01",
    "interacciones": [
        {
            "autor": "Ana",
            "canal": "LinkedIn",
            "tipo": "logro",
            "texto": "Fue seleccionada para el puesto de Desarrolladora Junior de IA.",
            "mensaje_id": "m1",
        }
    ],
    "analisis": [
        {
            "mensaje_id": "m1",
            "sentimiento": "positivo",
            "score_sentimiento": 0.8,
            "es_logro": True,
        }
    ],
}


@pytest.fixture
def lote_analizado(tmp_path: Path) -> Path:
    ruta = tmp_path / "analizado.json"
    ruta.write_text(json.dumps(LOTE_ANALIZADO, ensure_ascii=False), encoding="utf-8")
    return ruta


class _Falso:
    """Provider de mentira para la CLI. Contesta segun el formato pedido.

    Leer el prompt para decidir que formato responder es lo que hace
    util el doble: si devolviera siempre el mismo JSON, los otros dos
    formatos fallarian por forma y el test probaria el parser de
    errores, no la CLI.
    """

    nombre = "falso"

    def __init__(self, disponible: bool) -> None:
        self._disponible = disponible

    def es_disponible(self) -> bool:
        return self._disponible

    def completar(self, prompt: str, *, max_tokens: int = 800) -> ResultadoLLM:
        if "FAQ" in prompt or "mentor" in prompt:
            datos: dict[str, object] = {"tema": "Nodos de reintento en LangGraph"}
        elif "resumen semanal" in prompt:
            datos = {
                "titular": "Ana fue seleccionada para un puesto de IA",
                "resumen": (
                    "Ana fue seleccionada para el puesto de Desarrolladora "
                    "Junior de IA."
                ),
            }
        else:
            datos = {
                "titulo": "Ana fue seleccionada para un puesto de IA",
                "copy": (
                    "Ana fue seleccionada para el puesto de Desarrolladora "
                    "Junior de IA."
                ),
                "hashtags": ["#CarreraDev"],
                "potencial_engagement": "Alto",
            }
        return ResultadoLLM(texto=json.dumps(datos, ensure_ascii=False), modelo="falso")


def test_generar_sin_analisis_explica_que_falta(
    lote: Path, capsys: pytest.CaptureFixture
) -> None:
    """La generación lee el análisis, no lo recalcula.

    Recalcular produciría otro número en CPU, y un activo cuyo puntaje
    viene de otro análisis es un activo que nadie puede auditar.
    """
    assert main(["generar", str(lote)]) == 1
    err = capsys.readouterr().err
    assert "no tiene analisis" in err
    assert "sieve analyze" in err


def test_generar_sin_provider_dice_que_no_hay_fallback(
    lote_analizado: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un fallback que devuelve texto genérico publica placeholders.

    El provider se falsea en vez de apuntar a un host muerto: la suite
    tiene un guard que aborta cualquier socket, y está bien que lo tenga.
    """
    monkeypatch.setattr(
        "sieve.analysis.providers.construir_proveedor", lambda *a, **k: _Falso(False)
    )
    assert main(["generar", str(lote_analizado), "--provider", "ollama"]) == 1
    err = capsys.readouterr().err
    assert "no esta configurado" in err
    assert "no tiene fallback" in err


def test_generar_advertencia_de_publicabilidad(
    lote_analizado: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aunque todo pase, el comando aclara que nada es publicable.

    El panel de curaduría no existe, así que `curado` siempre es False.
    Que el comando lo diga en pantalla es lo que evita que alguien se
    lleve el JSON creyendo que sí.
    """
    monkeypatch.setattr(
        "sieve.analysis.providers.construir_proveedor", lambda *a, **k: _Falso(True)
    )

    codigo = main(["generar", str(lote_analizado), "--provider", "ollama"])
    salida = capsys.readouterr().out
    assert codigo == 0
    assert "NINGUNO es publicable" in salida
    assert "curaduria" in salida


def test_generar_no_publica_nada(
    lote_analizado: Path, tmp_path: Path, capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con `--salida` escribe, pero los activos salen con `curado: false`."""
    monkeypatch.setattr(
        "sieve.analysis.providers.construir_proveedor", lambda *a, **k: _Falso(True)
    )
    prefijo = str(tmp_path / "activos")
    assert main(
        ["generar", str(lote_analizado), "--provider", "ollama", "--salida", prefijo]
    ) == 0
    capsys.readouterr()

    escritos = list(tmp_path.glob("activos.m1.json"))
    assert len(escritos) == 1
    datos = json.loads(escritos[0].read_text(encoding="utf-8"))
    post = datos["post_linkedin"]
    assert post["curado"] is False
    # Y el FAQ nunca sale listo para publicar, porque el contrato por
    # defecto dice justamente eso.
    assert datos["sugerencia_contenido_faq"]["status"] == "derivado_a_mentoria"
