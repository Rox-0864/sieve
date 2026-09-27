"""
Los cinco terminos de la formula de score, uno por clase.

Por que una clase por termino y no cinco funciones en un archivo: cada
termino tiene su propia regla de DISPONIBILIDAD. Un termino que no se
puede calcular no es un termino con valor cero, es un termino ausente, y
el score tiene que renormalizar los pesos sin el. Si eso fuera un `if`
escondido adentro de una funcion, el que renormaliza y el que puntua
terminan en archivos distintos y la mitad de los scores queda mal.

Cada valor devuelto esta en 0..1. El score es un promedio ponderado de
cosas comparables; si un termino devuelve 0.95 y otro devuelve -0.8, la
formula deja de significar nada.

OJO con la normalizacion: "sentimiento" de -1..1 se mapea a 0..1 con
(v + 1) / 2. Un mensaje neutro vale 0.5, no 0. Un mensaje neutro NO es
"no relevante": es neutro. Si se tratara el 0 como cero, el pipeline
descartaria exactamente los mensajes que no son extremos, que suelen ser
los que mejor describen una situacion.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from sieve.models import Analisis, Interaccion

#: Horas en que un mensaje pierde la mitad de su valor por antiguedad.
#: Tres dias: es la unidad de un resumen semanal de comunidad, que es lo
#: que el router produce cuando nada destaca. Es un numero que se puede
#: discutir, que es el punto. Un 0.3 sin nombre no se puede discutir.
VIDA_MEDIA_RECENCIA_HORAS = 72.0

#: Caracteres a los que `senal_interaccion` da la nota maxima de longitud.
#: 400 es un parrafo largo. Un mensaje de 1000 caracteres no es "el doble
#: de interesante", asi que la longitud se satura en vez de crecer lineal.
LARGO_SATURADO = 400.0


@dataclass(frozen=True)
class ContextoLote:
    """Lo que se calcula UNA vez por lote y todos los terminos comparten.

    Vive aca y no se recalcula por mensaje porque es O(n) por lote: la
    frecuencia de temas y el timestamp mas reciente son del lote entero.
    Pasarlos por parametro hace explicito que el score de un mensaje
    DEPENDE de sus companeros, que es la parte incomoda del scoring
    batch y la que se esconde cuando cada mensaje se puntua solo.
    """

    #: tema -> cuantas veces aparece en el lote.
    frecuencia_temas: dict[str, int] = field(default_factory=dict)
    #: El tema mas frecuente. 0 = ningun mensaje tiene temas.
    tema_maximo: int = 0
    #: El mensaje mas reciente del lote.
    mas_reciente: datetime | None = None
    #: Si TODOS los mensajes tienen fecha. Ver `TerminoRecencia`.

    @property
    def hay_temas(self) -> bool:
        return self.tema_maximo > 0

    @classmethod
    def desde(cls, pares: list[tuple[Interaccion, Analisis]]) -> ContextoLote:
        """Arma el contexto a partir de los pares ya emparejados."""
        frecuencia: dict[str, int] = {}
        for _, analisis in pares:
            for tema in analisis.temas:
                clave = tema.strip().lower()
                if clave:
                    frecuencia[clave] = frecuencia.get(clave, 0) + 1

        # `todos_con_fecha` o nada: ver `TerminoRecencia.disponible`.
        fechas = [inter.timestamp for inter, _ in pares if inter.timestamp is not None]
        mas_reciente = (
            max(fechas) if len(fechas) == len(pares) and fechas else None
        )
        return cls(
            frecuencia_temas=frecuencia,
            tema_maximo=max(frecuencia.values(), default=0),
            mas_reciente=mas_reciente,
        )


class Termino(ABC):
    """Un termino de la formula: nombre, peso base, y como se calcula.

    `disponible` y `valor` estan separados a proposito. La disponibilidad
    se decide UNA vez por lote (es una propiedad del lote) y el valor una
    vez por mensaje. Si fueran una sola cosa, la renormalizacion de pesos
    tendria que adivinar si un valor de 0.0 significa "malo" o "no
    applicable".
    """

    nombre: str
    peso_base: float

    @abstractmethod
    def disponible(self, ctx: ContextoLote) -> bool:
        """Si este termino se puede calcular para este lote.

        False significa que el peso se redistribuye, NO que valga 0.
        """

    @abstractmethod
    def valor(self, inter: Interaccion, analisis: Analisis, ctx: ContextoLote) -> float:
        """El valor crudo, en 0..1."""


class TerminoSentimiento(Termino):
    """El tono del mensaje. 30% del score: es la senal mas fuerte."""

    nombre = "sentimiento"
    peso_base = 0.30

    def disponible(self, ctx: ContextoLote) -> bool:
        # `score_sentimiento` es obligatorio en Analisis, asi que siempre
        # se puede calcular.
        return True

    def valor(self, inter: Interaccion, analisis: Analisis, ctx: ContextoLote) -> float:
        # -1..1 -> 0..1. Neutro (0) queda en 0.5: un mensaje que no esta
        # ni bien ni mal no vale cero, vale la mitad.
        return (analisis.score_sentimiento + 1.0) / 2.0


class TerminoSalienciaTema(Termino):
    """Cuanto del lote habla de lo mismo. 25%.

    No es "el tema es interesante": es "este mensaje participa de una
    conversacion que se repite". Un tema que aparece en 5 de 7 mensajes
    tiene mas contexto para escribir un post que uno que aparece una vez,
    y eso vale aunque el tema sea aburrido.
    """

    nombre = "saliencia_tema"
    peso_base = 0.25

    def disponible(self, ctx: ContextoLote) -> bool:
        # Si NINGUN mensaje del lote tiene temas, la saliencia no se
        # puede medir: no es que valga 0, es que no hay nada que medir.
        # Cuando el LLM falla y todo cae a heuristica, es lo que pasa.
        return ctx.hay_temas

    def valor(self, inter: Interaccion, analisis: Analisis, ctx: ContextoLote) -> float:
        if not analisis.temas:
            # El mensaje no participa de ningun tema detectado. Vale 0, y
            # es informacion: el modelo dijo "esto no es un tema".
            return 0.0
        claves = [t.strip().lower() for t in analisis.temas if t.strip()]
        if not claves:
            return 0.0
        total = sum(ctx.frecuencia_temas.get(c, 0) / ctx.tema_maximo for c in claves)
        return float(total / len(claves))


class TerminoRecencia(Termino):
    """Antiguedad con decaimiento exponencial. 20%.

    Se mide RELATIVA al mensaje mas reciente del lote, no al reloj del
    sistema. Importa por una razon concreta: si se midiera contra `now()`,
    re-correr el mismo lote manana daria scores distintos y nadie podria
    comparar dos corridas del mismo dato. Contra el lote, la corrida es
    reproducible.
    """

    nombre = "recencia"
    peso_base = 0.20

    def disponible(self, ctx: ContextoLote) -> bool:
        # `ContextoLote` deja `mas_reciente` en None salvo que TODOS los
        # mensajes tengan fecha. Es todo o nada a proposito: si 6 de 7
        # tienen fecha, dar recencia a uno y no a otro hace que los dos
        # scores se calculen con reglas distintas y la comparacion entre
        # ellos deja de significar nada. Mejor que falte para todos.
        return ctx.mas_reciente is not None

    def valor(self, inter: Interaccion, analisis: Analisis, ctx: ContextoLote) -> float:
        if ctx.mas_reciente is None or inter.timestamp is None:
            return 0.0
        horas = (ctx.mas_reciente - inter.timestamp).total_seconds() / 3600.0
        # El mas reciente del lote vale 1.0 por definicion, sin importar
        # cuanto lleva de existir.
        #
        # `exp(-ln2 * t)` y no `0.5 ** t`: son el mismo numero, pero
        # `float.__pow__` puede devolver `complex` para exponentes
        # negativos, y mypy lo tipa como `Any`. `exp` es ademas
        # monotono y no tiene ese caso raro.
        return math.exp(-math.log(2.0) * max(0.0, horas) / VIDA_MEDIA_RECENCIA_HORAS)


class TerminoLogro(Termino):
    """Senal de logro explicita. 15%.

    Binaria a proposito. Un "logro" no es un espectro: o alguien conto
    que logro algo, o no lo conto. Un 0.7 de logro es un logro mal
    expresado, y el LLM ya tiene `es_logro` para decir que si o que no.
    """

    nombre = "logro"
    peso_base = 0.15

    def disponible(self, ctx: ContextoLote) -> bool:
        return True

    def valor(self, inter: Interaccion, analisis: Analisis, ctx: ContextoLote) -> float:
        return 1.0 if analisis.es_logro else 0.0


class TerminoInteraccion(Termino):
    """Largo y pregunta. 10%.

    NO incluye reacciones: el dato no existe en `Interaccion`. Cuando se
    sume al contrato, entra aca con su propio peso y no se reescribe el
    resto de la formula.

    Mitad y mitad, y no 0.7/0.3, porque no hay dato que diga que una cosa
    importe mas que la otra. Un reparto inventado seria una preferencia
    disfrazada de medicion.
    """

    nombre = "interaccion"
    peso_base = 0.10

    def disponible(self, ctx: ContextoLote) -> bool:
        return True

    def valor(self, inter: Interaccion, analisis: Analisis, ctx: ContextoLote) -> float:
        largo = min(1.0, len(inter.texto) / LARGO_SATURADO)
        pregunta = 1.0 if analisis.es_pregunta else 0.0
        return 0.5 * largo + 0.5 * pregunta


#: Los cinco, en el orden en que se muestran en el desglose.
TERMINOS: tuple[Termino, ...] = (
    TerminoSentimiento(),
    TerminoSalienciaTema(),
    TerminoRecencia(),
    TerminoLogro(),
    TerminoInteraccion(),
)


# ═══════════════════════════════════════════════════════════════════
#  NOTA SOBRE REPRODUCIBILIDAD
# ═══════════════════════════════════════════════════════════════════
#
# El score de este modulo es DETERMINISTA: dados los mismos `Analisis`,
# el mismo score, siempre. Eso esta verificado en los tests.
#
# Lo que NO es determinista es el LLM de M2, y no por el sampling: con
# temperatura 0.0 y `seed` fijo, dos llamadas identicas a
# `qwen2.5:3b` en CPU devuelven texto DISTINTO. Es aritmetica de punto
# flotante no asociativa en las multiplicaciones por matriz: el orden de
# las reducciones depende del scheduling de hilos, y por eso ni el seed
# ni la temperatura alcanza.
#
# Medido sobre los 7 mensajes de `data/samples/lote_demo.json`, dos
# corridas del mismo modelo: 4 de 7 voltean `es_logro` y los 7 cambian
# `relevant`. Como el router exige `es_logro`, la cantidad de
# `caso_exito` cambia entre corridas (3 contra 1).
#
# La respuesta NO es intentar hacer determinista el LLM: es no
# depender de el en el momento de la decision. M2 persiste su analisis y
# M3 lo LEE. Un post publicado traza hasta el `Analisis` exacto que lo
# produjo, y re-correr M2 es una accion explicita y separada, no algo
# que pase por abajo cuando alguien regenera un score.
#
# Si alguna vez hace falta reproduccion exacta, lavia no es ajustar el
# sampling: es correr el modelo en GPU o en un backend determinista.
