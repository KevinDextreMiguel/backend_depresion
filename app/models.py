import uuid
from sqlalchemy import Column, String, Integer, Boolean, DateTime, LargeBinary, ForeignKey, Numeric, JSON, Float
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy.types import TypeDecorator
from .database import Base
from .config import settings

class EncryptedString(TypeDecorator):
    impl = LargeBinary
    cache_ok = True

    def bind_expression(self, bindvalue):
        return func.encrypt_sensible_data(bindvalue, settings.ENCRYPTION_KEY)

    def column_expression(self, col):
        return func.decrypt_sensible_data(col, settings.ENCRYPTION_KEY)

class Usuario(Base):
    __tablename__ = "usuario"

    id_usuario = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre = Column(EncryptedString, nullable=False)
    foto_perfil = Column(String, nullable=True)
    correo = Column(String, nullable=True)
    rol = Column(String(30), nullable=False)
    fecha_registro = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    activo = Column(Boolean, nullable=False, default=True)
    fecha_baja = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    # Relationships
    administrador = relationship("Administrador", uselist=False, back_populates="usuario")
    psicologo = relationship("Psicologo", uselist=False, back_populates="usuario")
    estudiante = relationship("Estudiante", uselist=False, back_populates="usuario")
    consentimientos = relationship("Consentimiento", back_populates="usuario")
    auditorias = relationship("AuditoriaAcceso", back_populates="usuario")


class Administrador(Base):
    __tablename__ = "administrador"

    id_admin = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_usuario = Column(UUID(as_uuid=True), ForeignKey("usuario.id_usuario", ondelete="CASCADE"), nullable=False, unique=True)
    activo = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    usuario = relationship("Usuario", back_populates="administrador")


class Psicologo(Base):
    __tablename__ = "psicologo"

    id_psicologo = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_usuario = Column(UUID(as_uuid=True), ForeignKey("usuario.id_usuario", ondelete="CASCADE"), nullable=False, unique=True)
    especialidad = Column(String(100), nullable=True)
    numero_colegiatura = Column(String(50), nullable=True)
    activo = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    usuario = relationship("Usuario", back_populates="psicologo")
    evaluaciones = relationship("Evaluacion", back_populates="psicologo")
    derivaciones = relationship("DerivacionClinica", back_populates="psicologo")
    notificaciones = relationship("Notificacion", back_populates="psicologo")


class Estudiante(Base):
    __tablename__ = "estudiante"

    id_estudiante = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_usuario = Column(UUID(as_uuid=True), ForeignKey("usuario.id_usuario", ondelete="CASCADE"), nullable=False, unique=True)
    edad = Column(Integer, nullable=False)
    genero = Column(String(20), nullable=True)
    carrera = Column(String(100), nullable=True)
    universidad = Column(String(150), nullable=True)
    activo = Column(Boolean, nullable=False, default=True)
    fecha_baja = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    usuario = relationship("Usuario", back_populates="estudiante")
    evaluaciones = relationship("Evaluacion", back_populates="estudiante")
    vistas_ml = relationship("VistaSeudonimizadaML", back_populates="estudiante")


class Consentimiento(Base):
    __tablename__ = "consentimiento"

    id_consentimiento = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_usuario = Column(UUID(as_uuid=True), ForeignKey("usuario.id_usuario", ondelete="CASCADE"), nullable=False)
    version_documento = Column(String(50), nullable=False)
    fecha_aceptacion = Column(DateTime(timezone=True), nullable=False)
    ip_origen = Column(String(45), nullable=False)
    hash_documento = Column(String(255), nullable=False)
    revocado = Column(Boolean, nullable=False, default=False)
    fecha_revocacion = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    usuario = relationship("Usuario", back_populates="consentimientos")


class Cuestionario(Base):
    __tablename__ = "cuestionario"

    id_cuestionario = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre = Column(String(100), nullable=False)
    descripcion = Column(String, nullable=True)
    estado = Column(String(20), nullable=False)
    version = Column(String(20), nullable=True)
    activo = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    preguntas = relationship("Pregunta", back_populates="cuestionario", cascade="all, delete-orphan")
    evaluaciones = relationship("Evaluacion", back_populates="cuestionario")


