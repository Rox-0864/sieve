"""
Tests del analizador heuristico. Offline, deterministas, sin key.

Este es el PISO del sistema. Si estos tests fallan, el proyecto no
funciona sin credenciales, y con eso se cae la demo entera. Son los
tests mas importantes del repositorio despues de los de contrato.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from sieve.analysis.heuristico import (
    HITO,
    HITO_CRUDO,
    INTERROGATIVAS,
    MARCAS_DE_PEDIDO,
    NEGATIVO,
    NEGATIVO_CRUDO,
    POSITIVO,
    POSITIVO_CRUDO,
    SALUDOS,
    TEMAS_RUIDO,
    AnalizadorHeuristico,
    sin_acentos,
)
from sieve.models import Interaccion, Sentimiento


@pytest.fixture
def h() -> AnalizadorHeuristico:
    return AnalizadorHeuristico()


def hacer(texto: str, **kwargs: object) -> Interaccion:
    return Interaccion(autor="Alguien", canal="#test", texto=texto, **kwargs)


# ═══════════════════════════════════════════════════════════════════
#  Deteccion de preguntas — la parte que mas bugs produjo
# ═══════════════════════════════════════════════════════════════════


class TestDeteccionDePreguntas:
    @pytest.mark.parametrize(
        ("texto", "esperado", "nota"),
        [
            # Los relacionales. Esta lista se escribio DESPUES de descubrir
            # que "mas que el titulo" se contaba como pregunta y mandaba un
            # testimonio al router de FAQ.
            ("Despues de tres meses, me contrataron. El portfolio me sirvio mas que el titulo.",
             False, 'relacional: "mas que el titulo"'),
            ("Vivi en la ciudad donde naci y fue increible.",
             False, 'relacional: "donde naci"'),
            ("Trabaje como en la Escuela y me fue bien.",
             False, 'comparacion: "como en"'),
            ("El proyecto, que fue dificil, termino a tiempo.",
             False, "relativa tras coma larga"),
            ("Cuando llegue a mi casa, cene.",
             False, "subordinada temporal con coma"),
            # Las preguntas de verdad.
            ("Como hago un portfolio que no sea humo?", True, "signo de cierre"),
            ("¿Alguien sabe de OCI?", True, "apertura con signo invertido"),
            ("Hola, como anda todo", True, "interrogativo tras saludo"),
            ("Buenas, que tal el proyecto", True, "interrogativo tras saludo 2"),
            # Los cinco que estaban muertos por tener acento en la lista
            # mientras el texto se comparaba sin acentos.
            ("¿Cuales son los mejores libros?", True, "cuales acentuado"),
            ("Cuanto tiempo lleva Python?", True, "cuanto acentuado"),
            ("Donde queda el evento?", True, "donde acentuado"),
            ("¿Que es OCI?", True, "que acentuado"),
            # El negativo de control.
            ("hola buenos dias a todos", False, "saludo sin contenido"),
        ],
    )
    def test_preguntas(
        self, h: AnalizadorHeuristico, texto: str, esperado: bool, nota: str
    ) -> None:
        resultado = h.analizar(hacer(texto))
        assert resultado.es_pregunta is esperado, f"{nota}: {texto!r}"

    def test_el_signo_de_pregunta_manda_sobre_todo(self, h: AnalizadorHeuristico) -> None:
        """Un '?' final clasifica, aunque el resto diga lo contrario."""
        assert h.analizar(hacer("Cuando llega, no se.")).es_pregunta is False
        assert h.analizar(hacer("Cuando llega?")).es_pregunta is True


# ═══════════════════════════════════════════════════════════════════
#  Sentimiento
# ═══════════════════════════════════════════════════════════════════


class TestSentimiento:
    def test_logro_de_empleo_es_muy_positivo(self, h: AnalizadorHeuristico) -> None:
        r = h.analizar(
            hacer("Después de tres meses de estudiar solo, me contrataron como dev junior.")
        )
        assert r.sentimiento is Sentimiento.MUY_POSITIVO
        assert r.score > 0.6

    def test_queja_es_negativa(self, h: AnalizadorHeuristico) -> None:
        r = h.analizar(
            hacer("Estoy frustrado, mi proyecto falló y el bug sigue, no entiendo nada.")
        )
        assert r.sentimiento in (Sentimiento.NEGATIVO, Sentimiento.MUY_NEGATIVO)

    def test_neutro_no_es_neutraidad_de_texto(self, h: AnalizadorHeuristico) -> None:
        r = h.analizar(hacer("El servidor tiene cuatro nucleos y corre en un puerto local."))
        assert r.sentimiento is Sentimiento.NEUTRO

    def test_acentos_no_cambian_el_resultado(self, h: AnalizadorHeuristico) -> None:
        """'Duda' y 'DÚDA' tienen que ser la misma palabra.

        Si no, medio diccionario no matchea nunca y la mitad de los
        mensajes en español salen como neutros.
        """
        con = h.analizar(hacer("Tengo una duda grande sobre esto"))
        sin = h.analizar(hacer("tengo una duda grande sobre esto"))
        assert con.sentimiento is sin.sentimiento
        assert con.score == sin.score

    def test_la_escala_se_satura(self, h: AnalizadorHeuristico) -> None:
        """Treinta palabras positivas no dan 8.0: dan 1.0."""
        r = h.analizar(hacer("genial increible mejor gracias aprendi " * 6))
        assert -1.0 <= r.score <= 1.0

    def test_palabra_repetida_no_multiplica(self, h: AnalizadorHeuristico) -> None:
        """'muy muy muy bueno' no es tres veces mas bueno.

        Sin esto, un mensaje con una palabra repetida domina el ranking
        y el sistema mide insistencia en vez de valor.
        """
        una = h.analizar(hacer("el curso fue genial"))
        muchas = h.analizar(hacer("el curso fue genial genial genial genial genial"))
        assert muchas.score == una.score


# ═══════════════════════════════════════════════════════════════════
#  Hitos, temas y relevancia
# ═══════════════════════════════════════════════════════════════════


class TestHitos:
    @pytest.mark.parametrize(
        "texto",
        [
            "me contrataron como dev junior",
            "recibi mi oferta de trabajo",
            "hoy me ascendieron a tech lead",
            "aprobé el examen final",
            "mi proyecto fue aceptado en un programa",
        ],
    )
    def test_hitos_reales(self, h: AnalizadorHeuristico, texto: str) -> None:
        assert h.analizar(hacer(texto)).es_logro is True

    @pytest.mark.parametrize(
        "texto",
        [
            "estoy aprendiendo python hace dos semanas",
            "quiero aprender machine learning",
            "estoy buscando trabajo",
        ],
    )
    def test_intencion_no_es_hito(self, h: AnalizadorHeuristico, texto: str) -> None:
        """"Estoy aprendiendo" NO es un logro. "Quiero aprender" tampoco.

        Es la distincion que separa un caso de exito real de un mensaje
        de alguien que quiere empezar. Confundirlas hace que el post de
        LinkedIn sea sobre las intenciones del autor.
        """
        assert h.analizar(hacer(texto)).es_logro is False

    def test_pasiva_versus_activa_en_aceptar(self, h: AnalizadorHeuristico) -> None:
        """"Fue aceptado" es un hito. "Acepto la contrasena" no.

        La misma raiz con pasiva y activa cambia el significado entero, y
        por eso pesan distinto. Sin esto, un login fallido entra al
        ranking de logros, o un proyecto aceptado no entra.
        """
        logro = h.analizar(hacer("Mi proyecto fue aceptado en un programa"))
        mundane = h.analizar(hacer("El sistema acepta la contrasena"))

        assert logro.es_logro is True
        assert mundane.es_logro is False

    def test_un_sustantivo_comun_no_es_hito(self, h: AnalizadorHeuristico) -> None:
        """"Mi proyecto" no es un hito: es un sustantivo.

        Estuvo en el diccionario con peso 0.5 y hacia que TODA queja
        sobre un proyecto quedara con "hito profesional" en el log. Un
        termino que aparece en el 30% de los mensajes no es evidencia de
        nada.
        """
        r = h.analizar(hacer("Estoy frustrado, mi proyecto no avanza"))
        assert r.es_logro is False
        assert "hito profesional" not in r.razon

    def test_la_razon_no_sobredeclara_la_fuerza(self, h: AnalizadorHeuristico) -> None:
        """La razon no llama "hito" a una mencion que no llega al corte.

        Un log que dice "hito profesional" con 0.50 cuando el umbral es
        0.85 se ve bien en el dashboard y no se puede defender en una
        revision. La razon tiene que estar a la altura del numero.
        """
        r = h.analizar(hacer("estoy escribiendo en mi proyecto"))
        if "hito" in r.razon:
            assert r.es_logro is True, f"la razon dice hito pero no es_logro: {r.razon}"


class TestTemas:
    def test_detecta_empleo(self, h: AnalizadorHeuristico) -> None:
        assert "empleo" in h.analizar(hacer("me pasaron el cv y una entrevista")).temas

    def test_detecta_herramientas(self, h: AnalizadorHeuristico) -> None:
        assert "herramientas" in h.analizar(hacer("uso python y docker")).temas

    def test_saludo_no_es_tema(self, h: AnalizadorHeuristico) -> None:
        """'hola' no es un tema. Inflaria el newsletter de palabras vacias."""
        assert h.analizar(hacer("hola")).temas == []


class TestRelevancia:
    def test_logro_relevante_por_encima_del_saludo(self, h: AnalizadorHeuristico) -> None:
        logro = h.analizar(hacer("Después de tres meses de estudiar, me contrataron como dev."))
        saludo = h.analizar(hacer("hola"))
        assert logro.relevant > 0.6
        assert saludo.relevant < 0.2

    def test_mensaje_corto_puntua_bajo(self, h: AnalizadorHeuristico) -> None:
        r = h.analizar(hacer("genial!!!"))
        assert r.relevant < 0.35

    def test_texto_vacio_no_es_relevante(self, h: AnalizadorHeuristico) -> None:
        assert h.analizar(hacer("hola")).relevant < 0.3

    def test_la_relevancia_esta_normalizada(self, h: AnalizadorHeuristico) -> None:
        for texto in ("hola", "x" * 500, "me contrataron " * 40):
            r = h.analizar(hacer(texto))
            assert 0.0 <= r.relevant <= 1.0


# ═══════════════════════════════════════════════════════════════════
#  Explicabilidad
# ═══════════════════════════════════════════════════════════════════


class TestExplicabilidad:
    def test_siempre_explica(self, h: AnalizadorHeuristico) -> None:
        """Sin `razon` no hay debug ni demo posible.

        El scoring de M3 tiene que poder decir POR QUE algo salio con
        0.82. Si esta cadena no explica nada, el README miente.
        """
        for texto in ("hola", "me contrataron", "¿que es oci?", "x" * 300):
            assert h.analizar(hacer(texto)).razon.strip()

    def test_explica_el_hito_cuando_lo_hay(self, h: AnalizadorHeuristico) -> None:
        r = h.analizar(hacer("me contrataron como dev junior"))
        assert "hito" in r.razon

    def test_dice_que_no_hay_senales(self, h: AnalizadorHeuristico) -> None:
        assert "sin señales" in h.analizar(hacer("hola")).razon.lower()

    def test_la_razon_es_auditable(self, h: AnalizadorHeuristico) -> None:
        """La razon tiene que NOMBRAR la evidencia, no solo existir.

        Sin esto `razon` es decoracion: imposible depurar un score raro,
        ni escribir un post que justifique sus propias citas. Y no puede
        citar "no se" con tilde, porque `_normalizar` las elimina y la
        comparacion es sobre texto plano.
        """
        r = h.analizar(hacer("me contrataron como dev junior"))
        assert "contrataron" in r.razon

    def test_no_dice_una_palabra_que_no_esta(self, h: AnalizadorHeuristico) -> None:
        """La razon no puede afirmar evidencia que el mensaje no tiene."""
        r = h.analizar(hacer("estoy aprendiendo python"))
        assert "contrat" not in r.razon
        assert "hito" not in r.razon.lower()

    def test_la_razon_no_inventa_acentos(self, h: AnalizadorHeuristico) -> None:
        """La razon no puede mostrar una palabra que el autor no escribio.

        La busqueda corre sobre texto normalizado, asi que el termino que
        sale es "aprobe", no "aprobé". Mostrar "aprobé" en la razon
        significa que el sistema afirma algo que no esta en el mensaje, y
        eso hace perder la confianza en TODO el subsistema de explicacion.
        """
        r = h.analizar(hacer("Aprobé el examen final"))
        assert "aprobe" in r.razon
        assert "aprobé" not in r.razon


# ═══════════════════════════════════════════════════════════════════
#  Guardarrail: los diccionarios tienen que estar normalizados
# ═══════════════════════════════════════════════════════════════════════


class TestLexiconsNormalizados:
    """Candado contra una clase de bug, no estilo de escritura.

    El diccionario se escribia con acentos ("aprobé") y se comparaba
    contra texto normalizado ("aprobe"). Esas palabras NO MATCHEABAN
    NUNCA. Sin error, sin warning, sin test: el mensaje salia neutro y
    nadie se enteraba. Cuando se descobrio, cinco palabras estaban muertas
    y el arreglo manual (duplicar cada clave acentuada) era una bomba de
    reloj.

    Ahora la normalizacion es estructural, y estos tests son el candado.
    """

    LEXICONS = {
        "POSITIVO": POSITIVO,
        "NEGATIVO": NEGATIVO,
        "HITO": HITO,
    }

    @pytest.mark.parametrize("nombre", sorted(LEXICONS))
    def test_ninguna_clave_conserva_acentos(self, nombre: str) -> None:
        tabla = self.LEXICONS[nombre]
        sucias = [k for k in tabla if sin_acentos(k) != k]
        assert not sucias, (
            f"{nombre} tiene claves acentuadas que nunca matchearan: {sucias}"
        )

    @pytest.mark.parametrize("nombre", sorted(LEXICONS))
    def test_ningun_patron_aceptaria_acentos(self, nombre: str) -> None:
        """Guarda contra una futura tabla escrita sin pasar por el helper."""
        tabla = self.LEXICONS[nombre]
        for clave in tabla:
            assert clave == clave.lower(), f"{clave} no esta en minusculas"

    def test_los_conjuntos_tambien(self) -> None:
        for nombre, conjunto in {
            "INTERROGATIVAS": INTERROGATIVAS,
            "MARCAS_DE_PEDIDO": MARCAS_DE_PEDIDO,
            "SALUDOS": SALUDOS,
            "TEMAS_RUIDO": TEMAS_RUIDO,
        }.items():
            sucias = [k for k in conjunto if sin_acentos(k) != k]
            assert not sucias, f"{nombre} tiene claves acentuadas: {sucias}"

    @pytest.mark.parametrize(
        ("palabra", "tabla"),
        [
            ("conseguí", POSITIVO),
            ("aprobé", POSITIVO),
            ("difícil", NEGATIVO),
            ("ascendido", HITO),
        ],
    )
    def test_palabras_acentuadas_funcionan(self, palabra: str, tabla: dict[str, float]) -> None:
        """La prueba de fuego: la palabra acentuada TIENE que encontrar.

        Esta es la que fallaba. Si el helper de normalizacion se rompe,
        esta falla con un mensaje claro en vez de que el scoring se
        degrade en silencio.
        """
        assert sin_acentos(palabra) in tabla

    def test_toda_clave_acentuada_llega_normalizada(self) -> None:
        """El invariante completo, no dos casos a mano.

        Por cada clave escrita CON acento en los diccionarios crudos, la
        version normalizada tiene que existir en la tabla final. Es la
        forma de cubrir el invariante completo: si alguien agrega
        "conseguí" al crudo y olvida algo, esto lo dice.
        """
        for crudo, final in (
            (POSITIVO_CRUDO, POSITIVO),
            (NEGATIVO_CRUDO, NEGATIVO),
            (HITO_CRUDO, HITO),
        ):
            for clave in crudo:
                assert sin_acentos(clave) in final, (
                    f"{clave!r} se perdio al normalizar: el autor escribe con "
                    f"tilde y el matcher busca sin, y sin esta linea nadie lo ve"
                )

    def test_la_normalizacion_no_come_pesos(self) -> None:
        """`_normalizar_tabla` no puede perder ni fusionar valores en silencio.

        Dos palabras que solo difieren en tilde colapsan en una. Si eso
        pasa, la mas fuerte gana y la otra desaparece. Puede ser
        aceptable, pero tiene que ser VISIBLE, no un efecto secundario.
        """
        for crudo, final in (
            (POSITIVO_CRUDO, POSITIVO),
            (NEGATIVO_CRUDO, NEGATIVO),
            (HITO_CRUDO, HITO),
        ):
            esperado = {sin_acentos(k) for k in crudo}
            assert len(final) == len(esperado), (
                "hubo colision al normalizar y se perdieron palabras"
            )
            assert set(final) == esperado


# ═══════════════════════════════════════════════════════════════════
#  El camino hacia `Analisis`
# ═══════════════════════════════════════════════════════════════════


class TestHaciaAnalisis:
    def test_el_analisis_lleva_el_id(self, h: AnalizadorHeuristico) -> None:
        a = h.a_analisis(hacer("hola", mensaje_id="abc123"))
        assert a.mensaje_id == "abc123"

    def test_las_citas_son_reales(self, h: AnalizadorHeuristico) -> None:
        """Las citas heuristicas salen de un corte del texto.

        Es la diferencia con las del LLM: estas NO necesitan
        verificacion porque no hay nada que verificar. Un pipeline que
        publica citas inventadas es un pipeline que publica mentiras.
        """
        texto = "Después de tres meses de estudiar, me contrataron como dev junior."
        a = h.a_analisis(hacer(texto))
        for cita in a.citas:
            assert cita in texto

    def test_es_determinista(self, h: AnalizadorHeuristico) -> None:
        """Sin aleatoriedad, sin reloj, sin red. Mismo input, mismo output.

        Es la unica razon por la que sirve como REFERENCIA para medir al
        LLM: si la heuristica cambiara entre corridas, no habria contra
        que comparar.
        """
        texto = "Después de tres meses de estudiar, me contrataron como dev junior."
        primero = h.a_analisis(hacer(texto)).model_dump()
        for _ in range(20):
            assert h.a_analisis(hacer(texto)).model_dump() == primero

    def test_el_heuristico_no_arrastra_ningun_sdk(self) -> None:
        """El piso del sistema no puede depender de una nube.

        Si importar el heuristico arrastra el SDK de OpenAI u OCI, el
        fallback se cae en cuanto falta una credencial, y con el se cae
        todo el producto. Se comprueba en un interprete NUEVO, porque en
        este los otros tests ya importaron medio mundo.
        """
        codigo = (
            "import sys; import sieve.analysis.heuristico as h; "
            "malos = [m for m in ('openai','google','anthropic','langchain','oci') "
            "if m in sys.modules]; "
            "print(','.join(malos))"
        )
        resultado = subprocess.run(
            [sys.executable, "-c", codigo],
            capture_output=True,
            text=True,
            check=True,
        )
        assert resultado.stdout.strip() == "", (
            f"el heuristico arrastro: {resultado.stdout.strip()}"
        )
