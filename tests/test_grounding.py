"""
Tests de grounding.

El fixture principal es LITERALMENTE el ejemplo del documento ONE, y no
una construccion mia. Es la decision que le da sentido a todo este
modulo: si el grounding acepta el ejemplo oficial del brief, esta
midiendo lo correcto; y si lo rechaza, el hallazgo es sobre el brief y
no sobre un test que uno mismo escribio para pasar.

Y el brief falla. Su post oficial se pasa de "seleccionada" a
"contratada" y llama "mejor camino" a algo que la fuente no dice; su
newsletter oficial agrega "su primera oportunidad", que no esta en
ningun lado de la fuente. Los dos test que fijan ese comportamiento
estan al principio de este archivo, y son los que importan.
"""

from __future__ import annotations

import pytest

from sieve.generation.grounding import (
    VOCABULARIO_EDITORIAL,
    campos_textuales,
    check_profesional,
    cifras_en,
    clasificar_hashtags,
    entidades_en,
    ordinales_en,
    peldano_de,
    puede_publicarse,
    separar_hashtags,
    verificar_activo,
    verificar_copy,
)
from sieve.models import Interaccion, Newsletter, PostLinkedin, SugerenciaFaq

# ═══════════════════════════════════════════════════════════════════
#  EL BRIEF COMO FIXTURE
# ═══════════════════════════════════════════════════════════════════

BRIEF_FUENTE = Interaccion(
    autor="Mariana Souza",
    canal="#logros-y-empleos",
    tipo="testimonio",
    texto=(
        "Comunidad, quede seleccionada para el puesto de Desarrolladora "
        "Junior de IA! El proyecto del curso de LangChain y OCI que "
        "construi en mi portfolio marco toda la diferencia en la "
        "entrevista tecnica. Muy agradecida con la comunidad por todo "
        "el apoyo!"
    ),
)

BRIEF_POST = (
    "Nada nos da mas orgullo que ver a nuestros talentos conquistando el "
    "mercado de tecnologia! Nuestra estudiante Mariana Souza acaba de ser "
    "contratada como Desarrolladora Junior de IA tras destacar sus "
    "proyectos practicos desarrollados con LangChain y Oracle Cloud "
    "Infrastructure. Historias como la de Mariana demuestran que "
    "construir soluciones reales es el mejor camino para impulsar la "
    "carrera tech. Felicitaciones, Mariana!"
)

BRIEF_NEWSLETTER = (
    "Mariana Souza obtuvo su primera oportunidad como Dev Jr de IA "
    "destacando proyectos desarrollados durante la formacion."
)

#: Los campos de texto que escribe el MODELO en cada activo. El resto
#: lo pone el sistema, y `verificar_activo` no los revisa como copy.
#: Ver `TestCamposDelSistema` en `test_assets.py` para el bug que hizo
#: falta explicitar esto.
POST = ("titulo", "copy")


# ═══════════════════════════════════════════════════════════════════
#  LOS DOS TEST QUE IMPORTAN
# ═══════════════════════════════════════════════════════════════════


def test_el_post_del_brief_falla_por_dos_cosas() -> None:
    """El post oficial del brief tiene DOS problemas, y son los dos graves.

    1. "el mejor camino": la fuente no dice que sea el mejor. Es
       publicitaria.
    2. "seleccionada" -> "contratada": sube el estado laboral de una
       persona real un peldano. Esto NO es publicitaria, es un hecho.

    Se enumeran los dos y no ">= 1" porque un check que se queja de
    cinco cosas no dice cual es la importante. La informacion util para
    quien edita el borrador es "corta 'el mejor' y no digas contratada",
    no "tu post esta mal".
    """
    informe = verificar_copy(BRIEF_POST, BRIEF_FUENTE)
    assert informe.veredicto == "rechazado"
    assert [v.nombre for v in informe.rechazados] == [
        "escalera de compromiso",
        "superlativos",
    ]
    assert "'mejor'" in informe.rechazados[1].hallazgos[0]
    assert "contratada" in informe.rechazados[0].hallazgos[0]


def test_el_newsletter_del_brief_se_rechaza_por_ordinal() -> None:
    """'su primera oportunidad' no esta en la fuente. En ninguna parte.

    Es el fallo mas caro de los dos, porque el historial de carrera de
    una persona es un dato que se comparte, se cita y dura. Y sale del
    documento de referencia del proyecto.
    """
    informe = verificar_copy(BRIEF_NEWSLETTER, BRIEF_FUENTE)
    assert informe.veredicto == "rechazado"
    assert [v.nombre for v in informe.rechazados] == ["ordinales"]
    assert "'primera'" in informe.rechazados[0].hallazgos[0]


