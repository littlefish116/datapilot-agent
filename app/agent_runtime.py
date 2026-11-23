
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from app.agent_tools import (
    query_business_report,
    predict_delivery_risk
)

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(
    ROOT / ".env",
    encoding="utf-8-sig",
    override=True
)


def build_agent():
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise RuntimeError("未找到 OPENAI_API_KEY")

    llm = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "deepseek-chat"),
        api_key=api_key,
        base_url=os.getenv(
            "OPENAI_BASE_URL",
            "https://api.deepseek.com"
        ),
        temperature=0
    )

    return create_agent(
        model=llm,
        tools=[
            query_business_report,
            predict_delivery_risk
        ],
        system_prompt=(
            "你是DataPilot电商数据分析助手。"
            "只能依据工具结果报告真实业务数据，"
            "不得编造订单数量、金额或延期率。"
            "业务统计需要调用报表工具。"
            "预测评分需要调用预测工具。"
            "预测缺少必要参数时先询问，不能猜测。"
            "模型评分未经概率校准，"
            "不能解释为真实延期概率，"
            "没有评分参照时不能擅自划分风险等级。"
            "当前不能仅凭订单号查询订单特征。"
            "比例分析需要考虑样本量。"
            "低样本量月份不宜直接用于总体优劣排名。"
            "不能编造因果解释或工具不支持的统计结果。"
            "用简洁、准确的中文回答。"
            "用户只询问某个月的指标时，优先直接回答该月，"
            "不要主动宣称它是全期最高或最低。"
            "若进行月份排名，必须说明样本量筛选条件。"
            "不能仅凭样本数量大就声称具有整体代表性。"
            "报表仅涵盖符合SQL筛选条件的历史订单，"
            "不得将其无条件推广到所有订单。"
        ),

        checkpointer=InMemorySaver(),
    )
