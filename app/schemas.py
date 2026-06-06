from pydantic import BaseModel, EmailStr, Field, validator
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime

# --- General Schema Base ---
class PyModel(BaseModel):
    class Config:
        from_attributes = True

# --- Authentication & User Profiles ---
class UserBase(PyModel):
    rol: str = Field(..., description="Role: admin, psicologo, estudiante")

class UserCreate(UserBase):
    email: EmailStr
    password: str
    nombre: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: Dict[str, Any]

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    new_password: str
    access_token: str

class UserResponse(UserBase):
    id_usuario: UUID
    nombre: str
    foto_perfil: Optional[str] = None
    correo: str
    fecha_registro: datetime
    activo: bool

    model_config = {"populate_by_name": True}

class UserUpdateProfile(BaseModel):
    nombre: str = Field(..., min_length=1, description="Nombre completo del usuario")
    foto_perfil: Optional[str] = Field(None, description="Imagen en formato base64")

class UserUpdateRequest(BaseModel):
    rol: Optional[str] = Field(None, description="Nuevo rol del usuario: admin, psicologo, estudiante")
    activo: Optional[bool] = Field(None, description="Estado de acceso del usuario")

# --- Roles Schema Extensions ---
class StudentCreate(PyModel):
    edad: int = Field(..., ge=0)
    genero: Optional[str] = None
    carrera: Optional[str] = None
    universidad: Optional[str] = None

class StudentSignup(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, description="Contraseña de mínimo 6 caracteres")
    nombre: str = Field(..., min_length=1, description="Nombre completo obligatorio")
    edad: int = Field(..., ge=0, description="Edad obligatoria")
    genero: Optional[str] = None
    carrera: Optional[str] = None
    universidad: Optional[str] = None

class PsychologistCreate(PyModel):
    especialidad: Optional[str] = None
    numero_colegiatura: Optional[str] = None

# --- Consent Schema ---
class ConsentCreate(PyModel):
    version_documento: str
    ip_origen: str
    hash_documento: str

class ConsentResponse(ConsentCreate):
    id_consentimiento: UUID
    id_usuario: UUID
    fecha_aceptacion: datetime
    revocado: bool
    fecha_revocacion: Optional[datetime] = None

# --- Questionnaire and Questions ---
class PreguntaResponse(PyModel):
    id_pregunta: UUID
    texto: str
    orden: int
    dimension: Optional[str] = None
    activa: bool

class CuestionarioResponse(PyModel):
    id_cuestionario: UUID
    nombre: str
    descripcion: Optional[str] = None
    estado: str
    version: Optional[str] = None
    activo: bool
    preguntas: List[PreguntaResponse] = []

# --- Responses & Evaluations ---
class ResponseItem(BaseModel):
    pregunta_id: UUID
    valor: int = Field(..., ge=0, le=3, description="PHQ-9 value (0 to 3)")

class QuestionnaireSubmit(BaseModel):
    cuestionario_id: UUID
    estudiante_id: UUID
    psicologo_id: Optional[UUID] = None
    responses: List[ResponseItem]
    consentimiento: ConsentCreate
    ip_origen: str

class CuestionarioCompletoSubmit(BaseModel):
    # Datos Demográficos
    edad: int = Field(..., ge=0, le=120)
    genero: str = Field(..., description="Género: Masculino, Femenino, Otro")
    carrera: str = Field(..., min_length=1)
    universidad: str = Field(..., min_length=1)
    ciclo: str = Field(..., min_length=1)
    promedio_ponderado: float = Field(..., ge=0.0, le=20.0)
    situacion_pareja: str = Field(..., min_length=1)
    convivencia: str = Field(..., min_length=1)
    distrito_residencia: str = Field(..., min_length=1)
    trabajo_estudio: str = Field(..., min_length=1)
    migracion: str = Field(..., min_length=1)

    # Variables predictoras del modelo de ML (hábitos y salud)
    horas_sueno: float = Field(..., ge=0.0, le=24.0)
    calidad_sueno: str = Field(..., description="Calidad: Muy mala, Mala, Regular, Buena, Muy buena")
    historia_salud_mental: str = Field(..., description="Antecedentes: Nunca, Previo no actual, Actual, Prefiero no responder")

    # Respuestas de escalas
    mspss_respuestas: List[int] = Field(..., min_items=12, max_items=12, description="Exactamente 12 respuestas de la escala MSPSS (valores 1 a 4)")
    phq9_respuestas: List[int] = Field(..., min_items=9, max_items=9, description="Exactamente 9 respuestas del PHQ-9 (valores 0 a 3)")
    
    consentimiento_aceptado: bool = Field(..., description="Debe ser True para procesar el tamizaje")
    test_user_id: Optional[str] = None
    test_psicologo_user_id: Optional[str] = None

