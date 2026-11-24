# M7 — Orquestación: API HTTP + flujo n8n

Objetivo: exponer el pipeline como una API HTTP que n8n pueda orquestar
(`Webhook → HTTP → IF → Response`), y entregar el flujo n8n como artefacto.
Rama: `main` · Entrega: `single-pr` · Test runner: `pytest` (offline).

## Specs

- **S1** — API HTTP que expone las etapas de máquina del pipeline como
  endpoints JSON: analizar, generar, publicar.
- **S2** — Los endpoints validan contra el contrato Pydantic (`LoteComunidad`
  de entrada, `RespuestaSieve` de salida). El contrato sigue siendo el centro.
- **S3** — Los defaults son offline, como en la CLI: heurística para analizar
  y simulado para publicar. El LLM y el OCI real son opt-in.
- **S4** — La API no cura. La curaduría sigue siendo del panel humano (M6);
  la API orquesta solo las etapas de máquina.
- **S5** — El flujo n8n documenta `Webhook → HTTP → IF → Response`, llamando
  a los endpoints de la API.
- **S6** — Tests offline: `TestClient` + proveedor doble, sin red.

## Tasks

- **T1** — `sieve/api.py` (FastAPI) + `api` extra + tests. Ruta: inline.
  S1, S2, S3, S4, S6. ✅ (7 tests).
- **T2** — `docs/n8n-workflow.json`: el flujo n8n. Ruta: inline. S5. ✅
- **T3** — README M7 + M7 marcado ✅. Ruta: inline. S1, S5. ✅

## Log

- **L1** — "sigue con M7" (orquestación: API HTTP + flujo n8n).
- **L2** — T1 cerrado: `sieve/api.py` (FastAPI) con `/health`, `/analizar`
  (heurística por defecto), `/generar` (sin fallback), `/publicar`
  (simulado por defecto). Extra `api` (`fastapi` + `uvicorn`). 7 tests
  offline con `TestClient`.
- **L3** — T2+T3 cerrados: `docs/n8n-workflow.json` (template importable) y
  README con sección de orquestación + diagrama de secuencia. M7 marcado ✅.
  Roadmap completo: M0-M8, todos ✅.
