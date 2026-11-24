# M5 — Publicación a OCI Object Storage

Objetivo: subir los activos ya curados a OCI Object Storage, con
Pre-Authenticated Request (PAR) y una política IAM acotada. Sin este paso,
la curaduría (M6) aprueba activos que no van a ningún lado.
Rama: `main` · Entrega: `single-pr` · Test runner: `pytest` (offline).

## Specs

- **S1** — "bucket, PAR, política IAM acotada" (tabla de hitos del README, M5).
- **S2** — Solo se publica lo curado: `curado=True` **y** grounding limpio, es
  decir `paquete.publicable()`. Nunca sale un activo sin firmar.
- **S3** — El cliente OCI es un seam inyectable: los tests corren offline con
  un doble, y el SDK real (`oci`) se importa lazy. Instalar el SDK sigue
  siendo opcional (extra `oci`); un test que le pega a Oracle Cloud no es un
  test, es una mina.
- **S4** — El resultado es `AlmacenamientoOci` con `status` honesto:
  `guardado_con_exito`, `fallido` o `simulado`. Nunca se miente un fallo para
  que la demo se vea linda.
- **S5** — Modo simulado (`--simulado`) no toca la red ni exige credenciales.
- **S6** — CLI `sieve publicar` publica los paquetes aprobados de un
  directorio de curaduría.
- **S7** — Política IAM acotada documentada: permisos sobre el bucket y nada
  más.

## Tasks

- **T1** — `sieve/publicacion.py`: seam `ClienteAlmacenamiento`, `PublicadorOci`
  con el guard de curaduría, y `AlmacenamientoOci` de resultado. Ruta: inline.
  S2, S3, S4, S5. ✅ (5 tests).
- **T2** — CLI `sieve publicar --curar-dir --simulado` + tests. Ruta: inline.
  S5, S6. ✅ (3 tests).
- **T3** — README M5 (uso, política IAM) + M5 marcado ✅. Ruta: inline. S1, S7.

## Log

- **L1** — "las 2 cosas" (M5 código-OCI y M8 pulido, ambos). Este doc cubre
  M5; M8 se rastrea aparte. Orden: M5 primero (destino de la curaduría),
  M8 después (pulido final que refleja el sistema completo).
- **L2** — T1+T2 cerrados: `PublicadorOci` con seam inyectable (SDK `oci`
  importado lazy), guard de curaduría (`publicable()`), modo simulado, y
  `sieve publicar` (simulado por defecto). 8 tests nuevos.