class SubmitSuccessResponse(BaseModel):
    success: bool
    evaluacion_id: UUID
    resultado_id: UUID
    score: int
    nivel_riesgo: str
    alerta_suicidio: bool
    probabilidad: float

# --- Results ---
class ResultadoResponse(PyModel):
    id_resultado: UUID
    id_evaluacion: UUID
    nivel_riesgo: str
    probabilidad: float
    fecha_resultado: datetime
    interpretabilidad: Optional[Dict[str, Any]] = None
    alerta_suicidio: bool

class ChatbotResponseBase(PyModel):
    clave: str = Field(..., description="Clave única que identifica la respuesta predeterminada")
    texto: str = Field(..., description="Texto de la respuesta del chatbot")
    categoria: Optional[str] = Field(None, description="Categoría de la respuesta")
    activa: bool = Field(True, description="Si la respuesta está habilitada y se puede usar")
    orden: Optional[int] = Field(0, description="Orden de presentación cuando se listan respuestas")

class ChatbotResponseCreate(ChatbotResponseBase):
    pass

class ChatbotResponseUpdate(BaseModel):
    clave: Optional[str] = Field(None, description="Clave única que identifica la respuesta predeterminada")
    texto: Optional[str] = Field(None, description="Texto de la respuesta del chatbot")
    categoria: Optional[str] = Field(None, description="Categoría de la respuesta")
    activa: Optional[bool] = Field(None, description="Si la respuesta está habilitada y se puede usar")
    orden: Optional[int] = Field(None, description="Orden de presentación cuando se listan respuestas")

class ChatbotResponse(PyModel):
    id_respuesta: UUID
    clave: str
    texto: str
    categoria: Optional[str] = None
    activa: bool
    orden: Optional[int] = None
    id_creador: Optional[UUID] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

# --- Clinical Derivation ---
class DerivacionCreate(PyModel):
    id_resultado: UUID
    id_psicologo: UUID
    nivel_prioridad: str = Field(..., description="alto, urgente, moderado")
    accion_tomada: str
    institucion_referencia: Optional[str] = None

class DerivacionUpdate(BaseModel):
    estado: Optional[str] = Field(None, description="pendiente, contactado, atendido, cerrado")
    accion_tomada: Optional[str] = Field(None, description="Descripción de la acción tomada")
    institucion_referencia: Optional[str] = Field(None, description="Institución de referencia alternativa")

class DerivacionResponse(BaseModel):
    id: UUID
    prioridad: str
    accion: str
    institucion: Optional[str] = None
    fecha: datetime
    estado: str = Field(..., description="pendiente, contactado, atendido, cerrado")
    nivel_riesgo: str
    alerta_suicidio: bool


class ObservacionCreate(BaseModel):
    id_evaluacion: Optional[UUID] = None
    texto: str = Field(..., min_length=1, description="Texto de la observación")


class ObservacionUpdate(BaseModel):
    texto: Optional[str] = None


class ObservacionResponse(BaseModel):
    id_observacion: UUID
    id_evaluacion: Optional[UUID] = None
    id_psicologo: Optional[UUID] = None
    texto: str
    created_at: datetime
    updated_at: Optional[datetime] = None

# --- Statistics & Access Auditing ---
class RiskDistribution(BaseModel):
    minimal: int = 0
    mild: int = 0
    moderate: int = 0
    moderatelySevere: int = 0
    severe: int = 0

class StatisticsResponse(BaseModel):
    total: int
    averageScore: float
    riskDistribution: RiskDistribution

class ReportItem(BaseModel):
    id_anonimo: str
    fecha: datetime
    nivel_riesgo: str
    puntaje: int
    alerta_suicidio: bool
    carrera: Optional[str] = None
    universidad: Optional[str] = None

