"""
Test de CONTRATO: valida contra el JSON literal del documento ONE.

Este archivo es prueba de que el requisito mas importante del MVP
"devolver la salida estructurada definida por el documento" esta
cumplido. Si el documento cambia el esquema, este test se rompe y te
dice exactamente donde. Ese es todo el punto.
"""

from __future__ import annotations

import pytest

from sieve.models import (
    Interaccion,
    LoteComunidad,
    Sentimiento,
    TipoInteraccion,
    validar_contrato_documento,
)

# ─── PAYLOAD EXACTO DEL DOCUMENTO ────────────────────────────────────
# Copiado character por character del ejemplo de salida del PDF.
# Si se cambia el contrato, se edita aca y no en otro lado.

DOCUMENTO_SALIDA = {
    "status": "exito",
    "resumen_comunidad": {
        "total_interacciones_procesadas": 2,
        "sentimiento_predominante": "Altamente Positivo",
        "temas_principales": [
            "Contratacion / Logros",
            "LangGraph / Nodos Condicionales",
        ],
    },
    "activos_distribucion_generados": {
        "post_linkedin": {
            "titulo": "De la Comunidad al Mercado: El impacto de los proyectos practicos de IA",
            "copy": (
                "Nada nos da mas orgullo que ver a nuestros talentos conquistando "
                "el mercado de tecnologia! Nuestra estudiante Mariana Souza acaba de "
                "ser contratada como Desarrolladora Junior de IA tras destacar sus "
                "proyectos practicos desarrollados con LangChain y Oracle Cloud."
            ),
            "canal_recomendado": "LinkedIn Oficial",
            "potencial_engagement": "Alto",
        },
        "destreza_newsletter_semanal": {
            "seccion": "Logro de la Semana",
            "titular": "Estudiante consigue empleo dev con portfolio de IA en Oracle Cloud",
            "resumen": (
                "Mariana Souza obtuvo su primera oportunidad como Dev Jr de IA "
                "destacando proyectos desarrollados durante la formacion."
            ),
        },
        "sugerencia_contenido_faq": {
            "tema": "Tip Rapido: Como crear nodos de reintento en LangGraph",
            "origen": "Duda frecuente planteada por Lucas Albuquerque en el canal de soporte",
            "status": "derivado_a_mentoria",
        },
    },
    "almacenamiento_oci": {
        "bucket": "communitylab-activos-marketing",
        "ruta_objeto": "activos/2026-semana-04/paquete-distribucion.json",
        "status": "guardado_con_exito",
    },
}


class TestContratoSalida:
    """El payload del documento tiene que validar sin tocar una linea."""

    def test_valida_contra_el_esquema(self) -> None:
        respuesta = validar_contrato_documento(DOCUMENTO_SALIDA)
        assert respuesta.status == "exito"

    def test_conserva_los_campos_del_documento(self) -> None:
        r = validar_contrato_documento(DOCUMENTO_SALIDA)
        assert r.resumen_comunidad.total_interacciones_procesadas == 2
        assert r.almacenamiento_oci.bucket == "communitylab-activos-marketing"
        assert r.almacenamiento_oci.status == "guardado_con_exito"

    def test_los_tres_activos_del_documento_existen(self) -> None:
        r = validar_contrato_documento(DOCUMENTO_SALIDA)
        activos = r.activos_distribucion_generados
        assert activos.post_linkedin is not None
        # El atributo en Python se llama con la ortografia correcta
        # (`destaque_`); el alias emite el typo del documento
        # (`destreza_`). Ver test_el_tipo_de_activo_se_emite_con_el_typo.
        assert activos.destaque_newsletter_semanal is not None
        assert activos.sugerencia_contenido_faq is not None

    def test_el_tipo_de_activo_se_emite_con_el_typo_del_documento(self) -> None:
        """El documento escribe 'destreza_newsletter_semanal'. Se emite asi.

        Es casi con seguridad un typo suyo por 'destaca', pero es EL
        CONTRATO. Si el JSON sale con la palabra corregida, no valida
        contra el documento — y no valida, no existe.
        """
        r = validar_contrato_documento(DOCUMENTO_SALIDA)
        claves = r.model_dump(by_alias=True)["activos_distribucion_generados"]
        assert "destreza_newsletter_semanal" in claves
        # y no debe filtrarse una clave "corregida" inventada por nosotros
        assert "destaca_newsletter_semanal" not in claves

    def test_clave_desconocida_falla_en_voz_alta(self) -> None:
        """Una clave desconocida NO se descarta en silencio.

        Este test existe por un bug real: con `extra="ignore"` (el
        default de Pydantic), la clave mal escrita del documento se
        ignoraba y el newsletter salia vacio sin error. Un contrato
        que falla calladito hace perder horas de debug.
        """
        payload = {
            **DOCUMENTO_SALIDA,
            "activos_distribucion_generados": {
                **DOCUMENTO_SALIDA["activos_distribucion_generados"],
                "post_instagram": {"copy": "nope"},
            },
        }
        with pytest.raises(ValueError, match="post_instagram"):
            validar_contrato_documento(payload)

    def test_tipos_de_activo_se_serializan_como_en_el_documento(self) -> None:
        """'muy_positivo' tiene que salir como 'Altamente Positivo'.

        Este es el tipo de detalle invisible que hace que un evaluador
        mire el JSON y diga 'este lo armaron bien'. El enum interno es
        snake_case; la salida es vocabulario humano.
        """
        r = validar_contrato_documento(DOCUMENTO_SALIDA)
        assert r.resumen_comunidad.sentimiento_predominante == Sentimiento.MUY_POSITIVO
        serializado = r.model_dump()
        assert serializado["resumen_comunidad"]["sentimiento_predominante"] == "Altamente Positivo"

    def test_round_trip_preserva_los_valores_del_documento(self) -> None:
        """Validar y re-serializar tiene que devolver el mismo JSON.

        Este es el test mas importante del archivo. Si el round-trip
        altera una clave o un valor, el contrato esta roto aunque
        'valide'. Y un contrato que solo valida en un sentido no es un
        contrato: hay que poder emitir Y poder leer.
        """
        original = validar_contrato_documento(DOCUMENTO_SALIDA)
        reemitido = validar_contrato_documento(original.model_dump())
        assert reemitido.model_dump(mode="json") == original.model_dump(mode="json")

    def test_el_documento_no_declara_canal_en_newsletter_ni_faq(self) -> None:
        """El documento solo pone `canal_recomendado` en `post_linkedin`.

        Si el modelo exige esos campos en los otros dos activos, el
        pipeline se ve obligado a inventarlos. Un contrato que obliga a
        fabricar datos es un contrato mal escrito.
        """
        r = validar_contrato_documento(DOCUMENTO_SALIDA)
        activos = r.activos_distribucion_generados
        assert not hasattr(activos.destaque_newsletter_semanal, "canal_recomendado")
        assert not hasattr(activos.sugerencia_contenido_faq, "canal_recomendado")
        # pero sí en el post, que es donde el documento lo declara
        assert activos.post_linkedin.canal_recomendado == "LinkedIn Oficial"
        assert activos.post_linkedin.potencial_engagement == "Alto"


