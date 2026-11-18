"""
Reglas clínicas compartidas por los endpoints del panel del psicólogo.

- Alcance: un psicólogo solo ve y modifica los casos de su cartera.
- Trazabilidad: lectura de historias clínicas y cambios de estado quedan auditados.
- Ciclo de vida del caso (derivación clínica) con estados explícitos.
- Escalamiento: si una alerta no se atiende a tiempo, se avisa al resto del equipo.
"""

import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from ..models import AuditoriaAcceso, DerivacionClinica, Notificacion, Psicologo

# Estados del caso (derivación clínica), en orden de flujo.
CASE_STATES = {
    "pendiente": "Pendiente de contacto",
    "contactado": "Contactado",
    "en_seguimiento": "En seguimiento",
    "derivado_externo": "Derivado a servicio externo",
    "cerrado": "Cerrado",
    # Estado heredado de versiones anteriores; se trata como "en seguimiento".
    "atendido": "Atendido",
}
OPEN_CASE_STATES = {"pendiente", "contactado", "en_seguimiento", "atendido"}

# Escalamiento: minutos sin cambiar de "pendiente" antes de avisar al resto del equipo.
ESCALATION_URGENT_MINUTES = int(os.getenv("ESCALATION_URGENT_MINUTES", "120"))
ESCALATION_HIGH_MINUTES = int(os.getenv("ESCALATION_HIGH_MINUTES", str(24 * 60)))
ESCALATION_TITLE_PREFIX = "Escalamiento"


def resolve_psicologo(current_user: dict, db: Session) -> Psicologo | None:
    """Registro Psicologo del usuario autenticado, o None si es admin / no aplica."""
    if current_user.get("role") != "psicologo":
        return None
    try:
        return db.query(Psicologo).filter(Psicologo.id_usuario == UUID(current_user["id"])).first()
    except Exception:
        return None


def is_psicologo_without_profile(current_user: dict, psicologo: Psicologo | None) -> bool:
    """Psicólogo autenticado sin registro en la tabla psicologo: no tiene cartera."""
    return current_user.get("role") == "psicologo" and psicologo is None


def audit_access(db: Session, current_user: dict, accion: str, tabla: str, id_objetivo, detalle: str = "", ip: str | None = None) -> None:
    """
    Registra un acceso en auditoria_acceso sin interrumpir la operación principal
    si falla (p. ej. usuario sin fila en la tabla usuario).
    """
    try:
        user_id = UUID(str(current_user.get("id")))
        target = id_objetivo if isinstance(id_objetivo, UUID) else UUID(str(id_objetivo))
    except Exception:
        return
    try:
        db.add(AuditoriaAcceso(
            id_usuario=user_id,
            accion=accion[:50],
            tabla_objetivo=tabla[:50],
            id_objetivo=target,
            ip_origen=(ip or "")[:45] or None,
            detalle=detalle,
        ))
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[MindCheck] No se pudo auditar '{accion}': {exc}")


_last_escalation_check = 0.0
_escalation_lock = threading.Lock()


def escalate_overdue_alerts(db: Session, min_interval_seconds: int = 60) -> int:
    """
    Avisa a todos los psicólogos activos cuando un caso sigue "pendiente" más allá
    del plazo (urgente: 2 h; alto: 24 h por defecto). Cada caso se escala una sola
    vez. Se ejecuta de forma oportunista (al consultar notificaciones), con un
    intervalo mínimo para no repetir la consulta en cada petición.

    Devuelve cuántas notificaciones de escalamiento se crearon.
    """
    global _last_escalation_check
    now_ts = time.time()
    with _escalation_lock:
        if now_ts - _last_escalation_check < min_interval_seconds:
            return 0
        _last_escalation_check = now_ts

    now = datetime.now(timezone.utc)
    created = 0
    try:
        pending = (
            db.query(DerivacionClinica)
            .filter(DerivacionClinica.estado == "pendiente")
            .filter(DerivacionClinica.fecha_derivacion <= now - timedelta(minutes=min(ESCALATION_URGENT_MINUTES, ESCALATION_HIGH_MINUTES)))
            .all()
        )
        if not pending:
            return 0
        active_psicologos = db.query(Psicologo).filter(Psicologo.activo.is_(True)).all()
        for deriv in pending:
            fecha = deriv.fecha_derivacion
            if fecha is not None and fecha.tzinfo is None:
                fecha = fecha.replace(tzinfo=timezone.utc)
            limit = ESCALATION_URGENT_MINUTES if deriv.nivel_prioridad == "urgente" else ESCALATION_HIGH_MINUTES
            if fecha is None or now - fecha < timedelta(minutes=limit):
                continue
            existing = db.query(Notificacion).filter(Notificacion.id_resultado == deriv.id_resultado).all()
            if any((n.titulo or "").startswith(ESCALATION_TITLE_PREFIX) for n in existing):
                continue  # ya escalado
            base = existing[0] if existing else None
            already = {n.id_psicologo for n in existing}
            hours = int((now - fecha).total_seconds() // 3600)
            for psico in active_psicologos:
                if psico.id_psicologo in already and psico.id_psicologo == deriv.id_psicologo:
                    continue  # el responsable ya tiene su aviso original
                db.add(Notificacion(
                    id_notificacion=uuid.uuid4(),
                    id_psicologo=psico.id_psicologo,
                    id_resultado=deriv.id_resultado,
                    titulo=f"{ESCALATION_TITLE_PREFIX}: caso sin contactar",
                    mensaje=(
                        f"Un caso de prioridad {deriv.nivel_prioridad} lleva {hours} h sin ser contactado por el "
                        f"psicólogo asignado. Coordinen quién lo atiende y registren la acción en el caso."
                    ),
                    nivel_riesgo=base.nivel_riesgo if base else "alto",
                    alerta_suicidio=bool(base.alerta_suicidio) if base else deriv.nivel_prioridad == "urgente",
                    leida=False,
                    revisada=False,
                ))
                created += 1
        if created:
            db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[MindCheck] Error en escalamiento de alertas: {exc}")
        return 0
    return created
