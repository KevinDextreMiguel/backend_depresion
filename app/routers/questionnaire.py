import threading
import time
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import func
from ..database import get_db
from ..models import (
    Usuario, Estudiante, Psicologo, Consentimiento, Cuestionario, 
    Pregunta, Evaluacion, Respuesta, Resultado, VistaSeudonimizadaML, DerivacionClinica, ProgresoCuestionario, ModeloVersion, AuditoriaModelML
)
from ..schemas import QuestionnaireSubmit, SubmitSuccessResponse, ProgresoCreate, ProgresoUpdate, ProgresoResponse, ProgresoDeleteRequest, CuestionarioCompletoSubmit
from ..security import db_encrypt, db_decrypt, get_current_user_optional
from typing import Optional
import uuid
import random
import hashlib
from datetime import datetime

router = APIRouter(prefix="/questionnaire", tags=["Questionnaire"])

# Direct compatibility endpoint matching the exact payload from front-end
class SimpleSubmitPayload(dict):
    pass

@router.post("/submit-questionnaire", status_code=status.HTTP_201_CREATED)
def submit_questionnaire_simple(
    payload: CuestionarioCompletoSubmit,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Recibe el cuestionario completo del estudiante (demografía, hábitos, MSPSS y PHQ-9).
    Calcula scores clínicos, ejecuta la inferencia en tiempo real con la Logistic Regression
    del paper (sueño + calidad de sueño + MSPSS 1-7) y registra los resultados en la base de datos de manera atómica.
    """
    if not payload.consentimiento_aceptado:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Se requiere la aceptación del consentimiento informado para continuar."
        )

    if not all(1 <= v <= 7 for v in payload.mspss_respuestas):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Las respuestas MSPSS deben estar en la escala 1 a 7."
        )

    # 1-2. Cuestionario PHQ-9 y sus 9 preguntas. Se verifican (y crean si faltan)
    # una sola vez por proceso; luego se usan los ids en memoria.
    cuestionario_id, pregunta_ids = _ensure_phq9_questionnaire(db)

    # 3. Crear perfil de estudiante con datos ingresados
    # Priority: 1) Bearer token (authenticated user), 2) test_user_id from payload
    test_user_id = payload.test_user_id
    auth_user_id = None
    
    # Usuario autenticado a partir del Bearer token. Mismo orden que
    # security.get_current_user: JWT local (sin red) y, si no, Supabase Auth con
    # caché de 60 s (antes cada envío hacía una llamada HTTP a Supabase).
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            import jwt as pyjwt
            from ..config import settings
            decoded = pyjwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"], audience="authenticated")
            auth_user_id = decoded.get("sub")
        except Exception:
            auth_user_id = None

        if not auth_user_id:
            try:
                from ..security import get_supabase_client, _cache_get_user, _cache_put_user, _verify_with_supabase
                cached = _cache_get_user(token)
                if cached is not None:
                    auth_user_id = cached.get("id")
                else:
                    supabase = get_supabase_client()
                    if supabase:
                        user = _verify_with_supabase(supabase, token)
                        if user is not None:
                            _cache_put_user(token, user)
                            auth_user_id = user.get("id")
            except Exception as sup_err:
                print(f"[MindCheck] Supabase auth failed: {sup_err}", flush=True)
    
    try:
        if auth_user_id:
            # Use authenticated user's ID — already exists in auth.users and usuario
            anon_user_id = uuid.UUID(auth_user_id)
        elif test_user_id:
            anon_user_id = uuid.UUID(test_user_id)
        else:
            # No valid authentication — require login
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Se requiere iniciar sesión para completar el cuestionario. Por favor, inicia sesión o regístrate."
            )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticación inválido. Por favor, inicia sesión nuevamente."
        )
    
    client_ip = request.client.host if request.client else "127.0.0.1"
    
    try:
        # Reutilizar o crear Usuario (debe existir en auth.users para cumplir el FK)
        anon_user = db.query(Usuario).filter(Usuario.id_usuario == anon_user_id).first()
        if not anon_user:
            # Only create if user exists in auth.users (authenticated users always do)
            anon_user = Usuario(
                id_usuario=anon_user_id,
                nombre=db_encrypt(f"Estudiante Anónimo #{random.randint(1000, 9999)}"),
                correo=db_encrypt(f"anonimo_{anon_user_id.hex[:6]}@upc.edu.pe"),
                rol="estudiante",
                activo=True
            )
            db.add(anon_user)
            db.flush()

        # Reutilizar o crear perfil de Estudiante con los datos ingresados por el usuario
        db_student = db.query(Estudiante).filter(Estudiante.id_usuario == anon_user_id).first()
        if not db_student:
            student_id = uuid.uuid4()
            db_student = Estudiante(
                id_estudiante=student_id,
                id_usuario=anon_user_id,
                edad=payload.edad,
                genero=payload.genero,
                carrera=payload.carrera,
                universidad=payload.universidad
            )
            db.add(db_student)
        else:
            student_id = db_student.id_estudiante
            # Actualizar datos con lo último ingresado
            db_student.edad = payload.edad
            db_student.genero = payload.genero
            db_student.carrera = payload.carrera
            db_student.universidad = payload.universidad
            db.add(db_student)

        # Aceptar consentimiento informado
        existing_consent = db.query(Consentimiento).filter(Consentimiento.id_usuario == anon_user_id).first()
        if not existing_consent:
            consent_id = uuid.uuid4()
            db_consent = Consentimiento(
                id_consentimiento=consent_id,
                id_usuario=anon_user_id,
                version_documento="v1.0-cuestionario-completo",
                fecha_aceptacion=datetime.utcnow(),
                ip_origen=client_ip,
                hash_documento=hashlib.sha256(f"{anon_user_id}-consent".encode()).hexdigest(),
                revocado=False
            )
            db.add(db_consent)

        db.flush()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"[MindCheck] Error creando perfil: {e}", flush=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al crear perfil del estudiante: {str(e)}"
        )

    # 4. Asignar psicólogo activo por defecto.
    # T-008: se reparte por carga (menor cantidad de evaluaciones asignadas)
    # en vez de tomar siempre el primer psicólogo activo encontrado — evita que
    # toda la cartera de "pacientes asignados" recaiga sobre un único psicólogo.
    # Continuidad terapéutica: si el estudiante ya tiene un psicólogo activo
    # (su evaluación más reciente), se mantiene el mismo. Solo los estudiantes
    # nuevos se reparten por carga.
    psicologo = (
        db.query(Psicologo)
        .join(Evaluacion, Evaluacion.id_psicologo == Psicologo.id_psicologo)
        .filter(Evaluacion.id_estudiante == student_id, Psicologo.activo == True)
        .order_by(Evaluacion.fecha_evaluacion.desc())
        .first()
    )
    if psicologo is None:
        psicologo = (
            db.query(Psicologo)
            .filter(Psicologo.activo == True)
            .outerjoin(Evaluacion, Evaluacion.id_psicologo == Psicologo.id_psicologo)
            .group_by(Psicologo.id_psicologo)
            .order_by(func.count(Evaluacion.id_evaluacion).asc())
            .first()
        )
    if not psicologo:
        # No active psicologo — try to create a system one using the student's auth user ID
        # The student's user ID IS valid in auth.users (Supabase) so no FK violation
        try:
            # Look for any existing psicologo (active or inactive)
            psicologo = db.query(Psicologo).first()
            if not psicologo:
                # Create a placeholder psicologo linked to the student's own user record
                # (This user exists in auth.users, satisfying the FK constraint)
                psicologo_fallback = Psicologo(
                    id_psicologo=uuid.uuid4(),
                    id_usuario=anon_user_id,
                    especialidad="Salud Mental General",
                    numero_colegiatura="SISTEMA",
                    activo=True
                )
                db.add(psicologo_fallback)
                db.commit()
                psicologo = psicologo_fallback
        except Exception as psico_err:
            db.rollback()
            print(f"[MindCheck] Error asignando psicólogo: {psico_err}")
            # Last resort: try to query again
            psicologo = db.query(Psicologo).first()
            if not psicologo:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="No hay psicólogos disponibles en el sistema. Por favor contacte al administrador."
                )

    # 5-9. Evaluación, respuestas y resultado: se agregan a la sesión y se
    # confirman en UN solo commit al final (antes eran 5-8 commits secuenciales;
    # cada uno es un viaje de red a la base y dejaba datos a medias si algo fallaba).
    eval_id = uuid.uuid4()
    db_eval = Evaluacion(
        id_evaluacion=eval_id,
        id_estudiante=student_id,
        id_cuestionario=cuestionario_id,
        id_psicologo=psicologo.id_psicologo,
        estado="completada",
        consentimiento_verificado=True
    )
    db.add(db_eval)

    score = sum(payload.phq9_respuestas)
    db.add_all([
        Respuesta(id_respuesta=uuid.uuid4(), id_evaluacion=eval_id, id_pregunta=pregunta_id, valor=ans_val)
        for pregunta_id, ans_val in zip(pregunta_ids, payload.phq9_respuestas)
    ])

    # 7. Calcular score de MSPSS (escala 1-7): total y media de los 12 ítems
    mspss_total = sum(payload.mspss_respuestas)
    mspss_mean = round(mspss_total / 12.0, 4)

    # 8. Inferencia en tiempo real con el modelo del paper (Logistic Regression, NB01)
    ml_fallback = False
    try:
        from ..utils.ml_model import predict_depression_risk
        ml_pred, ml_prob, mspss_mean = predict_depression_risk(
            horas_sueno=payload.horas_sueno,
            calidad_sueno=payload.calidad_sueno,
            mspss_respuestas=payload.mspss_respuestas,
        )
    except Exception as ml_err:
        print(f"[MindCheck] Inferencia fallida, aplicando fallback por reglas: {ml_err}", flush=True)
        ml_fallback = True
        ml_pred = 1 if score >= 10 else 0
        ml_prob = float(round((score / 27.0) * 100, 2))

    # Nivel de riesgo clínico tradicional (PHQ-9 Cut-off)
    if score <= 4:
        risk_level = "minimo"
    elif score <= 9:
        risk_level = "leve"
    elif score <= 14:
        risk_level = "moderado"
    elif score <= 19:
        risk_level = "moderadamente_severo"
    else:
        risk_level = "severo"

    # Alerta de suicidio (PHQ-9 ítem 9 >= 1)
    q9_value = payload.phq9_respuestas[8]
    suicide_alert = q9_value >= 1

    # 9. Resultado clínico e interpretabilidad de ML
    result_id = uuid.uuid4()
    interpretabilidad = {
        "score": score,
        "q9_value": q9_value,
        "risk_factors": ["item_9_alert"] if suicide_alert else [],
        "ml_prediction": "riesgo_depresion" if ml_pred == 1 else "sin_riesgo",
        # Porcentaje 0-100. Si ml_fallback es True, no viene del modelo sino de puntaje/27.
        "ml_probability": ml_prob,
        "ml_fallback": ml_fallback,
        "horas_sueno": payload.horas_sueno,
        "mspss_total": mspss_total,
        "mspss_mean": mspss_mean,
        "calidad_sueno": payload.calidad_sueno,
        "historia_salud_mental": payload.historia_salud_mental
    }
    db.add(Resultado(
        id_resultado=result_id,
        id_evaluacion=eval_id,
        nivel_riesgo=risk_level,
        probabilidad=float(round(ml_prob, 2)),
        interpretabilidad=interpretabilidad,
        alerta_suicidio=suicide_alert
    ))

    # 10. Derivación automática si se requiere (forma parte del registro clínico)
    needs_derivation = suicide_alert or risk_level in ["moderadamente_severo", "severo"]
    deriv_id = uuid.uuid4()
    if needs_derivation:
        db.add(DerivacionClinica(
            id_derivacion=deriv_id,
            id_resultado=result_id,
            id_psicologo=psicologo.id_psicologo,
            nivel_prioridad="urgente" if suicide_alert else "alto",
            accion_tomada=(
                f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC · sistema] Derivación automática por tamizaje: "
                f"PHQ-9 {score}/27 ({risk_level.replace('_', ' ')})"
                + (f", ítem 9 = {q9_value}/3." if suicide_alert else ".")
            ),
            fecha_derivacion=datetime.utcnow(),
            estado="pendiente",
            institucion_referencia="Psicología Universitaria UPC / Servicio Médico Emergencia"
        ))
        db_eval.estado = "derivada"

    try:
        db.flush()  # registro clínico obligatorio; si falla, no se guarda nada
    except Exception as core_err:
        db.rollback()
        print(f"[MindCheck] Error guardando la evaluación: {core_err}", flush=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al registrar la evaluación: {str(core_err)}"
        )

    # 10b-12. Registros complementarios (notificación, auditorías, fila para el
    # modelo) en un punto de guardado: si fallan, no impiden guardar la evaluación.
    model_ver_str, origin_model = _active_model_labels(db)
    try:
        with db.begin_nested():
            if needs_derivation:
                # Notificación al psicólogo asignado (responsable del caso). Si no lo
                # contacta a tiempo, utils.clinical.escalate_overdue_alerts avisa al resto.
                from ..models import Notificacion, AuditoriaAcceso
                db.add(Notificacion(
                    id_psicologo=psicologo.id_psicologo,
                    id_resultado=result_id,
                    titulo="Ítem 9 positivo: contactar hoy" if suicide_alert else "Caso de riesgo alto asignado",
                    mensaje=(
                        (f"Respuesta al ítem 9 (ideación de muerte/autolesión): {q9_value}/3. " if suicide_alert else "")
                        + f"PHQ-9: {score}/27 ({risk_level.replace('_', ' ')}). "
                        + "Abre el caso, contacta al estudiante y registra la acción realizada."
                    ),
                    nivel_riesgo=risk_level,
                    alerta_suicidio=suicide_alert,
                    leida=False,
                    revisada=False,
                ))
                db.add(AuditoriaAcceso(
                    id_usuario=getattr(psicologo, "id_usuario", None),
                    accion="activacion_protocolo_suicida",
                    tabla_objetivo="derivacion_clinica",
                    id_objetivo=deriv_id,
                    ip_origen=client_ip,
                    detalle=f"Protocolo activado automáticamente. Alerta suicidio: {suicide_alert}, score: {score}, ML: {ml_prob}%"
                ))

            # 11. Auditoría de inferencia ML
            db.add(AuditoriaModelML(
                tipo_evento="prediccion",
                id_usuario=None,  # Anónimo
                model_version=model_ver_str,
                id_prediccion=result_id,
                resultado_prediccion="riesgo_depresion" if ml_pred == 1 else "sin_riesgo"
            ))

            # 12. Vista seudonimizada para análisis y reentrenamiento
            salt = "mindcheck-salt-2026"
            student_hash = hashlib.sha256(f"{student_id.hex}-{salt}".encode()).hexdigest()
            edad = db_student.edad or 0
            carrera = db_student.carrera or ""
            rango_edad = "18-20" if edad <= 20 else "21-25" if edad <= 25 else "25+"
            carrera_area = "Ingenierías" if "Ingeniería" in carrera else "Ciencias de la Salud" if carrera in ["Medicina", "Psicología"] else "Humanidades y Negocios"
            db.add(VistaSeudonimizadaML(
                id_registro=uuid.uuid4(),
                id_evaluacion=eval_id,
                id_estudiante=student_id,
                id_estudiante_hash=student_hash,
                rango_edad=rango_edad,
                genero=db_student.genero,
                carrera_area=carrera_area,
                universidad=db_student.universidad,
                q1=payload.phq9_respuestas[0],
                q2=payload.phq9_respuestas[1],
                q3=payload.phq9_respuestas[2],
                q4=payload.phq9_respuestas[3],
                q5=payload.phq9_respuestas[4],
                q6=payload.phq9_respuestas[5],
                q7=payload.phq9_respuestas[6],
                q8=payload.phq9_respuestas[7],
                q9=payload.phq9_respuestas[8],
                prediction="riesgo_depresion" if ml_pred == 1 else "sin_riesgo",
                origen_modelo=origin_model,
                note=f"Inferencia en tiempo real (LR paper). Probabilidad: {ml_prob}%.",
                horas_sueno=payload.horas_sueno,
                calidad_sueno=payload.calidad_sueno,
                historia_salud_mental=payload.historia_salud_mental,
                mspss_total=mspss_total,
                promedio_ponderado=payload.promedio_ponderado,
                ciclo=payload.ciclo,
                edad=payload.edad
            ))
    except Exception as extra_err:
        print(f"[MindCheck] Advertencia: registros complementarios no guardados: {extra_err}", flush=True)

    try:
        db.commit()
    except Exception as commit_err:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al confirmar la evaluación: {str(commit_err)}"
        )

    return {
        "success": True,
        "id": str(eval_id),
        "score": score,
        "nivel_riesgo": risk_level,
        "alerta_suicidio": suicide_alert,
        "interpretabilidad": interpretabilidad
    }


# --- Cachés de datos estáticos del envío -------------------------------------
PHQ9_QUESTIONNAIRE_ID = uuid.UUID('b1990c88-e25f-4a87-8d07-7ff7bd8de693')
DEFAULT_PHQ9 = [
    "Poco interés o placer en hacer las cosas",
    "Sentirse deprimido, triste o sin esperanza",
    "Dificultad para conciliar el sueño o dormir demasiado",
    "Sentirse cansado o con poca energía",
    "Disminución del apetito o comer en exceso",
    "Sentirse mal contigo mismo —o que eres un fracaso— o que has defraudado a tu familia",
    "Dificultad para concentrarte en cosas, como leer el periódico o ver la televisión",
    "Moverte o hablar tan lento que otras personas podrían haberlo notado, o lo contrario — estar tan inquieto que te mueves mucho más de lo habitual",
    "Pensamientos de que estarías mejor muerto o de hacerte daño de alguna manera"
]
_phq9_cache: dict = {}
_phq9_lock = threading.Lock()
_model_label_cache: dict = {"expires": 0.0, "value": None}


def _ensure_phq9_questionnaire(db: Session):
    """(id_cuestionario, [id_pregunta x9 en orden]). Crea lo que falte la primera vez."""
    cached = _phq9_cache.get("value")
    if cached:
        return cached
    with _phq9_lock:
        if _phq9_cache.get("value"):
            return _phq9_cache["value"]
        cuestionario = db.query(Cuestionario).filter(Cuestionario.id_cuestionario == PHQ9_QUESTIONNAIRE_ID).first()
        if not cuestionario:
            db.add(Cuestionario(
                id_cuestionario=PHQ9_QUESTIONNAIRE_ID,
                nombre="Cuestionario sobre la Salud del Paciente (PHQ-9)",
                descripcion="Herramienta de tamizaje para detectar y medir la gravedad de la depresión.",
                estado="activo",
                version="1.0",
                activo=True
            ))
            db.commit()
        preguntas = (
            db.query(Pregunta.id_pregunta)
            .filter(Pregunta.id_cuestionario == PHQ9_QUESTIONNAIRE_ID)
            .order_by(Pregunta.orden)
            .all()
        )
        if len(preguntas) != len(DEFAULT_PHQ9):
            # Recrear las preguntas si están incompletas o corruptas
            try:
                db.query(Pregunta).filter(Pregunta.id_cuestionario == PHQ9_QUESTIONNAIRE_ID).delete(synchronize_session=False)
                db.commit()
            except Exception:
                db.rollback()
            ids = []
            for idx, txt in enumerate(DEFAULT_PHQ9, start=1):
                pid = uuid.uuid4()
                ids.append(pid)
                db.add(Pregunta(id_pregunta=pid, id_cuestionario=PHQ9_QUESTIONNAIRE_ID, texto=txt, orden=idx, dimension="PHQ-9", activa=True))
            db.commit()
        else:
            ids = [p.id_pregunta for p in preguntas]
        _phq9_cache["value"] = (PHQ9_QUESTIONNAIRE_ID, ids)
        return _phq9_cache["value"]


def _active_model_labels(db: Session):
    """(model_version para auditoría, origen_modelo para la vista ML), cacheado 60 s."""
    now = time.time()
    if _model_label_cache["value"] and _model_label_cache["expires"] > now:
        return _model_label_cache["value"]
    from ..utils.ml_model import PRIMARY_MODEL_NAME
    current_model = (
        db.query(ModeloVersion.nombre, ModeloVersion.version)
        .filter(ModeloVersion.activo == True)
        .order_by(ModeloVersion.fecha_publicacion.desc())
        .first()
    )
    value = (
        f"{current_model.nombre} v{current_model.version}" if current_model else "LogisticRegression v1.0",
        # El prefijo "LogisticRegression" marca registros con MSPSS 1-7 (reutilizables para reentrenar).
        f"{PRIMARY_MODEL_NAME} v{current_model.version}" if current_model else f"{PRIMARY_MODEL_NAME} v1.0",
    )
    _model_label_cache.update(value=value, expires=now + 60)
    return value


# ============================================================================
# AUTO-SAVE & RECOVERY ENDPOINTS (HU0008)
# ============================================================================

def _progress_owner_id(current_user: Optional[dict]) -> Optional[uuid.UUID]:
    if not current_user or not current_user.get("id"):
        return None
    try:
        return uuid.UUID(str(current_user["id"]))
    except ValueError:
        return None


def _assert_progress_access(progress: ProgresoCuestionario, current_user: Optional[dict]) -> None:
    """
    T-001: si el progreso ya pertenece a un usuario autenticado, exige que quien
    llama sea ese mismo usuario. Los progresos anónimos (id_usuario NULL) se
    mantienen accesibles solo por session_id, igual que antes.
    """
    if progress.id_usuario is None:
        return
    caller_id = _progress_owner_id(current_user)
    if caller_id is None or caller_id != progress.id_usuario:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para acceder a este progreso de cuestionario."
        )


@router.post("/progress/save")
def save_progress(
    payload: ProgresoCreate,
    db: Session = Depends(get_db),
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """
    Save or update questionnaire progress automatically.
    Creates a new progress record if it doesn't exist, otherwise updates it.
    Non-critical: returns success even if DB save fails.
    """
    try:
        # Ensure the cuestionario exists (creates it if not, avoids FK error)
        cuestionario = db.query(Cuestionario).filter(
            Cuestionario.id_cuestionario == payload.cuestionario_id
        ).first()
        if not cuestionario:
            cuestionario = Cuestionario(
                id_cuestionario=payload.cuestionario_id,
                nombre="Cuestionario sobre la Salud del Paciente (PHQ-9)",
                descripcion="Herramienta de tamizaje para detectar y medir la gravedad de la depresión.",
                estado="activo",
                version="1.0",
                activo=True
            )
            db.add(cuestionario)
            db.commit()

        # Check if progress already exists for this session
        existing_progress = db.query(ProgresoCuestionario).filter(
            ProgresoCuestionario.session_id == payload.session_id
        ).first()

        if existing_progress:
            _assert_progress_access(existing_progress, current_user)
            owner_id = _progress_owner_id(current_user)
            if owner_id is not None and existing_progress.id_usuario is None:
                existing_progress.id_usuario = owner_id
            existing_progress.pregunta_actual = payload.pregunta_actual
            existing_progress.respuestas = payload.respuestas
            existing_progress.consentimiento_aceptado = payload.consentimiento_aceptado
            existing_progress.ultima_actualizacion = datetime.utcnow()
            existing_progress.activo = True
            db.commit()
            db.refresh(existing_progress)
            return existing_progress
        else:
            new_progress = ProgresoCuestionario(
                session_id=payload.session_id,
                id_usuario=_progress_owner_id(current_user),
                id_cuestionario=payload.cuestionario_id,
                pregunta_actual=payload.pregunta_actual,
                respuestas=payload.respuestas,
                consentimiento_aceptado=payload.consentimiento_aceptado,
                activo=True
            )
            db.add(new_progress)
            db.commit()
            db.refresh(new_progress)
            return new_progress
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"[MindCheck] Advertencia: No se pudo guardar progreso: {e}")
        # Return a neutral 200 so the client doesn't get CORS-less 500 errors
        return {"saved": False, "message": "Auto-guardado omitido temporalmente."}


@router.get("/progress/recover/{session_id}")
def recover_progress(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """
    Recover questionnaire progress for a given session ID.
    Returns the saved progress if it exists and is active, or null if not found.
    """
    progress = db.query(ProgresoCuestionario).filter(
        ProgresoCuestionario.session_id == session_id,
        ProgresoCuestionario.activo == True
    ).first()

    if not progress:
        # Return null/empty instead of 404 to avoid CORS issues on the client
        return None

    _assert_progress_access(progress, current_user)
    return progress


@router.delete("/progress/delete", status_code=status.HTTP_200_OK)
def delete_progress(
    payload: ProgresoDeleteRequest,
    db: Session = Depends(get_db),
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """
    Delete questionnaire progress (used when user voluntarily restarts).
    Marks the progress as inactive rather than deleting it.
    """
    progress = db.query(ProgresoCuestionario).filter(
        ProgresoCuestionario.session_id == payload.session_id
    ).first()

    if not progress:
        return {"success": True, "message": "No había progreso para esta sesión."}

    _assert_progress_access(progress, current_user)

    # Mark as inactive instead of deleting
    progress.activo = False
    db.commit()

    return {"success": True, "message": "Progreso eliminado correctamente."}


@router.post("/progress/update", response_model=ProgresoResponse, status_code=status.HTTP_200_OK)
def update_progress(
    session_id: str,
    payload: ProgresoUpdate,
    db: Session = Depends(get_db),
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """
    Update existing questionnaire progress.
    """
    progress = db.query(ProgresoCuestionario).filter(
        ProgresoCuestionario.session_id == session_id,
        ProgresoCuestionario.activo == True
    ).first()

    if not progress:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontró progreso activo para esta sesión."
        )

    _assert_progress_access(progress, current_user)

    progress.pregunta_actual = payload.pregunta_actual
    progress.respuestas = payload.respuestas
    progress.consentimiento_aceptado = payload.consentimiento_aceptado
    progress.ultima_actualizacion = datetime.utcnow()
    db.commit()
    db.refresh(progress)

    return progress
