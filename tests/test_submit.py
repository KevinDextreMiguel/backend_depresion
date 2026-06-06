from fastapi.testclient import TestClient
import sys
import os

# Ensure package import path
# Ensure package import path
ROOT = os.path.dirname(os.path.dirname(__file__))  # points to .../backend
sys.path.insert(0, ROOT)

from app.main import app
from app.database import get_db, engine
from app.models import DerivacionClinica, Resultado, AuditoriaAcceso

# NOTE: Tests will run against the configured Supabase/Postgres environment.
from sqlalchemy.orm import Session

client = TestClient(app)

def run_test():
    payload = {
        "responses": [0,1,0,0,2,1,0,0,2],
        "test_user_id": "426d542a-384f-440c-85d0-c30a7f7b47db",
        "timestamp": "2026-05-21T12:00:00Z"
    }

    print("Sending POST to submit-questionnaire...")
    resp = client.post("/make-server-d427d5bf/submit-questionnaire", json=payload)
    print("Status code:", resp.status_code)
    try:
        print("Response JSON:", resp.json())
    except Exception as e:
        print("Response content:", resp.text)

    # Inspect DB for derivations and audit entries
    with Session(bind=engine) as db:  # type: ignore
        derivs = db.query(DerivacionClinica).limit(5).all()
        audits = db.query(AuditoriaAcceso).limit(5).all()

        print(f"Found {len(derivs)} derivation(s) (latest up to 5):")
        for d in derivs:
            print(f"- id={d.id_derivacion}, prioridad={d.nivel_prioridad}, estado={d.estado}, fecha={d.fecha_derivacion}")

        print(f"Found {len(audits)} audit(s) (latest up to 5):")
        for a in audits:
            print(f"- id={a.id_auditoria}, accion={a.accion}, tabla={a.tabla_objetivo}, detalle={a.detalle}")

if __name__ == '__main__':
    run_test()
