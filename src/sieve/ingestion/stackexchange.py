"""
Cliente de la Stack Exchange API 2.3.

Por que la API y no el dump: el dump de es.stackoverflow pesa 528 MB
comprimido y hay que descomprimirlo con 7z. La API devuelve los MISMOS
campos por HTTP, en JSON, con cero bytes en disco y con 300 requests
gratis por dia. No hay ninguna razon para bajarse 528 MB en una
maquina con 5 GB libres.

Cache-first, siempre. La cuota de 300/dia no perdona.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

import httpx

from sieve.ingestion.cache import CacheDisco
from sieve.ingestion.ground_truth import GroundTruth, InteraccionEtiquetada, mensaje_id
from sieve.ingestion.text import desescapar_tags, html_a_texto, truncar
from sieve.models import Interaccion, TipoInteraccion

API_BASE = "https://api.stackexchange.com/2.3"
CACHE_DIR = "data/cache/stackexchange"

#: Pausa minima entre requests. Ser buen ciudadano con una API publica
#: gratuita no es cortesia, es engineering: las APIs sin rate limit
#: visible son las que empiezan a devolver 503.
_ESPERA_MINIMA_S = 0.35

#: Un transporte recibe la ruta y los params ya normalizados, y devuelve
#: (payload, cuota_restante). Devolver la cuota junto con el cuerpo es lo
#: que permite al cliente registrarla sin que el transporte real tenga
#: que conocer al cliente.
Transporte = Callable[[str, dict[str, Any]], tuple[dict[str, Any], int | None]]


class CuotaAgotadaError(RuntimeError):
    """La API devuelve un 400 con backoff cuando se pasa de 300/dia."""


def transporte_http(
    ruta: str, params: dict[str, Any], timeout: float = 30.0
) -> tuple[dict[str, Any], int | None]:
    """El unico lugar del proyecto donde existe la red real.

    Aislarlo asi no es purismo: hace que los tests puedan inyectar un
    transporte falso y garantizar por construccion que no salen a
    internet. Un test que depende de la cuota diaria de 300 requests
    no es un test, es una bomba de reloj.
    """
    with httpx.Client(timeout=timeout) as cliente:
        respuesta = cliente.get(f"{API_BASE}/{ruta}", params=params)

    # Stack Exchange avisa la cuota en la respuesta. Es lo unico que
    # permite saber cuanto queda sin gastar otro request.
    cuota = (
        int(respuesta.headers["quota_remaining"])
        if "quota_remaining" in respuesta.headers
        else None
    )

    if respuesta.status_code == 400:
        detalle = respuesta.text[:200]
        if "throttle" in detalle.lower() or "quota" in detalle.lower():
            raise CuotaAgotadaError(
                f"cuota de la API agotada: {detalle}. "
                "Los datos ya cacheados siguen siendo usables."
            )
        raise RuntimeError(f"API Stack Exchange devolvio 400: {detalle}")
    respuesta.raise_for_status()
    return respuesta.json(), cuota


def clave_de_preguntas(**overrides: Any) -> str:
    """Clave de cache que usa `preguntas()` con sus defaults.

    Existe para que un test o un script de precalentado puedan escribir
    el cache sin replicar el dict de parametros a mano. Replicarlo es
    como seRompe el cache en silencio: la clave no coincide, el cliente
    cree que no hay nada cacheado, y sale a la red. Durante el
    desarrollo de M1 eso costo una cuota real.
    """
    params: dict[str, Any] = {
        "site": "es.stackoverflow",
        "pagesize": 100,
        "page": 1,
        "order": "desc",
        "sort": "votes",
        "filter": "withbody",
    }
    params.update(overrides)
    return ClienteStackExchange.clave("questions", params)


class ClienteStackExchange:
    """Lectura de preguntas con su ground truth."""

    def __init__(
        self,
        cache_dir: str = CACHE_DIR,
        ttl_segundos: int = 86_400,
        timeout: float = 30.0,
        transporte: Transporte | None = None,
    ) -> None:
        self.cache = CacheDisco(cache_dir, ttl_segundos)
        self.timeout = timeout
        # Inyectar el transporte es lo que hace estos tests offline.
        self._transporte = transporte or (lambda r, p: transporte_http(r, p, timeout))
        self._ultimo_request = 0.0
        self._cuota_ultima: int | None = None
        self.requests_hechos = 0
        self.aciertos_cache = 0

    @property
    def cuota_restante(self) -> int | None:
        """Requests que quedan hoy. None si nunca se hizo uno."""
        return self._cuota_ultima

    @staticmethod
    def clave(ruta: str, params: dict[str, Any]) -> str:
        """Clave de cache estable para una consulta.

        Publica a proposito: permite precalentar el cache desde un script
        sin replicar la logica de thumbprint.
        """
        return f"{ruta}?{sorted(params.items())}"

    # ─── Transporte ──────────────────────────────────────────────

    def _get(self, ruta: str, **params: Any) -> dict[str, Any]:
        """Cache-first: solo sale a la red si no hay nada vigente."""
        params.setdefault("filter", "withbody")
        clave = self.clave(ruta, params)

        cacheado = self.cache.leer(clave)
        if isinstance(cacheado, dict):
            self.aciertos_cache += 1
            return cacheado

        # Throttle: nunca menos de _ESPERA_MINIMA_S entre llamadas.
        transcurrido = time.monotonic() - self._ultimo_request
        if transcurrido < _ESPERA_MINIMA_S:
            time.sleep(_ESPERA_MINIMA_S - transcurrido)

        payload, cuota = self._transporte(ruta, params)

        self._ultimo_request = time.monotonic()
        self.requests_hechos += 1
        if cuota is not None:
            self._cuota_ultima = cuota

        self.cache.escribir(clave, payload)
        return payload

    # ─── Lectura ─────────────────────────────────────────────────

    def preguntas(
        self,
        sitio: str = "es.stackoverflow",
        paginas: int = 1,
        por_pagina: int = 100,
        ordenar: str = "votes",
        etiqueta: str | None = None,
        minimo_score: int = 1,
    ) -> list[InteraccionEtiquetada]:
        """Trae preguntas ordenadas por valor percibido por la comunidad.

        `minimo_score=1` descarta el ruido. Una pregunta con score 0 fue
        preguntada, no respondida, ni mirada dos veces. Entrenarla al
        pipeline solo agrega costo de API.
        """
        resultado: list[InteraccionEtiquetada] = []
        for numero, pagina in enumerate(range(1, paginas + 1), start=1):
            params: dict[str, Any] = {
                "site": sitio,
                "pagesize": min(por_pagina, 100),
                "page": pagina,
                "order": "desc",
                "sort": ordenar,
            }
            if etiqueta:
                params["tagged"] = etiqueta

            payload = self._get("questions", **params)
            items = payload.get("items", [])
            for item in items:
                etiquetada = self._a_interaccion(item, sitio)
                if (
                    etiquetada
                    and etiquetada.ground_truth
                    and etiquetada.ground_truth.score >= minimo_score
                ):
                    resultado.append(etiquetada)
            if not payload.get("has_more"):
                break
            if numero < paginas:
                time.sleep(_ESPERA_MINIMA_S)
        return resultado

    def iter_preguntas(self, **kwargs: Any) -> Iterator[InteraccionEtiquetada]:
        """Variante generadora para no cargar 1000 preguntas en memoria."""
        yield from self.preguntas(**kwargs)

    # ─── Mapeo ───────────────────────────────────────────────────

    def _a_interaccion(
        self, item: dict[str, Any], sitio: str
    ) -> InteraccionEtiquetada | None:
        """Traduce un item de la API a una Interaccion + GroundTruth.

        Devuelve None en vez de tirar excepcion cuando el item no tiene
        cuerpo. Un registro raro no puede abortar la ingesta de los otros
        99: aca se degrada, no se rompe.
        """
        cuerpo = item.get("body") or ""
        texto = html_a_texto(cuerpo)
        if not texto.strip():
            return None

        owner = item.get("owner") or {}
        autor = (owner.get("display_name") or "desconocido").strip()
        tags = tuple(desescapar_tags(item.get("tags") or []))

        interaccion = Interaccion(
            autor=autor,
            canal=_etiqueta_canal(sitio, tags),
            tipo=TipoInteraccion.DUDA,
            texto=truncar(texto),
            timestamp=_a_datetime(item.get("creation_date")),
            mensaje_id=mensaje_id(
                Interaccion(
                    autor=autor, canal=_etiqueta_canal(sitio, tags), texto=texto
                )
            ),
        )

        ground_truth = GroundTruth(
            score=int(item.get("score") or 0),
            answer_count=int(item.get("answer_count") or 0),
            aceptado=bool(item.get("accepted_answer_id")),
            view_count=int(item.get("view_count") or 0),
            tags=tags,
            url=item.get("link"),
            # Los dos campos siguientes no son opcionales en la practica:
            # sin ellos no se puede cumplir la atribucion CC BY-SA.
            author_url=(owner or {}).get("link"),
            content_license=item.get("content_license"),
            source_id=str(item.get("question_id")) if item.get("question_id") else None,
        )
        return InteraccionEtiquetada(
            interaccion=interaccion, ground_truth=ground_truth
        )

    def a_lote(self, etiquetadas: list[InteraccionEtiquetada], comun: str) -> Any:
        """Arma el `LoteComunidad` que espera el resto del pipeline."""
        from sieve.models import LoteComunidad

        semana = datetime.now().isocalendar().week
        return LoteComunidad(
            origen_comunidad=comun,
            periodo_referencia=f"semana-{semana:02d}",
            interacciones=[e.interaccion for e in etiquetadas],
        )


# ─── Helpers ──────────────────────────────────────────────────────────


def _etiqueta_canal(sitio: str, tags: tuple[str, ...]) -> str:
    """El canal es el 'donde se dijo'. Para un dump es el sitio.

    Usar el sitio entero como canal pierde granularidad; usar solo el
    primer tag pierde contexto. `#sitio · tag1 · tag2` da las dos cosas
    y ademas es legible en la interfaz de curacion.
    """
    if not tags:
        return f"#{sitio}"
    return f"#{sitio} · {' · '.join(tags[:3])}"


def _a_datetime(epoch: int | None) -> datetime | None:
    return datetime.fromtimestamp(epoch) if epoch else None
