from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, extract, cast, String
from datetime import datetime, timedelta
from uuid import UUID
from typing import List
import io
from ..database import get_db
from ..models import Evaluacion, Resultado, Respuesta, Pregunta, Estudiante, DerivacionClinica, Usuario, AuditoriaAcceso, ModeloVersion, VistaSeudonimizadaML, ObservacionSeguimiento, Psicologo, Cita, Intervencion, Notificacion, AuditoriaModelML, ChatbotInteraccion
from ..schemas import (
    StatisticsResponse,
    RiskDistribution,
    ReportItem,
    StudentHistoryItem,
    AssignedPatientItem,
    AppointmentCreate,
    AppointmentUpdate,
    AppointmentResponse,
    UserResponse,
    UserUpdateRequest,
    ModelStatusResponse,
    ModelRetrainRequest,
    ModelRetrainResponse,
    DerivacionResponse,
    DerivacionUpdate,
    IntervencionCreate,
    IntervencionUpdate,
    IntervencionResponse,
    NotificacionResponse,
    NotificacionMarkRevisada,
    ObservacionResponse,
    StudentEvolutionItem,
    ChatbotMetricsResponse,
    ChatbotInteractionResponse,
    AuditLogResponse,
    AuditLogListResponse,
)
from ..security import require_role, get_supabase_client
from ..utils.queries import phq9_scores_subquery
from ..utils.clinical import (
    CASE_STATES,
    OPEN_CASE_STATES,
    audit_access,
    escalate_overdue_alerts,
    is_psicologo_without_profile,
)

# Nota de rendimiento: los endpoints que usan la sesión síncrona de SQLAlchemy
# se declaran con `def` (no `async def`). FastAPI los ejecuta en un threadpool,
# así varias consultas del panel corren en paralelo en lugar de bloquear el
# event loop una tras otra.

router = APIRouter(prefix="/admin", tags=["Administration & Reports"])


def _resolve_psicologo(current_user: dict, db: Session) -> Psicologo | None:
    """Devuelve el registro Psicologo del usuario autenticado, o None si es admin/no aplica."""
    if current_user.get("role") != "psicologo":
        return None
    try:
        return db.query(Psicologo).filter(Psicologo.id_usuario == UUID(current_user["id"])).first()
    except Exception:
        return None


def _find_student_by_key(db: Session, student_key: str) -> Estudiante | None:
    """
    Busca un estudiante por el prefijo de su UUID (ID anónimo #XXXXXX).
    Si el prefijo coincide con más de un estudiante, se rechaza en lugar de abrir
    la historia de una persona equivocada.
    """
    matches = (
        db.query(Estudiante)
        .filter(func.lower(func.substr(cast(Estudiante.id_estudiante, String), 1, len(student_key))) == student_key)
        .limit(2)
        .all()
    )
    if len(matches) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El ID anónimo coincide con más de un estudiante. Contacta al administrador para identificar el caso.",
        )
    return matches[0] if matches else None


def _assert_student_in_scope(psicologo: Psicologo | None, student_id, db: Session) -> None:
    """
    T-002: si quien llama es un psicólogo (no admin), exige que el estudiante tenga
    al menos una evaluación asignada a ese psicólogo antes de exponer su historial
    clínico, observaciones o intervenciones. Evita que un psicólogo consulte/edite
    el expediente de un paciente que no le fue asignado solo conociendo su ID.
    """
    if psicologo is None:
        return
    has_access = db.query(Evaluacion).filter(
        Evaluacion.id_estudiante == student_id,
        Evaluacion.id_psicologo == psicologo.id_psicologo,
    ).first()
    if not has_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Este estudiante no está asignado a tu cartera de pacientes.",
        )

@router.get("/statistics", response_model=StatisticsResponse)
def get_statistics(
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db)
):
    """
    Estadísticas del panel. Para un psicólogo se limitan a su cartera (evaluaciones
    asignadas a él); para el admin, a toda la institución.
    """
    psicologo = _resolve_psicologo(current_user, db)
    no_scope = is_psicologo_without_profile(current_user, psicologo)
    empty = StatisticsResponse(
        total=0, averageScore=0.0,
        riskDistribution=RiskDistribution(minimal=0, mild=0, moderate=0, moderatelySevere=0, severe=0),
    )
    if no_scope:
        return empty  # psicólogo sin perfil: sin cartera

    eval_filters = [Evaluacion.id_psicologo == psicologo.id_psicologo] if psicologo is not None else []

    # 0. Estudiantes
    evaluated_students = (
        db.query(func.count(func.distinct(Evaluacion.id_estudiante))).filter(*eval_filters).scalar() or 0
    )
    if psicologo is not None:
        total_students = evaluated_students
    else:
        total_students = (
            db.query(func.count(Estudiante.id_estudiante)).filter(Estudiante.activo.is_(True)).scalar() or 0
        )

    # 1. Casos abiertos y alertas suicidas activas (derivaciones no cerradas)
    deriv_q = (
        db.query(DerivacionClinica.estado, Resultado.alerta_suicidio)
        .join(Resultado, Resultado.id_resultado == DerivacionClinica.id_resultado)
        .filter(DerivacionClinica.estado.in_(OPEN_CASE_STATES))
    )
    if psicologo is not None:
        deriv_q = deriv_q.filter(DerivacionClinica.id_psicologo == psicologo.id_psicologo)
    open_rows = deriv_q.all()
    open_cases = len(open_rows)
    active_alerts = sum(1 for _, alerta in open_rows if alerta)
    pending_contact = sum(1 for estado, _ in open_rows if estado == "pendiente")

    # 2. Evaluaciones
    total = db.query(func.count(Evaluacion.id_evaluacion)).filter(*eval_filters).scalar() or 0
    if total == 0:
        empty.totalStudents = total_students
        empty.evaluatedStudents = evaluated_students
        empty.openCases, empty.activeSuicideAlerts, empty.pendingContact = open_cases, active_alerts, pending_contact
        return empty

    # 3. Puntaje PHQ-9 promedio (suma por evaluación, luego promedio)
    scores = phq9_scores_subquery(db)
    avg_score = (
        db.query(func.avg(scores.c.puntaje))
        .join(Evaluacion, Evaluacion.id_evaluacion == scores.c.id_evaluacion)
        .filter(*eval_filters)
        .scalar()
        or 0.0
    )

    # 4. Distribución por nivel de riesgo
    distribution_counts = (
        db.query(Resultado.nivel_riesgo, func.count(Resultado.id_resultado))
        .join(Evaluacion, Evaluacion.id_evaluacion == Resultado.id_evaluacion)
        .filter(*eval_filters)
        .group_by(Resultado.nivel_riesgo)
        .all()
    )
    dist_dict = {"minimo": 0, "leve": 0, "moderado": 0, "moderadamente_severo": 0, "severo": 0}
    for nivel, count in distribution_counts:
        if nivel in dist_dict:
            dist_dict[nivel] = count

    return StatisticsResponse(
        total=total,
        averageScore=float(round(avg_score, 1)),
        riskDistribution=RiskDistribution(
            minimal=dist_dict["minimo"],
            mild=dist_dict["leve"],
            moderate=dist_dict["moderado"],
            moderatelySevere=dist_dict["moderadamente_severo"],
            severe=dist_dict["severo"]
        ),
        totalStudents=total_students,
        evaluatedStudents=evaluated_students,
        activeSuicideAlerts=active_alerts,
        openCases=open_cases,
        pendingContact=pending_contact,
    )