def test_la_escalera_decompromiso_ve_la_subida_del_brief() -> None:
    """'seleccionada' -> 'contratada' sube un escalon y se ve.

    El post del brief es el fixture porque el fallo es real: el
    documento sube el estado laboral de una persona un peldano sin
    decirlo.
    """
    nivel_fuente, nombre_fuente = peldano_de(BRIEF_FUENTE.texto)
    nivel_copy, nombre_copy = peldano_de(BRIEF_POST)
    assert nombre_fuente == "seleccion"
    assert nombre_copy == "contrato"
    assert nivel_copy > nivel_fuente

    # Y el post, aislado de su superlativo, cae en la escalera.
    corregido = BRIEF_POST.replace("es el mejor camino", "es un camino")
    fallidos = [v.nombre for v in verificar_copy(corregido, BRIEF_FUENTE).rechazados]
    assert fallidos == ["escalera de compromiso"]


def test_bajar_de_peldano_tambien_se_arregla() -> None:
    """Dos correcciones de una palabra, y el post pasa.

    Borrar 'el mejor': 'un camino' es tan cierto como 'el mejor camino'
    pero no promete una comparacion que la fuente no hace. Bajar el
    peldano: 'fue seleccionada' en vez de 'acaba de ser contratada' hace
    que el post diga la verdad, porque la fuente dice que la
    seleccionaron, no que ya empezo a trabajar.

    El fix de cada hallazgo es de una palabra. Ese es el argumento de
    por que el check es util y no un obstaculo.

    Y queda avisado 'estudiante': no es un fallo, pero es una inferencia
    del sistema sobre una persona y tiene que estar a la vista.
    """
    corregido = (
        BRIEF_POST.replace("es el mejor camino", "es un camino")
        .replace("acaba de ser contratada", "fue seleccionada para el puesto de")
    )
    informe = verificar_copy(corregido, BRIEF_FUENTE)
    assert informe.veredicto == "aprobado_con_supuestos"
    assert not informe.rechazados
    assert any("estudiante" in s for s in informe.supuestos)


# ═══════════════════════════════════════════════════════════════════
#  CADA CHECK, POR SEPARADO
# ═══════════════════════════════════════════════════════════════════


def test_cifra_inventada_cae() -> None:
    fuente = Interaccion(autor="X", canal="c", texto="llego 5 proyectos al mes")
    assert verificar_copy("llego 120 proyectos al mes", fuente).rechazados[0].nombre == "cifras"


def test_cifra_en_palabras_se_compara_con_digitos() -> None:
    """La fuente dice 'tres meses' y el post '3 meses': es el MISMO hecho."""
    fuente = Interaccion(autor="X", canal="c", texto="despues de tres meses")
    informe = verificar_copy("despues de 3 meses lo conseguiu", fuente)
    assert not [v for v in informe.rechazados if v.nombre == "cifras"]


def test_el_articulo_indefinido_no_es_una_cifra() -> None:
    """'un camino' NO es un 1.

    Falso positivo medido: el check reportaba '1 no aparece en la
    fuente' sobre un post perfectly legitimo. Un check que llora lobo
    desacredita la linea entera y el humano aprende a saltearla.
    """
    assert cifras_en("es un camino") == set()
    assert cifras_en("un camino y una persona") == set()
    fuente = Interaccion(autor="X", canal="c", texto="construyo un proyecto")
    assert verificar_copy("construyo un proyecto", fuente).veredicto == "aprobado"


def test_numero_pegado_a_una_letra_no_es_cifra() -> None:
    """'GPT-4' y 'v2' son parte de un nombre, no una afirmacion."""
    assert cifras_en("usamos GPT-4 y Python v2") == set()


def test_compuestos_en_palabras() -> None:
    assert 35 in cifras_en("treinta y cinco dias")
    assert 35 in cifras_en("30 y cinco dias")
    assert 31 in cifras_en("treinta y uno")


def test_entidad_inventada_cae() -> None:
    """El hallazgo de la corrida real: #Emprendimiento, una dev junior."""
    fuente = Interaccion(
        autor="Mariana Souza", canal="c",
        texto="recien me contrataron como dev junior",
    )
    informe = verificar_copy("fue contratada en Globant y luego en Nubank", fuente)
    fallidos = [v for v in informe.rechazados if v.nombre == "entidades"]
    assert fallidos
    assert any("Globant" in h for h in fallidos[0].hallazgos)
    assert any("Nubank" in h for h in fallidos[0].hallazgos)


