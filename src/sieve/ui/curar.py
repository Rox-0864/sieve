"""
Panel de curacion (M6).

Correlo asi, con el directorio donde `generar` dejo los paquetes:

    streamlit run src/sieve/ui/curar.py -- data/curacion

Que hace y que NO hace
----------------------
Este panel es la firma humana que falta entre `generar` (activos con
`curado=False`) y `publicar` (M5). Muestra la fuente, el activo y el
informe de grounding, y deja aprobar o rechazar. Nada mas:

- No genera: lee los paquetes que `generar --curar-dir` dejo en disco.
- No analiza: el analisis ya viene pegado al lote.
- No publica: `curado=True` no es publicar. La publicacion (M5) sigue
  siendo otro paso, y `puede_publicarse` sigue siendo el unico gate.

La decision se persiste en el mismo archivo del paquete (sobreescribe
`decision`, `motivo`, `curador`, `fecha_decision`), asi que el panel no
tiene base de datos propia y el directorio de curacion ES el estado.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

import streamlit as st

from sieve.curation import (
    PaqueteCuracion,
    aplicar_decision,
    cargar_paquetes,
    guardar_paquete,
)

#: Campos de metadata del activo que NO son contenido que el curador
#: tenga que firmar: los decide el sistema. Mostrarlos junto al copy
#: confunde la firma con el pipeline.
_CAMPOS_META = {"confianza", "curado", "fuentes"}


def _directorio_por_defecto() -> str:
    """El directorio, de la linea de comandos o un default.

    `streamlit run app.py -- data/curacion` deja `data/curacion` en
    `sys.argv[1:]`. Sin argumento, un default que deja claro donde
    apuntar el panel.
    """
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    return args[0] if args else "data/curacion"


def _curador_actual() -> str:
    """El nombre de quien firma, editable en el sidebar.

    Sin identidad no hay auditoria: una firma de la que no se sabe de
    quien es no es una firma. El default es el usuario del SO, y se
    puede corregir en el panel.
    """
    import getpass

    usuario = ""
    try:
        usuario = getpass.getuser()
    except Exception:  # noqa: BLE001 — getuser puede fallar en entornos raros
        usuario = ""
    return st.sidebar.text_input("Curador", value=usuario, key="curador")


def _campos_visibles(activo: object) -> list[tuple[str, str]]:
    """Los campos de contenido del activo, sin la metadata de sistema."""
    datos = activo.model_dump()  # type: ignore[attr-defined]
    return [
        (campo, str(valor))
        for campo, valor in datos.items()
        if campo not in _CAMPOS_META
    ]


def _mostrar_informe(paquete: PaqueteCuracion) -> None:
    informe = paquete.informe_tipado()
    with st.expander(f"Grounding — {informe.veredicto}", expanded=True):
        for verificacion in informe.verificaciones:
            icono = "✅" if verificacion.ok else "❌"
            st.markdown(f"{icono} **{verificacion.nombre}**")
            for hallazgo in verificacion.hallazgos:
                st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;— {hallazgo}")
        if informe.supuestos:
            st.markdown("**Supuestos declarados (revisar):**")
            for supuesto in informe.supuestos:
                st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;— {supuesto}")
        if informe.marcos:
            st.markdown("**Marcos editoriales (no verificables):**")
            for marco in informe.marcos:
                st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;— {marco}")


def _mostrar_paquete(paquete: PaqueteCuracion, directorio: Path, curador: str) -> None:
    activo = paquete.activo_tipado()
    fuente = paquete.fuente

    st.subheader(f"{paquete.formato} · {fuente.autor}")
    st.caption(f"mensaje_id: {paquete.mensaje_id} · confianza: {activo.confianza:.2f}")

    with st.expander("Fuente", expanded=True):
        st.markdown(f"**{fuente.autor}** en *{fuente.canal}*:")
        st.write(fuente.texto)

    with st.expander("Activo", expanded=True):
        for campo, valor in _campos_visibles(activo):
            st.markdown(f"**{campo}:** {valor}")

    _mostrar_informe(paquete)

    with st.expander("Respuesta cruda del modelo"):
        st.code(paquete.respuesta_cruda or "(vacía)", language="json")

    st.divider()

    if paquete.decision == "pendiente":
        col_aprobar, col_rechazar = st.columns(2)
        with col_aprobar:
            if st.button(
                "✅ Aprobar",
                key=f"aprobar-{paquete.mensaje_id}-{paquete.formato}",
                type="primary",
                use_container_width=True,
            ):
                _guardar_decision(paquete, "aprobado", "", directorio, curador)
        with col_rechazar:
            motivo = st.text_input(
                "Motivo del rechazo",
                key=f"motivo-{paquete.mensaje_id}-{paquete.formato}",
                placeholder="por qué no se publica",
            )
            if st.button(
                "❌ Rechazar",
                key=f"rechazar-{paquete.mensaje_id}-{paquete.formato}",
                use_container_width=True,
            ):
                _guardar_decision(paquete, "rechazado", motivo, directorio, curador)
    else:
        fecha = paquete.fecha_decision
        fecha_txt = fecha.strftime("%Y-%m-%d %H:%M") if fecha else "?"
        st.success(f"{paquete.decision.capitalize()} por {paquete.curador or '?'} · {fecha_txt}")
        if paquete.motivo:
            st.write(f"Motivo: {paquete.motivo}")


def _guardar_decision(
    paquete: PaqueteCuracion,
    decision: Literal["aprobado", "rechazado"],
    motivo: str,
    directorio: Path,
    curador: str,
) -> None:
    """Aplica la firma y la persiste en el mismo archivo del paquete."""
    try:
        nuevo = aplicar_decision(paquete, decision, motivo=motivo, curador=curador)
    except ValueError as exc:
        st.error(str(exc))
        return
    guardar_paquete(nuevo, directorio)
    st.rerun()


def main() -> None:
    st.set_page_config(page_title="Curacion Sieve", layout="wide")
    st.title("Curacion de activos")

    curador = _curador_actual()

    directorio = Path(
        st.text_input(
            "Directorio de paquetes", value=_directorio_por_defecto(), key="directorio"
        )
    )
    paquetes = cargar_paquetes(directorio)

    if not paquetes:
        st.info(
            f"No hay paquetes en `{directorio}`. Corré "
            f"`sieve generar --curar-dir {directorio}` primero."
        )
        return

    por_decision = {
        "pendiente": [p for p in paquetes if p.decision == "pendiente"],
        "aprobado": [p for p in paquetes if p.decision == "aprobado"],
        "rechazado": [p for p in paquetes if p.decision == "rechazado"],
    }

    with st.sidebar:
        st.metric("Pendientes", len(por_decision["pendiente"]))
        st.metric("Aprobados", len(por_decision["aprobado"]))
        st.metric("Rechazados", len(por_decision["rechazado"]))
        st.divider()
        filtro = st.radio(
            "Filtrar",
            ["pendiente", "aprobado", "rechazado"],
            label_visibility="collapsed",
        )

    candidatos = por_decision[filtro]
    if not candidatos:
        st.info(f"No hay paquetes {filtro}.")
        return

    # Se muestra de a uno, el primero del filtro. Al firmar, `st.rerun`
    # recarga la lista y aparece el siguiente.
    _mostrar_paquete(candidatos[0], directorio, curador)


if __name__ == "__main__":
    main()
