# ===================== 1. 公司专用同义词词典（你自己维护）=====================
COMPANY_SYNONYMS = {
    "DUF": "DAQ Upload File",
    "DAQ Upload File": "DUF",
    "DSF": "DAQ Setting File",
    "DAQ Setting File": "DSF",
    # 你可以无限加公司缩写
    # "SOP": "标准作业程序",
    # "KPI": "关键绩效指标",
}

# ===================== 2. 查询文本扩展（用户问DUF → 自动搜DUF+全称）=====================
def expand_query_synonyms(query: str) -> str:
    """
    用户查询预处理：扩展缩写为全称，提升召回
    例：用户问 "DUF是什么" → 转为 "DUF OR DAQ Upload File 是什么"
    """
    for abbr, full in COMPANY_SYNONYMS.items():
        # 全词匹配，避免误替换
        query = query.replace(f" {abbr} ", f" {abbr} OR {full} ")
        query = query.replace(f" {full} ", f" {full} OR {abbr} ")
    return query

# ===================== 3. 文档文本增强（解析时标注缩写）=====================
def enhance_doc_synonyms(content: str) -> str:
    """
    文档内容增强：给全称自动加缩写标注
    例：DAQ Upload File → DAQ Upload File (DUF)
    """
    for abbr, full in COMPANY_SYNONYMS.items():
        content = content.replace(full, f"{full} ({abbr})")
    return content