"""Conversion intelligence primitives for ZWAF."""

from .intelligence import (
    BuyingIntent,
    ConversionAction,
    LeadSignal,
    Sentiment,
    analyze_message,
    decide_payment_link,
)
from .followup import (
    FollowupPlan,
    FollowupStage,
    LeadTemperature,
    build_followup_plan,
    classify_lead_temperature,
)

__all__ = [
    "BuyingIntent",
    "ConversionAction",
    "LeadSignal",
    "Sentiment",
    "FollowupPlan",
    "FollowupStage",
    "LeadTemperature",
    "analyze_message",
    "build_followup_plan",
    "classify_lead_temperature",
    "decide_payment_link",
]
