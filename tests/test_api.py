"""
Tests de la API HTTP (M7).

Offline: `TestClient` corre el ASGI app en memoria, sin socket. El
analisis heurístico y la publicacion simulada no necesitan nada; la
generacion usa un proveedor doble inyectado por monkeypatch.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from sieve.analysis.base import ResultadoLLM
from sieve.api import app
from sieve.curation import PaqueteCuracion, aplicar_decision
from sieve.generation.grounding import InformeGrounding, Verificacion
from sieve.models import Interaccion, PostLinkedin

cliente = TestClient(app)

LOTE = {
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
}

LOTE_ANALIZADO = {
    **LOTE,
    "analisis": [
        {"mensaje_id": "m1", "sentimiento": "positivo", "score_sentimiento": 0.8}
    ],
}


class _Falso:
    """Proveedor de mentira: responde JSON por formato, como la CLI."""

    nombre = "falso"

    def es_disponible(self) -> bool:
        return True

    def completar(self, prompt: str, *, max_tokens: int = 800) -> ResultadoLLM:
        if "FAQ" in prompt or "mentor" in prompt:
            datos: dict[str, object] = {"tema": "Como prepararse para un puesto de IA"}
        elif "resumen semanal" in prompt:
            datos = {
                "resumen": (
                    "Ana fue seleccionada para el puesto de Desarrolladora "
                    "Junior de IA."
                )
            }
        else:
            datos = {
                "copy": (
                    "Ana fue seleccionada para el puesto de Desarrolladora "
                    "Junior de IA."
                ),
                "hashtags": ["#CarreraDev"],
                "potencial_engagement": "Alto",
            }
        return ResultadoLLM(texto=json.dumps(datos, ensure_ascii=False), modelo="falso")


def test_health() -> None:
    respuesta = cliente.get("/health")
    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "ok"


def test_analizar_heuristica() -> None:
    respuesta = cliente.post("/analizar", json=LOTE)
    assert respuesta.status_code == 200
    lote = respuesta.json()
    assert len(lote["analisis"]) == 1, "el lote deberia salir analizado"


def test_analizar_provider_desconocido_falla() -> None:
    respuesta = cliente.post("/analizar", json=LOTE, params={"provider": "nope"})
    assert respuesta.status_code == 400
    assert "no soportado" in respuesta.json()["detail"]


def test_generar_sin_analisis_falla() -> None:
    respuesta = cliente.post("/generar", json=LOTE)
    assert respuesta.status_code == 400
    assert "analizar" in respuesta.json()["detail"]


def test_generar_con_proveedor_doble(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sieve.api.construir_proveedor", lambda *a, **k: _Falso()
    )
    respuesta = cliente.post(
        "/generar", json=LOTE_ANALIZADO, params={"perfil": "one"}
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["total"] == 1
    assert cuerpo["rechazados"] == 0
    assert len(cuerpo["activos"]) == 1


def test_publicar_simulado() -> None:
    activo = PostLinkedin(
        titulo="Logro de Ana",
        copy="Ana fue seleccionada como Desarrolladora Junior.",
        hashtags=["#DevJr"],
        canal_recomendado="LinkedIn Oficial",
        potencial_engagement="Alto",
    )
    informe = InformeGrounding(verificaciones=[Verificacion("entidades", True)])
    fuente = Interaccion(
        autor="Ana",
        canal="LinkedIn",
        texto="Fue seleccionada para el puesto de Desarrolladora Junior de IA.",
        mensaje_id="m1",
    )
    paquete = aplicar_decision(
        PaqueteCuracion.desde_activo(activo, informe, fuente, "post_linkedin"),
        "aprobado",
        curador="curadora",
    )

    respuesta = cliente.post(
        "/publicar",
        json=[paquete.model_dump(mode="json")],
    )
    assert respuesta.status_code == 200
    resultados = respuesta.json()
    assert len(resultados) == 1
    assert resultados[0]["status"] == "simulado"
    assert resultados[0]["ruta_objeto"] == "activos/m1/post_linkedin.json"


def test_publicar_salta_sin_firmar() -> None:
    activo = PostLinkedin(
        titulo="Logro de Ana",
        copy="Ana fue seleccionada como Desarrolladora Junior.",
        canal_recomendado="LinkedIn Oficial",
    )
    informe = InformeGrounding(verificaciones=[Verificacion("entidades", True)])
    fuente = Interaccion(
        autor="Ana",
        canal="LinkedIn",
        texto="Fue seleccionada para el puesto de Desarrolladora Junior de IA.",
        mensaje_id="m1",
    )
    pendiente = PaqueteCuracion.desde_activo(activo, informe, fuente, "post_linkedin")

    respuesta = cliente.post("/publicar", json=[pendiente.model_dump(mode="json")])
    assert respuesta.status_code == 200
    assert respuesta.json() == []
