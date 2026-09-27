"""
Señales de calidad propias del origen.

Esta es la abstraccion que hace que Sieve sea evaluable.

Una interaccion es "lo que alguien dijo". Un `GroundTruth` es "lo que
otros humanosGBM de esa interaccion". Son cosas distintas, y
mezclarlas en un solo modelo seria un error de diseno:

  - El producto NUNCA consume el ground truth en produccion. Nadie
    publica un post de LinkedIn porque Reddit tenga 400 upvotes en el
    post original. El score lo decide Sieve.
  - El ground truth solo se usa en EVALUACION. Ahi responde la otra
    pregunta importante: no "mi score es bonito" sino "mi score
    rankea las cosas igual que lo hacen las personas".

Stack Exchange tiene un juicio humano explicito: `accepted_answer_id`
marca que la comunidad considero que esa pregunta estaba resuelta. Es
el ground truth mas honesto que existe para "este mensaje vale la pena".
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from sieve.models import Interaccion


@dataclass(frozen=True, slots=True)
class GroundTruth:
    """Lo que la plataforma de origen dice del valor de una interaccion.

    frozen=True a proposito: el ground truth no se modifica. Es la
    referencia contra la que se mide, y una referencia que se puede
    mutar no es una referencia.
    """

    score: int = 0
    """Upvotes netos. En Stack Overflow la senal de valor mas directa."""

    answer_count: int = 0
    """Cuantas respuestas recibio. Alto = la comunidad la considered relevante."""

    aceptado: bool = False
    """Hubo una respuesta aceptada: alguien la resolvio y lo confirmo.

    Esta es la senal mas fuerte y la mas escasa. En es.stackoverflow
    solo el 43% de las preguntas la tienen.
    """

    view_count: int = 0
    """Veces vista. Senal de popularidad, mas laxa que las otras."""

    tags: tuple[str, ...] = ()
    """Etiquetas del tema, ya des-escapadas."""

    url: str | None = None
    """Link a la interaccion original. OBLIGATORIO para atribucion CC BY-SA."""

    author_url: str | None = None
    """Link al perfil del autor. OBLIGATORIO para atribucion CC BY-SA."""

    content_license: str | None = None
    """Licencia declarada por el origen. Hay que citarla con su version."""

    source_id: str | None = None
    """Identificador estable en la plataforma de origen."""

    # ─── Señales derivadas ────────────────────────────────────────

    @property
    def tiene_judicio_humano(self) -> bool:
        """¿Hay alguna señal de valor que un humano haya dejado?"""
        return self.aceptado or self.score > 0

    def intensidad(self) -> float:
        """Normaliza las señales a 0..1 para poder promediarlas.

        Las tres señales se ponen en la misma escala antes de mezclarlas.
        Comparar 237 upvotes con 6 respuestas sin normalizar da un
        ranking que en realidad solo mide el numero de upvotes.

        La aceptacion NO es un peso mas: es un PISO. Un humano dijo
        "esta pregunta esta resuelta". Eso vale mas que cualquier
        cantidad de upvotes, asi que el valor minimo de un elemento
        aceptado tiene que superar el maximo posible de uno no aceptado
        (0.75 contra 0.70). Si se modelara como un peso mas, un post
        con 100 upvotes y sin aceptacion le ganaria a una respuesta
        aceptada, que es exactamente al reves de lo que la comunidad
        considera valioso.
        """
        upvotes = min(self.score / 50.0, 1.0)
        respuestas = min(self.answer_count / 5.0, 1.0)
        base = 0.45 * upvotes + 0.25 * respuestas  # techo: 0.70
        if self.aceptado:
            return round(0.75 + 0.25 * base, 4)
        return round(base, 4)


@dataclass(frozen=True, slots=True)
class InteraccionEtiquetada:
    """Una interaccion con su ground truth cuando existe.

    El envoltorio — y no un campo opcional dentro de `Interaccion` —
    porque el modelo del nucleo no debe saber nada de upvotes. El dia
    que se los meta adentro, la logica de negocio empieza a filtrar por
    ellos sin que nadie lo note.
    """

    interaccion: Interaccion
    ground_truth: GroundTruth | None = None
    extras: dict[str, str] = field(default_factory=dict)


def mensaje_id(interaccion: Interaccion) -> str:
    """Identificador estable y reproducible para una interaccion.

    El hash va del contenido, no de la posicion en la lista. Si el mismo
    mensaje aparece en dos lotes, tiene que ser el mismo id: eso es lo
    que permite seguir un activo generado de vuelta al mensaje del que
    salio, y sin eso el sistema no es auditable.

    Hex de 12 caracteres: suficiente para miles de mensajes por lote y
    legible en logs.
    """
    base = f"{interaccion.autor}|{interaccion.canal}|{interaccion.texto}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:12]
