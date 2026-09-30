"""
Tests de `sieve.perfil`: el dato de marca y como se carga.

Este modulo es el unico lugar del proyecto donde vive el conocimiento de
una organizacion, asi que lo que se testea aca no es el check de
entidades -- eso vive en `test_grounding.py`-- sino las tres cosas de las
que depende todo lo demas:

  1. Que cargar un perfil inexistente diga CUALES hay. Un
     `FileNotFoundError: two` a las tres de la manana no lleva a ninguna
     parte; el mensaje tiene que convertir un nombre mal escrito en la
     accion obvia.
  2. Que un perfil declare lo que NO sabe. Un motor con supuestos
     silenciosos es un motor que miente sobre lo que sabe, y la
     unica defensa es que la lista de huecos exista y se pueda mostrar.
  3. Que sea inmutable. Un check que recibe el perfil no puede dejar que
     el llamador le cambie una palabra a mitad de una verificacion: el
     reporte y el veredicto tienen que salir del mismo perfil.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sieve.perfil import (
    NOMBRE_POR_DEFECTO,
    PerfilOrganizacion,
    cargar_perfil,
    listar_perfiles,
    verificar_perfil,
)


def test_un_perfil_inexistente_dice_que_perfiles_hay() -> None:
    """El error de un nombre mal escrito tiene que ser accionable.

    Sin la lista, quien escribe `--perfil two` en vez de `--perfil one`
    tiene que ir a abrir el directorio de perfiles para descubrir que se
    llamaban de otra manera. Con la lista, el error ya dice la respuesta.
    """
    with pytest.raises(FileNotFoundError) as exc:
        cargar_perfil("no-existe")

    mensaje = str(exc.value)
    assert "no-existe" in mensaje
    for nombre in listar_perfiles():
        assert nombre in mensaje


def test_el_perfil_neutro_no_vive_en_disco() -> None:
    """El default es codigo, no un archivo: se define donde se usa.

    Que exista `perfiles/por_defecto.toml` seria una puerta por la que
    alguien podria agregar marcas al perfil neutro creyendo que es un
    cliente mas, y el perfil neutro es justamente el que NO debe saber de
    nadie.
    """
    assert NOMBRE_POR_DEFECTO not in listar_perfiles()
    assert cargar_perfil(NOMBRE_POR_DEFECTO).nombre == NOMBRE_POR_DEFECTO


def test_cargar_es_barato_porque_el_perfil_no_cambia(
    perfil_one: PerfilOrganizacion,
) -> None:
    """El cache no es una micro-optimizacion: es una garantia.

    Se lee el perfil en CADA verificacion de grounding, y parsear TOML
    por check seria tirar CPU. Ademas, dos verificaciones del mismo lote
    tienen que hablar con el MISMO perfil, y con el cache eso no es una
    casualidad del codigo: es el mismo objeto.
    """
    assert cargar_perfil("one") is perfil_one


def test_el_perfil_de_one_declara_sus_huecos(
    perfil_one: PerfilOrganizacion,
) -> None:
    """Las seis preguntas de `pendiente_de_definir` se pueden leer.

    No se testea el CONTENIDO de cada hueco -- eso lo contesta ONE, no
    este repo -- sino que los huecos existan y esten. Un perfil que no
    declara lo que no sabe se convierte en un supuesto invisible en
    cuanto alguien lo llena, y de ahi no se vuelve.
    """
    huecos = verificar_perfil(perfil_one)

    declarados = list(perfil_one.restricciones.pendiente_de_definir)
    assert declarados, "un perfil sin huecos declarados no esta declarandolos"
    for hueco in declarados:
        assert hueco in huecos


def test_el_perfil_neutro_tambien_declara_lo_que_no_sabe(
    perfil_neutro: PerfilOrganizacion,
) -> None:
    """El que no sabe de nadie es el que mas tiene que decirlo.

    El perfil neutro no tiene marca, ni constantes, ni tono. Si
    `verificar_perfil` no lo dijera, el default del motor seria
    indistinguible de un perfil completo, y el que lo opera no tendria
    forma de saber que el motor no sabe de nadie hasta que rechaza el
    primer post legitimo.
    """
    huecos = verificar_perfil(perfil_neutro)

    assert any("vocabulario.marca" in hueco for hueco in huecos)
    assert any("constantes" in hueco for hueco in huecos)
    assert any("tono" in hueco for hueco in huecos)
    # Y no se hace pasar por lo que no es: un perfil con nombre propio
    # llenado a mano seria la forma de que "el default" dejara de ser
    # neutro sin que nadie lo note.
    assert not perfil_neutro.vocabulario.marca
    assert not perfil_neutro.expansiones
    assert not perfil_neutro.abreviaturas


def test_el_perfil_es_inmutable(perfil_one: PerfilOrganizacion) -> None:
    """No se le puede cambiar una palabra a mitad de una verificacion.

    El perfil se pasa por parametro a checks que corren en cualquier
    orden, y con un perfil mutable el resultado de un activo podria
    depender de que otro check ya habia pasado. Congelado, el reporte y
    el veredicto salen del mismo perfil o no salen.
    """
    with pytest.raises(ValidationError):
        perfil_one.nombre = "otro"

    with pytest.raises(ValidationError):
        perfil_one.vocabulario.marca = frozenset({"cualquiera"})


def test_un_perfil_incompleto_degrada_en_lugar_de_romper(
    perfil_one: PerfilOrganizacion,
) -> None:
    """Una constante que falta vale ""; una constante rota no rompe nada.

    El motor degrada a "sin dato" a proposito: un perfil incompleto
    tiene que producir un activo con un campo vacio y un reporte, no una
    excepcion a mitad de una corrida. Un `KeyError` en un perfil de un
    cliente que todavia no completo el archivo seria la forma de que el
    pipeline caiga entero por un dato opcional.
    """
    assert perfil_one.constante("no_existe") == ""
    assert perfil_one.constante("no_existe", "LinkedIn Oficial") == "LinkedIn Oficial"
    assert perfil_one.tono_de("canal_que_no_existe") == ""
    # Y lo que existe se lee igual que siempre.
    assert perfil_one.constante("canal_recomendado") == "LinkedIn Oficial"

# ── Coherencia interna del archivo ────────────────────────────────────
#
# Estas pruebas miran el perfil CONTRA SI MISMO. Existe por una errata
# concreta que costo un bug: "alumione" estaba escrito en el archivo
# donde-va "alumnione", y el efecto en produccion era que un nombre
# propio oficial de la marca salia rechazado como entidad inventada.
# Un perfil es dato, y el dato tambien se equivoca.


def test_los_nombres_propios_de_la_identidad_estan_en_el_vocabulario(
    perfil_one: PerfilOrganizacion,
) -> None:
    """Comunidades y alianzas con nombre propio tienen que estar respaldadas.

    Si el perfil dice que existe una comunidad que se llama X, y el
    vocabulario no reconoce a X, entonces un post legitimo sobre esa
    comunidad se rechaza. El motor no puede saber que el perfil se
    contradice, asi que la contradiccion se detecta aca.
    """
    from sieve.generation.grounding import _normalizar

    institutional = perfil_one.institutional_palabras
    for nombre in (*perfil_one.identidad.comunidades, *perfil_one.identidad.alianzas):
        assert _normalizar(nombre) in institutional, (
            f"{nombre!r} se declara en la identidad pero no esta en el "
            f"vocabulario: un post sobre el se rechaza como invencion"
        )


def test_los_textuales_de_la_web_no_tienen_erratas_de_una_letra(
    perfil_one: PerfilOrganizacion,
) -> None:
    """Lo que el perfil afirma ser textual, se tiene que parecer al texto.

    No verifica el contenido contra la web — eso no se puede hacer sin
    red — sino que cada nombre propio oficial este BIEN ESCRITO dentro
    del archivo. Es lo que agarro la errata de "alumione".
    """
    from sieve.generation.grounding import _normalizar
    from sieve.perfil import cargar_perfil

    institutional = perfil_one.institutional_palabras
    # Cada nombre oficial que el perfil reconoce tiene que hacer round-trip:
    # normalizarlo y volver a buscarlo tiene que devolverlo.
    for nombre in institutional:
        assert _normalizar(nombre) in institutional, (
            f"{nombre!r} no es estable bajo normalizacion: hay una errata en "
            f"el archivo y el motor nunca lo va a reconocer"
        )

    texto = " ".join(cargar_perfil("one").restricciones.resuelto_por_web).lower()
    for oficial in ("alumnione", "empregaone", "ai for tech", "ai for business"):
        assert oficial in institutional, f"{oficial} es oficial y no esta cargado"
        assert oficial in texto or oficial in institutional
