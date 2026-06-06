import os
import joblib
import pandas as pd
import numpy as np
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, RobustScaler
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.ensemble import RandomForestClassifier

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

def get_initial_dataset():
    """Carga el dataset de excel de la raíz y define el target binario."""
    if not os.path.exists(DATASET_PATH):
        raise FileNotFoundError(f"No se encontró el dataset de depresión en {DATASET_PATH}")
    
    df = pd.read_excel(DATASET_PATH)
    
    # Construcción del target binario PHQ-9 >= 10
    if 'phq9_total' in df.columns:
        df['target_binario'] = (df['phq9_total'] >= 10).astype(int)
    else:
        categorias_positivas = ['Moderada', 'Moderadamente severa', 'Severa']
        df['target_binario'] = df['depresion_objetivo'].isin(categorias_positivas).astype(int)
        
    return df

def train_and_save_model(df=None):
    """
    Entrena el pipeline de RandomForest con los hiperparámetros ganadores 
    del notebook sobre el DataFrame especificado (o el de excel por defecto) y lo serializa.
    """
    if df is None:
        df = get_initial_dataset()
        
    X = df[COLS_NUM + COLS_NOM + COLS_ORD].copy()
    y = df['target_binario']
    
    preprocessor = make_preprocessor()
    
    # Hiperparámetros óptimos del RandomForest según el notebook
    clf = RandomForestClassifier(
        n_estimators=200,
        max_depth=10,
        min_samples_leaf=5,
        class_weight='balanced',
        random_state=SEED_GLOBAL,
        n_jobs=-1
    )
    
    pipeline = Pipeline([
        ('preprocessor', preprocessor),
        ('classifier', clf)
    ])
    
    pipeline.fit(X, y)
    
    # Guardar modelo entrenado
    joblib.dump(pipeline, MODEL_PATH)
    print(f"OK: Modelo ML entrenado y guardado en: {MODEL_PATH}")
    return pipeline

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
