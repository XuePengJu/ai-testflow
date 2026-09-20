"""入库管道编排（V4.0）。

流程：抽取文本（调用方已完成）→ 分块 → 写 chunks 表 → 向量化写 Chroma →
更新 knowledge 状态。任一步失败抛异常，由调用方置 parse_status='failed'
并记录 error_message（界面可见，可一键重建索引重试）。

状态机：parsing → chunking → processing → ready | failed
"""
from __future__ import annotations

import json
import logging
from sqlalchemy.orm import Session

from app.models.knowledge import Chunk, Knowledge, KnowledgeBase
from app.services.knowledge import chunker, vectorstore

logger = logging.getLogger("knowledge.ingest")


class IngestError(RuntimeError):
    """入库失败（内容已部分写入时调用方负责清理）。"""


def _update_status(db: Session, knowledge: Knowledge, status: str,
                   error: str = "") -> None:
    knowledge.parse_status = status
    if error:
        knowledge.error_message = error[:2000]
    else:
        knowledge.error_message = ""
    db.commit()


def ingest_document(
    db: Session,
    kb: KnowledgeBase,
    knowledge: Knowledge,
    text: str,
) -> int:
    """执行入库：分块 + 写表 + 向量化。返回分块数。

    成功后 knowledge.parse_status='ready'；任何失败抛 IngestError（内部状态
    已回滚为 failed，chunks 行与向量由调用方清理）。
    """
    _update_status(db, knowledge, "parsing")
    try:
        # 0) 注入 embedding 配置（库创建者个人配置 > 平台 > 环境变量 > mock；与检索同源）
        from app.services import llm_service
        vectorstore.configure_embedding(llm_service.resolve_embedding(db, kb.user_id).get("cfg"))

        # 1) 结构化分块
        _update_status(db, knowledge, "chunking")
        raw_chunks = chunker.chunk_text(text)
        if not raw_chunks:
            raise IngestError("文档未能抽取到有效内容，请检查文件")

        # 2) 写 chunks 表（先落库再向量化：失败时可定位）
        chunk_rows: list[Chunk] = []
        for i, rc in enumerate(raw_chunks):
            chunk_rows.append(Chunk(
                user_id=kb.user_id,
                knowledge_base_id=kb.id,
                knowledge_id=knowledge.id,
                chunk_index=i,
                content=rc["content"],
                source_content=rc["content"],
                content_revision=0,
                context_header=rc.get("context_header", ""),
                chunk_type=rc.get("chunk_type", "text"),
                start_at=rc.get("start_at", 0),
                end_at=rc.get("end_at", 0),
            ))
        db.add_all(chunk_rows)
        db.commit()
        for c in chunk_rows:
            db.refresh(c)

        # 3) 向量化 + 写 Chroma
        _update_status(db, knowledge, "processing")
        indexed = [{
            "id": c.id,
            "content": c.content,
            "chunk_index": c.chunk_index,
        } for c in chunk_rows]
        vectorstore.index_chunks(
            kb_id=kb.id,
            knowledge_id=knowledge.id,
            user_id=kb.user_id,
            visibility=kb.visibility,
            chunks=indexed,
            file_name=knowledge.file_name or knowledge.title or "文档",
        )

        # 4) 收尾：状态 ready
        knowledge.chunk_count = len(chunk_rows)
        _update_status(db, knowledge, "ready")
        logger.info("文档入库完成 %s: %d chunks", knowledge.id, len(chunk_rows))
        return len(chunk_rows)
    except IngestError as e:
        _update_status(db, knowledge, "failed", str(e))
        raise
    except Exception as e:  # noqa: BLE001
        db.rollback()
        _update_status(db, knowledge, "failed", f"{type(e).__name__}: {e}")
        raise IngestError(f"入库失败：{type(e).__name__}: {e}") from e


def delete_document_vectors(db: Session, knowledge_id: str) -> None:
    """删除文档的向量与分块（重建索引前/删除文档时）。"""
    vectorstore.delete_knowledge_vectors(knowledge_id)
    from sqlalchemy import delete
    db.execute(delete(Chunk).where(Chunk.knowledge_id == knowledge_id))
    db.commit()


def visible_kb_ids(db: Session, user_id: int, admin: bool = False) -> list[str]:
    """当前用户可见的知识库 id 集合（权限过滤唯一入口）。

    - admin：全部可见
    - 普通用户：visibility='global' 的共享库 + 自己创建的 private 库
    检索/列表共用，保证权限口径一致。
    """
    from sqlalchemy import select
    if admin:
        stmt = select(KnowledgeBase.id).where(KnowledgeBase.deleted_at.is_(None))
    else:
        stmt = select(KnowledgeBase.id).where(
            KnowledgeBase.deleted_at.is_(None),
            (KnowledgeBase.visibility == "global") | (KnowledgeBase.user_id == user_id),
        )
    return [r[0] for r in db.execute(stmt).all()]


def doc_summary(knowledge: Knowledge) -> dict:
    """文档概要（API 返回用）。"""
    meta = {}
    try:
        meta = json.loads(knowledge.doc_meta or "{}")
    except (json.JSONDecodeError, TypeError):
        pass
    custom_meta = {}
    try:
        custom_meta = json.loads(knowledge.custom_meta or "{}")
    except (json.JSONDecodeError, TypeError):
        pass
    return {
        "id": knowledge.id,
        "knowledge_base_id": knowledge.knowledge_base_id,
        "title": knowledge.title,
        "file_name": knowledge.file_name,
        "file_type": knowledge.file_type,
        "file_size": knowledge.file_size,
        "parse_status": knowledge.parse_status,
        "error_message": knowledge.error_message,
        "chunk_count": knowledge.chunk_count,
        "created_at": knowledge.created_at.isoformat() if knowledge.created_at else None,
        "processed_at": knowledge.processed_at.isoformat() if knowledge.processed_at else None,
        "metadata": meta,
        "custom_meta": custom_meta,
        "wiki_summary": knowledge.wiki_summary or "",
        "wiki_category": knowledge.wiki_category or "",
    }