def test_sigla_ampliada_no_es_invencion() -> None:
    """'OCI' en la fuente, 'Oracle Cloud Infrastructure' en el post."""
    fuente = Interaccion(autor="X", canal="c", texto="use OCI y el proyecto de IA")
    informe = verificar_copy("el proyecto en Oracle Cloud Infrastructure con IA", fuente)
    assert not [v for v in informe.rechazados if v.nombre == "entidades"]


def test_abreviatura_de_puesto_no_es_invencion() -> None:
    """El post dice 'Dev Jr', la fuente dice 'Desarrolladora Junior'."""
    fuente = Interaccion(autor="X", canal="c", texto="puesto de Desarrolladora Junior")
    informe = verificar_copy("entra como Dev Jr al equipo", fuente)
    assert not [v for v in informe.rechazados if v.nombre == "entidades"]


def test_el_autor_esta_fundamentado_aunque_el_texto_no_lo_diga() -> None:
    """La fuente del brief NUNCA dice 'Mariana': solo el campo `autor`.

    El nombre esta fundamentado por el mensaje que la persona firmo, y
    un check que rechaza el ejemplo del brief por eso es un check con
    plumbing roto, no con criterio.
    """
    assert "Mariana" not in BRIEF_FUENTE.texto
    informe = verificar_copy("Felicitaciones, Mariana!", BRIEF_FUENTE)
    assert not [v for v in informe.rechazados if v.nombre == "entidades"]


def test_apellido_inventado_cae_aunque_el_nombre_no() -> None:
    """'Mariana Souza' esta; 'Mariana Souza-Kim' no. Exigir TODAS las partes."""
    informe = verificar_copy("Felicitaciones, Mariana Souza-Kim!", BRIEF_FUENTE)
    fallidos = [v for v in informe.rechazados if v.nombre == "entidades"]
    assert fallidos


def test_ordinal_inventado_cae() -> None:
    fuente = Interaccion(autor="X", canal="c", texto="recien me contrataron")
    assert verificar_copy("su primer empleo", fuente).veredicto == "rechazado"


def test_superlativo_inventado_cae() -> None:
    fuente = Interaccion(autor="X", canal="c", texto="recien me contrataron")
    assert verificar_copy("el mejor camino", fuente).veredicto == "rechazado"
    # y si la fuente lo dice, no hay falla
    fuente2 = Interaccion(autor="X", canal="c", texto="es el mejor camino")
    assert verificar_copy("el mejor camino", fuente2).veredicto == "aprobado"


def test_cita_inventada_cae() -> None:
    fuente = Interaccion(autor="X", canal="c", texto="me contrataron en marzo")
    informe = verificar_copy('dijo "me ascendieron a directora" en el post', fuente)
    fallidos = [v for v in informe.rechazados if v.nombre == "citas textuales"]
    assert fallidos


def test_verbatim_real_no_es_fallo() -> None:
    fuente = Interaccion(autor="X", canal="c", texto="me contrataron en marzo como dev")
    informe = verificar_copy('escribio "me contrataron en marzo como dev"', fuente)
    assert not [v for v in informe.rechazados if v.nombre == "citas textuales"]


# ═══════════════════════════════════════════════════════════════════
#  EL GATE
# ═══════════════════════════════════════════════════════════════════


def test_el_gate_requiere_las_dos_cosas() -> None:
    """Grounding limpio Y firmado. Si falta una, no se publica."""
    limpio = verificar_copy("Felicitaciones, Mariana!", BRIEF_FUENTE)
    assert limpio.veredicto == "aprobado"
    assert puede_publicarse(limpio, curado=False) is False
    assert puede_publicarse(limpio, curado=True) is True


def test_firmado_pero_sucio_tampoco_se_publica() -> None:
    sucio = verificar_copy(BRIEF_POST, BRIEF_FUENTE)
    assert puede_publicarse(sucio, curado=True) is False


def test_un_reporte_aprobado_no_es_un_reporte_vacio() -> None:
    """Veredicto y verificaciones son cosas distintas.

    Un `veredicto='aprobado'` con la lista de checks vacia seria
    indistinguible de un reporte que no llego a correr nada, y el gate
    no puede distinguir "todo bien" de "no mire".
    """
    informe = verificar_copy("Felicitaciones, Mariana!", BRIEF_FUENTE)
    assert informe.verificaciones
    assert len(informe.verificaciones) == 7


