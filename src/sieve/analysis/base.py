"""
Las fronteras de la capa de analisis.

Este modulo NO hace analisis. Define QUE es un proveedor de LLM, que es
un resultado, y donde un analisis puede fallar. Todo lo demas depende de
estas tres cosas y de nada mas.

La decision de fondo esta en `ResultadoAnalisis` y en `Procedencia`.

Un LLM no es una funcion pura. Es un servicio de otro que puede estar
caido, puede agotar cuota, puede devolver JSON con el fence de markdown
alrededor, y puede inventarse una cita que nunca dijo nadie. Si el
pipeline lo trata como si fuera deterministico, cada una de esas
fallas se convierte en un activo publicado con la cara del autor.

Por eso cada analisis viaja con:

  - `procedencia`: de donde salio. Sin esto no se puede comparar el LLM
    contra la heuristica, ni confiar en el resultado, ni entender por que
    algo se clasifico mal. Es el mismo principio que en M1: el ground
    truth vive fuera del producto.
  - `citas_descartadas`: las citas que el modelo dijo y no estaban en el
    texto. No se tiran en silencio: se cuentan. Un modelo que inventa
    citas el 30% de las veces es un dato que hay que poder ver.
  - `tokens` y `latencia_ms`: para saber cuanto cuesta y cuanto tarda
    antes de que se convierta en un problema.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from sieve.models import Analisis, ConteoProcedencia, Interaccion

#: De donde salio un analisis. `mixto` existe porque el fallback es POR
#: ITEM: un lote de 20 mensajes puede tener 19 del LLM y 1 heuristico.
#: Un unico booleano "usamos_llm" mentiria sobre ese lote.
Procedencia = Literal["llm", "heuristico", "mixto"]

#: Por que un item cayo a la heuristica. Se registra porque "cayo" y
#: "el LLM no estaba configurado" son problemas muy distintos.
MotivoFallback = Literal[
    "sin_proveedor",
    "error_de_red",
    "respuesta_ilegible",
    "json_invalido",
    "timeout",
    "sin_respuesta",
]


class ErrorDeAnalisisError(RuntimeError):
    """Fallo del LLM. El pipeline lo captura; nunca lo propaga al producto.

    Si esta excepcion llegara a la etapa de publicacion, un rate limit
    de OpenAI se convertiria en un error 500 y el lote entero se
    perderia. Perder 20 analisis recuperables no es aceptable.
    """


class ErrorDeRedError(ErrorDeAnalisisError):
    """El proveedor no respondio: caido, timeout, DNS, 500."""


class RespuestaIlegibleError(ErrorDeAnalisisError):
    """El proveedor respondio, pero no con lo que pedimos.

    Es el caso mas comun y el mas subestimado: el servidor esta
    perfecto, la APIKey es valida, y la respuesta es texto que no es
    JSON.Casi siempre por un fence de markdown o por prosa alrededor.
    """


@dataclass(frozen=True, slots=True)
class ResultadoLLM:
    """Lo que un proveedor devuelve, con lo que el prompt no controla.

    Existe para que el pipeline pueda medir. El texto de la respuesta es
    lo que el modelo decidio; los tokens y la latencia son los hechos que
    el modelo no elige y que hay que poder reportar igual. Si `completar`
    devolviera un `str` pelado, esos numeros se pierden en el `str` y el
    diagnostico de por que una ventana cayo a heuristica es
    indescifrable.
    """

    texto: str
    modelo: str
    tokens_entrada: int = 0
    tokens_salida: int = 0
    latencia_ms: int = 0

    @property
    def tokens_totales(self) -> int:
        return self.tokens_entrada + self.tokens_salida


@runtime_checkable
class ProveedorLLM(Protocol):
    """El unico contrato que el pipeline necesita de un LLM.

    Un metodo. Deliberadamente minimo: pedir `completar(prompt)` en vez de
    una API de chat completa significa que agregar un proveedor es
    escribir un metodo, no aprender una API nueva. Y hace que probar un
    proveedor sea escribir 5 lineas.

    `es_disponible()` NO debe hacer red. Debe responder "estoy
    configurado", no "el mundo funciona". Un chequeo de disponibilidad
    que pega a la red en un bucle sobre N items multiplica el tiempo de
    arranque por N.
    """

    nombre: str

    def es_disponible(self) -> bool:
        """True si esta configurado. NO debe hacer I/O de red."""
        ...

    def completar(self, prompt: str, *, max_tokens: int = 800) -> ResultadoLLM:
        """Devuelve el texto crudo envuelto en su instrumentacion.

        El parseo NO es responsabilidad del proveedor: vive en un solo
        lugar (`extract.py`) y asi se puede testear el parsing sin
        ningun proveedor de por medio. Por eso esto no devuelve un dict
        con "content" y "usage" como haria una API cruda, sino un tipo
        propio con los campos ya renombrados: el nombre `latencia_ms` no
        lo eligio el vendor y no queremos que se filtre al pipeline.
        """
        ...


@dataclass(frozen=True, slots=True)
class ResultadoAnalisis:
    """Un analisis + de donde vino + que le saga a la confianza.

    Envuelve a `Analisis` en vez de agregar campos ahi a proposito:
    `Analisis` es el contrato del documento ONE y tiene que seguir
    siendo serializable contra el PDF. La procedencia, los tokens y la
    latencia son instrumentacion del pipeline, no semantica del
    documento. Meterlos adentro seria ensuciar el contrato con cosas que
    el documento no define.
    """

    interaccion: Interaccion
    analisis: Analisis
    procedencia: Procedencia = "heuristico"
    modelo: str | None = None
    motivo_fallback: MotivoFallback | None = None
    tokens: int = 0
    latencia_ms: int = 0
    #: Citas que el LLM atribuyo al mensaje y no existen en el texto.
    citas_descartadas: tuple[str, ...] = ()

    @property
    def mensaje_id(self) -> str | None:
        return self.interaccion.mensaje_id

    @property
    def trustworthy(self) -> bool:
        """Si un humano tiene que mirar esto antes de publicarlo.

        Un analisis heuristico no es falso: es menos informado. Y las
        citas inventadas son una bandera roja. Este atributo es lo que
        despues va a poner `curado=True` en los activos y a filtrar la
        cola de revision de M6.
        """
        return self.procedencia == "llm" and not self.citas_descartadas

    def resumen(self) -> str:
        """Una linea para logs y para la CLI."""
        base = (
            f"{self.interaccion.mensaje_id or '?'} "
            f"{self.analisis.sentimiento.value:>14} "
            f"rel={self.analisis.relevant:.2f} "
            f"[{self.procedencia}"
        )
        if self.modelo:
            base += f"/{self.modelo}"
        if self.citas_descartadas:
            base += f" citas_descartadas={len(self.citas_descartadas)}"
        if self.motivo_fallback:
            base += f" motivo={self.motivo_fallback}"
        return base + "]"


@dataclass(slots=True)
class ResumenAnalisis:
    """El resultado de procesar un lote entero.

    Existe para que el pipeline pueda reportar honestamente. Un lote de
    100 donde 40 cayeron a la heuristica NO es un lote de 100 analisis
    con LLM: es algo distinto, y si el reporte no lo dice, el demo miente
    sobre lo que la demo hace.
    """

    resultados: list[ResultadoAnalisis] = field(default_factory=list)

    def agregar(self, resultado: ResultadoAnalisis) -> None:
        self.resultados.append(resultado)

    @property
    def total(self) -> int:
        return len(self.resultados)

    def por_procedencia(self) -> dict[str, int]:
        conteo: dict[str, int] = {"llm": 0, "heuristico": 0, "mixto": 0}
        for r in self.resultados:
            conteo[r.procedencia] += 1
        return conteo

    def conteo_procedencia(self) -> ConteoProcedencia:
        """El conteo que se persiste en `LoteComunidad.procedencia`.

        Vive aca y no en la CLI para que haya UN solo lugar que sabe
        contar procedencia. Si lo calcularan dos lugares, un dia uno cuenta
        `mixto` como `llm` y el JSON dice una cosa mientras el log de
        terminal dice otra. Para un campo que n8n usa para decidir si
        publica, dos verdades es peor que ninguna.

        `con_citas_descartadas` cuenta ANALISIS con al menos una cita
        inventada, no la cantidad de citas. `citas_descartadas` cuenta
        citas. Son numeros distintos y confundirlos cambia la respuesta
        a la pregunta que se le hace al campo: "cuantos de estos 7
        tengo que mirar antes de publicar".
        """
        conteo = self.por_procedencia()
        # Si `Procedencia` gana un valor y `ConteoProcedencia` no, el
        # conteo de abajo lo ignora en silencio y el lote queda con una
        # suma que no da. Explota aqui, en desarrollo, no en produccion.
        desconocidas = set(conteo) - set(ConteoProcedencia.model_fields)
        if desconocidas:
            raise ValueError(
                f"procedencias sin campo en ConteoProcedencia: {sorted(desconocidas)}. "
                "Agrega el campo o el lote se persiste con una suma que no da."
            )
        return ConteoProcedencia(
            llm=conteo["llm"],
            heuristico=conteo["heuristico"],
            mixto=conteo["mixto"],
            con_citas_descartadas=sum(
                1 for r in self.resultados if r.citas_descartadas
            ),
        )

    @property
    def tokens_totales(self) -> int:
        return sum(r.tokens for r in self.resultados)

    @property
    def citas_descartadas(self) -> int:
        return sum(len(r.citas_descartadas) for r in self.resultados)

    def por_id(self) -> dict[str, ResultadoAnalisis]:
        """Indexa por `mensaje_id`, OMITIENDO los que no lo tienen.

        `mensaje_id` es `str | None` porque el contrato lo permite, pero
        `dict[str, ...]` no. La clave de un `None` seria escribir
        "None" como id de un mensaje, y dos mensajes sin id pisarian la
        misma entrada en silencio. Perder el indexado de un item sin id
        es preferible a inventarle una clave.
        """
        return {
            r.interaccion.mensaje_id: r
            for r in self.resultados
            if r.interaccion.mensaje_id is not None
        }

    def a_analisis(self) -> list[Analisis]:
        """Solo los `Analisis` puros, para el contrato y para el scoring."""
        return [r.analisis for r in self.resultados]


def lote_a_texto(interacciones: Sequence[Interaccion]) -> str:
    """Serializa un lote al formato que ve el modelo.

    El formato tiene que ser inequivoco: si un mensaje contiene algo
    que parece un delimitador, el modelo lo va a contar como otro
    mensaje y el resultado se KeyError en el mapeo. Por eso el
    delimitador incluye el indice, que un LLM no va a inventar.
    """
    bloques = []
    for i, inter in enumerate(interacciones, start=1):
        bloques.append(
            f"### MENSAJE {i}\n"
            f"autor: {inter.autor}\n"
            f"canal: {inter.canal}\n"
            f"tipo: {inter.tipo.value}\n"
            f"texto: {inter.texto}"
        )
    return "\n\n".join(bloques)
