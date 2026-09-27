"""
Generacion de activos: el modelo propone, el grounding lo cross-examina.

Por que el generador NO es "un prompt y un modelo"
---------------------------------------------------
Porque un prompt que dice "no inventes" es una peticion, y un modelo de
3B parametros la ignora en cuanto tiene que llenar un campo. La defensa
no es la instruccion: es la arquitectura.

El error de diseno mas comun en un generador de contenido es delegar en
el prompt toda la seguridad. Un prompt no se testea, no se versiona de
forma que puedas comparar dos salidas, y no se puede hacer fallar en un
test. Acá el prompt ES la capa que mas se puede relajar, porque hay dos
capas debajo que no dependen de que el modelo obedezca:

1. **Se le pide menos.** El modelo escribe 3 de los 9 campos que tienen
   los tres activos. Los otros 6 los pone el sistema, mecanicamente. Un
   campo que el modelo no toca es un campo que no puede inventar, y esa
   es la unica defensa que no depende de su buena voluntad.

2. **El texto de la persona va citado**, no recontado. Ver `prompts.py`.

3. **Se verifica el artefacto.** `grounding.verificar_activo` lee los
   campos de texto que SALIERON y los contrasta contra la fuente.

Este modulo no tiene logica de verificacion propia. Ni una linea. La
reimplementaria en peor, y lo peor de una reimplementacion parcial es
que el dia que se agrega un check nuevo a `grounding.py` este modulo
sigue sque se verifica sin el, en silencio, y nadie se entera hasta que
publica un activo con un numero inventado.

Que el generador pueda no devolver nada
--------------------------------------
`ResultadoGeneracion.activo` es `T | None`. Si el grounding rechaza, el
activo es `None` y el informe explica por que. No se repara, no se
reintenta a ciegas, no se devuelve "lo mejor que pudo salir".

Un pipeline que no puede devolver nada esta obligado a inventar para
llenar el hueco, y el hueco se llena con lo mas riesgos: un testimonio
con el nombre de una persona real.

El FAQ nunca se publica solo
---------------------------
El contrato declara `status: Literal["derivado_a_mentoria",
"listo_para_publicar"] = "listo_para_publicar"`. O sea: el default del
contrato es el estado PELIGROSO. Cualquier cosa que construya un
`SugerenciaFaq` sin decir el status produce una FAQ que parece publicable
y no tiene respuesta revisada por nadie. Este modulo lo sobreescribe
siempre, y es la razon por la que el generador no se limita a validar
el dict del modelo sino a armar el activo entero.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import ValidationError

from sieve.analysis.base import ProveedorLLM, ResultadoAnalisis
from sieve.analysis.extract import JsonIlegibleError, extraer_json
from sieve.generation.grounding import (
    InformeGrounding,
    Verificacion,
    puede_publicarse,
    verificar_activo,
)
from sieve.generation.prompts import (
    VERSION_PROMPT_GENERACION,
    construir_prompt_faq,
    construir_prompt_newsletter,
    construir_prompt_post_linkedin,
)
from sieve.models import (
    ActivoBase,
    Interaccion,
    Newsletter,
    PostLinkedin,
    SugerenciaFaq,
)

T = TypeVar("T", bound=ActivoBase)

#: El identico del brief. Vive aca y no en el prompt porque es un dato
#: del CONTRATO: si cambia, hay que volver a generar todo.
CANAL_LINKEDIN = "LinkedIn Oficial"
SECCION_NEWSLETTER = "Logro de la Semana"


class ErrorDeGeneracionError(RuntimeError):
    """El proveedor no devolvio algo usable.

    NO es lo mismo que un rechazo de grounding, y la distincion se
    respeta en el tipo de retorno: un JSON ilegible es un problema del
    proveedor, reintentable, y un activo rechazado es una decision del
    sistema que no se reintenta. Confundirlos hace que un pipeline
    "reintente hasta que pase" termine publicando el activo de otra
    fuente.
    """


@dataclass(frozen=True)
class ResultadoGeneracion(Generic[T]):
    """Un activo, o nada. Y siempre el reporte de por que.

    `publicable` exige las DOS condiciones, igual que en M4: grounding
    limpio Y firma humana. Y como el panel de curaduria todavia no
    existe, en este milestone `publicable` es SIEMPRE `False`: el
    generador no firma nada. Que eso sea cierto por construccion y no
    por costumbre es lo que hace segura la etapa siguiente.
    """

    activo: T | None
    informe: InformeGrounding
    #: `None` cuando el activo llego bien y el grounding lo approve.
    motivo_fallo: str | None = None
    #: Claves que el modelo escribio y no se le habian pedido, y que no
    #: son de sistema. Se descartan y se anotan, NO se rechazan: ver
    #: `_generar`.
    campos_ignorados: tuple[str, ...] = ()
    #: Lo que el modelo contesto, textual. Sin esto, un rechazo se
    #: explica con el hallazgo del check y no se puede auditar por que
    #: el modelo escribio eso: "el modelo dijo X, el sistema lo rejects"
    #: es una cadena de evidencia, y sin el crudo solo queda la
    #: conclusion.
    respuesta_cruda: str = ""
    version_prompt: str = VERSION_PROMPT_GENERACION

    @property
    def publicable(self) -> bool:
        return self.activo is not None and puede_publicarse(
            self.informe, self.activo.curado
        )

    @property
    def rechazado_por_grounding(self) -> bool:
        """Distinguible de un fallo de proveedor, que si se reintenta."""
        return self.activo is None and self.motivo_fallo is None

    def resumen(self) -> str:
        partes = []
        if self.motivo_fallo:
            partes.append(f"SIN ACTIVO (fallo de generacion): {self.motivo_fallo}")
        elif self.activo is None:
            partes.append("SIN ACTIVO (grounding rechazado)")
        else:
            partes.append(type(self.activo).__name__)
        if self.campos_ignorados:
            partes.append(f"campos ignorados: {', '.join(self.campos_ignorados)}")
        if not self.publicable:
            partes.append("NO publicable: falta firma de curaduria")
        return "\n".join(partes) + "\n" + self.informe.resumen()


#: Atajo de nombre. Los tres metodos publicos devuelven la base y no el
#: tipo exacto, porque el formato se resuelve por string en runtime: prometer
#: `PostLinkedin` aca seria una mentira que mypy no puede verificar. Para
#: estrechar, `isinstance`.
Resultado = ResultadoGeneracion


# ── Que escribe el modelo y que escribe el sistema ────────────────
#
# Esta tabla es la arquitectura del modulo. Es lo que hay que leer para
# entender de donde sale cada campo de un activo.


@dataclass(frozen=True)
class _Especificacion(Generic[T]):
    clase: type[T]
    campos_del_modelo: tuple[str, ...]
    campos_del_sistema: tuple[str, ...]
    construir_prompt: Callable[[Interaccion], str]
    max_tokens: int


def _confianza(resultado: ResultadoAnalisis) -> float:
    """De donde salio el analisis, en un numero.

    NO mide que tan bien quedo el texto: eso lo decide el grounding, y
    es un eje distinto. Un activo puede ser veraz y salir de un analisis
    heuristico que clasifico de neutro un texto que decia "apenas 3
    semanas". La veracidad no dice nada de eso, y por eso son dos
    campos y no uno.

    Nunca 1.0: un texto puede ser veraz y aun asi sonar a maquina, y un
    1.0 se lee como "no hace falta revisarlo", que es justo la
    conclusion que este modulo no quiere que se saquen solos.
    """
    if resultado.procedencia == "llm" and not resultado.citas_descartadas:
        return 0.9
    if resultado.procedencia == "mixto":
        return 0.7
    return 0.4


def _fuentes(fuente: Interaccion) -> list[str]:
    return [fuente.mensaje_id] if fuente.mensaje_id else []


def _sistema_post(resultado: ResultadoAnalisis) -> dict[str, object]:
    return {
        "canal_recomendado": CANAL_LINKEDIN,
        "confianza": _confianza(resultado),
        "curado": False,
        "fuentes": _fuentes(resultado.interaccion),
    }


def _sistema_newsletter(resultado: ResultadoAnalisis) -> dict[str, object]:
    return {
        "seccion": SECCION_NEWSLETTER,
        "confianza": _confianza(resultado),
        "curado": False,
        "fuentes": _fuentes(resultado.interaccion),
    }


def _sistema_faq(resultado: ResultadoAnalisis) -> dict[str, object]:
    fuente = resultado.interaccion
    return {
        # Copia un dato que YA tenemos. Dejarlo en manos del modelo es
        # dejarle inventar nombres: un origen equivocado manda el
        # nombre de una persona al activo de otra, y el texto fluye
        # igual porque el nombre es el unico token que el lector no
        # checkea.
        "origen": f"Duda planteada por {fuente.autor} en {fuente.canal}",
        # Y el status tampoco. Una respuesta tecnica escrita por un
        # modelo de 3B a una duda real es fabricacion con formato de
        # respuesta, y el default del contrato es
        # `listo_para_publicar`. Este es el overwrite que evita que un
        # contrato razonable produzca el peor resultado posible.
        "status": "derivado_a_mentoria",
        "confianza": _confianza(resultado),
        "curado": False,
        "fuentes": _fuentes(fuente),
    }


#: `formato` -> quien escribe los campos que NO son del modelo.
_SISTEMA: dict[str, Callable[[ResultadoAnalisis], dict[str, object]]] = {
    "post_linkedin": _sistema_post,
    "newsletter": _sistema_newsletter,
    "faq": _sistema_faq,
}

_ESPECIFICACIONES: dict[str, _Especificacion[ActivoBase]] = {
    "post_linkedin": _Especificacion(
        clase=PostLinkedin,
        campos_del_modelo=("titulo", "copy", "hashtags", "potencial_engagement"),
        campos_del_sistema=("canal_recomendado", "confianza", "curado", "fuentes"),
        construir_prompt=construir_prompt_post_linkedin,
        max_tokens=700,
    ),
    "newsletter": _Especificacion(
        clase=Newsletter,
        campos_del_modelo=("titular", "resumen"),
        campos_del_sistema=("seccion", "confianza", "curado", "fuentes"),
        construir_prompt=construir_prompt_newsletter,
        max_tokens=500,
    ),
    "faq": _Especificacion(
        clase=SugerenciaFaq,
        campos_del_modelo=("tema",),
        campos_del_sistema=("origen", "status", "confianza", "curado", "fuentes"),
        construir_prompt=construir_prompt_faq,
        max_tokens=300,
    ),
}


class GeneradorActivos:
    """Escribe los tres formatos del brief y los pasa por el grounding.

    No cachea y no reintenta. Las dos decisiones son deliberadas:

    - Sin cache, porque un activo cacheado de una corrida anterior se
      genero con otra version de prompt, y `version_prompt` esta en el
      resultado justamente para que esa mezcla sea visible. Un cache sin
      clave de version es una forma de mezclar prompts en silencio.
    - Sin reintenta a ciegas, porque la segunda respuesta con otra
      semilla es OTRO activo, no una mejor version del mismo. Y como el
      LLM en CPU no es determinista, "reintentar" produce algo distinto
      cada vez, lo que hace imposible afirmar que el rechazo fue del
      grounding y no de la muestra.
    """

    def __init__(self, proveedor: ProveedorLLM) -> None:
        self.proveedor = proveedor

    # -- API pública ------------------------------------------------
    #
    # Devuelven `Resultado[ActivoBase]`: el formato se elige por string en
    # runtime, asi que la clase exacta no se puede prometer en la firma. Para
    # tocar `titulo` o `status`, `isinstance` primero.

    def generar_post(self, resultado: ResultadoAnalisis) -> Resultado[ActivoBase]:
        return self._generar(resultado, "post_linkedin")

    def generar_newsletter(self, resultado: ResultadoAnalisis) -> Resultado[ActivoBase]:
        return self._generar(resultado, "newsletter")

    def generar_faq(self, resultado: ResultadoAnalisis) -> Resultado[ActivoBase]:
        return self._generar(resultado, "faq")

    # -- El pipeline, uno solo para los tres formatos ---------------

    def _pedir(self, prompt: str, *, max_tokens: int) -> dict[str, object]:
        if not self.proveedor.es_disponible():
            raise ErrorDeGeneracionError(
                f"proveedor {getattr(self.proveedor, 'nombre', '?')} no "
                "configurado: la generacion no tiene fallback y no se simula"
            )
        respuesta = self.proveedor.completar(prompt, max_tokens=max_tokens)
        try:
            crudo = extraer_json(respuesta.texto)
        except JsonIlegibleError as exc:
            raise ErrorDeGeneracionError(
                f"respuesta no parseable: {exc}"
            ) from exc
        if not isinstance(crudo, dict):
            raise ErrorDeGeneracionError(
                f"se esperaba un objeto JSON y vino {type(crudo).__name__}"
            )
        return crudo

    def _rechazo_de_campos(
        self, tocados: set[str], crudo_texto: str
    ) -> ResultadoGeneracion[ActivoBase]:
        return ResultadoGeneracion(
            activo=None,
            informe=InformeGrounding(
                verificaciones=[
                    Verificacion(
                        "campos del sistema",
                        ok=False,
                        hallazgos=[
                            f"el modelo escribio {sorted(tocados)}, que el sistema "
                            "reserva. No es ruido: es un intento de decidir un "
                            "campo del que no es dueño."
                        ],
                    )
                ]
            ),
            respuesta_cruda=crudo_texto,
        )

    def _generar(
        self, resultado: ResultadoAnalisis, formato: str
    ) -> ResultadoGeneracion[ActivoBase]:
        spec = _ESPECIFICACIONES[formato]
        fuente = resultado.interaccion
        crudo = self._pedir(
            spec.construir_prompt(fuente), max_tokens=spec.max_tokens
        )
        crudo_texto = json.dumps(crudo, ensure_ascii=False)

        # Un modelo que escribe un campo del SISTEMA esta tratando de
        # decidir algo que no le corresponde, y eso se rechaza con
        # nombre. Un modelo que escribe cualquier otra clave de mas es
        # ruido, y el ruido se descarta anotado en vez de matar el
        # activo: un check que llora lobo por una clave de mas se
        # desacredita, y con el se van los rechazos que si importan.
        tocados = set(crudo) & set(spec.campos_del_sistema)
        if tocados:
            return self._rechazo_de_campos(tocados, crudo_texto)
        ignorados = tuple(
            sorted(set(crudo) - set(spec.campos_del_modelo) - tocados)
        )

        datos: dict[str, object] = {
            clave: crudo[clave] for clave in spec.campos_del_modelo if clave in crudo
        }
        datos.update(_SISTEMA[formato](resultado))

        try:
            activo = spec.clase.model_validate(datos)
        except ValidationError as exc:
            campos = ", ".join(
                f"{'.'.join(str(p) for p in e['loc'])}" for e in exc.errors()
            )
            raise ErrorDeGeneracionError(
                f"el modelo no lleno los campos del contrato: {campos}"
            ) from exc

        try:
            informe = verificar_activo(
                activo, fuente, campos_del_modelo=spec.campos_del_modelo
            )
        except ValueError as exc:
            # Pasa con `SugerenciaFaq.tema = "IA"`: el contrato no pone
            # min_length en `tema`, asi que un titulo de dos letras es
            # un `SugerenciaFaq` valido, y `verificar_activo` no tiene
            # contra que comparar. Sin este catch el pipeline revienta
            # con un ValueError sin contexto en vez de decir "el modelo
            # no escribio nada verificable".
            raise ErrorDeGeneracionError(
                f"el {formato} no tiene texto verificable: {exc}"
            ) from exc
        if informe.veredicto == "rechazado":
            return ResultadoGeneracion(
                activo=None,
                informe=informe,
                campos_ignorados=ignorados,
                respuesta_cruda=crudo_texto,
            )
        return ResultadoGeneracion(
            activo=activo,
            informe=informe,
            campos_ignorados=ignorados,
            respuesta_cruda=crudo_texto,
        )
