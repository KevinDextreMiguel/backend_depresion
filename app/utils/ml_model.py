"""
Modelo de discriminación concurrente de PHQ-9 >= 10 (paper_notebook / NB01).

Diseño (idéntico a NB01_Modelado_Discriminacion):
- Outcome: PHQ-9 >= 10.
- Predictores fijados a priori: sleep_hours, sleep_quality (1-5), mspss_mean (1-7).
- Modelo primario (desplegado): Logistic Regression L2, imputación por mediana + StandardScaler.
  C se ajusta por GridSearchCV (5-fold estratificado, scoring ROC-AUC).
- Comparador secundario (solo reporte): Random Forest.
- Evaluación: nested 5-fold CV con predicciones OOF; métricas ROC-AUC, AP y Brier.
- Datos: snapshot analítico P1-P4 (MSPSS 1-7). P5 (MSPSS 1-4) no se usa.

El modelo NO es un instrumento diagnóstico; la probabilidad es una salida de tamizaje.
"""

import json
import os

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import GridSearchCV, StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

# ---------------------------------------------------------------------------
# Configuración (NB01)
# ---------------------------------------------------------------------------
SEED = 42
N_OUTER = 5
N_INNER = 5

PRIMARY_MODEL_NAME = "LogisticRegression"
COMPARATOR_MODEL_NAME = "RandomForest"

FEATURES = ["sleep_hours", "sleep_quality", "mspss_mean"]
TARGET = "phq9_positive"
PHQ9_CUTOFF = 10

# Umbral de clasificación por defecto de predict(). El paper no define un punto
# operativo; la salida principal es la probabilidad.
DECISION_THRESHOLD = 0.5

SLEEP_QUALITY_MAP = {
    "muy mala": 1,
    "mala": 2,
    "regular": 3,
    "buena": 4,
    "muy buena": 5,
}

UTILS_DIR = os.path.dirname(__file__)
MODEL_FILENAME = "modelo_paper_lr.joblib"
MODEL_PATH = os.path.join(UTILS_DIR, MODEL_FILENAME)
DATASET_PATH = os.path.join(UTILS_DIR, "data", "nb01_analytic_dataset.csv")
DATASET_SHA256 = "d217f5b7951468d8e6699e9fd95f8a5acc79117627430da5ee670954443e2a16"


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------
def make_lr_pipeline():
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        # Regularización L2 (default de sklearn; `penalty=` está deprecado desde 1.8).
        ("classifier", LogisticRegression(
            solver="lbfgs",
            max_iter=2000,
            class_weight=None,
            random_state=SEED,
        )),
    ])


def make_rf_pipeline():
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("classifier", RandomForestClassifier(
            n_estimators=500,
            class_weight=None,
            random_state=SEED,
            n_jobs=1,
        )),
    ])


MODEL_SPECS = {
    PRIMARY_MODEL_NAME: {
        "make_pipeline": make_lr_pipeline,
        "grid": {"classifier__C": [0.01, 0.1, 1.0, 10.0, 100.0]},
    },
    COMPARATOR_MODEL_NAME: {
        "make_pipeline": make_rf_pipeline,
        "grid": {
            "classifier__max_depth": [3, 5, None],
            "classifier__min_samples_leaf": [3, 5, 10],
            "classifier__max_features": ["sqrt", 1.0],
        },
    },
}


def _grid_search(spec, inner_seed):
    return GridSearchCV(
        estimator=spec["make_pipeline"](),
        param_grid=spec["grid"],
        scoring="roc_auc",
        cv=StratifiedKFold(n_splits=N_INNER, shuffle=True, random_state=inner_seed),
        refit=True,
        n_jobs=-1,
    )


# ---------------------------------------------------------------------------
# Transformación de entradas de la app a las variables del paper
# ---------------------------------------------------------------------------
def encode_sleep_quality(value) -> float:
    if value is None:
        return np.nan
    if isinstance(value, (int, float)) and not pd.isna(value):
        return float(value) if 1 <= value <= 5 else np.nan
    return float(SLEEP_QUALITY_MAP.get(str(value).strip().lower(), np.nan))


