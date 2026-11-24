"""Generacion de activos: el modelo propone, el grounding lo cross-examina.

NOTA SOBRE `EXPANSIONES`, `VOCABULARIO_INSTITUCIONAL` y
`SUPUESTOS_DECLARADOS`: Estas constantes se exportaban desde aca y
ya NO existen ni se exportan. Antes vivian en el modulo y tenian el
conocimiento de una organizacion concreto escrito a mano; ahora eso
vive en `perfiles/<cliente>.toml` y lo lee un `PerfilOrganizacion`.

Se podria exportar "la constante del perfil neutro", que es un
conjunto VACIO. Y eso es una trampa: un `VOCABULARIO_INSTITUCIONAL`
vacio no dice "ninguna palabra es institucional", dice "no se donde
esta la informacion". Un objeto que miente sobre lo que sabe es peor
que un `AttributeError`. Para consultarlo se pasa el perfil:

    verificar_copy(copy, fuente, perfil=cargar_perfil("one"))

Las tres se sacaron por el mismo motivo y en el mismo orden. Primero
dejaron de exportarse, pero seguirian definidas en `grounding.py`
como `dict(perfil_neutro.expansiones)`: un diccionario VACIO con
nombre de constante, que leido de arriba afirma "esta organizacion no
tiene expansiones" cuando en realidad dice "no se donde estan". Un
default que miente es peor que un `AttributeError`, asi que ahora no
existen.
"""


from sieve.generation.assets import (
    CANAL_LINKEDIN,
    SECCION_NEWSLETTER,
    ErrorDeGeneracionError,
    GeneradorActivos,
    ResultadoGeneracion,
)
from sieve.generation.grounding import (
    VOCABULARIO_EDITORIAL,
    InformeGrounding,
    Veredicto,
    Verificacion,
    check_profesional,
    cifras_en,
    clasificar_hashtags,
    entidades_en,
    ordinales_en,
    peldano_de,
    puede_publicarse,
    separar_hashtags,
    supuestos_de,
    verificar_activo,
    verificar_copy,
)

__all__ = [
    "CANAL_LINKEDIN",
    "ErrorDeGeneracionError",
    "GeneradorActivos",
    "InformeGrounding",
    "ResultadoGeneracion",
    "SECCION_NEWSLETTER",
    "check_profesional",
    "VOCABULARIO_EDITORIAL",
    "Veredicto",
    "Verificacion",
    "cifras_en",
    "clasificar_hashtags",
    "entidades_en",
    "ordinales_en",
    "peldano_de",
    "puede_publicarse",
    "separar_hashtags",
    "supuestos_de",
    "verificar_activo",
    "verificar_copy",
]
