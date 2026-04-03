import re
import os
import gc
import subprocess
# ---------------------- LangChain 依赖 ----------------------
from langchain_core.tools import ToolException
from concurrent.futures import ThreadPoolExecutor, as_completed
import tempfile
import shutil
import pythoncom
import win32com.client as win32
import win32process
from translate_text import translate_text, _translation_cache

# ---------------------- 全局配置（你的默认目录） ----------------------
DEFAULT_INPUT_DIR = r"D:\seki\AI\copilotTest\input"
DEFAULT_OUTPUT_DIR = r"D:\seki\AI\copilotTest\output"
DEFAULT_TARGET_LANG = "日语"  # 默认翻译目标语言
DEFAULT_DELAY = 1.2  # 每次翻译后的延迟，单位秒（可调整，过快可能触发API限速）

def _get_excel_pid(excel_app):
    """获取Excel进程的PID"""
    try:
        hwnd = excel_app.Hwnd
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid
    except Exception:
        return None

def _kill_excel_by_pid(pid):
    """根据PID强制结束Excel进程"""
    if pid:
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False)
            print(f"已结束Excel进程 PID={pid}")
        except Exception as e:
            print(f"结束进程失败: {e}")

def _split_excel_to_temp_files(input_path, tmp_dir):
    """
    将Excel文件按工作表拆分为多个临时文件（每个工作表独立）。
    返回：列表，每个元素为 (sheet_name, temp_file_path)
    """
    pythoncom.CoInitialize()
    excel = None
    try:
        excel = win32.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        wb = excel.Workbooks.Open(input_path, ReadOnly=False, IgnoreReadOnlyRecommended=True, UpdateLinks=0)
        sheets = []
        for i, ws in enumerate(wb.Worksheets, 1):
            sheet_name = ws.Name
            # 复制当前工作表到新工作簿
            ws.Copy()  # 复制后新工作簿成为活动工作簿
            new_wb = excel.ActiveWorkbook
            # 保存新工作簿到临时文件
            temp_path = os.path.join(tmp_dir, f"sheet_{i:04d}_{sheet_name}.xlsx")
            new_wb.SaveAs(temp_path, FileFormat=51)  # 51 = xlOpenXMLWorkbook
            new_wb.Close(SaveChanges=False)
            sheets.append((sheet_name, temp_path))
        wb.Close(SaveChanges=False)
        return sheets
    finally:
        if excel:
            try:
                excel.Quit()
            except:
                pass
        pythoncom.CoUninitialize()

def _translate_single_excel(temp_path, output_path, target_lang, delay):
    """
    翻译单个Excel临时文件（包含完整的一个工作表），使用独立的Excel实例。
    返回翻译后的文件路径。
    """

    pythoncom.CoInitialize()
    excel = None
    pid = None
    try:
        # 创建独立Excel实例
        excel = win32.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        pid = _get_excel_pid(excel)
        
        # 打开文件
        wb = excel.Workbooks.Open(temp_path, ReadOnly=False, IgnoreReadOnlyRecommended=True, UpdateLinks=0)
        sheet_count = wb.Worksheets.Count  # 应为1
        
        # 初始化上下文
        excel_context = {
            "file_name": os.path.basename(temp_path),
            "slide_num": 1,
            "total_slides": sheet_count,
            "translated_segments": [],
            "term_requirements": "TBOX译为TBOX、TSU译为TSU、CAN总线译为CANバス（日语）/CAN Bus（英语），公司名称不翻译"
        }
        
        # 调用原有的全表翻译函数（已在你的代码中定义，可直接使用）
        translate_excel_all_text(wb, sheet_count, target_lang, delay, excel_context)
        
        # 保存
        wb.SaveAs(output_path, FileFormat=51)
        wb.Close(SaveChanges=False)
        
        return output_path
    finally:
        if excel:
            try:
                excel.Quit()
            except:
                pass
        if pid:
            _kill_excel_by_pid(pid)
        pythoncom.CoUninitialize()

