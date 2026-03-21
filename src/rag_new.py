from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder, PromptTemplate
from langchain_community.retrievers import BM25Retriever
from langchain_classic.retrievers import EnsembleRetriever  # 修正导入路径
import jieba

from langchain_classic.chains.retrieval import create_retrieval_chain
from langchain_classic.chains.combine_documents import create_stuff_documents_chain
from langchain_classic.retrievers import ParentDocumentRetriever
from langchain_classic.retrievers import ContextualCompressionRetriever
from langchain_classic.storage import LocalFileStore
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rerank import DashScopeRerank
from vector_db_new import get_all_parent_docs

# ====================== 全局配置（新手只需改这里） ======================
# 4. 大模型配置（请替换为自己的API Key）
DASHSCOPE_API_KEY = "sk-10579025107e412983a48273c2ff7d3f"  # 替换成自己的！

LLM = ChatOpenAI(
    model="qwen-plus",
    temperature=0.1,
    api_key=DASHSCOPE_API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    timeout=300,
    extra_body={"enable_search": True}
)

# 【核心】给BM25注册中文分词器
def chinese_tokenizer(text: str):
    return jieba.lcut(text)

# ---------- 新增：打印包装器，用于输出原始召回结果 ----------
from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from typing import List
from langchain_core.documents import Document
from pydantic import Field

# 替换原PrintingRetrieverWrapper为以下增强版本
# class PrintingRetrieverWrapper(BaseRetriever):
#     """增强版：打印ParentDocumentRetriever的完整执行过程"""
#     retriever: BaseRetriever = Field(description="The retriever to wrap")
#     name: str = Field(description="Name for printing")

#     def __init__(self, retriever: BaseRetriever, name: str):
#         super().__init__(retriever=retriever, name=name)

#     def _get_relevant_documents(
#         self, query: str, *, run_manager: CallbackManagerForRetrieverRun
#     ) -> List[Document]:
#         # 如果是ParentDocumentRetriever，打印内部执行细节
#         if isinstance(self.retriever, ParentDocumentRetriever):
#             print(f"\n【{self.name} 内部调试】")
#             # 1. 手动执行子块检索
#             child_docs = self.retriever.vectorstore.similarity_search(
#                 query, **self.retriever.search_kwargs
#             )
#             print(f"  子块检索数量：{len(child_docs)}")
#             # 2. 提取parent_id
#             parent_ids = [doc.metadata.get("parent_id") for doc in child_docs if doc.metadata.get("parent_id")]
#             print(f"  有效parent_id数量：{len(parent_ids)}")
#             # 3. 检查docstore中父文档
#             valid_parents = []
#             for pid in parent_ids:
#                 try:
#                     parent_doc = self.retriever.docstore.mget([pid])[0]
#                     if parent_doc:
#                         valid_parents.append(pid)
#                 except:
#                     pass
#             print(f"  docstore中存在的父文档数量：{len(valid_parents)}")
        
#         # 执行原检索逻辑
#         docs = self.retriever._get_relevant_documents(query, run_manager=run_manager)
#         print(f"\n【{self.name} 原始召回（重排序前）】")
#         if not docs:
#             print("  未召回任何文档！")
#         else:
#             for i, doc in enumerate(docs):
#                 print(f"文档 {i+1}:")
#                 print(f"  内容: {doc.page_content[:200]}...")
#                 print(f"  元数据: {doc.metadata}")
#         return docs
# ---------------------------------------------------------

