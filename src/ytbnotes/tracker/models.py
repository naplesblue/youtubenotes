"""
Opinion 数据模型与辅助工具

核心概念：
- Opinion: 博主对某只股票在某个价位的一条可验证观点
- opinion_id: 全局唯一标识 = {video_id}_{ticker}_{type}_{price}
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional


# 合法枚举值
PREDICTION_TYPES = {
    "target_price", "entry_zone", "support", "resistance",
    "direction_call", "reference_only", "stop_loss",
}

DIRECTIONS = {"long", "short", "hold"}

CONFIDENCES = {"high", "medium", "low"}

CONVICTIONS = {"high", "medium", "low"}

HORIZONS = {"short_term", "medium_term", "long_term"}

VERIFICATION_RESULTS = {"win", "loss", "pending", "expired"}

# 不参与胜率统计的观点类型
NON_VERIFIABLE_TYPES = {"reference_only", "stop_loss"}


def make_opinion_id(video_id: str, ticker: str, pred_type: str, price: float | None) -> str:
    """生成全局唯一的 opinion_id。"""
    raw = f"{video_id}_{ticker}_{pred_type}_{price}"
    short_hash = hashlib.md5(raw.encode()).hexdigest()[:6]
    price_str = f"{price:.0f}" if price else "na"
    return f"{video_id}_{ticker}_{pred_type}_{price_str}_{short_hash}"


@dataclass
class VerificationSnapshot:
    price: Optional[float] = None
    return_pct: Optional[float] = None
    result: Optional[str] = None  # win / loss / pending / expired
    regime: Optional[str] = None  # bull / bear / neutral

    def to_dict(self) -> dict:
        return {
            "price": self.price,
            "return_pct": self.return_pct,
            "result": self.result,
            "regime": self.regime,
        }

    @classmethod
    def from_dict(cls, d: dict) -> VerificationSnapshot:
        return cls(
            price=d.get("price"),
            return_pct=d.get("return_pct"),
            result=d.get("result"),
            regime=d.get("regime"),
        )


@dataclass
class Verification:
    status: str = "pending"
    snapshots: dict[str, VerificationSnapshot] = field(default_factory=lambda: {
        "30d": VerificationSnapshot(),
        "90d": VerificationSnapshot(),
        "180d": VerificationSnapshot(),
    })
    last_verified: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "snapshots": {k: v.to_dict() for k, v in self.snapshots.items()},
            "last_verified": self.last_verified,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Verification:
        snapshots = {}
        for k, v in (d.get("snapshots") or {}).items():
            snapshots[k] = VerificationSnapshot.from_dict(v) if isinstance(v, dict) else VerificationSnapshot()
        return cls(
            status=d.get("status", "pending"),
            snapshots=snapshots or {
                "30d": VerificationSnapshot(),
                "90d": VerificationSnapshot(),
                "180d": VerificationSnapshot(),
            },
            last_verified=d.get("last_verified"),
        )


@dataclass
class Prediction:
    type: str = "direction_call"
    direction: str = "long"
    price: Optional[float] = None
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    confidence: str = "medium"
    conviction: str = "medium"
    horizon: str = "medium_term"
    context: str = ""

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "direction": self.direction,
            "price": self.price,
            "target_price": self.target_price,
            "stop_loss": self.stop_loss,
            "confidence": self.confidence,
            "conviction": self.conviction,
            "horizon": self.horizon,
            "context": self.context,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Prediction:
        return cls(
            type=d.get("type", "direction_call"),
            direction=d.get("direction", "long"),
            price=d.get("price"),
            target_price=d.get("target_price"),
            stop_loss=d.get("stop_loss"),
            confidence=d.get("confidence", "medium"),
            conviction=d.get("conviction", "medium"),
            horizon=d.get("horizon", "medium_term"),
            context=d.get("context", ""),
        )

    @property
    def is_verifiable(self) -> bool:
        return self.type not in NON_VERIFIABLE_TYPES


@dataclass
class Opinion:
    opinion_id: str
    video_id: str
    channel: str
    analyst: str
    published_date: str

    ticker: str
    company_name: str
    sentiment: str

    prediction: Prediction
    price_at_publish: Optional[float] = None
    extraction_source: str = "cerebras_refinement"
    verification: Verification = field(default_factory=Verification)

    def to_dict(self) -> dict:
        return {
            "opinion_id": self.opinion_id,
            "video_id": self.video_id,
            "channel": self.channel,
            "analyst": self.analyst,
            "published_date": self.published_date,
            "ticker": self.ticker,
            "company_name": self.company_name,
            "sentiment": self.sentiment,
            "prediction": self.prediction.to_dict(),
            "price_at_publish": self.price_at_publish,
            "extraction_source": self.extraction_source,
            "verification": self.verification.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> Opinion:
        return cls(
            opinion_id=d["opinion_id"],
            video_id=d["video_id"],
            channel=d["channel"],
            analyst=d["analyst"],
            published_date=d["published_date"],
            ticker=d["ticker"],
            company_name=d.get("company_name", ""),
            sentiment=d.get("sentiment", "neutral"),
            prediction=Prediction.from_dict(d.get("prediction", {})),
            price_at_publish=d.get("price_at_publish"),
            extraction_source=d.get("extraction_source", "unknown"),
            verification=Verification.from_dict(d.get("verification", {})),
        )
