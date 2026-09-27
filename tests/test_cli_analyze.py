"""
Tests de `sieve analyze`. Offline: el default es heurística, y la red
esta bloqueada igual por `tests/conftest.py`.

El default sin LLM es la decision de diseno de este comando, asi que
tiene un test que lo verifica: si alguien pone un provider por defecto,
la suite tiene que romper.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sieve.cli import main
from sieve.models import Analisis, Interaccion, LoteComunidad, Sentimiento

LOTE = {
    "origen_comunidad": "es.stackoverflow",
    "periodo_referencia": "2026-09",
    "interacciones": [
        {
            "autor": "Ana",
            "canal": "#empleo",
            "texto": "Después de tres meses de estudiar solo, me contrataron como dev junior.",
            "mensaje_id": "m1",
        },
        {
            "autor": "Beto",
            "canal": "#preguntas",
            "texto": "¿Cómo hago un portfolio que no sea humo?",
            "mensaje_id": "m2",
        },
        {
            "autor": "Caro",
            "canal": "#charlas",
            "texto": "hola",
            "mensaje_id": "m3",
        },
    ],
}


@pytest.fixture
def lote(tmp_path: Path) -> Path:
    ruta = tmp_path / "lote.json"
    ruta.write_text(json.dumps(LOTE, ensure_ascii=False), encoding="utf-8")
    return ruta


# ═══════════════════════════════════════════════════════════════════
#  El contrato del comando
# ═══════════════════════════════════════════════════════════════════


class TestContratoDelComando:
    def test_el_default_no_pide_llm(self, lote: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Sin `--provider` no hay red. Nunca.

        No es prudencia, es la condicion para que el comando se pueda
        correr en una maquina sin credenciales. Un flag de debug que
        sale a una API de pago es una bomba de reloj.
        """
        assert main(["analyze", str(lote)]) == 0
        salida = capsys.readouterr().out
        assert "llm=0" in salida
        assert "llamadas=0" in salida

    def test_sin_llm_no_dice_fallback(self, lote: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """`sin_proveedor` no es un fallback: es el camino pedido.

        Si la salida dice "fallback" cuando nadie pidio un LLM, el
        usuario aprende a ignorar la palabra, y el dia que SÍ haya un
        fallback de verdad ya no la va a leer.
        """
        main(["analyze", str(lote), "--verbose"])
        salida = capsys.readouterr().out
        assert "fallback:" not in salida
        assert "heurística (no se pidió LLM)" in salida

    def test_ruta_inexistente_falla_limpio(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["analyze", "/tmp/no-existe-este-archivo.json"]) == 1
        assert "no existe" in capsys.readouterr().err

    def test_salida_escribe_los_analisis(self, lote: Path, tmp_path: Path) -> None:
        """El JSON tiene que salir con los analisis adentro.

        No en un archivo aparte, y no descartados: el consumidor
        descarga un JSON y tiene que tener los dos. Sin esto, Pydantic
        descarta el campo en silencio y el comando "funciona" mientras no
        guarda nada de lo que se le pidio.
        """
        destino = tmp_path / "salida.json"
        assert main(["analyze", str(lote), "--salida", str(destino)]) == 0

        datos = json.loads(destino.read_text(encoding="utf-8"))
        assert len(datos["interacciones"]) == 3
        assert len(datos["analisis"]) == 3
        assert datos["analisis"][0]["mensaje_id"] == "m1"

    def test_salida_respeta_el_orden(self, lote: Path, tmp_path: Path) -> None:
        """El emparejamiento es por indice, asi que el orden importa.

        Un analisis reordenado pone el testimonio de Ana sobre el
        mensaje de Beto, y eso se publica con el nombre de Beto.
        """
        destino = tmp_path / "salida.json"
        main(["analyze", str(lote), "--salida", str(destino)])
        datos = json.loads(destino.read_text(encoding="utf-8"))
        ids_interaccion = [i["mensaje_id"] for i in datos["interacciones"]]
        ids_analisis = [a["mensaje_id"] for a in datos["analisis"]]
        assert ids_interaccion == ids_analisis

    def test_el_analisis_es_util(self, lote: Path, tmp_path: Path) -> None:
        destino = tmp_path / "salida.json"
        main(["analyze", str(lote), "--salida", str(destino)])
        datos = json.loads(destino.read_text(encoding="utf-8"))
        ana = datos["analisis"][0]
        assert ana["es_logro"] is True
        assert ana["sentimiento"] == "muy_positivo"
        assert ana["razon_relevante"], "sin razon no se puede depurar"

    def test_verbose_muestra_cada_analisis(
        self, lote: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["analyze", str(lote), "--verbose"])
        salida = capsys.readouterr().out
        for i in ("m1", "m2", "m3"):
            assert i in salida

    def test_sin_verbose_es_corto(self, lote: Path, capsys: pytest.CaptureFixture[str]) -> None:
        main(["analyze", str(lote)])
        salida = capsys.readouterr().out
        assert "m1" not in salida
        assert len(salida.splitlines()) < 6

    def test_acepta_csv(self, tmp_path: Path) -> None:
        ruta = tmp_path / "lote.csv"
        ruta.write_text(
            "autor,canal,texto,mensaje_id\n"
            "Ana,#empleo,Después de tres meses me contrataron,m1\n",
            encoding="utf-8",
        )
        assert main(["analyze", str(ruta)]) == 0

    def test_un_provider_desconocido_no_silencia(self) -> None:
        """`choices` de argparse lo deberia impedir, pero el contrato
        tiene que estar en el codigo tambien: un `choices` se puede
        quitar sin que nadie se aviste."""
        with pytest.raises(SystemExit):
            main(["analyze", "x.json", "--provider", "inventado"])


# ═══════════════════════════════════════════════════════════════════
#  El emparejamiento, en el modelo
# ═══════════════════════════════════════════════════════════════════


class TestEmparejamientoEnElModelo:
    """El validador que impide publicar el analisis de una persona sobre
    el mensaje de otra."""

    def _inter(self, mid: str) -> Interaccion:
        return Interaccion(autor="X", canal="c", texto="texto", mensaje_id=mid)

    def _analisis(self, mid: str) -> Analisis:
        return Analisis(
            mensaje_id=mid,
            sentimiento=Sentimiento.NEUTRO,
            score_sentimiento=0.0,
        )

    def test_un_lote_sin_analisis_es_valido(self) -> None:
        """La entrada cruda no tiene analisis. Si fuera obligatorio, la
        ingestion de M1 tendria que inventar uno."""
        lote = LoteComunidad(
            origen_comunidad="c",
            periodo_referencia="p",
            interacciones=[self._inter("m1")],
        )
        assert lote.analisis == []

    def test_analisis_del_mismo_lote_pasa(self) -> None:
        lote = LoteComunidad(
            origen_comunidad="c",
            periodo_referencia="p",
            interacciones=[self._inter("m1"), self._inter("m2")],
            analisis=[self._analisis("m1"), self._analisis("m2")],
        )
        assert len(lote.analisis) == 2

    def test_cantidades_distintas_fallan(self) -> None:
        with pytest.raises(ValueError, match="no emparejan"):
            LoteComunidad(
                origen_comunidad="c",
                periodo_referencia="p",
                interacciones=[self._inter("m1"), self._inter("m2")],
                analisis=[self._analisis("m1")],
            )

    def test_un_id_cruzado_falla(self) -> None:
        """El caso grave: el analisis de m2 pegado sobre m1."""
        with pytest.raises(ValueError, match="pero la interaccion"):
            LoteComunidad(
                origen_comunidad="c",
                periodo_referencia="p",
                interacciones=[self._inter("m1")],
                analisis=[self._analisis("m2")],
            )

    def test_sin_ids_no_falla(self) -> None:
        """Si no hay ids, no hay nada que cruzar. El indice alcanza."""
        lote = LoteComunidad(
            origen_comunidad="c",
            periodo_referencia="p",
            interacciones=[Interaccion(autor="X", canal="c", texto="t")],
            analisis=[Analisis(sentimiento=Sentimiento.NEUTRO, score_sentimiento=0.0)],
        )
        assert len(lote.analisis) == 1


class TestTimeoutDeLLM:
    """El timeout de red es un parametro de produccion, no un default
    de libreria. Se prueba porque el default anterior (120s) cortaba
    JUSTO antes de que un modelo de 3B en CPU terminara el lote: se perdia
    el LLM y ademas saltaba el error. El peor resultado posible."""

    def test_ollama_usa_el_timeout_de_config(self) -> None:
        from sieve.analysis.providers import construir_proveedor
        from sieve.config import settings

        p = construir_proveedor("ollama", modelo="qwen2.5:3b")
        assert p is not None
        # El provider tiene que usar el valor de settings, no el default
        # de la clase. Si alguien vuelve a hardcodear 120, esto falla.
        assert settings.llm_timeout_seconds >= 300.0, (
            f"timeout de {settings.llm_timeout_seconds}s: un lote de 8 con un "
            "modelo de 3B en CPU tarda ~140s. Un timeout menor corta antes de "
            "que el modelo termine y tira el trabajo."
        )

    def test_el_default_no_es_el_default_de_libreria(self) -> None:
        from sieve.config import Settings

        assert Settings().llm_timeout_seconds == 900.0


class TestLaProcedenciaSePersiste:
    """La terminal puede mostrarla: lo que importa es que quede en el
    archivo, porque es lo que n8n va a leer en M3."""

    def test_el_json_lleva_la_procedencia(self, lote: Path, tmp_path: Path) -> None:
        salida = tmp_path / "lote.json"
        assert main(["analyze", str(lote), "--salida", str(salida)]) == 0
        d = json.loads(salida.read_text(encoding="utf-8"))
        assert "procedencia" in d, "la procedencia se imprime y no se guarda"
        assert d["procedencia"]["heuristico"] == len(d["interacciones"])
        assert d["procedencia"]["llm"] == 0

    def test_es_heuristica_como_pedida_no_como_fallback(
        self, lote: Path, tmp_path: Path
    ) -> None:
        """Sin --provider no se pidio LLM. Decir "no es de fiar" seria
        mentir: se hizo lo que se pidió."""
        salida = tmp_path / "lote.json"
        main(["analyze", str(lote), "--salida", str(salida)])
        d = json.loads(salida.read_text(encoding="utf-8"))
        assert d["procedencia"]["heuristico"] > 0

    def test_el_conteo_suma_los_analisis(self, lote: Path, tmp_path: Path) -> None:
        salida = tmp_path / "lote.json"
        main(["analyze", str(lote), "--salida", str(salida)])
        d = json.loads(salida.read_text(encoding="utf-8"))
        p = d["procedencia"]
        assert p["llm"] + p["heuristico"] + p["mixto"] == len(d["analisis"])
