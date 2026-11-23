
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "reports" / "sql"

# 读取订单特征
features = pd.read_csv(
    DATA_DIR / "11_order_features.csv"
)

# 读取实际送达日期
labels = pd.read_csv(
    DATA_DIR / "09_delivery_risk_labels.csv",
    usecols=["order_id", "order_delivered_customer_date"]
)

# 防止 JOIN 后产生重复订单
assert features["order_id"].is_unique
assert labels["order_id"].is_unique

# 将实际送达时间关联到特征表
df = features.merge(
    labels,
    on="order_id",
    how="left",
    validate="one_to_one"
)

# 转换日期字段
df["purchase_at"] = pd.to_datetime(
    df["order_purchase_timestamp"],
    errors="coerce"
)

df["delivered_at"] = pd.to_datetime(
    df["order_delivered_customer_date"],
    errors="coerce"
)

assert df["purchase_at"].notna().all()
assert df["delivered_at"].notna().all()

# 各数据集的购买时间范围，以及对应的标签核验时点
windows = [
    ("Train", None, "2018-05-01", "2018-05-01"),
    ("Validation", "2018-05-01", "2018-07-01", "2018-07-01"),
    ("Test", "2018-07-01", "2018-08-01", "2018-09-01"),
]

print("========== 标签成熟度检查 ==========")

for name, start, end, cutoff in windows:
    mask = df["purchase_at"] < pd.Timestamp(end)

    if start is not None:
        mask &= df["purchase_at"] >= pd.Timestamp(start)

    subset = df.loc[mask].copy()

    # 截止时点之前已经送达，最终标签才可认为已观察到
    ready = subset["delivered_at"] < pd.Timestamp(cutoff)

    print(f"\n{name}")
    print("订单总数：", len(subset))
    print("截止时点：", cutoff)
    print("标签已成熟：", int(ready.sum()))
    print("标签尚未成熟：", int((~ready).sum()))
    print("未成熟订单中的最终延期数：",
          int(subset.loc[~ready, "is_late"].sum()))

# 查看不同时间间隔下训练数据的标签成熟情况
train_cutoff = pd.Timestamp("2018-05-01")

print("\n========== 训练集时间间隔检查 ==========")

for gap_days in [0, 30, 45, 60]:
    purchase_cutoff = train_cutoff - pd.Timedelta(
        days=gap_days
    )

    subset = df[df["purchase_at"] < purchase_cutoff]

    unready = (
        subset["delivered_at"] >= train_cutoff
    ).sum()

    print(
        f"间隔 {gap_days:2d} 天："
        f"样本 {len(subset):6d}，"
        f"截至5月1日仍未送达 {unready:5d}"
    )
