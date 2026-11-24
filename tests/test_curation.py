"""
Tests de la curacion.

Offline, como todo el repo. No hay Streamlit aca: la logica de la firma
(aprobar, rechazar, persistir, reconstruir) es puro Python y se testea
sin la UI. El panel es una capa fina encima de esto.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sieve.curation import (
    PaqueteCuracion,
    aplicar_decision,
    cargar_paquetes,
    guardar_paquete,
    informe_a_dict,
    informe_de_dict,
)
from sieve.generation.grounding import InformeGrounding, Verificacion
from sieve.models import Interaccion, PostLinkedin, SugerenciaFaq

# ── Fixtures ───────────────────────────────────────────────────────

FUENTE = Interaccion(
    autor="Mariana Souza",
    canal="LinkedIn",
    texto="Fue seleccionada para el puesto de Desarrolladora Junior de IA.",
    mensaje_id="demo-1",
)


def _post() -> PostLinkedin:
    return PostLinkedin(
        titulo="Logro de Mariana",
        copy="Mariana fue seleccionada como Desarrolladora Junior.",
        hashtags=["#DevJr"],
        canal_recomendado="LinkedIn Oficial",
        potencial_engagement="Alto",
    )


def _informe_limpio() -> InformeGrounding:
    return InformeGrounding(verificaciones=[Verificacion("entidades", True)])


def _informe_con_supuestos() -> InformeGrounding:
    return InformeGrounding(
        verificaciones=[Verificacion("entidades", True)],
        supuestos=["'estudiante': terminologia oficial de Oracle"],
    )


def _informe_rechazado() -> InformeGrounding:
    return InformeGrounding(
        verificaciones=[
            Verificacion("entidades", False, ["Globant no esta en la fuente"])
        ]
    )


def _paquete(informe: InformeGrounding | None = None) -> PaqueteCuracion:
    return PaqueteCuracion.desde_activo(
        _post(), informe or _informe_limpio(), FUENTE, "post_linkedin"
    )


# ── Construccion ───────────────────────────────────────────────────


def test_desde_activo_arma_el_paquete() -> None:
    paquete = _paquete()
    assert paquete.mensaje_id == "demo-1"
    assert paquete.formato == "post_linkedin"
    assert paquete.fuente.autor == "Mariana Souza"
    assert paquete.decision == "pendiente"
    assert paquete.activo["curado"] is False


def test_desde_activo_exige_mensaje_id() -> None:
    fuente_sin_id = FUENTE.model_copy(update={"mensaje_id": None})
    with pytest.raises(ValueError, match="mensaje_id"):
        PaqueteCuracion.desde_activo(_post(), _informe_limpio(), fuente_sin_id, "post_linkedin")


def test_activo_tipado_reconstruye_la_clase() -> None:
    activo = _paquete().activo_tipado()
    assert isinstance(activo, PostLinkedin)
    assert activo.titulo == "Logro de Mariana"


def test_activo_tipado_por_formato() -> None:
    faq = SugerenciaFaq(
        tema="Como fue seleccionada",
        origen="Duda planteada por Mariana Souza",
        status="derivado_a_mentoria",
    )
    paquete = PaqueteCuracion.desde_activo(faq, _informe_limpio(), FUENTE, "faq")
    assert isinstance(paquete.activo_tipado(), SugerenciaFaq)


def test_informe_roundtrip() -> None:
    informe = _informe_con_supuestos()
    reconstruido = informe_de_dict(informe_a_dict(informe))
    assert reconstruido.veredicto == "aprobado_con_supuestos"
    assert reconstruido.supuestos == informe.supuestos
    assert reconstruido.verificaciones[0].nombre == "entidades"


# ── Persistencia ───────────────────────────────────────────────────


def test_guardar_y_cargar_roundtrip(tmp_path: Path) -> None:
    paquete = _paquete(_informe_con_supuestos())
    guardar_paquete(paquete, tmp_path)
    cargados = cargar_paquetes(tmp_path)
    assert len(cargados) == 1
    assert cargados[0] == paquete
    assert cargados[0].informe_tipado().veredicto == "aprobado_con_supuestos"


def test_guardar_nombra_por_mensaje_y_formato(tmp_path: Path) -> None:
    ruta = guardar_paquete(_paquete(), tmp_path)
    assert ruta.name == "demo-1.post_linkedin.json"


def test_cargar_directorio_inexistente_es_lista_vacia(tmp_path: Path) -> None:
    assert cargar_paquetes(tmp_path / "no-existe") == []


# ── La decision ────────────────────────────────────────────────────


def test_aprobar_da_vuelta_curado() -> None:
    aprobado = aplicar_decision(_paquete(), "aprobado", curador="curadora")
    assert aprobado.activo["curado"] is True
    assert aprobado.decision == "aprobado"
    assert aprobado.curador == "curadora"
    assert aprobado.fecha_decision is not None


def test_rechazar_exige_motivo() -> None:
    with pytest.raises(ValueError, match="motivo"):
        aplicar_decision(_paquete(), "rechazado")


def test_rechazar_deja_curado_en_false() -> None:
    rechazado = aplicar_decision(
        _paquete(), "rechazado", motivo="superlativo de mas", curador="curadora"
    )
    assert rechazado.activo["curado"] is False
    assert rechazado.decision == "rechazado"
    assert rechazado.motivo == "superlativo de mas"


def test_aplicar_decision_no_muta_el_original() -> None:
    original = _paquete()
    aplicar_decision(original, "aprobado", curador="curadora")
    assert original.decision == "pendiente"
    assert original.activo["curado"] is False


def test_decision_invalida_rechaza() -> None:
    with pytest.raises(ValueError, match="decision"):
        aplicar_decision(_paquete(), "pendiente")  # type: ignore[arg-type]


# ── El gate ────────────────────────────────────────────────────────


def test_publicable_exige_las_dos_condiciones() -> None:
    # Grounding limpio pero sin firma: no publicable.
    assert _paquete().publicable() is False
    aprobado = aplicar_decision(_paquete(), "aprobado", curador="curadora")
    assert aprobado.publicable() is True


def test_aprobado_con_grounding_rechazado_no_es_publicable() -> None:
    paquete = PaqueteCuracion.desde_activo(
        _post(), _informe_rechazado(), FUENTE, "post_linkedin"
    )
    aprobado = aplicar_decision(paquete, "aprobado", curador="curadora")
    assert aprobado.publicable() is False
