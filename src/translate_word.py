# translate_word.py
import os
import gc
import time
import subprocess
import pythoncom
import win32com.client as win32
import win32process
from langchain_core.tools import ToolException
from translate_text import translate_text, _translation_cache

# ---------------------- 全局配置 ----------------------
DEFAULT_INPUT_DIR = r"D:\seki\AI\copilotTest\input"
DEFAULT_OUTPUT_DIR = r"D:\seki\AI\copilotTest\output"
DEFAULT_TARGET_LANG = "日语"
DEFAULT_DELAY = 1.2

# Word 保存格式常量
wdFormatDocument = 0
wdFormatXMLDocument = 12
wdFormatXMLDocumentMacroEnabled = 13


def _get_word_pid(word_app):
    try:
        hwnd = word_app.Application.Hwnd
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid
    except Exception:
        return None


def _kill_word_by_pid(pid):
    if pid:
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False)
            print(f"已结束 Word 进程 PID={pid}")
        except Exception as e:
            print(f"结束进程失败: {e}")


def _get_save_format(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".docx":
        return wdFormatXMLDocument
    elif ext == ".docm":
        return wdFormatXMLDocumentMacroEnabled
    elif ext == ".doc":
        return wdFormatDocument
    else:
        return wdFormatXMLDocument


def _is_toc_paragraph(para):
    """判断是否为目录段落"""
    try:
        return para.Style.NameLocal.startswith("TOC")
    except:
        return False


def _has_fields(para):
    """判断段落是否包含域代码（如页码、交叉引用等）"""
    try:
        return para.Range.Fields.Count > 0
    except:
        return False


def _is_pure_text_run(run):
    """
    判断 Run 是否只包含纯文本（无图片、无其他嵌入对象）
    返回 True 表示可以安全替换文本
    """
    try:
        # 如果 Run 包含内联图片，则不处理
        if run.InlineShapes.Count > 0:
            return False
        # 如果有其他形状或嵌入对象，也可以进一步判断，但 InlineShapes 已经覆盖常见情况
        return True
    except:
        return False


def translate_run(run, target_lang, delay, context):
    """翻译单个 Run 的文本（如果非空且是纯文本）"""
    try:
        if not run.Text or not run.Text.strip():
            return
        # 跳过过短文本
        if len(run.Text.strip()) <= 1:
            return
        # 如果 Run 包含非文本内容，跳过
        if not _is_pure_text_run(run):
            return

        original = run.Text.strip()
        translated = translate_text(original, target_lang, delay, context=context)
        if translated:
            run.Text = translated
            # 更新上下文（保留最近5条）
            context["translated_segments"].append(f"原文：{original} | 译文：{translated}")
            if len(context["translated_segments"]) > 5:
                context["translated_segments"].pop(0)
    except Exception as e:
        # 静默失败，不中断整体流程
        print(f"翻译 Run 失败: {e}")


def process_paragraph(para, target_lang, delay, context):
    """处理单个段落：遍历其 Runs，只翻译纯文本 Run"""
    try:
        # 跳过目录段落、含域代码段落
        if _is_toc_paragraph(para) or _has_fields(para):
            return
        # 获取段落的 Range 并遍历 Runs
        rng = para.Range
        for run in rng.Runs:
            translate_run(run, target_lang, delay, context)
    except Exception as e:
        print(f"处理段落失败: {e}")


def process_cell(cell, target_lang, delay, context):
    """处理表格单元格：遍历其中的所有段落"""
    try:
        for para in cell.Range.Paragraphs:
            process_paragraph(para, target_lang, delay, context)
    except Exception as e:
        print(f"处理单元格失败: {e}")


def translate_word_file(file_name: str, source_dir: str = DEFAULT_INPUT_DIR,
                        output_dir: str = DEFAULT_OUTPUT_DIR,
                        target_lang: str = DEFAULT_TARGET_LANG,
                        delay: float = DEFAULT_DELAY) -> str:
    """
    翻译 Word 文件（逐 Run 处理版）
    - 只处理主体段落和表格单元格中的纯文本 Run
    - 保留图片、表格结构、形状等所有非文本对象
    - 避免破坏文档格式
    """
    global _translation_cache
    _translation_cache = {}

    input_path = os.path.abspath(os.path.join(source_dir, file_name))
    output_path = os.path.abspath(os.path.join(
        output_dir,
        f"{os.path.splitext(file_name)[0]}_{target_lang}{os.path.splitext(file_name)[1]}"
    ))

    if not os.path.exists(input_path):
        raise ToolException(f"Word 文件不存在: {input_path}")

    os.makedirs(output_dir, exist_ok=True)

    pythoncom.CoInitialize()
    word = None
    pid = None
    try:
        word = win32.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = False
        pid = _get_word_pid(word)

        doc = word.Documents.Open(input_path, ReadOnly=False, AddToRecentFiles=False)

        # 计算总处理单元数（用于进度显示）
        total_units = 0
        # 主体段落
        for para in doc.Paragraphs:
            if _is_toc_paragraph(para) or _has_fields(para):
                continue
            try:
                total_units += para.Range.Runs.Count
            except:
                pass
        # 表格单元格
        for table in doc.Tables:
            for row in table.Rows:
                for cell in row.Cells:
                    try:
                        total_units += cell.Range.Paragraphs.Count
                    except:
                        pass

        context = {
            "file_name": file_name,
            "slide_num": 0,
            "total_slides": total_units,
            "translated_segments": [],
            "term_requirements": "TBOX译为TBOX、TSU译为TSU、CAN总线译为CANバス（日语）/CAN Bus（英语），公司名称不翻译"
        }

        print(f"\n开始翻译 Word 文档，共约 {total_units} 个文本单元...")

        # 1. 处理主体段落
        processed = 0
        for para in doc.Paragraphs:
            if _is_toc_paragraph(para) or _has_fields(para):
                continue
            try:
                for run in para.Range.Runs:
                    processed += 1
                    context["slide_num"] = processed
                    translate_run(run, target_lang, delay, context)
            except Exception as e:
                print(f"处理段落时出错: {e}")

        # 2. 处理表格单元格
        for table in doc.Tables:
            for row in table.Rows:
                for cell in row.Cells:
                    try:
                        for para in cell.Range.Paragraphs:
                            processed += 1
                            context["slide_num"] = processed
                            process_paragraph(para, target_lang, delay, context)
                    except Exception as e:
                        print(f"处理单元格时出错: {e}")

        # 保存文档
        save_format = _get_save_format(output_path)
        doc.SaveAs2(output_path, FileFormat=save_format)
        doc.Close(SaveChanges=False)

        print(f"\n✅ Word 翻译完成！输出路径: {output_path}")

        _translation_cache.clear()
        gc.collect()
        return f"Word翻译完成！输出路径: {output_path}"

    except Exception as e:
        raise ToolException(f"Word 翻译失败: {e}") from e
    finally:
        if word:
            try:
                word.Quit()
                time.sleep(1)
            except:
                pass
        if pid:
            _kill_word_by_pid(pid)
        pythoncom.CoUninitialize()