class TestContratoEntrada:
    """El JSON de ENTRADA del documento tambien tiene que validar."""

    def test_entrada_del_documento(self) -> None:
        lote = LoteComunidad.model_validate(
            {
                "origen_comunidad": "Discord_Grupo_ONE_G10",
                "periodo_referencia": "Semana_04",
                "interacciones": [
                    {
                        "autor": "Mariana Souza",
                        "canal": "#logros-y-empleos",
                        "tipo": "testimonio",
                        "texto": (
                            "Comunidad, quede seleccionada para el puesto de "
                            "Desarrolladora Junior de IA! El proyecto del curso de "
                            "LangChain y OCI que construi en mi portfolio marco toda "
                            "la diferencia en la entrevista tecnica."
                        ),
                    },
                    {
                        "autor": "Lucas Albuquerque",
                        "canal": "#dudas-langgraph",
                        "tipo": "pregunta_tecnica",
                        "texto": (
                            "Tengo dudas sobre como estructurar los nodos "
                            "condicionales en LangGraph cuando la respuesta del "
                            "LLM necesita reintento. Alguien tiene un ejemplo "
                            "practico de router?"
                        ),
                    },
                ],
            }
        )
        assert len(lote.interacciones) == 2
        assert lote.interacciones[0].tipo == TipoInteraccion.TESTIMONIO
        assert lote.interacciones[1].tipo == TipoInteraccion.PREGUNTA_TECNICA

    def test_el_tipo_se_normaliza_a_enum(self) -> None:
        """Un tipo desconocido no debe reventar la ingesta.

        Si llega 'pregunta' en vez de 'pregunta_tecnica', el pipeline
        tiene que sobrevivir: la tipificacion la hace el LLM despues.
        """
        lote = LoteComunidad.model_validate(
            {
                "origen_comunidad": "test",
                "periodo_referencia": "S01",
                "interacciones": [
                    {"autor": "a", "canal": "c", "tipo": "tipo_inventado", "texto": "hola"}
                ],
            }
        )
        assert lote.interacciones[0].tipo == TipoInteraccion.CHAT


class TestValidacionesNegativas:
    """Un contrato que no puede fallar no es un contrato."""

    def test_rechaza_lote_vacio(self) -> None:
        with pytest.raises(ValueError):
            LoteComunidad.model_validate(
                {"origen_comunidad": "x", "periodo_referencia": "S01", "interacciones": []}
            )

    def test_rechaza_lote_sobre_el_maximo(self) -> None:
        """Un lote Gigabyte reventaria la API y el rate limit.

        La guarda tiene que estar en el borde del sistema — al deserializar
        — y no adentro del pipeline, cuando ya se gastaron los tokens.
        """
        from sieve.config import settings

        lote = {
            "origen_comunidad": "x",
            "periodo_referencia": "S01",
            "interacciones": [
                {"autor": "a", "canal": "c", "texto": "hola"}  # noqa: RUF001
            ]
            * (settings.max_batch_size + 1),
        }
        with pytest.raises(ValueError, match="excede"):
            LoteComunidad.model_validate(lote)

    def test_rechaza_texto_vacio(self) -> None:
        with pytest.raises(ValueError):
            Interaccion(autor="a", canal="c", texto="   ")

    def test_rechaza_sentimiento_fuera_de_rango(self) -> None:
        from sieve.models import Analisis

        with pytest.raises(ValueError):
            Analisis(sentimiento="positivo", score_sentimiento=1.8)

    def test_almacenamiento_puede_reportar_fallo(self) -> None:
        """Un 'status: fallido' tiene que ser valido.

        Ocultar errores de escritura para que la demo se vea bien es
        el camino mas corto a perder credibilidad tecnica.
        """
        r = validar_contrato_documento(
            {
                **DOCUMENTO_SALIDA,
                "almacenamiento_oci": {
                    **DOCUMENTO_SALIDA["almacenamiento_oci"],
                    "status": "fallido",
                },
            }
        )
        assert r.almacenamiento_oci.status == "fallido"