@router.get("/reports", response_model=list[ReportItem])
def get_reports(
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db)
):
    """
    Lists screening reports with details. It maintains anonymity while
    including clinical metadata like career, risk level, and date.
    """
    # Join Evaluacion, Resultado, Estudiante + puntaje agregado en una sola consulta
    scores = phq9_scores_subquery(db)
    query = db.query(
        Evaluacion.id_evaluacion,
        Evaluacion.fecha_evaluacion,
        Resultado.nivel_riesgo,
        Resultado.alerta_suicidio,
        Estudiante.id_estudiante,
        Estudiante.carrera,
        Estudiante.universidad,
        Evaluacion.estado,
        scores.c.puntaje,
    ).join(
        Resultado, Evaluacion.id_evaluacion == Resultado.id_evaluacion
    ).join(
        Estudiante, Evaluacion.id_estudiante == Estudiante.id_estudiante
    ).outerjoin(
        scores, scores.c.id_evaluacion == Evaluacion.id_evaluacion
    )

    # Un psicólogo solo debe ver los reportes de los estudiantes que le fueron
    # asignados, no el listado completo del sistema (mismo criterio que
    # assigned-patients / student-history — ver hallazgo T-002).
    psicologo = _resolve_psicologo(current_user, db)
    if psicologo is not None:
        query = query.filter(Evaluacion.id_psicologo == psicologo.id_psicologo)

    results = query.order_by(Evaluacion.fecha_evaluacion.desc()).all()

    report_list = []
    for row in results:
        # Build stable anonymous student ID from student UUID prefix.
        anon_id = f"#{row.id_estudiante.hex[:6].upper()}"

        report_list.append(
            ReportItem(
                id_anonimo=anon_id,
                fecha=row.fecha_evaluacion,
                nivel_riesgo=row.nivel_riesgo.replace("_", " ").capitalize(),
                puntaje=int(row.puntaje or 0),
                alerta_suicidio=row.alerta_suicidio,
                carrera=row.carrera,
                universidad=row.universidad
            )
        )

    return report_list

