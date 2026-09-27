"""
Analizador heuristico. Sin red, sin key, sin dependencias.

Este es el PISO del sistema, no un plan B. Si el LLM no esta disponible,
este codigo tiene que seguir produciendo un `Analisis` valido, porque:

  - la demo tiene que correr en una maquina sin credenciales
  - los tests tienen que ser deterministas
  - un rate limit no puede tirar 20 analisis que se podian resolver con
    una lista de palabras

Y hay una razon de fondo mas interesante: un evaluador que solo puede
medir contra el LLM no puede distinguir "el modelo es bueno" de "el
modelo y la heuristica coinciden". Con una referencia determinista se
puede medir el LLM de verdad.

Que NO hace, y es importante decirlo: no intenta entender el texto. No
hay embeddings, no hay sintaxis, no hay nada inteligente. Hay listas de
palabras. EsCRIBE asi a proposito, porque un sistema de scoring que se
puede explicar en una pagina es defendible delante de alguien, y uno que
usa un modelo de 400 MB de parametros no.

El margen de error es alto y esta escrito en los docstrings. Preferimos
un heuristic malo y honesto a un modelo opaco.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from sieve.analysis.citas import citas_de_ventana
from sieve.models import Analisis, Interaccion, Sentimiento


def sin_acentos(texto: str) -> str:
    """Minusculas, sin acentos, con espacios colapsados.

    Quitar acentes es lo que hace que "duda" y "DÚDA" sean la misma
    palabra. Es una perdida de informacion que aceptamos porque el
    costo de no hacerlo es que medio diccionario no matchea nunca.
    """
    plano = "".join(
        c for c in unicodedata.normalize("NFD", texto.lower())
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", plano)


def _normalizar_tabla(tabla: dict[str, float]) -> dict[str, float]:
    """Deja las claves listas para comparar contra texto normalizado.

    ESTA es la funcion que evita una clase entera de bugs silenciosos.
    El diccionario se escribe en español natural, con acentos y como lo
    escribe un humano ("conseguí", "aprobé", "difícil"). Antes se comparaba
    ese texto contra un mensaje ya normalizado, y el resultado era que
    esas palabras NO MATCHEABAN NUNCA, sin error ni warning: el mensaje
    salia neutro, el test no existia, y nadie se enteraba.

    El arreglo barato era escribir las dos variantes a mano ("consegui" Y
    "conseguí"). Funciona, pero es una bomba de reloj: la proxima palabra
    que se agregue sin acordarse queda muerta, y se descubre en produccion.
    Normalizar aqui una sola vez hace que el error no se pueda cometer.
    """
    return {sin_acentos(clave): valor for clave, valor in tabla.items()}


def _normalizar_frases(frases: tuple[str, ...] | set[str]) -> set[str]:
    """Lo mismo que `_normalizar_tabla`, para los conjuntos de palabras."""
    return {sin_acentos(f) for f in frases}


# ═══════════════════════════════════════════════════════════════════
#  Listas de palabras
#
#  Se escriben CON acentos, como las escribe una persona. Las claves se
#  normalizan al final de esta seccion. Si alguna clave queda acentuada
#  despues de ese punto, es un bug, y `tests/test_heuristico.py` lo
#  falla a proposito.
# ═══════════════════════════════════════════════════════════════════

#: Pesos de sentimiento. Los valores importan menos que el hecho de que
#: sean explicitos: "no se" puntua negativo por "no", y eso esta bien
#: porque "no me puedo contratar" ES una experiencia mala.
POSITIVO_CRUDO: dict[str, float] = {
    "contrataron": 0.9, "contratada": 0.9, "contratado": 0.9, "empleo": 0.6,
    "trabajo": 0.4, "primer": 0.4, "primera": 0.4, "logro": 0.8, "logre": 0.8,
    "conseguí": 0.8, "gradué": 0.9, "graduada": 0.9, "graduado": 0.9,
    "aprobé": 0.7, "promocioné": 0.9, "primer trabajo": 0.9, "buen trabajo": 0.7,
    "increíble": 0.8, "genial": 0.7, "bueno": 0.5, "mejor": 0.6, "gracias": 0.4,
    "ayudó": 0.7, "aprendí": 0.6, "superé": 0.7, "orgulloso": 0.8, "orgullosa": 0.8,
    "encantado": 0.8, "funciona": 0.5, "resuelto": 0.6, "logré": 0.8,
    "publicado": 0.5, "aceptado": 0.7, "aporte": 0.4, "aporté": 0.4,
    "destacado": 0.6, "oportunidad": 0.6,
}

NEGATIVO_CRUDO: dict[str, float] = {
    "problema": -0.6, "error": -0.6, "falla": -0.6, "fallo": -0.6, "roto": -0.7,
    "bug": -0.5, "congelado": -0.7, "congelé": -0.7, "perdí": -0.7,
    "rechazado": -0.8, "rechazada": -0.8, "no pude": -0.7, "imposible": -0.7,
    "frustrado": -0.8, "frustrante": -0.8, "cansado": -0.5, "difícil": -0.5,
    "atascado": -0.7, "atascada": -0.7, "no entiendo": -0.6, "no sé": -0.5,
    "no se": -0.4, "nunca": -0.5, "urgente": -0.4, "malo": -0.6, "peor": -0.7,
    "triste": -0.7, "decepcionado": -0.8, "abandono": -0.5, "renuncié": -0.6,
    "vergüenza": -0.7, "silencio": -0.3, "feo": -0.4,
}

#: Palabras que sugieren un hito profesional o personal. Mas fuertes que
#: el sentimiento: contratar a alguien importa mas que "estuvo genial".
HITO_CRUDO: dict[str, float] = {
    "contrataron": 0.95, "contratada": 0.95, "contratado": 0.95,
    "me contrataron": 0.95, "primer trabajo": 0.9, "primer empleo": 0.9,
    "oferta de trabajo": 0.9, "me ofrecieron": 0.85, "recibí la oferta": 0.9,
    "gradué": 0.9, "graduado": 0.85, "graduada": 0.85, "promocioné": 0.9,
    "ascendido": 0.9, "ascenso": 0.8, "me ascendieron": 0.9,
    "aprobé": 0.7, "aprobé el examen": 0.8, "lancé": 0.8, "publicado": 0.6,
    # "aceptado" a secas es ambiguo: puede ser un proyecto que entra a un
    # programa (hito) o un sistema que acepta una contrasena (nada). La
    # pasiva lo resuelve: "fue aceptado" significa que un tercero le
    # acepto algo a uno, y eso SI es un logro. "acepto" en activa no.
    "aceptado": 0.7, "acepté": 0.7, "fue aceptado": 0.85, "aceptaron mi": 0.9,
    "contribución": 0.7,
    "entrevista": 0.7, "oferta": 0.8, "certificación": 0.7, "beca": 0.8,
    "premio": 0.85, "mi primera": 0.75, "mi primer": 0.75, "tengo mi": 0.6,
}

#: Interrogativos. La lista sola no alcanza, y el error es sutil: "que"
#: aparece en "más que el título", "donde" en "la ciudad donde nací",
#: "como" en "como en la Escuela". Son relativos o comparativos, no
#: preguntas, y un filtro que las cuenta como pregunta manda al router la
#: mitad de los logs como FAQ.
#:
#: Por eso un interrogativo solo cuenta si esta EN POSICION DE PREGUNTA:
#: al principio de la oracion, o despues de un signo de cierre. Ver
#: `_es_posicion_de_pregunta`.
INTERROGATIVAS_CRUDAS = {
    "cómo", "qué", "cuándo", "cuánto", "cuáles", "dónde", "por qué", "para qué",
}

#: Estas si son seSenales de pregunta aunque esten en medio de la frase:
#: en una comunidad, "alguien sabe" y "ayuda" son casi siempre pedidos.
MARCAS_DE_PEDIDO_CRUDAS = {
    "alguien", "saben", "sabe", "podrían", "ayuda", "help", "duda",
}

#: Saludos. La coma cuenta como frontera de clausula SOLO despues de un
#: saludo, porque "Hola, cómo anda" es una pregunta y "El proyecto, que
#: fue difícil, terminó" es una relativa. Medir la longitud de lo que
#: precede no alcanza: "El proyecto" son dos palabras y parece un saludo.
SALUDOS_CRUDOS = {
    "hola", "buenas", "buenos", "hey", "saludos", "qué tal", "qué onda",
    "buen día", "buenas tardes", "qué más",
}

#: Temas. Un diccionario curado a mano, no un clasificador. El criterio
#: es: si el tema no esta aca y no aparece en la salida, es que el
#: concepto no es central todavia. Un mapa que crece con el uso.
TEMAS_CRUDOS: dict[str, tuple[str, ...]] = {
    "empleo": ("contrat", "trabajo", "empleo", "cv", "currículum", "entrevista",
               "oferta", "vacante", "postular", "reclut"),
    "aprendizaje": ("aprend", "estudi", "curso", "tutorial", "libro", "guía",
                    "practica", "práctica", "ejercicio", "mentoría", "ruta",
                    "aprendizaje"),
    "proyectos": ("proyecto", "portfolio", "repositorio", "repo", "github",
                  "aplicación", "web", "app", "código", "codigo"),
    "herramientas": ("python", "javascript", "react", "sql", "docker", "git",
                     "linux", "n8n", "langgraph", "openai", "llm", "api"),
    "comunidad": ("comunidad", "grupo", "discord", "meetup", "encuentro",
                  "networking", "evento", "charla"),
    "logros": ("logro", "conseguí", "terminé", "terminó", "completé", "cerré", "public"),
}

#: Corte de `es_logro`. Vive aca, no escrito en el `if`, porque la razon
#: necesita saber si el hito llego o no para no llamarlo "hito" cuando
#: solo fue una mencion. Un umbral en dos lugares es un umbral que se
#: desincroniza.
UMBRAL_HITO = 0.85

#: Temas que en una comunidad tecnica suelen ser RUIDO. Un mensaje de
#: "hola buen dia" no es un tema, es un mensaje. Tratarlo como tema
#: infla el newsletter con palabras vacias.
TEMAS_RUIDO_CRUDOS = {"hola", "buenos días", "buen día", "gracias", "chau", "adiós", "hey"}

# ═══════════════════════════════════════════════════════════════════
#  Normalizacion. A partir de aqui, todo indice contra texto ya normalizado.
# ═══════════════════════════════════════════════════════════════════

POSITIVO = _normalizar_tabla(POSITIVO_CRUDO)
NEGATIVO = _normalizar_tabla(NEGATIVO_CRUDO)
HITO = _normalizar_tabla(HITO_CRUDO)
INTERROGATIVAS = _normalizar_frases(INTERROGATIVAS_CRUDAS)
MARCAS_DE_PEDIDO = _normalizar_frases(MARCAS_DE_PEDIDO_CRUDAS)
SALUDOS = _normalizar_frases(SALUDOS_CRUDOS)
TEMAS = {
    tema: _normalizar_frases(pistas) for tema, pistas in TEMAS_CRUDOS.items()
}
TEMAS_RUIDO = _normalizar_frases(TEMAS_RUIDO_CRUDOS)


@dataclass(frozen=True, slots=True)
class ResultadoHeuristico:
    """Resultado intermedio antes de convertirlo en `Analisis`."""

    sentimiento: Sentimiento
    score: float
    temas: list[str] = field(default_factory=list)
    es_logro: bool = False
    es_pregunta: bool = False
    relevant: float = 0.0
    razon: str = ""
    citas: list[str] = field(default_factory=list)


class AnalizadorHeuristico:
    """Analisis lexical. Determinista, instantaneo, gratis."""

    def analizar(self, interaccion: Interaccion) -> ResultadoHeuristico:
        texto = interaccion.texto
        minuscula = sin_acentos(texto)

        score, palabras_pos, palabras_neg = self._sentimiento(minuscula)
        hito, palabras_hito = self._hito(minuscula)
        es_pregunta = self._es_pregunta(texto, minuscula)
        temas = self._temas(minuscula)

        # El score final no es el de sentimiento: es "vale la pena
        # surfacearlo". Un mensaje muy positivo que es solo un "hola
        # gracias" no vale la pena. Un logro moderate en un tema
        # relevante, sí.
        relevant = self._relevancia(score, hito, es_pregunta, temas, minuscula)

        return ResultadoHeuristico(
            sentimiento=self._a_enum(score),
            score=round(score, 4),
            temas=temas,
            es_logro=hito >= UMBRAL_HITO,
            es_pregunta=es_pregunta,
            relevant=round(relevant, 4),
            razon=self._razon(
                score, hito, es_pregunta, temas, palabras_hito, palabras_pos, palabras_neg
            ),
            citas=citas_de_ventana(texto),
        )

    def a_analisis(self, interaccion: Interaccion) -> Analisis:
        """Atajo para el camino sin `Analisis` de por medio."""
        r = self.analizar(interaccion)
        return Analisis(
            mensaje_id=interaccion.mensaje_id,
            sentimiento=r.sentimiento,
            score_sentimiento=r.score,
            temas=r.temas,
            es_logro=r.es_logro,
            es_pregunta=r.es_pregunta,
            # Las citas de la heuristica SIEMPRE son reales: salen del
        # texto por corte, no de un modelo. Es la diferencia entre una
        # cita verificable y una verificable-sin-verificacion.
            citas=r.citas,
            relevant=r.relevant,
            razon_relevante=r.razon,
        )

    # ─── Piezas ──────────────────────────────────────────────────

    @staticmethod
    def _normalizar(texto: str) -> str:
        """Atajo al helper de modulo. Ver `sin_acentos`."""
        return sin_acentos(texto)

    @staticmethod
    def _buscar(texto: str, tabla: dict[str, float]) -> tuple[float, list[str]]:
        """Suma los pesos de las palabras encontradas. Sin conteo repetido.

        Que NO cuente repetido es una decision: "muy muy muy bueno" no
        es tres veces mas bueno. Si se contara, un mensaje con una
        palabra repetida diez veces dominaria el ranking, y eso mide
        insistencia, no valor.
        """
        total = 0.0
        encontradas: list[str] = []
        for palabra, peso in tabla.items():
            # Buscar como palabra completa, no como substring: "no" no
            # debe matchear "notebook".
            patron = rf"(?<!\w){re.escape(palabra)}(?!\w)"
            if re.search(patron, texto):
                total += peso
                encontradas.append(palabra)
        return total, encontradas

    def _sentimiento(self, texto: str) -> tuple[float, list[str], list[str]]:
        pos, palabras_pos = self._buscar(texto, POSITIVO)
        neg, palabras_neg = self._buscar(texto, NEGATIVO)
        bruto = pos + neg
        # Acotar: 20 palabras positivas no es "muy positivo", es un
        # mensaje enthusiastic mal calibrado.
        return max(-1.0, min(1.0, bruto)), palabras_pos, palabras_neg

    def _hito(self, texto: str) -> tuple[float, list[str]]:
        total, encontradas = self._buscar(texto, HITO)
        return (min(total, 1.0) if encontradas else 0.0), encontradas

    def _es_pregunta(self, texto_original: str, texto: str) -> bool:
        """Deteccion de preguntas en dos señales, en orden de fuerza.

        1. El "?" o el "¿" inicial. Es lo unico concluyente.
        2. Un interrogativo EN POSICION DE PREGUNTA, o una marca de pedido.

        La posicion importa mas que la palabra. "Mas que el titulo" y
        "la ciudad donde naci" tienen interrogativos, pero no son
        preguntas; contarlas como tales manda al router la mitad de los
        logs como FAQ y el router se vuelve inservible.

        Sobre la coma: "Hola, como se hace un portfolio?" es el patron mas
        comun de una comunidad, y ahi la coma si abre una pregunta. Pero
        abrir la puerta a toda coma rompe con las relativas ("el
        proyecto, que fue dificil, termino"). El corte es acotado: la
        coma cuenta SOLO si lo que la precede es un saludo de la lista
        `SALUDOS`. Medir la longitud de lo anterior no servia: "El
        proyecto" son dos palabras y parece un saludo. Es un compromise
        deliberado: preferimos perder una pregunta sin "?" a clasificar
        de FAQ un testimonio real, porque el error de sobra publica un
        testimonio como pregunta y el de falta esconde uno.
        """
        if texto_original.rstrip().endswith("?"):
            return True
        if texto_original.lstrip().startswith("¿"):
            return True

        for palabra in INTERROGATIVAS | MARCAS_DE_PEDIDO:
            for match in re.finditer(rf"(?<!\w){re.escape(palabra)}(?!\w)", texto):
                if self._es_posicion_de_pregunta(texto, match.start()):
                    return True
        return False

    @staticmethod
    def _es_posicion_de_pregunta(texto: str, inicio: int) -> bool:
        """Esta palabra, en este offset, abre una pregunta?"""
        antes = texto[:inicio].rstrip()
        if not antes:
            # Interrogativo al principio de la frase. Puede ser una
            # pregunta ("Cuando llega el bus?") o una subordinada
            # ("Cuando llegue a mi casa, cene."). Las separa la coma: una
            # subordinada que abre la oracion lleva coma adentro, una
            # pregunta no. Es heuristico, no gramatica, pero en comunidad
            # la diferencia es muy consistente.
            resto = texto[inicio:]
            hasta_frase = re.split(r"[.!]", resto, maxsplit=1)[0]
            return "," not in hasta_frase
        if antes[-1] in "!?;:\n":
            return True
        if antes[-1] == ",":
            return antes[:-1].strip() in SALUDOS
        return False

    def _temas(self, texto: str) -> list[str]:
        encontrados: list[str] = []
        for tema, pistas in TEMAS.items():
            for pista in pistas:
                if re.search(rf"(?<!\w){re.escape(pista)}", texto):
                    encontrados.append(tema)
                    break
        return encontrados[:6]

    def _relevancia(
        self, score: float, hito: float, es_pregunta: bool, temas: list[str], texto: str
    ) -> float:
        """Combina las señales en 0..1. Todo el detalle esta aca.

        Los pesos no son "los correctos": son los que se pueden
        explicar en una frase, que es el unico criterio que importa
        para algo que despues se va a publicar.
        """
        # Un mensaje de 3 palabras no vale la pena por su sentimiento.
        longitud = min(len(texto) / 200.0, 1.0)

        # Saludo puro: mucho sentimiento positivo y cero contenido.
        base = 0.1 if (texto in TEMAS_RUIDO or len(texto) < 25) else 0.15

        base += 0.35 * abs(score)
        base += 0.40 * hito
        if es_pregunta:
            base += 0.15
        if temas:
            base += 0.10
        base *= 0.5 + 0.5 * longitud  # los mensajes substantive pesan mas

        return max(0.0, min(1.0, base))

    def _a_enum(self, score: float) -> Sentimiento:
        """Umbrales. El borde entre positivo y muy positivo esta en 0.6,
        no en 0.75: un solo "contrataron" ya es un hito, y merece la
        escala de arriba."""
        if score >= 0.6:
            return Sentimiento.MUY_POSITIVO
        if score >= 0.2:
            return Sentimiento.POSITIVO
        if score <= -0.6:
            return Sentimiento.MUY_NEGATIVO
        if score <= -0.2:
            return Sentimiento.NEGATIVO
        return Sentimiento.NEUTRO

    @staticmethod
    def _razon(
        score: float,
        hito: float,
        es_pregunta: bool,
        temas: list[str],
        palabras_hito: list[str],
        palabras_pos: list[str],
        palabras_neg: list[str],
    ) -> str:
        """La explicacion va con el dato, Y NOMBRA LA EVIDENCIA.

        Decir "hito profesional detectado (0.95)" no es una explicacion:
        es una afirmacion sin respaldo. Si el scoring de M3 va a
        justificar un post publicado, alguien tiene que poder abrir el
        mensaje y ver que palabras dispararon el numero. Por eso van los
        terminos, no solo los totales.

        Y como el texto se normaliza, las palabras van normalizadas
        tambien. "aprobe" no aparece escrito asi en ningun mensaje, y una
        razon que muestra una palabra que el autor no escribio hace perder
        la confianza en todo el sistema. Sepeparemos con la barra.
        """
        partes: list[str] = []
        if palabras_hito:
            # La razon no puede afirmar mas que lo que el numero sostiene.
            # Decir "hito profesional" con 0.50, cuando el umbral de
            # `es_logro` es 0.85, es una mentira utilitaria: el log queda
            # bonito y nadie puede defenderlo. Un score de hito que no
            # llega al corte se llama por lo que es, una mencion.
            if hito >= UMBRAL_HITO:
                partes.append(
                    f"hito profesional por {', '.join(palabras_hito)} ({hito:.2f})"
                )
            else:
                partes.append(
                    f"solo menciona {', '.join(palabras_hito)}, no llega a hito ({hito:.2f})"
                )
        if palabras_pos or palabras_neg:
            tono = "positivo" if score > 0 else "negativo"
            evidencia = palabras_pos + palabras_neg
            partes.append(f"tono {tono} por {', '.join(evidencia)} ({score:+.2f})")
        if es_pregunta:
            partes.append("es una pregunta")
        if temas:
            partes.append(f"temas: {', '.join(temas)}")
        if not partes:
            partes.append("sin señales de valor")
        return " | ".join(partes)


def a_analisis_desde_heuristica(
    interaccion: Interaccion, resultado: ResultadoHeuristico
) -> Analisis:
    """Puente para cuando el resultado heuristico ya esta calculado.

    Existe separado de `a_analisis` para que el pipeline pueda
    reutilizar el mismo resultado en dos caminos (fallback y analisis
    directo) sin recalcularlo.
    """
    return Analisis(
        mensaje_id=interaccion.mensaje_id,
        sentimiento=resultado.sentimiento,
        score_sentimiento=resultado.score,
        temas=resultado.temas,
        es_logro=resultado.es_logro,
        es_pregunta=resultado.es_pregunta,
        citas=resultado.citas,
        relevant=resultado.relevant,
        razon_relevante=resultado.razon,
    )


__all__ = [
    "AnalizadorHeuristico",
    "ResultadoHeuristico",
    "a_analisis_desde_heuristica",
]
