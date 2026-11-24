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
from sieve.perfil import Identidad, PerfilOrganizacion, Vocabulario

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


def test_el_post_del_brief_falla_por_dos_cosas(perfil_one: PerfilOrganizacion) -> None:
    """El post oficial del brief tiene DOS problemas, y son los dos graves.

    1. "el mejor camino": la fuente no dice que sea el mejor. Es
       publicitaria.
    2. "seleccionada" -> "contratada": sube el estado laboral de una
       persona real un peldano. Esto NO es publicitaria, es un hecho.

    Se enumeran los dos y no ">= 1" porque un check que se queja de
    cinco cosas no dice cual es la importante. La informacion util para
    quien edita el borrador es "corta 'el mejor' y no digas contratada",
    no "tu post esta mal".

    Y pide el perfil de ONE de forma explicita: "Oracle Cloud
    Infrastructure" es la sigla de OCI, y esa expansiones es DATO del
    cliente, no una regla del motor. Sin el perfil, el post del brief
    propio seria rechazado y el test estaria probando el caso
    contrario.
    """
    informe = verificar_copy(BRIEF_POST, BRIEF_FUENTE, perfil=perfil_one)
    assert informe.veredicto == "rechazado"
    assert [v.nombre for v in informe.rechazados] == [
        "escalera de compromiso",
        "superlativos",
    ]
    assert "'mejor'" in informe.rechazados[1].hallazgos[0]
    assert "contratada" in informe.rechazados[0].hallazgos[0]


def test_el_newsletter_del_brief_se_rechaza_por_ordinal(
    perfil_one: PerfilOrganizacion,
) -> None:
    """'su primera oportunidad' no esta en la fuente. En ninguna parte.

    Es el fallo mas caro de los dos, porque el historial de carrera de
    una persona es un dato que se comparte, se cita y dura. Y sale del
    documento de referencia del proyecto.

    Pide el perfil de ONE porque el titular abre con "Dev Jr" y la
    fuente dice "Desarrolladora Junior": si la equivalencia no esta
    cargada, el rechazo seria por el puesto de trabajo y no por la
    palabra "primera", que es lo que este test mide.
    """
    informe = verificar_copy(BRIEF_NEWSLETTER, BRIEF_FUENTE, perfil=perfil_one)
    assert informe.veredicto == "rechazado"
    assert [v.nombre for v in informe.rechazados] == ["ordinales"]
    assert "'primera'" in informe.rechazados[0].hallazgos[0]


def test_la_escalera_decompromiso_ve_la_subida_del_brief(
    perfil_one: PerfilOrganizacion,
) -> None:
    """'seleccionada' -> 'contratada' sube un escalon y se ve.

    El post del brief es el fixture porque el fallo es real: el
    documento sube el estado laboral de una persona un peldano sin
    decirlo.

    La escalera NO consulta el perfil, asi que la primera mitad del
    test va con el perfil neutro: si la escalera dependiera de saber de
    quien es la organizacion, no seria una regla del motor. La segunda
    mitad verifica el copy del brief, y ese si necesita el perfil de
    ONE: sin el, "Oracle Cloud Infrastructure" sale como entidad
    inventada y el hallazgo taparia el de la escalera.
    """
    nivel_fuente, nombre_fuente = peldano_de(BRIEF_FUENTE.texto)
    nivel_copy, nombre_copy = peldano_de(BRIEF_POST)
    assert nombre_fuente == "seleccion"
    assert nombre_copy == "contrato"
    assert nivel_copy > nivel_fuente

    # Y el post, aislado de su superlativo, cae en la escalera.
    corregido = BRIEF_POST.replace("es el mejor camino", "es un camino")
    fallidos = [
        v.nombre
        for v in verificar_copy(corregido, BRIEF_FUENTE, perfil=perfil_one).rechazados
    ]
    assert fallidos == ["escalera de compromiso"]


