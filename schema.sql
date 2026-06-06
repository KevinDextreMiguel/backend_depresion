-- ============================================================================
-- SQL Schema for Depression Screening Web App (FastAPI & Supabase Integration)
-- Implements robust RLS, pgcrypto symmetric encryption, and relational integrity.
-- ============================================================================

-- 1. Enable Required Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- 2. Drop existing tables if they exist (clean setup)
DROP TABLE IF EXISTS vista_seudonimizada_ml CASCADE;
DROP TABLE IF EXISTS auditoria_acceso CASCADE;
DROP TABLE IF EXISTS derivacion_clinica CASCADE;
DROP TABLE IF EXISTS intervencion CASCADE;
DROP TABLE IF EXISTS resultado CASCADE;
DROP TABLE IF EXISTS respuesta CASCADE;
DROP TABLE IF EXISTS chatbot_respuesta CASCADE;
DROP TABLE IF EXISTS evaluacion CASCADE;
DROP TABLE IF EXISTS pregunta CASCADE;
DROP TABLE IF EXISTS cuestionario CASCADE;
DROP TABLE IF EXISTS consentimiento CASCADE;
DROP TABLE IF EXISTS estudiante CASCADE;
DROP TABLE IF EXISTS psicologo CASCADE;
DROP TABLE IF EXISTS administrador CASCADE;
DROP TABLE IF EXISTS usuario CASCADE;

-- 3. Create Custom Symmetric Encryption Helpers for pgcrypto
-- Set a default secret key. Note: In production, this can be retrieved from vault or environment variables.
CREATE OR REPLACE FUNCTION decrypt_sensible_data(encrypted_data bytea, secret_key text)
RETURNS text AS $$
BEGIN
    RETURN pgp_sym_decrypt(encrypted_data, secret_key);
EXCEPTION
    WHEN OTHERS THEN
        RETURN NULL;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE OR REPLACE FUNCTION encrypt_sensible_data(plain_text text, secret_key text)
RETURNS bytea AS $$
BEGIN
    RETURN pgp_sym_encrypt(plain_text, secret_key);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- 4. Create Tables

-- TABLE: usuario
CREATE TABLE usuario (
    id_usuario uuid PRIMARY KEY,
    nombre bytea NOT NULL, -- Dato sensible cifrado con pgcrypto
    foto_perfil TEXT,
    rol VARCHAR(30) NOT NULL,
    fecha_registro TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    fecha_baja TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE
);
COMMENT ON COLUMN usuario.id_usuario IS 'FK lógica hacia auth.users.id de Supabase';
COMMENT ON COLUMN usuario.nombre IS 'Dato sensible cifrado con pgcrypto';

