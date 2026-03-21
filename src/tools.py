import warnings

warnings.filterwarnings("ignore")  # 屏蔽新手无关的警告

# ====================== 全局配置（新手只需改这里） ======================
# 4. 大模型配置（请替换为自己的API Key）
DASHSCOPE_API_KEY = "sk-b293f03e34da4c2d8cbae70232bd0d27"  # 替换成自己的！

# 1. 导入LangChain核心模块
from langchain_core.output_parsers import StrOutputParser
from typing import Optional, List
from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool, ToolException
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder, PromptTemplate
from langchain_openai import ChatOpenAI

# 在tbox_doc_agent.py的顶部导入自定义翻译模块
from tbox_custom_translator import (
    translate_ppt_file,
    translate_excel_file,
    DEFAULT_INPUT_DIR,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_TARGET_LANG,
    DEFAULT_DELAY
)

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