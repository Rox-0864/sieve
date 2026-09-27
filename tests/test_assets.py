"""
Tests del generador de activos.

Todos offline. El proveedor es un doble que devuelve un JSON escrito a
mano, y justamente por eso los tests no dependen de Qwen: un test que
depende del modelo pasa hoy y falla manana por muestreo, y un test que falla
por eso entrena al equipo a ignorar tests rojos.

La razon de que el doble devuelva JSON A MANO y no "lo que el prompt
pide" es que los JSON de estos tests estan escritos para FALLAR. Esos
son los casos que importan: un generador al que solo se le prueban
respuestas correctas no esta testeado.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from sieve.analysis.base import Procedencia, ResultadoAnalisis, ResultadoLLM
from sieve.generation.assets import (
    CANAL_LINKEDIN,
    SECCION_NEWSLETTER,
    ErrorDeGeneracionError,
    GeneradorActivos,
)
from sieve.generation.prompts import (
    construir_prompt_faq,
    construir_prompt_newsletter,
    construir_prompt_post_linkedin,
)
from sieve.models import Analisis, Interaccion, PostLinkedin

# ── El doble ───────────────────────────────────────────────────────


class ProveedorDoble:
    """Devuelve lo que le pusiste, y cuenta las llamadas."""

    def __init__(self, respuesta: str, *, disponible: bool = True) -> None:
        self.nombre = "doble"
        self._respuesta = respuesta
        self._disponible = disponible
        self.prompts: list[str] = []
        self.max_tokens: list[int] = []

    def es_disponible(self) -> bool:
        return self._disponible

    def completar(self, prompt: str, *, max_tokens: int = 800) -> ResultadoLLM:
        self.prompts.append(prompt)
        self.max_tokens.append(max_tokens)
        return ResultadoLLM(texto=self._respuesta, modelo="doble")


def _respuesta(**campos: Any) -> str:
    return json.dumps(campos, ensure_ascii=False)


# ── La fuente, la misma para todos ─────────────────────────────────

#: El mensaje del brief. Mariana fue SELECCIONADA. Todo lo que el
#: generador apruebe sobre esta fuente tiene que ser comprobable contra
#: estas lineas.
FUENTE = Interaccion(
    id="m1",
    autor="Mariana Souza",
    canal="LinkedIn",
    texto=(
        "Fue seleccionada para el puesto de Desarrolladora Junior de IA "
        "en una empresa de retail. Destaco por sus proyectos practicos "
        "con LangChain y Oracle Cloud Infrastructure, en la Comunidad de "
        "Inteligencia Artificial de ONE."
    ),
    fecha="2026-01-15",
    mensaje_id="demo-1",
)


def _resultado(procedencia: Procedencia = "llm") -> ResultadoAnalisis:
    return ResultadoAnalisis(
        interaccion=FUENTE,
        analisis=Analisis(sentimiento="positivo", score_sentimiento=0.8),
        procedencia=procedencia,
    )


POST_LIMPIO = _respuesta(
    # El titulo va contra la fuente TANTO como el copy. El primer
    # borrador de este test era "De la Comunidad al Mercado", y el
    # grounding lo rechazo: "Mercado" no esta en el mensaje. El titulo
    # es el lugar donde un modelo cuela un adjetivo sin que nadie lo
    # note, porque nadie compara un titulo con la fuente.
    titulo="Mariana Souza fue seleccionada para un puesto de IA",
    copy=(
        "Nuestra estudiante Mariana Souza fue seleccionada para el puesto "
        "de Desarrolladora Junior de IA tras destacar sus proyectos con "
        "LangChain y Oracle Cloud Infrastructure."
    ),
    hashtags=["#OracleCloud", "#InteligenciaArtificial", "#CarreraDev"],
    potencial_engagement="Alto",
)


# ── El prompt ──────────────────────────────────────────────────────


class TestPrompts:
    def test_el_texto_de_la_persona_va_citado(self) -> None:
        prompt = construir_prompt_post_linkedin(FUENTE)
        assert '"""' in prompt
        assert FUENTE.texto in prompt

    def test_el_prompt_no_revienta_por_las_llaves_del_json(self) -> None:
        """Regresion: `.format()` sobre un JSON literal lanza KeyError.

        Los few-shot son JSON con llaves, y un `{` de ejemplo se
        interpreta como marcador de campo. El sintoma es un `KeyError`
        con un fragmento del post como nombre de campo, en produccion.
        """
        for construir in (
            construir_prompt_post_linkedin,
            construir_prompt_newsletter,
            construir_prompt_faq,
        ):
            prompt = construir(FUENTE)
            assert FUENTE.texto in prompt
            assert '"titulo"' in prompt or '"tema"' in prompt or '"titular"' in prompt

    def test_cada_canal_pide_su_tono(self) -> None:
        assert "inspirador" in construir_prompt_post_linkedin(FUENTE)
        assert "esc ueto".replace(" ", "") in construir_prompt_newsletter(FUENTE).replace(" ", "")
        assert "didactico" in construir_prompt_faq(FUENTE)

    def test_el_ejemplo_few_shot_esta_corregido(self) -> None:
        """El ejemplo del brief, sin el superlativo y sin subir el hecho.

        Few-shot sin corregir entrena al modelo a inventar, y el unico
        sintoma es que un dia el output pega.

        Se recorta DESDE el JSON del ejemplo, no desde la palabra
        "titulo": la prosa que explica la correccion menciona "el mejor
        camino" justamente para decir que se lo saco, y un `in` a secas
        en toda la prosa da verde sobre el error que se quiere cazar.
        """
        prompt = construir_prompt_post_linkedin(FUENTE)
        ejemplo = prompt[prompt.index('{\n  "titulo"') :]
        assert "mejor camino" not in ejemplo
        assert "seleccionada" in ejemplo
        assert "contratada" not in ejemplo

    def test_el_prompt_de_faq_dice_que_no_escriba_la_respuesta(self) -> None:
        assert "NO" in construir_prompt_faq(FUENTE)
        assert "mentor" in construir_prompt_faq(FUENTE)

    def test_el_prompt_no_le_pide_los_campos_del_sistema(self) -> None:
        """Lo que el modelo no ve en el prompt, no lo puede escribir.

        Se busca la clave entre comillas y no la palabra suelta, porque
        "seccion de un resumen semanal" esta en prosa: un `in` a secas
        pasa por verde con el prompt lleno de lo que prohibe.
        """
        assert '"canal_recomendado"' not in construir_prompt_post_linkedin(FUENTE)
        assert '"seccion"' not in construir_prompt_newsletter(FUENTE)
        assert '"status"' not in construir_prompt_faq(FUENTE)


