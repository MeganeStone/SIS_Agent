# 在 vector_db.py 文件顶部添加导入
from langchain_core.documents import Document

from PIL import Image
import functools
import hashlib

from unstructured.partition.auto import partition  # 自动分区函数
from unstructured.documents.elements import Element, Text, Table, Image
from dashscope import MultiModalConversation
from collections import defaultdict

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_community.embeddings import DashScopeEmbeddings
from langchain_classic.storage import LocalFileStore
from langchain_community.vectorstores.utils import filter_complex_metadata
import pickle
import os
import time
import gc
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
from pathlib import Path

# 获取当前脚本所在目录的父级目录（即 SIS_Agent 根目录）
SIS_AGENT_ROOT = Path(__file__).parent.parent

from synonyms import enhance_doc_synonyms  # 导入同义词增强函数
# ====================== 全局配置（新手只需改这里） ======================
# 1. 本地文档文件夹（存放所有TBOX文档，支持PDF/TXT）
TBOX_DOCS_DIR = os.getenv("TBOX_DOCS_DIR") or str(SIS_AGENT_ROOT / "tbox_docs")  # 可改成你的实际路径，如"D:/seki/AI/TBOX文档"
PARENT_STORE_DIR = os.getenv("PARENT_STORE_DIR") or str(SIS_AGENT_ROOT / "parent_store")  # 父文档存储路径
# 2. 向量库保存路径
VECTOR_DB_DIR = os.getenv("VECTOR_DB_DIR") or str(SIS_AGENT_ROOT / "tbox_vector_db")
# 3. embedding模型API Key（请替换成自己的）
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL") or "text-embedding-v4"  # 可替换成你想用的模型，如 "text-embedding-v4"
IMAGE_CAPTION_API_KEY = os.getenv("IMAGE_CAPTION_API_KEY")
IMAGE_CAPTION_LLM_MODEL = os.getenv("IMAGE_CAPTION_LLM_MODEL") or "qwen3.5-flash"  # 可替换成你想用的模型，如 "qwen3.5-flash"
# 定义“单例”向量库实例（初始为None）
_global_vector_db = None

def _get_path_hash(file_path: str) -> str:
    """返回文件路径的 MD5 哈希值（安全字符串）"""
    return hashlib.md5(file_path.encode('utf-8')).hexdigest()

# 嵌入模型配置（阿里云百炼）
def load_embedding_model(dashscope_api_key: str):
    return DashScopeEmbeddings(
        model=EMBEDDING_MODEL,
        dashscope_api_key=dashscope_api_key
    )

def create_parent_child_docs(file_path, dashscope_api_key: str):
    """从文件生成父子文档对，返回 (child_docs, parent_docs)"""
    # 先调用 load_and_aggregate_docs 获取已处理的文档（含图片描述）
    parent_docs = load_and_aggregate_docs(file_path,dashscope_api_key)
    if parent_docs is None:
        # 如果 Unstructured 失败，回退到手动方法（例如 load_ppt_doc/load_excel_doc）
        # 这里需要你根据文件类型实现回退
        return [], []

    child_docs = []
    child_splitter = RecursiveCharacterTextSplitter(chunk_size=300, chunk_overlap=50)
    file_hash = _get_path_hash(file_path)  # 生成文件路径哈希值，用于 parent_id，避免特殊字符问题
    # 递归地将复杂类型（如 dict、tuple、list 中的复杂元素）转换为可序列化的形式，并移除无法处理的类型。
    parent_docs = filter_complex_metadata(parent_docs)

    for idx, parent_doc in enumerate(parent_docs):
        # 为父块生成唯一 parent_id
        parent_id = f"{file_hash}__{idx}"   # 哈希值+索引，仅含字母/数字/__（__是合法的，因为哈希无非法字符）
        parent_doc.metadata["parent_id"] = parent_id

        # 切分子块
        children = child_splitter.split_documents([parent_doc])
        for child in children:
            child.metadata["parent_id"] = parent_id
            child_docs.append(child)

    return child_docs, parent_docs

def get_all_parent_docs(store_dir: str = PARENT_STORE_DIR):
    """从本地存储中加载所有父文档，返回 Document 列表"""
    store = LocalFileStore(store_dir)
    parent_docs = []
    for key in store.yield_keys():
        data = store.mget([key])[0]
        if data:
            doc = pickle.loads(data)
            parent_docs.append(doc)
    return parent_docs

