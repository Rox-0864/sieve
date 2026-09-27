"""
CLI de Sieve.

Chica a proposito. Su unico trabajo es dejar el pipeline ejecutable
desde una terminal sin escribir un script cada vez:

    sieve ingest data/samples/lote_demo.json
    sieve fetch-se --paginas 2 --cache-only
    sieve eval-cache

Que sea chica no significa que sea tonta: es el unico punto donde se
puede equivocar uno y vaciar la cuota de la API, asi que los flags que
tocan la red son explicitos.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sieve.analysis.hibrido import AnalizadorHibrido
from sieve.ingestion import (
    ClienteStackExchange,
    CuotaAgotadaError,
    cargar_csv,
    cargar_json,
    guardar_json,
)
from sieve.models import ActivosGenerados, LoteComunidad


def _cmd_ingest(args: argparse.Namespace) -> int:
    """Carga un lote local y lo resume. No toca la red."""
    ruta = Path(args.ruta)
    if not ruta.exists():
        print(f"error: no existe {ruta}", file=sys.stderr)
        return 1

    lote = (
        cargar_csv(ruta) if ruta.suffix.lower() == ".csv" else cargar_json(ruta)
    )
    print(_resumen(lote))

    if args.salida:
        destino = guardar_json(lote, args.salida)
        print(f"escrito: {destino}")
    return 0


def _transporte_sin_red(
    ruta: str, params: dict[str, object]
) -> tuple[dict[str, object], None]:
    """Transporte que nunca sale. Para --cache-only.

    Se implementa con el mismo seam que los tests usan, en vez de
    parcheando CacheDisco.leer: si mañana el cliente cambia de cache a
    otra cosa, --cache-only sigue siendo correcto sin tocar esta linea.
    """
    raise CuotaAgotadaError(
        f"cache-only: falta el dato para '{ruta} {sorted(params)}' y esta "
        "corrida no puede ir a la red. Corré sin --cache-only para "
        "precalentar el cache, o subí el --ttl."
    )


def _cmd_fetch(args: argparse.Namespace) -> int:
    """Trae preguntas de Stack Exchange. Cache-first."""
    cliente = ClienteStackExchange(
        cache_dir=args.cache_dir,
        ttl_segundos=args.ttl,
        transporte=_transporte_sin_red if args.cache_only else None,
    )
    etiquetadas = cliente.preguntas(
        paginas=args.paginas, por_pagina=args.por_pagina, minimo_score=args.min_score
    )

    if not etiquetadas:
        print(
            "sin resultados. Si esperabas datos, casi seguro es que la "
            "cuota diaria (300) se agoto o el cache vencio: proba "
            "--cache-only o --ttl 604800.",
            file=sys.stderr,
        )
        return 1

    aceptadas = sum(1 for e in etiquetadas if e.ground_truth and e.ground_truth.aceptado)
    print(
        f"{len(etiquetadas)} preguntas | {aceptadas} con respuesta aceptada | "
        f"cuota restante: {cliente.cuota_restante}/300 | "
        f"requests: {cliente.requests_hechos} | cache: {cliente.aciertos_cache}"
    )

    if args.salida:
        lote = cliente.a_lote(etiquetadas, comun="es.stackoverflow")
        destino = guardar_json(lote, args.salida)
        print(f"escrito: {destino}")
    return 0


def _cmd_cache(args: argparse.Namespace) -> int:
    """Estado del cache: cuantos archivos y cuanto pesan."""
    cliente = ClienteStackExchange(cache_dir=args.cache_dir)
    archivos = sorted(cliente.cache.raiz.glob("*.json"))
    total = sum(a.stat().st_size for a in archivos)
    print(f"{len(archivos)} archivos | {total / 1024:.1f} KiB | ttl {cliente.cache.ttl}s")
    if args.limpiar:
        print(f"borrados: {cliente.cache.limpiar()}")
    return 0


def _resumen(lote: LoteComunidad) -> str:
    por_tipo: dict[str, int] = {}
    con_id = 0
    for i in lote.interacciones:
        por_tipo[i.tipo.value] = por_tipo.get(i.tipo.value, 0) + 1
        if i.mensaje_id:
            con_id += 1
    detalle = ", ".join(f"{k}={v}" for k, v in sorted(por_tipo.items()))
    largo = sum(len(i.texto) for i in lote.interacciones)
    return (
        f"{lote.origen_comunidad} / {lote.periodo_referencia}: "
        f"{len(lote.interacciones)} interacciones ({detalle}) | "
        f"{con_id} con mensaje_id | {largo} caracteres"
    )


def _cmd_analyze(args: argparse.Namespace) -> int:
    """Analiza un lote. Heuristica por defecto; LLM solo si se pide.

    El default sin LLM NO es prudencia, es una decision de que
    `sieve analyze` se pueda correr en una maquina sin credenciales, sin
    internet y sin querer gastar. Un comando que pega a una API
    silenciosamente es un comando del que nadie se va a fiar despues.
    """
    ruta = Path(args.ruta)
    if not ruta.exists():
        print(f"error: no existe {ruta}", file=sys.stderr)
        return 1

    lote = cargar_csv(ruta) if ruta.suffix.lower() == ".csv" else cargar_json(ruta)

    proveedor = None
    if args.provider:
        from sieve.analysis.providers import construir_proveedor

        proveedor = construir_proveedor(args.provider, modelo=args.modelo)
        if proveedor is None:
            print(
                f"error: el provider '{args.provider}' no esta soportado. "
                "Usá 'ollama' (local, gratis) o 'openai'.",
                file=sys.stderr,
            )
            return 1
        if not proveedor.es_disponible():
            print(
                f"error: '{args.provider}' no esta configurado. "
                "Para la heurística no hace falta nada: corré sin --provider.",
                file=sys.stderr,
            )
            return 1

    analizador = AnalizadorHibrido(proveedor)
    resumen = analizador.analizar_lote(lote.interacciones)
    d = analizador.ultimo_diagnostico

    print(f"{resumen.total} interacciones | {lote.origen_comunidad}")
    conteo = resumen.por_procedencia()
    print(
        f"  llm={conteo['llm']} heuristico={conteo['heuristico']} "
        f"mixto={conteo['mixto']}"
    )
    print(
        f"  llamadas={d.llamadas_llm} ventanas={d.ventanas} "
        f"tokens={d.tokens} latencia={d.latencia_ms}ms"
    )
    if d.citas_descartadas:
        print(f"  citas descartadas: {d.citas_descartadas}")
    if d.errores:
        print(f"  errores: {len(d.errores)} (primero: {d.errores[0][:80]})")

    if args.verbose:
        # `sin_proveedor` solo se imprime si se PIDIO un LLM. Sin
        # --provider, la heuristica no es un fallback: es el camino
        # solicitado. Decir "fallback" ahi es mentira, y entrena al
        # usuario a ignorar la palabra.
        pidio_llm = proveedor is not None
        for r in resumen.resultados:
            print(f"\n  [{r.procedencia}] {r.analisis.mensaje_id or '?'}")
            print(
                f"    {r.analisis.sentimiento.value} "
                f"score={r.analisis.score_sentimiento:+.2f} "
                f"rel={r.analisis.relevant:.2f}"
            )
            print(f"    {r.analisis.razon_relevante}")
            if r.motivo_fallback and pidio_llm:
                print(f"    fallback: {r.motivo_fallback}")
            elif not pidio_llm:
                print("    heurística (no se pidió LLM)")

    if args.salida:
        destino = guardar_json(LoteComunidad(
            origen_comunidad=lote.origen_comunidad,
            periodo_referencia=lote.periodo_referencia,
            interacciones=lote.interacciones,
            analisis=resumen.a_analisis(),
            # Se persiste la procedencia, no solo se imprime. La terminal
            # la muestra y el archivo la pierde: eso dejaba a n8n sin
            # forma de saber si el lote es de fiar sin volver a correr el
            # pipeline entero. Y correrlo de nuevo puede dar OTRO numero
            # si el modelo no es determinista, que en CPU no lo es.
            procedencia=resumen.conteo_procedencia(),
        ), args.salida)
        print(f"escrito: {destino}")
        if not resumen.conteo_procedencia().de_fiar and proveedor is not None:
            print("  aviso: el lote no es enteramente LLM o tiene citas descartadas")
    return 0


def _cmd_generar(args: argparse.Namespace) -> int:
    """Genera activos desde un lote YA analizado, e imprime el veredicto.

    A diferencia de `analyze`, aca no hay heuristica: la generacion sin
    modelo no existe. Un fallback que devuelve "texto generico" para no
    devolver nada es la forma mas comun de que un pipeline termine
    publicando placeholders.

    Dos cosas que este comando NO hace, y dice en pantalla:

    - No publica. Escribe los activos con `curado: false` y aclara que
      ninguno es publicable, porque la curaduria es obligatoria y el
      panel todavia no existe (M6). Un comando que deja el archivo a un
      `cat` de distancia sin aclarar eso es una bomba de reputacion.
    - No recalcula el analisis. Lee el que ya esta en el lote, para que
      el activo y el puntaje salgan del mismo analisis. Volver a correr
      `analyze` puede dar OTRO numero, porque el modelo en CPU no es
      determinista, y un activo de un analisis con un puntaje de otro
      lote es un activo que nadie puede auditar.
    """
    ruta = Path(args.ruta)
    if not ruta.exists():
        print(f"error: no existe {ruta}", file=sys.stderr)
        return 1

    lote = cargar_json(ruta)
    if not lote.analisis:
        print(
            "error: el lote no tiene analisis. Corre `sieve analyze --provider "
            "ollama` primero:\n  la generacion lee el analisis, no lo recalcula.",
            file=sys.stderr,
        )
        return 1

    from sieve.analysis.base import ResultadoAnalisis
    from sieve.analysis.providers import construir_proveedor
    from sieve.generation.assets import ErrorDeGeneracionError, GeneradorActivos

    proveedor = construir_proveedor(args.provider, modelo=args.modelo)
    if proveedor is None or not proveedor.es_disponible():
        print(
            f"error: el provider '{args.provider}' no esta configurado. "
            "La generacion no tiene fallback.",
            file=sys.stderr,
        )
        return 1

    # El analisis se enlaza por `mensaje_id`, no por posicion: un lote
    # puede traer los analisis en otro orden, y emparejarlos por indice
    # publicaria el testimonio de una persona con el puntaje de otra.
    por_id = {a.mensaje_id: a for a in lote.analisis if a.mensaje_id}
    generador = GeneradorActivos(proveedor)
    aprobados = 0
    con_rechazo = 0
    #: `mensaje_id` -> {formato: activo}, porque `ActivosGenerados` tiene
    #: UN slot por formato y es la respuesta de UNA interaccion. Meter los
    #: activos de un lote en un solo `ActivosGenerados` seria pisar los
    #: posts de siete personas y quedarse con uno.
    por_mensaje: dict[str, dict[str, object]] = {}

    for i, interaccion in enumerate(lote.interacciones, start=1):
        # `mensaje_id` es opcional en el contrato, y sin el no hay forma
        # de enlazar el analisis. Sin enlace NO se genera: se adivina el
        # analisis por posicion, y un lote con los analisis en otro orden
        # publica el testimonio de una persona con el puntaje de otra.
        if interaccion.mensaje_id is None:
            print(f"  [{i}] sin mensaje_id, se salta (no se puede enlazar)")
            continue
        analisis = por_id.get(interaccion.mensaje_id)
        if analisis is None:
            print(f"  [{i}] sin analisis, se salta {interaccion.mensaje_id}")
            continue
        entrada = ResultadoAnalisis(
            interaccion=interaccion, analisis=analisis, procedencia="llm"
        )
        for etiqueta, metodo in (
            ("post_linkedin", generador.generar_post),
            ("destreza_newsletter_semanal", generador.generar_newsletter),
            ("sugerencia_contenido_faq", generador.generar_faq),
        ):
            try:
                salida = metodo(entrada)
            except ErrorDeGeneracionError as exc:
                print(f"\n  [{i}] {etiqueta}: SIN ACTIVO ({exc})")
                con_rechazo += 1
                continue
            if salida.activo is None:
                con_rechazo += 1
                print(f"\n  [{i}] {etiqueta}: RECHAZADO")
                for v in salida.informe.rechazados:
                    for h in v.hallazgos:
                        print(f"    - {v.nombre}: {h}")
                continue
            aprobados += 1
            por_mensaje.setdefault(interaccion.mensaje_id, {})[etiqueta] = (
                salida.activo
            )
            print(f"\n  [{i}] {etiqueta}: {salida.informe.veredicto}")
            if salida.campos_ignorados:
                print(f"    campos ignorados: {list(salida.campos_ignorados)}")
            for s in salida.informe.supuestos:
                print(f"    supuesto: {s}")
            for m in salida.informe.marcos:
                print(f"    marco: {m}")

    print(
        f"\n{aprobados} activos con grounding limpio, {con_rechazo} rechazados.\n"
        "NINGUNO es publicable: la curaduria es obligatoria y el panel (M6) "
        "todavia no existe."
    )
    if args.salida:
        # `--salida` es un PREFIJO: se escribe un `ActivosGenerados` por
        # interaccion, que es la unidad del contrato de ONE. Un solo
        # archivo para el lote tendria que meter una lista donde el
        # documento define un objeto.
        for mensaje_id, activos in por_mensaje.items():
            destino = guardar_json(
                ActivosGenerados.model_validate(activos),  # type: ignore[arg-type]
                f"{args.salida}.{mensaje_id}.json",
            )
            print(f"escrito: {destino}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sieve", description="Sieve — ingesta de conversaciones de comunidad."
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    p_ingest = sub.add_parser("ingest", help="carga un lote local (json o csv)")
    p_ingest.add_argument("ruta")
    p_ingest.add_argument("--salida", help="donde escribir el lote normalizado")
    p_ingest.set_defaults(func=_cmd_ingest)

    p_fetch = sub.add_parser("fetch-se", help="preguntas de Stack Exchange")
    p_fetch.add_argument("--paginas", type=int, default=1)
    p_fetch.add_argument("--por-pagina", type=int, default=100)
    p_fetch.add_argument("--min-score", type=int, default=1)
    p_fetch.add_argument("--cache-dir", default="data/cache/stackexchange")
    p_fetch.add_argument("--ttl", type=int, default=86_400)
    p_fetch.add_argument("--cache-only", action="store_true", help="falla si hay que ir a la red")
    p_fetch.add_argument("--salida", help="donde escribir el lote")
    p_fetch.set_defaults(func=_cmd_fetch)

    p_analyze = sub.add_parser("analyze", help="analiza un lote (heurística por defecto)")
    p_analyze.add_argument("ruta")
    p_analyze.add_argument(
        "--provider",
        choices=["ollama", "openai"],
        help="LLM a usar. SIN este flag corre heurístico, sin red.",
    )
    p_analyze.add_argument("--modelo", help="nombre exacto del modelo")
    p_analyze.add_argument("--verbose", action="store_true", help="muestra cada análisis")
    p_analyze.add_argument("--salida", help="donde escribir el lote con sus análisis")
    p_analyze.set_defaults(func=_cmd_analyze)

    p_gen = sub.add_parser(
        "generar", help="genera activos desde un lote analizado (M4)"
    )
    p_gen.add_argument("ruta", help="lote JSON con `analisis` (salida de analyze)")
    p_gen.add_argument("--provider", default="ollama", help="ollama (default)")
    p_gen.add_argument("--modelo", default=None)
    p_gen.add_argument(
        "--salida",
        default=None,
        help="prefijo de salida: se escribe <prefijo>.<mensaje_id>.json",
    )
    p_gen.set_defaults(func=_cmd_generar)

    p_cache = sub.add_parser("cache", help="estado del cache")
    p_cache.add_argument("--cache-dir", default="data/cache/stackexchange")
    p_cache.add_argument("--limpiar", action="store_true")
    p_cache.set_defaults(func=_cmd_cache)

    args = parser.parse_args(argv)

    try:
        return int(args.func(args))
    except CuotaAgotadaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