# ── El camino feliz ────────────────────────────────────────────────


class TestGenerador:
    def test_post_limpio_produce_activo(self) -> None:
        resultado = GeneradorActivos(ProveedorDoble(POST_LIMPIO)).generar_post(
            _resultado()
        )
        assert resultado.activo is not None
        assert isinstance(resultado.activo, PostLinkedin)
        assert resultado.informe.veredicto in ("aprobado", "aprobado_con_supuestos")

    def test_los_campos_del_sistema_no_son_del_modelo(self) -> None:
        activo = GeneradorActivos(ProveedorDoble(POST_LIMPIO)).generar_post(
            _resultado()
        ).activo
        assert activo is not None
        assert activo.canal_recomendado == CANAL_LINKEDIN
        assert activo.curado is False
        assert activo.fuentes == ["demo-1"]

    def test_la_confianza_sale_de_la_procedencia_del_analisis(self) -> None:
        llm = GeneradorActivos(ProveedorDoble(POST_LIMPIO)).generar_post(
            _resultado("llm")
        ).activo
        heur = GeneradorActivos(ProveedorDoble(POST_LIMPIO)).generar_post(
            _resultado("heuristico")
        ).activo
        assert llm is not None and heur is not None
        assert llm.confianza > heur.confianza
        # Nunca 1.0: un 1.0 se lee como "no hace falta revisarlo".
        assert llm.confianza < 1.0

    def test_un_prompt_malo_no_pasa_la_verbatim_del_fuente(self) -> None:
        """Si el prompt no es el de este canal, el generador lo delata.

        Un bug de ruteo entre los tres formatos pasaria desapercibido: el
        modelo recibiria el prompt de la newsletter, veria un ejemplo de
        post y devolveria algo raro. Que el prompt exacto aparezca en la
        llamada es la unica forma de testear el ruteo sin un LLM.
        """
        proveedor = ProveedorDoble(POST_LIMPIO)
        GeneradorActivos(proveedor).generar_post(_resultado())
        assert "publicacion de LinkedIn" in proveedor.prompts[0]
        assert proveedor.max_tokens == [700]

    def test_el_faq_siempre_va_a_mentoria(self) -> None:
        activo = GeneradorActivos(
            ProveedorDoble(_respuesta(tema="Como reintentar un nodo en LangGraph"))
        ).generar_faq(_resultado()).activo
        assert activo is not None
        # El contrato declara `listo_para_publicar` por default: el
        # overwrite es lo que evita una FAQ sin respuesta revisada que
        # aparenta estar lista.
        assert activo.status == "derivado_a_mentoria"
        assert "Mariana" in activo.origen

    def test_el_newsletter_trae_la_seccion_del_sistema(self) -> None:
        activo = GeneradorActivos(
            ProveedorDoble(
                _respuesta(
                    titular="Mariana fue seleccionada para un puesto de IA",
                    resumen=(
                        "Mariana Souza fue seleccionada para el puesto de "
                        "Desarrolladora Junior de IA, por sus proyectos con "
                        "LangChain y Oracle Cloud Infrastructure."
                    ),
                )
            )
        ).generar_newsletter(_resultado()).activo
        assert activo is not None
        assert activo.seccion == SECCION_NEWSLETTER


