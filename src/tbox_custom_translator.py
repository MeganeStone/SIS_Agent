import time
import os
import sys
# 新增：导入httpx处理客户端配置
import httpx
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

if __name__ == "__main__":
    """独立测试翻译工具的入口"""
    import sys
    import traceback
    from pathlib import Path

    # ============== 测试配置 ==============
    TEST_FILE = "test.docx"  # 测试文件名
    TEST_INPUT_DIR = Path(DEFAULT_INPUT_DIR)
    TEST_OUTPUT_DIR = Path(DEFAULT_OUTPUT_DIR)
    
    # ============== 验证文件存在 ==============
    input_path = TEST_INPUT_DIR / TEST_FILE
    if not input_path.exists():
        print(f"❌ 错误：测试文件不存在！\n路径: {input_path}\n请确保文件已上传到 {TEST_INPUT_DIR}")
        sys.exit(1)
    
    print(f"✅ 文件存在: {input_path.resolve()}")
    print(f"✅ 测试目录: {TEST_INPUT_DIR.resolve()}")
    print(f"✅ 输出目录: {TEST_OUTPUT_DIR.resolve()}\n")
    
    # ============== 执行翻译测试 ==============
    print("="*50)
    print("🚀 开始翻译测试...")
    print("="*50)
    
    try:
        # 执行翻译
        start_time = time.time()
        result = translate_file(
            file_name=TEST_FILE,
            source_dir=DEFAULT_INPUT_DIR,
            output_dir=DEFAULT_OUTPUT_DIR,
            target_lang="中文",
            delay=DEFAULT_DELAY
        )
        elapsed = time.time() - start_time
        
        # ============== 结果验证 ==============
        print("\n" + "="*50)
        print("✅ 翻译完成！")
        print(f"耗时: {elapsed:.2f} 秒")
        print(f"输出文件: {Path(result).resolve()}")
        print("="*50)
        
        # 检查输出文件
        output_path = Path(DEFAULT_OUTPUT_DIR) / f"{TEST_FILE.split('.')[0]}_{DEFAULT_TARGET_LANG}.docx"
        if output_path.exists():
            print(f"✓ 输出文件已生成: {output_path.resolve()}")
            print(f"✓ 文件大小: {output_path.stat().st_size} 字节")
        else:
            print(f"❌ 输出文件未生成: {output_path.resolve()}")
            print("提示：可能因路径特殊字符导致文件未创建")
        
    except Exception as e:
        print(f"\n❌ 翻译测试失败: {str(e)}")
        print("详细错误:")
        traceback.print_exc()
        sys.exit(1)
    
    print("\n" + "="*50)
    print("测试完成！")
    print("="*50)