@router.get("/student-history/{anon_student_id}", response_model=list[StudentHistoryItem])
def get_student_history(
    anon_student_id: str,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    """
    Historial cronológico de evaluaciones de un estudiante (por ID anónimo).
    Cada lectura queda registrada en auditoria_acceso (trazabilidad de acceso a
    historias clínicas).
    """
    student, results = _build_student_history(anon_student_id, current_user, db)
    audit_access(
        db, current_user, "lectura_historial_clinico", "estudiante", student.id_estudiante,
        f"Consultó el historial de {results[0].id_anonimo if results else anon_student_id} ({len(results)} evaluaciones).",
    )
    return results


def _build_student_history(anon_student_id: str, current_user: dict, db: Session):
    """Devuelve (estudiante, lista de StudentHistoryItem). Valida alcance del psicólogo."""
    student_key = anon_student_id.lstrip("#").strip().lower()
    if not student_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Identificador de estudiante no válido")

    student = _find_student_by_key(db, student_key)

    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Estudiante no encontrado")

    psicologo = _resolve_psicologo(current_user, db)
    _assert_student_in_scope(psicologo, student.id_estudiante, db)

    history_rows = (
        db.query(
            Evaluacion.id_evaluacion,
            Evaluacion.fecha_evaluacion,
            Resultado.nivel_riesgo,
            Resultado.alerta_suicidio,
            Estudiante.carrera,
            Estudiante.universidad,
            Evaluacion.estado,
            func.max(DerivacionClinica.accion_tomada).label("comentarios"),
        )
        .join(Resultado, Evaluacion.id_evaluacion == Resultado.id_evaluacion)
        .join(Estudiante, Evaluacion.id_estudiante == Estudiante.id_estudiante)
        .outerjoin(DerivacionClinica, DerivacionClinica.id_resultado == Resultado.id_resultado)
        .filter(Estudiante.id_estudiante == student.id_estudiante)
        .group_by(
            Evaluacion.id_evaluacion,
            Evaluacion.fecha_evaluacion,
            Resultado.nivel_riesgo,
            Resultado.alerta_suicidio,
            Estudiante.carrera,
            Estudiante.universidad,
            Evaluacion.estado,
        )
        .order_by(Evaluacion.fecha_evaluacion.desc())
        .all()
    )
    eval_ids = [row.id_evaluacion for row in history_rows]

    # Datos por evaluación cargados en lote (antes eran 3 consultas por fila).
    scores_by_eval: dict = {}
    interp_by_eval: dict = {}
    obs_by_eval: dict = {}
    items_by_eval: dict = {}
    if eval_ids:
        # Respuestas PHQ-9 ítem por ítem (el ítem 9 es clave para valorar riesgo).
        for eval_id, valor in (
            db.query(Respuesta.id_evaluacion, Respuesta.valor)
            .join(Pregunta, Pregunta.id_pregunta == Respuesta.id_pregunta)
            .filter(Respuesta.id_evaluacion.in_(eval_ids))
            .order_by(Respuesta.id_evaluacion, Pregunta.orden)
            .all()
        ):
            items_by_eval.setdefault(eval_id, []).append(int(valor))
        scores_by_eval = dict(
            db.query(Respuesta.id_evaluacion, func.sum(Respuesta.valor))
            .filter(Respuesta.id_evaluacion.in_(eval_ids))
            .group_by(Respuesta.id_evaluacion)
            .all()
        )
        # HU0018 (T-005): expone la interpretabilidad al psicólogo. Se consulta
        # aparte porque Postgres 'json' plano no soporta igualdad para GROUP BY.
        interp_by_eval = dict(
            db.query(Resultado.id_evaluacion, Resultado.interpretabilidad)
            .filter(Resultado.id_evaluacion.in_(eval_ids))
            .all()
        )
        for o in (
            db.query(ObservacionSeguimiento)
            .filter(ObservacionSeguimiento.id_evaluacion.in_(eval_ids))
            .order_by(ObservacionSeguimiento.created_at.desc())
            .all()
        ):
            obs_by_eval.setdefault(o.id_evaluacion, []).append({
                "id_observacion": o.id_observacion,
                "id_evaluacion": o.id_evaluacion,
                "id_psicologo": o.id_psicologo,
                "texto": o.texto,
                "created_at": o.created_at,
                "updated_at": o.updated_at,
                # Solo el autor (o un admin) puede editarla; mismo criterio que el PUT.
                "editable": psicologo is None or o.id_psicologo == psicologo.id_psicologo,
            })

    results = []
    for row in history_rows:
        results.append(
            StudentHistoryItem(
                id_anonimo=f"#{student.id_estudiante.hex[:6].upper()}",
                fecha=row.fecha_evaluacion,
                nivel_riesgo=row.nivel_riesgo.replace("_", " ").capitalize(),
                puntaje=int(scores_by_eval.get(row.id_evaluacion) or 0),
                alerta_suicidio=row.alerta_suicidio,
                carrera=row.carrera,
                universidad=row.universidad,
                interpretabilidad=interp_by_eval.get(row.id_evaluacion),
                estado_evaluacion=row.estado,
                comentarios=row.comentarios,
                observaciones=obs_by_eval.get(row.id_evaluacion, []),
                respuestas_phq9=items_by_eval.get(row.id_evaluacion) or None,
            )
        )

    return student, results


@router.get("/student-history/{anon_student_id}/export/pdf")
def export_student_history_pdf(
    anon_student_id: str,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    """
    HU0028: exporta el reporte individual de evaluación de un estudiante
    (historial, evolución y observaciones) en PDF descargable. Disponible
    para admin y para el psicólogo que tenga asignado al estudiante
    (reutiliza el mismo control de alcance que /student-history).
    """
    student, history = _build_student_history(anon_student_id, current_user, db)
    audit_access(
        db, current_user, "exportacion_historial_pdf", "estudiante", student.id_estudiante,
        f"Exportó a PDF el historial de {history[0].id_anonimo if history else anon_student_id}.",
    )
    if not history:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El estudiante no tiene evaluaciones registradas")

    student_label = history[0].id_anonimo

    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib import colors

        pdf_buffer = io.BytesIO()
        doc = SimpleDocTemplate(pdf_buffer, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
        story = []

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'ReportTitle', parent=styles['Heading1'], fontSize=20, leading=24,
            textColor=colors.HexColor('#1E293B'), spaceAfter=12,
        )
        subtitle_style = ParagraphStyle(
            'ReportSubtitle', parent=styles['Normal'], fontSize=10,
            textColor=colors.HexColor('#64748B'), spaceAfter=16,
        )
        section_style = ParagraphStyle(
            'SectionTitle', parent=styles['Heading2'], fontSize=13,
            textColor=colors.HexColor('#1E293B'), spaceBefore=14, spaceAfter=6,
        )
        body_style = styles['Normal']

        story.append(Paragraph(f"MindCheck - Reporte Individual del Estudiante {student_label}", title_style))
        story.append(Paragraph(
            f"Generado el: {datetime.now().strftime('%Y-%m-%d %H:%M')} | Carrera: {history[0].carrera} | Universidad: {history[0].universidad}",
            subtitle_style,
        ))

        story.append(Paragraph("Historial y evolución de evaluaciones", section_style))
        table_data = [["Fecha", "Nivel de Riesgo", "PHQ-9", "Ítem 9", "Alerta", "Estado"]]
        for item in history:
            table_data.append([
                item.fecha.strftime("%Y-%m-%d") if item.fecha else "",
                item.nivel_riesgo,
                str(item.puntaje),
                f"{item.respuestas_phq9[8]}/3" if item.respuestas_phq9 and len(item.respuestas_phq9) >= 9 else "—",
                "CRITICO" if item.alerta_suicidio else "Normal",
                item.estado_evaluacion or "",
            ])
        t = Table(table_data, colWidths=[80, 130, 60, 60, 80, 90])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4A90E2')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#FFFFFF'), colors.HexColor('#F1F5F9')]),
        ]))
        story.append(t)

        story.append(Paragraph("Observaciones de seguimiento", section_style))
        any_obs = False
        for item in history:
            for obs in (item.observaciones or []):
                any_obs = True
                fecha_obs = obs.created_at.strftime("%Y-%m-%d %H:%M") if obs.created_at else ""
                story.append(Paragraph(f"<b>{fecha_obs}</b> — {obs.texto or ''}", body_style))
                story.append(Spacer(1, 4))
        if not any_obs:
            story.append(Paragraph("Sin observaciones registradas.", body_style))

        doc.build(story)
        pdf_buffer.seek(0)
        filename = f"reporte_{student_label.lstrip('#')}.pdf"
        return StreamingResponse(
            pdf_buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except ImportError:
        # Fallback si reportlab no está disponible: reporte de texto plano.
        output = io.StringIO()
        output.write(f"MindCheck - Reporte Individual del Estudiante {student_label}\n")
        output.write(f"Generado el: {datetime.now().isoformat()}\n\n")
        output.write(f"{'FECHA':<20}{'RIESGO':<25}{'PUNTAJE':<10}{'ALERTA'}\n")
        for item in history:
            fecha_str = item.fecha.strftime('%Y-%m-%d %H:%M') if item.fecha else ""
            output.write(f"{fecha_str:<20}{item.nivel_riesgo:<25}{item.puntaje:<10}{'SI' if item.alerta_suicidio else 'NO'}\n")
        buffer = io.BytesIO(output.getvalue().encode("utf-8"))
        return StreamingResponse(
            buffer,
            media_type="text/plain",
            headers={"Content-Disposition": f"attachment; filename=reporte_{student_label.lstrip('#')}.txt"},
        )


@router.get("/assigned-patients", response_model=list[AssignedPatientItem])
def get_assigned_patients(
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
    risk_levels: list[str] | None = Query(default=None),
):
    """
    Returns the list of students assigned to the authenticated psychologist.
    Administrators can view the same listing for all students.
    """
    user_role = current_user.get("role")
    scores = phq9_scores_subquery(db)
    query = db.query(
        Estudiante.id_estudiante,
        Estudiante.carrera,
        Estudiante.universidad,
        Evaluacion.id_evaluacion,
        Evaluacion.fecha_evaluacion,
        Evaluacion.estado,
        Resultado.nivel_riesgo,
        Resultado.alerta_suicidio,
        scores.c.puntaje,
        DerivacionClinica.estado.label("estado_caso"),
    ).join(
        Evaluacion, Estudiante.id_estudiante == Evaluacion.id_estudiante
    ).join(
        Resultado, Evaluacion.id_evaluacion == Resultado.id_evaluacion
    ).outerjoin(
        scores, scores.c.id_evaluacion == Evaluacion.id_evaluacion
    ).outerjoin(
        DerivacionClinica, DerivacionClinica.id_resultado == Resultado.id_resultado
    )

    if user_role == "psicologo":
        psicologo = None
        try:
            psicologo = db.query(Psicologo).filter(Psicologo.id_usuario == UUID(current_user["id"])) .first()
        except Exception:
            psicologo = None

        if psicologo is None:
            return []

        query = query.filter(Evaluacion.id_psicologo == psicologo.id_psicologo)

    # Evaluación más reciente de cada estudiante (global). Para el psicólogo, el
    # filtro anterior deja solo a los estudiantes cuya evaluación más reciente
    # está asignada a él: su cartera actual.
    subquery = db.query(
        Evaluacion.id_estudiante.label("estudiante_id"),
        func.max(Evaluacion.fecha_evaluacion).label("last_date"),
    ).group_by(Evaluacion.id_estudiante).subquery()

    query = query.join(
        subquery,
        (Evaluacion.id_estudiante == subquery.c.estudiante_id) &
        (Evaluacion.fecha_evaluacion == subquery.c.last_date),
    )

    if risk_levels:
        query = query.filter(Resultado.nivel_riesgo.in_(risk_levels))

    # Triaje: ítem 9 positivo con caso abierto, luego casos sin contactar,
    # luego severidad y, a igual prioridad, lo más reciente primero.
    severity_rank = {"severo": 0, "moderadamente_severo": 1, "moderado": 2, "leve": 3, "minimo": 4}
    rows = sorted(
        query.all(),
        key=lambda r: (
            0 if (r.alerta_suicidio and (r.estado_caso in OPEN_CASE_STATES)) else 1,
            0 if r.estado_caso == "pendiente" else 1,
            severity_rank.get(r.nivel_riesgo, 9),
            -(r.fecha_evaluacion.timestamp() if r.fecha_evaluacion else 0),
        ),
    )

    assigned = []
    for row in rows:
        score = row.puntaje or 0
        assigned.append(
            AssignedPatientItem(
                id_anonimo=f"#{row.id_estudiante.hex[:6].upper()}",
                carrera=row.carrera,
                universidad=row.universidad,
                ultima_evaluacion=row.fecha_evaluacion,
                nivel_riesgo=row.nivel_riesgo.replace("_", " ").capitalize(),
                puntaje=int(score),
                alerta_suicidio=row.alerta_suicidio,
                estado_evaluacion=row.estado,
                estado_caso=row.estado_caso,
            )
        )

    return assigned


@router.get("/appointments", response_model=list[AppointmentResponse])
def list_appointments(
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    """
    List appointments for the authenticated psychologist or all appointments for admin.
    """
    user_role = current_user.get("role")
    query = db.query(Cita, Estudiante.carrera).outerjoin(
        Estudiante, Estudiante.id_estudiante == Cita.id_estudiante
    )
    if user_role == "psicologo":
        try:
            psicologo = db.query(Psicologo).filter(Psicologo.id_usuario == UUID(current_user["id"])) .first()
        except Exception:
            psicologo = None
        if psicologo is None:
            return []
        query = query.filter(Cita.id_psicologo == psicologo.id_psicologo)

    rows = query.order_by(Cita.fecha_inicio.desc()).all()
    result = [
        AppointmentResponse(
            id_cita=r.id_cita,
            id_psicologo=r.id_psicologo,
            id_estudiante=r.id_estudiante,
            fecha_inicio=r.fecha_inicio,
            duracion_minutos=r.duracion_minutos,
            estado=r.estado,
            descripcion=r.descripcion,
            created_at=r.created_at,
            id_anonimo=f"#{r.id_estudiante.hex[:6].upper()}",
            carrera=carrera,
        )
        for r, carrera in rows
    ]
    return result


@router.post("/appointments", response_model=AppointmentResponse, status_code=201)
def create_appointment(
    payload: AppointmentCreate,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    """
    Schedule a new appointment (cita) between a psychologist and a student.
    Psychologists will have their own id inferred from the authenticated user.
    """
    # resolve estudiante identifier
    student_key = payload.id_estudiante.lstrip("#").strip().lower()
    if not student_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Identificador de estudiante no válido")

    student = _find_student_by_key(db, student_key)

    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Estudiante no encontrado")

    # resolve psicólogo
    user_role = current_user.get("role")
    psicologo_id = None
    if user_role == "psicologo":
        try:
            psicologo = db.query(Psicologo).filter(Psicologo.id_usuario == UUID(current_user["id"])) .first()
        except Exception:
            psicologo = None
        if psicologo is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Psicólogo no encontrado")
        # Solo puede agendar con estudiantes de su cartera.
        _assert_student_in_scope(psicologo, student.id_estudiante, db)
        psicologo_id = psicologo.id_psicologo
    else:
        # admin schedules using the most recent psychologist assigned to the student
        last_eval = db.query(Evaluacion).filter(Evaluacion.id_estudiante == student.id_estudiante).order_by(Evaluacion.fecha_evaluacion.desc()).first()
        if last_eval is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No se encontró psicólogo asignado al estudiante")
        psicologo_id = last_eval.id_psicologo

    # check overlapping appointments for this psychologist
    start = payload.fecha_inicio
    end = payload.fecha_inicio + timedelta(minutes=payload.duracion_minutos)

    nearby = db.query(Cita).filter(Cita.id_psicologo == psicologo_id).filter(
        Cita.fecha_inicio >= (start - timedelta(hours=24)),
        Cita.fecha_inicio <= (end + timedelta(hours=24)),
    ).all()

    for ap in nearby:
        ap_start = ap.fecha_inicio
        ap_end = ap.fecha_inicio + timedelta(minutes=ap.duracion_minutos)
        if not (end <= ap_start or start >= ap_end):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ese horario ya está ocupado para el psicólogo. Por favor selecciona otro horario.",
            )

    new = Cita(
        id_psicologo=psicologo_id,
        id_estudiante=student.id_estudiante,
        fecha_inicio=payload.fecha_inicio,
        duracion_minutos=payload.duracion_minutos,
        descripcion=payload.descripcion,
        estado="programada",
    )
    db.add(new)
    db.commit()
    db.refresh(new)

    return AppointmentResponse(
        id_cita=new.id_cita,
        id_psicologo=new.id_psicologo,
        id_estudiante=new.id_estudiante,
        fecha_inicio=new.fecha_inicio,
        duracion_minutos=new.duracion_minutos,
        estado=new.estado,
        descripcion=new.descripcion,
        created_at=new.created_at,
        id_anonimo=f"#{student.id_estudiante.hex[:6].upper()}",
        carrera=student.carrera,
    )


APPOINTMENT_STATES = {"programada", "completada", "no_asistio", "cancelada"}


@router.put("/appointments/{appointment_id}", response_model=AppointmentResponse)
def update_appointment(
    appointment_id: UUID,
    payload: AppointmentUpdate,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    """Completa, cancela, marca inasistencia o reprograma una cita."""
    cita = db.query(Cita).filter(Cita.id_cita == appointment_id).first()
    if cita is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cita no encontrada")

    psicologo = _resolve_psicologo(current_user, db)
    if is_psicologo_without_profile(current_user, psicologo) or (
        psicologo is not None and cita.id_psicologo != psicologo.id_psicologo
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Esta cita no pertenece a tu agenda.")

    if payload.estado is not None:
        if payload.estado not in APPOINTMENT_STATES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Estado inválido. Usa: {', '.join(sorted(APPOINTMENT_STATES))}")
        cita.estado = payload.estado
    if payload.duracion_minutos is not None:
        if not 15 <= payload.duracion_minutos <= 240:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La duración debe estar entre 15 y 240 minutos")
        cita.duracion_minutos = payload.duracion_minutos
    if payload.fecha_inicio is not None:
        start = payload.fecha_inicio
        end = start + timedelta(minutes=cita.duracion_minutos)
        overlapping = db.query(Cita).filter(
            Cita.id_psicologo == cita.id_psicologo,
            Cita.id_cita != cita.id_cita,
            Cita.estado == "programada",
            Cita.fecha_inicio >= start - timedelta(hours=24),
            Cita.fecha_inicio <= end + timedelta(hours=24),
        ).all()
        for ap in overlapping:
            ap_end = ap.fecha_inicio + timedelta(minutes=ap.duracion_minutos)
            if not (end <= ap.fecha_inicio or start >= ap_end):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ese horario ya está ocupado. Elige otro.")
        cita.fecha_inicio = start
        cita.estado = "programada"
    if payload.descripcion is not None:
        cita.descripcion = payload.descripcion

    db.add(cita)
    db.commit()
    db.refresh(cita)
    estudiante = db.query(Estudiante.carrera).filter(Estudiante.id_estudiante == cita.id_estudiante).first()
    return AppointmentResponse(
        id_cita=cita.id_cita,
        id_psicologo=cita.id_psicologo,
        id_estudiante=cita.id_estudiante,
        fecha_inicio=cita.fecha_inicio,
        duracion_minutos=cita.duracion_minutos,
        estado=cita.estado,
        descripcion=cita.descripcion,
        created_at=cita.created_at,
        id_anonimo=f"#{cita.id_estudiante.hex[:6].upper()}",
        carrera=estudiante.carrera if estudiante else None,
    )


@router.get("/student/evolution", response_model=List[StudentEvolutionItem])
def get_student_evolution(
    current_user: dict = Depends(require_role(["estudiante", "admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    """
    Returns the chronological list of evaluations for the authenticated student.
    """
    user_id = current_user.get("id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no autenticado")

    # Find student by linked usuario id
    try:
        student = db.query(Estudiante).filter(Estudiante.id_usuario == UUID(user_id)).first()
    except Exception:
        student = db.query(Estudiante).filter(Estudiante.id_usuario == user_id).first()

    if not student:
        # If the caller is admin/psicologo and not a student, return empty
        return []

    scores = phq9_scores_subquery(db)
    rows = (
        db.query(
            Evaluacion.id_evaluacion,
            Evaluacion.fecha_evaluacion,
            Resultado.nivel_riesgo,
            Resultado.alerta_suicidio,
            scores.c.puntaje,
        )
        .join(Resultado, Evaluacion.id_evaluacion == Resultado.id_evaluacion)
        .outerjoin(scores, scores.c.id_evaluacion == Evaluacion.id_evaluacion)
        .filter(Evaluacion.id_estudiante == student.id_estudiante)
        .order_by(Evaluacion.fecha_evaluacion.asc())
        .all()
    )

    series = []
    for r in rows:
        series.append({
            "fecha": r.fecha_evaluacion,
            "puntaje": int(r.puntaje or 0),
            "nivel_riesgo": r.nivel_riesgo.replace("_", " ").capitalize(),
            "alerta_suicidio": r.alerta_suicidio,
        })

    return series


@router.post("/student-history/{anon_student_id}/observations", response_model=ObservacionResponse)
def create_student_observation(
    anon_student_id: str,
    payload: dict,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    """
    Create a new observation/note for a student (optionally linked to an evaluation).
    """
    from ..schemas import ObservacionCreate

    body = ObservacionCreate(**payload)

    student_key = anon_student_id.lstrip("#").strip().lower()
    if not student_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Identificador de estudiante no válido")

    student = _find_student_by_key(db, student_key)
    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Estudiante no encontrado")

    psicologo = _resolve_psicologo(current_user, db)
    _assert_student_in_scope(psicologo, student.id_estudiante, db)

    # Sin evaluación indicada, se vincula a la más reciente del estudiante; si no,
    # la observación quedaría huérfana y nunca aparecería en su historial.
    if body.id_evaluacion is None:
        latest = (
            db.query(Evaluacion.id_evaluacion)
            .filter(Evaluacion.id_estudiante == student.id_estudiante)
            .order_by(Evaluacion.fecha_evaluacion.desc())
            .first()
        )
        if latest is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El estudiante no tiene evaluaciones a las que asociar la observación")
        body.id_evaluacion = latest.id_evaluacion

    # If an evaluation is provided, validate it belongs to the student
    if body.id_evaluacion is not None:
        ev = db.query(Evaluacion).filter(Evaluacion.id_evaluacion == body.id_evaluacion).first()
        if ev is None or ev.id_estudiante != student.id_estudiante:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Evaluación inválida para este estudiante")

    new_obs = ObservacionSeguimiento(
        id_evaluacion=body.id_evaluacion,
        id_psicologo=getattr(psicologo, "id_psicologo", None),
        texto=body.texto,
    )
    db.add(new_obs)
    db.commit()
    db.refresh(new_obs)

    return {
        "id_observacion": new_obs.id_observacion,
        "id_evaluacion": new_obs.id_evaluacion,
        "id_psicologo": new_obs.id_psicologo,
        "texto": new_obs.texto,
        "created_at": new_obs.created_at,
        "updated_at": new_obs.updated_at,
    }


@router.put("/observations/{observation_id}", response_model=ObservacionResponse)
def update_student_observation(
    observation_id: UUID,
    payload: dict,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    from ..schemas import ObservacionUpdate

    body = ObservacionUpdate(**payload)
    obs = db.query(ObservacionSeguimiento).filter(ObservacionSeguimiento.id_observacion == observation_id).first()
    if obs is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observación no encontrada")

    # T-007: solo el psicólogo autor de la observación (o un admin) puede editarla.
    psicologo = _resolve_psicologo(current_user, db)
    if psicologo is not None and obs.id_psicologo != psicologo.id_psicologo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el psicólogo que registró esta observación puede editarla.",
        )

    if body.texto is not None:
        obs.texto = body.texto
    db.add(obs)
    db.commit()
    db.refresh(obs)

    return {
        "id_observacion": obs.id_observacion,
        "id_evaluacion": obs.id_evaluacion,
        "id_psicologo": obs.id_psicologo,
        "texto": obs.texto,
        "created_at": obs.created_at,
        "updated_at": obs.updated_at,
    }

@router.get("/derivations", response_model=list[DerivacionResponse])
def get_clinical_derivations(
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db)
):
    """
    Casos clínicos (derivaciones) ordenados para triaje. Un psicólogo ve solo los
    casos asignados a él; el admin ve todos.
    """
    psicologo = _resolve_psicologo(current_user, db)
    if is_psicologo_without_profile(current_user, psicologo):
        return []

    scores = phq9_scores_subquery(db)
    query = (
        db.query(
            DerivacionClinica.id_derivacion,
            DerivacionClinica.nivel_prioridad,
            DerivacionClinica.accion_tomada,
            DerivacionClinica.fecha_derivacion,
            DerivacionClinica.estado,
            DerivacionClinica.institucion_referencia,
            Resultado.nivel_riesgo,
            Resultado.alerta_suicidio,
            Evaluacion.id_estudiante,
            scores.c.puntaje,
        )
        .join(Resultado, DerivacionClinica.id_resultado == Resultado.id_resultado)
        .join(Evaluacion, Evaluacion.id_evaluacion == Resultado.id_evaluacion)
        .outerjoin(scores, scores.c.id_evaluacion == Evaluacion.id_evaluacion)
    )
    if psicologo is not None:
        query = query.filter(DerivacionClinica.id_psicologo == psicologo.id_psicologo)

    priority_order = {"urgente": 0, "alto": 1, "moderado": 2}
    open_first = lambda estado: 0 if estado in OPEN_CASE_STATES else 1  # noqa: E731
    rows = sorted(
        query.all(),
        key=lambda d: (
            open_first(d.estado),
            priority_order.get(d.nivel_prioridad, 99),
            -(d.fecha_derivacion.timestamp() if d.fecha_derivacion else 0),
        ),
    )
    return [
        {
            "id": d.id_derivacion,
            "prioridad": d.nivel_prioridad,
            "accion": d.accion_tomada,
            "fecha": d.fecha_derivacion,
            "estado": d.estado,
            "institucion": d.institucion_referencia,
            "nivel_riesgo": d.nivel_riesgo,
            "alerta_suicidio": d.alerta_suicidio,
            "id_anonimo": f"#{d.id_estudiante.hex[:6].upper()}" if d.id_estudiante else None,
            "puntaje": int(d.puntaje) if d.puntaje is not None else None,
        }
        for d in rows
    ]


@router.put("/derivations/{derivation_id}", response_model=DerivacionResponse)
def update_clinical_derivation(
    derivation_id: UUID,
    payload: DerivacionUpdate,
    request: Request,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    """
    Registra una acción sobre un caso y, opcionalmente, cambia su estado.
    La nota es obligatoria al cambiar de estado; cada entrada queda en el
    historial del caso (con fecha y autor) y en auditoria_acceso.
    """
    derivation = db.query(DerivacionClinica).filter(DerivacionClinica.id_derivacion == derivation_id).first()
    if derivation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Derivación clínica no encontrada")

    psicologo = _resolve_psicologo(current_user, db)
    if is_psicologo_without_profile(current_user, psicologo) or (
        psicologo is not None and derivation.id_psicologo != psicologo.id_psicologo
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Este caso no está asignado a ti.")

    nota = (payload.accion_tomada or "").strip()
    nuevo_estado = payload.estado
    if nuevo_estado is not None and nuevo_estado not in CASE_STATES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Estado inválido. Usa: {', '.join(k for k in CASE_STATES if k != 'atendido')}",
        )
    cambia_estado = nuevo_estado is not None and nuevo_estado != derivation.estado
    if cambia_estado and not nota:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Describe brevemente la acción realizada para cambiar el estado del caso.",
        )

    if nota or cambia_estado:
        autor = current_user.get("email") or ("admin" if psicologo is None else "psicólogo")
        sello = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        etiqueta = f" → {CASE_STATES[nuevo_estado]}" if cambia_estado else ""
        entrada = f"[{sello} UTC · {autor}{etiqueta}] {nota}".strip()
        # Entradas más recientes primero, una por línea.
        derivation.accion_tomada = "\n".join(filter(None, [entrada, (derivation.accion_tomada or "").strip()]))
    if cambia_estado:
        anterior = derivation.estado
        derivation.estado = nuevo_estado
    if payload.institucion_referencia is not None:
        derivation.institucion_referencia = payload.institucion_referencia

    db.add(derivation)
    db.commit()
    db.refresh(derivation)

    if nota or cambia_estado:
        audit_access(
            db, current_user, "actualizacion_caso_clinico", "derivacion_clinica", derivation.id_derivacion,
            (f"Estado: {anterior} -> {nuevo_estado}. " if cambia_estado else "") + f"Nota: {nota}",
            ip=request.client.host if request.client else None,
        )

    row = (
        db.query(Resultado.nivel_riesgo, Resultado.alerta_suicidio, Evaluacion.id_estudiante, Evaluacion.id_evaluacion)
        .join(Evaluacion, Evaluacion.id_evaluacion == Resultado.id_evaluacion)
        .filter(Resultado.id_resultado == derivation.id_resultado)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error al leer los datos de resultado asociados")
    puntaje = db.query(func.sum(Respuesta.valor)).filter(Respuesta.id_evaluacion == row.id_evaluacion).scalar()

    return {
        "id": derivation.id_derivacion,
        "prioridad": derivation.nivel_prioridad,
        "accion": derivation.accion_tomada,
        "fecha": derivation.fecha_derivacion,
        "estado": derivation.estado,
        "institucion": derivation.institucion_referencia,
        "nivel_riesgo": row.nivel_riesgo,
        "alerta_suicidio": row.alerta_suicidio,
        "id_anonimo": f"#{row.id_estudiante.hex[:6].upper()}",
        "puntaje": int(puntaje) if puntaje is not None else None,
    }


@router.get("/trends")
def get_monthly_trends(
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    """
    Agrega evaluaciones por mes (últimos 6 meses) para gráficos del panel admin.
    """
    now = datetime.utcnow()
    months = []
    for i in range(5, -1, -1):
        d = now - timedelta(days=30 * i)
        months.append({"year": d.year, "month": d.month, "label": d.strftime("%b")})

    subquery = db.query(
        Respuesta.id_evaluacion,
        func.sum(Respuesta.valor).label("total_score"),
    ).group_by(Respuesta.id_evaluacion).subquery()

    # Psicólogo: solo su cartera.
    psicologo = _resolve_psicologo(current_user, db)
    scope = []
    if psicologo is not None:
        scope = [Evaluacion.id_psicologo == psicologo.id_psicologo]
    elif is_psicologo_without_profile(current_user, psicologo):
        scope = [Evaluacion.id_evaluacion.is_(None)]  # sin cartera: nada

    rows = (
        db.query(
            extract("year", Evaluacion.fecha_evaluacion).label("y"),
            extract("month", Evaluacion.fecha_evaluacion).label("m"),
            func.count(Evaluacion.id_evaluacion).label("evaluaciones"),
            func.avg(subquery.c.total_score).label("promedio"),
        )
        .outerjoin(subquery, Evaluacion.id_evaluacion == subquery.c.id_evaluacion)
        .filter(*scope)
        .group_by("y", "m")
        .order_by("y", "m")
        .all()
    )

    lookup = {
        (int(r.y), int(r.m)): {
            "evaluaciones": int(r.evaluaciones or 0),
            "promedios": round(float(r.promedio or 0), 1),
        }
        for r in rows
        if r.y is not None and r.m is not None
    }

    return [
        {
            "month": m["label"],
            "evaluaciones": lookup.get((m["year"], m["month"]), {}).get("evaluaciones", 0),
            "promedios": lookup.get((m["year"], m["month"]), {}).get("promedios", 0),
        }
        for m in months
    ]

@router.get("/model/status", response_model=ModelStatusResponse)
def get_model_status(
    current_user: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db)
):
    """
    Obtiene la versión activa actual del modelo y la cantidad de datos disponibles para reentrenamiento.
    """
    active_model = db.query(ModeloVersion).filter(ModeloVersion.activo == True).order_by(ModeloVersion.fecha_publicacion.desc()).first()
    record_count = db.query(func.count(VistaSeudonimizadaML.id_registro)).scalar() or 0

    if not active_model:
        return ModelStatusResponse(
            model_name="PHQ-9 Rule-based Engine",
            version="1.0",
            active_records=record_count,
            last_retrained_at=None,
            origen_datos="default",
            comentario="Versión inicial del modelo no encontrada en la base de datos."
        )

    # Get latest metrics from AuditoriaModelML
    audit = db.query(AuditoriaModelML).filter(
        AuditoriaModelML.model_version == active_model.version,
        AuditoriaModelML.tipo_evento == "entrenamiento"
    ).order_by(AuditoriaModelML.fecha_evento.desc()).first()

    return ModelStatusResponse(
        model_name=active_model.nombre,
        version=active_model.version,
        active_records=record_count,
        last_retrained_at=active_model.fecha_publicacion,
        origen_datos=active_model.origen_datos,
        comentario=active_model.comentario,
        accuracy=float(audit.accuracy) if audit and audit.accuracy is not None else None,
        precision=float(audit.precision) if audit and audit.precision is not None else None,
        recall=float(audit.recall) if audit and audit.recall is not None else None,
        f1_score=float(audit.f1_score) if audit and audit.f1_score is not None else None
    )


@router.post("/model/retrain", response_model=ModelRetrainResponse, status_code=status.HTTP_200_OK)
def retrain_model(
    payload: ModelRetrainRequest,
    request: Request,
    current_user: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db)
):
    """
    Inicia un reentrenamiento y comparación de modelos utilizando el dataset completo
    almacenado en la tabla vista_seudonimizada_ml de Supabase.
    """
    from ..utils.ml_model import train_and_compare_models, get_initial_dataset
    
    # 1. Cargar dataset completo desde la base de datos
    try:
        df_combined = get_initial_dataset(db)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al cargar el dataset de entrenamiento desde Supabase: {str(e)}"
        )

    # 2. Entrenar y comparar todos los modelos
    try:
        train_results, winner_name = train_and_compare_models(
            df_combined, db, include_comparator=payload.incluir_comparador
        )
    except Exception as train_err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error durante el reentrenamiento y comparación de modelos: {str(train_err)}"
        )

    # Winner details
    winner_metrics = train_results[winner_name]
    val_acc = winner_metrics["accuracy"]
    val_prec = winner_metrics["precision"]
    val_rec = winner_metrics["recall"]
    val_f1 = winner_metrics["f1_score"]

    # Map to performance response
    comparison_list = []
    for name, r in train_results.items():
        comparison_list.append({
            "model_name": name,
            "accuracy": r["accuracy"],
            "precision": r["precision"],
            "recall": r["recall"],
            "f1_score": r["f1_score"],
            "roc_auc": r.get("roc_auc"),
            "average_precision": r.get("average_precision"),
            "brier": r.get("brier"),
            "is_winner": r["is_winner"]
        })

    # 3. Registrar la nueva versión del modelo en la base de datos
    active_model = db.query(ModeloVersion).filter(ModeloVersion.activo == True).order_by(ModeloVersion.fecha_publicacion.desc()).first()
    previous_version = f"{active_model.nombre} v{active_model.version}" if active_model else None

    version = payload.version
    if not version:
        if active_model and active_model.version:
            parts = active_model.version.split('.')
            if parts[-1].isdigit():
                parts[-1] = str(int(parts[-1]) + 1)
                version = '.'.join(parts)
            else:
                version = f"{active_model.version}.1"
        else:
            version = "1.1"

    model_name = winner_name

    if len(version) > 20:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La versión del modelo no puede superar los 20 caracteres.",
        )

    # 3-4. Nueva versión + auditorías en UNA sola transacción: si algo falla no
    # queda una versión activa sin su registro de auditoría (antes se hacía
    # commit de la versión y luego fallaba el insert de auditoría).
    client_ip = (request.client.host if request.client else "127.0.0.1")[:45]
    admin_id = UUID(current_user["id"])
    try:
        if active_model:
            active_model.activo = False
            db.add(active_model)

        new_model = ModeloVersion(
            nombre=model_name[:100],
            version=version,
            origen_datos=(payload.origen_datos or "Supabase (dataset histórico + nuevos registros)")[:255],
            comentario=payload.comentario or f"Reentrenamiento. Modelo activo: {winner_name}.",
            activo=True,
        )
        db.add(new_model)
        db.flush()  # asigna id_modelo sin confirmar todavía

        db.add(AuditoriaModelML(
            tipo_evento="entrenamiento",
            id_usuario=admin_id,
            model_version=version,
            precision=val_prec,
            recall=val_rec,
            f1_score=val_f1,
            accuracy=val_acc,
            # La columna es VARCHAR(50).
            resultado_prediccion=f"{winner_name}; n={len(df_combined)}"[:50],
        ))
        db.add(AuditoriaAcceso(
            id_usuario=admin_id,
            accion="reentrenamiento_modelo",
            tabla_objetivo="modelo_version",
            id_objetivo=new_model.id_modelo,
            ip_origen=client_ip,
            detalle=f"Reentrenó el modelo. Activo: {winner_name} v{version}. Registros totales: {len(df_combined)}."
        ))
        db.commit()
    except Exception as persist_err:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"El modelo se entrenó, pero no se pudo registrar la nueva versión: {persist_err}",
        )

    return ModelRetrainResponse(
        success=True,
        model_name=model_name,
        version=version,
        previous_version=previous_version,
        updated_records=len(df_combined),
        message=(
            f"Reentrenamiento completado. Modelo activo: '{winner_name}' "
            f"(ROC-AUC OOF={winner_metrics.get('roc_auc', 0):.3f}, "
            f"Brier={winner_metrics.get('brier', 0):.3f})"
        ),
        comparison=comparison_list
    )


@router.get("/users", response_model=list[UserResponse])
def get_all_users(
    current_user: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db)
):
    """
    Lista todos los usuarios (HU0005). Solo accesible por administradores.
    """
    users = db.query(Usuario).order_by(Usuario.fecha_registro.desc()).all()
    return users


@router.put("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: UUID,
    update_data: UserUpdateRequest,
    request: Request,
    current_user: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db)
):
    """
    Modifica el rol o estado activo de un usuario (HU0005).
    Registra la acción en AuditoriaAcceso.
    """
    usuario = db.query(Usuario).filter(Usuario.id_usuario == user_id).first()
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado"
        )

    cambios = []
    
    # 1. Update Role
    if update_data.rol is not None and update_data.rol != usuario.rol:
        old_role = usuario.rol
        usuario.rol = update_data.rol
        cambios.append(f"rol: {old_role} -> {update_data.rol}")
        
        # Opcional: Modificar Supabase Auth User Metadata (Si existe Supabase)
        supabase = get_supabase_client()
        if supabase:
            try:
                # Usa Admin API para actualizar metadatos
                supabase.auth.admin.update_user_by_id(
                    str(user_id),
                    {"user_metadata": {"role": update_data.rol}}
                )
            except Exception as e:
                print(f"Warning: Failed to update Supabase metadata: {e}")

    # 2. Update Activo Status
    if update_data.activo is not None and update_data.activo != usuario.activo:
        old_status = usuario.activo
        usuario.activo = update_data.activo
        cambios.append(f"activo: {old_status} -> {update_data.activo}")

    if cambios:
        db.commit()
        db.refresh(usuario)
        
        # 3. Auditoría de cambios (AC4)
        client_ip = request.client.host if request.client else "127.0.0.1"
        admin_id = UUID(current_user["id"])
        
        auditoria = AuditoriaAcceso(
            id_usuario=admin_id,
            accion="modificacion_rol_permisos",
            tabla_objetivo="usuario",
            id_objetivo=user_id,
            ip_origen=client_ip,
            detalle=f"Modificó: {', '.join(cambios)}"
        )
        db.add(auditoria)
        db.commit()

    return usuario


# --- Clinical Interventions Routes (HU0027) ---

@router.get("/student-history/{anon_student_id}/interventions", response_model=list[IntervencionResponse])
def get_student_interventions(
    anon_student_id: str,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db)
):
    """
    Obtiene la lista cronológica de intervenciones realizadas a un estudiante (HU0027).
    """
    student_key = anon_student_id.lstrip("#").strip().lower()
    if not student_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Identificador de estudiante no válido")

    student = _find_student_by_key(db, student_key)

    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Estudiante no encontrado")

    psicologo = _resolve_psicologo(current_user, db)
    _assert_student_in_scope(psicologo, student.id_estudiante, db)

    interventions = (
        db.query(Intervencion)
        .filter(Intervencion.id_estudiante == student.id_estudiante)
        .order_by(Intervencion.fecha_intervencion.desc())
        .all()
    )
    return [
        IntervencionResponse(
            id_intervencion=i.id_intervencion,
            id_estudiante=i.id_estudiante,
            id_psicologo=i.id_psicologo,
            tipo_intervencion=i.tipo_intervencion,
            descripcion=i.descripcion,
            fecha_intervencion=i.fecha_intervencion,
            created_at=i.created_at,
            updated_at=i.updated_at,
            es_propia=psicologo is not None and i.id_psicologo == psicologo.id_psicologo,
        )
        for i in interventions
    ]


@router.post("/student-history/{anon_student_id}/interventions", response_model=IntervencionResponse, status_code=201)
def create_student_intervention(
    anon_student_id: str,
    body: IntervencionCreate,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db)
):
    """
    Registra una nueva intervención realizada para un estudiante (HU0027 Scenario 1 & 3).
    """
    # Enforce mandatory fields check
    if not body.tipo_intervencion or not body.tipo_intervencion.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El tipo de intervención es obligatorio")
    if not body.descripcion or not body.descripcion.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La descripción de la intervención es obligatoria")
    if not body.fecha_intervencion:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La fecha de la intervención es obligatoria")

    student_key = anon_student_id.lstrip("#").strip().lower()
    if not student_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Identificador de estudiante no válido")

    student = _find_student_by_key(db, student_key)

    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Estudiante no encontrado")

    psicologo = _resolve_psicologo(current_user, db)
    _assert_student_in_scope(psicologo, student.id_estudiante, db)

    if psicologo is None:
        # id_psicologo es NOT NULL en el modelo: incluso un admin necesita un
        # registro Psicologo propio para quedar como autor de la intervención.
        try:
            psicologo = db.query(Psicologo).filter(Psicologo.id_usuario == UUID(current_user["id"])).first()
        except Exception:
            psicologo = None
        if psicologo is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Se requiere un perfil de psicólogo para registrar intervenciones.",
            )

    new_intervention = Intervencion(
        id_estudiante=student.id_estudiante,
        id_psicologo=psicologo.id_psicologo,
        tipo_intervencion=body.tipo_intervencion,
        descripcion=body.descripcion,
        fecha_intervencion=body.fecha_intervencion,
    )
    db.add(new_intervention)
    db.commit()
    db.refresh(new_intervention)

    return new_intervention


@router.put("/interventions/{intervention_id}", response_model=IntervencionResponse)
def update_clinical_intervention(
    intervention_id: UUID,
    body: IntervencionUpdate,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db)
):
    """
    Actualiza la información de una intervención clínica previamente registrada (HU0027 Scenario 2 & 3).
    """
    intervention = db.query(Intervencion).filter(Intervencion.id_intervencion == intervention_id).first()
    if intervention is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Intervención no encontrada")

    # T-007: solo el psicólogo autor de la intervención (o un admin) puede editarla.
    psicologo = _resolve_psicologo(current_user, db)
    if psicologo is not None and intervention.id_psicologo != psicologo.id_psicologo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el psicólogo que registró esta intervención puede editarla.",
        )

    if body.tipo_intervencion is not None:
        if not body.tipo_intervencion.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El tipo de intervención no puede estar vacío")
        intervention.tipo_intervencion = body.tipo_intervencion

    if body.descripcion is not None:
        if not body.descripcion.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La descripción no puede estar vacía")
        intervention.descripcion = body.descripcion

    if body.fecha_intervencion is not None:
        intervention.fecha_intervencion = body.fecha_intervencion

    db.add(intervention)
    db.commit()
    db.refresh(intervention)

    return intervention


# --- Clinical Notification Routes (HU0029) ---

@router.get("/notifications", response_model=list[NotificacionResponse])
def get_notifications(
    solo_no_revisadas: bool = Query(False, description="Si es True, filtra solo notificaciones no revisadas"),
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db)
):
    """
    Devuelve las notificaciones del psicólogo autenticado (HU0029 CA1 & CA2).
    Incluye la información del estudiante relevante para visualización de detalle.
    """
    user_id = current_user.get("id")
    psicologo = None
    if user_id:
        try:
            psicologo = db.query(Psicologo).filter(Psicologo.id_usuario == UUID(user_id)).first()
        except Exception:
            pass

    if current_user.get("role") == "psicologo" and psicologo is None:
        return []

    # Escalamiento de casos sin contactar a tiempo (como máximo una vez por minuto).
    escalate_overdue_alerts(db)

    # Admins see all notifications; psicólogos only see their own
    query = db.query(Notificacion)
    if psicologo:
        query = query.filter(Notificacion.id_psicologo == psicologo.id_psicologo)

    if solo_no_revisadas:
        query = query.filter(Notificacion.revisada == False)

    # Una sola consulta: notificación -> resultado -> evaluación -> estudiante + puntaje
    # (antes eran hasta 4 consultas por notificación).
    scores = phq9_scores_subquery(db)
    rows = (
        query.outerjoin(Resultado, Resultado.id_resultado == Notificacion.id_resultado)
        .outerjoin(Evaluacion, Evaluacion.id_evaluacion == Resultado.id_evaluacion)
        .outerjoin(Estudiante, Estudiante.id_estudiante == Evaluacion.id_estudiante)
        .outerjoin(scores, scores.c.id_evaluacion == Evaluacion.id_evaluacion)
        .add_columns(
            Evaluacion.id_evaluacion,
            Estudiante.id_estudiante,
            Estudiante.carrera,
            Estudiante.universidad,
            scores.c.puntaje,
        )
        .order_by(Notificacion.created_at.desc())
        .limit(50)
        .all()
    )

    result = []
    for n, id_evaluacion, id_estudiante, carrera, universidad, puntaje_sum in rows:
        id_anonimo = f"#{id_estudiante.hex[:6].upper()}" if id_estudiante else None
        puntaje = int(puntaje_sum or 0) if id_evaluacion else None

        result.append(
            NotificacionResponse(
                id_notificacion=n.id_notificacion,
                id_psicologo=n.id_psicologo,
                id_resultado=n.id_resultado,
                titulo=n.titulo,
                mensaje=n.mensaje,
                nivel_riesgo=n.nivel_riesgo,
                alerta_suicidio=n.alerta_suicidio,
                leida=n.leida,
                revisada=n.revisada,
                fecha_revision=n.fecha_revision,
                created_at=n.created_at,
                id_anonimo=id_anonimo,
                carrera=carrera,
                universidad=universidad,
                puntaje=puntaje,
            )
        )
    return result


@router.put("/notifications/{notification_id}/mark-revisada", response_model=NotificacionResponse)
def mark_notification_revisada(
    notification_id: UUID,
    body: NotificacionMarkRevisada,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db)
):
    """
    Marca una notificación como revisada y actualiza su estado (HU0029 CA3).
    """
    notif = db.query(Notificacion).filter(Notificacion.id_notificacion == notification_id).first()
    if notif is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notificación no encontrada")
    psicologo = _resolve_psicologo(current_user, db)
    if is_psicologo_without_profile(current_user, psicologo) or (
        psicologo is not None and notif.id_psicologo != psicologo.id_psicologo
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Esta notificación no es tuya.")

    notif.revisada = body.revisada
    notif.leida = True
    if body.revisada and notif.fecha_revision is None:
        notif.fecha_revision = datetime.utcnow()

    db.add(notif)
    db.commit()
    db.refresh(notif)

    # Enrich response
    id_anonimo = None
    carrera = None
    universidad = None
    puntaje = None
    try:
        resultado = notif.resultado
        if resultado:
            eval_row = db.query(Evaluacion).filter(
                Evaluacion.id_evaluacion == resultado.id_evaluacion
            ).first()
            if eval_row:
                student = db.query(Estudiante).filter(
                    Estudiante.id_estudiante == eval_row.id_estudiante
                ).first()
                if student:
                    id_anonimo = f"#{student.id_estudiante.hex[:6].upper()}"
                    carrera = student.carrera
                    universidad = student.universidad
                puntaje = int(db.query(func.sum(Respuesta.valor)).filter(
                    Respuesta.id_evaluacion == eval_row.id_evaluacion
                ).scalar() or 0)
    except Exception:
        pass

    return NotificacionResponse(
        id_notificacion=notif.id_notificacion,
        id_psicologo=notif.id_psicologo,
        id_resultado=notif.id_resultado,
        titulo=notif.titulo,
        mensaje=notif.mensaje,
        nivel_riesgo=notif.nivel_riesgo,
        alerta_suicidio=notif.alerta_suicidio,
        leida=notif.leida,
        revisada=notif.revisada,
        fecha_revision=notif.fecha_revision,
        created_at=notif.created_at,
        id_anonimo=id_anonimo,
        carrera=carrera,
        universidad=universidad,
        puntaje=puntaje,
    )


# --- Chatbot Performance Monitoring (HU0016) ---

@router.get("/chatbot/metrics", response_model=ChatbotMetricsResponse)
def get_chatbot_metrics(
    limit_incidencias: int = Query(50, ge=1, le=500),
    current_user: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """
    Indicadores clave de desempeño del chatbot (HU0016 CA1/CA2/CA3):
    total de interacciones registradas, tasa de resolución (% de consultas
    que tuvieron match con una respuesta configurada) y lista de las
    interacciones no resueltas (incidencias/fallback) más recientes.
    """
    total = db.query(func.count(ChatbotInteraccion.id_interaccion)).scalar() or 0
    resueltas = db.query(func.count(ChatbotInteraccion.id_interaccion)).filter(
        ChatbotInteraccion.resuelta.is_(True)
    ).scalar() or 0
    no_resueltas = total - resueltas
    tasa = (resueltas / total * 100.0) if total > 0 else 0.0

    incidencias = (
        db.query(ChatbotInteraccion)
        .filter(ChatbotInteraccion.resuelta.is_(False))
        .order_by(ChatbotInteraccion.created_at.desc())
        .limit(limit_incidencias)
        .all()
    )

    return ChatbotMetricsResponse(
        total_interacciones=total,
        resueltas=resueltas,
        no_resueltas=no_resueltas,
        tasa_resolucion=round(tasa, 2),
        incidencias=incidencias,
    )


@router.get("/chatbot/interactions", response_model=List[ChatbotInteractionResponse])
def list_chatbot_interactions(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    resuelta: bool | None = Query(None, description="Filtra por resuelta/no resuelta"),
    current_user: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """
    Lista las interacciones del chatbot registradas, para exportación/reporte
    de desempeño (HU0016 CA4).
    """
    query = db.query(ChatbotInteraccion)
    if resuelta is not None:
        query = query.filter(ChatbotInteraccion.resuelta.is_(resuelta))
    interactions = (
        query.order_by(ChatbotInteraccion.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return interactions


# --- System Audit Log (HU0043 CA3) ---

@router.get("/audit", response_model=AuditLogListResponse)
def get_audit_log(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    id_usuario: UUID | None = Query(None, description="Filtra por usuario"),
    accion: str | None = Query(None, description="Filtra por tipo de acción (coincidencia exacta o parcial)"),
    tabla_objetivo: str | None = Query(None, description="Filtra por tabla afectada"),
    fecha_desde: datetime | None = Query(None, description="Filtra eventos desde esta fecha (inclusive)"),
    fecha_hasta: datetime | None = Query(None, description="Filtra eventos hasta esta fecha (inclusive)"),
    current_user: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """
    Consulta el historial de auditoría de accesos y cambios del sistema
    (HU0043 CA3), con filtros opcionales y paginación.
    """
    query = db.query(AuditoriaAcceso)

    if id_usuario is not None:
        query = query.filter(AuditoriaAcceso.id_usuario == id_usuario)
    if accion:
        query = query.filter(AuditoriaAcceso.accion.ilike(f"%{accion}%"))
    if tabla_objetivo:
        query = query.filter(AuditoriaAcceso.tabla_objetivo.ilike(f"%{tabla_objetivo}%"))
    if fecha_desde is not None:
        query = query.filter(AuditoriaAcceso.fecha_evento >= fecha_desde)
    if fecha_hasta is not None:
        query = query.filter(AuditoriaAcceso.fecha_evento <= fecha_hasta)

    total = query.count()
    rows = (
        query.outerjoin(Usuario, Usuario.id_usuario == AuditoriaAcceso.id_usuario)
        .add_entity(Usuario)
        .order_by(AuditoriaAcceso.fecha_evento.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items: List[AuditLogResponse] = []
    for row, usuario in rows:
        items.append(
            AuditLogResponse(
                id_auditoria=row.id_auditoria,
                id_usuario=row.id_usuario,
                accion=row.accion,
                tabla_objetivo=row.tabla_objetivo,
                id_objetivo=row.id_objetivo,
                fecha_evento=row.fecha_evento,
                ip_origen=row.ip_origen,
                detalle=row.detalle,
                usuario_email=usuario.correo if usuario else None,
                usuario_nombre=getattr(usuario, "nombre", None) if usuario else None,
            )
        )

    return AuditLogListResponse(total=total, items=items)
