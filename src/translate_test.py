import sys
import traceback
from pathlib import Path
import time
from tbox_custom_translator import translate_file, DEFAULT_INPUT_DIR, DEFAULT_OUTPUT_DIR, DEFAULT_DELAY

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
        target_lang="英文",
        delay=DEFAULT_DELAY
    )
    elapsed = time.time() - start_time
    
    # ============== 结果验证 ==============
    print("\n" + "="*50)
    print("✅ 翻译完成！")
    print(f"耗时: {elapsed:.2f} 秒")
    print(f"输出文件: {Path(result).resolve()}")
    print("="*50)
    
except Exception as e:
    print(f"\n❌ 翻译测试失败: {str(e)}")
    print("详细错误:")
    traceback.print_exc()
    sys.exit(1)

print("\n" + "="*50)
print("测试完成！")
print("="*50)