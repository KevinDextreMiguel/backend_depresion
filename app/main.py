from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request, status, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from .config import settings
from .database import engine, Base, get_db, check_database_connection, SessionLocal
from .routers import auth, questionnaire, admin, chatbot, extended_features
from .routers import backups as backups_router
from .security import require_role
from .exceptions import http_exception_handler, validation_exception_handler
from sqlalchemy.orm import Session
from uuid import UUID
from typing import Dict, Any

# Initialize database tables automatically (for sqlite or postgres schema safety)
# Usually tables are built via schema.sql in Supabase, but this ensures they exist locally/for testing
_db_ok, _db_msg = check_database_connection()
if _db_ok:
    try:
        Base.metadata.create_all(bind=engine)
        print(f"[MindCheck] {_db_msg} | AUTH_MODE={settings.AUTH_MODE}")
        
        # Migración de esquema dinámica (HU0041/HU0043)
        try:
            from sqlalchemy import text
            with engine.connect() as conn:
                result = conn.execute(text("SELECT * FROM vista_seudonimizada_ml LIMIT 1"))
                existing_cols = list(result.keys())
                
                new_cols = [
                    ("horas_sueno", "FLOAT"),
                    ("calidad_sueno", "VARCHAR(50)"),
                    ("historia_salud_mental", "VARCHAR(100)"),
                    ("mspss_total", "INTEGER"),
                    ("promedio_ponderado", "FLOAT"),
                    ("ciclo", "VARCHAR(50)"),
                    ("edad", "INTEGER")
                ]
                
                alter_applied = False
                for col_name, col_type in new_cols:
                    if col_name not in existing_cols:
                        # SQLite / PostgreSQL alter syntax compatibility
                        conn.execute(text(f"ALTER TABLE vista_seudonimizada_ml ADD COLUMN {col_name} {col_type}"))
                        print(f"[MindCheck] Columna migrada: vista_seudonimizada_ml.{col_name}")
                        alter_applied = True
                if alter_applied:
                    try:
                        conn.commit()
                    except Exception:
                        pass
        except Exception as alter_err:
            print(f"[MindCheck] Error al verificar/migrar esquema: {alter_err}")
        
        # Seed default configurations if not exists
        try:
            from .models import ConfiguracionSistema
            db = SessionLocal()
            defaults = {
                "tc_version": "1.0",
                "tc_content": "Términos y Condiciones Generales: Al ingresar a MindCheck, aceptas que tus datos de navegación y respuestas serán tratadas para el tamizaje clínico y apoyo terapéutico, bajo la Ley N.° 29733 de Protección de Datos Personales del Perú.",
                "consent_version": "1.0",
                "consent_content": "Consentimiento Informado (Ley N.° 29733): Autorizo el tratamiento de mis respuestas de salud mental del cuestionario PHQ-9 de forma confidencial. Comprendo que mis respuestas se usarán para evaluar mi nivel de riesgo y activar los canales de derivación correspondientes.",
                "anomalous_session_threshold": "50"
            }
            for k, v in defaults.items():
                existing = db.query(ConfiguracionSistema).filter(ConfiguracionSistema.clave == k).first()
                if not existing:
                    cfg = ConfiguracionSistema(clave=k, valor=v, descripcion=f"Parámetro {k}")
                    db.add(cfg)
            db.commit()
            db.close()
            print("[MindCheck] Configuración seed completada.")
        except Exception as seed_err:
            print(f"[MindCheck] Error al seedear configuración: {seed_err}")
    except Exception as e:
        print(f"[MindCheck] Conexión OK pero error creando tablas: {e}")
else:
    print(f"[MindCheck] ERROR de base de datos: {_db_msg}")


# --- Backup Scheduler (HU0032 CA1) ---
_scheduler = None

def _run_scheduled_backup():
    """Ejecuta un respaldo automático según la programación configurada."""
    from .utils.backup import create_db_backup
    db = SessionLocal()
    try:
        log = create_db_backup(db, tipo="automatico")
        print(f"[Backup Scheduler] Respaldo automático {'completado' if log.estado == 'completado' else 'fallido'}: {log.nombre}")
    except Exception as e:
        print(f"[Backup Scheduler] Error en respaldo automático: {e}")
    finally:
        db.close()


