"""
Índices de rendimiento para las consultas del panel admin/psicólogo.

Postgres no crea índices automáticamente sobre las claves foráneas. Sin ellos,
cada SUM(valor) por evaluación o cada join evaluación→respuesta recorría la
tabla completa. Todas las sentencias son idempotentes (IF NOT EXISTS).

La misma lista está en supabase/migrations/20261116000000_performance_indexes.sql.
"""

from sqlalchemy import text

PERFORMANCE_INDEXES = [
    # Puntaje PHQ-9: SUM(valor) GROUP BY id_evaluacion
    ("idx_respuesta_id_evaluacion", "respuesta", "id_evaluacion"),
    # Historial, evolución, cartera del psicólogo, filtros por fecha
    ("idx_evaluacion_estudiante_fecha", "evaluacion", "id_estudiante, fecha_evaluacion"),
    ("idx_evaluacion_psicologo_fecha", "evaluacion", "id_psicologo, fecha_evaluacion"),
    ("idx_evaluacion_fecha", "evaluacion", "fecha_evaluacion"),
    ("idx_resultado_nivel_riesgo", "resultado", "nivel_riesgo"),
    ("idx_resultado_fecha", "resultado", "fecha_resultado"),
    ("idx_derivacion_id_resultado", "derivacion_clinica", "id_resultado"),
    ("idx_derivacion_fecha", "derivacion_clinica", "fecha_derivacion"),
    ("idx_observacion_id_evaluacion", "observacion_seguimiento", "id_evaluacion"),
    ("idx_notificacion_psicologo_created", "notificacion", "id_psicologo, created_at"),
    ("idx_notificacion_created", "notificacion", "created_at"),
    ("idx_notificacion_id_resultado", "notificacion", "id_resultado"),
    ("idx_auditoria_acceso_fecha", "auditoria_acceso", "fecha_evento"),
    ("idx_auditoria_acceso_usuario", "auditoria_acceso", "id_usuario"),
    ("idx_intervencion_estudiante_fecha", "intervencion", "id_estudiante, fecha_intervencion"),
    ("idx_auditoria_ml_fecha", "auditoria_model_ml", "fecha_evento"),
    ("idx_auditoria_ml_version_tipo", "auditoria_model_ml", "model_version, tipo_evento"),
    ("idx_vista_ml_fecha", "vista_seudonimizada_ml", "fecha_generacion"),
    ("idx_vista_ml_origen", "vista_seudonimizada_ml", "origen_modelo"),
    ("idx_chatbot_interaccion_created", "chatbot_interaccion", "created_at"),
    ("idx_cita_psicologo_fecha", "cita", "id_psicologo, fecha_inicio"),
    ("idx_consentimiento_fecha", "consentimiento", "fecha_aceptacion"),
]


def ensure_performance_indexes(engine) -> int:
    """Crea los índices que falten. Devuelve cuántas sentencias se ejecutaron sin error."""
    ok = 0
    for name, table, columns in PERFORMANCE_INDEXES:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})"))
            ok += 1
        except Exception as exc:  # una tabla ausente no debe impedir el arranque
            print(f"[MindCheck] No se pudo crear el índice {name}: {exc}")
    return ok
