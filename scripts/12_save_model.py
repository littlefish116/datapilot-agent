
from pathlib import Path
import json
import joblib
import pandas as pd
import sklearn

from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import OneHotEncoder
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import (
    average_precision_score,
    roc_auc_score
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "reports" / "sql"
MODEL_DIR = ROOT / "models"
MODEL_DIR.mkdir(exist_ok=True)

# 1. 加载数据
features = pd.read_csv(
    DATA / "11_order_features.csv"
)

delivery = pd.read_csv(
    DATA / "09_delivery_risk_labels.csv",
    usecols=["order_id", "order_delivered_customer_date"]
)

df = features.merge(
    delivery,
    on="order_id",
    validate="one_to_one"
)

df["purchase_at"] = pd.to_datetime(
    df["order_purchase_timestamp"]
)
df["delivered_at"] = pd.to_datetime(
    df["order_delivered_customer_date"]
)

# 2. 复用之前的实验协议
train = df[
    (df["purchase_at"] < "2018-05-01") &
    (df["delivered_at"] < "2018-05-01")
].copy()

val = df[
    (df["purchase_at"] >= "2018-05-01") &
    (df["purchase_at"] < "2018-07-01") &
    (df["delivered_at"] < "2018-07-01")
].copy()

assert len(train) == 68318
assert len(val) == 11149

numeric = [
    "purchase_month",
    "purchase_weekday",
    "item_count",
    "item_value"
]
categorical = ["customer_state"]
feature_cols = numeric + categorical

# 3. 构建与HGB-V1相同的模型
preprocessor = ColumnTransformer([
    (
        "num",
        SimpleImputer(strategy="median"),
        numeric
    ),
    (
        "cat",
        Pipeline([
            ("imputer", SimpleImputer(
                strategy="most_frequent"
            )),
            ("onehot", OneHotEncoder(
                handle_unknown="ignore",
                sparse_output=False
            ))
        ]),
        categorical
    )
])

model = Pipeline([
    ("preprocessor", preprocessor),
    ("classifier", HistGradientBoostingClassifier(
        max_iter=150,
        learning_rate=0.05,
        max_leaf_nodes=15,
        min_samples_leaf=50,
        l2_regularization=1.0,
        early_stopping=False,
        random_state=42
    ))
])

# 4. 训练
model.fit(
    train[feature_cols],
    train["is_late"]
)

# 5. 验证是否复现历史结果
val_prob = model.predict_proba(
    val[feature_cols]
)[:, 1]

ap = average_precision_score(val["is_late"], val_prob)
auc = roc_auc_score(val["is_late"], val_prob)

print(f"Validation AP: {ap:.4f}")
print(f"Validation ROC-AUC: {auc:.4f}")

# 检查是否接近原先的HGB-V1实验
assert abs(ap - 0.059414) < 0.001
assert abs(auc - 0.559259) < 0.001

# 6. 保存完整Pipeline
model_path = MODEL_DIR / "delivery_risk_hgb_v1.joblib"
joblib.dump(model, model_path)

# 保存模型元数据
metadata = {
    "model_name": "delivery_risk_hgb_v1",
    "features": feature_cols,
    "positive_label": 1,
    "train_samples": len(train),
    "validation_samples": len(val),
    "validation_ap": float(ap),
    "validation_roc_auc": float(auc),
    "sklearn_version": sklearn.__version__,
    "prediction_point": "after_checkout",
    "limitations": [
        "Historical delivered-order dataset",
        "Validation filtered by delivery maturity",
        "Uncalibrated risk scores",
        "Not validated for production deployment"
    ]
}

(MODEL_DIR / "metadata.json").write_text(
    json.dumps(metadata, ensure_ascii=False, indent=2),
    encoding="utf-8"
)

# 7. 重新加载并验证
loaded_model = joblib.load(model_path)

sample = val[feature_cols].iloc[[0]]
original_prob = model.predict_proba(sample)[0, 1]
loaded_prob = loaded_model.predict_proba(sample)[0, 1]

assert abs(original_prob - loaded_prob) < 1e-10

print("\n========== 模型保存成功 ==========")
print("模型路径:", model_path)
print("特征列表:", feature_cols)
print("原始模型预测:", round(original_prob, 6))
print("加载模型预测:", round(loaded_prob, 6))
print("模型重新加载验证通过！")
