"""
API HTTP de Sieve (M7).

Es la capa que n8n orquesta: `Webhook → HTTP → IF → Response`. Cada etapa
de maquina del pipeline es un endpoint JSON, y n8n las enlaza. La API NO
cura: la curaduria sigue siendo del panel humano (M6). La API orquesta
las etapas de maquina, y punto.

Los defaults son offline, igual que en la CLI: `/analizar` corre
heuristica si no se pide provider, y `/publicar` es simulado si no se
pide lo contrario. El LLM y el OCI real son opt-in. Un endpoint que le
pega a una nube o a un modelo sin que se lo pidas explicito no es un
endpoint del que nadie se vaya a fiar.

Se corre con:

    uvicorn sieve.api:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from sieve.analysis.base import ProveedorLLM
from sieve.analysis.hibrido import AnalizadorHibrido
from sieve.analysis.providers import construir_proveedor
from sieve.config import settings
from sieve.curation import PaqueteCuracion
from sieve.generation.assets import ErrorDeGeneracionError, GeneradorActivos
from sieve.models import ActivosGenerados, AlmacenamientoOci, LoteComunidad
from sieve.perfil import NOMBRE_POR_DEFECTO, cargar_perfil
from sieve.pipeline import (
    ETIQUETA_POR_FORMATO,
    FORMATO_POR_TIPO,
    enrutar_lote_analizado,
)
from sieve.publicacion import ClienteOci, PublicadorOci

app = FastAPI(
    title="Sieve",
    description="Convierte conversaciones de comunidad en activos publicables.",
    version="1.0.0",
)


class RespuestaGeneracion(BaseModel):
    """Lo que `/generar` devuelve: los activos y el conteo honesto."""

    total: int
    rechazados: int
    activos: list[ActivosGenerados]


def _proveedor(provider: str, modelo: str | None) -> ProveedorLLM:
    """Construye el proveedor (solo cuando se pide uno).

    Los errores son HTTP y nombran el problema: un 500 silencioso o un
    provider que no dice que no esta configurado es lo que hace que un
    flujo n8n se rompa sin que nadie sepa por que.
    """
    proveedor = construir_proveedor(provider, modelo=modelo)
    if proveedor is None:
        raise HTTPException(
            status_code=400, detail=f"provider '{provider}' no soportado"
        )
    if not proveedor.es_disponible():
        raise HTTPException(
            status_code=400, detail=f"provider '{provider}' no esta configurado"
        )
    return proveedor


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "1.0.0"}


@app.post("/analizar", response_model=LoteComunidad)
def analizar(
    lote: LoteComunidad,
    provider: str | None = Query(default=None),
    modelo: str | None = Query(default=None),
) -> LoteComunidad:
    """Analiza un lote. Heuristica por defecto; LLM solo si se pide."""
    proveedor = _proveedor(provider, modelo) if provider is not None else None
    analizador = AnalizadorHibrido(proveedor)
    resumen = analizador.analizar_lote(lote.interacciones)
    return LoteComunidad(
        origen_comunidad=lote.origen_comunidad,
        periodo_referencia=lote.periodo_referencia,
        interacciones=lote.interacciones,
        analisis=resumen.a_analisis(),
        procedencia=resumen.conteo_procedencia(),
    )


@app.post("/generar", response_model=RespuestaGeneracion)
def generar(
    lote: LoteComunidad,
    perfil: str = NOMBRE_POR_DEFECTO,
    provider: str = "ollama",
    modelo: str | None = Query(default=None),
) -> RespuestaGeneracion:
    """Genera activos desde un lote YA analizado. Sin fallback.

    La generacion sin modelo no existe: si el provider no esta
    configurado, el endpoint falla con un 400 y lo dice. Un fallback que
    devuelve placeholders es la forma mas comun de publicar basura.
    """
    if not lote.analisis:
        raise HTTPException(
            status_code=400,
            detail="el lote no tiene analisis: corre /analizar primero",
        )
    proveedor = _proveedor(provider, modelo)
    try:
        perfil_org = cargar_perfil(perfil)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    generador = GeneradorActivos(proveedor, perfil_org)
    por_mensaje: dict[str, dict[str, object]] = {}
    total = 0
    rechazados = 0

    # El router decide QUE activo sale de cada mensaje; el generador lo
    # produce. Generar los tres formatos era gastar tokens en activos que
    # el router ya habia descartado.
    for resultado, decision in enrutar_lote_analizado(lote):
        mensaje_id = resultado.interaccion.mensaje_id
        if mensaje_id is None:
            continue
        for formato in FORMATO_POR_TIPO[decision.tipo]:
            etiqueta = ETIQUETA_POR_FORMATO[formato]
            try:
                salida = generador.generar(resultado, formato)
            except ErrorDeGeneracionError:
                rechazados += 1
                continue
            if salida.activo is None:
                rechazados += 1
                continue
            total += 1
            por_mensaje.setdefault(mensaje_id, {})[etiqueta] = salida.activo

    activos = [ActivosGenerados.model_validate(a) for a in por_mensaje.values()]
    return RespuestaGeneracion(total=total, rechazados=rechazados, activos=activos)


@app.post("/publicar", response_model=list[AlmacenamientoOci])
def publicar(
    paquetes: list[PaqueteCuracion],
    simulado: bool = True,
    bucket: str = settings.oci_bucket,
    prefijo: str = settings.oci_prefix,
) -> list[AlmacenamientoOci]:
    """Publica paquetes curados. Simulado por defecto.

    Solo publica lo `publicable()`: un paquete sin firmar se saltea, no
    se publica "por las dudas". El default simulado no toca la red.
    """
    cliente: ClienteOci | None = None
    if not simulado:
        try:
            cliente = ClienteOci(bucket=bucket)
        except ImportError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    publicador = PublicadorOci(cliente, bucket=bucket, prefijo=prefijo)
    return [
        publicador.publicar(paquete)
        for paquete in paquetes
        if paquete.publicable()
    ]
