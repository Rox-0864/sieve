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
