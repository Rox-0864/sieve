"""
Tests del comando `sieve publicar`.

Offline: el modo simulado no instancia ningun cliente OCI, asi que
estos tests no tocan la red ni necesitan credenciales. Lo que se prueba
es la regla de oro: solo sale lo firmado.
"""

from __future__ import annotations

from pathlib import Path

from sieve.cli import main
from sieve.curation import PaqueteCuracion, aplicar_decision, guardar_paquete
from sieve.generation.grounding import InformeGrounding, Verificacion
from sieve.models import Interaccion, PostLinkedin

FUENTE = Interaccion(
    autor="Ana",
    canal="LinkedIn",
    texto="Fue seleccionada para el puesto de Desarrolladora Junior de IA.",
    mensaje_id="m1",
)


def _paquete(decision: str | None = None) -> PaqueteCuracion:
    activo = PostLinkedin(
        titulo="Logro de Ana",
        copy="Ana fue seleccionada como Desarrolladora Junior.",
        hashtags=["#DevJr"],
        canal_recomendado="LinkedIn Oficial",
        potencial_engagement="Alto",
    )
    informe = InformeGrounding(verificaciones=[Verificacion("entidades", True)])
    paquete = PaqueteCuracion.desde_activo(activo, informe, FUENTE, "post_linkedin")
    if decision == "aprobado":
        return aplicar_decision(paquete, "aprobado", curador="curadora")
    return paquete


def test_publicar_simulado_no_toca_la_red(
    tmp_path: Path, capsys
) -> None:
    guardar_paquete(_paquete("aprobado"), tmp_path)
    assert main(["publicar", "--curar-dir", str(tmp_path)]) == 0
    salida = capsys.readouterr().out
    assert "simulado" in salida
    assert "activos/m1/post_linkedin.json" in salida


def test_publicar_salta_sin_firmar(tmp_path: Path, capsys) -> None:
    """Un paquete pendiente no sale, y el comando lo dice."""
    guardar_paquete(_paquete(), tmp_path)
    assert main(["publicar", "--curar-dir", str(tmp_path)]) == 0
    salida = capsys.readouterr().out
    assert "salteado" in salida
    assert "nada que publicar" in salida


def test_publicar_directorio_vacio_falla(tmp_path: Path, capsys) -> None:
    assert main(["publicar", "--curar-dir", str(tmp_path)]) == 1
    assert "no hay paquetes" in capsys.readouterr().err