class StudentHistoryItem(BaseModel):
    id_anonimo: str
    fecha: datetime
    nivel_riesgo: str
    puntaje: int
    alerta_suicidio: bool
    carrera: Optional[str] = None
    universidad: Optional[str] = None
    estado_evaluacion: Optional[str] = None
    comentarios: Optional[str] = None
    observaciones: List[ObservacionResponse] = []


class StudentEvolutionItem(BaseModel):
    fecha: datetime
    puntaje: int
    nivel_riesgo: str
    alerta_suicidio: bool

class AssignedPatientItem(BaseModel):
    id_anonimo: str
    carrera: Optional[str] = None
    universidad: Optional[str] = None
    ultima_evaluacion: datetime
    nivel_riesgo: str
    puntaje: int
    alerta_suicidio: bool
    estado_evaluacion: Optional[str] = None


class AppointmentCreate(BaseModel):
    id_estudiante: str
    fecha_inicio: datetime
    duracion_minutos: int = Field(60, description="Duración en minutos")
    descripcion: Optional[str] = None


class AppointmentResponse(PyModel):
    id_cita: UUID
    id_psicologo: UUID
    id_estudiante: UUID
    fecha_inicio: datetime
    duracion_minutos: int
    estado: str
    descripcion: Optional[str] = None
    created_at: datetime

class ModelStatusResponse(BaseModel):
    model_name: str
    version: str
    active_records: int
    last_retrained_at: Optional[datetime] = None
    origen_datos: Optional[str] = None
    comentario: Optional[str] = None

class ModelRetrainRequest(BaseModel):
    model_name: Optional[str] = Field("PHQ-9 Rule-based Engine", description="Nombre del modelo de entrenamiento")
    version: Optional[str] = Field(None, description="Versión nueva del modelo")
    origen_datos: Optional[str] = Field(None, description="Fuente de los datos usados para el reentrenamiento")
    comentario: Optional[str] = Field(None, description="Comentario o descripción del reentrenamiento")

class ModelRetrainResponse(BaseModel):
    success: bool
    model_name: str
    version: str
    previous_version: Optional[str] = None
    updated_records: int
    message: str

# --- Questionnaire Progress (Auto-save & Recovery) ---
class ProgresoCreate(BaseModel):
    session_id: str
    cuestionario_id: UUID
    pregunta_actual: int = 0
    respuestas: Any
    consentimiento_aceptado: bool = False

class ProgresoUpdate(BaseModel):
    pregunta_actual: int
    respuestas: Any
    consentimiento_aceptado: bool = False

class ProgresoResponse(PyModel):
    id_progreso: UUID
    session_id: str
    cuestionario_id: UUID
    pregunta_actual: int
    respuestas: Any
    consentimiento_aceptado: bool
    ultima_actualizacion: datetime
    activo: bool

class ProgresoDeleteRequest(BaseModel):
    session_id: str


# --- Clinical Intervention (HU0027) ---
class IntervencionCreate(BaseModel):
    tipo_intervencion: str = Field(..., min_length=1, description="Tipo de intervención (e.g. Terapia, Derivación)")
    descripcion: str = Field(..., min_length=1, description="Descripción detallada de la intervención")
    fecha_intervencion: datetime = Field(..., description="Fecha y hora de la intervención")

class IntervencionUpdate(BaseModel):
    tipo_intervencion: Optional[str] = Field(None, min_length=1, description="Tipo de intervención")
    descripcion: Optional[str] = Field(None, min_length=1, description="Descripción detallada")
    fecha_intervencion: Optional[datetime] = Field(None, description="Fecha y hora de la intervención")

class IntervencionResponse(PyModel):
    id_intervencion: UUID
    id_estudiante: UUID
    id_psicologo: UUID
    tipo_intervencion: str
    descripcion: str
    fecha_intervencion: datetime
    created_at: datetime
    updated_at: Optional[datetime] = None


# --- Clinical Notifications (HU0029) ---
class NotificacionResponse(PyModel):
    id_notificacion: UUID
    id_psicologo: Optional[UUID] = None
    id_resultado: Optional[UUID] = None
    titulo: str
    mensaje: str
    nivel_riesgo: str
    alerta_suicidio: bool
    leida: bool
    revisada: bool
    fecha_revision: Optional[datetime] = None
    created_at: datetime

    # Computed student info (populated by route handler)
    id_anonimo: Optional[str] = None
    carrera: Optional[str] = None
    universidad: Optional[str] = None
    puntaje: Optional[int] = None

