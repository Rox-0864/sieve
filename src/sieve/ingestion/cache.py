"""
Cache en disco para respuestas HTTP.

No es una optimizacion: es un requisito. La API de Stack Exchange da
300 requests por dia. Un pipeline que re-consulta en cada corrida
quema la cuota en minutos y despues no podés reproducir nada de lo
que hiciste ayer.

El cache tambien convierte al pipeline en algo determinista: los tests
usan fixtures, la demo usa cache, y una corrida de hace tres dias se
puede volver a correr sin red y da el mismo resultado.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


class CacheDisco:
    """JSON en disco, con vencimiento.

    Simplicidad a proposito: no hay LRU, no hay eviction, no hay
    sqlite. Es una carpeta con archivos y un TTL. A escala de un
    proyecto de portfolio es exactamente suficiente, y cuando no lo sea
    el perfil de uso va a decir exactamente cual pieza falta.
    """

    def __init__(self, raiz: Path | str, ttl_segundos: int = 86_400) -> None:
        self.raiz = Path(raiz)
        self.ttl = ttl_segundos
        self.raiz.mkdir(parents=True, exist_ok=True)

    def _ruta(self, clave: str) -> Path:
        # Hash del key: una URL puede tener caracteres invalidos en un
        # nombre de archivo, y una ruta larga rompe el limite del OS.
        digest = hashlib.sha256(clave.encode("utf-8")).hexdigest()[:32]
        return self.raiz / f"{digest}.json"

    def existe(self, clave: str) -> bool:
        """TTL<=0 significa 'no cachear': siempre se considera vencido.

        Al reves, un TTL de 0 devolveria True para todo lo que esta en
        disco y el cache se volveria permanente por accidente. Cero tiene
        que significar cero.
        """
        ruta = self._ruta(clave)
        if not ruta.exists() or self.ttl <= 0:
            return False
        return (time.time() - ruta.stat().st_mtime) < self.ttl

    def leer(self, clave: str) -> Any | None:
        """Devuelve el valor cacheado, o None si no hay o vencio.

        Un archivo corrupto devuelve None en vez de propagar la
        excepcion: un cache nunca puede ser la razon por la que el
        pipeline cae.
        """
        ruta = self._ruta(clave)
        if not self.existe(clave):
            return None
        try:
            return json.loads(ruta.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def escribir(self, clave: str, valor: Any) -> Path:
        ruta = self._ruta(clave)
        temporal = ruta.with_suffix(".tmp")
        temporal.write_text(
            json.dumps(valor, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # Escritura atomica: si el proceso muere a mitad de camino, no
        # queda un JSON truncado que se lee como cache valido.
        temporal.replace(ruta)
        return ruta

    def limpiar(self) -> int:
        """Borra todo. Devuelve cuantos archivos elimino."""
        count = 0
        for archivo in self.raiz.glob("*.json"):
            archivo.unlink(missing_ok=True)
            count += 1
        return count

    def __len__(self) -> int:
        return len(list(self.raiz.glob("*.json")))
