"""
批量知识库导入接口（Phase 1）

提供：
  POST /api/knowledge/bulk_upsert
  - 支持 dry_run 预览
  - 幂等 upsert：依据 (content, category) 唯一键判断新增或更新
  - 全部写入后只重建一次 FAISS 索引（性能关键）
"""
import logging
from typing import List, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.knowledge import KnowledgeItem
from services.knowledge_service import KnowledgeService
from services.text_embedding import embed_input

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/knowledge", tags=["知识库批量"])


class BulkUpsertRequest(BaseModel):
    """批量 upsert 请求"""
    items: List[KnowledgeItem]
    dry_run: bool = False


def _resolve_content(item: KnowledgeItem) -> str:
    """优先取 content，兼容 question/answer 拼接"""
    if item.content:
        return item.content
    if item.question or item.answer:
        return f"问题: {item.question or ''}\n答案: {item.answer or ''}"
    return ""


def _find_existing(db, content: str, category: str):
    """根据 (content, category) 在已启用文档中找匹配"""
    candidates = db.search_documents_by_content(content)
    return next(
        (d for d in candidates
         if d.get("category") == category and d.get("content") == content),
        None,
    )


@router.post("/bulk_upsert")
async def bulk_upsert(req: BulkUpsertRequest):
    """
    批量新增/更新知识条目（幂等）

    幂等策略：以 (content, category) 为唯一键
    - 命中已有 → update（重新生成 embedding）
    - 未命中 → insert
    - dry_run=True 时只返回预览，不写库、不重建索引
    """
    try:
        ks = KnowledgeService()
        if not ks.initialized:
            await ks.initialize()
        db = ks.db

        inserted_ids: List[int] = []
        updated_ids: List[int] = []
        preview_new: List[int] = []
        errors: List[Dict] = []

        for idx, item in enumerate(req.items):
            try:
                content = _resolve_content(item)
                if not content:
                    errors.append({"index": idx, "error": "content 或 question/answer 至少需要一个"})
                    continue

                keywords = item.keywords or []
                existing = _find_existing(db, content, item.category)

                if req.dry_run:
                    preview_new.append(-1) if not existing else updated_ids.append(existing["id"])
                    continue

                text_for_embedding = f"{content} {' '.join(keywords)}"
                embedding = embed_input(text_for_embedding)

                if existing:
                    db.update_document(
                        doc_id=existing["id"],
                        category=item.category,
                        keywords=keywords,
                        embedding=embedding,
                    )
                    updated_ids.append(existing["id"])
                else:
                    new_id = db.add_document(
                        content=content,
                        category=item.category,
                        keywords=keywords,
                        embedding=embedding,
                    )
                    inserted_ids.append(new_id)

            except Exception as inner_e:
                logger.exception("处理第 %s 条失败", idx)
                errors.append({"index": idx, "error": str(inner_e)})

        if not req.dry_run and (inserted_ids or updated_ids):
            await ks._build_vector_index()

        would_insert = len([i for i in preview_new if i == -1])
        return {
            "status": "success",
            "dry_run": req.dry_run,
            "would_insert": would_insert if req.dry_run else len(inserted_ids),
            "would_update": len(updated_ids),
            "inserted_ids": inserted_ids,
            "updated_ids": updated_ids,
            "errors": errors,
            "message": (
                f"预览：将新增 {would_insert} 条，更新 {len(updated_ids)} 条，{len(errors)} 个错误"
                if req.dry_run else
                f"完成：新增 {len(inserted_ids)} 条，更新 {len(updated_ids)} 条，{len(errors)} 个错误"
            ),
        }

    except Exception as e:
        logger.exception("批量 upsert 失败")
        raise HTTPException(status_code=500, detail=f"批量导入失败: {str(e)}")