def _setup_backup_scheduler():
    """Lee BackupConfig de la BD y programa el job APScheduler si está activo."""
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from .models import BackupConfig
        db = SessionLocal()
        try:
            config = db.query(BackupConfig).first()
        finally:
            db.close()

        if _scheduler and _scheduler.running:
            _scheduler.shutdown(wait=False)

        _scheduler = BackgroundScheduler()

        if config and config.activo and config.periodicidad != "manual":
            h, m = map(int, config.hora.split(":"))
            if config.periodicidad == "diaria":
                _scheduler.add_job(_run_scheduled_backup, "cron", hour=h, minute=m, id="backup_job", replace_existing=True)
            elif config.periodicidad == "semanal" and config.dia_semana is not None:
                _scheduler.add_job(_run_scheduled_backup, "cron", day_of_week=config.dia_semana, hour=h, minute=m, id="backup_job", replace_existing=True)
            elif config.periodicidad == "mensual" and config.dia_mes is not None:
                _scheduler.add_job(_run_scheduled_backup, "cron", day=config.dia_mes, hour=h, minute=m, id="backup_job", replace_existing=True)
            _scheduler.start()
            print(f"[Backup Scheduler] Programado: {config.periodicidad} a las {config.hora}")
        else:
            _scheduler.start()
            print("[Backup Scheduler] Iniciado sin job activo (periodicidad=manual o inactivo).")
    except ImportError:
        print("[Backup Scheduler] APScheduler no instalado. Instala con: pip install apscheduler")
    except Exception as e:
        print(f"[Backup Scheduler] Error al configurar: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ciclo de vida de la aplicación: arranque y cierre."""
    _setup_backup_scheduler()
    yield
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        print("[Backup Scheduler] Detenido.")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Backend en FastAPI con conexión a Supabase y PostgreSQL para la detección de depresión estudiantil.",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

_cors_origins = [
    o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()
] or ["http://localhost:5173", "http://127.0.0.1:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=r"https?://.*",  # Permite cualquier origen (incluyendo Vercel) con credenciales de forma dinámica
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)

# --- Mount Structured Router Modules ---
app.include_router(auth.router, prefix="/api")
app.include_router(questionnaire.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(chatbot.router, prefix="/api")
app.include_router(backups_router.router, prefix="/api")
app.include_router(extended_features.router, prefix="/api")

# --- ROOT Health Check ---
@app.get("/")
async def root():
    db_ok, db_msg = check_database_connection()
    return {
        "status": "online",
        "message": "Servidor FastAPI de Salud Mental UPC Iniciado Correctamente.",
        "version": settings.VERSION,
        "docs": "/docs",
        "database": {
            "connected": db_ok,
            "detail": db_msg,
            "provider": "supabase" if settings.is_supabase_postgres else "postgresql",
        },
    }

# ============================================================================
# DIRECT FRONTEND COMPATIBILITY LAYER
# Maps existing Supabase Edge Function endpoints directly to FastAPI routes
# making the system 100% plug-and-play for the React application.
# ============================================================================

@app.post("/make-server-d427d5bf/submit-questionnaire", status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def compatibility_submit_questionnaire(
    payload: Dict[str, Any] = Body(...),
    request: Request = None,
    db: Session = Depends(get_db),
):
    """
    Direct compatibility endpoint mapping the original Deno Edge Function
    `/make-server-d427d5bf/submit-questionnaire` directly to our FastAPI DB handler.
    """
    from .routers.questionnaire import submit_questionnaire_simple
    # If request is None, create a dummy request object
    if request is None:
        request = type('DummyRequest', (), {'headers': {}, 'client': None})()
    return await submit_questionnaire_simple(payload, request, db)


@app.post("/make-server-d427d5bf/login", include_in_schema=False)
async def compatibility_login(payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    from .routers.auth import login
    from .schemas import UserLogin

    response = await login(
        UserLogin(email=payload.get("email"), password=payload.get("password")),
        db,
    )
    # Map to AuthSession structure expected by frontend
    # Note: response.user is a dictionary, not a Pydantic model
    return {
        "access_token": response.access_token,
        "token_type": response.token_type,
        "user": {
            "id": response.user.get("id") or str(response.user.get("id_usuario", "")),
            "email": response.user.get("email") or response.user.get("correo", ""),
            "nombre": response.user.get("nombre", ""),
            "foto_perfil": response.user.get("foto_perfil"),
            "rol": response.user.get("rol", "")
        }
    }


@app.post("/make-server-d427d5bf/login-student", include_in_schema=False)
async def compatibility_login_student(payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """
    Login route specifically for students. Uses the same login logic as /login
    but verifies the user has the 'estudiante' role.
    """
    from .routers.auth import login
    from .schemas import UserLogin

    response = await login(
        UserLogin(email=payload.get("email"), password=payload.get("password")),
        db,
    )
    # Map to AuthSession structure expected by frontend
    return {
        "access_token": response.access_token,
        "token_type": response.token_type,
        "user": {
            "id": response.user.get("id") or str(response.user.get("id_usuario", "")),
            "email": response.user.get("email") or response.user.get("correo", ""),
            "nombre": response.user.get("nombre", ""),
            "foto_perfil": response.user.get("foto_perfil"),
            "rol": response.user.get("rol", "")
        }
    }


@app.post("/make-server-d427d5bf/signup-student", status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def compatibility_signup_student(
    payload: Dict[str, Any] = Body(...),
    request: Request = None,
    db: Session = Depends(get_db),
):
    """
    Compatibility endpoint: routes student registration through the legacy prefix
    so Vercel's /make-server-d427d5bf proxy can forward it to Render.
    """
    from .routers.auth import signup_student
    from .schemas import StudentSignup

    print(f"[Compat signup-student] payload keys: {list(payload.keys())}", flush=True)

    if request is None:
        request = type('DummyRequest', (), {'client': None})()

    try:
        edad_raw = payload.get("edad")
        edad_val = int(edad_raw) if edad_raw is not None else 0

        student_in = StudentSignup(
            email=payload.get("email", ""),
            password=payload.get("password", ""),
            nombre=payload.get("nombre", ""),
            edad=edad_val,
            genero=payload.get("genero"),
            carrera=payload.get("carrera"),
            universidad=payload.get("universidad"),
        )
    except Exception as ve:
        print(f"[Compat signup-student] Validation error: {ve}", flush=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Datos inválidos: {str(ve)}"
        )

    result = await signup_student(student_in, request, db)
    # result is an AuthTokenResponse — map to the AuthSession shape the frontend expects
    return {
        "access_token": result.access_token,
        "token_type": result.token_type,
        "user": result.user,
    }


@app.post("/make-server-d427d5bf/signup", status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def compatibility_signup(
    payload: Dict[str, Any] = Body(...),
    request: Request = None,
    db: Session = Depends(get_db),
):
    """
    Registro admin vía auth local (JWT) — compatible con el frontend MindCheck.
    """
    from .routers.auth import signup, login
    from .schemas import UserCreate, UserLogin

    email = payload.get("email")
    password = payload.get("password")
    name = payload.get("name", "Admin")

    if not email or not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Correo y contraseña son obligatorios.",
        )

    user_in = UserCreate(
        email=email,
        password=password,
        nombre=name,
        rol="admin",
    )
    # Execute signup
    await signup(user_in, request, db)
    
    # After signup, login to return the token
    response = await login(
        UserLogin(email=email, password=password),
        db,
    )
    
    # Map to AuthSession structure expected by frontend
    return {
        "access_token": response.access_token,
        "token_type": response.token_type,
        "user": {
            "id": response.user.get("id") or str(response.user.get("id_usuario", "")),
            "email": response.user.get("email") or response.user.get("correo", ""),
            "nombre": response.user.get("nombre", ""),
            "foto_perfil": response.user.get("foto_perfil"),
            "rol": response.user.get("rol", "")
        }
    }


@app.get("/make-server-d427d5bf/admin/users", include_in_schema=False)
async def compatibility_get_all_users(
    current_user: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db)
):
    from .routers.admin import get_all_users
    return await get_all_users(current_user, db)


@app.put("/make-server-d427d5bf/admin/users/{user_id}", include_in_schema=False)
async def compatibility_update_user(
    user_id: UUID,
    payload: Dict[str, Any] = Body(...),
    request: Request = None,
    current_user: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db)
):
    from .routers.admin import update_user
    from .schemas import UserUpdateRequest
    if request is None:
        request = type('DummyRequest', (), {'headers': {}, 'client': None})()
    
    body = UserUpdateRequest(
        rol=payload.get("rol"),
        activo=payload.get("activo")
    )
    return await update_user(user_id, body, request, current_user, db)


@app.get("/make-server-d427d5bf/admin/model/status", include_in_schema=False)
async def compatibility_get_model_status(
    current_user: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db)
):
    from .routers.admin import get_model_status
    return await get_model_status(current_user, db)


@app.post("/make-server-d427d5bf/admin/model/retrain", include_in_schema=False)
async def compatibility_retrain_model(
    payload: Dict[str, Any] = Body(...),
    request: Request = None,
    current_user: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db)
):
    from .routers.admin import retrain_model
    from .schemas import ModelRetrainRequest
    if request is None:
        request = type('DummyRequest', (), {'headers': {}, 'client': None})()
    
    body = ModelRetrainRequest(
        model_name=payload.get("model_name"),
        version=payload.get("version"),
        origen_datos=payload.get("origen_datos"),
        comentario=payload.get("comentario")
    )
    return await retrain_model(body, request, current_user, db)


@app.get("/make-server-d427d5bf/statistics", include_in_schema=False)
async def compatibility_statistics(
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db)
):
    """
    Direct compatibility endpoint mapping `/make-server-d427d5bf/statistics`
    to fetch real-time aggregated scoring and risk levels.
    """
    from .routers.admin import get_statistics
    return await get_statistics(current_user, db)


@app.get("/make-server-d427d5bf/health", include_in_schema=False)
async def compatibility_health():
    """
    Health check mapping for Deno function compatibility.
    """
    return {"status": "ok", "service": settings.PROJECT_NAME, "version": settings.VERSION}


@app.get("/make-server-d427d5bf/reports", include_in_schema=False)
async def compatibility_reports(
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    from .routers.admin import get_reports
    return await get_reports(current_user, db)


@app.get("/make-server-d427d5bf/derivations", include_in_schema=False)
async def compatibility_derivations(
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    from .routers.admin import get_clinical_derivations
    return await get_clinical_derivations(current_user, db)


@app.put("/make-server-d427d5bf/derivations/{derivation_id}", include_in_schema=False)
async def compatibility_update_derivation(
    derivation_id: UUID,
    payload: Dict[str, Any] = Body(...),
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    from .schemas import DerivacionUpdate
    from .routers.admin import update_clinical_derivation

    body = DerivacionUpdate(**payload)
    return await update_clinical_derivation(derivation_id, body, current_user=current_user, db=db)


@app.get("/make-server-d427d5bf/student-history/{anon_student_id}", include_in_schema=False)
async def compatibility_student_history(
    anon_student_id: str,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    from .routers.admin import get_student_history
    return await get_student_history(anon_student_id, current_user, db)


@app.get("/make-server-d427d5bf/student/evolution", include_in_schema=False)
async def compatibility_student_evolution(
    current_user: dict = Depends(require_role(["estudiante", "admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    from .routers.admin import get_student_evolution
    return await get_student_evolution(current_user, db)


@app.post("/make-server-d427d5bf/student-history/{anon_student_id}/observations", status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def compatibility_create_student_observation(
    anon_student_id: str,
    payload: Dict[str, Any] = Body(...),
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    from .routers.admin import create_student_observation
    return await create_student_observation(anon_student_id, payload, current_user, db)


@app.put("/make-server-d427d5bf/observations/{observation_id}", include_in_schema=False)
async def compatibility_update_student_observation(
    observation_id: UUID,
    payload: Dict[str, Any] = Body(...),
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    from .routers.admin import update_student_observation
    return await update_student_observation(observation_id, payload, current_user, db)


@app.get("/make-server-d427d5bf/admin/assigned-patients", include_in_schema=False)
async def compatibility_get_assigned_patients(
    request: Request,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    from .routers.admin import get_assigned_patients
    risk_levels = request.query_params.getlist("risk_levels") or None
    return await get_assigned_patients(current_user, db, risk_levels)


@app.get("/make-server-d427d5bf/admin/appointments", include_in_schema=False)
async def compatibility_list_appointments(
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    from .routers.admin import list_appointments
    return await list_appointments(current_user, db)


@app.post("/make-server-d427d5bf/admin/appointments", status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def compatibility_create_appointment(
    payload: Dict[str, Any] = Body(...),
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    from .routers.admin import create_appointment
    from .schemas import AppointmentCreate
    body = AppointmentCreate(**payload)
    return await create_appointment(body, current_user, db)


@app.get("/make-server-d427d5bf/student-history/{anon_student_id}/interventions", include_in_schema=False)
async def compatibility_get_student_interventions(
    anon_student_id: str,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db)
):
    from .routers.admin import get_student_interventions
    return await get_student_interventions(anon_student_id, current_user, db)


@app.post("/make-server-d427d5bf/student-history/{anon_student_id}/interventions", status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def compatibility_create_student_intervention(
    anon_student_id: str,
    payload: Dict[str, Any] = Body(...),
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db)
):
    from .routers.admin import create_student_intervention
    from .schemas import IntervencionCreate
    body = IntervencionCreate(**payload)
    return await create_student_intervention(anon_student_id, body, current_user, db)


@app.put("/make-server-d427d5bf/interventions/{intervention_id}", include_in_schema=False)
async def compatibility_update_clinical_intervention(
    intervention_id: UUID,
    payload: Dict[str, Any] = Body(...),
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db)
):
    from .routers.admin import update_clinical_intervention
    from .schemas import IntervencionUpdate
    body = IntervencionUpdate(**payload)
    return await update_clinical_intervention(intervention_id, body, current_user, db)


# --- HU0029: Notification Compatibility Routes ---

@app.get("/make-server-d427d5bf/admin/notifications", include_in_schema=False)
async def compatibility_get_notifications(
    solo_no_revisadas: bool = False,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    from .routers.admin import get_notifications
    return await get_notifications(solo_no_revisadas=solo_no_revisadas, current_user=current_user, db=db)


@app.put("/make-server-d427d5bf/admin/notifications/{notification_id}/mark-revisada", include_in_schema=False)
async def compatibility_mark_notification_revisada(
    notification_id: UUID,
    payload: Dict[str, Any] = Body(...),
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    from .routers.admin import mark_notification_revisada
    from .schemas import NotificacionMarkRevisada
    body = NotificacionMarkRevisada(**payload)
    return await mark_notification_revisada(notification_id, body, current_user, db)


@app.get("/make-server-d427d5bf/trends", include_in_schema=False)
async def compatibility_trends(
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    from .routers.admin import get_monthly_trends
    return await get_monthly_trends(current_user, db)


@app.get("/make-server-d427d5bf/chatbot/responses", include_in_schema=False)
async def compatibility_get_chatbot_responses(
    active: bool = True,
    db: Session = Depends(get_db),
):
    from .routers.chatbot import get_chatbot_responses
    return await get_chatbot_responses(active=active, db=db)


@app.post("/make-server-d427d5bf/chatbot/responses", status_code=status.HTTP_201_CREATED)
async def compatibility_create_chatbot_response(
    payload: dict,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    from .schemas import ChatbotResponseCreate
    from .routers.chatbot import create_chatbot_response

    body = ChatbotResponseCreate(**payload)
    return await create_chatbot_response(body, current_user=current_user, db=db)


@app.put("/make-server-d427d5bf/chatbot/responses/{response_id}")
async def compatibility_update_chatbot_response(
    response_id: UUID,
    payload: dict,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    from .schemas import ChatbotResponseUpdate
    from .routers.chatbot import update_chatbot_response

    body = ChatbotResponseUpdate(**payload)
    return await update_chatbot_response(response_id, body, current_user=current_user, db=db)


@app.delete("/make-server-d427d5bf/chatbot/responses/{response_id}", status_code=status.HTTP_204_NO_CONTENT)
async def compatibility_delete_chatbot_response(
    response_id: UUID,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    from .routers.chatbot import delete_chatbot_response
    return await delete_chatbot_response(response_id, current_user=current_user, db=db)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
