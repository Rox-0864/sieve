"""Perfil de organizacion: el conocimiento de MARCA, como dato.

## El problema que este modulo resuelve

El motor de grounding necesitaba saber cosas de una organizacion
concreta: que palabras son institutional para ella, que siglas se
expanden como, que supuestos estan declarados, como se llaman sus
canales. Todo eso estaba escrito en `grounding.py` y `prompts.py`.

Consecuencia: el motor sabia que "los miembros de la comunidad son
estudiantes del programa". Eso no es una regla de grounding, es un
dato de un cliente. El dia que haya un segundo cliente, el motor
mentiria sobre uno de los dos, y el error seria invisible: un check
que rechaza un post legitimo de la organizacion equivocada.

La solucion no es "sacarlo y que el motor no sepa nada", porque
entonces los checks serian incesarios: sin un perfil, "LangChain" en
un post de un proyecto de LangChain seria una entidad inventada, y
"Felicitaciones" seria una empresa. El motor NECESITA un perfil.
Lo que no deberia es tener UN perfil, fijo, escrito a mano.

## La regla que gobierna el diseno

Un supuesto institucional autoriza decir cosas sobre la
ORGANIZACION. Nunca sobre una PERSONA.

    "Somos el programa ONE"                    -> se puede, esta aca
    "Mariana es nuestra estudiante"            -> NO: sale del mensaje
                                                 de Mariana, no de aca

Esta es la linea que el perfil tiene que hacer posible de mantener, y
por eso los supuestos estan indexados por VOCABLO y no por "la
comunidad". Ver la nota en `perfiles/one.toml`.

## Ademas: el perfil declara lo que NO sabe

`verificar_perfil()` lista las claves `[PENDIENTE]`. Un motor con
supuestos silenciosos es un motor que miente sobre lo que sabe; lo
minimo que le debemos a quien lo opera es una lista de los huecos.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Iterator
from functools import cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: Donde viven los perfiles. Relativo a la raiz del proyecto.
DIRECTORIO_PERFILES = Path(__file__).resolve().parent.parent.parent / "perfiles"

#: El perfil que se usa cuando nadie pasa uno explicito. Los tests
#: corren contra este, y por eso el motor se puede testear sin
#: depender de los datos de un cliente.
NOMBRE_POR_DEFECTO = "por_defecto"


class Identidad(BaseModel):
    """Como se presenta la marca ante el modelo."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sector: str
    audiencia: tuple[str, ...] = ()
    #: La identidad que se inyecta en el prompt. Un solo lugar del
    #: codigo decide como se presenta la marca.
    prompt: str
    alianzas: tuple[str, ...] = ()
    #: Definicion textual de la organizacion sobre si misma, cuando la
    #: publica. Una generalizacion nuestra seria peor que esto.
    definicion_oficial: str = ""
    #: Las ramas del programa. Importa porque mezclar dos cohorts es
    #: un error de segmentacion que no se ve en la redaccion.
    rutas: tuple[str, ...] = ()
    #: Comunidades con nombre propio. Una alumni no es una estudiante.
    comunidades: tuple[str, ...] = ()


