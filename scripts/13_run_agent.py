
import os
import sys
from pathlib import Path

# 将项目根目录添加到Python模块搜索路径最前面
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from app.agent_tools import (
    query_business_report,
    predict_delivery_risk
)



ROOT = Path(__file__).resolve().parents[1]

# 读取项目根目录的环境变量
load_dotenv(ROOT / ".env")

if not os.getenv("OPENAI_API_KEY"):
    raise RuntimeError(
        "没有找到DEEPSEEK_API_KEY，请检查.env文件"
    )


# 1. 初始化大模型
llm = ChatOpenAI(
    model=os.getenv("OPENAI_MODEL", "deepseek-flash"),
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://api.deepseek.com",
    temperature=0
)


# 2. 创建Agent
agent = create_agent(
    model=llm,
    tools=[
        query_business_report,
        predict_delivery_risk
    ],
    system_prompt=(
        "你是DataPilot电商数据分析助手。"
        "你可以查询Olist历史订单报表，"
        "也可以调用已训练的延期风险评分模型。"
        "涉及真实业务数量、金额、比例时，必须调用报表工具，"
        "不得编造统计数据。"
        "涉及订单风险评分时，必须调用预测工具，"
        "缺少必要参数时先向用户询问，不得猜测。"
        "不能把未经校准的风险评分当成真实延期概率。"
        "如果工具报错或现有报表不能回答问题，应明确说明。"
        "使用简洁中文回答，注明数据来自历史Olist数据集。"
        "分析延期率、增长率等比例指标时，"
        "必须同时考虑样本数量。"
        "样本量小于100的月份属于低样本量月份，"
        "不得直接据此得出总体表现最差等结论。"
        "如果报告最高或最低月份，"
        "必须明确是否设置样本量筛选条件。"
        "所有业务分析结论必须由工具返回的数据支持，"
        "不允许编造原因或因果关系。"
    )
)


# 3. 接收用户问题
question = input("请输入你的问题：").strip()

if not question:
    raise ValueError("问题不能为空")


# 4. Agent执行
result = agent.invoke({
    "messages": [
        {"role": "user", "content": question}
    ]
})


# 5. 检查实际调用过哪些工具
used_tools = []

for message in result["messages"]:
    for call in getattr(message, "tool_calls", []) or []:
        used_tools.append(call["name"])


print("\n========== 工具调用记录 ==========")
print(used_tools if used_tools else "未调用工具")

print("\n========== Agent回答 ==========")
print(result["messages"][-1].content)
