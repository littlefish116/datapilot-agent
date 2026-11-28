from app.v2_api import router as v2_router
from app.v3_api import router as v3_router
from app.v3_web import router as v3_web_router
import sqlite3
from app.agent_runtime import build_agent
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from pathlib import Path
import json
from fastapi.responses import JSONResponse
import joblib
import pandas as pd
from uuid import UUID, uuid4
from fastapi import FastAPI, Request
from pydantic import BaseModel, Field, ConfigDict, field_validator

from fastapi import HTTPException
from app.sql_tools import REPORTS, run_report
import logging
from openai import OpenAIError

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "models"

MODEL_PATH = MODEL_DIR / "delivery_risk_hgb_v1.joblib"
METADATA_PATH = MODEL_DIR / "metadata.json"

EXPECTED_FEATURES = [
    "purchase_month",
    "purchase_weekday",
    "item_count",
    "item_value",
    "customer_state",
]

# 巴西各州和联邦区代码
VALID_STATES = set(
    "AC AL AP AM BA CE DF ES GO MA MT MS MG "
    "PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
)


# ==============================
# 1. 定义请求数据结构
# ==============================
class OrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purchase_month: int = Field(ge=1, le=12)
    purchase_weekday: int = Field(ge=0, le=6)
    item_count: int = Field(ge=1)
    item_value: float = Field(ge=0)
    customer_state: str

    @field_validator("customer_state")
    @classmethod
    def check_state(cls, value):
        value = value.strip().upper()

        if value not in VALID_STATES:
            raise ValueError("无效的巴西州代码")

        return value


# ==============================
# 2. 服务启动时加载模型
# ==============================
@asynccontextmanager
async def lifespan(app: FastAPI):
    if not MODEL_PATH.is_file():
        logger.critical(
            "模型文件不存在: %s",
            MODEL_PATH
        )
        raise RuntimeError(
            "模型文件缺失，服务拒绝启动"
        )
    model = joblib.load(MODEL_PATH)

    metadata = json.loads(
        METADATA_PATH.read_text(encoding="utf-8")
    )

    if metadata["features"] != EXPECTED_FEATURES:
        raise ValueError("模型特征配置不匹配")

    app.state.model = model
    app.state.metadata = metadata

    print("Delivery Risk 模型加载成功")
    app.state.agent = build_agent()

    print("DataPilot Agent 初始化成功")
    yield


app = FastAPI(
    title="DataPilot API",
    description="电商订单数据分析与延期风险评分服务",
    version="0.1.0",
    lifespan=lifespan
)

app.include_router(v2_router)
app.include_router(v3_router)
app.include_router(v3_web_router)


# ==============================
# 3. 健康检查
# ==============================
@app.get("/health")
def health(request: Request):
    return {
        "status": "ok",
        "model_loaded": True,
        "model_name": request.app.state.metadata["model_name"]
    }


# ==============================
# 4. 延期风险预测
# ==============================
@app.post("/api/predict")
def predict(order: OrderInput, request: Request):

    metadata = request.app.state.metadata
    model = request.app.state.model

    # Pydantic对象转换成字典
    order_data = order.model_dump()

    # 转换成模型期望的DataFrame格式
    X = pd.DataFrame(
        [order_data],
        columns=metadata["features"]
    )

    # 读取延期类别1的评分
    score = float(
        model.predict_proba(X)[0, 1]
    )

    return JSONResponse(
        content={
            "model_name": metadata["model_name"],
            "risk_score": round(score, 6),
            "score_type": "uncalibrated_model_score",
            "notice": (
                "仅供历史数据实验演示；"
                "模型评分未经概率校准，"
                "不得视为可靠的实际延期概率。"
            )
        },
        media_type="application/json; charset=utf-8"
    )


# ==============================
# 5. 查询可用业务报表
# ==============================
@app.get("/api/reports")
def list_reports():
    return {
        "available_reports": list(REPORTS.keys())
    }


# ==============================
# 6. 执行指定业务报表
# ==============================

@app.get("/api/reports/{report_id}")
def get_report(report_id: str):

    if report_id not in REPORTS:
        raise HTTPException(
            status_code=404,
            detail="不存在该报表"
        )

    try:
        return run_report(report_id)

    except sqlite3.Error:
        logger.exception(
            "SQLite报表查询失败: %s",
            report_id
        )
        raise HTTPException(
            status_code=503,
            detail="业务数据库暂时不可用"
        )

    except OSError:
        logger.exception(
            "SQL报表文件读取失败: %s",
            report_id
        )
        raise HTTPException(
            status_code=500,
            detail="报表配置读取失败"
        )




# ==============================
# 7. Agent 多轮对话接口
# ==============================

class ChatRequest(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=2000
    )

    # 第一次请求可以不提供会话ID
    conversation_id: UUID | None = None

    @field_validator("question")
    @classmethod
    def validate_question(cls, value):
        value = value.strip()

        if not value:
            raise ValueError("问题不能为空")

        return value


@app.post("/api/chat")
def chat(payload: ChatRequest, request: Request):

    agent = request.app.state.agent

    # 首次对话生成UUID，后续沿用原ID
    conversation_id = str(
        payload.conversation_id or uuid4()
    )

    # LangGraph通过thread_id管理对话历史
    config = {
        "configurable": {
            "thread_id": conversation_id
        }
    }

    try:
        result = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": payload.question
                    }
                ]
            },
            config=config
        )

    except OpenAIError:
        # 在服务端记录异常，方便排查
        logger.exception("LLM API调用失败")

        # 不向用户暴露内部异常细节
        raise HTTPException(
            status_code=503,
            detail="模型服务暂时不可用，请稍后重试"
        )

    messages = result["messages"]

    # 找到最新一轮用户消息的位置
    last_user_index = max(
        i
        for i, message in enumerate(messages)
        if getattr(message, "type", None) == "human"
    )

    # 只分析本轮产生的消息
    current_turn = messages[last_user_index + 1:]

    used_tools = []

    for message in current_turn:
        calls = getattr(message, "tool_calls", []) or []

        for call in calls:
            used_tools.append(call["name"])

    answer = messages[-1].content

    return {
        "conversation_id": conversation_id,
        "question": payload.question,
        "answer": answer,
        "used_tools": used_tools
    }


# ==============================
# 8. DataPilot Web首页
# ==============================
@app.get("/", include_in_schema=False)
def home():
    return FileResponse(
        ROOT / "frontend" / "index.html"
    )

@app.get("/v2", include_in_schema=False)
def v2_demo_home():
    return FileResponse(ROOT / "frontend" / "v2.html")
