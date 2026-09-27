"""
Sieve — the data contract.

Este modulo es el CORAZON del proyecto. Si algo anda mal, se rompe aca,
no en la logica de negocio.

Por que Pydantic y no dataclasses sueltas:

El documento de ONE define un contrato JSON exacto para la entrada y
para la salida. Si los modelos no validan contra ese esquema, el
requisito "devolver la salida estructurada" no esta cumplido por muy
bonito que sea el texto que genere el LLM.

Un Pydantic model convierte el contrato del PDF en algo que el
intérprete de Python hace cumplir en runtime. El JSON invalido falla
ruidosamente y en el borde del sistema, no tres capas mas adentro.
"""

from __future__ import annotations

import warnings
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

# ═══════════════════════════════════════════════════════════════════
#  ENTRADA
# ═══════════════════════════════════════════════════════════════════


class TipoInteraccion(StrEnum):
    """Clasificacion del mensaje. Extensible sin romper el contrato."""

    TESTIMONIO = "testimonio"
    PREGUNTA_TECNICA = "pregunta_tecnica"
    LOGRO = "logro"
    DUDA = "duda"
    RETROALIMENTACION = "retroalimentacion"
    CHAT = "chat"


class Interaccion(BaseModel):
    """Un mensaje de la comunidad, ya normalizado.

    Nota de diseno: `mensaje_id` NO existe en el JSON del documento.
    Se agrega porque un pipeline que manda 20 mensajes a un LLM necesita
    poder referenciar la respuesta a su entrada. El "traceability" de
    una linea hasta su origen es lo que hace auditable un sistema que
    inventa texto.
    """

    model_config = ConfigDict(extra="ignore")  #tolera campos extra del JSON

    autor: str = Field(..., min_length=1, max_length=120)
    canal: str = Field(..., min_length=1, max_length=120)
    tipo: TipoInteraccion = TipoInteraccion.CHAT
    texto: str = Field(..., min_length=1)
    timestamp: datetime | None = None

    # Metadata de proceso, no del dato de origen
    mensaje_id: str | None = None
    relevant: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("texto")
    @classmethod
    def _no_vacio(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("el texto no puede ser solo espacios")
        return v.strip()

    @field_validator("tipo", mode="before")
    @classmethod
    def _tipo_desconocido_cae_en_chat(cls, v: object) -> object:
        """Un tipo desconocido no debe reventar la ingesta.

        Los canales reales no respetan ninguna taxonomia. Si llega
        'pregunta' o 'logro!!!' y el enum no lo conoce, abortar la
        ingesta entera por una etiqueta es la decision equivocada: la
        tipificacion fina la hace el LLM en la etapa de analisis. Acá
        solo se preserva el dato; no se pierde nada.
        """
        # Todo lo que no sea texto (None, 7, [], {}) cae en CHAT. Un
        # tipo no textual no tiene forma de ser una categoría.
        if not isinstance(v, str):
            return TipoInteraccion.CHAT
        try:
            return TipoInteraccion(v)
        except ValueError:
            return TipoInteraccion.CHAT


class LoteComunidad(BaseModel):
    """La peticion completa. Es el objeto de entrada del endpoint."""

    origen_comunidad: str = Field(..., min_length=1)
    periodo_referencia: str = Field(..., min_length=1)
    interacciones: list[Interaccion] = Field(..., min_length=1)

    @field_validator("interacciones")
    @classmethod
    def _limita_tamano(cls, v: list[Interaccion]) -> list[Interaccion]:
        # Protege contra un lote Gigabyte que reviente la API y el rate limit.
        from sieve.config import settings

        if len(v) > settings.max_batch_size:
            raise ValueError(
                f"lote de {len(v)} interacciones excede "
                f"MAX_BATCH_SIZE={settings.max_batch_size}"
            )
        return v


# ═══════════════════════════════════════════════════════════════════
#  ANALISIS — lo que produce el LLM
# ═══════════════════════════════════════════════════════════════════


class Sentimiento(StrEnum):
    """Escala de 5 niveles, no binaria.

    El documento usa el literal 'Altamente Positivo' en su ejemplo de
    salida. Internamente usamos snake_case porque es lo correcto en
    Python, y `_missing_` traduce el vocabulario del documento en
    ambos sentidos.

    Con 3 niveles (pos / neutro / neg) se pierde la distincion entre
    "estuvo bien" y "contrataron a la estudiante" — y esa distincion es
    justamente la que decide si el mensaje se convierte en un caso de
    exito.
    """

    MUY_NEGATIVO = "muy_negativo"
    NEGATIVO = "negativo"
    NEUTRO = "neutro"
    POSITIVO = "positivo"
    MUY_POSITIVO = "muy_positivo"

    @classmethod
    def _missing_(cls, value: object) -> Sentimiento | None:
        """Acepta el vocabulario del documento ONE.

        'Altamente Positivo' y 'muy_positivo' son el mismo dato escrito
        de dos formas. Un enum sin esto revienta con un error de
        validacion opaco en la frontera del sistema.
        """
        if not isinstance(value, str):
            return None
        clave = value.strip().lower().replace(" ", "_").replace("-", "_")
        for miembro in cls:
            if miembro.value == clave:
                return miembro
        # El documento dice "Altamente Positivo" donde el enum dice
        # "muy_positivo". Son synonyms, no valores distintos.
        alias = {
            "altamente_positivo": cls.MUY_POSITIVO,
            "altamente_negativo": cls.MUY_NEGATIVO,
            "muy_alto": cls.MUY_POSITIVO,
        }
        return alias.get(clave)


#: Vocabulario exacto del documento, para serializar la salida.
#: El contrato se evalua a ojo: si el JSON dice "muy_positivo" donde el
#: documento dice "Altamente Positivo", alguien lo va a marcar.
SENTIMIENTO_A_DOCUMENTO: dict[Sentimiento, str] = {
    Sentimiento.MUY_NEGATIVO: "Muy Negativo",
    Sentimiento.NEGATIVO: "Negativo",
    Sentimiento.NEUTRO: "Neutro",
    Sentimiento.POSITIVO: "Positivo",
    Sentimiento.MUY_POSITIVO: "Altamente Positivo",
}


class Analisis(BaseModel):
    """El resultado del LLM para UNA interaccion.

    Todos los campos de scoring se devuelven por separado y explicados.
    La explicacion no es decorativa: es lo que permite escribir un
    README honesto y lo que hace auditable una decision automatica.
    """

    mensaje_id: str | None = None
    sentimiento: Sentimiento = Sentimiento.NEUTRO
    score_sentimiento: float = Field(..., ge=-1.0, le=1.0)
    temas: list[str] = Field(default_factory=list, max_length=6)
    es_logro: bool = False
    es_pregunta: bool = False
    citas: list[str] = Field(default_factory=list, max_length=5)
    relevant: float = Field(default=0.0, ge=0.0, le=1.0)
    razon_relevante: str = ""


# ═══════════════════════════════════════════════════════════════════
#  ACTIVOS — lo que se publica
# ═══════════════════════════════════════════════════════════════════


class TipoActivo(StrEnum):
    """Las ramas del router condicional."""

    CASO_EXITO = "caso_exito"
    FAQ = "faq"
    HIGHLIGHT = "highlight"


class ActivoBase(BaseModel):
    """Base comun a todo activo generado.

    Solo campos que el documento define para TODOS los activos.

    `canal_recomendado` y `potencial_engagement` NO viven aca: el
    documento solo los declara en `post_linkedin`. Ponerlos en la base
    obligaria a inventarlos en el newsletter y en el FAQ, y el primer
    test de contrato lo detecta como campo faltante. Menos campos
    inventados = contrato mas honesto.

    `confianza` y `curado` son extensiones propias: un motor que
    genera texto sin que nadie pueda revisarlo es una bomba de
    reputacion. `curado=False` significa 'esto es un borrador, un
    humano tiene que firmarlo'.
    """

    model_config = ConfigDict(extra="forbid")

    confianza: float = Field(default=0.5, ge=0.0, le=1.0)
    curado: bool = False
    fuentes: list[str] = Field(default_factory=list)


with warnings.catch_warnings():
    # `copy` es el nombre que exige el documento ONE. Pydantic avisa de que
    # tapa `BaseModel.copy()`, y esta bien: el contrato manda sobre la
    # estetica de la API. `BaseModel.copy()` esta deprecado en Pydantic v2
    # (`model_copy()` lo reemplaza), asi que en la practica no se pierde
    # ninguna capacidad real. Se silencia aca, y no solo en pytest, para
    # que importar el paquete no ensucie la salida de la CLI.
    warnings.filterwarnings("ignore", message='Field name "copy".*', category=UserWarning)

    class PostLinkedin(ActivoBase):
        titulo: str = Field(..., min_length=5)
        # mypy protestona en esta linea y tiene razon en que el nombre
        # tapa `BaseModel.copy()`. El nombre lo impone el contrato del
        # documento ONE, asi que la decision es del contrato, no mia: por
        # abajo va la supresion puntual. En Pydantic v2 el metodo
        # equivalente es `model_copy()`.
        copy: str = Field(..., min_length=20)  # type: ignore[assignment]
        hashtags: list[str] = Field(default_factory=list, max_length=8)
        canal_recomendado: str
        potencial_engagement: Literal["Alto", "Medio", "Bajo"] = "Medio"


class Newsletter(ActivoBase):
    seccion: str
    titular: str
    resumen: str = Field(..., min_length=20)


class SugerenciaFaq(ActivoBase):
    tema: str
    origen: str
    status: Literal["derivado_a_mentoria", "listo_para_publicar"] = "listo_para_publicar"


class ActivosGenerados(BaseModel):
    """Agrupa los activos. Los campos son opcionales a proposito:

    el router decide que se genera. En un lote donde nadie logro un
    hito, no hay post de LinkedIn de caso de exito — y el modelo no
    deberia inventar uno para llenar el hueco.

    `extra="forbid"` es lo mas importante de esta clase. Con el
    default ("ignore"), una clave mal escrita en el contrato se
    descarta en silencio y el campo aparece como si nunca hubiera
    llegado. Eso casi pasa aca: el documento escribe
    "destreza_newsletter_semanal" con una "e" de mas, el modelo la
    ignoraba, y el newsletter salia vacio sin una sola excepcion.
    Un contrato que falla en silencio no es un contrato. Ahora falla
    ruidosamente.

    Sobre el alias: "destreza_newsletter_semanal" parece un typo por
    "destaca", pero es LA clave que define el documento. Se emite
    exactamente esa para que el JSON sea evaluable contra el
    documento, mientras el atributo interno se llama
    `destreza_newsletter_semanal` para no arrastrar la falta de
    ortografia al codigo.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    post_linkedin: PostLinkedin | None = None
    destaque_newsletter_semanal: Newsletter | None = Field(
        default=None, alias="destreza_newsletter_semanal"
    )
    sugerencia_contenido_faq: SugerenciaFaq | None = None


# ═══════════════════════════════════════════════════════════════════
#  RESPUESTA — el contrato de salida del documento
# ═══════════════════════════════════════════════════════════════════


class ResumenComunidad(BaseModel):
    total_interacciones_procesadas: int = Field(..., ge=0)
    sentimiento_predominante: Sentimiento = Sentimiento.NEUTRO
    temas_principales: list[str] = Field(default_factory=list, max_length=8)
    distribucion_sentimiento: dict[str, int] = Field(default_factory=dict)
    lineas_tiempo: list[str] = Field(default_factory=list)

    @field_serializer("sentimiento_predominante")
    def _serializa_vocabulario_documento(self, v: Sentimiento) -> str:
        """Emite 'Altamente Positivo', no 'muy_positivo'.

        El enum interno es snake_case porque es lo correcto en Python.
        El contrato de salida es vocabulario humano, porque es lo que
        pide el documento. Son dos readerships distintos y cada uno
        recibe el idioma que le corresponde.
        """
        return SENTIMIENTO_A_DOCUMENTO[v]


class AlmacenamientoOci(BaseModel):
    """Trazabilidad de la persistencia.

    `status` tiene que poder ser 'fallido'. Ocultar un error de
    escritura para que la demo se vea linda es exactamente el tipo de
    mentira que hace que un proyecto no sea confiable.
    """

    bucket: str
    ruta_objeto: str
    status: Literal["guardado_con_exito", "fallido", "simulado"]
    url_preautenticada: str | None = None


class RespuestaSieve(BaseModel):
    """La respuesta final. Este modelo ES el entregable del MVP.

    Los nombres de los campos son los del documento, a proposito. Cambiar
    'sentimiento_predominante' por 'dominant_sentiment' hace el proyecto
    mas lindo de leer y deja de cumplir el contrato.
    """

    status: Literal["exito", "error_parcial", "error"] = "exito"
    resumen_comunidad: ResumenComunidad
    activos_distribucion_generados: ActivosGenerados
    almacenamiento_oci: AlmacenamientoOci
    # Trazabilidad — fuera del contrato del documento, pero es lo que
    # convierte una demo en un sistema auditable.
    metadata: dict[str, Any] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════
#  Validacion del contrato contra el JSON del documento
# ═══════════════════════════════════════════════════════════════════


def validar_contrato_documento(payload: dict[str, Any]) -> RespuestaSieve:
    """Valida un payload contra el esquema de salida del documento ONE.

    Sirve como test y como punto de entrada: si esto pasa, el formato
    es correcto. Si falla, el traceback de Pydantic dice exactamente que
    campo se rompio y por que.
    """
    return RespuestaSieve.model_validate(payload)
