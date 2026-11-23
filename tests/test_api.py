
import importlib

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage, AIMessage

# 明确导入项目中的FastAPI模块
api = importlib.import_module("app.main")


# ==================================
# 1. 模拟Agent，避免真实DeepSeek调用
# ==================================
class FakeAgent:
    def __init__(self):
        self.history = {}

    def invoke(self, payload, config):
        thread_id = config["configurable"]["thread_id"]
        question = payload["messages"][-1]["content"]

        # 为每个会话保存独立历史
        history = self.history.setdefault(thread_id, [])

        user_message = HumanMessage(content=question)

        if "延期率" in question:
            assistant_message = AIMessage(
                content="模拟报表查询结果",
                tool_calls=[
                    {
                        "name": "query_business_report",
                        "args": {
                            "report_id": "monthly_late_delivery"
                        },
                        "id": "mock-call-1"
                    }
                ]
            )
        else:
            assistant_message = AIMessage(
                content="模拟普通回复",
                tool_calls=[]
            )

        history.extend([
            user_message,
            assistant_message
        ])

        return {"messages": list(history)}


# ==================================
# 2. 创建测试客户端
# ==================================
@pytest.fixture
def client(monkeypatch):

    # 用模拟Agent替代真实DeepSeek Agent
    fake_agent = FakeAgent()

    monkeypatch.setattr(
        api,
        "build_agent",
        lambda: fake_agent
    )

    # with确保执行FastAPI的lifespan
    with TestClient(api.app) as test_client:
        yield test_client


# ==================================
# 3. 健康检查
# ==================================
def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "ok"
    assert data["model_loaded"] is True


