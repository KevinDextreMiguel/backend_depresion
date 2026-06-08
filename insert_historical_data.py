import os
import sys
import uuid
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

load_dotenv()

db_url = os.getenv("DATABASE_URL")
if not db_url:
    from app.db_resolver import resolve_database_url
    db_url = resolve_database_url()

print("Connecting to:", db_url)
engine = create_engine(db_url)

# 1. Alter table constraints in Supabase
alter_queries = [
    "ALTER TABLE vista_seudonimizada_ml ALTER COLUMN id_evaluacion DROP NOT NULL;",
    "ALTER TABLE vista_seudonimizada_ml ALTER COLUMN id_estudiante DROP NOT NULL;",
    "ALTER TABLE vista_seudonimizada_ml ALTER COLUMN id_estudiante_hash DROP NOT NULL;",
    "ALTER TABLE vista_seudonimizada_ml ALTER COLUMN genero TYPE VARCHAR(100);",
    "ALTER TABLE estudiante ALTER COLUMN genero TYPE VARCHAR(100);"
]

with engine.begin() as conn:
    print("Altering columns...")
    for q in alter_queries:
        try:
            conn.execute(text(q))
            print(f"Executed: {q}")
        except Exception as ex:
            print(f"Error altering column: {ex}")

# 2. Check if historical data has already been inserted
with engine.connect() as conn:
    res = conn.execute(text("SELECT count(*) FROM vista_seudonimizada_ml WHERE origen_modelo = 'historico'"))
    count = res.scalar()
    print(f"Current historical records: {count}")
    if count > 0:
        print("Historical data already exists. Skipping insertion to avoid duplication.")
        sys.exit(0)

# 3. Read Excel data
excel_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data_depresion.xlsx"))
print(f"Reading Excel from: {excel_path}")
df = pd.read_excel(excel_path)

# 4. Insert rows
rows_to_insert = []
for idx, row in df.iterrows():
    # Map fields
    row_data = {
        "id_registro": str(uuid.uuid4()),
        "id_evaluacion": None,
        "id_estudiante": None,
        "id_estudiante_hash": "HISTORICO",
        "rango_edad": None,
        "edad": int(row["edad"]) if not pd.isna(row["edad"]) else None,
        "genero": str(row["genero"]) if not pd.isna(row["genero"]) else None,
        "carrera_area": str(row["facultad"]) if not pd.isna(row["facultad"]) else None,
        "universidad": str(row["tipo_universidad"]) if not pd.isna(row["tipo_universidad"]) else None,
        "q1": int(row["phq9_1"]) if not pd.isna(row["phq9_1"]) else None,
        "q2": int(row["phq9_2"]) if not pd.isna(row["phq9_2"]) else None,
        "q3": int(row["phq9_3"]) if not pd.isna(row["phq9_3"]) else None,
        "q4": int(row["phq9_4"]) if not pd.isna(row["phq9_4"]) else None,
        "q5": int(row["phq9_5"]) if not pd.isna(row["phq9_5"]) else None,
        "q6": int(row["phq9_6"]) if not pd.isna(row["phq9_6"]) else None,
        "q7": int(row["phq9_7"]) if not pd.isna(row["phq9_7"]) else None,
        "q8": int(row["phq9_8"]) if not pd.isna(row["phq9_8"]) else None,
        "q9": int(row["phq9_9"]) if not pd.isna(row["phq9_9"]) else None,
        "prediction": str(row["depresion_objetivo"]) if not pd.isna(row["depresion_objetivo"]) else None,
        "origen_modelo": "historico",
        "note": "Datos históricos cargados desde data_depresion.xlsx",
        "horas_sueno": float(row["horas_sueno"]) if not pd.isna(row["horas_sueno"]) else None,
        "calidad_sueno": str(row["calidad_sueno"]) if not pd.isna(row["calidad_sueno"]) else None,
        "historia_salud_mental": str(row["historia_salud_mental"]) if not pd.isna(row["historia_salud_mental"]) else None,
        "mspss_total": int(row["mspss_total"]) if not pd.isna(row["mspss_total"]) else None,
        "promedio_ponderado": float(row["promedio_ponderado"]) if not pd.isna(row["promedio_ponderado"]) else None,
        "ciclo": str(row["ciclo"]) if not pd.isna(row["ciclo"]) else None
    }
    rows_to_insert.append(row_data)

# 5. Batch insert using sqlalchemy
insert_query = text("""
    INSERT INTO vista_seudonimizada_ml (
        id_registro, id_evaluacion, id_estudiante, id_estudiante_hash, rango_edad, edad, genero,
        carrera_area, universidad, q1, q2, q3, q4, q5, q6, q7, q8, q9, prediction,
        origen_modelo, note, horas_sueno, calidad_sueno, historia_salud_mental, mspss_total,
        promedio_ponderado, ciclo
    ) VALUES (
        :id_registro, :id_evaluacion, :id_estudiante, :id_estudiante_hash, :rango_edad, :edad, :genero,
        :carrera_area, :universidad, :q1, :q2, :q3, :q4, :q5, :q6, :q7, :q8, :q9, :prediction,
        :origen_modelo, :note, :horas_sueno, :calidad_sueno, :historia_salud_mental, :mspss_total,
        :promedio_ponderado, :ciclo
    )
""")

with engine.begin() as conn:
    print(f"Inserting {len(rows_to_insert)} records...")
    for idx, row in enumerate(rows_to_insert):
        conn.execute(insert_query, row)
        if (idx + 1) % 50 == 0:
            print(f"Inserted {idx + 1} records...")
    print("All records inserted successfully!")

print("Verification check:")
with engine.connect() as conn:
    res = conn.execute(text("SELECT count(*) FROM vista_seudonimizada_ml WHERE origen_modelo = 'historico'"))
    print("Inserted count in DB:", res.scalar())
print("Finished script execution.")
