
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "reports" / "sql"

v1 = pd.read_csv(DATA / "11_order_features.csv")
v2 = pd.read_csv(DATA / "12_order_features_v2.csv")

# 1. 检查基本规模及订单唯一性
print("========== 基本信息 ==========")
print("V1:", v1.shape)
print("V2:", v2.shape)

print("V1 重复订单:", v1["order_id"].duplicated().sum())
print("V2 重复订单:", v2["order_id"].duplicated().sum())

assert v1["order_id"].is_unique
assert v2["order_id"].is_unique

# 2. 按订单编号对齐两张表
merged = v1.merge(
    v2,
    on="order_id",
    how="outer",
    suffixes=("_v1", "_v2"),
    indicator=True,
    validate="one_to_one"
)

print("\n========== 样本匹配情况 ==========")
print(merged["_merge"].value_counts())

assert (merged["_merge"] == "both").all()

# 3. 比较V1、V2共有的字段
print("\n========== 共有字段一致性 ==========")

common_cols = [
    "order_purchase_timestamp",
    "customer_state",
    "purchase_month",
    "purchase_weekday",
    "item_count",
    "item_value",
    "is_late"
]

for col in common_cols:
    a = merged[f"{col}_v1"]
    b = merged[f"{col}_v2"]

    if col == "item_value":
        same = np.isclose(
            a, b, rtol=0, atol=0.011
        )
    else:
        same = (a == b).fillna(False)

    diff_count = int((~same).sum())
    print(f"{col}: 不一致 {diff_count} 条")

    assert diff_count == 0, f"{col}存在不一致"

# 4. 检查标签是否保持一致
print("\n========== V2标签分布 ==========")
print(v2["is_late"].value_counts())
print("延期率:", round(v2["is_late"].mean() * 100, 4), "%")

assert len(v2) == 96470
assert v2["is_late"].sum() == 6534

# 5. 检查新增特征
new_cols = [
    "purchase_hour",
    "freight_value",
    "total_weight_g",
    "seller_count",
    "has_interstate_seller"
]

print("\n========== 新特征缺失值 ==========")
print(v2[new_cols].isna().sum())

print("\n========== 新特征分布 ==========")
print(
    v2[new_cols].describe(
        percentiles=[0.5, 0.9, 0.99]
    ).to_string()
)

print("\n========== 跨州卖家分布 ==========")
print(
    v2["has_interstate_seller"].value_counts(
        dropna=False
    )
)

# 6. 基础取值检查
print("\n========== 异常值检查 ==========")
print("负运费:",
      (v2["freight_value"] < 0).sum())
print("负重量:",
      (v2["total_weight_g"] < 0).sum())
print("商品数小于等于0:",
      (v2["item_count"] <= 0).sum())
print("下单小时不在0-23:",
      (~v2["purchase_hour"].between(0, 23)).sum())

print("\nV1/V2共有字段核验完成！")