# ==================================
# 4. SQL报表测试
# ==================================
def test_order_status(client):
    response = client.get(
        "/api/reports/order_status"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["report_id"] == "order_status"
    assert data["truncated"] is False

    delivered = next(
        row for row in data["rows"]
        if row["order_status"] == "delivered"
    )

    assert delivered["order_count"] == 96478


def test_monthly_late_delivery(client):
    response = client.get(
        "/api/reports/monthly_late_delivery"
    )

    assert response.status_code == 200

    rows = response.json()["rows"]

    march = next(
        row for row in rows
        if row["purchase_month"] == "2018-03"
    )

    assert march["delivered_with_dates"] == 7003
    assert march["late_orders"] == 1328
    assert march["late_rate_pct"] == pytest.approx(18.96)


# ==================================
# 5. 模型预测接口测试
# ==================================
def test_predict(client):
    payload = {
        "purchase_month": 7,
        "purchase_weekday": 2,
        "item_count": 2,
        "item_value": 199.9,
        "customer_state": "SP"
    }

    response = client.post(
        "/api/predict",
        json=payload
    )

    assert response.status_code == 200

    data = response.json()

    assert data["model_name"] == "delivery_risk_hgb_v1"

    assert data["score_type"] == "uncalibrated_model_score"

    assert data["risk_score"] == pytest.approx(
        0.025889,
        abs=0.00001
    )


# ==================================
# 6. 非法参数测试
# ==================================
def test_invalid_month(client):
    payload = {
        "purchase_month": 15,
        "purchase_weekday": 2,
        "item_count": 2,
        "item_value": 199.9,
        "customer_state": "SP"
    }

    response = client.post(
        "/api/predict",
        json=payload
    )

    assert response.status_code == 422


def test_invalid_report(client):
    response = client.get(
        "/api/reports/not_exists"
    )

    assert response.status_code == 404


# ==================================
# 7. 多轮会话及工具记录测试
# ==================================
def test_chat_session(client):

    # 第一轮：模拟调用报表工具
    first = client.post(
        "/api/chat",
        json={
            "question": "2018年3月的订单延期率是多少？"
        }
    )

    assert first.status_code == 200

    first_data = first.json()

    conversation_id = first_data["conversation_id"]

    assert conversation_id
    assert first_data["used_tools"] == [
        "query_business_report"
    ]

    # 第二轮：沿用同一个会话ID
    second = client.post(
        "/api/chat",
        json={
            "question": "谢谢你！",
            "conversation_id": conversation_id
        }
    )

    assert second.status_code == 200

    second_data = second.json()

    assert second_data["conversation_id"] == conversation_id

    # 不能重复统计上一轮的工具调用
    assert second_data["used_tools"] == []

    # 新建会话应该获得不同的ID
    third = client.post(
        "/api/chat",
        json={
            "question": "你好"
        }
    )

    assert third.status_code == 200

    assert (
        third.json()["conversation_id"]
        != conversation_id
    )



# ==================================
# 8. 模拟DeepSeek API故障
# ==================================
def test_chat_llm_failure(client):

    from openai import OpenAIError

    class FailingAgent:
        def invoke(self, *args, **kwargs):
            raise OpenAIError(
                "Simulated DeepSeek API failure"
            )

    # 仅本次测试使用故障Agent
    client.app.state.agent = FailingAgent()

    response = client.post(
        "/api/chat",
        json={
            "question": "2018年3月的延期率是多少？"
        }
    )

    assert response.status_code == 503

    assert response.json()["detail"] == (
        "模型服务暂时不可用，请稍后重试"
    )

    # 不应暴露内部错误信息
    assert "Simulated DeepSeek API failure" not in response.text



# ==================================
# 9. 不同会话之间不能共享历史
# ==================================
def test_chat_conversation_isolation(client):

    # 创建会话A
    response_a = client.post(
        "/api/chat",
        json={
            "question": "2018年3月的订单延期率是多少？"
        }
    )

    # 创建会话B
    response_b = client.post(
        "/api/chat",
        json={
            "question": "你好，我是另一个会话"
        }
    )

    assert response_a.status_code == 200
    assert response_b.status_code == 200

    id_a = response_a.json()["conversation_id"]
    id_b = response_b.json()["conversation_id"]

    # 不同会话必须使用不同ID
    assert id_a != id_b

    fake_agent = client.app.state.agent

    # 各自只有本轮的两条模拟消息
    assert len(fake_agent.history[id_a]) == 2
    assert len(fake_agent.history[id_b]) == 2

    # 检查不同会话保存的问题
    assert fake_agent.history[id_a][0].content == (
        "2018年3月的订单延期率是多少？"
    )

    assert fake_agent.history[id_b][0].content == (
        "你好，我是另一个会话"
    )

    # 继续会话A
    response_a2 = client.post(
        "/api/chat",
        json={
            "question": "谢谢你！",
            "conversation_id": id_a
        }
    )

    assert response_a2.status_code == 200

    # 会话A增加消息，会话B不受影响
    assert len(fake_agent.history[id_a]) == 4
    assert len(fake_agent.history[id_b]) == 2

    # 第二轮没有新工具调用
    assert response_a2.json()["used_tools"] == []

    print("不同会话的历史记录相互隔离")


# ==================================
# 10. 非法会话ID应被拒绝
# ==================================
def test_invalid_conversation_id(client):

    response = client.post(
        "/api/chat",
        json={
            "question": "你好",
            "conversation_id": "not-a-valid-uuid"
        }
    )

    # UUID类型校验由Pydantic完成
    assert response.status_code == 422


# ==================================
# 11. 数据库不可用
# ==================================
def test_database_unavailable(
    client, monkeypatch, tmp_path
):
    import app.sql_tools as sql_tools

    # 指向一个不存在的SQLite数据库
    missing_db = tmp_path / "missing.db"

    monkeypatch.setattr(
        sql_tools,
        "DB_PATH",
        missing_db
    )

    response = client.get(
        "/api/reports/order_status"
    )

    assert response.status_code == 503

    assert response.json()["detail"] == (
        "业务数据库暂时不可用"
    )

    # 不应向用户暴露内部数据库错误
    assert "unable to open database" not in response.text


# ==================================
# 12. 模型文件缺失时拒绝启动
# ==================================
def test_missing_model_prevents_startup(
    monkeypatch, tmp_path
):
    from fastapi.testclient import TestClient

    # 模拟模型文件不存在
    missing_model = tmp_path / "missing.joblib"

    monkeypatch.setattr(
        api,
        "MODEL_PATH",
        missing_model
    )

    with pytest.raises(
        RuntimeError,
        match="模型文件缺失"
    ):
        with TestClient(api.app):
            pass
