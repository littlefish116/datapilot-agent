
from pathlib import Path
import pandas as pd

from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import OneHotEncoder
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "reports" / "sql"

# 1. 加载数据
v1 = pd.read_csv(DATA / "11_order_features.csv")
v2 = pd.read_csv(DATA / "12_order_features_v2.csv")

delivery = pd.read_csv(
    DATA / "09_delivery_risk_labels.csv",
    usecols=["order_id", "order_delivered_customer_date"]
)

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
    return df

v1 = prepare(v1)
v2 = prepare(v2)

# 2. 定义特征
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

# 3. 定义实验函数
def run_experiment(name, df, numeric):

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

    features = numeric + categorical

    # 数值特征：缺失值填充
    num_pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="median"))
    ])

    # 类别特征：独热编码，输出稠密矩阵
    cat_pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="most_frequent")),
        ("onehot", OneHotEncoder(
            handle_unknown="ignore",
            sparse_output=False
        ))
    ])

    preprocessor = ColumnTransformer([
        ("num", num_pipeline, numeric),
        ("cat", cat_pipeline, categorical)
    ])

    # 梯度提升树
    classifier = HistGradientBoostingClassifier(
        max_iter=150,
        learning_rate=0.05,
        max_leaf_nodes=15,
        min_samples_leaf=50,
        l2_regularization=1.0,
        early_stopping=False,
        random_state=42
    )

    model = Pipeline([
        ("preprocessor", preprocessor),
        ("classifier", classifier)
    ])

    model.fit(
        train[features],
        train["is_late"]
    )

    y_val = val["is_late"]
    y_prob = model.predict_proba(
        val[features]
    )[:, 1]

    ap = average_precision_score(y_val, y_prob)
    auc = roc_auc_score(y_val, y_prob)

    print(f"\n========== {name} ==========")
    print("训练样本:", len(train))
    print("验证样本:", len(val))
    print(f"Validation AP: {ap:.4f}")
    print(f"Validation ROC-AUC: {auc:.4f}")

    return ap, auc


# 4. 两组实验
hgb_v1 = run_experiment(
    "HGB-V1",
    v1,
    base_numeric
)

hgb_v2 = run_experiment(
    "HGB-V2",
    v2,
    base_numeric + new_numeric
)

# 5. 汇总
print("\n========== 模型对照 ==========")

results = pd.DataFrame([
    {"model": "LR-V1", "AP": 0.0545, "ROC_AUC": 0.6163},
    {"model": "LR-V2", "AP": 0.0525, "ROC_AUC": 0.6019},
    {"model": "HGB-V1", "AP": hgb_v1[0], "ROC_AUC": hgb_v1[1]},
    {"model": "HGB-V2", "AP": hgb_v2[0], "ROC_AUC": hgb_v2[1]},
])

print(results.to_string(index=False))