# ── Lo que el generador tiene que frenar ───────────────────────────


class TestRechazos:
    def test_el_grounding_rechaza_y_no_devuelve_activo(self) -> None:
        """El caso central:preferir no tener activo antes que tener uno falso."""
        resultado = GeneradorActivos(
            ProveedorDoble(
                _respuesta(
                    titulo="Mariana ya fue contratada",
                    copy=(
                        "Nuestra estudiante Mariana Souza ya fue contratada como "
                        "Desarrolladora Senior de IA en una empresa de retail, "
                        "con un sueldo de 5000 dolares."
                    ),
                    hashtags=["#CarreraDev"],
                    potencial_engagement="Alto",
                )
            )
        ).generar_post(_resultado())
        assert resultado.activo is None
        assert resultado.rechazado_por_grounding
        assert resultado.motivo_fallo is None
        assert resultado.informe.rechazados

    def test_un_rechazo_por_grounding_no_es_un_fallo_de_proveedor(self) -> None:
        """Si se confunden, un pipeline 'reintenta hasta que pase' termina
        publicando el activo de otra fuente."""
        resultado = GeneradorActivos(
            ProveedorDoble(
                _respuesta(
                    titulo="Mariana Souza fue seleccionada",
                    copy=(
                        "Nuestra estudiante Mariana Souza fue seleccionada para "
                        "el puesto de Desarrolladora Senior de IA."
                    ),
                    hashtags=["#CarreraDev"],
                    potencial_engagement="Alto",
                )
            )
        ).generar_post(_resultado())
        assert resultado.activo is None
        assert resultado.motivo_fallo is None
        assert resultado.rechazado_por_grounding

    def test_hashtags_pegados_se_descartan_como_rechazo(self) -> None:
        """El bug medido: cinco hashtags en un string pasa `list[str]`."""
        resultado = GeneradorActivos(
            ProveedorDoble(
                _respuesta(
                    titulo="De la Comunidad al Mercado",
                    copy=(
                        "Nuestra estudiante Mariana Souza fue seleccionada para el "
                        "puesto de Desarrolladora Junior de IA tras destacar sus "
                        "proyectos con LangChain y Oracle Cloud Infrastructure."
                    ),
                    hashtags=["#OracleCloud #InteligenciaArtificial #CarreraDev"],
                    potencial_engagement="Alto",
                )
            )
        ).generar_post(_resultado())
        assert resultado.activo is None
        assert "hashtag" in resultado.informe.resumen().lower()

    def test_escribir_un_campo_del_sistema_es_rechazo_con_nombre(self) -> None:
        """El modelo no puede decidir `status` ni el canal recomendado."""
        resultado = GeneradorActivos(
            ProveedorDoble(
                _respuesta(
                    tema="Como reintentar un nodo en LangGraph",
                    status="listo_para_publicar",
                )
            )
        ).generar_faq(_resultado())
        assert resultado.activo is None
        assert "status" in resultado.informe.resumen()

    def test_una_clave_de_mas_se_ignora_y_se_anota(self) -> None:
        """Ruido no es motivo para descartar un activo bueno.

        Un check que llora lobo por una clave de mas se desacredita, y
        con el se van los rechazos que si importan.
        """
        resultado = GeneradorActivos(
            ProveedorDoble(
                _respuesta(
                    tema="Nodos de reintento en LangGraph",
                    imagen="prompt_de_una_imagen",
                )
            )
        ).generar_faq(_resultado())
        assert resultado.activo is not None
        assert resultado.campos_ignorados == ("imagen",)