def load_file_to_parent_child(file_path, dashscope_api_key: str):
    """根据文件类型返回 (child_docs, parent_docs)"""
    if file_path.endswith((".pdf",'.pptx', '.xlsx', ".docx")):
        return create_parent_child_docs(file_path,dashscope_api_key)
    else:
        # TXT：使用原有加载方式，每个块同时作为父块和子块
        docs = load_single_doc(file_path)  # 返回 Document 列表
        # 递归地将复杂类型（如 dict、tuple、list 中的复杂元素）转换为可序列化的形式，并移除无法处理的类型。
        docs = filter_complex_metadata(docs)
        child_docs = docs
        parent_docs = docs
        file_hash = _get_path_hash(file_path)
        # 为每个文档生成 parent_id（如果需要）
        for i, doc in enumerate(parent_docs):
            doc.metadata["parent_id"] = f"{file_hash}__{i}"
        return child_docs, parent_docs
    
# 定义统一的 Unstructured 加载函数（支持自动分区和图片描述）
def load_with_unstructured(file_path, use_blip=True, dashscope_api_key: str = None):
    """
    使用 Unstructured 解析文件，返回 Document 列表。
    对 Image 元素调用 BLIP 生成描述。
    """
    try:
        # 调用 unstructured 自动分区
        elements = partition(filename=file_path, languages=["jpn", "chi_sim", "eng"])
        docs = []

        # 遍历每个元素
        for idx, element in enumerate(elements):
            # 基本元数据
            metadata = {
                "file_name": os.path.basename(file_path),
                "file_mtime": os.path.getmtime(file_path),
                "file_path": file_path,
                "element_index": idx,
                "element_type": type(element).__name__,  # 例如 "Title", "Table", "Image"
            }

            # 合并 Unstructured 自动提取的元数据（如页码）
            if hasattr(element, 'metadata'):
                for key, value in element.metadata.to_dict().items():
                    metadata[f"unstructured_{key}"] = value

            # 获取元素文本内容
            content = element.text or ""
            # 👇 加这一行：自动给文档全称加缩写
            content = enhance_doc_synonyms(content)

            # 如果是图片元素且开启了 BLIP
            if use_blip and isinstance(element, Image):
                # 尝试从 metadata 中获取图片二进制数据
                # Unstructured 的 Image 元素通常会将图片保存为临时文件，路径在 metadata['image_path']
                # 1. 加载在线模型服务
                image_caption_func = load_vision_model(dashscope_api_key)
                image_path = element.metadata.get('image_path')
                if image_path and os.path.exists(image_path):
                    desc = image_caption_func(image_path)
                    if desc and desc != "[图片无法描述]":
                        content = desc
                        metadata["generated_description"] = True
                else:
                    # 无法获取图片数据，保留原内容（可能为空）
                    pass

            # 如果内容为空则跳过（除非是表格但无文本？表格元素一般有文本）
            if not content.strip():
                continue

            # 创建 Document 对象
            doc = Document(page_content=content.strip(), metadata=metadata)
            docs.append(doc)
            # 递归地将复杂类型（如 dict、tuple、list 中的复杂元素）转换为可序列化的形式，并移除无法处理的类型。
            docs = filter_complex_metadata(docs)  

        return docs
    except Exception as e:
        print(f"Unstructured 解析失败 {file_path}：{str(e)}，回退到手动方法")
        return None  # 返回 None 表示需要回退
    
def aggregate_docs_by_page(docs: list[Document]) -> list[Document]:
    """
    将按元素解析的文档列表，按页码聚合为按页的父文档。
    适用于 PPT, PDF, Word 等有页码概念的文档。
    """
    # 按页码分组
    page_groups = defaultdict(list)
    for doc in docs:
        # 从你的元数据中获取页码
        page_num = doc.metadata.get("unstructured_page_number", 0)
        page_groups[page_num].append(doc)

    # 生成按页聚合的父文档
    aggregated_docs = []
    for page_num in sorted(page_groups.keys()):
        doc_list = page_groups[page_num]
        # 合并当前页所有元素的内容
        full_content = "\n".join([doc.page_content for doc in doc_list])
        # 使用第一个文档的元数据作为基础，更新页码和内容
        base_metadata = doc_list[0].metadata.copy()
        base_metadata.update({
            "page_number": page_num,  # 简化的页码字段
            "aggregated": True,       # 标记为聚合后的父文档
            "original_elements_count": len(doc_list)  # 记录合并了多少个原始元素
        })
        aggregated_docs.append(Document(page_content=full_content, metadata=base_metadata))
    
    return aggregated_docs

