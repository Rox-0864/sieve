"""
Grounding: la comprobacion mecanica de que un activo generado no inventa.

El problema que este modulo resuelve, medido y no supuesto
------------------------------------------------------
Se le dio a `qwen2.5:3b` una sola interaccion de 159 caracteres y se le
pidio un post de LinkedIn. En UNA generacion:

  1. `potencial_engagement: 120` -- un numero de engagement que no esta
     en la fuente. Lo atrapa el TIPO: `Literal["Alto","Medio","Bajo"]`.
  2. Los 5 hashtags en UN solo string. Pasa `list[str]`. Nada lo ve.
  3. `#Emprendimiento` y `#TrabajoDeInnovacion`: no estan en la fuente,
     y ella fue contratada como dev junior, no como emprendedora.
  4. `"un tiempo"` en lugar de `"tres meses"`. No invento un numero:
     BORRO el que estaba, y el post perdio lo unico concreto que tenia.
  5. `"Mi Travesia"`, `"Estudiante Solitario"`. Ella no dijo eso. Nadie
     lo dijo.

De esos, el sistema de tipos ya atrapaba el 1. Ese fue el hallazgo que
ordeno todo lo demas:

    TODO CAMPO QUE NO PUEDE ESTAR VACIO ES UN SLOT QUE EL MODELO
    LLENA CON FICCION.

El modelo no invento `120` por estupidez: el campo no tenia forma de
decir "no lo se", asi que puso algo. Por eso `potencial_engagement` es
un enum de tres palabras y no un numero. Quien escribio eso entendia el
problema, y este modulo aplica el mismo criterio al resto del copy.

La escalera de compromiso
-------------------------
El propio documento ONE contiene el ejemplo de la falla, y falla en su
propio ejemplo oficial. De la MISMA fuente salen dos piezas con
comportamiento distinto:

  fuente:      "queda seleccionada para el puesto de Desarrolladora
                Junior de IA"
  post:        "Mariana Souza acaba de ser contratada como Desarrolladora
                Junior de IA"
  newsletter:  "Estudiante consigue empleo dev con portfolio de IA"

"seleccionada" -> "contratada" -> "consigue empleo". Cada escalon es un
hecho nuevo sobre el empleo de una persona real, y el documento sube
dos. A eso se le suma "su primera oportunidad", que no aparece en
NINGUN lado de la fuente: el historial de carrera de Mariana queda
inventado de punta a punta.

Ese es el fallo mas peligroso de la lista, y es invisible para un
validador de esquema. Un esquema dice "este campo es un string"; no dice
"este string contradice a la fuente".

Lo que este modulo NO puede hacer
--------------------------------
Verificar que "Estudiante Solitario" sea verdad. Ella no lo dijo, asi
que no hay nada contra que compararlo: las palabras de emocion no se
verifican contra una fuente que no las contiene. Ningun regex del mundo
resuelve eso.

La unica defensa que queda es reducir el espacio: no darle al modelo
mucho texto donde meterse. Y poner un humano delante, que es lo que el
brief llama "Panel de Curaduria" (linea 315) y que el equipo dejo como
diferencial opcional. Con la curadura OBLIGATORIA, este modulo es la
primera capa, no la unica. El limite se documenta en vez de esconderse.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from sieve.models import Interaccion

# ═══════════════════════════════════════════════════════════════════
#  NORMALIZACION
# ═══════════════════════════════════════════════════════════════════
#
# Todo el matching es insensible a acentos y a mayusculas por una razon
# que no es de estilo: el modelo escribe "practicos" donde la fuente
# tiene "prácticas", y un check que se rompe por una tilde reporta un
# falso positivo. Un check que miente es peor que no tener check.


def _sin_acentos(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def _normalizar(texto: str) -> str:
    """Minusculas, sin acentos, espacios colapsados.

    Se usa para COMPARAR, nunca para mostrar: el reporte tiene que
    mostrar el texto real, con tildes, porque es el texto que un humano
    va a editar en el panel de curaduria.
    """
    return re.sub(r"\s+", " ", _sin_acentos(texto).lower()).strip()


def _contiene(agujon: str, paja: str) -> bool:
    """`agujon` aparece como palabra completa dentro de `paja`."""
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(agujon)}(?![a-z0-9])", paja))


# ═══════════════════════════════════════════════════════════════════
#  CIFRAS
# ═══════════════════════════════════════════════════════════════════
#
# Hay que entender numeros en PALABRAS porque la fuente dice "tres
# meses" y un post puede decir "3 meses". Si solo se miran digitos, el
# check pasa con dos fuentes distintas y pierde su sentido.
#
# "un", "una" y "uno" NO son cifras aca, y es una decision medida. Un
# post legitimo dice "es un camino" y el check reportaba "1 no aparece
# en la fuente". Un check que llora lobo por un articulo indefinido se
# desacredita solo: el humano aprende a saltear el renglon y con el se
# van los hallazgos que SI importan. "treinta y uno" sigue funcionando
# por la via de los compuestos, y "millon" se cuenta aparte.
#
# El numero pegado a una letra se ignora a proposito: "GPT-4", "v2",
# "3B" y "Python3" son parte de un nombre propio, no una afirmacion
# sobre la comunidad. Confundirlos con cifras produce ruido, y un check
# que se queja de "LangChain2" entrena a ignorar el check entero.

_UNIDADES: dict[str, int] = {
    "cero": 0, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
}
_VEINTE: dict[str, int] = {
    "veinte": 20, "veintiuno": 21, "veintidos": 22, "veintitres": 23,
    "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26,
    "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
}
_DECENAS: dict[str, int] = {
    "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
    "setenta": 70, "ochenta": 80, "noventa": 90,
}
_CENTENAS: dict[str, int] = {"cien": 100, "ciento": 100}
#: Solo se usa COMPUESTOS ("treinta y uno"), donde "uno" no puede ser
#: el articulo indefinido y si es un numero. Por eso "uno" vive aca y
#: no en `_UNIDADES`.
_DECIMALES: dict[str, int] = {
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
}

_ORDINALES: frozenset[str] = frozenset({
    "primer", "primera", "primero", "primeras", "primeros", "primerisimo",
    "segundo", "segunda", "tercer", "tercera", "tercero", "cuarto", "cuarta",
    "quinto", "quinta", "sexto", "sexta", "septimo", "septima", "octavo",
    "octava", "noveno", "novena", "decimo", "decima", "unico", "unica",
    "unicos", "unicas", "mitad", "doble", "triple",
})


def _valor_numero(palabra: str) -> int | None:
    for tabla in (_UNIDADES, _VEINTE, _DECENAS, _CENTENAS):
        if palabra in tabla:
            return tabla[palabra]
    if palabra in ("mil", "millones", "millon", "millares", "millar"):
        return 1_000_000 if palabra.startswith("millon") else (
            100_000 if palabra.startswith("millar") else 1000
        )
    return None


def cifras_en(texto: str) -> set[int]:
    """Los valores numericos del texto, en digitos o en palabras."""
    normalizado = _normalizar(texto)
    valores: set[int] = set()

    # Digitos sueltos. El lookaround de letras es lo que excluye
    # "GPT-4" y "v2": van pegados a un token, son parte del nombre.
    for coincidencia in re.finditer(r"(?<![a-z0-9._-])\d+(?![a-z0-9._-])", normalizado):
        valores.add(int(coincidencia.group()))

    for palabra in re.findall(r"\b[a-z]+\b", normalizado):
        valor = _valor_numero(palabra)
        if valor is not None:
            valores.add(valor)

    # Compuestos: "treinta y cinco", "ciento veinte", "30 y cinco",
    # "treinta y uno".
    #
    # El operando derecho se busca como PALABRA en _DECIMALES y no con
    # _valor_numero, porque "uno" salio de la tabla de numeros para que
    # el articulo indefinido no se cuente como 1 -- y "treinta y uno"
    # tiene que seguir valiendo 31. Y el izquierdo admite digitos,
    # porque "30 y cinco" es 35 igual que "treinta y cinco".
    decenas = set(_DECENAS.values())
    for izquierda, derecha in re.findall(r"\b([a-z0-9]+)\s+y\s+([a-z]+)\b", normalizado):
        a = int(izquierda) if izquierda.isdigit() else _valor_numero(izquierda)
        b = _DECIMALES.get(derecha)
        if a is None or b is None:
            continue
        if a in decenas:
            valores.add(a + b)          # treinta y cinco
        elif a == 100 and b < 100:
            valores.add(100 + b)         # ciento veinte
        elif a == 1000 and b < 100:
            valores.add(1000 * b)        # mil doscientos
    return valores


def ordinales_en(texto: str) -> set[str]:
    return {p for p in re.findall(r"\b[a-z]+\b", _normalizar(texto)) if p in _ORDINALES}


# ═══════════════════════════════════════════════════════════════════
#  ENTIDADES
# ═══════════════════════════════════════════════════════════════════

#: Siglas y expansiones legitimas. El documento usa "OCI" en la fuente y
#: "Oracle Cloud Infrastructure" en el post: no es una invencion, es la
#: misma cosa escrita distinto. Sin este mapa, el check de entidades
#: rechazaria el ejemplo oficial del propio brief.
EXPANSIONES: dict[str, str] = {
    "oracle cloud infrastructure": "oci",
    "oracle cloud": "oci",
    "infraestructura oracle cloud": "oci",
    "oraclecloud": "oci",
    "inteligencia artificial": "ia",
    "inteligenciaartificial": "ia",
    "aprendizaje automatico": "ml",
    "aprendizajeautomatico": "ml",
    "machine learning": "ml",
    "machinelearning": "ml",
    "modelo de lenguaje": "llm",
    "modelodelenguaje": "llm",
    "redes neuronales": "red neuronal",
    "redesneuronales": "red neuronal",
    "programacion": "programacion",
}

#: Abreviaturas que equivalen a la palabra larga. El post del brief
#: escribe "Dev Jr" donde la fuente dice "Desarrolladora Junior": es la
#: misma persona con el mismo puesto, no un dato nuevo. Sin este mapa
#: el check reporta a "Jr" como una entidad inventada.
ABREVIATURAS: dict[str, str] = {
    "jr": "junior", "jrta": "junior", "sr": "senior", "srta": "seniorita",
    "dev": "dev", "srjr": "senior", "tec": "tecnologico", "org": "organizacion",
}

#: Nombres propios de la institucion y del ecosistema tecnico. No
#: necesitan estar en la fuente: son las palabras que ya sabemos
#: ciertas y que un revisor no deberia tener que verificar.
VOCABULARIO_INSTITUCIONAL: frozenset[str] = frozenset({
    "one", "oracle", "alura", "linkedin", "twitter", "discord", "telegram",
    "n8n", "langchain", "langgraph", "python", "docker", "kubernetes",
    "github", "git", "aws", "azure", "gcp", "cloudflare", "streamlit",
    "fastapi", "pydantic", "postgresql", "mysql", "mongodb", "redis",
    "openai", "anthropic", "gemini", "claude", "llama", "qwen", "ollama",
    "cuda", "oci", "ia", "api", "llm", "sql", "html", "css", "ux", "ui",
    "hackathon", "bootcamp", "senior", "junior", "trainee", "dev",
    "data science", "cloud", "web", "backend", "frontend", "fullstack",
    "generativa", "generativo", "infraestructura", "stack",
})

##: Palabras que el copy de una institucion SIEMPRE capitalize y que no
#: son entidades: "Felicitaciones, Mariana!" empieza con mayuscula y
#: "Felicitaciones" no es una empresa. Sin esta lista el check reporta
#: una entidad llamada "Felicitaciones" y se vuelve ruido.
#:
#: Aqui viven tambien las palabras de publicitaria institucional
#: ("talentos", "historias", "logro"). Son encuadre, no informacion: se
#: permiten en silencio porque un post sin "nuestros talentos" no es un
#: post de este programa. Lo que NO entra aca es nada que afirme algo
#: sobre una persona: esas van a SUPUESTOS_DECLARADOS y se reportan.
VOCABULARIO_EDITORIAL: frozenset[str] = frozenset({
    "felicitaciones", "felicidades", "gracias", "hola", "buenos", "querida",
    "querido", "queridos", "queridas", "hoy", "logro", "logros", "talento",
    "talentos", "historia", "historias", "comunidad", "programa", "aqui",
    "alli", "cerca", "lejos", "ademas", "finalmente", "durante", "despues",
    "esta", "este", "esto", "estos", "estas", "estilos", "destacado",
    "destacada", "destacados", "destacadas", "equipo", "oportunidad",
    "oportunidades", "logica", "sabiduria",
})


def _es_mencion(fragmento: str) -> bool:
    limpio = fragmento.strip()
    return limpio.startswith(("#", "@")) or limpio.startswith("r/")


#: Un run capitalizado MAXIMO, con conectores internos en minuscula.
#: "Oracle Cloud Infrastructure" tiene que llegar al check como UNA
#: entidad: si llega como tres, la clave "oracle cloud infrastructure"
#: de EXPANSIONES nunca se consulta y una sigla legitima se reporta
#: como invencion. El conector en minuscula ("y", "de") corta el run.
#: Cada palabra siguiente es O capitalized O un conector en minuscula
#: seguido de otra capitalized. La version con dos grupos encadenados
#: fallaba: el grupo de conectores se consumia antes de tiempo y
#: "LangChain y Oracle Cloud Infrastructure" se partia en dos, con
#: "Cloud Infrastructure" sin expanslon que lo respaldara.
_RUN_CAPITALIZADO = re.compile(
    r"[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ]*"
    r"(?:\s+(?:[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ]*"
    r"|(?:de|del|la|el|los|las|y|of|the)\s+[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ]*))*"
)


def entidades_en(texto: str) -> list[str]:
    """Runs capitalizados que no abren frase ni son menciones.

    Se quitan los conectores en minuscula del resultado porque no son
    parte de ningun nombre: "LangChain y Oracle" son dos entidades, no
    una que se llame "LangChain y Oracle".
    """
    encontradas: list[str] = []
    for oracion in re.split(r"[.!?;:\n]+", texto):
        # En espanol todo adjetivo de posicion se escribe con mayuscula
        # y "Nada nos da mas orgullo" no habla de una entidad "Nada".
        cuerpo = re.sub(r"^\s*\S+", " ", oracion)
        for coincidencia in _RUN_CAPITALIZADO.finditer(cuerpo):
            run = re.sub(
                r"\s+(?:de|del|la|el|los|las|y|of|the)\s+", " ", coincidencia.group()
            ).strip()
            if not run or _es_mencion(run):
                continue
            if _normalizar(run) in VOCABULARIO_EDITORIAL:
                continue
            encontradas.append(run)
    # Se deduplican conservando el orden: el reporte lista cada entidad
    # una vez, no una vez por cada vez que se nombro.
    return list(dict.fromkeys(encontradas))


def _parte_respalda(parte: str, fuente_n: str, permitidas: set[str]) -> bool:
    """Una palabra de una entidad multi-palabra tiene respaldo."""
    if parte in permitidas or _contiene(parte, fuente_n):
        return True
    larga = ABREVIATURAS.get(parte)
    return larga is not None and (larga in permitidas or _contiene(larga, fuente_n))


def _respaldado(entidad: str, fuente_n: str, permitidas: set[str]) -> bool:
    """La entidad se sostiene con la fuente, el vocabulario o una expansion."""
    base = _normalizar(entidad)
    if base in permitidas or _contiene(base, fuente_n):
        return True
    expansion = EXPANSIONES.get(base)
    if expansion and _contiene(expansion, fuente_n):
        return True
    abreviatura = ABREVIATURAS.get(base)
    if abreviatura and (_contiene(abreviatura, fuente_n) or abreviatura in permitidas):
        return True
    # Multi-palabra: todas las partes tienen que estar respaldadas. Se
    # exige TODAS y no "algunas" porque "Mariana Souza Jr" tiene dos
    # partes respaldadas y un apellido inventado, y basta una para que
    # sea un apellido nuevo.
    # Una expansion puede ocupar UN TRAMO de la entidad y no toda ella.
    # "LangChain Oracle Cloud Infrastructure" no es una sigla, pero
    # "Oracle Cloud Infrastructure" si lo es, asi que se buscan todas
    # las subsecuencias contiguas de 2 y 3 partes.
    partes = [p for p in base.split() if p]
    for largo in (3, 2):
        for inicio in range(len(partes) - largo + 1):
            tramo = " ".join(partes[inicio:inicio + largo])
            base_tramo = EXPANSIONES.get(tramo)
            if base_tramo and _contiene(base_tramo, fuente_n):
                return True

    return bool(partes) and all(
        _parte_respalda(p, fuente_n, permitidas) for p in partes
    )


def check_entidades(copy: str, fuente: str, autor: str) -> Verificacion:
    permitidas = set(VOCABULARIO_INSTITUCIONAL) | {_normalizar(autor)}
    fuente_n = _normalizar(fuente)
    foundadas = [e for e in entidades_en(copy) if not _respaldado(e, fuente_n, permitidas)]
    return Verificacion(
        "entidades",
        ok=not foundadas,
        hallazgos=[f"'{e}' no esta en la fuente ni en el vocabulario" for e in foundadas],
    )


# ═══════════════════════════════════════════════════════════════════
#  ESCALERA DE COMPROMISO
# ═══════════════════════════════════════════════════════════════════
#
# El estado laboral de una persona real es el hecho con mas dano
# reputacional posible en un post de una empresa: es la clase de dato
# que se comparte, se cita y dura. Y es el mas facil de subir un
# escalon sin querer, porque "seleccionada" y "contratada" describen
# situaciones parecidas y el modelo no ve la diferencia.
#
# Se modela como escalera y no como lista de palabras prohibidas
# porque el problema no es la palabra: es la DISTANCIA con la fuente.

# Las inflexiones van COMPLETAS (`seleccionad[oa]s?`) y no como prefijo
# ("seleccionad"). Con el prefijo, el `(?![a-z0-9])` del final se come la
# terminacion: "seleccionada" es "seleccionad" + "a", el lookahead ve
# una letra y NO matchea. El resultado era que la palabra "seleccionada"
# -- la mas importante de la escalera -- nunca se detectaba, y el
# brief pasaba por un check roto.
# Las formas van COMPLETAS y con las dos boundaries.
#
# Dos bugs reales hicieron falta para llegar aca, y los dos importan:
#
#  1. Con el prefijo ("seleccionad"), el `(?![a-z0-9])` final se come
#     la terminacion: "seleccionada" es "seleccionad" + "a", el lookahead
#     ve una letra y no matchea. La palabra MAS IMPORTANTE de la
#     escalera nunca se detectaba.
#  2. Con un stem ("contrat"), "contrataron" no matchea: el verbo es
#     contratar -> contrat+aron, no contrat+ad+on. Y con el prefijo
#     corregido, "empleadora" matchea "empleado" + "r" si se saca el
#     boundary.
#
# La forma correcta de expresar "estas son las palabras" es enumerar las
# palabras. Las inflexiones del espanol son irregulares y no hay patron
# que las cubra sin falsos positivos. Ademas el boundary se conserva, asi
# que "empleadora" (que es "empleador", no "empleada") no entra.
_ESCALERA: tuple[tuple[str, str, int], ...] = (
    (
        r"sue[nñ]os?|sue[nñ]a|sue[nñ]an|so[nñ]ar|aspirar|"
        r"aspiraci[oó]n|querr[ií]a|quisi[oó]|me gustar[ií]a",
        "deseo",
        0,
    ),
    (
        r"busc[oa]s?|buscamos|buscan|buscando|buscar|aplic[oa]s?|"
        r"aplicamos|aplican|aplicando|postul[oa]s?|postulamos|postulan|"
        r"postulando|candidat[oa]s?",
        "postulacion",
        1,
    ),
    (
        r"entrevist[oa]s?|entrevistamos|entrevistan|entrevistando|"
        r"entrevistar|preliminar|finalistas?|aspirantes?|proceso de",
        "entrevista",
        2,
    ),
    (
        r"seleccion|seleccionad[oa]s?|selecciona[sn]?|seleccion[oó]|"
        r"seleccionamos|seleccionar|aceptad[oa]s?|acepta[sn]?|acept[oó]|"
        r"aceptamos|"
        r"aceptar|ofertas?|propuestas?|elegid[oa]s?|aprobad[oa]s?",
        "seleccion",
        3,
    ),
    (
        r"contratad[oa]s?|contrata[sn]?|contrat[oó]|contratamos|contratan|"
        r"contratando|contratar|contrataron|contrat[ée]|emplead[oa]s?|"
        r"emplea[sn]?|empleamos|emplean|empleando|emplear|ingres[oó]|"
        r"ingresamos|ingresaron|ya trabaj[oa]|comenz[oó] a trabajar|"
        r"arranc[oó]|pasa a formar|se suma al equipo",
        "contrato",
        4,
    ),
)


def _coincidencias(patron: str, texto: str) -> list[str]:
    """Las palabras literales del texto que matchean el patron."""
    normalizado = _normalizar(texto)
    return [
        m.group()
        for m in re.finditer(rf"(?<![a-z0-9])(?:{patron})(?![a-z0-9])", normalizado)
    ]


def peldano_de(texto: str) -> tuple[int, str]:
    """El peldano mas alto de la escalera que aparece en el texto."""
    normalizado = _normalizar(texto)
    peldano, nombre = -1, "ninguno"
    for patron, etiqueta, nivel in _ESCALERA:
        if re.search(rf"(?<![a-z0-9])(?:{patron})(?![a-z0-9])", normalizado) and (
            nivel > peldano
        ):
            # Solo se actualiza el nombre si el nivel SUBE. Con
            # `max()` en la primera version, un peldano bajo que aparecia
            # despues pisaba el nombre del mas alto: "el proceso de
            # seleccion" devolvia 'entrevista' en vez de 'seleccion',
            # porque "proceso de" (nivel 2) matchea antes que
            # "seleccion" (nivel 3) y se quedaba con el ultimo.
            peldano, nombre = nivel, etiqueta
    return peldano, nombre


# ═══════════════════════════════════════════════════════════════════
#  SUPERLATIVOS
# ═══════════════════════════════════════════════════════════════════
#
# "es el mejor camino" no esta en la fuente. Es publicitaria, no un
# hecho sobre la persona, y aun asi lo dice el post oficial del brief.
# Se marca como fallo DURO a proposito, y la eleccion es discutible,
# asi que va con su razon:
#
#   Si el superlativo fuera un aviso blando, el reporte seria una
#   pared de lineas y el humano terminaria aprobando sin leer. Un
#   check que se queja de todo entrena a ignorar el check. Un check
#   que corta trae el problema de vuelta, con la frase exacta a
#   borrar, y borrar "es el mejor camino" no rompe nada: queda "es un
#   camino", que es cierto.
#
# El costo real de esta decision es trabajo humano de mas, y es
# preferible a publicar superlativos que la fuente no dice.

_SUPERLATIVOS: frozenset[str] = frozenset({
    "mejor", "peor", "unico", "unica", "unicos", "unicas", "optimo",
    "optima", "perfecto", "perfecta", "excepcional", "excepcionalmente",
    "increible", "impresionante", "magnifico", "imparable", "insuperable",
    "revolucionario", "revolucionaria", "siempre", "nunca", "jamas",
    "definitivamente", "indiscutible", "largest", "maximo", "maxima",
})


# ═══════════════════════════════════════════════════════════════════
#  EL INFORME
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Verificacion:
    """El resultado de UN check. Nunca un bool pelado.

    `hallazgos` lleva el fragmento literal que hay que mirar. Sin el
    texto, un check que falla obliga a reabrir el activo y compararlo
    con la fuente a ojo, que es exactamente el trabajo que el panel de
    curaduria vino a evitar.
    """

    nombre: str
    ok: bool
    hallazgos: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        if self.ok:
            return f"[ok   ] {self.nombre}"
        return f"[FALLA] {self.nombre}: " + "; ".join(self.hallazgos)


Veredicto = Literal["aprobado", "aprobado_con_supuestos", "rechazado"]


@dataclass(frozen=True)
class InformeGrounding:
    """Todo lo que hay que mirar antes de publicar un activo."""

    verificaciones: list[Verificacion]
    supuestos: list[str] = field(default_factory=list)
    marcos: list[str] = field(default_factory=list)

    @property
    def rechazados(self) -> list[Verificacion]:
        return [v for v in self.verificaciones if not v.ok]

    @property
    def veredicto(self) -> Veredicto:
        if self.rechazados:
            return "rechazado"
        if self.supuestos or self.marcos:
            return "aprobado_con_supuestos"
        return "aprobado"

    @property
    def publicable(self) -> bool:
        """El gate. El unico lugar del proyecto donde se decide esto.

        Deliberadamente NO mira `curado`: la firma humana se evalua
        junto a este veredicto y no dentro, para que un activo no pueda
        declararse publicable por su propio contenido. La funcion que
        junta las dos cosas es `puede_publicarse`, y es la que el
        pipeline y la CLI tienen que llamar.
        """
        return self.veredicto != "rechazado"

    def resumen(self) -> str:
        lineas = [f"VEREDICTO: {self.veredicto}"]
        lineas += [f"  {v}" for v in self.verificaciones]
        if self.supuestos:
            lineas.append("  supuestos declarados (revisar):")
            lineas += [f"    - {s}" for s in self.supuestos]
        if self.marcos:
            lineas.append("  marcos editoriales, NO verificables:")
            lineas += [f"    - {m}" for m in self.marcos]
        return "\n".join(lineas)


def puede_publicarse(informe: InformeGrounding, curado: bool) -> bool:
    """Grounding limpio Y alguien lo firmo. Las dos condiciones.

    Un activo con el grounding impecable y `curado=False` no es
    publicable, y uno firmado con un fallo de grounding tampoco. Vive
    en una funcion y no en un campo del activo para que sea imposible
    publicar saltandolo por error: se lee de un solo lugar.
    """
    return informe.publicable and curado


# ═══════════════════════════════════════════════════════════════════
#  LOS CHECKS
# ═══════════════════════════════════════════════════════════════════
#
# Cada check toma (texto_generado, texto_fuente) y devuelve una
# `Verificacion`. Todos leen la FUENTE y el ARTEFACTO, nunca lo que el
# modelo declara que uso: si el gate dependiera de las declaraciones,
# un modelo que no declara nada pasaria todos los checks, que es el
# peor de los mundos posibles.


def check_cifras(copy: str, fuente: str) -> Verificacion:
    nuevas = cifras_en(copy) - cifras_en(fuente)
    return Verificacion(
        "cifras",
        ok=not nuevas,
        hallazgos=[f"'{n}' no aparece en la fuente" for n in sorted(nuevas)],
    )


def check_entidades_texto(copy: str, fuente: str, autor: str) -> Verificacion:
    return check_entidades(copy, fuente, autor)


def check_compromiso(copy: str, fuente: str) -> Verificacion:
    nivel_fuente, nombre_fuente = peldano_de(fuente)
    nivel_copy, nombre_copy = peldano_de(copy)
    if nivel_copy <= nivel_fuente:
        return Verificacion("escalera de compromiso", ok=True)

    # El mensaje trae la PALABRA LITERAL, no solo el nombre del peldano.
    # "la fuente llega a 'seleccion' y el post sube a 'contrato'" obliga
    # a abrir los dos textos y buscar cual era la palabra; "el post dice
    # 'contratada' y la fuente solo 'seleccionada'" se corrige directo.
    # El reporte existe para que nadie tenga que hacer esa tarea a ojo.
    palabras_copy = [
        palabra
        for patron, etiqueta, nivel in _ESCALERA
        if nivel == nivel_copy
        for palabra in _coincidencias(patron, copy)
    ]
    palabras_fuente = [
        palabra
        for patron, etiqueta, nivel in _ESCALERA
        if nivel == nivel_fuente
        for palabra in _coincidencias(patron, fuente)
    ]
    return Verificacion(
        "escalera de compromiso",
        ok=False,
        hallazgos=[
            f"el post dice {'/'.join(sorted(set(palabras_copy)))} y la fuente "
            f"solo {'/'.join(sorted(set(palabras_fuente)))}: es un hecho "
            "nuevo sobre el empleo de alguien real"
        ],
    )


#: Niveles profesionales, de menor a mayor.
#:
#: "senior" y "junior" estan en `VOCABULARIO_INSTITUCIONAL`, y por eso
#: `check_entidades` NO los revisa: la lista blanca existe para que `Sr` y
#: `Jr` no producer falsos positivos, y cumple. El costo de esa lista
#: blanca es que el nivel del cargo de una persona con nombre propio
#: dejo de verificarse en ningun lado, y un post podia publicar
#: "Desarrolladora Senior" sobre alguien que la fuente dice Junior.
#:
#: Es el mismo bug que "seleccionada" convertida en "contratada", y es
#: peor: el cargo junior de alguien es un hecho verificable sobre su
#: carrera, y publicarlo inflado no se ve como publicarlo inflado.
#:
#: Asi que el nivel no se whitelistea: se COMPARA. Si el copy dice un
#: nivel, el fuente tiene que decir el mismo.
NIVELES_PROFESIONALES: dict[str, int] = {
    "trainee": 0,
    "practicante": 0,
    "junior": 1,
    "semi senior": 2,
    "senior": 3,
    "principal": 4,
    "staff": 4,
    "lead": 4,
    "head": 5,
    "director": 5,
}


def _niveles(texto: str) -> set[str]:
    """Los marcadores de nivel que aparecen como palabras sueltas."""
    texto_n = _normalizar(texto)
    return {
        palabra
        for palabra in NIVELES_PROFESIONALES
        if re.search(r"\b" + re.escape(palabra) + r"\b", texto_n)
    }


def check_profesional(copy: str, fuente: str) -> Verificacion:
    """El nivel del cargo no se cambia, en ninguna direccion.

    Se comparan los conjuntos, no solo "el copy no puede subir". Bajar
    tambien es un hecho falso: decir que alguien es junior cuando la
    fuente dice senior es una afirmacion inexacta sobre una carrera real,
    y el impacto reputacional es el mismo con el signo cambiado.

    El trade-off, dicho explicitamente: "reunion con el equipo senior"
    es un falso positivo, porque la fuente no menciona ningun nivel. Se
    acepta a proposito. Un hallazgo de mas cuesta que un humano mire
    una frase; un cargo inflado en un post con nombre propio no se ve,
    y ese es el costo que este modulo existe para evitar.
    """
    del_copy = _niveles(copy)
    if not del_copy:
        return Verificacion("nivel profesional", ok=True)
    del_fuente = _niveles(fuente)
    nuevos = del_copy - del_fuente
    if not nuevos:
        return Verificacion("nivel profesional", ok=True)
    if not del_fuente:
        return Verificacion(
            "nivel profesional",
            ok=False,
            hallazgos=[
                f"el post dice {sorted(nuevos)} y la fuente no dice ningun "
                "nivel: es un cargo nuevo sobre alguien real"
            ],
        )
    return Verificacion(
        "nivel profesional",
        ok=False,
        hallazgos=[
            f"el post dice {sorted(nuevos)} y la fuente dice {sorted(del_fuente)}"
        ],
    )


def check_ordinales(copy: str, fuente: str) -> Verificacion:
    nuevos = ordinales_en(copy) - ordinales_en(fuente)
    if not nuevos:
        return Verificacion("ordinales", ok=True)
    return Verificacion(
        "ordinales",
        ok=False,
        hallazgos=[f"'{o}' no aparece en la fuente" for o in sorted(nuevos)],
    )


def check_superlativos(copy: str, fuente: str) -> Verificacion:
    fuente_n = _normalizar(fuente)
    nuevos = sorted(
        p for p in re.findall(r"\b[a-z]+\b", _normalizar(copy))
        if p in _SUPERLATIVOS and not _contiene(p, fuente_n)
    )
    return Verificacion(
        "superlativos",
        ok=not nuevos,
        hallazgos=[f"'{s}' no aparece en la fuente" for s in nuevos],
    )


def check_citas(copy: str, fuente: str) -> Verificacion:
    """Comillas tipograficas o rectas: el texto citado tiene que existir.

    Reutiliza el criterio de `analysis/citas.py` aplicado al activo. Una
    cita textual es la forma mas fuerte de afirmacion y por eso la mas
    facil de falsear sin que nadie lo note releyendo rapido.
    """
    fuente_n = _normalizar(fuente)
    inventadas: list[str] = []
    for patron in (r"[\"“]([^\"”]{8,})[\"”]", r"'([^']{8,})'"):
        for coincidencia in re.finditer(patron, copy):
            citada = coincidencia.group(1).strip()
            if _normalizar(citada) not in fuente_n:
                inventadas.append(citada)
    return Verificacion(
        "citas textuales",
        ok=not inventadas,
        hallazgos=[f"'{c}' no esta en la fuente" for c in inventadas],
    )


# ═══════════════════════════════════════════════════════════════════
#  HASHTAGS: ENCUADRE, NO HECHOS
# ═══════════════════════════════════════════════════════════════════
#
# Un hashtag no afirma nada refutable. "#CarreraDev" es encuadre, no
# informacion, y el ejemplo oficial del brief trae cuatro hashtags de los
# cuales dos no aparecen en la fuente.
#
# Que un hashtag no verificable NO sea un fallo tampoco es una decision
# de comodidad: seria otra forma de mentir, prometer una verificacion
# que no existe. Lo que corresponde es que los marcos LLEGUEN al humano,
# que es el trabajo del panel de curaduria. Por eso van fuera del camino
# critico, y el codice lo dice.


def separar_hashtags(tags: Iterable[str]) -> tuple[list[str], list[str]]:
    """(hashtags bien formados, hashtags que no son uno-por-item).

    El segundo valor es el hallazgo real de la corrida de prueba: el
    modelo devolvio los cinco hashtags del post en UN string. Pasa
    `list[str]` con un solo elemento y no es lo que el contrato quiere.
    """
    bien: list[str] = []
    mal: list[str] = []
    for tag in tags:
        piezas = [p for p in re.split(r"[\s,]+", tag.strip()) if p]
        if len(piezas) > 1 or (piezas and not piezas[0].startswith("#")):
            mal.append(tag)
        elif piezas:
            bien.append(piezas[0])
    return bien, mal


def clasificar_hashtags(
    tags: Sequence[str], fuente: str
) -> tuple[list[str], list[str]]:
    """(hashtags con respaldo, marcos editoriales)."""
    fuente_n = _normalizar(fuente)
    con_respaldo: list[str] = []
    marcos: list[str] = []
    for tag in tags:
        base = _normalizar(tag.lstrip("#"))
        expansion = EXPANSIONES.get(base)
        if (
            _contiene(base, fuente_n)
            or base in VOCABULARIO_INSTITUCIONAL
            or (expansion is not None and _contiene(expansion, fuente_n))
        ):
            con_respaldo.append(tag)
        else:
            marcos.append(tag)
    return con_respaldo, marcos


#: Supuestos de contexto sobre PERSONAS que el sistema hace, declarados.
#:
#: "Nuestra estudiante" esta en el post oficial del brief y no esta en la
#: fuente: nadie escribio que Mariana sea estudiante. Se deduce del
#: contexto -- la comunidad es de estudiantes de ONE -- y es correcto.
#:
#: La diferencia entre un supuesto y un invento no es la veracidad: es
#: que el supuesto es el MISMO para todos los mensajes, esta escrito
#: aca, y el humano lo ve. Un supuesto declarado y global es una regla
#: del negocio. Un supuesto por mensaje, invisible, es fabricacion.
SUPUESTOS_DECLARADOS: dict[str, str] = {
    "estudiante": "los miembros de la comunidad son estudiantes del programa",
    "estudiantes": "los miembros de la comunidad son estudiantes del programa",
    "egresad": "los miembros de la comunidad egresaron del programa",
    "participante": "los miembros de la comunidad participan del programa",
}


def supuestos_de(copy: str, fuente: str) -> list[str]:
    """Palabras de contexto usadas sin respaldo, declaradas como supuesto."""
    fuente_n = _normalizar(fuente)
    usados = set(re.findall(r"\b[a-z]+\b", _normalizar(copy)))
    return [
        f"'{palabra}': {SUPUESTOS_DECLARADOS[palabra]}"
        for palabra in sorted(usados)
        if palabra in SUPUESTOS_DECLARADOS and not _contiene(palabra, fuente_n)
    ]


# ═══════════════════════════════════════════════════════════════════
#  API
# ═══════════════════════════════════════════════════════════════════


def corpus_fuente(fuente: Interaccion) -> str:
    """TODO lo que un activo puede afirmar sin inventar, en un string.

    Incluye el `autor` y el `canal`, no solo el `texto`, y hay una razon
    medida: el post oficial del brief escribe "Mariana Souza" y la
    fuente NUNCA dice "Mariana" -- el nombre solo vive en el campo
    `autor`. Un check que compara contra el texto solo rechaza el
    ejemplo de referencia por una falla de plumbing.

    Y el nombre no es una invencion: la persona esta identificada por el
    propio mensaje que escribio. Por eso va en el corpus.
    """
    return f"{fuente.autor} {fuente.canal} {fuente.texto}"


def verificar_copy(copy: str, fuente: Interaccion | str, autor: str = "") -> InformeGrounding:
    """Pasa UN texto generado por los siete checks.

    `fuente` acepta el `Interaccion` entero o solo el texto. Aceptar los
    dos evita que el llamador tenga que acordarse de extraer `.texto` y
    de la chance de pasar el texto equivocado.
    """
    if isinstance(fuente, Interaccion):
        texto_fuente = corpus_fuente(fuente)
        nombre_autor = fuente.autor
    else:
        texto_fuente = f"{autor} {fuente}" if autor else fuente
        nombre_autor = autor
    if not nombre_autor and isinstance(fuente, str):
        raise ValueError("con una fuente de texto plano hace falta el autor")

    return InformeGrounding(
        verificaciones=[
            check_cifras(copy, texto_fuente),
            check_entidades_texto(copy, texto_fuente, nombre_autor),
            check_compromiso(copy, texto_fuente),
            check_profesional(copy, texto_fuente),
            check_ordinales(copy, texto_fuente),
            check_superlativos(copy, texto_fuente),
            check_citas(copy, texto_fuente),
        ],
        supuestos=supuestos_de(copy, texto_fuente),
    )


def campos_textuales(
    activo: BaseModel, *, campos: Sequence[str] | None = None
) -> dict[str, str]:
    """Los campos de texto de un activo, sin metadatos.

    Se leen del MODELO y no de un dict porque los tres activos tienen
    campos distintos y distintos campos numericos. Hardcodear la lista
    de campos en el generador es como aparecio `120` en
    `potencial_engagement`: un campo que el generador no conoce lo
    inventa.

    `campos` es la lista de campos que escribio el MODELO, y existe
    porque este modulo se equivoco al adivinarlo. Sin el parametro,
    `seccion = "Logro de la Semana"` -- que escribe el SISTEMA, con
    precision para que el modelo no lo escriba -- pasaba por el check de
    entidades y(reportaba) que "Semana" no estaba en la fuente. Ningun
    newsletter podia publicarse, y ningun test de M4 lo vio porque todos
    llamaban a `verificar_copy` y no a esta funcion.

    La lesson es la de siempre: un check de contenido no puede pasar
    por campos que NO son contenido inventable. Por defecto (`None`) se
    toman todos los de texto, que es lo comodo para inspeccionar un
    activo a mano.
    """
    permitidos = set(campos) if campos is not None else None
    texto: dict[str, str] = {}
    for nombre, definicion in type(activo).model_fields.items():
        if definicion.annotation is not str:
            continue
        if permitidos is not None and nombre not in permitidos:
            continue
        valor = getattr(activo, nombre, None)
        if isinstance(valor, str) and len(valor) >= 3:
            texto[nombre] = valor
    return texto


def verificar_activo(
    activo: BaseModel,
    fuente: Interaccion,
    *,
    campos_del_modelo: Sequence[str] | None = None,
) -> InformeGrounding:
    """Pasa los campos de texto del MODELO, mas los hashtags.

    Los hashtags van por un camino distinto y no son un check: son
    encuadre, se separan y se reportan como marcos. Un activo sin
    hashtags -- el newsletter y el FAQ no tienen -- no falla por eso.
    """
    textos = campos_textuales(activo, campos=campos_del_modelo)
    if not textos:
        raise ValueError(f"{type(activo).__name__} no tiene texto para verificar")

    verificaciones: list[Verificacion] = []
    for campo, valor in textos.items():
        for v in verificar_copy(valor, fuente).verificaciones:
            if not v.ok:
                verificaciones.append(
                    Verificacion(
                        f"{campo}/{v.nombre}", ok=False, hallazgos=v.hallazgos
                    )
                )

    tags = list(getattr(activo, "hashtags", []) or [])
    _, malformados = separar_hashtags(tags)
    for mal in malformados:
        verificaciones.append(
            Verificacion(
                "formato de hashtags",
                ok=False,
                hallazgos=[f"'{mal}' no es un hashtag por item: son varios juntos"],
            )
        )
    _, marcos = clasificar_hashtags(
        [t for t in tags if t.startswith("#")], corpus_fuente(fuente)
    )

    if not verificaciones:
        verificaciones.append(Verificacion("campos de texto", ok=True))

    todos = " ".join(textos.values())
    return InformeGrounding(
        verificaciones=verificaciones,
        supuestos=supuestos_de(todos, corpus_fuente(fuente)),
        marcos=marcos,
    )
