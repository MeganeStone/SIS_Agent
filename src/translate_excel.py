import re
import gc
import zipfile
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
from lxml import etree as ET
from langchain_core.tools import ToolException
from translate_text import translate_text
from dotenv import load_dotenv
load_dotenv()
import os
from pathlib import Path

# 获取当前脚本所在目录的父级目录（即 SIS_Agent 根目录）
SIS_AGENT_ROOT = Path(__file__).parent.parent

DEFAULT_INPUT_DIR = os.getenv("TRANSLATE_INPUT_DIR") or str(SIS_AGENT_ROOT / "translate" / "input")
DEFAULT_OUTPUT_DIR = os.getenv("TRANSLATE_OUTPUT_DIR") or str(SIS_AGENT_ROOT / "translate" / "output")
DEFAULT_TARGET_LANG = os.getenv("TRANSLATE_TARGET_LANG") or "日语"
DEFAULT_DELAY = float(os.getenv("TRANSLATE_DELAY") or 1.2)
MAX_WORKERS = int(os.getenv("MAX_WORKERS") or 6)

def _is_meaningful_text(text: str) -> bool:
    text = text.strip()
    if len(text) <= 1:
        return False
    if text.isdigit():
        return False
    if all(c in '，。！？；：""''《》【】（）,.!?;:\'"`~@#$%^&*()_+=-' for c in text):
        return False
    return True

def _translate_drawing_file(drawing_path, target_lang, delay, context):
    print(f"[DEBUG] 开始处理 drawing 文件: {drawing_path}")
    try:
        parser = ET.XMLParser(remove_blank_text=True)
        tree = ET.parse(drawing_path, parser)
        root = tree.getroot()
        a_uri = None
        for prefix, uri in root.nsmap.items():
            if prefix == 'a':
                a_uri = uri
                break
        if not a_uri:
            a_uri = 'http://schemas.openxmlformats.org/drawingml/2006/main'
        tag_p = f"{{{a_uri}}}p"
        tag_r = f"{{{a_uri}}}r"
        tag_t = f"{{{a_uri}}}t"
        modified_count = 0
        for p in root.iter(tag_p):
            runs = [r for r in p if r.tag == tag_r]
            if not runs:
                continue
            orig_text = ''
            for r in runs:
                t_elem = r.find(tag_t)
                if t_elem is not None and t_elem.text:
                    orig_text += t_elem.text
            orig_text = orig_text.strip()
            if not orig_text or not _is_meaningful_text(orig_text):
                continue
            translated = translate_text(orig_text, target_lang, delay, context)
            if not translated or translated == orig_text:
                continue
            first_run = runs[0]
            t_elem = first_run.find(tag_t)
            if t_elem is None:
                t_elem = ET.Element(tag_t)
                first_run.append(t_elem)
            t_elem.text = translated
            for r in runs[1:]:
                p.remove(r)
            modified_count += 1
        print(f"[DEBUG] 共修改了 {modified_count} 个段落")
        tree.write(drawing_path, encoding='utf-8', xml_declaration=True, pretty_print=False)
    except Exception as e:
        print(f"[ERROR] 处理 drawing 文件出错 {drawing_path}: {e}")
        import traceback
        traceback.print_exc()

def _collect_si_tasks(xml_path):
    tasks = []
    tree = ET.parse(xml_path)
    root = tree.getroot()
    for si in root.iter():
        if si.tag.endswith('}si'):
            # 收集所有直接子元素 <r>
            runs = [child for child in si if child.tag.endswith('}r')]
            if runs:
                orig_text = ''.join(
                    (r.find('./{*}t').text or '') if r.find('./{*}t') is not None else ''
                    for r in runs
                ).strip()
                if orig_text and _is_meaningful_text(orig_text):
                    tasks.append((si, runs, orig_text))
            else:
                # 没有 <r>，可能是直接 <t>
                t_elem = si.find('./{*}t')
                if t_elem is not None and t_elem.text and _is_meaningful_text(t_elem.text.strip()):
                    tasks.append((si, [t_elem], t_elem.text.strip()))
    return tasks, tree

def _translate_si_group(tasks_group, target_lang, delay, base_context, group_id):
    ctx = {
        "file_name": base_context["file_name"],
        "slide_num": 0,
        "total_slides": base_context["total_slides"],
        "translated_segments": [],
        "term_requirements": base_context["term_requirements"]
    }
    results = []
    for si, elements, original in tasks_group:
        translated = translate_text(original, target_lang, delay, context=ctx)
        if not translated:
            translated = original
        results.append((si, elements, translated))
        ctx["translated_segments"].append(f"原文：{original} | 译文：{translated}")
        if len(ctx["translated_segments"]) > 5:
            ctx["translated_segments"].pop(0)
    return results

