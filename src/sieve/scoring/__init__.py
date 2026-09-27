"""
Puntuacion explicable y ruteo condicional (M3).

Publica lo minimo para que el resto del proyecto importe por nombre y no
conozca la implementacion:

    from sieve.scoring import puntuar_lote, enrutar_lote, agrupar_por_tipo

La disciplina del paquete: `puntaje.py` NO sabe que existe un router, y
`router.py` NO sabe como se calcula un score. Se conocen por el tipo
`Puntaje`. Si el router empieza a leer `puntaje.terminos[0].valor` para
decidir algo, la explicabilidad se mezclo con la decision y las dos
cosas dejan de poder cambiarse por separado.
"""

from __future__ import annotations

from sieve.scoring.puntaje import (
    Puntaje,
    TerminoPuntaje,
    indexar_por_id,
    puntuar,
    puntuar_lote,
)
from sieve.scoring.router import (
    UMBRAL_CASO_EXITO,
    UMBRAL_FAQ,
    DecisionRuta,
    agrupar_por_tipo,
    enrutar,
    enrutar_lote,
)
from sieve.scoring.terminos import (
    TERMINOS,
    VIDA_MEDIA_RECENCIA_HORAS,
    ContextoLote,
    Termino,
)

__all__ = [
    "ContextoLote",
    "DecisionRuta",
    "Puntaje",
    "TERMINOS",
    "Termino",
    "TerminoPuntaje",
    "UMBRAL_CASO_EXITO",
    "UMBRAL_FAQ",
    "VIDA_MEDIA_RECENCIA_HORAS",
    "agrupar_por_tipo",
    "enrutar",
    "enrutar_lote",
    "indexar_por_id",
    "puntuar",
    "puntuar_lote",
]
