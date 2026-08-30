"""LLM 配置模型（V2.4 FR-I）：每用户双槽位（text / vision）+ 平台默认。

- user_id：所属用户；**0 = 平台默认**（admin 配置，未配置个人模型的用户兜底）
- slot：text（默认文本模型）/ vision（图像识别模型，可选）
- api_key：AES-256-GCM 加密落库（密钥由 JWT_SECRET + owner 派生），永不明文、不回显全文
"""
from app.core.utils import utcnow

from sqlalchemy import Column, String, Integer, Text, DateTime, UniqueConstraint

from app.core.db import Base


class LLMConfig(Base):
    __tablename__ = "llm_configs"
    __table_args__ = (
        UniqueConstraint("user_id", "slot", name="uq_llm_user_slot"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, default=0, index=True)  # 0 = 平台默认
    slot = Column(String(16), nullable=False)                        # text / vision
    provider = Column(String(32), nullable=False, default="custom")
    base_url = Column(String(256), nullable=False, default="")
    model = Column(String(128), nullable=False, default="")
    api_key_enc = Column(Text, nullable=False, default="")           # 加密后的 Key
    api_key_tail = Column(String(16), default="")                    # 脱敏回显用（尾 4 位）
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