def translate_excel_xml_based(input_path, output_path, target_lang, delay):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        with zipfile.ZipFile(input_path, 'r') as zf:
            zf.extractall(tmp_path)

        base_context = {
            "file_name": Path(input_path).name,
            "slide_num": 0,
            "total_slides": 0,
            "translated_segments": [],
            "term_requirements": "TBOX译为TBOX、TSU译为TSU、CAN总线译为CANバス（日语）/CAN Bus（英语），公司名称不翻译"
        }

        xl_dir = tmp_path / "xl"
        shared = xl_dir / "sharedStrings.xml"

        if shared.exists():
            print("[DEBUG] 开始处理 sharedStrings.xml...")
            tasks, tree = _collect_si_tasks(shared)
            if tasks:
                total = len(tasks)
                group_size = (total + MAX_WORKERS - 1) // MAX_WORKERS
                groups = [tasks[i:i+group_size] for i in range(0, total, group_size)]
                print(f"[DEBUG] 共 {total} 个字符串单元，分为 {len(groups)} 组并行翻译...")
                group_results = [None] * len(groups)
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    futures = {executor.submit(_translate_si_group, group, target_lang, delay, base_context, idx): idx
                               for idx, group in enumerate(groups)}
                    for future in as_completed(futures):
                        idx = futures[future]
                        try:
                            group_results[idx] = future.result()
                        except Exception as e:
                            print(f"组 {idx+1} 翻译失败: {e}")
                            group_results[idx] = []
                # 写回
                for grp_res in group_results:
                    for si, elements, translated in grp_res:
                        if elements and elements[0].tag.endswith('}r'):
                            # 有 <r> 的情况
                            first_run = elements[0]
                            t_elem = first_run.find('./{*}t')
                            if t_elem is None:
                                t_elem = ET.Element(f"{{{first_run.nsmap.get('', '')}}}t")
                                first_run.append(t_elem)
                            t_elem.text = translated
                            for r in elements[1:]:
                                si.remove(r)
                        elif elements and elements[0].tag.endswith('}t'):
                            # 只有 <t> 的情况
                            elements[0].text = translated
                tree.write(shared, encoding='utf-8', xml_declaration=True, pretty_print=False)
                print("[DEBUG] sharedStrings.xml 处理完成")
            else:
                print("[DEBUG] sharedStrings.xml 无可翻译的文本")
        else:
            print("[DEBUG] 未找到 sharedStrings.xml，单元格文本将不会被翻译")

        # 并行处理 drawing 和 chart
        files_to_process = []
        drawings_dir = xl_dir / "drawings"
        if drawings_dir.exists():
            files_to_process.extend(drawings_dir.glob("drawing*.xml"))
        charts_dir = xl_dir / "charts"
        if charts_dir.exists():
            files_to_process.extend(charts_dir.glob("chart*.xml"))

        if files_to_process:
            print(f"[DEBUG] 共找到 {len(files_to_process)} 个 drawing/chart 文件，开始并行处理...")
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                future_to_file = {}
                for file_path in files_to_process:
                    ctx = copy.deepcopy(base_context)
                    future = executor.submit(_translate_drawing_file, file_path, target_lang, delay, ctx)
                    future_to_file[future] = file_path
                for future in as_completed(future_to_file):
                    file_path = future_to_file[future]
                    try:
                        future.result()
                        print(f"[DEBUG] 完成处理: {file_path.name}")
                    except Exception as e:
                        print(f"[ERROR] 处理文件 {file_path} 出错: {e}")
        else:
            print("[DEBUG] 未找到 drawings/charts 目录，形状/图表文本将不会被翻译")

        # 重新打包
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file_path in tmp_path.rglob("*"):
                if file_path.is_file():
                    arcname = file_path.relative_to(tmp_path)
                    zf.write(file_path, arcname)

def translate_excel_file(file_name: str, source_dir: str = DEFAULT_INPUT_DIR,
                         output_dir: str = DEFAULT_OUTPUT_DIR,
                         target_lang: str = DEFAULT_TARGET_LANG,
                         delay: float = DEFAULT_DELAY) -> str:
    input_path = Path(source_dir) / file_name
    if not input_path.exists():
        raise ToolException(f"Excel文件不存在: {input_path}")
    output_path = Path(output_dir) / f"{input_path.stem}_{target_lang}{input_path.suffix}"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    translate_excel_xml_based(str(input_path), str(output_path), target_lang, delay)

    gc.collect()
    return f"Excel翻译完成！输出路径: {output_path}"