def aggregate_excel_docs(docs: list[Document], chunk_size: int = 10) -> list[Document]:
    """
    将按单元格解析的Excel文档列表，按行数聚合为带表头的小文档块。
    :param docs: 原始解析的文档列表，每个单元格一个Document。
    :param chunk_size: 每个块包含的数据行数（不含表头）。
    :return: 聚合后的父文档列表。
    """
    if not docs:
        return []

    # 假设第一行是表头（Excel解析时，第一行的单元格会被优先解析）
    # 这里通过位置来判断，你可能需要根据实际情况调整
    header_docs = [docs[0]] if len(docs) > 0 else []
    data_docs = docs[1:] if len(docs) > 1 else []
    
    # 如果没有数据，只返回表头
    if not data_docs:
        header_content = "\n".join([doc.page_content for doc in header_docs])
        return [Document(page_content=header_content, metadata=header_docs[0].metadata.copy())]

    # 提取表头内容
    header_content = "\n".join([doc.page_content for doc in header_docs])
    aggregated_docs = []

    # 按chunk_size分块处理数据行
    for i in range(0, len(data_docs), chunk_size):
        chunk = data_docs[i:i + chunk_size]
        # 合并当前块的数据内容
        chunk_content = "\n".join([doc.page_content for doc in chunk])
        # 将表头和数据内容合并
        full_content = f"{header_content}\n{chunk_content}"
        # 使用块中第一个文档的元数据作为基础
        base_metadata = chunk[0].metadata.copy()
        base_metadata.update({
            "aggregated": True,
            "chunk_size": chunk_size,
            "row_start": i + 2,  # 数据行从第2行开始（表头是第1行）
            "row_end": min(i + chunk_size + 1, len(data_docs) + 1)
        })
        aggregated_docs.append(Document(page_content=full_content, metadata=base_metadata))
    
    return aggregated_docs

def load_and_aggregate_docs(file_path: str, use_blip: bool = True, dashscope_api_key: str = None) -> list[Document]:
    """
    加载文件并根据文件类型进行智能聚合，生成适合作为父文档的列表。
    这是你应该在主流程中调用的函数。
    """
    # 第一步：使用你原有的函数进行精细解析
    raw_docs = load_with_unstructured(file_path, use_blip=use_blip, dashscope_api_key=dashscope_api_key)
    
    # 第二步：根据文件后缀名选择聚合策略
    if file_path.lower().endswith((".pdf", ".pptx", ".docx", ".doc")):
        # PPT/PDF/Word：按页聚合
        return aggregate_docs_by_page(raw_docs)
    elif file_path.lower().endswith((".xlsx", ".xls")):
        # Excel：按行块聚合，保留表头
        return aggregate_excel_docs(raw_docs, chunk_size=10)
    else:
        # 其他文件类型：返回原始解析结果，不做聚合
        return raw_docs
    
@functools.lru_cache(maxsize=1)
def load_vision_model(dashscope_api_key: str):
    """
    阿里百炼 在线多模态模型（无需本地加载）
    直接返回调用函数，替代BLIP
    """
    def image_caption(image_path: str):
        """图像描述函数"""
        messages = [
            {
                "role": "user",
                "content": [
                    {"image": image_path},  # 本地图片路径
                    {"text": "请仅输出图像中的文本内容"}
                ]
            }
        ]
        # 调用通义千问VL模型
        response = MultiModalConversation.call(
            api_key=dashscope_api_key,
            model=IMAGE_CAPTION_LLM_MODEL,  # 免费高速模型
            messages=messages
        )
        return response.output.choices[0].message.content[0]["text"]
    
    return image_caption

# 3. 文本分割配置
TEXT_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
    separators=["\n\n", "\n", "。", "！", "？", "，", "、", " ", "."],
)

def init_docs_dir():
    """初始化本地文档文件夹（不存在则创建）"""
    if not os.path.exists(TBOX_DOCS_DIR):
        os.makedirs(TBOX_DOCS_DIR)
        print(f"✅ 已创建本地文档文件夹：{TBOX_DOCS_DIR}，请将TBOX文档放入该目录")

def get_local_docs_info():
    """获取本地文档文件夹的文件信息（文件名+修改时间）"""
    init_docs_dir()
    docs_info = {}
    for file in os.listdir(TBOX_DOCS_DIR):
        if file.endswith((".pdf", ".txt", ".pptx", ".xlsx", ".docx")):  # 支持PPT和Excel
            file_path = os.path.join(TBOX_DOCS_DIR, file)
            # 获取文件修改时间（时间戳，用于检测变更）
            mtime = os.path.getmtime(file_path)
            docs_info[file] = {
                "path": file_path,
                "mtime": mtime,
                "mtime_str": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            }
    return docs_info

