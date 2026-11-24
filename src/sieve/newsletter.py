"""
El resumen semanal (newsletter): la agregacion que junta los destaques.

Un `caso_exito` produce un post Y su bloque de newsletter ("Logro de la
Semana"); un `highlight` produce solo un destaque. El resumen semanal es
la AGREGACION de esos bloques en un solo documento — lo que el README
llama "resumen semanal de la comunidad".

La regla de oro es la misma que en el resto del pipeline: solo entra lo
curado. Un destaque sin firmar no puede salir en el resumen, y un lote
sin logros ni highlights da un newsletter vacio — que es correcto: no se
inventa contenido para llenar la semana.
"""

from __future__ import annotations

from pydantic import BaseModel

from sieve.models import ActivosGenerados, Newsletter


class NewsletterSemanal(BaseModel):
    """El resumen semanal: un periodo y sus destaques, en orden."""

    periodo: str
    destaques: list[Newsletter]


def armar_newsletter(
    activos: list[ActivosGenerados], *, periodo: str, solo_curados: bool = True
) -> NewsletterSemanal:
    """Arma el resumen semanal desde los activos generados.

    Junta los destaques de newsletter de los `ActivosGenerados` en un
    solo documento. Por defecto `solo_curados=True`: un destaque sin
    firmar no sale en el resumen. El orden es el de la lista que recibe,
    que es el orden del lote — no se reordena, porque reordenar los
    destaques de una semana es una decision editorial, no tecnica.
    """
    destaques = [
        a.destaque_newsletter_semanal
        for a in activos
        if a.destaque_newsletter_semanal is not None
        and (not solo_curados or a.destaque_newsletter_semanal.curado)
    ]
    return NewsletterSemanal(periodo=periodo, destaques=destaques)