def build_qa_chain(vector_db):
    # 1. 加载父文档存储
    parent_store_dir = "./parent_store"
    docstore = LocalFileStore(parent_store_dir)
    child_splitter = RecursiveCharacterTextSplitter(chunk_size=300, chunk_overlap=50)  # 和vector_db_new.py中一致
    
    # ========== 修正：自定义父文档检索器（对齐MultiVectorRetriever + Pydantic字段） ==========
    from langchain_classic.retrievers import MultiVectorRetriever  # 修正父类
    from langchain_core.callbacks import CallbackManagerForRetrieverRun
    from typing import List, Optional
    from langchain_core.documents import Document
    from pydantic import Field
    import pickle
    class CustomParentDocumentRetriever(MultiVectorRetriever):
        """
        自定义父文档检索器（对齐原生ParentDocumentRetriever的继承链和字段）
        解决原生ParentDocumentRetriever空召回问题
        """
        # 显式定义Pydantic字段（必须！否则报ValueError）
        vectorstore: object = Field(description="向量库实例")
        docstore: object = Field(description="父文档存储实例")
        search_kwargs: dict = Field(default={"k": 20}, description="向量检索参数")
        
        def _get_relevant_documents(
            self, query: str, *, run_manager: Optional[CallbackManagerForRetrieverRun] = None
        ) -> List[Document]:
            # 步骤1：从向量库检索子块（和原生逻辑一致）
            child_docs = self.vectorstore.similarity_search(query, **self.search_kwargs)
            print(f"\n【父文档检索器（向量） 内部调试】")
            print(f"  子块检索数量：{len(child_docs)}")
            
            # 步骤2：提取并去重parent_id
            parent_ids = list({doc.metadata["parent_id"] for doc in child_docs if doc.metadata.get("parent_id")})
            print(f"  有效parent_id数量：{len(parent_ids)}")
            
            # 步骤3：从docstore加载父文档（手动实现，绕过原生bug）
            parent_docs = []
            if parent_ids:
                # 批量获取父文档（提升效率）
                parent_data_list = self.docstore.mget(parent_ids)
                valid_parent_count = 0
                for pid, p_data in zip(parent_ids, parent_data_list):
                    if p_data:
                        valid_parent_count += 1
                        try:
                            parent_doc = pickle.loads(p_data)
                            # 合法性校验：确保是Document且内容非空
                            if isinstance(parent_doc, Document) and parent_doc.page_content.strip():
                                parent_docs.append(parent_doc)
                        except Exception as e:
                            print(f"  加载父文档失败 {pid}：{str(e)}")
                print(f"  docstore中存在的父文档数量：{valid_parent_count}")
            return parent_docs
        
    # 初始化自定义检索器（完全替代原生ParentDocumentRetriever）
    parent_retriever = CustomParentDocumentRetriever(
        vectorstore=vector_db,
        docstore=docstore,
        child_splitter=child_splitter,  # 保留该参数（对齐原生接口）
        search_kwargs={"k": 20},
        # 以下为MultiVectorRetriever必需的默认字段（原生ParentDocumentRetriever会自动处理）
        id_key="parent_id",  # 关联子块和父块的元数据键
        vectorstore_kwargs={},
    )
    # ========== 自定义检索器结束 ==========
    
    # 3. 构建 BM25 检索器（基于父块）
    all_parent_docs = get_all_parent_docs(parent_store_dir)  # 从存储加载所有父块
    if all_parent_docs:
        bm25_retriever = BM25Retriever.from_documents(
            all_parent_docs,
            tokenizer=chinese_tokenizer
        )
        bm25_retriever.k = 4  # BM25检索返回的文档数量（可调）
    else:
        bm25_retriever = None
    
    # ---------- 新增：分别打印两个检索器的原始召回 ----------
    # 包装父文档检索器
    # parent_retriever = PrintingRetrieverWrapper(parent_retriever, name="父文档检索器（向量）")
    # if bm25_retriever:
    #     # 包装 BM25 检索器
    #     bm25_retriever = PrintingRetrieverWrapper(bm25_retriever, name="BM25检索器")

    # 4. 融合检索器（向量 + BM25）
    if bm25_retriever:
        ensemble_retriever = EnsembleRetriever(
            retrievers=[parent_retriever, bm25_retriever],
            weights=[0.7, 0.3]  # 可调
        )
        base_retriever = ensemble_retriever
    else:
        base_retriever = parent_retriever

    # ---------- 新增：包装 base_retriever，打印原始召回结果 ----------
    # base_retriever = PrintingRetrieverWrapper(base_retriever, name="融合检索器")
    # -------------------------------------------------------------
    
    # 5. 添加重排序器（使用百炼的rerank模型）
    rerank_compressor = DashScopeRerank(
        api_key=DASHSCOPE_API_KEY,
        model="qwen3-rerank",
        top_n=3
    )
    final_retriever = ContextualCompressionRetriever(
        base_compressor=rerank_compressor,
        base_retriever=base_retriever
        # base_retriever=parent_retriever
    )
    
    # 6. 构建提示模板和链（与之前类似）
    qa_prompt = ChatPromptTemplate.from_template("""
    你是本田TBOX/TSU车载终端的技术专家，服务于本田Tier1供应商。
    请严格基于以下参考文档回答问题，只回答文档中存在的信息，不要编造内容。
    如果文档中没有相关信息，请明确说明「参考文档中未找到相关信息」。
    回答语言要和用户问题一致（用户问中文答中文，问日文答日文，问英文答英文）。

    参考文档：
    {context}

    用户问题：
    {input}
    """)
    
    document_chain = create_stuff_documents_chain(LLM, qa_prompt)
    retrieval_chain = create_retrieval_chain(final_retriever, document_chain)
    
    return retrieval_chain

def rag_qa_chain(question: str, qa_chain) -> str:
    """RAG问答函数（依赖注入：qa_chain由外部传入）"""
    try:
        result = qa_chain.invoke({"input": question})

        # ========== 调试输出：打印召回内容 ==========
        print("\n" + "="*50)
        print(f"【问题】{question}")
        print("【重排序后最终召回文档】")
        context = result.get("context", [])
        if not context:
            print("  未召回任何文档！")
        else:
            for i, doc in enumerate(context):
                print(f"文档 {i+1}:")
                print(f"  内容: {doc.page_content[:200]}...")  # 只打印前200字符
                print(f"  元数据: {doc.metadata}")
        print("="*50 + "\n")
        # =========================================
        return result["answer"]
    except Exception as e:
        print(f"RAG问答执行失败：{e}")
        return f"回答失败：{str(e)}"