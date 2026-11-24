"""
Tests del panel de curacion.

El panel es Streamlit, pero se testea sin navegador con `AppTest`: se
corre el script, se manipulan los widgets y se verifica que la firma
aterrice en el disco. La logica de la firma ya esta testeada en
`test_curation.py`; aca lo que se prueba es que la UI la dispara de
verdad y no solo la muestra.
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from sieve.curation import PaqueteCuracion, guardar_paquete
from sieve.generation.grounding import InformeGrounding, Verificacion
from sieve.models import Interaccion, PostLinkedin

APP = Path(__file__).resolve().parent.parent / "src" / "sieve" / "ui" / "curar.py"

FUENTE = Interaccion(
    autor="Ana",
    canal="LinkedIn",
    texto="Fue seleccionada para el puesto de Desarrolladora Junior de IA.",
    mensaje_id="demo-1",
)


def _paquete_post() -> PaqueteCuracion:
    activo = PostLinkedin(
        titulo="Logro de Ana",
        copy="Ana fue seleccionada como Desarrolladora Junior.",
        hashtags=["#DevJr"],
        canal_recomendado="LinkedIn Oficial",
        potencial_engagement="Alto",
    )
    informe = InformeGrounding(verificaciones=[Verificacion("entidades", True)])
    return PaqueteCuracion.desde_activo(
        activo,
        informe,
        FUENTE,
        "post_linkedin",
        respuesta_cruda='{"copy": "Ana fue seleccionada"}',
    )


def _app(directorio: Path) -> AppTest:
    at = AppTest.from_file(str(APP), default_timeout=15)
    at.run()
    at.text_input(key="directorio").set_value(str(directorio))
    at.run()
    return at


def test_directorio_vacio_muestra_aviso(tmp_path: Path) -> None:
    at = _app(tmp_path)
    assert not at.exception
    assert any("No hay paquetes" in i.value for i in at.info)


def test_muestra_fuente_y_activo(tmp_path: Path) -> None:
    guardar_paquete(_paquete_post(), tmp_path)
    at = _app(tmp_path)
    assert not at.exception
    assert any("post_linkedin" in s.value for s in at.subheader)
    assert any("Ana" in s.value for s in at.subheader)


def test_aprobar_persiste_la_firma(tmp_path: Path) -> None:
    guardar_paquete(_paquete_post(), tmp_path)
    at = _app(tmp_path)

    at.button(key="aprobar-demo-1-post_linkedin").click()
    at.run()

    paquete = PaqueteCuracion.model_validate_json(
        (tmp_path / "demo-1.post_linkedin.json").read_text(encoding="utf-8")
    )
    assert paquete.decision == "aprobado"
    assert paquete.activo["curado"] is True


def test_rechazar_sin_motivo_no_firma(tmp_path: Path) -> None:
    """Un rechazo sin motivo no se persiste: el motivo es lo auditable."""
    guardar_paquete(_paquete_post(), tmp_path)
    at = _app(tmp_path)

    at.button(key="rechazar-demo-1-post_linkedin").click()
    at.run()

    paquete = PaqueteCuracion.model_validate_json(
        (tmp_path / "demo-1.post_linkedin.json").read_text(encoding="utf-8")
    )
    assert paquete.decision == "pendiente"
    assert any("motivo" in e.value for e in at.error)


def test_rechazar_con_motivo_persiste(tmp_path: Path) -> None:
    guardar_paquete(_paquete_post(), tmp_path)
    at = _app(tmp_path)

    at.text_input(key="motivo-demo-1-post_linkedin").set_value("superlativo de más")
    at.button(key="rechazar-demo-1-post_linkedin").click()
    at.run()

    paquete = PaqueteCuracion.model_validate_json(
        (tmp_path / "demo-1.post_linkedin.json").read_text(encoding="utf-8")
    )
    assert paquete.decision == "rechazado"
    assert paquete.activo["curado"] is False
    assert paquete.motivo == "superlativo de más"
