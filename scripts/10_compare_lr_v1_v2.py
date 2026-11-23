
from pathlib import Path
import numpy as np
from sklearn.preprocessing import FunctionTransformer
import pandas as pd

from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "reports" / "sql"

# ==============================
# 1. 读取V1、V2和送达时间
# ==============================
v1 = pd.read_csv(DATA / "11_order_features.csv")
v2 = pd.read_csv(DATA / "12_order_features_v2.csv")

delivery = pd.read_csv(
    DATA / "09_delivery_risk_labels.csv",
    usecols=["order_id", "order_delivered_customer_date"]
)

assert v1["order_id"].is_unique
assert v2["order_id"].is_unique
assert set(v1["order_id"]) == set(v2["order_id"])

def prepare(df):
    df = df.merge(
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
    assert df["purchase_at"].notna().all()
    assert df["delivered_at"].notna().all()

    return df

v1 = prepare(v1)
v2 = prepare(v2)

# ==============================
# 2. 定义两组特征
# ==============================
base_numeric = [
    "purchase_month",
    "purchase_weekday",
    "item_count",
    "item_value"
]

new_numeric = [
    "purchase_hour",
    "freight_value",
    "total_weight_g",
    "seller_count",
    "has_interstate_seller"
]

categorical = ["customer_state"]

# ==============================
# 3. 相同实验协议下训练与评估
# ==============================
def run_experiment(name, df, numeric, log_features=None):
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

    feature_cols = numeric + categorical

    # 指定哪些特征需要对数变换
    log_features = log_features or []

    # 其他数值特征保持原处理方式
    normal_numeric = [
        col for col in numeric
        if col not in log_features
    ]

    # 原始数值特征：缺失填充 + 标准化
    num_pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler())
    ])

    # 对数特征：缺失填充 + log1p + 标准化
    log_pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("log1p", FunctionTransformer(np.log1p)),
        ("scaler", StandardScaler())
    ])

    # 类别特征：保持原有方式
    cat_pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="most_frequent")),
        ("onehot", OneHotEncoder(handle_unknown="ignore"))
    ])

    preprocessor = ColumnTransformer([
        ("num", num_pipeline, normal_numeric),
        ("log", log_pipeline, log_features),
        ("cat", cat_pipeline, categorical)
    ])

    model = Pipeline([
        ("preprocessor", preprocessor),
        ("classifier", LogisticRegression(max_iter=2000))
    ])

    model.fit(
        train[feature_cols],
        train["is_late"]
    )

    # 仅使用验证集评估排序能力
    y_val = val["is_late"]
    y_prob = model.predict_proba(
        val[feature_cols]
    )[:, 1]

    ap = average_precision_score(y_val, y_prob)
    auc = roc_auc_score(y_val, y_prob)

    print(f"\n========== {name} ==========")
    print("训练样本:", len(train))
    print("验证样本:", len(val))
    print("特征字段数:", len(feature_cols))
    print(f"Validation AP: {ap:.4f}")
    print(f"Validation ROC-AUC: {auc:.4f}")

    return ap, auc


v1_ap, v1_auc = run_experiment(
    "V1",
    v1,
    base_numeric
)

v2_ap, v2_auc = run_experiment(
    "V2",
    v2,
    base_numeric + new_numeric
)

# ==============================
# 4. 输出对比结果
# ==============================
print("\n========== V1 / V2 对照 ==========")
print(f"AP:      {v1_ap:.4f} -> {v2_ap:.4f}")
print(f"ROC-AUC: {v1_auc:.4f} -> {v2_auc:.4f}")

print(f"AP变化:      {v2_ap - v1_ap:+.4f}")
print(f"ROC-AUC变化: {v2_auc - v1_auc:+.4f}")



# ==============================
# 5. 新增特征消融实验
# ==============================

experiments = [
    ("V1 + purchase_hour", ["purchase_hour"]),
    ("V1 + freight_value", ["freight_value"]),
    ("V1 + total_weight_g", ["total_weight_g"]),
    ("V1 + seller_count", ["seller_count"]),
    ("V1 + has_interstate_seller", ["has_interstate_seller"]),
]

results = []

for name, added_features in experiments:

    # 原来的4个数值特征 + 1个新增特征
    numeric = base_numeric + added_features

    # V2数据包含所有新增特征
    ap, auc = run_experiment(
        name,
        v2,
        numeric
    )

    results.append({
        "experiment": name,
        "AP": ap,
        "ROC_AUC": auc,
        "AP_change": ap - v1_ap,
        "AUC_change": auc - v1_auc
    })

# 汇总结果
results_df = pd.DataFrame(results)

print("\n========== 特征消融汇总 ==========")
print(
    results_df.sort_values(
        "AP",
        ascending=False
    ).to_string(index=False)
)



# ==============================
# 任务21：log1p特征变换实验
# ==============================

# 实验1：V1只对商品金额进行log1p变换
v1_log_ap, v1_log_auc = run_experiment(
    "V1 + log1p(item_value)",
    v1,
    base_numeric,
    log_features=["item_value"]
)

# 实验2：V2对三项右偏数值特征变换
v2_log_ap, v2_log_auc = run_experiment(
    "V2 + log1p(3 features)",
    v2,
    base_numeric + new_numeric,
    log_features=[
        "item_value",
        "freight_value",
        "total_weight_g"
    ]
)

print("\n========== log1p实验汇总 ==========")

print(f"V1 原始 AP: {v1_ap:.4f}")
print(f"V1 log AP:  {v1_log_ap:.4f}")
print(f"V1 原始 AUC: {v1_auc:.4f}")
print(f"V1 log AUC:  {v1_log_auc:.4f}")

print()

print(f"V2 原始 AP: {v2_ap:.4f}")
print(f"V2 log AP:  {v2_log_ap:.4f}")
print(f"V2 原始 AUC: {v2_auc:.4f}")
print(f"V2 log AUC:  {v2_log_auc:.4f}")
