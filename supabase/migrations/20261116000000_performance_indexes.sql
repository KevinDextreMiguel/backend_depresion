-- Índices de rendimiento para el panel admin/psicólogo.
-- Postgres no indexa automáticamente las claves foráneas; sin estos índices
-- las consultas de puntaje PHQ-9 e historial recorrían tablas completas.
-- Idempotente: el backend también los crea al arrancar (app/db_indexes.py).

CREATE INDEX IF NOT EXISTS idx_respuesta_id_evaluacion ON respuesta (id_evaluacion);
CREATE INDEX IF NOT EXISTS idx_evaluacion_estudiante_fecha ON evaluacion (id_estudiante, fecha_evaluacion);
CREATE INDEX IF NOT EXISTS idx_evaluacion_psicologo_fecha ON evaluacion (id_psicologo, fecha_evaluacion);
CREATE INDEX IF NOT EXISTS idx_evaluacion_fecha ON evaluacion (fecha_evaluacion);
CREATE INDEX IF NOT EXISTS idx_resultado_nivel_riesgo ON resultado (nivel_riesgo);
CREATE INDEX IF NOT EXISTS idx_resultado_fecha ON resultado (fecha_resultado);
CREATE INDEX IF NOT EXISTS idx_derivacion_id_resultado ON derivacion_clinica (id_resultado);
CREATE INDEX IF NOT EXISTS idx_derivacion_fecha ON derivacion_clinica (fecha_derivacion);
CREATE INDEX IF NOT EXISTS idx_observacion_id_evaluacion ON observacion_seguimiento (id_evaluacion);
CREATE INDEX IF NOT EXISTS idx_notificacion_psicologo_created ON notificacion (id_psicologo, created_at);
CREATE INDEX IF NOT EXISTS idx_notificacion_created ON notificacion (created_at);
CREATE INDEX IF NOT EXISTS idx_notificacion_id_resultado ON notificacion (id_resultado);
CREATE INDEX IF NOT EXISTS idx_auditoria_acceso_fecha ON auditoria_acceso (fecha_evento);
CREATE INDEX IF NOT EXISTS idx_auditoria_acceso_usuario ON auditoria_acceso (id_usuario);
CREATE INDEX IF NOT EXISTS idx_intervencion_estudiante_fecha ON intervencion (id_estudiante, fecha_intervencion);
CREATE INDEX IF NOT EXISTS idx_auditoria_ml_fecha ON auditoria_model_ml (fecha_evento);
CREATE INDEX IF NOT EXISTS idx_auditoria_ml_version_tipo ON auditoria_model_ml (model_version, tipo_evento);
CREATE INDEX IF NOT EXISTS idx_vista_ml_fecha ON vista_seudonimizada_ml (fecha_generacion);
CREATE INDEX IF NOT EXISTS idx_vista_ml_origen ON vista_seudonimizada_ml (origen_modelo);
CREATE INDEX IF NOT EXISTS idx_chatbot_interaccion_created ON chatbot_interaccion (created_at);
CREATE INDEX IF NOT EXISTS idx_cita_psicologo_fecha ON cita (id_psicologo, fecha_inicio);
CREATE INDEX IF NOT EXISTS idx_consentimiento_fecha ON consentimiento (fecha_aceptacion);