# ── Los fallos que no son de grounding ─────────────────────────────


class TestErroresDeGeneracion:
    def test_json_ilegible_es_error_del_proveedor(self) -> None:
        with pytest.raises(ErrorDeGeneracionError, match="no parseable"):
            GeneradorActivos(ProveedorDoble("lo siento, no puedo")).generar_post(
                _resultado()
            )

    def test_una_lista_no_es_un_activo(self) -> None:
        with pytest.raises(ErrorDeGeneracionError, match="objeto JSON"):
            GeneradorActivos(
                ProveedorDoble(json.dumps([{"titulo": "x"}]))
            ).generar_post(_resultado())

    def test_potencial_engagement_fuera_de_rango_lo_tipa_el_contrato(self) -> None:
        """El bug del numero. Lo caza el tipo, sin ningun check.

        Y por eso `potencial_engagement` es `Literal`: el campo no tiene
        forma de decir "no lo se", y un campo que no puede estar vacio
        es un slot que el modelo llena con ficcion.
        """
        with pytest.raises(ErrorDeGeneracionError, match="potencial_engagement"):
            GeneradorActivos(ProveedorDoble(POST_LIMPIO.replace('"Alto"', "120"))).generar_post(
                _resultado()
            )

    def test_falta_un_campo_obligatorio(self) -> None:
        with pytest.raises(ErrorDeGeneracionError, match="campos del contrato"):
            GeneradorActivos(
                ProveedorDoble(_respuesta(titulo="Solo un titulo sin copy"))
            ).generar_post(_resultado())

    def test_sin_proveedor_no_hay_fallback_inventado(self) -> None:
        """La generacion sin LLM no devuelve un activo de relleno.

        Un fallback que escribe "texto generico" para no devolver nada
        es la forma mas comun de que un pipeline termine publicando
        placeholders.
        """
        with pytest.raises(ErrorDeGeneracionError, match="no configurado"):
            GeneradorActivos(
                ProveedorDoble(POST_LIMPIO, disponible=False)
            ).generar_post(_resultado())