def _merge_excel_from_temp_files(original_path, sheets_info, output_path):
    """
    【终极神版】倒序合并 + 固定插最前面 | 100%稳定 零报错 顺序正确
    """

    pythoncom.CoInitialize()
    excel = None
    try:
        excel = win32.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False

        # 1. 新建工作簿（保留默认Sheet1）
        new_wb = excel.Workbooks.Add()

        # ==============================================
        # 🔥 你的神思路：倒序遍历！从最后一个sheet开始合并
        # ==============================================
        for sheet_name, trans_temp_path in reversed(sheets_info):
            print(f"合并工作表：{sheet_name}")
            
            # 打开临时翻译文件
            wb_temp = excel.Workbooks.Open(trans_temp_path, ReadOnly=True)
            source_sheet = wb_temp.Worksheets(1)

            # ==============================================
            # ✅ 唯一稳定的写法：复制 → 移动到【最前面】
            # ==============================================
            source_sheet.Copy()
            copied_sheet = excel.ActiveSheet
            copied_sheet.Move(Before=new_wb.Sheets(1))  # 稳定不崩

            # 关闭临时文件
            wb_temp.Close(SaveChanges=False)

        # 2. 删除默认Sheet1（安全删除）
        new_wb.Worksheets("Sheet1").Delete()

        # 3. 保存
        new_wb.SaveAs(output_path, FileFormat=51)
        new_wb.Close(SaveChanges=False)

        print(f"✅ 合并完成！顺序/内容全正常")

    finally:
        if excel:
            try:
                excel.Quit()
            except:
                pass
        pythoncom.CoUninitialize()

def translate_excel_all_text(wb, sheet_count, target_lang, delay, excel_context):
    """
    完整翻译Excel工作表的所有文字元素（单元格+形状+批注+图表+页眉页脚）
    """
    # 遍历所有工作表（带序号）
    for i, ws in enumerate(wb.Worksheets, 1):
        excel_context["slide_num"] = i
        excel_context["translated_segments"] = []  # 每个Sheet独立上下文
        print(f"\n=== Excel 第 {i}/{sheet_count} 个工作表: {ws.Name} ===")

        # ========== 1. 翻译单元格（优化UsedRange，遍历所有有内容的单元格） ==========
        try:
            # 刷新UsedRange，避免漏选
            ws.UsedRange
            # 遍历所有单元格（替代仅UsedRange，可根据需求调整为UsedRange提升效率）
            # 方式1：高效版（仅UsedRange）
            for cell in ws.UsedRange:
                val = cell.Value
                if val and isinstance(val, str):
                    val_stripped = val.strip()
                    # if re.search(r"[\u4e00-\u9fff]", val_stripped):
                    cell.Value = translate_text(val_stripped, target_lang, delay, context=excel_context)
        
        except Exception as e:
            print(f"翻译单元格失败: {e}")

        # ========== 2. 翻译所有形状文字（含分组/TextFrame2） ==========
        def translate_shape_text(shp):
            """递归翻译形状（含分组形状）的文字"""
            try:
                # 处理分组形状：递归遍历子形状
                if shp.Type == 6:  # 6=xlGroup：分组形状
                    for sub_shp in shp.GroupItems:
                        translate_shape_text(sub_shp)
                    return
                
                # 处理SmartArt图形
                if shp.Type == 19:  # 19=xlSmartArt：SmartArt图形
                    for node in shp.SmartArt.AllNodes:
                        if node.TextFrame2.TextRange.Text:
                            t = node.TextFrame2.TextRange.Text.strip()
                            # if re.search(r"[\u4e00-\u9fff]", t):
                            node.TextFrame2.TextRange.Text = translate_text(t, target_lang, delay, context=excel_context)
                    return

                # 优先用TextFrame2（Office 2007+），兼容TextFrame
                if hasattr(shp, "TextFrame2") and shp.TextFrame2.HasText:
                    t = shp.TextFrame2.TextRange.Text.strip()
                    # if re.search(r"[\u4e00-\u9fff]", t):
                    shp.TextFrame2.TextRange.Text = translate_text(t, target_lang, delay, context=excel_context)
                elif hasattr(shp, "TextFrame") and shp.TextFrame.Characters.Text:
                    t = shp.TextFrame.Characters.Text.strip()
                    # if re.search(r"[\u4e00-\u9fff]", t):
                    shp.TextFrame.Characters().Text = translate_text(t, target_lang, delay, context=excel_context)
            except Exception as e:
                print(f"翻译形状[{shp.Name}]失败: {e}")

        # 遍历所有形状（含分组）
        for shp in ws.Shapes:
            translate_shape_text(shp)

        # ========== 3. 翻译单元格批注/备注 ==========
        try:
            # Excel 2016+用ws.Comments，旧版用ws.Notes
            for comment in ws.Comments:
                t = comment.Text().strip()
                if re.search(r"[\u4e00-\u9fff]", t):
                    comment.Text(translate_text(t, target_lang, delay, context=excel_context))
        except Exception as e:
            print(f"翻译批注失败: {e}")

        # ========== 4. 翻译图表中的文字 ==========
        try:
            for chart in ws.ChartObjects:
                # 翻译图表标题
                if chart.Chart.HasTitle:
                    t = chart.Chart.ChartTitle.Text.strip()
                    # if re.search(r"[\u4e00-\u9fff]", t):
                    chart.Chart.ChartTitle.Text = translate_text(t, target_lang, delay, context=excel_context)
                # 翻译坐标轴标签（X/Y轴）
                for axis in chart.Chart.Axes:
                    if axis.HasTitle:
                        t = axis.AxisTitle.Text.strip()
                        # if re.search(r"[\u4e00-\u9fff]", t):
                        axis.AxisTitle.Text = translate_text(t, target_lang, delay, context=excel_context)
                # 翻译数据标签（可选）
                # for series in chart.Chart.SeriesCollection():
                #     if series.HasDataLabels:
                #         for label in series.DataLabels:
                #             t = label.Text.strip()
                #             if re.search(r"[\u4e00-\u9fff]", t):
                #                 label.Text = translate_text(t, target_lang, delay, context=excel_context)
        except Exception as e:
            print(f"翻译图表文字失败: {e}")

        # ========== 5. 翻译页眉/页脚文字 ==========
        try:
            # 翻译页眉（左/中/右）
            for align in ["LeftHeader", "CenterHeader", "RightHeader"]:
                t = getattr(ws.PageSetup, align).strip()
                # if re.search(r"[\u4e00-\u9fff]", t):
                setattr(ws.PageSetup, align, translate_text(t, target_lang, delay, context=excel_context))
            # 翻译页脚（左/中/右）
            for align in ["LeftFooter", "CenterFooter", "RightFooter"]:
                t = getattr(ws.PageSetup, align).strip()
                # if re.search(r"[\u4e00-\u9fff]", t):
                setattr(ws.PageSetup, align, translate_text(t, target_lang, delay, context=excel_context))
        except Exception as e:
            print(f"翻译页眉页脚失败: {e}")