def test_bajar_de_peldano_tambien_se_arregla(perfil_one: PerfilOrganizacion) -> None:
    """Dos correcciones de una palabra, y el post pasa.

    Borrar 'el mejor': 'un camino' es tan cierto como 'el mejor camino'
    pero no promete una comparacion que la fuente no hace. Bajar el
    peldano: 'fue seleccionada' en vez de 'acaba de ser contratada' hace
    que el post diga la verdad, porque la fuente dice que la
    seleccionaron, no que ya empezo a trabajar.

    El fix de cada hallazgo es de una palabra. Ese es el argumento de
    por que el check es util y no un obstaculo.

    Y queda avisado 'estudiante': no es un fallo, pero es una inferencia
    del sistema sobre una persona y tiene que estar a la vista. Que
    aparezca es la mitad de lo que este test afirma, y sale del perfil:
    un supuesto institucional lo DECLARA la organizacion, no el motor.
    """
    corregido = (
        BRIEF_POST.replace("es el mejor camino", "es un camino")
        .replace("acaba de ser contratada", "fue seleccionada para el puesto de")
    )
    informe = verificar_copy(corregido, BRIEF_FUENTE, perfil=perfil_one)
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


def test_sigla_ampliada_no_es_invencion(perfil_one: PerfilOrganizacion) -> None:
    """'OCI' en la fuente, 'Oracle Cloud Infrastructure' en el post.

    El mapa de expansiones es DATO del cliente: que "Oracle Cloud
    Infrastructure" sea la sigla de "OCI" lo sabe ONE, no el motor. Por
    eso el test pide el perfil de forma explicita, y por eso hace falta
    el otro test -- el que muestra que el perfil neutro SI lo rechaza --
    para que quede claro que la equivalencia no la tiene el motor.
    """
    fuente = Interaccion(autor="X", canal="c", texto="use OCI y el proyecto de IA")
    informe = verificar_copy(
        "el proyecto en Oracle Cloud Infrastructure con IA", fuente, perfil=perfil_one
    )
    assert not [v for v in informe.rechazados if v.nombre == "entidades"]


def test_abreviatura_de_puesto_no_es_invencion(perfil_one: PerfilOrganizacion) -> None:
    """El post dice 'Dev Jr', la fuente dice 'Desarrolladora Junior'.

    "Jr" abrevia a "junior" segun DECLARA la organizacion, asi que el
    perfil va explicito. Sin el, el motor no puede saber que "Dev Jr" y
    "Desarrolladora Junior" son el mismo puesto, y tendria que rechazar
    el uno o el otro.
    """
    fuente = Interaccion(autor="X", canal="c", texto="puesto de Desarrolladora Junior")
    informe = verificar_copy("entra como Dev Jr al equipo", fuente, perfil=perfil_one)
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


def test_un_hashtag_sin_respaldo_es_marco_y_no_fallo(
    perfil_one: PerfilOrganizacion,
) -> None:
    """'#CarreraDev' no esta en la fuente y no ES un hecho.

    Tratarlo como fallo seria prometer una verificacion que no existe.
    Va al reporte como marco, que es el trabajo del humano.

    '#OracleCloud' se apoya en que el perfil de ONE declara que
    "OracleCloud" es la sigla de "OCI", y "OCI" si esta en la fuente. Sin
    el perfil, los DOS serian marcos y la comparacion entre ellos
    desapareceria.
    """
    _, marcos = clasificar_hashtags(
        ["#TalentosTech", "#OracleCloud"], BRIEF_FUENTE.texto, perfil_one
    )
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


