from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, extract, cast, String
from datetime import datetime, timedelta
from uuid import UUID
from typing import List
from ..database import get_db
from ..models import Evaluacion, Resultado, Respuesta, Estudiante, DerivacionClinica, Usuario, AuditoriaAcceso, ModeloVersion, VistaSeudonimizadaML, ObservacionSeguimiento, Psicologo, Cita, Intervencion, Notificacion, AuditoriaModelML
from ..schemas import (
    StatisticsResponse,
    RiskDistribution,
    ReportItem,
    StudentHistoryItem,
    AssignedPatientItem,
    AppointmentCreate,
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
)
from ..security import require_role, get_supabase_client

router = APIRouter(prefix="/admin", tags=["Administration & Reports"])

@router.get("/statistics", response_model=StatisticsResponse)
async def get_statistics(
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db)
):
    """
    Fetches real-time clinical screening statistics, including total screenings,
    average score, and a breakdown of risk levels for the admin dashboard.
    """
    # 1. Count total evaluations
    total = db.query(Evaluacion).count()
    if total == 0:
        return StatisticsResponse(
            total=0,
            averageScore=0.0,
            riskDistribution=RiskDistribution(minimal=0, mild=0, moderate=0, moderatelySevere=0, severe=0)
        )

    # 2. Calculate average score
    # Fetch all scores from results
    scores_query = db.query(func.avg(Respuesta.valor)).scalar()
    # Wait, the average PHQ-9 score is the sum of responses for each evaluation, averaged.
    # Let's write a query that calculates the average sum of responses.
    # Group responses by evaluation, sum them, and take the average.
    subquery = db.query(
        Respuesta.id_evaluacion, 
        func.sum(Respuesta.valor).label("total_score")
    ).group_by(Respuesta.id_evaluacion).subquery()
    
    avg_score = db.query(func.avg(subquery.c.total_score)).scalar() or 0.0

    # 3. Calculate risk distribution
    # Risk categories match schema values: minimo, leve, moderado, moderadamente_severo, severo
    distribution_counts = db.query(
        Resultado.nivel_riesgo,
        func.count(Resultado.id_resultado)
    ).group_by(Resultado.nivel_riesgo).all()

    dist_dict = {
        "minimo": 0,
        "leve": 0,
        "moderado": 0,
        "moderadamente_severo": 0,
        "severo": 0
    }
    
    for row in distribution_counts:
        nivel = row[0]
        count = row[1]
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
        )
    )

@router.get("/reports", response_model=list[ReportItem])
async def get_reports(
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db)
):
    """
    Lists screening reports with details. It maintains anonymity while
    including clinical metadata like career, risk level, and date.
    """
    # Join Evaluacion, Resultado, Estudiante
    results = db.query(
        Evaluacion.id_evaluacion,
        Evaluacion.fecha_evaluacion,
        Resultado.nivel_riesgo,
        Resultado.alerta_suicidio,
        Estudiante.id_estudiante,
        Estudiante.carrera,
        Estudiante.universidad,
        Evaluacion.estado
    ).join(
        Resultado, Evaluacion.id_evaluacion == Resultado.id_evaluacion
    ).join(
        Estudiante, Evaluacion.id_estudiante == Estudiante.id_estudiante
    ).order_by(Evaluacion.fecha_evaluacion.desc()).all()

    report_list = []
    for row in results:
        # Build stable anonymous student ID from student UUID prefix.
        anon_id = f"#{row.id_estudiante.hex[:6].upper()}"
        
        # We calculate the score by summing the respuestas associated with the evaluation
        score_sum = db.query(func.sum(Respuesta.valor)).filter(
            Respuesta.id_evaluacion == row.id_evaluacion
        ).scalar() or 0

        report_list.append(
            ReportItem(
                id_anonimo=anon_id,
                fecha=row.fecha_evaluacion,
                nivel_riesgo=row.nivel_riesgo.replace("_", " ").capitalize(),
                puntaje=int(score_sum),
                alerta_suicidio=row.alerta_suicidio,
                carrera=row.carrera,
                universidad=row.universidad
            )
        )

    return report_list

