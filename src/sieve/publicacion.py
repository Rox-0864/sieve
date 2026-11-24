"""
Publicacion (M5): sube activos curados a OCI Object Storage.

La regla de oro: solo se publica lo curado. Un activo con `curado=False`
o con grounding rechazado nunca sale de aca. El panel (M6) es quien da
vuelta `curado`; este modulo lo respeta y lo sube.

El cliente es un seam inyectable
--------------------------------
Los tests corren offline con un doble, y el SDK real (`oci`) se importa
lazy dentro de `ClienteOci`. Asi instalar el extra `oci` sigue siendo
opcional, y un test no puede pegarle a Oracle Cloud por accidente: un
test que toca la red no es un test, es una mina.

El resultado es honesto
-----------------------
`AlmacenamientoOci.status` es `guardado_con_exito`, `fallido` o
`simulado`. Un fallo de escritura se reporta como `fallido`, no se
esconde para que la demo se vea linda: es exactamente la mentira que
hace que un proyecto no sea confiable.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sieve.curation import PaqueteCuracion
from sieve.models import AlmacenamientoOci


class PublicacionError(RuntimeError):
    """No se puede publicar: o el activo no esta curado, o el destino fallo."""


class ClienteAlmacenamiento(Protocol):
    """El seam. Sube bytes a un destino y devuelve la URL del objeto.

    La implementacion real es `ClienteOci`; los tests usan un doble. El
    protocolo tiene UN metodo a proposito: quien sube devuelve la URL, y
    el resto (ruta, contenido, guard) es del `PublicadorOci`.
    """

    def subir(self, contenido: bytes, ruta_objeto: str) -> str:
        """Sube `contenido` a `ruta_objeto` y devuelve su URL (o PAR)."""
        ...


class PublicadorOci:
    """Sube activos curados. Nunca toca uno sin firmar.

    `cliente=None` es el modo simulado: no toca la red, no exige
    credenciales, y devuelve `status="simulado"`. Es el default seguro
    para una demo o una corrida de prueba.
    """

    def __init__(
        self,
        cliente: ClienteAlmacenamiento | None,
        *,
        bucket: str,
        prefijo: str = "activos",
    ) -> None:
        self.cliente = cliente
        self.bucket = bucket
        self.prefijo = prefijo
        #: El detalle del ultimo fallo de subida, para que la CLI pueda
        #: decir POR QUE fallo y no solo "fallido".
        self.ultimo_error: str | None = None

    def _ruta(self, paquete: PaqueteCuracion) -> str:
        return f"{self.prefijo}/{paquete.mensaje_id}/{paquete.formato}.json"

    def _contenido(self, paquete: PaqueteCuracion) -> bytes:
        return json.dumps(paquete.activo, ensure_ascii=False, indent=2).encode(
            "utf-8"
        )

    def publicar(self, paquete: PaqueteCuracion) -> AlmacenamientoOci:
        if not paquete.publicable():
            raise PublicacionError(
                f"{paquete.mensaje_id}.{paquete.formato} no es publicable: "
                "falta la firma de curacion o el grounding rechazo"
            )
        ruta = self._ruta(paquete)
        if self.cliente is None:
            return AlmacenamientoOci(
                bucket=self.bucket, ruta_objeto=ruta, status="simulado"
            )
        try:
            url = self.cliente.subir(self._contenido(paquete), ruta)
        except Exception as exc:  # noqa: BLE001 — el detalle viaja al stderr
            self.ultimo_error = f"{ruta}: {exc}"
            return AlmacenamientoOci(
                bucket=self.bucket, ruta_objeto=ruta, status="fallido"
            )
        return AlmacenamientoOci(
            bucket=self.bucket,
            ruta_objeto=ruta,
            status="guardado_con_exito",
            url_preautenticada=url,
        )


class ClienteOci:
    """El cliente real de OCI Object Storage.

    Importa `oci` recien al instanciar, no al importar el modulo: asi
    `publicacion.py` se importa (y sus tests corren) sin el extra `oci`.
    Construirlo exige credenciales validas en el config de OCI; si no
    las hay, falla al instanciar, no tres pasos despues.

    No esta cubierto por los tests offline a proposito: probarlo exige
    el SDK y una tenancy real. Al instalar `oci`, verificar `put_object`
    y `create_preauthenticated_request` contra la version del SDK.
    """

    def __init__(
        self,
        *,
        bucket: str,
        config_path: str = "~/.oci/config",
        profile: str = "DEFAULT",
        vigencia_dias: int = 7,
    ) -> None:
        try:
            import oci  # type: ignore[import-not-found]  # lazy: no obliga el extra
        except ImportError as exc:
            raise ImportError(
                "el cliente OCI necesita el extra 'oci': pip install -e '.[oci]'"
            ) from exc
        self._oci = oci
        self.bucket = bucket
        config = oci.config.from_file(config_path, profile)
        self._object_storage = oci.object_storage.ObjectStorageClient(config)
        self._namespace = self._object_storage.get_namespace().data
        self._vigencia = timedelta(days=vigencia_dias)

    def subir(self, contenido: bytes, ruta_objeto: str) -> str:
        self._object_storage.put_object(
            self._namespace,
            self.bucket,
            ruta_objeto,
            contenido,
            content_type="application/json",
        )
        expira = datetime.now(UTC) + self._vigencia
        detalles = self._oci.object_storage.models.CreatePreauthenticatedRequestDetails(
            name=f"par-{ruta_objeto}-{int(expira.timestamp())}",
            object_name=ruta_objeto,
            access_type="ObjectRead",
            time_expires=expira,
        )
        respuesta = self._object_storage.create_preauthenticated_request(
            self._namespace, self.bucket, detalles
        )
        return respuesta.data.access_uri  # type: ignore[no-any-return]
