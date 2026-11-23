
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

df = pd.read_csv(
    ROOT / "reports/sql/11_order_features.csv"
)

# 转换购买时间
df["order_purchase_timestamp"] = pd.to_datetime(
    df["order_purchase_timestamp"]
)

# 提取年月
df["year_month"] = (
    df["order_purchase_timestamp"]
    .dt.to_period("M")
)

# 每月样本量、延期数量、延期率
monthly = df.groupby("year_month").agg(
    total_orders=("order_id", "count"),
    late_orders=("is_late", "sum"),
    late_rate=("is_late", "mean")
).reset_index()

monthly["late_rate_pct"] = (
    monthly["late_rate"] * 100
).round(2)

print("========== 月度样本分布 ==========")
print(
    monthly[
        ["year_month", "total_orders",
         "late_orders", "late_rate_pct"]
    ].to_string(index=False)
)

# 候选时间切分
train = df[
    df["order_purchase_timestamp"] < "2018-05-01"
]
val = df[
    (df["order_purchase_timestamp"] >= "2018-05-01") &
    (df["order_purchase_timestamp"] < "2018-07-01")
]
test = df[
    (df["order_purchase_timestamp"] >= "2018-07-01") &
    (df["order_purchase_timestamp"] < "2018-08-01")
]

print("\n========== 候选划分 ==========")

for name, data in [
    ("Train", train),
    ("Validation", val),
    ("Test", test)
]:
    print(
        f"{name}: {len(data)} 条，"
        f"延期 {data['is_late'].sum()} 条，"
        f"延期率 {data['is_late'].mean():.2%}"
    )

print(
    "\n暂未分配的样本数：",
    len(df) - len(train) - len(val) - len(test)
)
