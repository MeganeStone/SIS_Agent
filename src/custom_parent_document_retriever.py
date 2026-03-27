from langchain_classic.retrievers import MultiVectorRetriever  # 修正父类
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from typing import List, Optional
from langchain_core.documents import Document
from pydantic import Field
import pickle

# ========== 修正：自定义父文档检索器（对齐MultiVectorRetriever + Pydantic字段） ==========
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
        print(f"\n【父文档检索器（向量）】")
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