class NotificacionMarkRevisada(BaseModel):
    revisada: bool = True


# --- Backup & Restore (HU0032) ---
class BackupConfigResponse(PyModel):
    id_config: UUID
    periodicidad: str
    hora: str
    dia_semana: Optional[int] = None
    dia_mes: Optional[int] = None
    activo: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

class BackupConfigUpdate(BaseModel):
    periodicidad: Optional[str] = Field(None, description="diaria, semanal, mensual, manual")
    hora: Optional[str] = Field(None, description="Hora de ejecución en formato HH:MM")
    dia_semana: Optional[int] = Field(None, ge=0, le=6, description="0=Lunes, 6=Domingo")
    dia_mes: Optional[int] = Field(None, ge=1, le=31, description="Día del mes (1-31)")
    activo: Optional[bool] = Field(None, description="Habilitar o deshabilitar respaldos automáticos")

class BackupLogResponse(PyModel):
    id_backup: UUID
    nombre: str
    ruta: str
    tamano_bytes: Optional[int] = None
    estado: str
    fecha_creacion: datetime
    tipo: str
    error_mensaje: Optional[str] = None

class BackupRunResponse(BaseModel):
    success: bool
    message: str
    backup: Optional[BackupLogResponse] = None

class BackupRestoreResponse(BaseModel):
    success: bool
    message: str


# --- Extended Features Schemas (HU0033 - HU0045) ---

class TerminosStatusResponse(BaseModel):
    version: str
    content: str
    accepted: bool
    fecha_aceptacion: Optional[datetime] = None

class TerminosAcceptRequest(BaseModel):
    version: str

class ConsentStatusResponse(BaseModel):
    version: str
    content: str
    accepted: bool
    fecha_aceptacion: Optional[datetime] = None

class ConsentAcceptRequest(BaseModel):
    version: str

class UserConsentHistoryItem(BaseModel):
    id_consentimiento: UUID
    version_documento: str
    fecha_aceptacion: datetime
    ip_origen: str
    hash_documento: str
    revocado: bool

class ConfiguracionResponse(PyModel):
    id_config: UUID
    clave: str
    valor: str
    descripcion: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

class ConfiguracionUpdate(BaseModel):
    valor: str
    descripcion: Optional[str] = None

class RiskDistributionDetail(BaseModel):
    minimal: int
    mild: int
    moderate: int
    moderatelySevere: int
    severe: int

class KPIsDashboardResponse(BaseModel):
    total_screenings: int
    average_score: float
    risk_distribution: RiskDistributionDetail
    total_alerts: int
    active_users: int
    total_interventions: int
    total_derivations: int

class TrendPoint(BaseModel):
    label: str
    count: int
    avg_score: float

class TrendsAdvancedResponse(BaseModel):
    by_month: List[TrendPoint]
    by_career: List[TrendPoint]
    by_university: List[TrendPoint]

class InterventionEffectivenessItem(BaseModel):
    id_estudiante_anonimo: str
    antes_intervencion: str  # Nivel de riesgo previo
    despues_intervencion: str # Nivel de riesgo posterior
    score_diff: int           # Variación de puntuación
    fecha_intervencion: datetime
    tipo_intervencion: str

class LiveMonitoringItem(BaseModel):
    sessions_active: int
    total_requests: int
    cpu_usage_pct: float
    memory_usage_mb: float
    alerts: List[Dict[str, Any]]

class AuditoriaModelMLResponse(PyModel):
    id_log: UUID
    tipo_evento: str
    id_usuario: Optional[UUID] = None
    model_version: str
    fecha_evento: datetime
    precision: Optional[float] = None
    recall: Optional[float] = None
    f1_score: Optional[float] = None
    accuracy: Optional[float] = None
    id_prediccion: Optional[UUID] = None
    resultado_prediccion: Optional[str] = None

class MLModelMetricsDetail(BaseModel):
    version: str
    nombre: str
    fecha: datetime
    precision: float
    recall: float
    f1_score: float
    accuracy: float
    training_samples: int


