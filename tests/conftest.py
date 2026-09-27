"""
Guardas globales de la suite de tests.

RED BLOQUEADA. A proposito, y de forma agresiva.

La API de Stack Exchange da 300 requests por dia. Durante el desarrollo
de M1 un test reconstruyo a mano la clave de cache, no coincidio con la
que generaba el cliente, y en vez de fallar salio a la red de verdad:
trajo 100 preguntas reales y quemo cuota sin que nadie se enterara. Un
test que pega a un servicio externo de cuota limitada no es un test, es
una bomba de reloj.

Estos guards hacen que ese error sea imposible de repetir:

  - `no_hay_red` reemplaza `socket.socket.connect` por una excepcion.
    Si un test intenta salir a internet, revienta de inmediato y con un
    mensaje que dice por que.
  - `entorno_aislado` limpia las variables de credenciales para que un
    test no dependa de un `.env` real ni pueda escribir en un bucket de
    produccion por accidente.

Con esto, "los tests son offline" deja de ser una promesa del README y
pasa a ser una propiedad verificada de la suite.
"""

from __future__ import annotations

import socket

import pytest

_VARS_SENSIBLES = (
    "OPENAI_API_KEY",
    "COHERE_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "LLM_API_KEY",
    "OCI_USER_OCID",
    "OCI_TENANCY_OCID",
    "OCI_PRIVATE_KEY",
    "OCI_BUCKET",
)


class RedBloqueadaError(RuntimeError):
    """Un test intento hacer I/O de red."""


@pytest.fixture(autouse=True)
def no_hay_red(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bloquea cualquier salida a la red durante los tests."""

    def _rechazar(*args: object, **kwargs: object) -> None:
        raise RedBloqueadaError(
            "un test intento abrir un socket. Los tests de Sieve deben ser "
            "100% offline: inyecta un transporte falso (ver "
            "tests/test_ingestion.py::TestMapeoStackExchange) o usa el cache."
        )

    monkeypatch.setattr(socket.socket, "connect", _rechazar)
    monkeypatch.setattr(socket.socket, "connect_ex", _rechazar)
    monkeypatch.setattr(socket, "create_connection", _rechazar)


@pytest.fixture(autouse=True)
def entorno_aislado(monkeypatch: pytest.MonkeyPatch) -> None:
    """Borra credenciales: ningun test puede tocar un recurso real."""
    import os

    for variable in _VARS_SENSIBLES:
        monkeypatch.delenv(variable, raising=False)
    # Settings() se instancia en algunos modulos; darle un directorio
    # de cache temporal evita escribir en data/cache/ durante la suite.
    monkeypatch.setenv("SIEVE_CACHE_DIR", ".cache-test")
    monkeypatch.setenv("HF_HOME", os.devnull)
