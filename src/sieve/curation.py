"""
Curacion de activos: la firma humana que falta entre generar y publicar.

El generador (M4) produce activos con `curado=False`, y asi tienen que
quedar hasta que un humano los mire. Este modulo es la infraestructura
de esa firma: un PAQUETE que junta todo lo que el curador necesita para
decidir, y las funciones para guardarlo, cargarlo y aplicarle la
decision.

Que junta el paquete, y por que no es solo el activo
-----------------------------------------------------
`ActivosGenerados` (el contrato de ONE) guarda los activos pelados.
Un panel de curacion que solo ve el activo no puede decidir nada: no ve
de que mensaje salio, no ve por que el grounding lo aprobo (o con que
supuestos), y no puede volver a mirar la fuente. Aprobar a ciegas es
firmar sin leer.

Por eso el paquete lleva la fuente, el activo, el informe de grounding
serializado y la respuesta cruda del modelo. La respuesta cruda es la
cadena de evidencia: "el modelo dijo X, el sistema lo aprobo" es lo que
se puede auditar; sin el crudo solo queda la conclusion.

El `informe` y el `activo` viven como dict dentro del paquete, y los
metodos `informe_tipado()` / `activo_tipado()` los reconstruyen. No se
guardan como objetos tipados a proposito: un dict es lo que viaja al
disco y lo que el panel lee de vuelta, y la reconstruccion explicita es
el unico lugar donde el paquete sabe a que clase corresponde cada
`formato`.

La decision es auditable
------------------------
Aprobar da vuelta `curado` a `True`; rechazar lo deja en `False` y exige
un `motivo`. En ambos casos quedan `curador` y `fecha_decision`. Sin eso,
un panel que "aprueba" no deja rastro de quien firmo ni cuando, y una
firma sin rastro no es una firma.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from sieve.generation.grounding import (
    InformeGrounding,
    Verificacion,
    puede_publicarse,
)
from sieve.models import ActivoBase, Interaccion, Newsletter, PostLinkedin, SugerenciaFaq

#: La etiqueta interna de cada formato. Son las mismas claves de
#: `_ESPECIFICACIONES` en `assets.py`: no se introduce un tercer nombre
#: para la misma cosa.
Formato = Literal["post_linkedin", "newsletter", "faq"]
Decision = Literal["pendiente", "aprobado", "rechazado"]

TIPO_POR_FORMATO: dict[Formato, type[ActivoBase]] = {
    "post_linkedin": PostLinkedin,
    "newsletter": Newsletter,
    "faq": SugerenciaFaq,
}


# ── Serializacion del informe ──────────────────────────────────────
#
# `InformeGrounding` y `Verificacion` son dataclasses frozen, no modelos
# Pydantic: no serializan solos. Estas dos funciones son el unico
# puente. Viven aca y no en `grounding.py` para no tocar la capa que
# esta en el camino caliente de la generacion con una preocupacion de
# persistencia que le es ajena.


def informe_a_dict(informe: InformeGrounding) -> dict[str, Any]:
    """El informe, en la forma que viaja al disco."""
    return {
        "verificaciones": [
            {"nombre": v.nombre, "ok": v.ok, "hallazgos": list(v.hallazgos)}
            for v in informe.verificaciones
        ],
        "supuestos": list(informe.supuestos),
        "marcos": list(informe.marcos),
    }


def informe_de_dict(datos: dict[str, Any]) -> InformeGrounding:
    """Reconstruye el informe desde lo que viajo al disco."""
    return InformeGrounding(
        verificaciones=[
            Verificacion(
                nombre=v["nombre"],
                ok=v["ok"],
                hallazgos=list(v.get("hallazgos", [])),
            )
            for v in datos.get("verificaciones", [])
        ],
        supuestos=list(datos.get("supuestos", [])),
        marcos=list(datos.get("marcos", [])),
    )


class PaqueteCuracion(BaseModel):
    """Un activo y todo lo que un humano necesita para firmarlo.

    Es la unidad de curacion: un paquete por activo, no por interaccion.
    Una interaccion puede producir hasta tres activos (post, newsletter
    y FAQ), y cada uno se cura por separado porque cada uno se publica
    por separado.
    """

    model_config = ConfigDict(extra="forbid")

    mensaje_id: str
    formato: Formato
    #: El mensaje del que salio el activo. Sin la fuente no hay nada
    #: contra que comparar el grounding, y aprobar a ciegas es firmar
    #: sin leer.
    fuente: Interaccion
    #: El activo serializado. Se reconstruye con `activo_tipado()`.
    activo: dict[str, Any]
    #: El informe de grounding serializado. `informe_tipado()`.
    informe: dict[str, Any]
    #: Lo que el modelo contesto, textual. Cadena de evidencia.
    respuesta_cruda: str = ""
    campos_ignorados: tuple[str, ...] = ()
    version_prompt: str = ""

    # ── La firma humana ─────────────────────────────────────────
    decision: Decision = "pendiente"
    motivo: str = ""
    curador: str = ""
    fecha_decision: datetime | None = None

    @classmethod
    def desde_activo(
        cls,
        activo: ActivoBase,
        informe: InformeGrounding,
        fuente: Interaccion,
        formato: Formato,
        *,
        respuesta_cruda: str = "",
        campos_ignorados: tuple[str, ...] = (),
        version_prompt: str = "",
    ) -> PaqueteCuracion:
        """Arma el paquete desde un activo ya aprobado por el grounding.

        Solo se llama para activos cuyo `informe.veredicto !=
        "rechazado"`: un activo rechazado no llega a paquete porque no
        hay nada que firmar. La fuente tiene que tener `mensaje_id`;
        sin el, no hay forma de nombrar el archivo ni de enlazar la
        decision de vuelta al mensaje.
        """
        mensaje_id = fuente.mensaje_id
        if not mensaje_id:
            raise ValueError(
                "un paquete de curacion necesita un mensaje_id: sin el no se "
                "puede nombrar el archivo ni trazar la decision al mensaje"
            )
        return cls(
            mensaje_id=mensaje_id,
            formato=formato,
            fuente=fuente,
            activo=activo.model_dump(mode="json"),
            informe=informe_a_dict(informe),
            respuesta_cruda=respuesta_cruda,
            campos_ignorados=campos_ignorados,
            version_prompt=version_prompt,
        )

    def activo_tipado(self) -> ActivoBase:
        """El activo reconstruido con su clase, segun `formato`."""
        return TIPO_POR_FORMATO[self.formato].model_validate(self.activo)

    def informe_tipado(self) -> InformeGrounding:
        """El informe reconstruido."""
        return informe_de_dict(self.informe)

    def publicable(self) -> bool:
        """El gate, SIN reimplementarlo.

        Es `puede_publicarse`, la funcion unica del proyecto. Un paquete
        aprobado con grounding rechazado no es publicable, y uno con
        grounding impecable sin firmar tampoco.
        """
        return puede_publicarse(self.informe_tipado(), self.activo_tipado().curado)


def aplicar_decision(
    paquete: PaqueteCuracion,
    decision: Literal["aprobado", "rechazado"],
    *,
    motivo: str = "",
    curador: str = "",
) -> PaqueteCuracion:
    """Devuelve el paquete con la firma aplicada. No muta el original.

    Aprobar da vuelta `curado` a `True`; rechazar lo deja en `False` y
    exige un `motivo`, porque un rechazo sin razon no se puede auditar
    ni devolver al pipeline. La firma queda registrada con `curador` y
    `fecha_decision`.
    """
    if decision not in ("aprobado", "rechazado"):
        raise ValueError(
            f"decision '{decision}' no valida: solo 'aprobado' o 'rechazado'"
        )
    if decision == "rechazado" and not motivo.strip():
        raise ValueError(
            "un rechazo necesita un motivo: es la parte auditable de la decision"
        )

    activo = dict(paquete.activo)
    activo["curado"] = decision == "aprobado"

    return paquete.model_copy(
        update={
            "activo": activo,
            "decision": decision,
            "motivo": motivo.strip(),
            "curador": curador.strip(),
            "fecha_decision": datetime.now(),
        }
    )


# ── Persistencia ───────────────────────────────────────────────────


def _nombre_archivo(paquete: PaqueteCuracion) -> str:
    return f"{paquete.mensaje_id}.{paquete.formato}.json"


def guardar_paquete(paquete: PaqueteCuracion, directorio: str | Path) -> Path:
    """Escribe el paquete en el directorio. Un archivo por paquete."""
    destino = Path(directorio)
    destino.mkdir(parents=True, exist_ok=True)
    ruta = destino / _nombre_archivo(paquete)
    ruta.write_text(
        json.dumps(paquete.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return ruta


def cargar_paquetes(directorio: str | Path) -> list[PaqueteCuracion]:
    """Carga todos los paquetes de un directorio, en orden de archivo.

    Un directorio que no existe no es un error: es un pipeline que
    todavia no genero nada, y el panel arranca igual con una lista
    vacia. Lo que SI es un error es un archivo que no valida: ahi el
    paquete se rompe ruidosamente y se nombra, no se descarta en
    silencio.
    """
    raiz = Path(directorio)
    if not raiz.is_dir():
        return []
    paquetes: list[PaqueteCuracion] = []
    for archivo in sorted(raiz.glob("*.json")):
        paquetes.append(
            PaqueteCuracion.model_validate_json(archivo.read_text(encoding="utf-8"))
        )
    return paquetes
