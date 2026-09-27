"""
El analizador hibrido. Donde se decide de donde salio cada analisis.

Este es el modulo que hace que el proyecto funcione sin credenciales y
sin que eso sea una limitacion: no es "LLM con un plan B", es una
escalera con peldanos que se registran.

La decision que define todo lo demas: **el fallback es POR ITEM, no por
lote.**

La version ingenua manda 20 mensajes al LLM, y si algo sale mal cae
todo el lote a la heuristica. Es tentador porque es simple, y es
incorrecto por una razon concreta: un LLM devolviendose mal no invalida
los 19 mensajes que si proceso bien. Tirar 19 analisis correctos porque
el twentieth vino con una coma de mas es la forma rapida de que nadie
confie en el sistema.

Asi que:

  - Cada item se valida por separado. Un item malo no toca a los demas.
  - El lote se manda partido en ventanas, porque los modelos chicos
    inventan indices cuando el lote es grande, y un indice equivocado
    significa un analisis atribuido al mensaje equivocado.
  - Si una ventana entera falla, se reintenta item por item antes de
    rendirse.
  - Si el item no tiene indice, o el indice no corresponde a ningun
    mensaje del lote, se descarta y se cuenta. No se adivina a que
    mensaje correspondia.

Y cada resultado declara su `procedencia`. Un lote donde 18 salieron
del LLM y 2 de la heuristica se reporta como eso, no como "LLM": la
mentira mas pequena es la que mas caro sale cuando alguien busca por
que un activo salio raro.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from sieve.analysis.base import (
    ErrorDeAnalisisError,
    ErrorDeRedError,
    MotivoFallback,
    ProveedorLLM,
    RespuestaIlegibleError,
    ResultadoAnalisis,
    ResumenAnalisis,
)
from sieve.analysis.citas import verificar_citas
from sieve.analysis.extract import JsonIlegibleError, a_analisis, extraer_json
from sieve.analysis.heuristico import AnalizadorHeuristico
from sieve.analysis.prompts import construir_prompt
from sieve.models import Analisis, Interaccion

#: Cuantos mensajes por llamada. Chiquito a proposito: un modelo de
#: 0.5B mantiene bien 4 items y mal 20, y ademas un lote chico que
#: falla se puede reintentar item por item sin multiplicar los tokens.
TAMANO_LOTE = 8


@dataclass(frozen=True, slots=True)
class ItemDelLLM:
    """Un item del LLM ya validado y con las citas verificadas."""

    analisis: Analisis
    citas_descartadas: tuple[str, ...]
    tokens: int
    latencia_ms: int


@dataclass(frozen=True, slots=True)
class Diagnostico:
    """Lo que hay que mirar cuando algo sale mal.

    Sin esto, "el LLM no funciono" no se puede depurar: no se sabe si
    fue la red, la cuota, el formato o el prompt. Con esto, se sabe.
    """

    ventanas: int = 0
    llamadas_llm: int = 0
    items_por_llm: int = 0
    items_por_heuristica: int = 0
    citas_descartadas: int = 0
    tokens: int = 0
    latencia_ms: int = 0
    errores: tuple[str, ...] = ()

    def resumen(self) -> str:
        return (
            f"ventanas={self.ventanas} llamadas={self.llamadas_llm} "
            f"llm={self.items_por_llm} heur={self.items_por_heuristica} "
            f"citas_descartadas={self.citas_descartadas} tokens={self.tokens}"
        )


@dataclass(slots=True)
class _Contador:
    """Acumuladores de una corrida. Se reinician en cada `analizar_lote`.

    Antes eran `getattr(self, "_tokens", 0)` dispersos por el codigo, que
    es la forma de tener estado invisible ybugs de estado que aparecen
    solo en la segunda corrida.
    """

    llamadas: int = 0
    tokens: int = 0
    latencia_ms: int = 0
    errores: list[str] = field(default_factory=list)

    def limpiar(self) -> None:
        self.llamadas = 0
        self.tokens = 0
        self.latencia_ms = 0
        self.errores.clear()


class AnalizadorHibrido:
    """LLM con piso heuristico. Siempre devuelve un analisis valido."""

    def __init__(
        self,
        proveedor: ProveedorLLM | None = None,
        *,
        heuristico: AnalizadorHeuristico | None = None,
        tamano_lote: int = TAMANO_LOTE,
    ) -> None:
        self.proveedor = proveedor
        self.heuristico = heuristico or AnalizadorHeuristico()
        self.tamano_lote = max(1, tamano_lote)
        self.contador = _Contador()
        self.ultimo_diagnostico = Diagnostico()

    # ─── API principal ───────────────────────────────────────────

    def analizar_lote(self, interacciones: Sequence[Interaccion]) -> ResumenAnalisis:
        """Analiza un lote. Devuelve un resultado por mensaje, siempre."""
        self.contador.limpiar()
        resumen = ResumenAnalisis()
        if not interacciones:
            self.ultimo_diagnostico = Diagnostico()
            return resumen

        if self.proveedor is None or not self.proveedor.es_disponible():
            for inter in interacciones:
                resumen.agregar(self._heuristica(inter, "sin_proveedor"))
            self.ultimo_diagnostico = self._diagnostico(resumen, ventanas=0)
            return resumen

        ventanas = 0
        for inicio in range(0, len(interacciones), self.tamano_lote):
            ventana = list(interacciones[inicio : inicio + self.tamano_lote])
            ventanas += 1
            for resultado in self._analizar_ventana(ventana):
                resumen.agregar(resultado)

        self.ultimo_diagnostico = self._diagnostico(resumen, ventanas=ventanas)
        return resumen

    def analizar(self, interaccion: Interaccion) -> ResultadoAnalisis:
        """Atajo para un mensaje suelto."""
        return self.analizar_lote([interaccion]).resultados[0]

    # ─── Ventana ─────────────────────────────────────────────────

    def _analizar_ventana(self, ventana: list[Interaccion]) -> list[ResultadoAnalisis]:
        """Una ventana, con tres escalones de degradacion.

        El camino feliz es un viaje. El de degradacion baja el costo en
        cada escalon:

          1. La ventana entera sale bien -> se usan todos sus items.
          2. Faltan items -> se reintentan SOLO esos, de a uno.
          3. Un item sigue faltando -> heuristica para ese item.

        El paso 2 existe porque un JSON de 8 items con 1 item raro es el
        caso mas comun de un modelo real, y reintentar el lote entero
        gasta tokens sin chances de mejorar. Y "reintentar el lote
        entero" ademas tiraria abajo los items que ya salieron bien.

        La version anterior de esta funcion decia `if not por_indice`:
        reintentaba solo si la ventana entera habia fallado. Con un
        modelo que acierta 1 de 8, los otros 7 caian a heuristica para
        siempre, sin reintento, y el escalon 2 no existia en la practica
        salvo cuando fallaba TODO. El caso que el propio docstring
        describe como el motivations era el unico que no se cubria.

        Y reintentar solo lo que falta tiene un costo que se ve: si 7 de
        8 entraron bien, la recuperacion son 7 llamadas. Por eso no se
        reintenta un item que ya volvio, y por eso una ventana de un
        solo item no reintenta (ya esta en su ultimo escalon).
        """
        por_indice = self._llm_ventana(ventana)

        faltantes = [
            (i, ventana[i - 1]) for i in range(1, len(ventana) + 1) if i not in por_indice
        ]
        if faltantes and len(ventana) > 1:
            por_indice.update(self._llm_de_a_uno(faltantes))

        resultados: list[ResultadoAnalisis] = []
        for i, inter in enumerate(ventana, start=1):
            item = por_indice.get(i)
            if item is None:
                resultados.append(self._heuristica(inter, self._motivo_pendiente()))
                continue
            resultados.append(
                ResultadoAnalisis(
                    interaccion=inter,
                    analisis=item.analisis,
                    procedencia="llm",
                    modelo=self._nombre_modelo(),
                    tokens=item.tokens,
                    latencia_ms=item.latencia_ms,
                    citas_descartadas=item.citas_descartadas,
                )
            )
        return resultados

    def _llm_ventana(self, ventana: list[Interaccion]) -> dict[int, ItemDelLLM]:
        """Un viaje al LLM para toda la ventana. Vacio si no se pudo."""
        assert self.proveedor is not None
        self.contador.llamadas += 1
        inicio = time.monotonic()

        try:
            respuesta = self.proveedor.completar(construir_prompt(ventana))
        except (ErrorDeAnalisisError, OSError) as exc:
            self._nota_error(exc)
            return {}
        self.contador.latencia_ms += int((time.monotonic() - inicio) * 1000)
        self.contador.tokens += respuesta.tokens_totales

        try:
            datos = extraer_json(respuesta.texto)
        except JsonIlegibleError as exc:
            self.contador.errores.append(f"json_invalido: {str(exc)[:120]}")
            return {}

        try:
            parseados = self._parsear_items(datos, ventana)
        except RespuestaIlegibleError as exc:
            self.contador.errores.append(f"respuesta_ilegible: {str(exc)[:120]}")
            return {}

        # Los tokens y la latencia son de la llamada, no del item. Se
        # reparten para que el total del lote sume lo que realmente se
        # gasto, sin inventar una medicion por item.
        por_item_tokens = respuesta.tokens_totales // max(1, len(parseados) or 1)
        por_item_latencia = respuesta.latencia_ms // max(1, len(parseados) or 1)

        salida: dict[int, ItemDelLLM] = {}
        for indice, analisis in parseados.items():
            inter = ventana[indice - 1]
            verificadas, descartadas = verificar_citas(analisis.citas, inter.texto)
            # Las citas no verificadas NO llegan al Analisis. Un activo
            # tiene que poder citar su fuente; una cita que no se puede
            # verificar contra el texto original no puede terminar en un
            # post publicado con el nombre de otra persona.
            analisis.citas.clear()
            analisis.citas.extend(verificadas)
            salida[indice] = ItemDelLLM(
                analisis=analisis,
                citas_descartadas=tuple(descartadas),
                tokens=por_item_tokens,
                latencia_ms=por_item_latencia,
            )
        return salida

    def _llm_de_a_uno(self, pares: Sequence[tuple[int, Interaccion]]) -> dict[int, ItemDelLLM]:
        """Segundo escalon: un mensaje por llamada, solo los que faltan.

        Recibe pares `(indice_original, interaccion)` en vez de una lista
        suelta, y devuelve las claves indexadas por el indice ORIGINAL.

        Que el indice viaje explicito es el punto. La version anterior
        tomaba la lista, reenumeraba desde 1 y guardaba bajo `i`, asi que
        al recibir solo un subconjunto de la ventana las claves ya no
        cuadraban con los mensajes: los items se associate al mensaje
        equivocado en silencio, que es peor que perderlos.

        Y por dentro lee `por_indice.get(1)`, no `.get(i)`: la llamada
        individual reenumerar su unico argumento, asi que su clave SIEMPRE
        es 1. Ese `1` fijo fue la causa del bug original, cuando se
        comparaba contra `i` y solo el primer mensaje de la ventana se
        reintentaba.
        """
        salida: dict[int, ItemDelLLM] = {}
        for indice_original, inter in pares:
            por_indice = self._llm_ventana([inter])
            item = por_indice.get(1)
            if item is not None:
                salida[indice_original] = item
        return salida

    def _parsear_items(
        self, datos: dict[str, object], ventana: list[Interaccion]
    ) -> dict[int, Analisis]:
        """Del JSON crudo a `{indice: Analisis}`, descartando lo invalido.

        El `indice` es lo UNICO que vincula una respuesta con su entrada,
        y por eso el prompt lo pide explicitamente. Si falta, o no
        corresponde a ningun mensaje del lote, el item se descarta:
        inventar a que mensaje corresponde es peor que perderlo, porque
        un analisis pegado al mensaje equivocado produce un activo sobre
        la persona que no se lo merecio.
        """
        items = datos.get("resultados")
        if not isinstance(items, list):
            raise RespuestaIlegibleError(f"sin clave 'resultados'; hay: {sorted(datos)[:8]}")

        salida: dict[int, Analisis] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            indice = item.get("indice")
            # `bool` es subclase de `int` en Python: `True` pasaria el
            # chequeo como indice 1 y asignaria un analisis al mensaje
            # equivocado. El chequeo explicito lo evita.
            if (
                isinstance(indice, int)
                and not isinstance(indice, bool)
                and 1 <= indice <= len(ventana)
            ):
                try:
                    salida[indice] = a_analisis(
                        item, mensaje_id=ventana[indice - 1].mensaje_id
                    )
                except JsonIlegibleError as exc:
                    self.contador.errores.append(
                        f"item {indice} invalido: {str(exc)[:80]}"
                    )
        return salida

    # ─── Heuristica y estado ─────────────────────────────────────

    def _heuristica(self, inter: Interaccion, motivo: MotivoFallback) -> ResultadoAnalisis:
        return ResultadoAnalisis(
            interaccion=inter,
            analisis=self.heuristico.a_analisis(inter),
            procedencia="heuristico",
            modelo=None,
            motivo_fallback=motivo,
        )

    def _nombre_modelo(self) -> str:
        # El protocolo no obliga a exponer `modelo`: lo que obliga es que
        # la procedencia lo registre. Se lee del atributo si existe.
        return str(getattr(self.proveedor, "modelo", None) or self._nombre_proveedor())

    def _nombre_proveedor(self) -> str:
        return str(getattr(self.proveedor, "nombre", "desconocido"))

    def _motivo_pendiente(self) -> MotivoFallback:
        """Por que cayo este item. El primer error de la corrida."""
        if not self.contador.errores:
            return "sin_respuesta"
        ultimo = self.contador.errores[-1]
        if "json_invalido" in ultimo:
            return "json_invalido"
        if "respuesta_ilegible" in ultimo:
            return "respuesta_ilegible"
        if "timeout" in ultimo.lower():
            return "timeout"
        return "error_de_red"

    def _nota_error(self, exc: Exception) -> None:
        if isinstance(exc, TimeoutError):
            self.contador.errores.append("timeout")
        elif isinstance(exc, ErrorDeRedError):
            self.contador.errores.append(f"error_de_red: {str(exc)[:100]}")
        else:
            self.contador.errores.append(f"error: {str(exc)[:100]}")

    def _diagnostico(self, resumen: ResumenAnalisis, *, ventanas: int) -> Diagnostico:
        por_llm = sum(1 for r in resumen.resultados if r.procedencia == "llm")
        return Diagnostico(
            ventanas=ventanas,
            llamadas_llm=self.contador.llamadas,
            items_por_llm=por_llm,
            items_por_heuristica=len(resumen.resultados) - por_llm,
            citas_descartadas=resumen.citas_descartadas,
            tokens=self.contador.tokens,
            latencia_ms=self.contador.latencia_ms,
            errores=tuple(self.contador.errores),
        )
