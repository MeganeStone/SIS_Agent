import warnings

warnings.filterwarnings("ignore")  # 屏蔽新手无关的警告

# ====================== 全局配置（新手只需改这里） ======================
# 4. 大模型配置（请替换为自己的API Key）
DASHSCOPE_API_KEY = "sk-10579025107e412983a48273c2ff7d3f"  # 替换成自己的！

# 1. 导入LangChain核心模块
from typing import Optional, List
from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool, ToolException
from langchain.tools import tool

import requests
import json

# 在tbox_doc_agent.py的顶部导入自定义翻译模块
from tbox_custom_translator import (
    translate_ppt_file,
    translate_excel_file,
    DEFAULT_INPUT_DIR,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_TARGET_LANG,
    DEFAULT_DELAY
)

@tool
def web_search(query: str) -> str:
    """
    使用火山引擎进行联网搜索，获取实时信息、新闻、天气、股价等。
    当你需要查询以下内容时，请使用此工具：
    - 最新的新闻资讯、时事热点
    - 实时的天气、股价、汇率、油价等信息
    - 需要引用权威来源的内容
    - 任何可能超出模型知识截止日期的问题
    
    输入：简短的搜索关键词（1-100个字符）
    返回：包含搜索结果和大模型总结的文本
    """
    
    # 从环境变量获取 API Key（强烈推荐，不要硬编码）
    api_key = "eplh4Tnp86GM50B7qf1YNbNhs65GjeA8"  # 替换成自己的API Key，或使用 os.getenv("VOLC_API_KEY") 从环境变量获取
    
    # 火山引擎联网搜索 API 地址
    url = "https://open.feedcoopapi.com/search_api/web_search"
    
    # 请求头
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # 请求体 - 使用 web_summary 模式（返回 LLM 总结）
    payload = {
        "Query": query[:100],  # 限制长度
        "SearchType": "web_summary",  # 获取带总结的结果
        "Count": 10,  # 返回结果条数，最多50
        "NeedSummary": True,  # 必须为 True，配合 web_summary
        # 可选：限制发布时间范围
        # "TimeRange": "OneWeek",  # OneDay/OneWeek/OneMonth/OneYear
        # 可选：指定搜索站点（最多5个）
        # "Sites": "people.com.cn|xinhuanet.com",
        # 可选：过滤权威等级（1=非常权威）
        # "AuthInfoLevel": 1,
        # 可选：开启 Query 改写（略微增加耗时）
        # "QueryControl": {"QueryRewrite": True}
    }
    
    try:
        # 使用 stream=True 接收流式响应
        with requests.post(url, headers=headers, json=payload, stream=True, timeout=30) as resp:
            resp.encoding = 'utf-8'
            resp.raise_for_status()      # 检查 HTTP 状态码
            full_content = ""

            # 逐行读取 SSE 流
            for line in resp.raw:
                if not line:
                    print("收到空行，继续等待下一行...")
                    continue
                line = line.decode('utf-8').strip()
                if not line:
                    continue
                # SSE 数据行格式：data: {...}
                if line.startswith("data:"):
                    data_str = line[5:]  # 去掉 "'data:" 前缀
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        # 提取 Delta.Content（流式内容片段）
                        choices = data.get("Result", {}).get("Choices", [])
                        if not choices:
                            continue
                        for choice in choices:
                            delta = choice.get("Delta", {})
                            if delta.get("Content"):
                                full_content += delta["Content"]
                    except json.JSONDecodeError:
                        print(f"无法解析的JSON行: {data_str}")
                        continue  # 跳过解析失败的行
                else:
                    print(f"非数据行")  # 调试输出非数据行
            print(f"\n【Web搜索工具】")
            print(f"  搜索查询：{query}")
            print(f"  搜索结果（前200字符）：{full_content.strip()[:200]}...")
            return full_content.strip() if full_content else "未获取到有效总结内容"
    except requests.RequestException as e:
        print(f"Web搜索工具调用失败: {str(e)}")
        return f"搜索失败: {str(e)}"
    except Exception as e:
        print(f"Web搜索工具处理响应失败: {str(e)}")
        return f"搜索出错: {str(e)}"
    
# ====================== 第四步：工具封装 ======================
class TranslateFileInput(BaseModel):
    file_name: str = Field(description="要翻译的文件名（必须包含后缀，如test.pptx、test.xlsx）")
    source_dir: Optional[str] = Field(default=DEFAULT_INPUT_DIR, description="源文件目录，默认D:\\seki\\AI\\copilotTest\\input")
    output_dir: Optional[str] = Field(default=DEFAULT_OUTPUT_DIR, description="输出目录，默认D:\\seki\\AI\\copilotTest\\output")
    target_lang: Optional[str] = Field(default=DEFAULT_TARGET_LANG, description="目标语言，默认日语")

# 封装PPT翻译Tool
def _wrap_ppt_translation(file_name: str, source_dir: str = DEFAULT_INPUT_DIR, 
                          output_dir: str = DEFAULT_OUTPUT_DIR, target_lang: str = DEFAULT_TARGET_LANG) -> str:
    '''用于翻译PPT文件（.pptx），支持自定义源目录、输出目录和目标语言，默认翻译为日语'''
    try:
        return translate_ppt_file(file_name, source_dir, output_dir, target_lang)
    except Exception as e:
        raise ToolException(f"PPT翻译工具调用失败: {str(e)}")

# 封装Excel翻译Tool
def _wrap_excel_translation(file_name: str, source_dir: str = DEFAULT_INPUT_DIR, 
                           output_dir: str = DEFAULT_OUTPUT_DIR, target_lang: str = DEFAULT_TARGET_LANG) -> str:
    '''用于翻译Excel文件（.xlsx），支持自定义源目录、输出目录和目标语言，默认翻译为日语'''
    try:
        return translate_excel_file(file_name, source_dir, output_dir, target_lang)
    except Exception as e:
        raise ToolException(f"Excel翻译工具调用失败: {str(e)}")

# 构建结构化Tool
translate_ppt_tool = StructuredTool.from_function(
    func=_wrap_ppt_translation,
    name="ppt翻译",
    description="用于翻译PPT文件（.pptx），支持自定义源目录、输出目录和目标语言，默认翻译为日语",
    args_schema=TranslateFileInput,
    handle_tool_error=True
)

translate_excel_tool = StructuredTool.from_function(
    func=_wrap_excel_translation,
    name="excel翻译",
    description="用于翻译Excel文件（.xlsx），保留所有元素（单元格、文本框、图形），支持自定义目录和目标语言，默认翻译为日语",
    args_schema=TranslateFileInput,
    handle_tool_error=True
)

# 定义RAG工具的参数Schema
class RAGQAInput(BaseModel):
    question: str = Field(description="用户的TBOX/TSU相关问题（必填）")

# ====================== 第四步：工具封装 ======================
# 核心：创建RAG工具的函数（接收外部传入的qa_chain）
def create_rag_qa_tool(qa_chain):
    """创建RAG问答工具（依赖注入：qa_chain由外部传入）"""
    # 包装函数：适配工具的参数格式，内部调用rag_qa_chain
    def rag_qa_tool_wrapper(question: str) -> str:
        from rag_new import rag_qa_chain
        return rag_qa_chain(question, qa_chain)
    
    # 封装工具
    return StructuredTool.from_function(
        func=rag_qa_tool_wrapper,
        name="rag",
        description="回答企业TBOX/TSU车载终端业务相关问题（如PM是谁、TBOX参数、开发体制），参数仅需question（用户问题）",
        args_schema=RAGQAInput,
        handle_tool_error=True
    )