-- TABLE: administrador
CREATE TABLE administrador (
    id_admin uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    id_usuario uuid NOT NULL UNIQUE REFERENCES usuario(id_usuario) ON DELETE CASCADE,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- TABLE: psicologo
CREATE TABLE psicologo (
    id_psicologo uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    id_usuario uuid NOT NULL UNIQUE REFERENCES usuario(id_usuario) ON DELETE CASCADE,
    especialidad VARCHAR(100),
    numero_colegiatura VARCHAR(50),
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
COMMENT ON COLUMN psicologo.numero_colegiatura IS 'Validación profesional';

-- TABLE: estudiante
CREATE TABLE estudiante (
    id_estudiante uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    id_usuario uuid NOT NULL UNIQUE REFERENCES usuario(id_usuario) ON DELETE CASCADE,
    edad INTEGER NOT NULL,
    genero VARCHAR(20),
    carrera VARCHAR(100),
    universidad VARCHAR(150),
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    fecha_baja TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- TABLE: consentimiento
CREATE TABLE consentimiento (
    id_consentimiento uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    id_usuario uuid NOT NULL REFERENCES usuario(id_usuario) ON DELETE CASCADE,
    version_documento VARCHAR(50) NOT NULL,
    fecha_aceptacion TIMESTAMP WITH TIME ZONE NOT NULL,
    ip_origen VARCHAR(45) NOT NULL,
    hash_documento VARCHAR(255) NOT NULL,
    revocado BOOLEAN NOT NULL DEFAULT FALSE,
    fecha_revocacion TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
COMMENT ON COLUMN consentimiento.hash_documento IS 'Hash del consentimiento firmado';

-- TABLE: cuestionario
CREATE TABLE cuestionario (
    id_cuestionario uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    nombre VARCHAR(100) NOT NULL,
    descripcion TEXT,
    estado VARCHAR(20) NOT NULL,
    version VARCHAR(20),
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- TABLE: pregunta
CREATE TABLE pregunta (
    id_pregunta uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    id_cuestionario uuid NOT NULL REFERENCES cuestionario(id_cuestionario) ON DELETE CASCADE,
    texto TEXT NOT NULL,
    orden INTEGER NOT NULL,
    dimension VARCHAR(50),
    activa BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
COMMENT ON COLUMN pregunta.dimension IS 'Ej. PHQ-9 item suicida';

-- TABLE: evaluacion
CREATE TABLE evaluacion (
    id_evaluacion uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    id_estudiante uuid NOT NULL REFERENCES estudiante(id_estudiante) ON DELETE CASCADE,
    id_cuestionario uuid NOT NULL REFERENCES cuestionario(id_cuestionario) ON DELETE CASCADE,
    id_psicologo uuid NOT NULL REFERENCES psicologo(id_psicologo) ON DELETE CASCADE,
    fecha_evaluacion TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    estado VARCHAR(30),
    consentimiento_verificado BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
COMMENT ON COLUMN evaluacion.estado IS 'pendiente, completada, derivada';

-- TABLE: respuesta
CREATE TABLE respuesta (
    id_respuesta uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    id_evaluacion uuid NOT NULL REFERENCES evaluacion(id_evaluacion) ON DELETE CASCADE,
    id_pregunta uuid NOT NULL REFERENCES pregunta(id_pregunta) ON DELETE CASCADE,
    valor INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- TABLE: chatbot_respuesta
CREATE TABLE chatbot_respuesta (
    id_respuesta uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    clave VARCHAR(150) NOT NULL UNIQUE,
    texto TEXT NOT NULL,
    categoria VARCHAR(100),
    activa BOOLEAN NOT NULL DEFAULT TRUE,
    orden INTEGER,
    id_creador uuid,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE
);

-- TABLE: resultado
CREATE TABLE resultado (
    id_resultado uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    id_evaluacion uuid NOT NULL UNIQUE REFERENCES evaluacion(id_evaluacion) ON DELETE CASCADE,
    nivel_riesgo VARCHAR(20) NOT NULL,
    probabilidad NUMERIC(5,2) NOT NULL,
    fecha_resultado TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    interpretabilidad JSON,
    alerta_suicidio BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
COMMENT ON COLUMN resultado.interpretabilidad IS 'Ruta del árbol / variables relevantes';
COMMENT ON COLUMN resultado.alerta_suicidio IS 'Activado si PHQ-9 ítem 9 supera umbral';

-- TABLE: derivacion_clinica
CREATE TABLE derivacion_clinica (
    id_derivacion uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    id_resultado uuid NOT NULL REFERENCES resultado(id_resultado) ON DELETE CASCADE,
    id_psicologo uuid NOT NULL REFERENCES psicologo(id_psicologo) ON DELETE CASCADE,
    nivel_prioridad VARCHAR(20) NOT NULL,
    accion_tomada TEXT NOT NULL,
    fecha_derivacion TIMESTAMP WITH TIME ZONE NOT NULL,
    estado VARCHAR(30) NOT NULL,
    institucion_referencia VARCHAR(150),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
COMMENT ON COLUMN derivacion_clinica.nivel_prioridad IS 'alto, urgente, moderado';
COMMENT ON COLUMN derivacion_clinica.estado IS 'pendiente, contactado, atendido, cerrado';

-- TABLE: auditoria_acceso
CREATE TABLE auditoria_acceso (
    id_auditoria uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    id_usuario uuid NOT NULL REFERENCES usuario(id_usuario) ON DELETE CASCADE,
    accion VARCHAR(50) NOT NULL,
    tabla_objetivo VARCHAR(50) NOT NULL,
    id_objetivo uuid NOT NULL,
    fecha_evento TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ip_origen VARCHAR(45),
    detalle TEXT
);
COMMENT ON COLUMN auditoria_acceso.accion IS 'lectura, escritura, exportación';

-- TABLE: vista_seudonimizada_ml
CREATE TABLE vista_seudonimizada_ml (
    id_registro uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    id_evaluacion uuid NOT NULL UNIQUE REFERENCES evaluacion(id_evaluacion) ON DELETE CASCADE,
    id_estudiante uuid NOT NULL REFERENCES estudiante(id_estudiante) ON DELETE CASCADE,
    id_estudiante_hash VARCHAR(255) NOT NULL,
    rango_edad VARCHAR(20),
    genero VARCHAR(20),
    carrera_area VARCHAR(100),
    universidad VARCHAR(150),
    q1 INTEGER,
    q2 INTEGER,
    q3 INTEGER,
    q4 INTEGER,
    q5 INTEGER,
    q6 INTEGER,
    q7 INTEGER,
    q8 INTEGER,
    q9 INTEGER,
    prediction VARCHAR(20),
    fecha_generacion TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    origen_modelo VARCHAR(50),
    note TEXT
);
COMMENT ON COLUMN vista_seudonimizada_ml.id_estudiante_hash IS 'Hash irreversible para análisis ML';
COMMENT ON COLUMN vista_seudonimizada_ml.carrera_area IS 'Agrupada para reducir reidentificación';
COMMENT ON COLUMN vista_seudonimizada_ml.note IS 'Vista seudonimizada derivada de evaluación + respuestas + resultado';

-- TABLE: progreso_cuestionario
CREATE TABLE progreso_cuestionario (
    id_progreso uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id VARCHAR(255) NOT NULL UNIQUE,
    id_cuestionario uuid NOT NULL REFERENCES cuestionario(id_cuestionario) ON DELETE CASCADE,
    pregunta_actual INTEGER NOT NULL DEFAULT 0,
    respuestas JSON NOT NULL,
    consentimiento_aceptado BOOLEAN NOT NULL DEFAULT FALSE,
    ultima_actualizacion TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_progreso_session_id ON progreso_cuestionario(session_id);
COMMENT ON COLUMN progreso_cuestionario.session_id IS 'Identificador único de sesión para recuperación de progreso';
COMMENT ON COLUMN progreso_cuestionario.respuestas IS 'Array JSON con las respuestas parciales del cuestionario';

-- TABLE: intervencion
CREATE TABLE intervencion (
    id_intervencion uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    id_estudiante uuid NOT NULL REFERENCES estudiante(id_estudiante) ON DELETE CASCADE,
    id_psicologo uuid NOT NULL REFERENCES psicologo(id_psicologo) ON DELETE CASCADE,
    tipo_intervencion VARCHAR(100) NOT NULL,
    descripcion TEXT NOT NULL,
    fecha_intervencion TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE
);

-- 5. Access Control & Row-Level Security (RLS)
ALTER TABLE usuario ENABLE ROW LEVEL SECURITY;
ALTER TABLE administrador ENABLE ROW LEVEL SECURITY;
ALTER TABLE psicologo ENABLE ROW LEVEL SECURITY;
ALTER TABLE estudiante ENABLE ROW LEVEL SECURITY;
ALTER TABLE consentimiento ENABLE ROW LEVEL SECURITY;
ALTER TABLE cuestionario ENABLE ROW LEVEL SECURITY;
ALTER TABLE pregunta ENABLE ROW LEVEL SECURITY;
ALTER TABLE evaluacion ENABLE ROW LEVEL SECURITY;
ALTER TABLE respuesta ENABLE ROW LEVEL SECURITY;
ALTER TABLE resultado ENABLE ROW LEVEL SECURITY;
ALTER TABLE derivacion_clinica ENABLE ROW LEVEL SECURITY;
ALTER TABLE auditoria_acceso ENABLE ROW LEVEL SECURITY;
ALTER TABLE vista_seudonimizada_ml ENABLE ROW LEVEL SECURITY;
ALTER TABLE progreso_cuestionario ENABLE ROW LEVEL SECURITY;
ALTER TABLE intervencion ENABLE ROW LEVEL SECURITY;

-- Creating basic policies (For FastAPI to run bypass, or using service-role/anon credentials)
-- In a FastAPI architecture, the backend connects using a service_role key or high-privilege credentials,
-- and handles validation/auditing at the app layer, logging access in the `auditoria_acceso` table.
CREATE POLICY "Permitir todo al rol de servicio" ON usuario FOR ALL TO postgres USING (true);
CREATE POLICY "Permitir todo al rol de servicio" ON administrador FOR ALL TO postgres USING (true);
CREATE POLICY "Permitir todo al rol de servicio" ON psicologo FOR ALL TO postgres USING (true);
CREATE POLICY "Permitir todo al rol de servicio" ON estudiante FOR ALL TO postgres USING (true);
CREATE POLICY "Permitir todo al rol de servicio" ON consentimiento FOR ALL TO postgres USING (true);
CREATE POLICY "Permitir todo al rol de servicio" ON cuestionario FOR ALL TO postgres USING (true);
CREATE POLICY "Permitir todo al rol de servicio" ON pregunta FOR ALL TO postgres USING (true);
CREATE POLICY "Permitir todo al rol de servicio" ON evaluacion FOR ALL TO postgres USING (true);
CREATE POLICY "Permitir todo al rol de servicio" ON respuesta FOR ALL TO postgres USING (true);
CREATE POLICY "Permitir todo al rol de servicio" ON resultado FOR ALL TO postgres USING (true);
CREATE POLICY "Permitir todo al rol de servicio" ON derivacion_clinica FOR ALL TO postgres USING (true);
CREATE POLICY "Permitir todo al rol de servicio" ON auditoria_acceso FOR ALL TO postgres USING (true);
CREATE POLICY "Permitir todo al rol de servicio" ON vista_seudonimizada_ml FOR ALL TO postgres USING (true);
CREATE POLICY "Permitir todo al rol de servicio" ON chatbot_respuesta FOR ALL TO postgres USING (true);
CREATE POLICY "Permitir todo al rol de servicio" ON progreso_cuestionario FOR ALL TO postgres USING (true);
CREATE POLICY "Permitir todo al rol de servicio" ON intervencion FOR ALL TO postgres USING (true);

-- 6. Insert Mock Data for Cuestionario PHQ-9
INSERT INTO cuestionario (id_cuestionario, nombre, descripcion, estado, version, activo) VALUES
('b1990c88-e25f-4a87-8d07-7ff7bd8de693', 'Cuestionario sobre la Salud del Paciente (PHQ-9)', 'Herramienta de tamizaje para detectar y medir la gravedad de la depresión.', 'activo', '1.0', true)
ON CONFLICT (id_cuestionario) DO NOTHING;

INSERT INTO pregunta (id_pregunta, id_cuestionario, texto, orden, dimension, activa) VALUES
('d1111111-1111-1111-1111-111111111111', 'b1990c88-e25f-4a87-8d07-7ff7bd8de693', 'Poco interés o placer en hacer las cosas', 1, 'PHQ-9', true),
('d2222222-2222-2222-2222-222222222222', 'b1990c88-e25f-4a87-8d07-7ff7bd8de693', 'Se ha sentido decaído(a), deprimido(a) o sin esperanzas', 2, 'PHQ-9', true),
('d3333333-3333-3333-3333-333333333333', 'b1990c88-e25f-4a87-8d07-7ff7bd8de693', 'Dificultad para quedarse o permanecer dormido(a), o dormir demasiado', 3, 'PHQ-9', true),
('d4444444-4444-4444-4444-444444444444', 'b1990c88-e25f-4a87-8d07-7ff7bd8de693', 'Se ha sentido cansado(a) o con poca energía', 4, 'PHQ-9', true),
('d5555555-5555-5555-5555-555555555555', 'b1990c88-e25f-4a87-8d07-7ff7bd8de693', 'Sin apetito o ha comido en exceso', 5, 'PHQ-9', true),
('d6666666-6666-6666-6666-666666666666', 'b1990c88-e25f-4a87-8d07-7ff7bd8de693', 'Se ha sentido mal consigo mismo(a) o que es un fracaso', 6, 'PHQ-9', true),
('d7777777-7777-7777-7777-777777777777', 'b1990c88-e25f-4a87-8d07-7ff7bd8de693', 'Dificultad para concentrarse en ciertas actividades', 7, 'PHQ-9', true),
('d8888888-8888-8888-8888-888888888888', 'b1990c88-e25f-4a87-8d07-7ff7bd8de693', '¿Se ha movido o hablado tan lento que otras personas podrían notarlo?', 8, 'PHQ-9', true),
('d9999999-9999-9999-9999-999999999999', 'b1990c88-e25f-4a87-8d07-7ff7bd8de693', 'Pensamientos de que estaría mejor muerto(a) o de lastimarse de alguna manera', 9, 'PHQ-9 item suicida', true)
ON CONFLICT (id_pregunta) DO NOTHING;
