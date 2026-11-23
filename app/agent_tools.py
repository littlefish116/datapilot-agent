
from typing import Literal

import httpx
from langchain.tools import tool


API_BASE = "http://127.0.0.1:8000"


@tool
def query_business_report(
    report_id: Literal[
        "order_status",
        "monthly_late_delivery",
        "top_categories",
        "top_states"
    ]
) -> dict:
    """查询Olist电商历史业务报表。

    可用报表：
    order_status：订单状态及数量；
    monthly_late_delivery：按购买月份统计延期率；
    top_categories：销售额最高的商品类别；
    top_states：已交付订单最多的客户所在州。
    """
    try:
        response = httpx.get(
            f"{API_BASE}/api/reports/{report_id}",
            timeout=15
        )
        response.raise_for_status()
        return response.json()

    except httpx.HTTPError as exc:
        return {
            "error": "业务报表接口调用失败",
            "details": str(exc)
        }


@tool
def predict_delivery_risk(
    purchase_month: int,
    purchase_weekday: int,
    item_count: int,
    item_value: float,
    customer_state: str
) -> dict:
    """根据订单提交时的五个特征计算延期风险评分。

    purchase_month：购买月份，1-12；
    purchase_weekday：星期，0=周日，1=周一，...，6=周六；
    item_count：订单商品条目数，至少为1；
    item_value：订单商品金额，非负；
    customer_state：巴西州代码，例如SP、RJ、MG。

    必须提供全部五个字段。缺少数据时应向用户询问，
    不能自行猜测。评分未经概率校准。
    """
    payload = {
        "purchase_month": purchase_month,
        "purchase_weekday": purchase_weekday,
        "item_count": item_count,
        "item_value": item_value,
        "customer_state": customer_state
    }

    try:
        response = httpx.post(
            f"{API_BASE}/api/predict",
            json=payload,
            timeout=15
        )
        response.raise_for_status()
        return response.json()

    except httpx.HTTPStatusError as exc:
        return {
            "error": "预测参数未通过校验或接口失败",
            "status_code": exc.response.status_code,
            "details": exc.response.text
        }

    except httpx.RequestError as exc:
        return {
            "error": "无法连接预测接口",
            "details": str(exc)
        }
