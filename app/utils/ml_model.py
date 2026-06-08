import os
import joblib
import pandas as pd
import numpy as np
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, RobustScaler
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import AdaBoostClassifier
from xgboost import XGBClassifier

# Configuración del modelo y reproducibilidad
SEED_GLOBAL = 42
MODEL_FILENAME = "modelo_entrenado.joblib"
MODEL_PATH = os.path.join(os.path.dirname(__file__), MODEL_FILENAME)
DATASET_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data_depresion.xlsx"))

# Definición de variables del Bloque B
COLS_NUM = ["horas_sueno", "mspss_total"]
COLS_NOM = ["historia_salud_mental"]
COLS_ORD = ["calidad_sueno"]

ORDEN_SUENIO = ["Muy mala", "Mala", "Regular", "Buena", "Muy buena"]

def make_preprocessor():
    """Construye el ColumnTransformer de preprocesamiento de scikit-learn."""
    num_pipeline = Pipeline([
        ('imputer', SimpleImputer(strategy='median')),
        ('scaler',  RobustScaler()),
    ])

    cat_nom_pipeline = Pipeline([
        ('imputer', SimpleImputer(strategy='most_frequent')),
        ('onehot',  OneHotEncoder(drop='if_binary', sparse_output=False, handle_unknown='ignore')),
    ])

    cat_ord_pipeline = Pipeline([
        ('imputer', SimpleImputer(strategy='most_frequent')),
        ('ordinal', OrdinalEncoder(categories=[ORDEN_SUENIO],
                                   handle_unknown='use_encoded_value',
                                   unknown_value=-1)),
    ])

    preprocessor = ColumnTransformer([
        ('num', num_pipeline, COLS_NUM),
        ('cat_nom', cat_nom_pipeline, COLS_NOM),
        ('cat_ord', cat_ord_pipeline, COLS_ORD),
    ])
    
    return preprocessor

def get_initial_dataset(db=None):
    """Carga el dataset desde la tabla de base de datos vista_seudonimizada_ml."""
    from ..database import SessionLocal
    local_session = False
    if db is None:
        db = SessionLocal()
        local_session = True
    try:
        from ..models import VistaSeudonimizadaML
        records = db.query(VistaSeudonimizadaML).all()
        
        if not records:
            # Fallback to local Excel if database is completely empty
            if not os.path.exists(DATASET_PATH):
                raise FileNotFoundError(f"No se encontró el dataset de depresión en {DATASET_PATH}")
            df = pd.read_excel(DATASET_PATH)
            if 'phq9_total' in df.columns:
                df['target_binario'] = (df['phq9_total'] >= 10).astype(int)
            else:
                categorias_positivas = ['Moderada', 'Moderadamente severa', 'Severa']
                df['target_binario'] = df['depresion_objetivo'].isin(categorias_positivas).astype(int)
            return df
        
        data = []
        for r in records:
            if r.horas_sueno is not None and r.mspss_total is not None and r.historia_salud_mental is not None and r.calidad_sueno is not None:
                # Construct target binary from phq9 sum (leakage-free target) if possible
                q_sum = 0
                has_qs = False
                for q_idx in range(1, 10):
                    val = getattr(r, f"q{q_idx}")
                    if val is not None:
                        q_sum += val
                        has_qs = True
                
                if has_qs:
                    target = 1 if q_sum >= 10 else 0
                else:
                    categorias_positivas = ['Moderada', 'Moderadamente severa', 'Severa', 'riesgo_depresion', 'alto_riesgo']
                    target = 1 if r.prediction in categorias_positivas else 0
                
                data.append({
                    "horas_sueno": r.horas_sueno,
                    "mspss_total": r.mspss_total,
                    "historia_salud_mental": r.historia_salud_mental,
                    "calidad_sueno": r.calidad_sueno,
                    "target_binario": target
                })
        
        if not data:
            # If records exist but all have null features, load fallback Excel
            df = pd.read_excel(DATASET_PATH)
            df['target_binario'] = (df['phq9_total'] >= 10).astype(int) if 'phq9_total' in df.columns else df['depresion_objetivo'].isin(['Moderada', 'Moderadamente severa', 'Severa']).astype(int)
            return df

        return pd.DataFrame(data)
    finally:
        if local_session:
            db.close()