def test_el_informe_dice_que_chequear_fallo() -> None:
    """El hallazgo lleva el texto, no un booleano.

    Un check que falla sin decir QUE obliga a reabrir el activo y
    compararlo a mano, que es el trabajo que el panel de curaduria vino
    a evitar.
    """
    informe = verificar_copy(BRIEF_POST, BRIEF_FUENTE)
    assert "mejor" in informe.resumen()
    assert "FALLA" in informe.resumen()


# ═══════════════════════════════════════════════════════════════════
#  HASHTAGS: ENCUADRE, NO HECHOS
# ═══════════════════════════════════════════════════════════════════


def test_hashtags_juntos_es_el_bug_que_ocurrio() -> None:
    """El modelo devolvio 5 hashtags en UN string. Pasa `list[str]`."""
    bien, mal = separar_hashtags(["#a #b #c #d #e"])
    assert bien == []
    assert mal == ["#a #b #c #d #e"]


def test_hashtags_bien_uno_por_item() -> None:
    bien, mal = separar_hashtags(["#a", "#b", "#c"])
    assert bien == ["#a", "#b", "#c"]
    assert mal == []


def test_un_hashtag_sin_respaldo_es_marco_y_no_fallo() -> None:
    """'#CarreraDev' no esta en la fuente y no ES un hecho.

    Tratarlo como fallo seria prometer una verificacion que no existe.
    Va al reporte como marco, que es el trabajo del humano.
    """
    _, marcos = clasificar_hashtags(["#TalentosTech", "#OracleCloud"], BRIEF_FUENTE.texto)
    assert marcos == ["#TalentosTech"]  # OracleCloud resuelve a OCI, que esta


def test_un_hashtag_sin_almohadilla_no_cuenta_como_hashtag() -> None:
    _, mal = separar_hashtags(["Talento"])
    assert mal == ["Talento"]


# ═══════════════════════════════════════════════════════════════════
#  SOBRE EL ACTIVO COMPLETO
# ═══════════════════════════════════════════════════════════════════


def test_el_activo_se_verifica_campo_por_campo() -> None:
    """El campo que falla viaja con su nombre en el reporte.

    'superlativos' no dice que hay que editar 'titulo' o 'copy'.
    """
    activo = PostLinkedin(
        titulo="La historia de Mariana",
        copy=BRIEF_POST,
        hashtags=["#OracleCloud", "#TalentosTech"],
        canal_recomendado="LinkedIn Oficial",
        potencial_engagement="Alto",
    )
    informe = verificar_activo(activo, BRIEF_FUENTE, campos_del_modelo=POST)
    nombres = [v.nombre for v in informe.rechazados]
    assert "copy/superlativos" in nombres
    assert "#TalentosTech" in informe.marcos


def test_un_activo_limpio_pasa() -> None:
    activo = PostLinkedin(
        titulo="Felicitaciones, Mariana",
        copy="Felicitaciones, Mariana! Accomplio el objetivo del bootcamp",
        hashtags=["#OracleCloud"],
        canal_recomendado="LinkedIn Oficial",
        potencial_engagement="Alto",
    )
    assert (
        verificar_activo(activo, BRIEF_FUENTE, campos_del_modelo=POST).rechazados
        == []
    )