class Pregunta(Base):
    __tablename__ = "pregunta"

    id_pregunta = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_cuestionario = Column(UUID(as_uuid=True), ForeignKey("cuestionario.id_cuestionario", ondelete="CASCADE"), nullable=False)
    texto = Column(String, nullable=False)
    orden = Column(Integer, nullable=False)
    dimension = Column(String(50), nullable=True)
    activa = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    cuestionario = relationship("Cuestionario", back_populates="preguntas")
    respuestas = relationship("Respuesta", back_populates="pregunta")


class Evaluacion(Base):
    __tablename__ = "evaluacion"

    id_evaluacion = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_estudiante = Column(UUID(as_uuid=True), ForeignKey("estudiante.id_estudiante", ondelete="CASCADE"), nullable=False)
    id_cuestionario = Column(UUID(as_uuid=True), ForeignKey("cuestionario.id_cuestionario", ondelete="CASCADE"), nullable=False)
    id_psicologo = Column(UUID(as_uuid=True), ForeignKey("psicologo.id_psicologo", ondelete="CASCADE"), nullable=False)
    fecha_evaluacion = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    estado = Column(String(30), nullable=True)
    consentimiento_verificado = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    estudiante = relationship("Estudiante", back_populates="evaluaciones")
    cuestionario = relationship("Cuestionario", back_populates="evaluaciones")
    psicologo = relationship("Psicologo", back_populates="evaluaciones")
    respuestas = relationship("Respuesta", back_populates="evaluacion", cascade="all, delete-orphan")
    resultado = relationship("Resultado", uselist=False, back_populates="evaluacion")
    vista_ml = relationship("VistaSeudonimizadaML", uselist=False, back_populates="evaluacion")


class Respuesta(Base):
    __tablename__ = "respuesta"

    id_respuesta = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_evaluacion = Column(UUID(as_uuid=True), ForeignKey("evaluacion.id_evaluacion", ondelete="CASCADE"), nullable=False)
    id_pregunta = Column(UUID(as_uuid=True), ForeignKey("pregunta.id_pregunta", ondelete="CASCADE"), nullable=False)
    valor = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    evaluacion = relationship("Evaluacion", back_populates="respuestas")
    pregunta = relationship("Pregunta", back_populates="respuestas")


class Resultado(Base):
    __tablename__ = "resultado"

    id_resultado = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_evaluacion = Column(UUID(as_uuid=True), ForeignKey("evaluacion.id_evaluacion", ondelete="CASCADE"), nullable=False, unique=True)
    nivel_riesgo = Column(String(20), nullable=False)
    probabilidad = Column(Numeric(5, 2), nullable=False)
    fecha_resultado = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    interpretabilidad = Column(JSON, nullable=True)
    alerta_suicidio = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    evaluacion = relationship("Evaluacion", back_populates="resultado")
    derivaciones = relationship("DerivacionClinica", back_populates="resultado")