def test_el_faq_solo_deriva(perfil_one: PerfilOrganizacion) -> None:
    """`derivado_a_mentoria` es el patron correcto: no inventar respuesta.

    El brief responde una duda derivandola a mentoria en vez de escribir
    una respuesta tecnica que nadie verifico. El codigo tiene que poder
    hacer lo mismo, y ese estado existe justamente para eso.

    ESTE TEST PASABA POR EL MOTIVO EQUIVOCADO, Y HAY QUE DEJARLO
    DICHO.

    Antes usaba `BRIEF_FUENTE` —el mensaje de MARIANA— para la FAQ que
    en el brief es de LUCAS, y se apoyaba en que "LangGraph" estuviera
    en el perfil. Dos cosas mal:

    1. LangGraph NO es stack declarado de ONE. El plan de estudios
       publico de Oracle nombra IA, agentes, n8n, RAG y OCI. LangChain y
       LangGraph son el proyecto de la persona, y por eso los saca
       `perfiles/one.toml` de la lista institucional: nombrarlos sigue
       siendo una afirmacion que necesita respaldo.
    2. La fuente era la de otra persona. La FAQ se deriva de la duda
       de Lucas, y el mensaje de Mariana no habla de nodos de
       reintento.

    Y el perfil hacia de red: mientras "LangGraph" estuvo en la lista
    blanca, el test paso SIN que la fuente lo respaldara. Es decir,
    cubria el caso del brief tapando un emparejamiento equivocado.

    Arreglado usando la interaccion que realmente origina la FAQ: ahora
    "LangGraph" lo respalda el mensaje de quien pregunto, que es como
    tiene que funcionar. El perfil va explicito por otra razon que se
    documenta abajo, y NO para respaldar la entidad.
    """
    activo = SugerenciaFaq(
        tema="Tip: nodos de reintento en LangGraph",
        origen="Duda planteada en el canal de soporte",
        status="derivado_a_mentoria",
    )
    duda_de_lucas = Interaccion(
        autor="Lucas Albuquerque",
        canal="#dudas-langgraph",
        texto=(
            "Tengo dudas sobre como estructurar los nodos condicionales en "
            "LangGraph cuando la respuesta del LLM necesita reintento. "
            "Alguien tiene un ejemplo practico de router?"
        ),
    )
    informe = verificar_activo(
        activo, duda_de_lucas, campos_del_modelo=("tema",), perfil=perfil_one
    )
    assert informe.rechazados == []
    # Y el control que hacia falta: la MISMA FAQ contra el mensaje de
    # otra persona tiene que fallar, porque LangGraph no esta en el
    # mensaje de Mariana. Si esto pasa, la entidad se esta respaldando
    # con el perfil cuando no deberia.
    assert verificar_activo(
        activo, BRIEF_FUENTE, campos_del_modelo=("tema",), perfil=perfil_one
    ).rechazados != []


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

    Antes 'senior' y 'junior' estaban en `VOCABULARIO_INSTITUCIONAL`
    para que `Sr` y `Jr` no dieran falsos positivos, y el costo fue que
    el nivel del cargo dejo de verificarse en ningun lado: un post podia
    publicar "Desarrolladora Senior" sobre alguien que la fuente dice
    Junior. Es el mismo bug que "seleccionada" convertida en
    "contratada", y peor: el cargo de alguien es un hecho verificable y
    publicarlo inflado no se ve como publicarlo inflado.

    Esta prueba ya no depende de esa lista blanca, y esa es la mejora:
    el nivel no se whitelistea, se COMPARA. El vocabulario institucional
    hoy es el perfil neutro, que esta vacio, asi que si el nivel volviera
    a colarse por ahi, este test seguiria verde y el check habria
    vuelto a quedar sin teeth. Por eso `test_el_vocabulario_neutro_no_declara_niveles`
    vigila que la lista siga vacia.
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
    """Palabra capitalizada por posicion, no nombre propio.

    `VOCABULARIO_EDITORIAL` sigue siendo del motor y no del perfil, y a
    proposito: "Felicitaciones", "Gracias" o "Logro" son convenciones de
    escritura de una comunidad, no palabras de la marca de nadie. Si
    arrancaran a depender del perfil, el post de una organizacion sin
    perfil seria imposible de redactar.
    """
    assert "felicitaciones" in VOCABULARIO_EDITORIAL
    assert "Felicitaciones" not in entidades_en("Felicitaciones, Mariana!")


def test_una_marca_nueva_en_medio_de_frase_cae() -> None:
    """Si NO esta en la lista de editoriales, se reporta."""
    nombres = entidades_en("Leofi Purple presento el plan en la reunion")
    assert "Purple" in nombres


def test_ordinales() -> None:
    assert "primera" in ordinales_en("su primera oportunidad")
    assert ordinales_en("una vez mas") == set()


