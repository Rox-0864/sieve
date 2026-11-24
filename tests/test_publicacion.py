"""
Tests de la publicacion (M5).

Offline. El cliente de OCI es un doble que registra las subidas; el
modo simulado no instancia ningun cliente. La regla de oro que se
testea aca: un activo sin firmar nunca sale.
"""

from __future__ import annotations

import json

import pytest

from sieve.curation import PaqueteCuracion, aplicar_decision
from sieve.generation.grounding import InformeGrounding, Verificacion
from sieve.models import Interaccion, PostLinkedin
from sieve.publicacion import PublicacionError, PublicadorOci

FUENTE = Interaccion(
    autor="Ana",
    canal="LinkedIn",
    texto="Fue seleccionada para el puesto de Desarrolladora Junior de IA.",
    mensaje_id="demo-1",
)


class ClienteFalso:
    """Doble del cliente de almacenamiento: registra y devuelve una URL."""

    def __init__(self, falla: bool = False) -> None:
        self.subidas: list[tuple[bytes, str]] = []
        self._falla = falla

    def subir(self, contenido: bytes, ruta_objeto: str) -> str:
        self.subidas.append((contenido, ruta_objeto))
        if self._falla:
            raise RuntimeError("red caida")
        return f"https://fake.example/{ruta_objeto}"


def _paquete() -> PaqueteCuracion:
    activo = PostLinkedin(
        titulo="Logro de Ana",
        copy="Ana fue seleccionada como Desarrolladora Junior.",
        hashtags=["#DevJr"],
        canal_recomendado="LinkedIn Oficial",
        potencial_engagement="Alto",
    )
    informe = InformeGrounding(verificaciones=[Verificacion("entidades", True)])
    return PaqueteCuracion.desde_activo(activo, informe, FUENTE, "post_linkedin")


def _aprobado() -> PaqueteCuracion:
    return aplicar_decision(_paquete(), "aprobado", curador="curadora")


def test_publicar_aprobado_sube_y_devuelve_url() -> None:
    cliente = ClienteFalso()
    publicador = PublicadorOci(cliente, bucket="b")
    resultado = publicador.publicar(_aprobado())

    assert resultado.status == "guardado_con_exito"
    assert resultado.bucket == "b"
    assert resultado.ruta_objeto == "activos/demo-1/post_linkedin.json"
    assert resultado.url_preautenticada == "https://fake.example/activos/demo-1/post_linkedin.json"
    assert len(cliente.subidas) == 1


def test_publicar_pendiente_rechaza() -> None:
    publicador = PublicadorOci(ClienteFalso(), bucket="b")
    with pytest.raises(PublicacionError, match="no es publicable"):
        publicador.publicar(_paquete())


def test_publicar_simulado_no_toca_la_red() -> None:
    publicador = PublicadorOci(None, bucket="b")
    resultado = publicador.publicar(_aprobado())
    assert resultado.status == "simulado"
    assert resultado.url_preautenticada is None


def test_publicar_fallo_devuelve_fallido() -> None:
    publicador = PublicadorOci(ClienteFalso(falla=True), bucket="b")
    resultado = publicador.publicar(_aprobado())
    assert resultado.status == "fallido"
    assert publicador.ultimo_error is not None
    assert "red caida" in publicador.ultimo_error


def test_contenido_subido_es_el_activo_curado() -> None:
    cliente = ClienteFalso()
    publicador = PublicadorOci(cliente, bucket="b")
    publicador.publicar(_aprobado())

    contenido, _ruta = cliente.subidas[0]
    activo = json.loads(contenido.decode("utf-8"))
    assert activo["curado"] is True
    assert activo["titulo"] == "Logro de Ana"
