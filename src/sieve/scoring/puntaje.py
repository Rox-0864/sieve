"""
El score, y por que se puede explicar.

La promesa del proyecto es que se pueda señalar un mensaje y decir "entro
con 0.82 porque es una contratacion y el tema Contratacion aparece 3
veces en este lote". Un numero solo no cumple esa promesa: obliga a
confiar. `Puntaje` lleva el desglose, los pesos QUE SE APLICARON, y que
terminos no hubo.

La renormalizacion (opcion A) es la decision mas importante del archivo:
un termino que no se puede calcular NO vale cero, no esta. Si valiera
cero, un lote sin timestamps (que es el caso de los datos de muestra, y
de medio mundo real) perderia el 20% de su peso sin avisar, y el score
seguiria pareciendo un score normal. Renormalizar y DECLARAR que se
renormalizo hace que la comparacion entre lotes con datos distintos sea
visible en vez de silenciosamente incorrecta.

El precio de A, dicho con todas las letras: dos lotes con datos
diferentes dan scores con reglas distintas y NO son comparables entre si.
Un lote con fechas y otro sin las tienen no comparten escala. Por eso
`terminos_disponibles` es un campo de primer nivel y no un detalle
interno: quien compare dos puntajes tiene que ver la diferencia antes de
comparar los numeros.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, computed_field

from sieve.analysis.base import ResumenAnalisis
from sieve.models import Analisis, Interaccion
from sieve.scoring.terminos import TERMINOS, ContextoLote, Termino


class TerminoPuntaje(BaseModel):
    """Un termino ya pesado: cuanto valia, cuanto pesaba, cuanto aporto."""

    nombre: str
    #: Valor crudo en 0..1.
    valor: float = Field(..., ge=0.0, le=1.0)
    #: Peso base, tal cual lo define la formula (0.30, 0.25, ...).
    peso_base: float
    #: Peso REALMENTE aplicado, ya renormalizado. Difiere de `peso_base`
    #: cuando faltaron terminos. Ver `motivo_pesos`.
    peso_aplicado: float
    #: valor * peso_aplicado.
    contribucion: float

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ausente(self) -> bool:
        return self.peso_aplicado == 0.0


class Puntaje(BaseModel):
    """El score de un mensaje, con el desglose que lo justifica."""

    mensaje_id: str | None = None
    score: float = Field(..., ge=0.0, le=1.0)
    terminos: list[TerminoPuntaje]
    #: Nombres de los terminos que NO se pudieron calcular. Vacio = el
    #: score se calculo con la formula completa.
    terminos_ausentes: list[str] = Field(default_factory=list)
    #: Suma de los pesos base de los terminos que si se calcularon. 1.0
    #: = la formula entera; menos = se renormalizo.
    peso_total_aplicado: float = Field(..., gt=0.0, le=1.0)

    @property
    def mayor_aportacion(self) -> TerminoPuntaje:
        """El termino que mas movio la aguja. El "porque" de una linea."""
        return max(self.terminos, key=lambda t: t.contribucion)

    @property
    def renormalizado(self) -> bool:
        return self.peso_total_aplicado < 1.0 - 1e-9

    def explicacion(self) -> str:
        """Una linea para logs, para la CLI y para un dueño de producto.

        Dice el score, el termino que mas aporto, y si falta algo. La
        ultima parte no es opcional: un 0.71 calculado con cuatro
        terminos no es lo mismo que un 0.71 con cinco, y si el texto no
        lo dice, alguien los va a comparar.
        """
        tope = self.mayor_aportacion
        base = (
            f"{self.score:.2f} por {self.mayor_aportacion.nombre} "
            f"({tope.valor:.2f} x peso {tope.peso_aplicado:.2f})"
        )
        if self.renormalizado:
            base += (
                f" [sin {', '.join(self.terminos_ausentes)}: pesos "
                f"renormalizados a {self.peso_total_aplicado:.2f}]"
            )
        return base

    def pesos_por_nombre(self) -> dict[str, TerminoPuntaje]:
        return {t.nombre: t for t in self.terminos}


def _valor(term: Termino, inter: Interaccion, analisis: Analisis, ctx: ContextoLote) -> float:
    """El valor, acotado a 0..1.

    El `min`/`max` no es desconfianza del termino: es una red contra los
    errores de float. Un termino que devuelve 1.0000000000000002 por una
    operacion de decaimiento no puede hacer fallar la validacion de
    Pydantic de todo el lote por una razon que no le importa a nadie.
    """
    return max(0.0, min(1.0, term.valor(inter, analisis, ctx)))


def puntuar(
    interaccion: Interaccion,
    analisis: Analisis,
    ctx: ContextoLote,
    terminos: tuple[Termino, ...] = TERMINOS,
) -> Puntaje:
    """Puntua UN mensaje. `ctx` es del lote entero; ver `ContextoLote`."""
    disponibles = [t for t in terminos if t.disponible(ctx)]
    ausentes = [t.nombre for t in terminos if not t.disponible(ctx)]

    peso_total = sum(t.peso_base for t in disponibles)
    if peso_total <= 0.0:
        # Todos los terminos caidos. No hay score, no hay renormalizacion
        # que lo arregle. Se elige 0.0 explicito para que el error sea
        # visible en el desglose y no un NaN silencioso.
        return Puntaje(
            mensaje_id=interaccion.mensaje_id,
            score=0.0,
            terminos=[],
            terminos_ausentes=ausentes,
            peso_total_aplicado=peso_total,
        )

    items: list[TerminoPuntaje] = []
    total = 0.0
    for term in disponibles:
        valor = _valor(term, interaccion, analisis, ctx)
        peso = term.peso_base / peso_total
        contribucion = valor * peso
        total += contribucion
        items.append(
            TerminoPuntaje(
                nombre=term.nombre,
                valor=valor,
                peso_base=term.peso_base,
                peso_aplicado=peso,
                contribucion=contribucion,
            )
        )
    return Puntaje(
        mensaje_id=interaccion.mensaje_id,
        score=max(0.0, min(1.0, total)),
        terminos=items,
        terminos_ausentes=ausentes,
        peso_total_aplicado=peso_total,
    )


def puntuar_lote(
    resumen: ResumenAnalisis,
    terminos: tuple[Termino, ...] = TERMINOS,
) -> list[Puntaje]:
    """Puntua todo el lote.

    Devuelve la lista en el MISMO orden que `resumen.resultados`, porque
    el emparejamiento por indice es lo que hace que esto sea auditable: si
    el score 0.82 aparece pegado al mensaje equivocado, se publica el
    testimonio de una persona sobre el mensaje de otra. El orden se
    hereda, no se reordena.
    """
    pares: list[tuple[Interaccion, Analisis]] = [
        (r.interaccion, r.analisis) for r in resumen.resultados
    ]
    ctx = ContextoLote.desde(pares)
    return [puntuar(inter, analisis, ctx, terminos) for inter, analisis in pares]


def indexar_por_id(puntajes: list[Puntaje]) -> dict[str, Puntaje]:
    """Como `ResumenAnalisis.por_id`: omite los que no tienen id.

    Escribir "None" como clave de un dict es un bug esperando su momento:
    dos mensajes sin id se pisan en silencio y uno desaparece del ranking
    sin que nadie lo note.
    """
    return {p.mensaje_id: p for p in puntajes if p.mensaje_id is not None}


__all__ = [
    "Puntaje",
    "TerminoPuntaje",
    "puntuar",
    "puntuar_lote",
    "indexar_por_id",
]