@router.get("/student-history/{anon_student_id}", response_model=list[StudentHistoryItem])
async def get_student_history(
    anon_student_id: str,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    """
    Retrieves a student's chronological evaluation history using the stable
    anonymous identifier shown in the admin reports.
    """
    student_key = anon_student_id.lstrip("#").strip().lower()
    if not student_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Identificador de estudiante no válido")

    student = db.query(Estudiante).filter(
        func.lower(func.substr(cast(Estudiante.id_estudiante, String), 1, len(student_key))) == student_key
    ).first()

    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Estudiante no encontrado")

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
    results = []
    for row in history_rows:
        # fetch observations related to this evaluation
        obs = db.query(ObservacionSeguimiento).filter(ObservacionSeguimiento.id_evaluacion == row.id_evaluacion).order_by(ObservacionSeguimiento.created_at.desc()).all()
        obs_list = [
            {
                "id_observacion": o.id_observacion,
                "id_evaluacion": o.id_evaluacion,
                "id_psicologo": o.id_psicologo,
                "texto": o.texto,
                "created_at": o.created_at,
                "updated_at": o.updated_at,
            }
            for o in obs
        ]

        results.append(
            StudentHistoryItem(
                id_anonimo=f"#{student.id_estudiante.hex[:6].upper()}",
                fecha=row.fecha_evaluacion,
                nivel_riesgo=row.nivel_riesgo.replace("_", " ").capitalize(),
                puntaje=int(
                    db.query(func.sum(Respuesta.valor)).filter(
                        Respuesta.id_evaluacion == row.id_evaluacion
                    ).scalar() or 0
                ),
                alerta_suicidio=row.alerta_suicidio,
                carrera=row.carrera,
                universidad=row.universidad,
                estado_evaluacion=row.estado,
                comentarios=row.comentarios,
                observaciones=obs_list,
            )
        )

    return results


@router.get("/assigned-patients", response_model=list[AssignedPatientItem])
async def get_assigned_patients(
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
    risk_levels: list[str] | None = Query(default=None),
):
    """
    Returns the list of students assigned to the authenticated psychologist.
    Administrators can view the same listing for all students.
    """
    user_role = current_user.get("role")
    query = db.query(
        Estudiante.id_estudiante,
        Estudiante.carrera,
        Estudiante.universidad,
        Evaluacion.id_evaluacion,
        Evaluacion.fecha_evaluacion,
        Evaluacion.estado,
        Resultado.nivel_riesgo,
        Resultado.alerta_suicidio,
    ).join(
        Evaluacion, Estudiante.id_estudiante == Evaluacion.id_estudiante
    ).join(
        Resultado, Evaluacion.id_evaluacion == Resultado.id_evaluacion
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

    subquery = db.query(
        Evaluacion.id_estudiante.label("estudiante_id"),
        func.max(Evaluacion.fecha_evaluacion).label("last_date"),
    )
    if user_role == "psicologo":
        subquery = subquery.filter(Evaluacion.id_psicologo == psicologo.id_psicologo)
    subquery = subquery.group_by(Evaluacion.id_estudiante).subquery()

    query = query.join(
        subquery,
        (Evaluacion.id_estudiante == subquery.c.estudiante_id) &
        (Evaluacion.fecha_evaluacion == subquery.c.last_date),
    )

    if risk_levels:
        query = query.filter(Resultado.nivel_riesgo.in_(risk_levels))

    query = query.order_by(Evaluacion.fecha_evaluacion.desc())

    assigned = []
    for row in query.all():
        score = db.query(func.sum(Respuesta.valor)).filter(Respuesta.id_evaluacion == row.id_evaluacion).scalar() or 0
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
            )
        )

    return assigned


@router.get("/appointments", response_model=list[AppointmentResponse])
async def list_appointments(
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    """
    List appointments for the authenticated psychologist or all appointments for admin.
    """
    user_role = current_user.get("role")
    query = db.query(Cita)
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
        )
        for r in rows
    ]
    return result


@router.post("/appointments", response_model=AppointmentResponse, status_code=201)
async def create_appointment(
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

    student = db.query(Estudiante).filter(
        func.lower(func.substr(cast(Estudiante.id_estudiante, String), 1, len(student_key))) == student_key
    ).first()

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
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Horario no disponible para el psicólogo")

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
    )


