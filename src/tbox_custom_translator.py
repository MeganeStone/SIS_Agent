import os
# ---------------------- LangChain 依赖 ----------------------
from langchain_core.tools import ToolException
from translate_ppt import translate_ppt_file
from translate_excel import translate_excel_file
from translate_word import translate_word_file
from dotenv import load_dotenv
load_dotenv()
from pathlib import Path

# 获取当前脚本所在目录的父级目录（即 SIS_Agent 根目录）
SIS_AGENT_ROOT = Path(__file__).parent.parent

# ---------------------- 全局配置（你的默认目录） ----------------------
DEFAULT_INPUT_DIR = os.getenv("TRANSLATE_INPUT_DIR") or str(SIS_AGENT_ROOT / "translate" / "input")
DEFAULT_TARGET_LANG = os.getenv("TRANSLATE_TARGET_LANG") or "日语"  # 默认翻译目标语言
DEFAULT_DELAY = float(os.getenv("TRANSLATE_DELAY") or 1.2)  # 每次翻译后的延迟，单位秒（可调整，过快可能触发API限速）

def translate_file(file_name: str, workspace_dir: str = DEFAULT_INPUT_DIR, target_lang: str = DEFAULT_TARGET_LANG, delay: float = DEFAULT_DELAY):
    """通用文件翻译接口，根据文件后缀自动调用对应的翻译函数"""
    ext = os.path.splitext(file_name)[1].lower()
    if ext == ".pptx":
        return translate_ppt_file(file_name, workspace_dir, target_lang, delay)
    elif ext == ".xlsx":
        return translate_excel_file(file_name, workspace_dir, target_lang, delay)
    elif ext in [".docx", ".doc"]:
        return translate_word_file(file_name, workspace_dir, target_lang, delay)

    else:
        raise ToolException(f"不支持的文件类型: {ext}，仅支持.pptx、.xlsx和.docx")