class Vocabulario(BaseModel):
    """Palabras que esta organizacion puede nombrar sin respaldo.

    Separado en tres grupos a proposito. Mezclarlos fue un error
    anterior: tratabamos el nombre de la marca y el nombre de una
    herramienta del sector como si fueran la misma cosa.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Identidad de la organizacion. Siempre cierto.
    marca: frozenset[str] = frozenset()
    #: Stack que la organizacion declara propio.
    marca_tecnica: frozenset[str] = frozenset()
    #: Encuadre institucional: no son entidades, son maneras de
    #: contar. "Historia como la de Mariana" no es una empresa.
    autodescripcion: frozenset[str] = frozenset()

    @property
    def institucional(self) -> frozenset[str]:
        """Lo que se acepta sin estar en la fuente del mensaje."""
        return self.marca | self.marca_tecnica | self.autodescripcion


class SupuestosDeclarados(BaseModel):
    """Palabras de contexto institucional usadas sin respaldo textual.

    El check las reporta como `aprobado_con_supuestos` en vez de
    rechazarlas: son encuadre declarado, no informacion. La
    justificacion va SIEMPRE acompanada, porque un supuesto sin
    justificacion es un supuesto invisible.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    justificaciones: dict[str, str] = Field(default_factory=dict)

    def __contains__(self, palabra: str) -> bool:
        return palabra in self.justificaciones

    # Pydantic ya define `__iter__` (para `model_dump`) y devuelve tuplas
    # de campos. Acá se itera el DICCIONARIO de justificaciones, que es
    # lo que el resto del proyecto espera de "los supuestos de un
    # perfil", y por eso el ignore: aca se cambia el contrato a
    # proposito y no por descuido.
    def __iter__(self) -> Iterator[str]:  # type: ignore[override]
        return iter(self.justificaciones)

    def justificacion_de(self, palabra: str) -> str | None:
        return self.justificaciones.get(palabra)

    def __len__(self) -> int:
        return len(self.justificaciones)

    def usadas(self, palabras: Iterable[str]) -> list[str]:
        """Supuestos presentes en una coleccion de palabras."""
        return sorted({p for p in palabras if p in self})


class Restricciones(BaseModel):
    """Lo que la organizacion no puede decir, y lo que no definio."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prohibido: tuple[str, ...] = ()
    #: Huecos declarados. Que un motor liste lo que no sabe es la
    #: diferencia entre una herramienta y una caja negra.
    pendiente_de_definir: tuple[str, ...] = ()
    #: Preguntas que la investigacion YA respondio, con la respuesta.
    #: Vive en el perfil y no en un comentario porque un humano la va
    #: a necesitar: volver a preguntar lo que ya se contesto es la
    #: forma mas comun de que una reunion seretch.
    resuelto_por_web: tuple[str, ...] = ()


class Vigilancia(BaseModel):
    """Terminos que un humano tiene que ver antes de publicar.

    Distinto de `Restricciones.prohibido`: estos no estan prohibidos,
    tocan un borde que la organizacion todavia no defini. Se marcan,
    no se bloquean.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    terminos: tuple[str, ...] = ()
    motivo: str = ""