def mspss_mean_from_items(items) -> float:
    """Media de los 12 ítems MSPSS (escala 1-7). Devuelve NaN si está incompleto o fuera de rango."""
    if items is None or len(items) != 12:
        return np.nan
    values = [float(v) for v in items]
    if not all(1 <= v <= 7 for v in values):
        return np.nan
    return float(np.mean(values))


# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------
def load_paper_dataset() -> pd.DataFrame:
    """Snapshot analítico de NB01 (P1-P4, MSPSS 1-7)."""
    if not os.path.exists(DATASET_PATH):
        raise FileNotFoundError(f"No se encontró el dataset analítico del paper en {DATASET_PATH}")
    df = pd.read_csv(DATASET_PATH)
    return df[FEATURES + [TARGET]].copy()


def load_new_records(db) -> pd.DataFrame:
    """
    Registros nuevos de vista_seudonimizada_ml capturados con el modelo del paper
    (MSPSS 1-7). Los registros previos usaban MSPSS 1-4 y no son comparables.
    """
    from ..models import VistaSeudonimizadaML

    records = (
        db.query(VistaSeudonimizadaML)
        .filter(VistaSeudonimizadaML.origen_modelo.like(f"{PRIMARY_MODEL_NAME}%"))
        .all()
    )

    rows = []
    for r in records:
        qs = [getattr(r, f"q{i}") for i in range(1, 10)]
        if any(q is None for q in qs) or r.mspss_total is None or r.calidad_sueno is None:
            continue
        rows.append({
            "sleep_hours": r.horas_sueno if r.horas_sueno is not None else np.nan,
            "sleep_quality": encode_sleep_quality(r.calidad_sueno),
            "mspss_mean": r.mspss_total / 12.0,
            TARGET: int(sum(qs) >= PHQ9_CUTOFF),
        })

    df = pd.DataFrame(rows, columns=FEATURES + [TARGET])
    return df.dropna(subset=["sleep_quality", "mspss_mean"])


def get_initial_dataset(db=None) -> pd.DataFrame:
    """Dataset de entrenamiento: snapshot del paper + registros nuevos compatibles (si hay BD)."""
    df = load_paper_dataset()
    if db is None:
        return df
    try:
        new = load_new_records(db)
    except Exception as exc:
        print(f"[ml_model] No se pudieron leer registros nuevos de la BD: {exc}")
        return df
    if new.empty:
        return df
    return pd.concat([df, new], ignore_index=True)


# ---------------------------------------------------------------------------
# Entrenamiento y evaluación
# ---------------------------------------------------------------------------
def _nested_oof(spec, X, y):
    outer_cv = StratifiedKFold(n_splits=N_OUTER, shuffle=True, random_state=SEED)
    oof = np.full(len(y), np.nan)
    for fold, (train_idx, test_idx) in enumerate(outer_cv.split(X, y), start=1):
        search = _grid_search(spec, inner_seed=SEED + fold)
        search.fit(X.iloc[train_idx], y.iloc[train_idx])
        oof[test_idx] = search.predict_proba(X.iloc[test_idx])[:, 1]
    return oof


def _metrics(y_true, prob):
    pred = (prob >= DECISION_THRESHOLD).astype(int)
    return {
        "roc_auc": float(roc_auc_score(y_true, prob)),
        "average_precision": float(average_precision_score(y_true, prob)),
        "brier": float(brier_score_loss(y_true, prob)),
        "accuracy": float(accuracy_score(y_true, pred)),
        "precision": float(precision_score(y_true, pred, zero_division=0)),
        "recall": float(recall_score(y_true, pred, zero_division=0)),
        "f1_score": float(f1_score(y_true, pred, zero_division=0)),
    }