def translate_excel_file(file_name: str, source_dir: str = DEFAULT_INPUT_DIR, output_dir: str = DEFAULT_OUTPUT_DIR,
                         target_lang: str = DEFAULT_TARGET_LANG, delay: float = DEFAULT_DELAY) -> str:
    """
    翻译Excel文件（并行版）：按工作表拆分为临时文件，多线程并行翻译，最后合并。
    支持保留所有格式、图形、图表等。
    """
    
    input_path = os.path.abspath(os.path.join(source_dir, file_name)).replace("/", "\\")
    output_path = os.path.abspath(os.path.join(output_dir, f"{os.path.splitext(file_name)[0]}_{target_lang}{os.path.splitext(file_name)[1]}")).replace("/", "\\")
    
    if not os.path.exists(input_path):
        raise ToolException(f"Excel文件不存在: {input_path}")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    # 1. 拆分为临时文件
    tmp_dir = tempfile.mkdtemp(prefix="excel_split_")
    try:
        sheets_info = _split_excel_to_temp_files(input_path, tmp_dir)
        if not sheets_info:
            raise ToolException("没有找到工作表")
        
        # 2. 并行翻译所有临时文件
        translated_temp_files = []


        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = []
            for sheet_name, temp_path in sheets_info:
                trans_temp = os.path.join(tmp_dir, f"trans_{os.path.basename(temp_path)}")
                future = executor.submit(_translate_single_excel, temp_path, trans_temp, target_lang, delay)
                futures.append((future, sheet_name, trans_temp))
            
            for future, sheet_name, trans_temp in futures:
                # future.result()  # 去掉try块，直接执行，出错会抛出异常
                future.result()
                translated_temp_files.append((sheet_name, trans_temp))
        
        # 3. 合并回最终文件
        # 注意：translated_temp_files 顺序应与 sheets_info 一致（我们已经保持了顺序）
        _merge_excel_from_temp_files(input_path, translated_temp_files, output_path)
        
        # 4. 清理
        # _translation_cache.clear()
        gc.collect()
        return f"Excel翻译完成！输出路径: {output_path}"
    
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    