def load_single_doc(file_path):
    """
    加载单个文档，返回分割后的文档列表。
    """
    if file_path.endswith('.txt'):
        # 文本文件继续用原有方法（Unstructured 对纯文本处理类似）
        loader = TextLoader(file_path, encoding="utf-8")
        docs = loader.load()
        # 👇 加这一行：自动给文档全称加缩写
        docs = enhance_doc_synonyms(docs)
        split_docs = TEXT_SPLITTER.split_documents(docs)
        mtime = os.path.getmtime(file_path)
        for doc in split_docs:
            doc.metadata.update({
                "file_name": os.path.basename(file_path),
                "file_mtime": mtime,
                "file_path": file_path,
                "doc_type": "txt"
            })
        return split_docs

    else:
        return []
    
def diff_update_vector_db(dashscope_api_key: str):
    """
    差分更新向量库（核心！只处理增/删/改的文件）
    逻辑：
    1. 对比本地文件 vs 向量库中的文件；
    2. 删除：向量库有但本地没有的文件 → 删对应向量；
    3. 修改：本地文件修改时间 > 向量库中的 → 先删旧向量，再插新向量；
    4. 新增：本地有但向量库没有的文件 → 插新向量；
    """
    start_time = time.time()
    print("🔍 开始差分更新向量库...")
    
    EMBEDDINGS = load_embedding_model(dashscope_api_key)  # 加载嵌入模型（包含路径处理和错误提示）
    # 1. 获取本地文件信息和向量库
    local_docs = get_local_docs_info()
    vector_db = Chroma(collection_name="all_child_docs", persist_directory=VECTOR_DB_DIR, embedding_function=EMBEDDINGS)
    # 获取向量库中的文件信息
    db_docs = vector_db.get()
    db_file_info = {}
    for idx, meta in enumerate(db_docs["metadatas"]):
        if "file_name" in meta:
            file_name = meta["file_name"]
            file_mtime = meta.get("file_mtime", 0)
            if file_name not in db_file_info or file_mtime > db_file_info[file_name]:
                db_file_info[file_name] = file_mtime

    # 初始化 docstore（父块存储）
    parent_store_dir = PARENT_STORE_DIR
    store = LocalFileStore(parent_store_dir)
    
    # 3. 第一步：删除向量库中已不存在的文件
    deleted_files = [f for f in db_file_info if f not in local_docs]
    if deleted_files:
        print(f"🗑️ 检测到已删除的文件：{deleted_files}")
        for file_name in deleted_files:
            # 找到该文件对应的所有向量ID并删除
            del_ids = [db_docs["ids"][i] for i, meta in enumerate(db_docs["metadatas"]) 
                       if meta.get("file_name") == file_name]
            if del_ids:
                vector_db.delete(ids=del_ids)
            file_path = None
            keys_to_delete = []
            for key in store.yield_keys():
                if key.startswith(file_name):  # 不准确，因为 file_name 可能不包含路径
                    keys_to_delete.append(key)
            if keys_to_delete:
                store.mdelete(keys_to_delete)
        print(f"✅ 已删除{len(deleted_files)}个文件的向量和父块")
    
    # 4. 第二步：处理修改/新增的文件
    updated_count = 0
    added_count = 0
    for file_name, file_info in local_docs.items():
        file_path = file_info["path"]
        local_mtime = file_info["mtime"]
        
        # 新增文件：向量库中没有
        if file_name not in db_file_info:
            print(f"➕ 新增文件：{file_name}")
            # 生成父子文档
            child_docs, parent_docs = load_file_to_parent_child(file_path)  # 需要实现此函数
            if child_docs:
                vector_db.add_documents(child_docs)
                added_count += 1
            # 将父块存入 docstore
            for parent_doc in parent_docs:
                parent_id = parent_doc.metadata.get("parent_id")
                if parent_id:
                    # 序列化并存储
                    store.mset([(parent_id, pickle.dumps(parent_doc))])

        # 修改文件：本地修改时间更新
        elif local_mtime > db_file_info[file_name]:
            print(f"🔄 修改文件：{file_name}")
            # 先删旧向量
            del_ids = [db_docs["ids"][i] for i, meta in enumerate(db_docs["metadatas"]) 
                       if meta.get("file_name") == file_name]
            if del_ids:
                vector_db.delete(ids=del_ids)
            # 再删旧父块
            keys_to_delete = []
            for key in store.yield_keys():
                if key.startswith(file_path):
                    keys_to_delete.append(key)
            if keys_to_delete:
                store.mdelete(keys_to_delete)
            # 再插新向量
            # 生成父子文档
            child_docs, parent_docs = load_file_to_parent_child(file_path)  # 需要实现此函数
            if child_docs:
                vector_db.add_documents(child_docs)
                updated_count += 1
            # 将父块存入 docstore
            for parent_doc in parent_docs:
                parent_id = parent_doc.metadata.get("parent_id")
                if parent_id:
                    # 序列化并存储
                    store.mset([(parent_id, pickle.dumps(parent_doc))])
    
    # 5. 保存向量库
    # vector_db.persist()
    cost_time = round(time.time() - start_time, 2)
    print(f"""
    ✅ 向量库差分更新完成！
    - 新增文件：{added_count}个
    - 修改文件：{updated_count}个
    - 删除文件：{len(deleted_files)}个
    - 耗时：{cost_time}秒
    """)
    # 清理内存
    gc.collect()
    global _global_vector_db
    _global_vector_db = vector_db  # 更新全局实例
    return vector_db