def train_and_compare_models(df=None, db=None):
    """
    Reproduce NB01: nested CV (OOF) para LR y RF, y despliega la Logistic Regression
    (modelo primario del paper) ajustada sobre todo el dataset con C elegido por CV.

    Retorna (results: dict[nombre -> métricas], winner_name).
    `winner_name` es siempre el modelo primario: el paper no selecciona modelo por métrica.
    """
    if df is None:
        df = get_initial_dataset(db)

    X = df[FEATURES].astype(float)
    y = df[TARGET].astype(int)

    results = {}
    for name, spec in MODEL_SPECS.items():
        oof = _nested_oof(spec, X, y)
        results[name] = {"model_name": name, **_metrics(y, oof)}

    final_search = _grid_search(MODEL_SPECS[PRIMARY_MODEL_NAME], inner_seed=SEED)
    final_search.fit(X, y)

    artifact = {
        "pipeline": final_search.best_estimator_,
        "model_name": PRIMARY_MODEL_NAME,
        "features": FEATURES,
        "target": f"PHQ-9 >= {PHQ9_CUTOFF}",
        "best_params": final_search.best_params_,
        "decision_threshold": DECISION_THRESHOLD,
        "n_train": int(len(y)),
        "prevalence": float(y.mean()),
        "oof_metrics": results,
        "paper_dataset_sha256": DATASET_SHA256,
    }
    joblib.dump(artifact, MODEL_PATH)
    print(
        f"OK: {PRIMARY_MODEL_NAME} (paper) guardado en {MODEL_PATH} | "
        f"best={json.dumps(final_search.best_params_)} | n={len(y)} | "
        f"OOF AUC LR={results[PRIMARY_MODEL_NAME]['roc_auc']:.3f} "
        f"RF={results[COMPARATOR_MODEL_NAME]['roc_auc']:.3f}"
    )

    for name in results:
        results[name]["is_winner"] = name == PRIMARY_MODEL_NAME

    return results, PRIMARY_MODEL_NAME


_cached_artifact = None
_cached_mtime = None


def load_trained_model():
    """Carga el artefacto del modelo; lo entrena con el dataset del paper si no existe."""
    global _cached_artifact, _cached_mtime
    if not os.path.exists(MODEL_PATH):
        print(f"Modelo no encontrado en {MODEL_PATH}. Entrenando con el dataset del paper...")
        train_and_compare_models(load_paper_dataset())
    mtime = os.path.getmtime(MODEL_PATH)
    if _cached_artifact is None or mtime != _cached_mtime:
        _cached_artifact = joblib.load(MODEL_PATH)
        _cached_mtime = mtime
    return _cached_artifact


# ---------------------------------------------------------------------------
# Inferencia
# ---------------------------------------------------------------------------
def predict_depression_risk(horas_sueno: float, calidad_sueno: str, mspss_respuestas):
    """
    Probabilidad de PHQ-9 >= 10 con la Logistic Regression del paper.

    Args:
        horas_sueno: horas de sueño (0-24). Si falta, el pipeline imputa la mediana de entrenamiento.
        calidad_sueno: "Muy mala" | "Mala" | "Regular" | "Buena" | "Muy buena".
        mspss_respuestas: 12 respuestas MSPSS en escala 1-7.

    Returns:
        (prediction, probability, mspss_mean)
        prediction: 1 si prob >= DECISION_THRESHOLD.
        probability: porcentaje 0-100.
        mspss_mean: media MSPSS usada como predictor.
    """
    sleep_quality = encode_sleep_quality(calidad_sueno)
    if pd.isna(sleep_quality):
        raise ValueError(f"calidad_sueno no reconocida: {calidad_sueno!r}")

    mspss_mean = mspss_mean_from_items(mspss_respuestas)
    if pd.isna(mspss_mean):
        raise ValueError("MSPSS debe tener 12 respuestas en escala 1-7.")

    artifact = load_trained_model()
    pipeline = artifact["pipeline"]
    threshold = artifact.get("decision_threshold", DECISION_THRESHOLD)

    X = pd.DataFrame([{
        "sleep_hours": float(horas_sueno) if horas_sueno is not None else np.nan,
        "sleep_quality": sleep_quality,
        "mspss_mean": mspss_mean,
    }], columns=FEATURES)

    prob = float(pipeline.predict_proba(X)[0, 1])
    prediction = int(prob >= threshold)
    return prediction, round(prob * 100, 2), round(mspss_mean, 4)