# ═══════════════════════════════════════════════════════════════════
#  EL INVARIANTE DEL REFACTOR: EL MOTOR NO SABE DE QUIEN
# ═══════════════════════════════════════════════════════════════════
#
# El punto de mover el conocimiento de marca a `perfiles/one.toml` es
# este: el MISMO texto, el MISMO motor, y veredictos distintos segun de
# quien sea el activo. Un perfil no es un dato que se carga por prolijidad:
# es lo que decide si el motor puede o no nombrar a la marca.
#
# Los tests de arriba usan el perfil de ONE porque afirman algo de
# ONE. Los de aca se quedan en el neutro, y por eso son la red de
# seguridad: si alguien vuelve a meter la marca en el codigo, el motor
# dejaria de ser generico y estos tests tienen que caerse.


#: Una fuente que NO menciona a Alura ni a ninguna marca. Si el copy
#: nombrara a Alura y la fuente no, el unico motivo para aceptarlo es el
#: perfil: por eso la fuente no dice nada de marcas.
FUENTE_SIN_MARCA = Interaccion(
    autor="Mariana Souza",
    canal="#logros",
    tipo="testimonio",
    texto="comparti el proyecto que arme durante el curso, con apoyo de la comunidad",
)


def test_el_perfil_neutro_rechaza_lo_que_el_de_one_acepta(
    perfil_one: PerfilOrganizacion, perfil_neutro: PerfilOrganizacion
) -> None:
    """'Alura' no es una entidad inventada para ONE, y si lo es sin perfil.

    Una sola palabra separa los dos veredictos. Con el perfil de ONE
    "Alura" es la marca de la organizacion y el post sale limpio; con el
    neutro no hay quien la respalde y sale rechazado. Si este test se
    rompe, o el motor volvio a hardcodear la marca, o un perfil dejo de
    cargarse: las dos son el bug que el refactor vino a matar.
    """
    copy = "Felicitaciones, Mariana! El proyecto lo apoya Alura"
    assert not perfil_neutro.vocabulario.marca
    assert "alura" in perfil_one.institutional_palabras

    sin_perfil = verificar_copy(copy, FUENTE_SIN_MARCA, perfil=perfil_neutro)
    con_perfil = verificar_copy(copy, FUENTE_SIN_MARCA, perfil=perfil_one)

    assert sin_perfil.veredicto == "rechazado"
    fallidos = [v for v in sin_perfil.rechazados if v.nombre == "entidades"]
    assert any("Alura" in h for h in fallidos[0].hallazgos)
    assert not con_perfil.rechazados


