"""Conversion intelligence primitives for ZWAF."""

from .intelligence import (
    BuyingIntent,
    ConversionAction,
    LeadSignal,
    Sentiment,
    analyze_message,
    decide_payment_link,
)

__all__ = [
    "BuyingIntent",
    "ConversionAction",
    "LeadSignal",
    "Sentiment",
    "analyze_message",
    "decide_payment_link",
]
