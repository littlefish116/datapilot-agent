
from pathlib import Path
import pandas as pd

# 1. 定位项目目录和数据文件
ROOT = Path(__file__).resolve().parents[1]
file_path = ROOT / "reports" / "sql" / "11_order_features.csv"

# 2. 读取数据
df = pd.read_csv(file_path)

print("========== 数据基本信息 ==========")
print("数据形状：", df.shape)
print("\n前5行：")
print(df.head())

# 3. 检查字段类型
print("\n========== 字段类型 ==========")
print(df.dtypes)

# 4. 检查缺失值
print("\n========== 缺失值 ==========")
print(df.isnull().sum())

# 5. 检查订单是否重复
print("\n========== 重复订单 ==========")
print("重复 order_id 数量：",
      df["order_id"].duplicated().sum())

# 6. 检查标签分布
print("\n========== 标签分布 ==========")
print(df["is_late"].value_counts())

print("\n延期率：")
print(df["is_late"].mean() * 100)

# 7. 检查数值特征分布
print("\n========== 数值特征统计 ==========")
print(df[["item_count", "item_value"]].describe())

# 8. 检查时间范围
df["order_purchase_timestamp"] = pd.to_datetime(
    df["order_purchase_timestamp"],
    errors="coerce"
)

print("\n========== 时间范围 ==========")
print("最早订单：", df["order_purchase_timestamp"].min())
print("最晚订单：", df["order_purchase_timestamp"].max())
print("无效购买时间：", df["order_purchase_timestamp"].isna().sum())