class TestFugasDeIdioma:
    """Un modelo multilingue se le escapa el idioma, y el grounding lo ve.

    Este fixture NO es inventado: es la salida real de `qwen2.5:3b` en
    la primera corrida contra el mensaje del brief. El titular del
    newsletter salio en portugues, "Desenvolvedora Júnior", con la
    palabra cambiada y el acento puesto.

    Vale la pena como test por una razon que no es el portugues: es el
    unico sintoma que casi no se ve a simple vista. Un titular con
    "Desenvolvedora" se lee perfecto para quien no Portuguese, suena
    igual de profesional, y al ojo no se ve. Lo que lo delata es que la
    palabra no estaba en la fuente, y ese es el trabajo del grounding:
    comparar, no leer.

    Y el caso general es peor que un idioma: un modelo de 3B puede
    inventar un nombre propio que suena exactamente a un nombre propio.
    Esto es la version ruidosa de un problema silencioso.
    """

    def test_el_titular_en_portugues_se_rechaza(self) -> None:
        resultado = GeneradorActivos(
            ProveedorDoble(
                _respuesta(
                    titular="Mariana foi selecionada para Desenvolvedora Júnior",
                    resumen=(
                        "Mariana Souza foi selecionada para o puesto de "
                        "Desenvolvedora Júnior de IA."
                    ),
                )
            )
        ).generar_newsletter(_resultado())
        assert resultado.activo is None
        assert "Desenvolvedora" in resultado.informe.resumen()


class TestCamposDelSistema:
    """Lo que escribe el sistema NO se verifica como copy del modelo.

    Este archivo es la regresion de un bug que solo aparecio al
    escribirse el generador: `seccion = "Logro de la Semana"` es una
    constante del sistema, escrita a proposito para que el modelo NO la
    escriba, y `verificar_activo` la pasaba por el check de entidades y
    reportaba que "Semana" no estaba en la fuente. Consecuencia: ningun
    newsletter podia publicarse, nunca, y ningun test de M4 lo vio
    porque todos llamaban a `verificar_copy` y no a `verificar_activo`.

    Un check de contenido no puede pasar por campos que no son
    contenido inventable.
    """

    def test_el_newsletter_se_puede_publicar(self) -> None:
        activo = GeneradorActivos(
            ProveedorDoble(
                _respuesta(
                    titular="Mariana fue seleccionada para un puesto de IA",
                    resumen=(
                        "Mariana Souza fue seleccionada para el puesto de "
                        "Desarrolladora Junior de IA, por sus proyectos con "
                        "LangChain y Oracle Cloud Infrastructure."
                    ),
                )
            )
        ).generar_newsletter(_resultado()).activo
        assert activo is not None

    def test_un_campo_del_sistema_invitado_sigue_siendo_rechazo(self) -> None:
        """El parametro no se puede usar para salvar un invento.

        Si `campos_del_modelo` fuera "los campos que quiero verificar",
        el generador podria excluir `copy` del chequeo y publicaria
        cualquier cosa. Por eso es la lista de lo que escribio el
        MODELO, y el copy siempre esta.
        """
        from sieve.generation.grounding import verificar_activo

        activo = GeneradorActivos(
            ProveedorDoble(
                _respuesta(
                    titular="Mariana fue seleccionada para un puesto de IA",
                    resumen=(
                        "Mariana fue contratada como Desarrolladora Senior con "
                        "un sueldo de 5000 dolares."
                    ),
                )
            )
        )
        generado = activo.generar_newsletter(_resultado())
        assert generado.activo is None
        assert generado.informe.rechazados
        # Y el rechazo es el del `resumen`, el campo del modelo.
        assert any("resumen/" in v.nombre for v in generado.informe.rechazados)
        assert verificar_activo is not None


# ── La puerta ──────────────────────────────────────────────────────


class TestGate:
    def test_nada_generado_es_publicable_aun(self) -> None:
        """El panel de curaduria no existe: `publicable` es False siempre.

        Que sea cierto por construccion, y no por costumbre, es lo que
        hace segura la etapa siguiente.
        """
        resultado = GeneradorActivos(ProveedorDoble(POST_LIMPIO)).generar_post(
            _resultado()
        )
        assert resultado.informe.publicable
        assert not resultado.publicable
        assert "curaduria" in resultado.resumen()
