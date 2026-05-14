import warnings

warnings.filterwarnings("ignore")  # 屏蔽新手无关的警告

# 1. 导入LangChain核心模块
from typing import Optional, List
from pydantic import BaseModel, Field, create_model
from langchain_core.tools import StructuredTool, ToolException
from langchain.tools import tool

import requests
import json
from dotenv import load_dotenv
load_dotenv()
import os
from exceptions import InvalidAPIKeyError

from parse_SPI import run_parse_spi
from pathlib import Path
# 在tbox_doc_agent.py的顶部导入自定义翻译模块
from tbox_custom_translator import (
    translate_file,
    DEFAULT_INPUT_DIR,
    DEFAULT_TARGET_LANG
)

def create_parse_spi_tool(workspace_dir: Path):
    """创建 SPI 日志解析工具，使用 workspace 目录内的 parse_spi 子目录"""
    @tool
    def parse_spi(
        logs_folder: str = "parse_spi/logs",
        config_file: str = "spi_id.txt",
        template_file: str = "template.xlsx"
    ) -> str:
        """
        将spi log解析成易于分析的Excel文件。
        
        参数:
        - workspace_dir: 用户工作文件路径
        
        返回: 解析成功或失败信息
        """
        # 禁止绝对路径
        if any(Path(p).is_absolute() for p in [logs_folder, config_file, template_file]):
            return "请使用 workspace 内相对路径，不要使用绝对路径。"

        result = run_parse_spi(
            workspace_dir,
            logs_dir=logs_folder,
            config_path=config_file,
            template_path=template_file
        )
        if not result.get('success'):
            return f"SPI解析失败：{result.get('message')}"
        return f"SPI解析成功，输出文件：{result['output_path']}；共提取 {result['count']} 条报文，类型：{','.join(result['types'])}。"

    return parse_spi

def create_web_search_tool(volc_api_key: str):
    def web_search(query: str) -> str:
        # 火山引擎联网搜索 API 地址
        url = os.getenv("VOLC_SEARCH_API_URL") or "https://open.feedcoopapi.com/search_api/web_search"  # 替换成火山引擎提供的搜索API地址，或使用环境变量配置
        
        # 请求头
        headers = {
            "Authorization": f"Bearer {volc_api_key}",
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
                        line = json.loads(line)  # 尝试解析为JSON
                        if line["ResponseMetadata"].get("Error").get("Code") == "invalid_api_key":
                            raise InvalidAPIKeyError("火山引擎API Key 无效，请检查后重试")
                print(f"\n【Web搜索工具】")
                print(f"  搜索查询：{query}")
                print(f"  搜索结果（前200字符）：{full_content.strip()[:200]}...")
                return full_content.strip() if full_content else "未获取到有效总结内容"
        except InvalidAPIKeyError:
            raise InvalidAPIKeyError("火山引擎API Key 无效，请检查后重试")
        except requests.RequestException as e:
            print(f"Web搜索工具调用失败: {str(e)}")
            return f"搜索失败: {str(e)}"
        except Exception as e:
            print(f"Web搜索工具处理响应失败: {str(e)}")
            return f"搜索出错: {str(e)}"
    return StructuredTool.from_function(
        func=web_search,
        name="web_search",
        description="联网搜索最新信息",
        args_schema=None
    )
    
# ====================== 第四步：工具封装 ======================
def create_translate_file_tool(dashscope_api_key: str, workspace_dir: str = None):
    if workspace_dir is None:
        workspace_dir = DEFAULT_INPUT_DIR

    TranslateFileInput = create_model(
        "TranslateFileInput",
        file_name=(str, ...),
        workspace_dir=(str, workspace_dir),
        target_lang=(str, DEFAULT_TARGET_LANG),
    )

    # 封装文件翻译Tool
    def _wrap_translation(file_name: str, workspace_dir: str = workspace_dir,
                            target_lang: str = DEFAULT_TARGET_LANG) -> str:
        '''用于翻译文件（.pptx、.xlsx等），支持自定义源目录、输出目录和目标语言，默认翻译为日语'''
        try:
            # 这里设置临时环境变量，以便 translate_file 内部调用的 translate_text 能读取
            os.environ["TRANSLATE_API_KEY"] = dashscope_api_key
            return translate_file(file_name, workspace_dir, target_lang)
        except Exception as e:
            raise ToolException(f"文件翻译工具调用失败: {str(e)}")

    # 构建结构化Tool
    translate_file_tool = StructuredTool.from_function(
        func=_wrap_translation,
        name="文件翻译",
        description="用于翻译文件（.pptx、.xlsx等），支持自定义源目录、输出目录和目标语言，默认翻译为日语",
        args_schema=TranslateFileInput,
        handle_tool_error=True
    )
    return translate_file_tool

# 定义RAG工具的参数Schema
class RAGQAInput(BaseModel):
    question: str = Field(description="用户的TBOX/TSU相关问题（必填）")

# ====================== 第四步：工具封装 ======================
# 核心：创建RAG工具的函数（接收外部传入的qa_chain）
def create_rag_qa_tool(qa_chain):
    """创建RAG问答工具（依赖注入：qa_chain由外部传入）"""
    # 包装函数：适配工具的参数格式，内部调用rag_qa_chain
    def rag_qa_tool_wrapper(question: str) -> str:
        from rag import rag_qa_chain
        return rag_qa_chain(question, qa_chain)
    
    # 封装工具
    return StructuredTool.from_function(
        func=rag_qa_tool_wrapper,
        name="rag",
        description="回答企业TBOX/TSU车载终端业务相关问题（如PM是谁、TBOX参数、开发体制），参数仅需question（用户问题）",
        args_schema=RAGQAInput,
        handle_tool_error=True
    )