def get_classifiers():
    return {
        "LogisticRegression": LogisticRegression(
            C=1.0, 
            random_state=SEED_GLOBAL, 
            max_iter=1000
        ),
        "DecisionTree": DecisionTreeClassifier(
            max_depth=5, 
            min_samples_leaf=10, 
            random_state=SEED_GLOBAL
        ),
        "RandomForest": RandomForestClassifier(
            n_estimators=200,
            max_depth=10,
            min_samples_leaf=5,
            class_weight='balanced',
            random_state=SEED_GLOBAL,
            n_jobs=-1
        ),
        "AdaBoost": AdaBoostClassifier(
            n_estimators=100,
            learning_rate=1.0,
            random_state=SEED_GLOBAL
        ),
        "XGBoost": XGBClassifier(
            n_estimators=200,
            max_depth=3,
            learning_rate=0.1,
            random_state=SEED_GLOBAL,
            eval_metric='logloss'
        )
    }

def train_and_compare_models(df=None, db=None):
    """
    Entrena y compara 5 modelos clasificadores. Selecciona el modelo ganador basado
    en balanced accuracy y lo guarda como el modelo de predicción activo.
    """
    if df is None:
        df = get_initial_dataset(db)
        
    X = df[COLS_NUM + COLS_NOM + COLS_ORD].copy()
    y = df['target_binario']
    
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, balanced_accuracy_score
    
    # 80-20 Stratified Train-Validation Split
    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.2, random_state=SEED_GLOBAL, stratify=y
    )
    
    classifiers = get_classifiers()
    results = {}
    pipelines = {}
    
    for name, clf in classifiers.items():
        preprocessor = make_preprocessor()
        pipeline = Pipeline([
            ('preprocessor', preprocessor),
            ('classifier', clf)
        ])
        
        # Fit on train split
        pipeline.fit(X_train, y_train)
        
        # Predict on val split
        y_pred = pipeline.predict(X_val)
        
        # Calculate metrics
        acc = float(accuracy_score(y_val, y_pred))
        prec = float(precision_score(y_val, y_pred, zero_division=0))
        rec = float(recall_score(y_val, y_pred, zero_division=0))
        f1 = float(f1_score(y_val, y_pred, zero_division=0))
        bal_acc = float(balanced_accuracy_score(y_val, y_pred))
        
        results[name] = {
            "model_name": name,
            "accuracy": acc,
            "precision": prec,
            "recall": rec,
            "f1_score": f1,
            "balanced_accuracy": bal_acc
        }
        
        # Fit pipeline on all data for deployment
        full_pipeline = Pipeline([
            ('preprocessor', make_preprocessor()),
            ('classifier', clf)
        ])
        full_pipeline.fit(X, y)
        pipelines[name] = full_pipeline
        
    # Select winner by balanced accuracy
    winner_name = max(results, key=lambda k: results[k]["balanced_accuracy"])
    
    # Save winning model
    joblib.dump(pipelines[winner_name], MODEL_PATH)
    print(f"OK: Modelo ML ganador '{winner_name}' guardado en: {MODEL_PATH}")
    
    # Add winner flag
    for name in results:
        results[name]["is_winner"] = (name == winner_name)
        
    return results, winner_name

def train_and_save_model(df=None):
    """Wrapper compatible para el pipeline antiguo de entrenamiento."""
    results, winner_name = train_and_compare_models(df)
    return results

def load_trained_model():
    """Carga el modelo entrenado desde el archivo serializado, entrenándolo primero si no existe."""
    if not os.path.exists(MODEL_PATH):
        print(f"Modelo no encontrado en {MODEL_PATH}. Iniciando entrenamiento inicial...")
        return train_and_save_model()
    return joblib.load(MODEL_PATH)

def predict_depression_risk(horas_sueno: float, mspss_total: int, historia_salud_mental: str, calidad_sueno: str):
    """
    Realiza la predicción de riesgo de depresión (PHQ-9 >= 10) utilizando el modelo RandomForest.
    
    Retorna:
        prediction (int): 1 si predice riesgo alto (depresión moderada-severa), 0 en caso contrario.
        probability (float): Probabilidad en porcentaje (0.0 a 100.0) de pertenecer al grupo de riesgo.
    """
    pipeline = load_trained_model()
    
    # Crear un DataFrame con una sola fila para la predicción
    input_data = pd.DataFrame([{
        "horas_sueno": float(horas_sueno),
        "mspss_total": int(mspss_total),
        "historia_salud_mental": str(historia_salud_mental),
        "calidad_sueno": str(calidad_sueno)
    }])
    
    prediction = int(pipeline.predict(input_data)[0])
    probabilities = pipeline.predict_proba(input_data)[0]
    
    # Probabilidad del target binario = 1
    probability = float(round(probabilities[1] * 100, 2))
    
    return prediction, probability
