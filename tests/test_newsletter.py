"""
Tests del resumen semanal (`sieve.newsletter`).

Verifica que la agregacion junta los destaques, filtra lo sin curar por
defecto, y conserva el orden del lote (no reordena: eso es editorial).
"""

from __future__ import annotations

from sieve.models import ActivosGenerados, Newsletter
from sieve.newsletter import armar_newsletter


def _newsletter(titular: str, *, curado: bool = False) -> Newsletter:
    return Newsletter(
        seccion="Logro de la Semana",
        titular=titular,
        resumen=f"Resumen de {titular}, con mas de veinte caracteres de largo.",
        curado=curado,
    )


def _con_newsletter(destaque: Newsletter | None) -> ActivosGenerados:
    return ActivosGenerados(destaque_newsletter_semanal=destaque)


def test_arma_newsletter_junta_los_destaques() -> None:
    activos = [
        _con_newsletter(_newsletter("Ana", curado=True)),
        _con_newsletter(None),  # sin destaque
        _con_newsletter(_newsletter("Beto", curado=True)),
    ]
    semanal = armar_newsletter(activos, periodo="2026-47")
    assert [d.titular for d in semanal.destaques] == ["Ana", "Beto"]


def test_arma_newsletter_filtra_sin_curar() -> None:
    """Un destaque sin firmar no sale en el resumen, por defecto."""
    activos = [
        _con_newsletter(_newsletter("Ana", curado=True)),
        _con_newsletter(_newsletter("Beto", curado=False)),
    ]
    semanal = armar_newsletter(activos, periodo="2026-47")
    assert [d.titular for d in semanal.destaques] == ["Ana"]


def test_arma_newsletter_puede_incluir_sin_curar() -> None:
    activos = [
        _con_newsletter(_newsletter("Ana", curado=True)),
        _con_newsletter(_newsletter("Beto", curado=False)),
    ]
    semanal = armar_newsletter(activos, periodo="2026-47", solo_curados=False)
    assert len(semanal.destaques) == 2


def test_arma_newsletter_vacio_sin_destaques() -> None:
    semanal = armar_newsletter([_con_newsletter(None)], periodo="2026-47")
    assert semanal.destaques == []


def test_arma_newsletter_conserva_el_orden() -> None:
    activos = [
        _con_newsletter(_newsletter("Tercero", curado=True)),
        _con_newsletter(_newsletter("Primero", curado=True)),
        _con_newsletter(_newsletter("Segundo", curado=True)),
    ]
    semanal = armar_newsletter(activos, periodo="2026-47")
    assert [d.titular for d in semanal.destaques] == ["Tercero", "Primero", "Segundo"]