def test_el_potential_de_engagement_lo_frena_el_tipo() -> None:
    """El hallazgo que ordeno todo: `Literal` contra un numero.

    El modelo puso 120 y el tipo lo rechazo sin que hubiera que
    escribir una linea de codigo. Por eso el enum es de tres palabras
    y no un numero: un campo que no puede decir 'no lo se' se llena.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PostLinkedin(
            titulo="Un titulo valido de prueba",
            copy="Un copy suficientemente largo para pasar la validacion",
            canal_recomendado="LinkedIn Oficial",
            potencial_engagement=120,  # type: ignore[arg-type]
        )


def test_el_newsletter_no_tiene_hashtags_y_no_falla_por_eso() -> None:
    activo = Newsletter(
        seccion="Logro de la Semana",
        titular="Felicitaciones a Mariana",
        resumen="Mariana construyo con LangChain y OCI en su portfolio",
    )
    informe = verificar_activo(
        activo, BRIEF_FUENTE, campos_del_modelo=("titular", "resumen")
    )
    assert informe.marcos == []


def test_el_faq_solo_deriva() -> None:
    """`derivado_a_mentoria` es el patron correcto: no inventar respuesta.

    El brief responde una duda derivandola a mentoria en vez de escribir
    una respuesta tecnica que nadie verifico. El codigo tiene que poder
    hacer lo mismo, y ese estado existe justamente para eso.
    """
    activo = SugerenciaFaq(
        tema="Tip: nodos de reintento en LangGraph",
        origen="Duda planteada en el canal de soporte",
        status="derivado_a_mentoria",
    )
    assert (
        verificar_activo(activo, BRIEF_FUENTE, campos_del_modelo=("tema",)).rechazados
        == []
    )


def test_los_metadatos_no_se_verifican_como_texto() -> None:
    """`canal_recomendado` y `potencial_engagement` no son afirmaciones.

    Un activo con un canal inventado pasaria el check de entidades si se
    verificaran como texto, y 'LinkedIn Oficial' no es una entidad
    inventada: lo pone el sistema, no el modelo.

    Y no es solo un caso: `seccion = "Logro de la Semana"` hacia lo
    mismo, y ese fue un bug real que dejo a todos los newsletters sin
    poder publicarse. Por eso los campos a verificar los declara quien
    sabe de donde vino cada uno -- el generador -- y no una lista
    hardcodeada acá, que es la forma de que se desincronice en
    silencio la proxima vez que se agrega un campo.
    """
    activo = PostLinkedin(
        titulo="Felicitaciones, Mariana",
        copy="Felicitaciones, Mariana! Accomplio el objetivo del bootcamp",
        hashtags=["#OracleCloud"],
        canal_recomendado="LinkedIn Oficial",
        potencial_engagement="Alto",
    )
    assert set(campos_textuales(activo, campos=POST)) == {"titulo", "copy"}
    # Sin el parametro se toman todos los de texto: es el default comodo
    # para mirar un activo a mano, y el que hace visible el problema en
    # vez de esconderlo.
    assert "canal_recomendado" in campos_textuales(activo)


def test_el_nivel_del_cargo_no_se_puede_cambiar() -> None:
    """El bug que aparecio al escribir el generador, no antes.

    'senior' y 'junior' estan en `VOCABULARIO_INSTITUCIONAL` para que
    `Sr` y `Jr` no producing falsos positivos, y cumplen. El costo es
    que el nivel del cargo dejo de verificarse en ningun lado, y un post
    podia publicar 'Desarrolladora Senior' sobre alguien que la fuente
    dice Junior. Es el mismo bug que 'seleccionada' convertida en
    'contratada', y peor: el cargo de alguien es un hecho verificable y
    publicarlo inflado no se ve como publicarlo inflado.
    """
    fuente_junior = Interaccion(
        id="j",
        autor="Mariana Souza",
        canal="LinkedIn",
        texto="Fue seleccionada para el puesto de Desarrolladora Junior de IA.",
        fecha="2026-01-15",
        mensaje_id="j1",
    )
    assert not check_profesional(
        "Mariana fue contratada como Desarrolladora Senior de IA",
        fuente_junior.texto,
    ).ok
    assert check_profesional(
        "Mariana fue seleccionada como Desarrolladora Junior de IA",
        fuente_junior.texto,
    ).ok
    # Y en ninguna direccion: bajar tambien es un hecho falso.
    assert not check_profesional(
        "Mariana es Desarrolladora Trainee de IA", fuente_junior.texto
    ).ok


# ═══════════════════════════════════════════════════════════════════
#  EXTRACCION
# ═══════════════════════════════════════════════════════════════════


def test_lo_que_abre_frase_no_es_entidad() -> None:
    """'Nada nos da mas orgullo' no habla de una entidad 'Nada'."""
    assert "Nada" not in entidades_en("Nada nos da mas orgullo")


def test_felicitaciones_no_es_entidad() -> None:
    """Palabra capitalizada por posicion, no nombre propio."""
    assert "felicitaciones" in VOCABULARIO_EDITORIAL
    assert "Felicitaciones" not in entidades_en("Felicitaciones, Mariana!")


def test_una_marca_nueva_en_medio_de_frase_cae() -> None:
    """Si NO esta en la lista de editoriales, se reporta."""
    nombres = entidades_en("Leofi Purple presento el plan en la reunion")
    assert "Purple" in nombres


def test_ordinales() -> None:
    assert "primera" in ordinales_en("su primera oportunidad")
    assert ordinales_en("una vez mas") == set()
