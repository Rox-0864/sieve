"""
HTML → texto limpio, sin dependencias.

Por que no BeautifulSoup: la salida de la API de Stack Exchange es HTML
generado, no HTML de website arbitraria. Es un subconjunto chico y bien
conocido (parrafos, listas, `<pre><code>`, citas). Treinta lineas de
stdlib resuelven el problema y el proyecto se queda sin una dependencia
que auditar, actualizar y auditar de nuevo.

La alternativa correcta: si algun dia hay que parsear HTML de una pagina
real, recien ahi se justifica. Ese dia no es hoy.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

#: Tags que segun el contexto siguen con un salto de linea.
_BLOQUE = {
    "p", "div", "br", "li", "tr", "blockquote", "pre", "h1", "h2",
    "h3", "h4", "h5", "h6", "ul", "ol", "table", "section", "hr",
}

#: Tags cuyo contenido no es texto legible y se descarta entero.
_DESCARTAR = {"script", "style", "noscript", "svg"}


class _Extractor(HTMLParser):
    """Convierte HTML en texto con saltos de linea donde los hay.

    `convert_charrefs=True` (el default) hace el unescape de entidades
    automaticamente. Es lo que arregla los tags de la API, que llegan
    como `inyecci&#243;n-sql`.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._partes: list[str] = []
        self._silencio = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DESCARTAR:
            self._silencio += 1
        elif tag in _BLOQUE:
            self._partes.append("\n")
        elif tag == "code":
            # El codigo se marca para que el espaciado no se destruya.
            self._partes.append("\x00")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DESCARTAR and self._silencio:
            self._silencio -= 1
        elif tag in _BLOQUE:
            self._partes.append("\n")
        elif tag == "code":
            self._partes.append("\x00")

    def handle_data(self, data: str) -> None:
        if not self._silencio:
            self._partes.append(data)

    def texto(self) -> str:
        return "".join(self._partes)


def html_a_texto(html: str) -> str:
    """De HTML a texto plano legible.

    Un <pre><code> se vuelve un bloque con prefijo. La indentacion del
    codigo es informacion: sin ella, un ejemplo de Python deja de
    tener sentido y el LLM genera basura a partir de basura.
    """
    if not html:
        return ""

    parser = _Extractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — HTML malformado no puede tumbar la ingesta
        # Una etiqueta sin cerrar aborta el parser. Peor un texto
        # parcialmente limpio que ninguna ingesta. Se degrada, no se
        # rompe: este archivo vive en el borde del sistema.
        pass

    crudo = parser.texto()
    return _normalizar(crudo)


def _normalizar(texto: str) -> str:
    """Limpieza final: entidades, espacios y saltos repetidos.

    El colapso de espacios tiene que RESPETAR los bloques de codigo.
    Aplicar `[ \t]+ -> " "` sobre todo el documento convierte la
    indentacion de Python de 4 espacios en 1, y un ejemplo de codigo sin
    indentacion ya no es codigo: el LLM recibe basura y genera basura.
    Por eso el texto se parte en segmentos y se cleanan por separado.
    """
    segmentos = texto.split("\x00")
    limpio: list[str] = []
    for i, segmento in enumerate(segmentos):
        if i % 2 == 1:  # dentro de un <code>: intocable
            limpio.append("\n" + segmento.strip("\n") + "\n")
        else:
            limpio.append(re.sub(r"[ \t]+", " ", segmento.replace("\xa0", " ")))

    unido = "".join(limpio)
    # Los saltos repetidos sí se colapsan en todas partes: no son
    # informacion en un documento de texto plano.
    unido = re.sub(r"\n\s*\n\s*\n+", "\n\n", unido)
    return unido.strip()


def desescapar_tags(tags: list[str]) -> list[str]:
    """Arregla los tags que la API devuelve HTML-escapeados.

    `inyecci&#243;n-sql` tiene que terminar siendo `inyección-sql`.
    Sin esto, los tags se usan como criterio de filtrado contra un valor
    que no existe, y el filtro no matchea nada. Es un fallo silencioso:
    el pipeline corre, el dashboard dibuja, y el filtro devuelve cero
    resultados sin una sola queja.
    """
    from html import unescape

    return [unescape(t) for t in tags]


def truncar(texto: str, limite: int = 4000, sufijo: str = "…") -> str:
    """Recorta sin cortar a la mitad de una palabra.

    Mandar 40.000 caracteres a un LLM por un mensaje de chat es tirar
    plata. El corte va en el limite del LLM, no despues de que ya se
    pago la request.
    """
    if len(texto) <= limite:
        return texto
    cortado = texto[:limite]
    ultimo_espacio = cortado.rfind(" ")
    if ultimo_espacio > limite * 0.8:
        cortado = cortado[:ultimo_espacio]
    return cortado + sufijo