class PerfilOrganizacion(BaseModel):
    """Conocimiento de marca de UNA organizacion.

    Inmutable. Dos perfiles no se pisan, y un check que recibe el
    perfil como parametro no puede dejar que el llamador lo modifique
    a mitad de una verificacion.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    nombre: str
    descripcion: str = ""
    identidad: Identidad
    vocabulario: Vocabulario = Vocabulario()
    expansiones: dict[str, str] = Field(default_factory=dict)
    abreviaturas: dict[str, str] = Field(default_factory=dict)
    supuestos: SupuestosDeclarados = SupuestosDeclarados()
    constantes: dict[str, str] = Field(default_factory=dict)
    tono: dict[str, str] = Field(default_factory=dict)
    restricciones: Restricciones = Restricciones()
    vigilancia: Vigilancia = Vigilancia()

    # ── Derivados que los checks consultan ───────────────────────

    @property
    def institutional_palabras(self) -> frozenset[str]:
        """Alias explicito. "Institucional" se lee mejor que
        "vocabulario" cuando lo que uno quiere es la lista entera."""
        return self.vocabulario.institucional

    def constante(self, nombre: str, por_defecto: str = "") -> str:
        """Lee una constante del contrato con default explicito.

        Devolver un default en vez de KeyError importa: un perfil
        incompleto tiene que degradar a "sin dato", no romper el
        pipeline entero.
        """
        return self.constantes.get(nombre, por_defecto)

    def tono_de(self, canal: str) -> str:
        return self.tono.get(canal, "")

    @classmethod
    def de_toml(cls, datos: dict[str, Any]) -> PerfilOrganizacion:
        """Construye el perfil desde el diccionario de `tomllib`."""
        return cls.model_validate(
            {
                "nombre": datos["nombre"],
                "descripcion": datos.get("descripcion", ""),
                "vocabulario": _a_lista(datos.get("vocabulario", {})),
                "expansiones": datos.get("expansiones", {}),
                "abreviaturas": datos.get("abreviaturas", {}),
                "supuestos": {
                    "justificaciones": datos.get("supuestos", {}),
                },
                "constantes": datos.get("constantes", {}),
                "tono": datos.get("tono", {}),
                "identidad": {
                    **datos["identidad"],
                    "audiencia": tuple(datos["identidad"].get("audiencia", ())),
                    "alianzas": tuple(datos["identidad"].get("alianzas", ())),
                    "rutas": tuple(datos["identidad"].get("rutas", ())),
                    "comunidades": tuple(
                        datos["identidad"].get("comunidades", ())
                    ),
                },
                "restricciones": _a_lista(datos.get("restricciones", {})),
                "vigilancia": {
                    "terminos": tuple(
                        datos.get("vigilancia", {}).get("terminos", ())
                    ),
                    "motivo": datos.get("vigilancia", {}).get("motivo", ""),
                },
            }
        )


def _a_lista(seccion: dict[str, Any]) -> dict[str, tuple[Any, ...]]:
    """Convierte listas de TOML en tuplas, para que Pydantic las acepte
    como campos inmutables."""
    return {k: tuple(v) for k, v in seccion.items() if isinstance(v, list)}


# ── Perfil por defecto ────────────────────────────────────────────────
#
# Deliberadamente VACIO de conocimiento de cliente. Los tests corren
# contra este: si un test pasa con el perfil de ONE, es porque depende
# de un cliente y el test esta mal escrito. Si falla, el motor esta
# realmente funcionando.


def _perfil_por_defecto() -> PerfilOrganizacion:
    """Perfil neutro: no sabe de ninguna organizacion.

    Deliberadamente sin vocabulario, sin expansiones y sin supuestos.
    Es lo que hace testeable al motor: si un check pasa con este
    perfil, funciona sin ningun conocimiento de cliente, y si falla,
    la razon esta en la logica y no en los datos.

    La identidad si dice algo generico, porque un prompt de una linea
    no es un prompt. Lo que NO dice es el nombre de nadie.
    """
    return PerfilOrganizacion(
        nombre=NOMBRE_POR_DEFECTO,
        descripcion="Perfil neutro. No sabe de ninguna organizacion.",
        identidad=Identidad(
            sector="desconocido",
            prompt=(
                "Sos un redactor. Escribis para una comunidad, y el unico "
                "material que tenes es el texto de una persona."
            ),
        ),
    )


@cache
def cargar_perfil(nombre: str = "one") -> PerfilOrganizacion:
    """Carga un perfil desde `perfiles/<nombre>.toml`, con cache.

    La cache existe porque el perfil es inmutable y se lee en cada
    verificacion de grounding. Parsear TOML por check seria tirar
    CPU para algo que no cambia.
    """
    if nombre == NOMBRE_POR_DEFECTO:
        return _perfil_por_defecto()
    ruta = DIRECTORIO_PERFILES / f"{nombre}.toml"
    if not ruta.exists():
        disponibles = sorted(
            p.stem for p in DIRECTORIO_PERFILES.glob("*.toml")
        )
        raise FileNotFoundError(
            f"No existe el perfil {nombre!r} en {DIRECTORIO_PERFILES}. "
            f"Disponibles: {disponibles or ['ninguno']}"
        )
    with ruta.open("rb") as fh:
        return PerfilOrganizacion.de_toml(tomllib.load(fh))


def listar_perfiles() -> list[str]:
    """Nombres de perfil disponibles en disco."""
    return sorted(p.stem for p in DIRECTORIO_PERFILES.glob("*.toml"))


def verificar_perfil(perfil: PerfilOrganizacion) -> list[str]:
    """Lista los huecos declarados de un perfil.

    Lo que devuelve no son errores: es lo que la organizacion todavia
    no defini. Que aparezca en un reporte es el punto.
    """
    huecos = list(perfil.restricciones.pendiente_de_definir)
    if not perfil.vocabulario.marca:
        huecos.append("vocabulario.marca vacio: ningun nombre propio")
    if not perfil.constantes:
        huecos.append("constantes vacio: el contrato va a usar defaults")
    if not perfil.tono:
        huecos.append("tono vacio: un prompt para todos los canales")
    return huecos
