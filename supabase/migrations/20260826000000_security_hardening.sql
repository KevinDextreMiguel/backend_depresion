-- Migración de seguridad — Fase 3 (auditoría de trazabilidad HU, 2026-08-26)
-- Aditiva y no destructiva: segura de ejecutar sobre una base de datos en producción.
-- Corresponde a los hallazgos transversales T-001 (progreso sin dueño) y T-003 (roles sin validar).

-- T-001: permite vincular un registro de progreso de cuestionario a un usuario autenticado
-- (si aplica) para poder verificar propiedad en /questionnaire/progress/*. Los registros
-- existentes quedan con id_usuario = NULL, tratados como anónimos igual que antes.
ALTER TABLE progreso_cuestionario ADD COLUMN IF NOT EXISTS id_usuario uuid NULL;
CREATE INDEX IF NOT EXISTS idx_progreso_id_usuario ON progreso_cuestionario(id_usuario);

-- T-003: evita que se inserte o actualice un rol fuera del catálogo permitido a nivel de BD,
-- como última línea de defensa además de la validación en el backend (schemas.py).
ALTER TABLE usuario DROP CONSTRAINT IF EXISTS usuario_rol_check;
ALTER TABLE usuario ADD CONSTRAINT usuario_rol_check
    CHECK (rol IN ('estudiante', 'psicologo', 'admin', 'investigador'));