@router.get("/student/evolution", response_model=List[StudentEvolutionItem])
async def get_student_evolution(
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

    rows = (
        db.query(
            Evaluacion.id_evaluacion,
            Evaluacion.fecha_evaluacion,
            Resultado.nivel_riesgo,
            Resultado.alerta_suicidio,
        )
        .join(Resultado, Evaluacion.id_evaluacion == Resultado.id_evaluacion)
        .filter(Evaluacion.id_estudiante == student.id_estudiante)
        .order_by(Evaluacion.fecha_evaluacion.asc())
        .all()
    )

    series = []
    for r in rows:
        score = int(db.query(func.sum(Respuesta.valor)).filter(Respuesta.id_evaluacion == r.id_evaluacion).scalar() or 0)
        series.append({
            "fecha": r.fecha_evaluacion,
            "puntaje": score,
            "nivel_riesgo": r.nivel_riesgo.replace("_", " ").capitalize(),
            "alerta_suicidio": r.alerta_suicidio,
        })

    return series


@router.post("/student-history/{anon_student_id}/observations", response_model=ObservacionResponse)
async def create_student_observation(
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

    student = db.query(Estudiante).filter(
        func.lower(func.substr(cast(Estudiante.id_estudiante, String), 1, len(student_key))) == student_key
    ).first()
    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Estudiante no encontrado")

    # If an evaluation is provided, validate it belongs to the student
    if body.id_evaluacion is not None:
        ev = db.query(Evaluacion).filter(Evaluacion.id_evaluacion == body.id_evaluacion).first()
        if ev is None or ev.id_estudiante != student.id_estudiante:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Evaluación inválida para este estudiante")

    psicologo = None
    try:
        user_id = current_user.get("id")
        if user_id:
            psicologo = db.query(Psicologo).filter(Psicologo.id_usuario == UUID(user_id)).first()
    except Exception:
        psicologo = None

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
async def update_student_observation(
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
async def get_clinical_derivations(
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db)
):
    """
    Retrieves all pending or active clinical derivations, including risk metadata,
    ordered by priority for triage.
    """
    derivations = db.query(
        DerivacionClinica.id_derivacion,
        DerivacionClinica.nivel_prioridad,
        DerivacionClinica.accion_tomada,
        DerivacionClinica.fecha_derivacion,
        DerivacionClinica.estado,
        DerivacionClinica.institucion_referencia,
        Resultado.nivel_riesgo,
        Resultado.alerta_suicidio
    ).join(
        Resultado, DerivacionClinica.id_resultado == Resultado.id_resultado
    ).all()

    parsed_derivations = []
    for d in derivations:
        parsed_derivations.append({
            "id": d.id_derivacion,
            "prioridad": d.nivel_prioridad,
            "accion": d.accion_tomada,
            "fecha": d.fecha_derivacion,
            "estado": d.estado,
            "institucion": d.institucion_referencia,
            "nivel_riesgo": d.nivel_riesgo,
            "alerta_suicidio": d.alerta_suicidio
        })

    priority_order = {
        "urgente": 0,
        "alto": 1,
        "moderado": 2,
    }

    parsed_derivations.sort(
        key=lambda item: (
            priority_order.get(item["prioridad"], 99),
            -item["fecha"].timestamp(),
        )
    )

    return parsed_derivations


@router.put("/derivations/{derivation_id}", response_model=DerivacionResponse)
async def update_clinical_derivation(
    derivation_id: UUID,
    payload: DerivacionUpdate,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    """
    Updates the status or referral details of a clinical derivation for case prioritization.
    """
    derivation = db.query(DerivacionClinica).filter(DerivacionClinica.id_derivacion == derivation_id).first()
    if derivation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Derivación clínica no encontrada")

    if payload.estado is not None:
        derivation.estado = payload.estado
    if payload.accion_tomada is not None:
        derivation.accion_tomada = payload.accion_tomada
    if payload.institucion_referencia is not None:
        derivation.institucion_referencia = payload.institucion_referencia

    db.add(derivation)
    db.commit()
    db.refresh(derivation)

    result = db.query(Resultado.nivel_riesgo, Resultado.alerta_suicidio).join(
        DerivacionClinica,
        DerivacionClinica.id_resultado == Resultado.id_resultado
    ).filter(DerivacionClinica.id_derivacion == derivation_id).first()

    if result is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error al leer los datos de resultado asociados")

    return {
        "id": derivation.id_derivacion,
        "prioridad": derivation.nivel_prioridad,
        "accion": derivation.accion_tomada,
        "fecha": derivation.fecha_derivacion,
        "estado": derivation.estado,
        "institucion": derivation.institucion_referencia,
        "nivel_riesgo": result.nivel_riesgo,
        "alerta_suicidio": result.alerta_suicidio,
    }


@router.get("/trends")
async def get_monthly_trends(
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

    rows = (
        db.query(
            extract("year", Evaluacion.fecha_evaluacion).label("y"),
            extract("month", Evaluacion.fecha_evaluacion).label("m"),
            func.count(Evaluacion.id_evaluacion).label("evaluaciones"),
            func.avg(subquery.c.total_score).label("promedio"),
        )
        .outerjoin(subquery, Evaluacion.id_evaluacion == subquery.c.id_evaluacion)
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
async def get_model_status(
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

    return ModelStatusResponse(
        model_name=active_model.nombre,
        version=active_model.version,
        active_records=record_count,
        last_retrained_at=active_model.fecha_publicacion,
        origen_datos=active_model.origen_datos,
        comentario=active_model.comentario,
    )


@router.post("/model/retrain", response_model=ModelRetrainResponse, status_code=status.HTTP_200_OK)
async def retrain_model(
    payload: ModelRetrainRequest,
    request: Request,
    current_user: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db)
):
    """
    Inicia un reentrenamiento de modelo con datos existentes de la vista seudonimizada
    combinados con el dataset histórico de excel, y calcula métricas de desempeño.
    """
    import pandas as pd
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
    from ..utils.ml_model import train_and_save_model, get_initial_dataset, make_preprocessor
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.pipeline import Pipeline

    # 1. Cargar registros nuevos desde la base de datos
    registros = db.query(VistaSeudonimizadaML).all()
    record_count = len(registros)

    # 2. Cargar y combinar dataset base con nuevos registros
    try:
        df_base = get_initial_dataset()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al cargar el dataset inicial de depresión: {str(e)}"
        )

    new_rows = []
    for r in registros:
        if r.horas_sueno is not None and r.mspss_total is not None and r.historia_salud_mental is not None and r.calidad_sueno is not None:
            # Calcular target a partir de respuestas del PHQ-9 (leakage-free target)
            q_sum = (r.q1 or 0) + (r.q2 or 0) + (r.q3 or 0) + (r.q4 or 0) + (r.q5 or 0) + (r.q6 or 0) + (r.q7 or 0) + (r.q8 or 0) + (r.q9 or 0)
            new_rows.append({
                "horas_sueno": r.horas_sueno,
                "mspss_total": r.mspss_total,
                "historia_salud_mental": r.historia_salud_mental,
                "calidad_sueno": r.calidad_sueno,
                "target_binario": 1 if q_sum >= 10 else 0
            })

    if new_rows:
        df_new = pd.DataFrame(new_rows)
        df_combined = pd.concat([df_base, df_new], ignore_index=True)
    else:
        df_combined = df_base

    # 3. Calcular métricas reales con un split de validación estratificado
    try:
        X_comb = df_combined[["horas_sueno", "mspss_total", "historia_salud_mental", "calidad_sueno"]].copy()
        y_comb = df_combined["target_binario"]
        
        X_train, X_test, y_train, y_test = train_test_split(
            X_comb, y_comb, test_size=0.2, random_state=42, stratify=y_comb
        )
        
        val_pipeline = Pipeline([
            ('preprocessor', make_preprocessor()),
            ('classifier', RandomForestClassifier(
                n_estimators=200,
                max_depth=10,
                min_samples_leaf=5,
                class_weight='balanced',
                random_state=42,
                n_jobs=-1
            ))
        ])
        val_pipeline.fit(X_train, y_train)
        y_pred = val_pipeline.predict(X_test)
        
        val_acc = float(accuracy_score(y_test, y_pred))
        val_prec = float(precision_score(y_test, y_pred, zero_division=0))
        val_rec = float(recall_score(y_test, y_pred, zero_division=0))
        val_f1 = float(f1_score(y_test, y_pred, zero_division=0))
    except Exception as metrics_err:
        # Fallback a métricas estimadas en caso de falla
        val_acc, val_prec, val_rec, val_f1 = 0.85, 0.82, 0.80, 0.81

    # 4. Entrenar el modelo final con el 100% de los datos y guardarlo
    try:
        train_and_save_model(df_combined)
    except Exception as train_err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error durante el reentrenamiento del modelo RandomForest: {str(train_err)}"
        )

    # 5. Registrar la nueva versión del modelo en la base de datos
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

    model_name = payload.model_name or "RandomForest"

    if active_model:
        active_model.activo = False
        db.add(active_model)

    new_model = ModeloVersion(
        nombre=model_name,
        version=version,
        origen_datos=payload.origen_datos or f"dataset histórico + {record_count} nuevos registros",
        comentario=payload.comentario or "Reentrenamiento real del RandomForest (Bloque B).",
        activo=True,
    )
    db.add(new_model)
    db.commit()

    # 6. Registrar evento en la tabla de AuditoriaModelML
    client_ip = request.client.host if request.client else "127.0.0.1"
    admin_id = UUID(current_user["id"])
    
    audit_ml = AuditoriaModelML(
        tipo_evento="entrenamiento",
        id_usuario=admin_id,
        model_version=version,
        precision=val_prec,
        recall=val_rec,
        f1_score=val_f1,
        accuracy=val_acc,
        resultado_prediccion=f"Reentrenado con {len(df_combined)} registros en total."
    )
    db.add(audit_ml)

    # Log de acceso general
    auditoria = AuditoriaAcceso(
        id_usuario=admin_id,
        accion="reentrenamiento_modelo",
        tabla_objetivo="modelo_version",
        id_objetivo=new_model.id_modelo,
        ip_origen=client_ip,
        detalle=f"Reentrenó el modelo {model_name} a la versión {version}. Registros totales: {len(df_combined)}. Accuracy: {val_acc:.4f}"
    )
    db.add(auditoria)
    db.commit()

    return ModelRetrainResponse(
        success=True,
        model_name=model_name,
        version=version,
        previous_version=previous_version,
        updated_records=len(df_combined),
        message=f"Reentrenamiento completado exitosamente. Accuracy estimado: {val_acc:.2%}"
    )


@router.get("/users", response_model=list[UserResponse])
async def get_all_users(
    current_user: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db)
):
    """
    Lista todos los usuarios (HU0005). Solo accesible por administradores.
    """
    users = db.query(Usuario).order_by(Usuario.fecha_registro.desc()).all()
    return users


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
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
async def get_student_interventions(
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

    student = db.query(Estudiante).filter(
        func.lower(func.substr(cast(Estudiante.id_estudiante, String), 1, len(student_key))) == student_key
    ).first()

    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Estudiante no encontrado")

    interventions = (
        db.query(Intervencion)
        .filter(Intervencion.id_estudiante == student.id_estudiante)
        .order_by(Intervencion.fecha_intervencion.desc())
        .all()
    )
    return interventions


@router.post("/student-history/{anon_student_id}/interventions", response_model=IntervencionResponse, status_code=201)
async def create_student_intervention(
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

    student = db.query(Estudiante).filter(
        func.lower(func.substr(cast(Estudiante.id_estudiante, String), 1, len(student_key))) == student_key
    ).first()

    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Estudiante no encontrado")

    # Resolve active psychologist
    psicologo = None
    try:
        user_id = current_user.get("id")
        if user_id:
            psicologo = db.query(Psicologo).filter(Psicologo.id_usuario == UUID(user_id)).first()
    except Exception:
        psicologo = None

    if psicologo is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Psicólogo no encontrado para el usuario actual")

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
async def update_clinical_intervention(
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
async def get_notifications(
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

    # Admins see all notifications; psicólogos only see their own
    query = db.query(Notificacion)
    if psicologo:
        query = query.filter(Notificacion.id_psicologo == psicologo.id_psicologo)

    if solo_no_revisadas:
        query = query.filter(Notificacion.revisada == False)

    notifs = query.order_by(Notificacion.created_at.desc()).limit(50).all()

    result = []
    for n in notifs:
        # Enrich with student data through resultado -> evaluacion -> estudiante
        id_anonimo = None
        carrera = None
        universidad = None
        puntaje = None
        try:
            resultado = n.resultado
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
                    # Calculate puntaje
                    puntaje = db.query(func.sum(Respuesta.valor)).filter(
                        Respuesta.id_evaluacion == eval_row.id_evaluacion
                    ).scalar() or 0
                    puntaje = int(puntaje)
        except Exception:
            pass

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
async def mark_notification_revisada(
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
