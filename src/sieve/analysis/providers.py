"""
Providers de LLM. Todos por HTTP, ninguno con SDK.

Decision que hay que justificar: `pyproject.toml` declara
`google-generativeai`, `openai`, `anthropic` y `langchain-core` en el
extra `llm`, y ninguno esta instalado. Escribir adapters contra librerias
que no tenes produce codigo que nunca corrio y que nadie puede depurar.
Eso no es un adapter, es un Advertisement.

La alternativa es HTTP directo con `httpx`, que ya es dependencia del
proyecto desde M1. Y da mas cobertura de la que tendria con SDKs:

  - `OpenAICompatibleProvider` habla el dialecto `/v1/chat/completions`,
    que es el que exponen OpenAI, Groq, Together, vLLM, LM Studio y el
    endpoint de compatibilidad de Gemini. Una implementacion, seis
    proveedores.
  - `OllamaProvider` habla `/api/chat`, que es nativo y mas simple.

El resultado es cero dependencias nuevas y, mas importante, los dos se
pueden testear con un transporte falso. Un provider que solo se puede
probar con una key real es un provider que no se probo.

Cada provider expone UN metodo (`completar`) y NO valida la respuesta.
El parseo vive en `extract.py` para que haya un solo lugar donde vive.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

import httpx

from sieve.analysis.base import ErrorDeRedError, ResultadoLLM

#: Transporte: (url, headers, json_body) -> dict. Mismo patron que M1,
#: y por el mismo motivo: los tests no pueden pegarle a la red.
TransporteHTTP = Callable[[str, dict[str, str], dict[str, object]], dict[str, object]]


def transporte_httpx(
    url: str,
    headers: dict[str, str],
    body: dict[str, object],
    *,
    timeout: float = 60.0,
) -> dict[str, object]:
    """El unico lugar del proyecto con red hacia un LLM.

    Aislado para que `tests/conftest.py` pueda bloquear la red y los
    tests sigan siendo offline. Si aparece otro `httpx.post` en otro
    modulo, esta aislacion dejo de servir.
    """
    try:
        with httpx.Client(timeout=timeout) as cliente:
            respuesta = cliente.post(url, headers=headers, json=body)
            respuesta.raise_for_status()
            # `httpx` devuelve `Any` porque la forma depende de lo que
            # conteste el servidor. Se declara como `dict[str, object]`
            # en vez de dejar que se propague: los call sites que leen
            # claves sueltas tienen que dealing con `object` y no con
            # `Any`, que es donde los errores se vuelven invisibles.
            datos: dict[str, object] = respuesta.json()
            return datos
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise ErrorDeRedError(f"{url}: {exc}") from exc


def _contar(valor: object) -> int:
    """Lee un contador de tokens de una respuesta que no controlamos.

    `object` y no `Any` a proposito: obliga a decidir. Un proveedor puede
    mandar `42`, `"42"`, `None`, `0` o (porque el server cambio de
    version) un dict. Todo eso vale 0 y no rompe nada.

    Lo que NO se hace es `int(valor)`. Con `Any` mypy no se queja y el
    error aparece en produccion como un `TypeError` en medio de un lote
    de ocho, cuando el unico problema real era un contador que venia
    como texto. Un dato raro no vale un sistema caido.
    """
    if isinstance(valor, bool):
        return 0
    if isinstance(valor, int):
        return max(0, valor)
    if isinstance(valor, str):
        try:
            return max(0, int(valor.strip()))
        except ValueError:
            return 0
    return 0


# ═══════════════════════════════════════════════════════════════════
#  OpenAI-compatible
# ═══════════════════════════════════════════════════════════════════


class OpenAICompatibleProvider:
    """El dialecto `/v1/chat/completions`.

    Cubre OpenAI, Groq, Together, vLLM, LM Studio y el endpoint de
    compatibilidad de Gemini. El `modelo` va como string porque en este
    dialecto el nombre del modelo es opaco para el servidor.
    """

    nombre = "openai-compatible"

    def __init__(
        self,
        *,
        modelo: str,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        temperatura: float = 0.0,
        transporte: TransporteHTTP | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.modelo = modelo
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.temperatura = temperatura
        self.transporte = transporte or (lambda u, h, b: transporte_httpx(u, h, b, timeout=timeout))

    def es_disponible(self) -> bool:
        """Configurado, no "funcionando". Este metodo NO hace red.

        Es una distincion que importa: si el chequeo de disponibilidad
        pegara a la red, llamarlo por item en un lote de 100 seria 100
        requests de comprobacion antes de empezar a trabajar.
        """
        return bool(self.api_key and self.modelo)

    def completar(self, prompt: str, *, max_tokens: int = 1200) -> ResultadoLLM:
        if not self.es_disponible():
            raise ErrorDeRedError("OpenAICompatibleProvider sin api_key o modelo")

        cuerpo: dict[str, object] = {
            "model": self.modelo,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperatura,
            "max_tokens": max_tokens,
            # Cuando el servidor lo soporta, esto obliga a JSON valido y
            # elimina de raiz la mitad de los casos de parseo roto. No
            # todos los proveedores lo implementan: por eso el parseo
            # tolerante de `extract.py` sigue siendo obligatorio.
            "response_format": {"type": "json_object"},
        }

        inicio = time.monotonic()
        datos = self.transporte(
            f"{self.base_url}/chat/completions",
            {
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
            },
            cuerpo,
        )
        latencia = int((time.monotonic() - inicio) * 1000)

        try:
            texto = str(datos["choices"][0]["message"]["content"])  # type: ignore[index]
        except (KeyError, IndexError, TypeError) as exc:
            raise ErrorDeRedError(
                f"respuesta sin choices[0].message.content: {str(datos)[:200]}"
            ) from exc

        uso = datos.get("usage") or {}
        return ResultadoLLM(
            texto=texto,
            modelo=str(self.modelo),
            tokens_entrada=int(uso.get("prompt_tokens", 0)) if isinstance(uso, dict) else 0,
            tokens_salida=int(uso.get("completion_tokens", 0)) if isinstance(uso, dict) else 0,
            latencia_ms=latencia,
        )


# ═══════════════════════════════════════════════════════════════════
#  Ollama
# ═══════════════════════════════════════════════════════════════════


class OllamaProvider:
    """La API nativa de Ollama. Local, gratis, sin key.

    Es el provider por defecto y deberia seguir siendolo. Un portfolio
    que se clone y se ejecute sin pedirle a nadie una credencial vale mas
    que uno que menciona cuatro modelos y necesita los cuatro keys.

    `es_disponible` NO hace red, asi que "tengo ollama instalado" y
    "tengo el server prendido" son cosas distintas. La primera se puede
    responder al arrancar; la segunda se descubre en la primera llamada
    y cae al heuristico.
    """

    nombre = "ollama"

    def __init__(
        self,
        *,
        modelo: str = "qwen2.5:1.5b",
        base_url: str = "http://localhost:11434",
        temperatura: float = 0.0,
        transporte: TransporteHTTP | None = None,
        timeout: float = 900.0,
    ) -> None:
        self.modelo = modelo
        self.base_url = base_url.rstrip("/")
        self.temperatura = temperatura
        # Timeout alto a proposito: un modelo de 1.5B en CPU puede tardar
        # 30-60s en un lote. Con el default de 30s caeria a la
        # heuristica y el usuario perderia el LLM sin entender por que.
        self.transporte = transporte or (lambda u, h, b: transporte_httpx(u, h, b, timeout=timeout))

    def es_disponible(self) -> bool:
        return bool(self.modelo)

    def completar(self, prompt: str, *, max_tokens: int = 1200) -> ResultadoLLM:
        if not self.es_disponible():
            raise ErrorDeRedError("OllamaProvider sin modelo configurado")

        cuerpo: dict[str, object] = {
            "model": self.modelo,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {
                "temperature": self.temperatura,
                # `num_predict` es solo el tope de tokens: NO le pide
                # JSON al modelo. Medido: con y sin `format: "json"`,
                # qwen2.5:3b devuelve el mismo JSON bien formado. La
                # diferencia real es que el fence lo pone cuando quiere,
                # asi que `extract.py` sigue haciendo falta igual.
                "num_predict": max_tokens,
            },
        }

        inicio = time.monotonic()
        datos = self.transporte(
            f"{self.base_url}/api/chat",
            {"Content-Type": "application/json"},
            cuerpo,
        )
        latencia = int((time.monotonic() - inicio) * 1000)

        try:
            texto = str(datos["message"]["content"])  # type: ignore[index]
        except (KeyError, TypeError) as exc:
            raise ErrorDeRedError(f"respuesta sin message.content: {str(datos)[:200]}") from exc

        return ResultadoLLM(
            texto=texto,
            modelo=str(self.modelo),
            # Ollama devuelve esto, pero a veces como 0, ausente, o como
            # un string porque el server cambia entre versiones. Es
            # informacion opcional, asi que un valor raro vale 0 en
            # lugar de romper el analisis entero por un contador.
            tokens_entrada=_contar(datos.get("prompt_eval_count")),
            tokens_salida=_contar(datos.get("eval_count")),
            latencia_ms=latencia,
        )


# ═══════════════════════════════════════════════════════════════════
#  Fabrica
# ═══════════════════════════════════════════════════════════════════


def construir_proveedor(
    proveedor: str,
    *,
    modelo: str | None = None,
    transporte: TransporteHTTP | None = None,
) -> OpenAICompatibleProvider | OllamaProvider | None:
    """Arma el provider pedido por configuracion. None si no se puede.

    Devolver `None` en vez de levantar es deliberado: "no hay key" es un
    estado de ejecucion legitimo, no un error. El pipeline lo maneja
    bajando a la heuristica, con `motivo_fallback="sin_proveedor"`.

    `modelo` pisa lo que dice la configuracion. Existe para comparar dos
    modelos en la misma corrida sin editar el `.env` en el medio: comparar
    prompts exige que SOLO cambie el modelo, y si el nombre viene de
    settings, cada comparacion es un restart y un archivo editado.

    Los mapeos de `base_url` por proveedor son el detalle que mas
    problemas causa: todos dicen "compatible con OpenAI" pero cada uno
    tiene una URL distinta y una forma distinta de nombrar el modelo.
    """
    from sieve.config import settings

    nombre = (proveedor or "").strip().lower()
    override = (modelo or "").strip() or None

    if nombre in ("ollama", "local", ""):
        return OllamaProvider(
            modelo=override or settings.llm_analysis_model or "qwen2.5:1.5b",
            base_url=settings.ollama_base_url,
            temperatura=settings.llm_analysis_temperature,
            timeout=settings.llm_timeout_seconds,
            transporte=transporte,
        )

    if nombre == "gemini":
        return OpenAICompatibleProvider(
            modelo=override or settings.llm_analysis_model or "gemini-2.0-flash",
            api_key=settings.gemini_api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            temperatura=settings.llm_analysis_temperature,
            transporte=transporte,
        )

    if nombre == "openai":
        return OpenAICompatibleProvider(
            modelo=override or settings.llm_analysis_model or "gpt-4o-mini",
            api_key=settings.openai_api_key,
            base_url="https://api.openai.com/v1",
            temperatura=settings.llm_analysis_temperature,
            transporte=transporte,
        )

    if nombre == "anthropic":
        # La API de Anthropic NO es compatible con OpenAI. Peor que
        # devolver algo que parece funcionar: este caso se documenta
        # como no soportado en vez de fingir un adapter. Cuando haga
        # falta, es un `messages` con `system` aparte, y son 30 lineas
        # mas con su propia suite de tests.
        return None

    return None
