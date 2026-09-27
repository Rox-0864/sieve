"""
El router: que activo se genera a partir de un mensaje.

Tres ramas, y el orden importa. Un mensaje puede cumplir dos condiciones a
la vez (un hito que ademas es pregunta), y el orden define cual gana. La
primera que gana es `caso_exito`: un logro es la senal mas cara de
convertir en post, y mandarlo a FAQ tira el mejor material del lote.

La tabla del README tiene un hueco y hay que decirlo: los umbrales cubren
`score >= 0.65`, `pregunta y score >= 0.35` y `score < 0.35`, pero
dejan sin definir el caso "score entre 0.35 y 0.65, sin senal de logro y
sin pregunta". Aca ese caso cae a `highlight`.

La eleccion no es arbitraria y tiene una razon: `highlight` es el activo
de menor riesgo. Un resumen semanal de la comunidad publica conversacion
real; un post de LinkedIn publica UN testimonio con el nombre de alguien.
Cuando la especificacion no dice que hacer, el destino por defecto tiene
que ser el que menos puede hacer dano. Publicar de mas es un problema de
reputacion; publicar de menos es un problema de metricas.
"""

from __future__ import annotations

from pydantic import BaseModel

from sieve.analysis.base import ResultadoAnalisis
from sieve.models import TipoActivo
from sieve.scoring.puntaje import Puntaje

#: Umbral de `caso_exito`. Sale del README.
UMBRAL_CASO_EXITO = 0.65
#: Umbral de `faq`.
UMBRAL_FAQ = 0.35


class DecisionRuta(BaseModel):
    """A donde va el mensaje, y POR QUE.

    `motivo` no es decorativo. Un router que no explica su decision es
    un `if` con pasos, y la pregunta "por que este mensaje no salio"
    no tiene respuesta. Con el motivo, la pregunta se contesta sola
    mirando el score y el termino que mas aporto.
    """

    tipo: TipoActivo
    score: float
    motivo: str
    #: La regla que dispara, textual. Para greppear.
    regla: str
    #: Si el score se renormalizo por falta de datos.
    renormalizado: bool = False

    @property
    def es_revision_manual(self) -> bool:
        """Si este activo necesita un humano antes de publicarse.

        Un `caso_exito` publica el testimonio de una persona con su
        nombre. Eso no sale de una maquina sin que alguien lo firme.
        """
        return self.tipo in (TipoActivo.CASO_EXITO, TipoActivo.FAQ)


def enrutar(resultado: ResultadoAnalisis, puntaje: Puntaje) -> DecisionRuta:
    """Decide el activo de un mensaje. Nunca levanta."""
    score = puntaje.score
    es_pregunta = resultado.analisis.es_pregunta
    es_logro = resultado.analisis.es_logro
    if score >= UMBRAL_CASO_EXITO and es_logro:
        return DecisionRuta(
            tipo=TipoActivo.CASO_EXITO,
            score=score,
            renormalizado=puntaje.renormalizado,
            motivo=(
                f"score {score:.2f} >= {UMBRAL_CASO_EXITO} y el analisis marca "
                f"logro; aporta mas {puntaje.mayor_aportacion.nombre}"
            ),
            regla="score>=0.65 y es_logro",
        )

    if es_pregunta and score >= UMBRAL_FAQ:
        return DecisionRuta(
            tipo=TipoActivo.FAQ,
            score=score,
            renormalizado=puntaje.renormalizado,
            motivo=(
                f"es pregunta con score {score:.2f} >= {UMBRAL_FAQ}; "
                f"aporta mas {puntaje.mayor_aportacion.nombre}"
            ),
            regla="es_pregunta y score>=0.35",
        )

    if score < UMBRAL_FAQ:
        return DecisionRuta(
            tipo=TipoActivo.HIGHLIGHT,
            score=score,
            renormalizado=puntaje.renormalizado,
            motivo=f"score {score:.2f} < {UMBRAL_FAQ}: no llega al umbral de FAQ",
            regla="score<0.35",
        )

    # El hueco de la tabla. Ver la nota del modulo.
    return DecisionRuta(
        tipo=TipoActivo.HIGHLIGHT,
        score=score,
        renormalizado=puntaje.renormalizado,
        motivo=(
            f"score {score:.2f} entre {UMBRAL_FAQ} y {UMBRAL_CASO_EXITO} sin "
            "senal de logro: la tabla no lo define y highlight es el de menor riesgo"
        ),
        regla="hueco de la tabla -> highlight",
    )


def enrutar_lote(
    resultados: list[ResultadoAnalisis],
    puntajes: list[Puntaje],
) -> list[DecisionRuta]:
    """Enruta el lote entero.

    `puntajes` tiene que venir en el mismo orden que `resultados` (lo
    devuelve `puntuar_lote` asi). Si no, el chequeo falla con un mensaje
    explicito en vez de emparejar el score de una persona con el mensaje
    de otra, que es el peor bug posible en este sistema.
    """
    if len(resultados) != len(puntajes):
        raise ValueError(
            f"{len(resultados)} resultados y {len(puntajes)} puntajes: no "
            "emparejan. `puntuar_lote` devuelve el mismo orden que el resumen; "
            "reordenar uno de los dos rompe el emparejamiento."
        )
    return [enrutar(r, p) for r, p in zip(resultados, puntajes, strict=True)]


def agrupar_por_tipo(decisiones: list[DecisionRuta]) -> dict[TipoActivo, list[DecisionRuta]]:
    """Para el generador de activos de M4.

    Un lote donde nadie logro un hito no tiene `caso_exito`, y esa
    ausencia tiene que ser visible: si el generador inventa un post para
    llenar el hueco, el router decidio bien y el generador lo arruino.
    """
    grupos: dict[TipoActivo, list[DecisionRuta]] = {t: [] for t in TipoActivo}
    for d in decisiones:
        grupos[d.tipo].append(d)
    return grupos
