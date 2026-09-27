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

from sieve.ingestion import (
    ClienteStackExchange,
    CuotaAgotadaError,
    cargar_csv,
    cargar_json,
    guardar_json,
)
from sieve.models import LoteComunidad


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
