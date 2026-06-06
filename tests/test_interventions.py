from fastapi.testclient import TestClient
import sys
import os
import uuid
from datetime import datetime

# Ensure package import path
ROOT = os.path.dirname(os.path.dirname(__file__))  # points to .../backend
sys.path.insert(0, ROOT)

from app.main import app
from app.database import get_db, engine
from app.models import Usuario, Estudiante, Psicologo, Intervencion
from sqlalchemy.orm import Session

client = TestClient(app)

def run_tests():
    print("==================================================")
    print("RUNNING AUTOMATED TESTS FOR CLINICAL INTERVENTIONS (HU0027)")
    print("==================================================")

    # 1. Setup Mock User, Student, and Psychologist in Database
    with Session(bind=engine) as db:
        # Create a test student user
        student_user_id = uuid.uuid4()
        student_user = Usuario(
            id_usuario=student_user_id,
            nombre=b"Estudiante Test",  # Sensible data representation
            rol="estudiante",
            activo=True
        )
        db.add(student_user)

        estudiante = Estudiante(
            id_usuario=student_user_id,
            edad=21,
            carrera="Ingenieria de Software",
            universidad="UPC",
            activo=True
        )
        db.add(estudiante)

        # Create a test psychologist user
        psico_user_id = uuid.uuid4()
        psico_user = Usuario(
            id_usuario=psico_user_id,
            nombre=b"Psicologo Test",
            rol="psicologo",
            activo=True
        )
        db.add(psico_user)

        psicologo = Psicologo(
            id_psicologo=uuid.uuid4(),
            id_usuario=psico_user_id,
            especialidad="Clinica",
            activo=True
        )
        db.add(psicologo)
        db.commit()

        # Track keys for our request payloads
        anon_student_id = f"#{estudiante.id_estudiante.hex[:6].upper()}"
        psico_jwt_id = str(psico_user_id)

    # Helper mock header for require_role
    # Since auth uses Depends(require_role), let's override or use our mock header if auth mode allows it,
    # or override the dependency require_role to return a mock psychologist user.
    # Let's override require_role in app dependency overrides!
    from app.security import require_role
    
    def override_require_role(roles):
        def dependency():
            return {
                "id": psico_jwt_id,
                "email": "psico@test.com",
                "role": "psicologo"
            }
        return dependency

    app.dependency_overrides[require_role] = override_require_role

    try:
        # Scenario 3: Campos obligatorios (Missing or empty description/type)
        print("\n[TEST SCENARIO 3] campos obligatorios...")
        payload_missing = {
            "tipo_intervencion": "",
            "descripcion": "Descripción de prueba",
            "fecha_intervencion": datetime.utcnow().isoformat() + "Z"
        }
        resp = client.post(
            f"/make-server-d427d5bf/student-history/{anon_student_id}/interventions",
            json=payload_missing
        )
        print("Response Code (should be 400 or 422):", resp.status_code)
        assert resp.status_code in (400, 422), f"Expected 400 or 422, got {resp.status_code}"
        print("SUCCESS: Missing fields correctly validation rejected.")

        # Scenario 1: Registro exitoso
        print("\n[TEST SCENARIO 1] registro exitoso...")
        payload_success = {
            "tipo_intervencion": "Terapia Individual",
            "descripcion": "Intervención clínica de contención emocional exitosa.",
            "fecha_intervencion": datetime.utcnow().isoformat() + "Z"
        }
        resp = client.post(
            f"/make-server-d427d5bf/student-history/{anon_student_id}/interventions",
            json=payload_success
        )
        print("Response Code (should be 201):", resp.status_code)
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}"
        data = resp.json()
        intervention_id = data["id_intervencion"]
        print("SUCCESS: Registered intervention successfully. ID:", intervention_id)

        # Retrieve interventions list
        print("\n[TEST LISTING] fetching registered interventions...")
        resp = client.get(f"/make-server-d427d5bf/student-history/{anon_student_id}/interventions")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        interventions = resp.json()
        assert len(interventions) >= 1, "Expected at least 1 intervention in list"
        print("SUCCESS: Listed interventions successfully. Count:", len(interventions))

        # Scenario 2: Edición de intervención
        print("\n[TEST SCENARIO 2] edición de intervención...")
        payload_update = {
            "tipo_intervencion": "Terapia Cognitivo-Conductual",
            "descripcion": "Descripción actualizada de la intervención.",
            "fecha_intervencion": datetime.utcnow().isoformat() + "Z"
        }
        resp = client.put(
            f"/make-server-d427d5bf/interventions/{intervention_id}",
            json=payload_update
        )
        print("Response Code (should be 200):", resp.status_code)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        updated_data = resp.json()
        assert updated_data["tipo_intervencion"] == "Terapia Cognitivo-Conductual", "Mismatch updated type"
        assert updated_data["descripcion"] == "Descripción actualizada de la intervención.", "Mismatch updated desc"
        print("SUCCESS: Updated intervention successfully.")

        print("\n==================================================")
        print("ALL AUTOMATED TESTS PASSED SUCCESSFULLY! ✅")
        print("==================================================")

    finally:
        # Clean up dependency overrides
        app.dependency_overrides.clear()
        
        # Clean up database records
        with Session(bind=engine) as db:
            db.query(Intervencion).filter(Intervencion.id_estudiante == estudiante.id_estudiante).delete()
            db.query(Estudiante).filter(Estudiante.id_estudiante == estudiante.id_estudiante).delete()
            db.query(Psicologo).filter(Psicologo.id_psicologo == psicologo.id_psicologo).delete()
            db.query(Usuario).filter(Usuario.id_usuario.in_([student_user_id, psico_user_id])).delete()
            db.commit()

if __name__ == '__main__':
    run_tests()
