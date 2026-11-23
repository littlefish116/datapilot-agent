
from pathlib import Path

import pandas as pd

from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    roc_auc_score,
    precision_score,
    recall_score,
    f1_score
)

# =============================
# 1. 读取特征与实际送达时间
# =============================
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "reports" / "sql"

features = pd.read_csv(DATA / "11_order_features.csv")

labels = pd.read_csv(
    DATA / "09_delivery_risk_labels.csv",
    usecols=["order_id", "order_delivered_customer_date"]
)

df = features.merge(
    labels,
    on="order_id",
    how="left",
    validate="one_to_one"
)

df["purchase_at"] = pd.to_datetime(
    df["order_purchase_timestamp"]
)
df["delivered_at"] = pd.to_datetime(
    df["order_delivered_customer_date"]
)

assert df["order_id"].is_unique
assert df["purchase_at"].notna().all()
assert df["delivered_at"].notna().all()

# =============================
# 2. 按时间划分数据
# =============================

# 训练集：5月1日前购买且已送达
train = df[
    (df["purchase_at"] < "2018-05-01") &
    (df["delivered_at"] < "2018-05-01")
].copy()

# 验证集：5-6月购买，7月1日前已送达
val = df[
    (df["purchase_at"] >= "2018-05-01") &
    (df["purchase_at"] < "2018-07-01") &
    (df["delivered_at"] < "2018-07-01")
].copy()

# 测试集：7月购买，使用事后完整标签评分
test = df[
    (df["purchase_at"] >= "2018-07-01") &
    (df["purchase_at"] < "2018-08-01")
].copy()

for name, data in [
    ("Train", train),
    ("Validation", val),
    ("Test", test)
]:
    print(
        f"{name}: {len(data)} samples, "
        f"positive rate = {data['is_late'].mean():.2%}"
    )
    assert data["is_late"].nunique() == 2

# =============================
# 3. 定义模型输入特征
# =============================
numeric_features = [
    "purchase_month",
    "purchase_weekday",
    "item_count",
    "item_value"
]

categorical_features = [
    "customer_state"
]

feature_cols = numeric_features + categorical_features

# =============================
# 4. 建立预处理 Pipeline
# =============================
preprocessor = ColumnTransformer(
    transformers=[
        (
            "num",
            StandardScaler(),
            numeric_features
        ),
        (
            "cat",
            OneHotEncoder(handle_unknown="ignore"),
            categorical_features
        )
    ]
)

model = Pipeline(
    steps=[
        ("preprocessor", preprocessor),
        ("classifier", LogisticRegression(max_iter=2000))
    ]
)

# =============================
# 5. 训练模型：只能使用训练集
# =============================
model.fit(
    train[feature_cols],
    train["is_late"]
)

# =============================
# 6. 评估模型
# =============================
def evaluate(name, data):
    X = data[feature_cols]
    y = data["is_late"]

    # 预测延期概率
    y_prob = model.predict_proba(X)[:, 1]

    # 第一版暂时使用0.5阈值
    y_pred = (y_prob >= 0.5).astype(int)

    print(f"\n========== {name} ==========")
    print("Average Precision:",
          round(average_precision_score(y, y_prob), 4))
    print("ROC-AUC:",
          round(roc_auc_score(y, y_prob), 4))
    print("Accuracy:",
          round(accuracy_score(y, y_pred), 4))
    print("Precision:",
          round(precision_score(y, y_pred, zero_division=0), 4))
    print("Recall:",
          round(recall_score(y, y_pred, zero_division=0), 4))
    print("F1:",
          round(f1_score(y, y_pred, zero_division=0), 4))
    print("预测为延期的订单数:", int(y_pred.sum()))

# =============================
# 7. 输出基础评估指标
# =============================
evaluate("Validation", val)
evaluate("Test", test)


import numpy as np
from sklearn.metrics import (
    precision_recall_curve,
    confusion_matrix
)

# 1. 获取验证集的延期概率
val_prob = model.predict_proba(
    val[feature_cols]
)[:, 1]

val_y = val["is_late"]

# 2. 查看预测概率的分布
print("\n========== 预测概率分布 ==========")

for q in [0.25, 0.50, 0.75, 0.90, 0.95, 0.99, 1.0]:
    print(
        f"分位数 {q:.2f}: "
        f"{np.quantile(val_prob, q):.4f}"
    )

# 3. 根据验证集寻找 F1 最大的阈值
precision, recall, thresholds = precision_recall_curve(
    val_y,
    val_prob
)

# 最后一个 precision/recall 点没有对应阈值
p = precision[:-1]
r = recall[:-1]

f1 = (
    2 * p * r /
    np.maximum(p + r, 1e-12)
)

best_idx = int(np.argmax(f1))
best_threshold = float(thresholds[best_idx])

print("\n========== 验证集阈值选择 ==========")
print("最佳F1阈值:", round(best_threshold, 4))
print("对应F1:", round(float(f1[best_idx]), 4))

# 4. 对比不同阈值在验证集上的表现
def evaluate_threshold(name, y, prob, threshold):
    pred = (prob >= threshold).astype(int)

    print(f"\n{name} | 阈值 {threshold:.4f}")
    print("预测延期数:", int(pred.sum()))
    print("Precision:",
          round(precision_score(y, pred, zero_division=0), 4))
    print("Recall:",
          round(recall_score(y, pred, zero_division=0), 4))
    print("F1:",
          round(f1_score(y, pred, zero_division=0), 4))
    print("混淆矩阵:")
    print(confusion_matrix(y, pred))

for threshold in [0.5, 0.2, 0.1, 0.05, best_threshold]:
    evaluate_threshold(
        "Validation", val_y, val_prob, threshold
    )

# 5. 锁定验证集选择的阈值，进行测试集评估
test_prob = model.predict_proba(
    test[feature_cols]
)[:, 1]

evaluate_threshold(
    "Test",
    test["is_late"],
    test_prob,
    best_threshold
)