def test_una_segunda_organizacion_tiene_otro_vocabulario(
    perfil_one: PerfilOrganizacion,
) -> None:
    """El motor no esta atado a ONE: alcanza con otro perfil.

    Se construye EN EL TEST a proposito. Un segundo cliente en
    `perfiles/` probaria que el archivo se carga, no que el motor es
    generico: el objeto que se le pasa al motor tiene que poder ser
    cualquier marca, y eso se demuestra pasando una marca que no existe
    en ningun archivo.

    Las tres partes del mismo check con la misma fuente:
      * "Korda" con su perfil: es la marca, sale limpio.
      * "Korda" con el perfil de ONE: no es marca de nadie, se rechaza.
      * "Alura" con su perfil: la marca de OTRO, tambien se rechaza.
    Que un perfil no autorice la marca de otro es lo que impide que el
    motor termine aceptando un nombre propio cualquiera.
    """
    korda = PerfilOrganizacion(
        nombre="Korda",
        descripcion="Organizacion inventada, para probar que el motor es generico",
        identidad=Identidad(
            sector="comercio de barrio",
            audiencia=("vecinos",),
            prompt="Sos un redactor de Korda.",
        ),
        vocabulario=Vocabulario(
            marca=frozenset({"korda", "vecinal"}),
            autodescripcion=frozenset({"comercio", "vecinos"}),
        ),
        expansiones={"kordapay": "kpay"},
    )
    fuente = Interaccion(
        autor="Ana", canal="c", texto="abrimos el local el lunes y cobramos con Kpay"
    )

    # Su propia marca: el perfil la respalda y el motor no dice nada.
    limpio = verificar_copy("El local nuevo lo abre Korda", fuente, perfil=korda)
    assert limpio.veredicto == "aprobado"

    # Y su sigla, que no se reconoce porque este PERFIL la declara: en
    # otra fuente, "KordaPay" seria un nombre propio inventado.
    sigla = verificar_copy("KordaPay liquida en el local", fuente, perfil=korda)
    assert not [v for v in sigla.rechazados if v.nombre == "entidades"]

    # Ahora al reves, sobre una fuente que no nombra a nadie: cada
    # perfil autoriza la marca SUYA y ninguna mas. Con el perfil de ONE,
    # "Korda" es un nombre propio cualquiera; con el de Korda, "Alura"
    # -- que es marca de ONE -- es un nombre propio cualquiera. Un perfil
    # que aceptara la marca de otro seria un perfil que acepta cualquier
    # empresa que se le ocurra al modelo.
    sin_marcas = Interaccion(autor="Ana", canal="c", texto="abrimos el local el lunes")
    for etiqueta, perfil, marca in (
        ("de ONE", perfil_one, "Korda"),
        ("de Korda", korda, "Alura"),
    ):
        informe = verificar_copy(
            f"El local nuevo lo abre {marca}", sin_marcas, perfil=perfil
        )
        fallidos = [v for v in informe.rechazados if v.nombre == "entidades"]
        assert fallidos, f"el perfil {etiqueta} acepto la marca de otra organizacion"
        assert any(marca in h for h in fallidos[0].hallazgos)


def test_el_perfil_tambien_define_los_supuestos(
    perfil_one: PerfilOrganizacion, perfil_neutro: PerfilOrganizacion
) -> None:
    """Un supuesto institucional lo declara la organizacion, no el motor.

    "Nuestra estudiante" esta en el post del brief y en ninguna parte
    del mensaje de Mariana. El motor no lo rechaza porque inventar el
    estado de una persona es peor que suponerlo: lo marca para que un
    humano lo vea. Y para marcarlo hace falta que la organizacion haya
    declarado que la palabra sugiere pertenencia a un programa. El
    perfil neutro no declara nada, y por lo tanto no supone nada.
    """
    copy = "Nuestra estudiante Mariana Souza construyo el proyecto del curso"

    declarados = verificar_copy(copy, BRIEF_FUENTE, perfil=perfil_one)
    assert not declarados.rechazados
    assert any("estudiante" in s for s in declarados.supuestos)

    neutros = verificar_copy(copy, BRIEF_FUENTE, perfil=perfil_neutro)
    assert not neutros.supuestos
    assert not neutros.rechazados


def test_el_modulo_no_expone_constantes_derivadas_del_perfil_neutro() -> None:
    """No hay diccionario vacio con nombre de constante en `grounding`.

    `EXPANSIONES`, `ABREVIATURAS` y `SUPUESTOS_DECLARADOS` eran
    `dict(perfil_neutro.<campo>)`: siempre vacios, porque el perfil
    neutro no sabe de nadie. Leidas de arriba afirmaban "esta
    organizacion no tiene expansiones", cuando en realidad decian "no
    se donde estan". La trampa es que se ven bien: son del tipo
    correcto y no rompen nada. Por eso el invariante se testea sobre
    los NOMBRES, no sobre el comportamiento.

    Esta asercion falla si alguien reintroduce cualquiera de las tres,
    y el comentario al lado dice por que esa reintroduccion es un bug
    y no una comodidad.
    """
    import sieve.generation.grounding as g

    for nombre in ("EXPANSIONES", "ABREVIATURAS", "SUPUESTOS_DECLARADOS"):
        assert not hasattr(g, nombre), (
            f"{nombre} volvio a definirse: es un {dict.__name__} vacio "
            "derivado del perfil neutro, y un objeto que miente sobre lo "
            "que sabe es peor que un AttributeError. Para consultarlo se "
            "pasa el perfil: verificar_copy(copy, fuente, perfil=...)"
        )
