import requests
import json
from typing import List, Optional, Any, Dict
from langchain_classic.retrievers.document_compressors.base import BaseDocumentCompressor
from langchain_core.documents import Document
from pydantic import Field, PrivateAttr

class DashScopeRerank(BaseDocumentCompressor):
    """使用阿里百炼的 qwen3-rerank 模型对文档进行重排序（符合官方API格式）"""

    api_key: str = Field(..., description="阿里百炼 API Key")
    model: str = Field(default="qwen3-rerank", description="重排序模型名称")
    top_n: int = Field(default=3, description="返回的最相关文档数量")
    return_documents: bool = Field(default=True, description="是否在响应中返回文档内容")

    # 正确的 API 端点（从 curl 命令中确认）
    _url: str = PrivateAttr(default="https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank")

    def compress_documents(
        self, documents: List[Document], query: str, callbacks: Optional[Any] = None
    ) -> List[Document]:
        """根据官方API格式对文档进行重排序"""
        if not documents:
            return []

        # 按照官方示例构造请求体
        payload: Dict[str, Any] = {
            "model": self.model,
            "input": {
                "query": query,
                "documents": [doc.page_content for doc in documents]
            },
            "parameters": {
                "return_documents": self.return_documents,
                "top_n": self.top_n
            }
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(
                self._url,
                headers=headers,
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            result = response.json()

            # 解析官方API响应（根据示例推测结构）
            # 预期结构：{"output": {"results": [{"index": 0, "relevance_score": 0.95, ...}]}}
            if "output" in result and "results" in result["output"]:
                # 按返回的索引顺序重新排列文档
                ranked_indices = [item["index"] for item in result["output"]["results"]]
                # 返回前 top_n 个文档（API已经按分数排序，直接取索引即可）
                reordered_docs = [documents[i] for i in ranked_indices]
                return reordered_docs[:self.top_n]
            else:
                # 如果响应格式不符，打印响应内容以便调试，并返回原顺序的前 top_n 个
                print(f"Rerank 响应格式异常: {result}")
                return documents[:self.top_n]

        except requests.exceptions.HTTPError as e:
            print(f"Rerank HTTP 错误: {e}")
            print(f"响应内容: {e.response.text if e.response else '无'}")
            # 出错时返回原文档的前 top_n 个作为降级方案
            return documents[:self.top_n]
        except Exception as e:
            print(f"Rerank 调用失败: {e}")
            return documents[:self.top_n]

    # 可选：添加一个简单的测试方法
    def test_connection(self):
        """测试API连接是否正常"""
        test_docs = [Document(page_content="这是一个测试文档")]
        try:
            result = self.compress_documents(test_docs, "测试")
            return True
        except:
            return False
        
if __name__ == "__main__":
    api_key = "sk-10579025107e412983a48273c2ff7d3f"
    reranker = DashScopeRerank(api_key=api_key, top_n=2)

    docs = [
        Document(page_content="文本排序模型广泛用于搜索引擎和推荐系统中"),
        Document(page_content="量子计算是计算科学的一个前沿领域"),
        Document(page_content="预训练语言模型的发展给文本排序模型带来了新的进展")
    ]
    query = "什么是文本排序模型"

    result = reranker.compress_documents(docs, query)
    print("重排序后的文档：")
    for i, doc in enumerate(result):
        print(f"{i+1}. {doc.page_content}")