# M6 — Panel de curaduría Streamlit

Objetivo: un panel donde un humano aprueba o rechaza los activos generados,
con el informe de grounding a la vista. Sin esta firma humana, `curado` es
siempre `False` y ningún activo es publicable por construcción.
Rama: `main` · Entrega: `single-pr` · Test runner: `pytest` (offline).

## Specs

- **S1** — "panel de curaduría Streamlit con aprobar/rechazar" (tabla de hitos
  del README, M6).
- **S2** — El panel tiene que mostrar, para cada activo: la fuente
  (`Interaccion`: autor, canal, texto), el activo en sí, y el informe de
  grounding (`veredicto`, supuestos, marcos, hallazgos). Hoy `generar --salida`
  escribe solo el activo pelado (`ActivosGenerados`) y pierde el informe, la
  fuente y la respuesta cruda: sin eso el curador no puede decidir.
- **S3** — La firma humana es el único camino a `curado=True`. El generador
  sigue escribiendo `curado: False`; el panel es quien lo da vuelta.
- **S4** — `puede_publicarse(informe, curado)` sigue siendo el único gate. El
  panel no lo reimplementa: un activo aprobado con grounding rechazado NO es
  publicable.
- **S5** — El panel no genera, no analiza, no publica: solo cura. Las etapas
  siguen separadas.
- **S6** — La decisión es auditable: queda registrado el estado
  (aprobado/rechazado), el motivo, el curador y la fecha.

## Tasks

- **T1** — `sieve/curation.py`: modelo `PaqueteCuracion` (fuente, formato,
  activo, informe serializado, respuesta cruda, decisión) + `guardar`/`cargar`.
  Ruta: inline. S2, S3, S6. ✅ (15 tests, commit a continuación).
- **T2** — `generar` escribe paquetes de curaduría (flag nuevo) sin romper
  `--salida`. Ruta: inline. S2. ✅ (`--curar-dir`, commit a continuación).
- **T3** — Panel Streamlit: carga el directorio, muestra fuente + activo +
  informe, botones aprobar/rechazar, persiste la decisión. Ruta: inline. S1, S3,
  S4, S6. ✅ (5 tests AppTest, commit a continuación).
- **T4** — README: sección de curaduría y cómo correr el panel. Ruta: inline.
  S5. ✅

## Log

- **L1** — "si continua en orden de prioridad" (tras proponer: higiene de repo
  primero, después M6 como el hito más sustancial). Higiene cerrada en
  `032364b` y `ca37b16`; esto arranca M6.
- **L2** — T1 cerrado: `sieve/curation.py` + `tests/test_curation.py` (15 tests).
  El paquete junta fuente + activo + informe + respuesta cruda; la decisión
  (aprobado/rechazado) da vuelta `curado` y exige motivo en el rechazo.
- **L3** — T2 cerrado: `generar --curar-dir` escribe un paquete por activo
  aprobado, con fuente + informe + respuesta cruda. `--salida` intacto.
- **L4** — T3 cerrado: `sieve/ui/curar.py` carga el directorio, muestra
  fuente/activo/informe, y firma aprobar/rechazar sobre el mismo archivo.
  Testeado con `AppTest` (sin navegador): aprobar persiste, rechazar sin
  motivo no firma.
- **L5** — T4 cerrado: README con sección de curaduría, quickstart con el
  extra `ui`, y M6 marcado ✅ en la tabla de hitos. M6 cerrado de punta a
  punta: `generar --curar-dir` → panel → firma persistida.
