import os
# ---------------------- LangChain 依赖 ----------------------
from langchain_core.tools import ToolException
from translate_ppt import translate_ppt_file
from translate_excel import translate_excel_file
from translate_word import translate_word_file

# ---------------------- 全局配置（你的默认目录） ----------------------
DEFAULT_INPUT_DIR = r"D:\seki\AI\copilotTest\input"
DEFAULT_OUTPUT_DIR = r"D:\seki\AI\copilotTest\output"
DEFAULT_TARGET_LANG = "日语"  # 默认翻译目标语言
DEFAULT_DELAY = 1.2  # 每次翻译后的延迟，单位秒（可调整，过快可能触发API限速）

def translate_file(file_name: str, source_dir: str = DEFAULT_INPUT_DIR, output_dir: str = DEFAULT_OUTPUT_DIR, target_lang: str = DEFAULT_TARGET_LANG, delay: float = DEFAULT_DELAY):
    """通用文件翻译接口，根据文件后缀自动调用对应的翻译函数"""
    ext = os.path.splitext(file_name)[1].lower()
    if ext == ".pptx":
        return translate_ppt_file(file_name, source_dir, output_dir, target_lang, delay)
    elif ext == ".xlsx":
        return translate_excel_file(file_name, source_dir, output_dir, target_lang, delay)
    elif ext in [".docx", ".doc"]:
        return translate_word_file(file_name, source_dir, output_dir, target_lang, delay)

    else:
        raise ToolException(f"不支持的文件类型: {ext}，仅支持.pptx和.xlsx")