class DerivacionClinica(Base):
    __tablename__ = "derivacion_clinica"

    id_derivacion = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_resultado = Column(UUID(as_uuid=True), ForeignKey("resultado.id_resultado", ondelete="CASCADE"), nullable=False)
    id_psicologo = Column(UUID(as_uuid=True), ForeignKey("psicologo.id_psicologo", ondelete="CASCADE"), nullable=False)
    nivel_prioridad = Column(String(20), nullable=False)
    accion_tomada = Column(String, nullable=False)
    fecha_derivacion = Column(DateTime(timezone=True), nullable=False)
    estado = Column(String(30), nullable=False)
    institucion_referencia = Column(String(150), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    resultado = relationship("Resultado", back_populates="derivaciones")
    psicologo = relationship("Psicologo", back_populates="derivaciones")


class ObservacionSeguimiento(Base):
    __tablename__ = "observacion_seguimiento"

    id_observacion = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_evaluacion = Column(UUID(as_uuid=True), ForeignKey("evaluacion.id_evaluacion", ondelete="CASCADE"), nullable=True)
    id_psicologo = Column(UUID(as_uuid=True), ForeignKey("psicologo.id_psicologo", ondelete="SET NULL"), nullable=True)
    texto = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    psicologo = relationship("Psicologo")
    evaluacion = relationship("Evaluacion")


class Cita(Base):
    __tablename__ = "cita"

    id_cita = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_psicologo = Column(UUID(as_uuid=True), ForeignKey("psicologo.id_psicologo", ondelete="CASCADE"), nullable=False)
    id_estudiante = Column(UUID(as_uuid=True), ForeignKey("estudiante.id_estudiante", ondelete="CASCADE"), nullable=False)
    fecha_inicio = Column(DateTime(timezone=True), nullable=False)
    duracion_minutos = Column(Integer, nullable=False, default=60)
    estado = Column(String(30), nullable=False, default="programada")
    descripcion = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    psicologo = relationship("Psicologo")
    estudiante = relationship("Estudiante")


class AuditoriaAcceso(Base):
    __tablename__ = "auditoria_acceso"

    id_auditoria = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_usuario = Column(UUID(as_uuid=True), ForeignKey("usuario.id_usuario", ondelete="CASCADE"), nullable=False)
    accion = Column(String(50), nullable=False)
    tabla_objetivo = Column(String(50), nullable=False)
    id_objetivo = Column(UUID(as_uuid=True), nullable=False)
    fecha_evento = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ip_origen = Column(String(45), nullable=True)
    detalle = Column(String, nullable=True)

    # Relationships
    usuario = relationship("Usuario", back_populates="auditorias")


class ModeloVersion(Base):
    __tablename__ = "modelo_version"

    id_modelo = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre = Column(String(100), nullable=False)
    version = Column(String(20), nullable=False)
    fecha_publicacion = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    origen_datos = Column(String(255), nullable=True)
    activo = Column(Boolean, nullable=False, default=True)
    comentario = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ChatbotRespuesta(Base):
    __tablename__ = "chatbot_respuesta"

    id_respuesta = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clave = Column(String(120), nullable=False, unique=True)
    texto = Column(String, nullable=False)
    categoria = Column(String(80), nullable=True)
    activa = Column(Boolean, nullable=False, default=True)
    orden = Column(Integer, nullable=True, default=0)
    id_creador = Column(UUID(as_uuid=True), ForeignKey("usuario.id_usuario", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    creador = relationship("Usuario")


class VistaSeudonimizadaML(Base):
    __tablename__ = "vista_seudonimizada_ml"

    id_registro = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_evaluacion = Column(UUID(as_uuid=True), ForeignKey("evaluacion.id_evaluacion", ondelete="CASCADE"), nullable=True, unique=True)
    id_estudiante = Column(UUID(as_uuid=True), ForeignKey("estudiante.id_estudiante", ondelete="CASCADE"), nullable=True)
    id_estudiante_hash = Column(String(255), nullable=True)
    rango_edad = Column(String(20), nullable=True)
    genero = Column(String(20), nullable=True)
    carrera_area = Column(String(100), nullable=True)
    universidad = Column(String(150), nullable=True)
    q1 = Column(Integer, nullable=True)
    q2 = Column(Integer, nullable=True)
    q3 = Column(Integer, nullable=True)
    q4 = Column(Integer, nullable=True)
    q5 = Column(Integer, nullable=True)
    q6 = Column(Integer, nullable=True)
    q7 = Column(Integer, nullable=True)
    q8 = Column(Integer, nullable=True)
    q9 = Column(Integer, nullable=True)
    prediction = Column(String(20), nullable=True)
    fecha_generacion = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    origen_modelo = Column(String(50), nullable=True)
    note = Column(String, nullable=True)
    
    # Nuevos campos predictivos y sociodemográficos (Cuestionario ampliado)
    horas_sueno = Column(Float, nullable=True)
    calidad_sueno = Column(String(50), nullable=True)
    historia_salud_mental = Column(String(100), nullable=True)
    mspss_total = Column(Integer, nullable=True)
    promedio_ponderado = Column(Float, nullable=True)
    ciclo = Column(String(50), nullable=True)
    edad = Column(Integer, nullable=True)

    # Relationships
    evaluacion = relationship("Evaluacion", back_populates="vista_ml")
    estudiante = relationship("Estudiante", back_populates="vistas_ml")


class ProgresoCuestionario(Base):
    __tablename__ = "progreso_cuestionario"

    id_progreso = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(String(255), nullable=False, unique=True, index=True)
    id_cuestionario = Column(UUID(as_uuid=True), ForeignKey("cuestionario.id_cuestionario", ondelete="CASCADE"), nullable=False)
    pregunta_actual = Column(Integer, nullable=False, default=0)
    respuestas = Column(JSON, nullable=False)
    consentimiento_aceptado = Column(Boolean, nullable=False, default=False)
    ultima_actualizacion = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    activo = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    cuestionario = relationship("Cuestionario")


class Intervencion(Base):
    __tablename__ = "intervencion"

    id_intervencion = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_estudiante = Column(UUID(as_uuid=True), ForeignKey("estudiante.id_estudiante", ondelete="CASCADE"), nullable=False)
    id_psicologo = Column(UUID(as_uuid=True), ForeignKey("psicologo.id_psicologo", ondelete="CASCADE"), nullable=False)
    tipo_intervencion = Column(String(100), nullable=False)
    descripcion = Column(String, nullable=False)
    fecha_intervencion = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    # Relationships
    estudiante = relationship("Estudiante")
    psicologo = relationship("Psicologo", foreign_keys=[id_psicologo])


class Notificacion(Base):
    __tablename__ = "notificacion"

    id_notificacion = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_psicologo = Column(UUID(as_uuid=True), ForeignKey("psicologo.id_psicologo", ondelete="CASCADE"), nullable=True)
    id_resultado = Column(UUID(as_uuid=True), ForeignKey("resultado.id_resultado", ondelete="CASCADE"), nullable=True)
    titulo = Column(String(200), nullable=False)
    mensaje = Column(String, nullable=False)
    nivel_riesgo = Column(String(40), nullable=False)
    alerta_suicidio = Column(Boolean, nullable=False, default=False)
    leida = Column(Boolean, nullable=False, default=False)
    revisada = Column(Boolean, nullable=False, default=False)
    fecha_revision = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    psicologo = relationship("Psicologo", back_populates="notificaciones")
    resultado = relationship("Resultado")


class BackupConfig(Base):
    __tablename__ = "backup_config"

    id_config = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    periodicidad = Column(String(50), nullable=False, default="manual")  # diaria, semanal, mensual, manual
    hora = Column(String(5), nullable=False, default="00:00")  # HH:MM
    dia_semana = Column(Integer, nullable=True)  # 0-6 (Lunes-Domingo)
    dia_mes = Column(Integer, nullable=True)  # 1-31
    activo = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())


class BackupLog(Base):
    __tablename__ = "backup_log"

    id_backup = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre = Column(String(255), nullable=False)
    ruta = Column(String(500), nullable=False)
    tamano_bytes = Column(Integer, nullable=True)
    estado = Column(String(50), nullable=False)  # completado, fallido
    fecha_creacion = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    tipo = Column(String(50), nullable=False)  # automatico, manual, backup_pre_restore
    error_mensaje = Column(String, nullable=True)


class TerminosAceptacion(Base):
    __tablename__ = "terminos_aceptacion"

    id_aceptacion = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_usuario = Column(UUID(as_uuid=True), ForeignKey("usuario.id_usuario", ondelete="CASCADE"), nullable=True)
    version = Column(String(50), nullable=False)
    fecha_aceptacion = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ip_origen = Column(String(45), nullable=False)
    aceptado = Column(Boolean, nullable=False, default=True)


class ConfiguracionSistema(Base):
    __tablename__ = "configuracion_sistema"

    id_config = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clave = Column(String(100), unique=True, nullable=False)
    valor = Column(String, nullable=False)
    descripcion = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())


class AuditoriaModelML(Base):
    __tablename__ = "auditoria_model_ml"

    id_log = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tipo_evento = Column(String(50), nullable=False)  # 'entrenamiento' o 'prediccion'
    id_usuario = Column(UUID(as_uuid=True), ForeignKey("usuario.id_usuario", ondelete="SET NULL"), nullable=True)
    model_version = Column(String(50), nullable=False)
    fecha_evento = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    precision = Column(Numeric(5, 4), nullable=True)
    recall = Column(Numeric(5, 4), nullable=True)
    f1_score = Column(Numeric(5, 4), nullable=True)
    accuracy = Column(Numeric(5, 4), nullable=True)
    id_prediccion = Column(UUID(as_uuid=True), nullable=True)
    resultado_prediccion = Column(String(50), nullable=True)