# 初始化向量库的函数（首次全量，后续直接加载）
def init_or_load_vector_db(dashscope_api_key: str):
    """初始化/加载向量库（首次全量，后续差分）"""
    EMBEDDINGS = load_embedding_model(dashscope_api_key)  # 加载嵌入模型（包含路径处理和错误提示）
    try:
        # 确保 docstore 目录存在
        parent_store_dir = PARENT_STORE_DIR
        os.makedirs(parent_store_dir, exist_ok=True)
        store = LocalFileStore(parent_store_dir)
        if not os.path.exists(VECTOR_DB_DIR) or len(os.listdir(VECTOR_DB_DIR)) == 0:
            print("📦 首次使用，全量构建向量库...")
            local_docs = get_local_docs_info()
            all_child_docs = []
            # 用于存储父块，稍后统一存入 docstore
            all_parent_docs = []
            for file_name, file_info in local_docs.items():
                print(f"📄 加载文件：{file_name}")
                # 生成父子文档
                child_docs, parent_docs = load_file_to_parent_child(file_info["path"],dashscope_api_key)
                all_child_docs.extend(child_docs)
                all_parent_docs.extend(parent_docs)

            if all_child_docs:
                vector_db = Chroma(
                    collection_name="all_child_docs",
                    embedding_function=EMBEDDINGS,
                    persist_directory=VECTOR_DB_DIR
                )
                # ========== 补充关键步骤：将子块添加到向量库 ==========
                vector_db.add_documents(all_child_docs)
                # vector_db.persist()
                # 父块存入 docstore
                # 使用字典批量存储以提高效率
                parent_dict = {}
                for parent in all_parent_docs:
                    parent_id = parent.metadata.get("parent_id")
                    if parent_id:
                        parent_dict[parent_id] = pickle.dumps(parent)
                if parent_dict:
                    store.mset(list(parent_dict.items()))
                print(f"✅ 首次全量构建完成：子块{len(all_child_docs)}个，父块{len(all_parent_docs)}个")
            else:
                vector_db = Chroma(collection_name="all_child_docs", persist_directory=VECTOR_DB_DIR, embedding_function=EMBEDDINGS)
                print("⚠️ 本地文档文件夹为空，向量库为空")
        else:
            vector_db = Chroma(collection_name="all_child_docs", persist_directory=VECTOR_DB_DIR, embedding_function=EMBEDDINGS)
            print(f"✅ 加载已有向量库：{VECTOR_DB_DIR}")
        return vector_db
    except Exception as e:
        print(f"向量库初始化失败：{str(e)}")
        # 返回空向量库，避免程序崩溃
        return Chroma(collection_name="all_child_docs", persist_directory=VECTOR_DB_DIR, embedding_function=EMBEDDINGS)


def get_vector_db(dashscope_api_key: str):
    """获取全局向量库实例（确保已初始化）"""
    global _global_vector_db
    if _global_vector_db is None:
        _global_vector_db = init_or_load_vector_db(dashscope_api_key)
        print(f"【向量库初始化完成】当前文档数量：{len(_global_vector_db.get()['metadatas'])}")
    else:
        print("【向量库实例已存在】直接使用")
    return _global_vector_db
