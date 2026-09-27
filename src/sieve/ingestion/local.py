"""
Loaders de archivos locales: JSON y CSV.

Es la via de entrada de la demo y de los tests. No necesita red, no
necesita cuota de API, y corre en cualquier lado. El pipeline tiene que
poder demostrarse sin depender de un servicio de terceros: si la demo
requiere internet, se cae en el primer tunel de la red.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sieve.ingestion.ground_truth import mensaje_id
from sieve.models import Interaccion, LoteComunidad

#: Separador por defecto del CSV. Una de las trampas mas comunes de la
#: ingesta: un CSV con punto y coma, generado por Excel en locale
#: europeo, parseado con coma, produce UNA columna llamada "a;b;c".
DEFAULT_DELIMITER = ","


def cargar_json(ruta: Path | str, delimitador: str = DEFAULT_DELIMITER) -> LoteComunidad:
    """Carga el JSON de lote del documento ONE.

    Acepta las tres formas que aparecen en la practica:
      1. el objeto completo del documento ({origen_comunidad, ...})
      2. una lista suelta de interacciones
      3. una lista de envios de Discord con {autor, contenido, ...}
    """
    datos = json.loads(Path(ruta).read_text(encoding="utf-8"))

    if isinstance(datos, list):
        datos = {
            "origen_comunidad": Path(ruta).stem,
            "periodo_referencia": "sin-periodo",
            "interacciones": datos,
        }
    elif isinstance(datos, dict) and "messages" in datos:
        datos = _normaliza_formato_discord(datos, Path(ruta).stem)

    lote = LoteComunidad.model_validate(datos)
    for interaccion in lote.interacciones:
        if not interaccion.mensaje_id:
            interaccion.mensaje_id = mensaje_id(interaccion)
    return lote


def cargar_csv(
    ruta: Path | str,
    origen_comunidad: str = "csv-import",
    periodo_referencia: str = "sin-periodo",
    delimitador: str | None = None,
) -> LoteComunidad:
    """Carga un CSV. Autodetecta el delimitador si no se indica.

    La autodeteccion existe porque un mismo proyecto va a recibir CSVs
    con coma de pandas y con punto y coma de Excel en español. Adivinar
    mal convierte N columnas en una sola y el error aparece tres etapas
    despues, cuando el LLM no encuentra los autores.
    """
    ruta = Path(ruta)
    with ruta.open(encoding="utf-8-sig", newline="") as archivo:
        muestra = archivo.read(8192)
        archivo.seek(0)
        sep = delimitador or _detectar_delimitador(muestra)
        reader = csv.DictReader(archivo, delimiter=sep)

        filas_crudas = [_normaliza_fila(fila) for fila in reader]
        filas: list[dict[str, Any]] = [f for f in filas_crudas if _tiene_texto(f)]

    return _a_lote(filas, origen_comunidad, periodo_referencia, ruta.stem)


def guardar_json(lote: LoteComunidad, ruta: Path | str) -> Path:
    """Serializa un lote. Lo que va al bucket de OCI después."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(
        json.dumps(lote.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return ruta


# ─── Helpers ──────────────────────────────────────────────────────────

#: Nombres aceptados para cada campo, de mas probable a menos.
_CAMPOS = {
    "autor": ("autor", "author", "user", "username", "display_name", "nombre"),
    "canal": ("canal", "channel", "room", "guild", "community", "sala"),
    "texto": ("texto", "text", "content", "contenido", "message", "body", "mensaje"),
    "tipo": ("tipo", "type", "kind", "categoria"),
    "timestamp": ("timestamp", "fecha", "date", "created_at", "ts"),
}


def _detectar_delimitador(muestra: str) -> str:
    """El delimitador que mas columnas produce en la primera linea."""
    if not muestra:
        return DEFAULT_DELIMITER
    primera = muestra.splitlines()[0]
    mejor, puntaje = DEFAULT_DELIMITER, -1
    for candidato in (",", ";", "\t", "|"):
        cantidad = primera.count(candidato)
        if cantidad > puntaje:
            mejor, puntaje = candidato, cantidad
    return mejor


def _tiene_texto(fila: dict[str, Any]) -> bool:
    return bool(str(fila.get("texto") or "").strip())


def _normaliza_fila(fila: dict[str, Any]) -> dict[str, Any]:
    """Mapea una fila de CSV al esquema del documento.

    Los headers de un CSV exportado de Discord o de Google Sheets nunca
    coinciden con el contrato. Aceptar los sinónimos mas comunes evita
    que la primera fila valida y las 400 siguientes fallen.
    """
    normalizado: dict[str, Any] = {}
    for destino, alias in _CAMPOS.items():
        for clave, valor in fila.items():
            if clave and clave.strip().lower() in alias:
                normalizado[destino] = valor
                break
    return normalizado


def _normaliza_formato_discord(
    datos: dict[str, Any], origen: str
) -> dict[str, Any]:
    """Traduce el formato de un export de Discord al contrato.

    Discord usa `content` para el texto y no tiene `canal` por mensaje.
    El nombre del canal vive en el mensaje de sistema que announce que
    alguien entro al canal; adivinarlo seria inventar informacion.
    """
    mensajes = []
    for mensaje in datos.get("messages", []):
        texto = str(mensaje.get("content") or "").strip()
        if not texto:
            continue
        mensajes.append(
            {
                "autor": mensaje.get("author", {}).get("username", "desconocido")
                if isinstance(mensaje.get("author"), dict)
                else mensaje.get("author", "desconocido"),
                "canal": mensaje.get("channel", mensaje.get("canal", origen)),
                "texto": texto,
                "tipo": mensaje.get("type", "chat"),
                "timestamp": mensaje.get("timestamp"),
            }
        )
    return {
        "origen_comunidad": datos.get("guild", {}).get("name", origen)
        if isinstance(datos.get("guild"), dict)
        else origen,
        "periodo_referencia": datos.get("sin-periodo", "sin-periodo"),
        "interacciones": mensajes,
    }


def _a_lote(
    filas: Iterable[dict[str, Any]], origen: str, periodo: str, nombre: str
) -> LoteComunidad:
    filas = list(filas)
    if not filas:
        raise ValueError(
            f"'{nombre}' no tiene filas utilizables. "
            "Revisa los encabezados: se aceptan autor/author/user, "
            "canal/channel/room, texto/text/content/contenido."
        )
    return LoteComunidad(
        origen_comunidad=origen,
        periodo_referencia=periodo,
        interacciones=[Interaccion.model_validate(f) for f in filas],
    )
