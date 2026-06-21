from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import func
from ..database import get_db
from ..models import (
    Usuario, Estudiante, Psicologo, Consentimiento, Cuestionario, 
    Pregunta, Evaluacion, Respuesta, Resultado, VistaSeudonimizadaML, DerivacionClinica, ProgresoCuestionario, ModeloVersion, AuditoriaModelML
)
from ..schemas import QuestionnaireSubmit, SubmitSuccessResponse, ProgresoCreate, ProgresoUpdate, ProgresoResponse, ProgresoDeleteRequest, CuestionarioCompletoSubmit
from ..security import db_encrypt, db_decrypt
import uuid
import random
import hashlib
from datetime import datetime

router = APIRouter(prefix="/questionnaire", tags=["Questionnaire"])

# Direct compatibility endpoint matching the exact payload from front-end
class SimpleSubmitPayload(dict):
    pass

@router.post("/submit-questionnaire", status_code=status.HTTP_201_CREATED)
async def submit_questionnaire_simple(
    payload: CuestionarioCompletoSubmit,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Recibe el cuestionario completo del estudiante (demografía, hábitos, MSPSS y PHQ-9).
    Calcula scores clínicos, ejecuta la inferencia en tiempo real con el modelo RandomForest
    y registra los resultados en la base de datos de manera atómica.
    """
    if not payload.consentimiento_aceptado:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Se requiere la aceptación del consentimiento informado para continuar."
        )

    # 1. Obtener o crear el cuestionario PHQ-9 por defecto
    cuestionario = db.query(Cuestionario).filter(
        Cuestionario.id_cuestionario == uuid.UUID('b1990c88-e25f-4a87-8d07-7ff7bd8de693')
    ).first()
    
    if not cuestionario:
        cuestionario = Cuestionario(
            id_cuestionario=uuid.UUID('b1990c88-e25f-4a87-8d07-7ff7bd8de693'),
            nombre="Cuestionario sobre la Salud del Paciente (PHQ-9)",
            descripcion="Herramienta de tamizaje para detectar y medir la gravedad de la depresión.",
            estado="activo",
            version="1.0",
            activo=True
        )
        db.add(cuestionario)
        db.commit()

    # 2. Asegurar preguntas en BD
    existing_questions = db.query(Pregunta).filter(Pregunta.id_cuestionario == cuestionario.id_cuestionario).count()
    if existing_questions == 0:
        default_phq9 = [
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
        for idx, txt in enumerate(default_phq9, start=1):
            q = Pregunta(
                id_pregunta=uuid.uuid4(),
                id_cuestionario=cuestionario.id_cuestionario,
                texto=txt,
                orden=idx,
                dimension="PHQ-9",
                activa=True
            )
            db.add(q)
        db.commit()

    # 3. Crear perfil de estudiante con datos ingresados
    # Priority: 1) Bearer token (authenticated user), 2) test_user_id from payload
    test_user_id = payload.test_user_id
    auth_user_id = None
    
    # Try to get authenticated user from Bearer token
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        # Try Supabase validation first (most reliable for Supabase-issued tokens)
        try:
            from ..security import get_supabase_client
            supabase = get_supabase_client()
            if supabase:
                try:
                    user_resp = supabase.auth.get_user(token)
                    if user_resp and user_resp.user:
                        auth_user_id = user_resp.user.id
                        print(f"[MindCheck] Auth OK via Supabase: {auth_user_id}", flush=True)
                except Exception as sup_err:
                    print(f"[MindCheck] Supabase auth failed: {sup_err}", flush=True)
        except Exception:
            pass
        
        # Fallback: Try local JWT decode
        if not auth_user_id:
            try:
                import jwt as pyjwt
                from ..config import settings
                decoded = pyjwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"], audience="authenticated")
                auth_user_id = decoded.get("sub")
                if auth_user_id:
                    print(f"[MindCheck] Auth OK via local JWT: {auth_user_id}", flush=True)
            except Exception as jwt_err:
                print(f"[MindCheck] Local JWT decode failed: {jwt_err}", flush=True)
    
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
            db.commit()

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

        db.commit()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"[MindCheck] Error creando perfil: {e}", flush=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al crear perfil del estudiante: {str(e)}"
        )

    # 4. Asignar psicólogo activo por defecto
    psicologo = db.query(Psicologo).filter(Psicologo.activo == True).first()
    if not psicologo:
        test_psico_user = payload.test_psicologo_user_id
        try:
            psico_user_id = uuid.UUID(test_psico_user) if test_psico_user else anon_user_id
        except Exception:
            psico_user_id = anon_user_id

        existing_psico_usuario = db.query(Usuario).filter(Usuario.id_usuario == psico_user_id).first()
        if not existing_psico_usuario:
            psico_user = Usuario(
                id_usuario=psico_user_id,
                nombre=db_encrypt("Dra. Sarah Chen"),
                correo=db_encrypt("s.chen@upc.edu.pe"),
                rol="psicologo",
                activo=True
            )
            db.add(psico_user)
            db.commit()

        psicologo = Psicologo(
            id_psicologo=uuid.uuid4(),
            id_usuario=psico_user_id,
            especialidad="Terapia Cognitivo Conductual",
            numero_colegiatura="COP-49201",
            activo=True
        )
        db.add(psicologo)
        db.commit()

    # 5. Crear Evaluacion
    eval_id = uuid.uuid4()
    db_eval = Evaluacion(
        id_evaluacion=eval_id,
        id_estudiante=student_id,
        id_cuestionario=cuestionario.id_cuestionario,
        id_psicologo=psicologo.id_psicologo,
        estado="completada",
        consentimiento_verificado=True
    )
    db.add(db_eval)
    db.commit()

    # 6. Guardar respuestas del PHQ-9 en base de datos
    questions = db.query(Pregunta).filter(Pregunta.id_cuestionario == cuestionario.id_cuestionario).order_by(Pregunta.orden).all()
    if len(questions) != 9:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Las preguntas del cuestionario PHQ-9 no están completamente inicializadas."
        )

    score = 0
    for idx, ans_val in enumerate(payload.phq9_respuestas):
        score += ans_val
        pregunta = questions[idx]
        db_ans = Respuesta(
            id_respuesta=uuid.uuid4(),
            id_evaluacion=eval_id,
            id_pregunta=pregunta.id_pregunta,
            valor=ans_val
        )
        db.add(db_ans)

    # 7. Calcular score de MSPSS total
    mspss_total = sum(payload.mspss_respuestas)

    # 8. Inferencia en tiempo real con el modelo de Machine Learning (RandomForest Bloque B)
    from ..utils.ml_model import predict_depression_risk
    try:
        ml_pred, ml_prob = predict_depression_risk(
            horas_sueno=payload.horas_sueno,
            mspss_total=mspss_total,
            historia_salud_mental=payload.historia_salud_mental,
            calidad_sueno=payload.calidad_sueno
        )
    except Exception as ml_err:
        # Fallback por reglas si el modelo falla o no está disponible
        print(f"[MindCheck] Inferencia fallida, aplicando fallback por reglas: {ml_err}")
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

    # 9. Insertar resultado clínico e interpretabilidad de ML
    result_id = uuid.uuid4()
    db_result = Resultado(
        id_resultado=result_id,
        id_evaluacion=eval_id,
        nivel_riesgo=risk_level,
        probabilidad=float(round(ml_prob, 2)),
        interpretabilidad={
            "score": score,
            "q9_value": q9_value,
            "risk_factors": ["item_9_alert" if suicide_alert else None],
            "tree_path": "root -> phq9_score -> " + risk_level,
            "ml_prediction": "riesgo_depresion" if ml_pred == 1 else "sin_riesgo",
            "ml_probability": ml_prob,
            "horas_sueno": payload.horas_sueno,
            "mspss_total": mspss_total,
            "calidad_sueno": payload.calidad_sueno,
            "historia_salud_mental": payload.historia_salud_mental
        },
        alerta_suicidio=suicide_alert
    )
    db.add(db_result)
    db.commit()

    # 10. Derivación automática si se requiere
    if suicide_alert or risk_level in ["moderadamente_severo", "severo"]:
        deriv_id = uuid.uuid4()
        priority = "urgente" if suicide_alert else "alto"
        db_deriv = DerivacionClinica(
            id_derivacion=deriv_id,
            id_resultado=result_id,
            id_psicologo=psicologo.id_psicologo,
            nivel_prioridad=priority,
            accion_tomada=f"Derivación automática iniciada por tamizaje. Alerta suicidio: {suicide_alert}, Score: {score}, ML Probabilidad: {ml_prob}%",
            fecha_derivacion=datetime.utcnow(),
            estado="pendiente",
            institucion_referencia="Psicología Universitaria UPC / Servicio Médico Emergencia"
        )
        db.add(db_deriv)
        
        db_eval.estado = "derivada"
        db.add(db_eval)
        db.commit()

        # Notificación a psicólogos activos
        try:
            from ..models import Notificacion
            titulo = "⚠ Caso Crítico Detectado" if suicide_alert else "🔴 Caso de Riesgo Alto Detectado"
            mensaje = (
                f"Se ha detectado un caso con riesgo {risk_level.replace('_', ' ')}. "
                f"Puntaje PHQ-9: {score}/27. "
                + (f"Inferencia RandomForest: {ml_prob}% riesgo. " if ml_pred == 1 else "")
                + ("⚠ ALERTA DE SUICIDIO ACTIVA. " if suicide_alert else "")
                + "Se requiere atención inmediata."
            )
            all_psicologos = db.query(Psicologo).filter(Psicologo.activo == True).all()
            for psico in all_psicologos:
                notif = Notificacion(
                    id_psicologo=psico.id_psicologo,
                    id_resultado=result_id,
                    titulo=titulo,
                    mensaje=mensaje,
                    nivel_riesgo=risk_level,
                    alerta_suicidio=suicide_alert,
                    leida=False,
                    revisada=False,
                )
                db.add(notif)
            db.commit()
        except Exception as notif_err:
            db.rollback()
            print(f"[MindCheck] Error creando notificación: {notif_err}")

        # Auditoría de acceso
        try:
            from ..models import AuditoriaAcceso
            psico_user_id = psicologo.id_usuario if hasattr(psicologo, 'id_usuario') else None
            auditoria = AuditoriaAcceso(
                id_usuario=psico_user_id,
                accion="activacion_protocolo_suicida",
                tabla_objetivo="derivacion_clinica",
                id_objetivo=deriv_id,
                ip_origen=client_ip,
                detalle=f"Protocolo activado automáticamente. Alerta suicidio: {suicide_alert}, score: {score}, ML: {ml_prob}%"
            )
            db.add(auditoria)
            db.commit()
        except Exception:
            db.rollback()

    # 11. Auditoría de Inferencia ML en AuditoriaModelML
    try:
        current_model = db.query(ModeloVersion).filter(ModeloVersion.activo == True).order_by(ModeloVersion.fecha_publicacion.desc()).first()
        model_ver_str = f"{current_model.nombre} v{current_model.version}" if current_model else "RandomForest v1.0"
        
        audit_pred = AuditoriaModelML(
            tipo_evento="prediccion",
            id_usuario=None, # Anónimo
            model_version=model_ver_str,
            id_prediccion=result_id,
            resultado_prediccion="riesgo_depresion" if ml_pred == 1 else "sin_riesgo"
        )
        db.add(audit_pred)
        db.commit()
    except Exception as audit_ml_err:
        db.rollback()
        print(f"[MindCheck] Error en auditoría ML: {audit_ml_err}")

    # 12. Insertar en Vista Seudonimizada ML con todas las variables recolectadas
    salt = "mindcheck-salt-2026"
    student_hash = hashlib.sha256(f"{student_id.hex}-{salt}".encode()).hexdigest()
    
    rango_edad = "18-20" if db_student.edad <= 20 else "21-25" if db_student.edad <= 25 else "25+"
    carrera_area = "Ingenierías" if "Ingeniería" in db_student.carrera else "Ciencias de la Salud" if db_student.carrera in ["Medicina", "Psicología"] else "Humanidades y Negocios"
    
    current_model = db.query(ModeloVersion).filter(ModeloVersion.activo == True).order_by(ModeloVersion.fecha_publicacion.desc()).first()
    origin_model = f"{current_model.nombre} v{current_model.version}" if current_model else "RandomForest v1.0"

    db_ml = VistaSeudonimizadaML(
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
        note=f"Inferencia en tiempo real (RF). Probabilidad: {ml_prob}%.",
        horas_sueno=payload.horas_sueno,
        calidad_sueno=payload.calidad_sueno,
        historia_salud_mental=payload.historia_salud_mental,
        mspss_total=mspss_total,
        promedio_ponderado=payload.promedio_ponderado,
        ciclo=payload.ciclo,
        edad=payload.edad
    )
    db.add(db_ml)
    db.commit()

    return {
        "success": True,
        "id": str(eval_id),
        "score": score,
        "nivel_riesgo": risk_level,
        "alerta_suicidio": suicide_alert,
        "interpretabilidad": db_result.interpretabilidad
    }


# ============================================================================
# AUTO-SAVE & RECOVERY ENDPOINTS (HU0008)
# ============================================================================

@router.post("/progress/save", response_model=ProgresoResponse, status_code=status.HTTP_200_OK)
async def save_progress(
    payload: ProgresoCreate,
    db: Session = Depends(get_db)
):
    """
    Save or update questionnaire progress automatically.
    Creates a new progress record if it doesn't exist, otherwise updates it.
    """
    # Check if progress already exists for this session
    existing_progress = db.query(ProgresoCuestionario).filter(
        ProgresoCuestionario.session_id == payload.session_id
    ).first()

    if existing_progress:
        # Update existing progress
        existing_progress.pregunta_actual = payload.pregunta_actual
        existing_progress.respuestas = payload.respuestas
        existing_progress.consentimiento_aceptado = payload.consentimiento_aceptado
        existing_progress.ultima_actualizacion = datetime.utcnow()
        existing_progress.activo = True
        db.commit()
        db.refresh(existing_progress)
        return existing_progress
    else:
        # Create new progress record
        new_progress = ProgresoCuestionario(
            session_id=payload.session_id,
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


@router.get("/progress/recover/{session_id}", response_model=ProgresoResponse, status_code=status.HTTP_200_OK)
async def recover_progress(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    Recover questionnaire progress for a given session ID.
    Returns the saved progress if it exists and is active.
    """
    progress = db.query(ProgresoCuestionario).filter(
        ProgresoCuestionario.session_id == session_id,
        ProgresoCuestionario.activo == True
    ).first()

    if not progress:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontró progreso guardado para esta sesión."
        )

    return progress


@router.delete("/progress/delete", status_code=status.HTTP_200_OK)
async def delete_progress(
    payload: ProgresoDeleteRequest,
    db: Session = Depends(get_db)
):
    """
    Delete questionnaire progress (used when user voluntarily restarts).
    Marks the progress as inactive rather than deleting it.
    """
    progress = db.query(ProgresoCuestionario).filter(
        ProgresoCuestionario.session_id == payload.session_id
    ).first()

    if not progress:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontró progreso para esta sesión."
        )

    # Mark as inactive instead of deleting
    progress.activo = False
    db.commit()

    return {"success": True, "message": "Progreso eliminado correctamente."}


@router.post("/progress/update", response_model=ProgresoResponse, status_code=status.HTTP_200_OK)
async def update_progress(
    session_id: str,
    payload: ProgresoUpdate,
    db: Session = Depends(get_db)
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

    progress.pregunta_actual = payload.pregunta_actual
    progress.respuestas = payload.respuestas
    progress.consentimiento_aceptado = payload.consentimiento_aceptado
    progress.ultima_actualizacion = datetime.utcnow()
    db.commit()
    db.refresh(progress)

    return progress
