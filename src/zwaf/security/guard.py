"""
Input Guard — sanitiza e detecta prompt injection.
Fork do guard da Sofia SDR, adaptado para o ZWAF (sem dependências de schemas Sofia).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class InputClassification(str, Enum):
    SAFE = "safe"
    SUSPICIOUS = "suspicious"
    BLOCKED = "blocked"


class SecurityIncidentType(str, Enum):
    PROMPT_INJECTION = "prompt_injection"
    JAILBREAK = "jailbreak"
    DATA_EXFILTRATION = "data_exfiltration"
    SYSTEM_PROMPT_LEAK = "system_prompt_leak"
    ROLE_CONFUSION = "role_confusion"
    SPAM = "spam"


class SecurityIncidentSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class SecurityIncident:
    session_id: Optional[str]
    lead_id: Optional[str]
    incident_type: SecurityIncidentType
    severity: SecurityIncidentSeverity
    payload: str
    action_taken: str


@dataclass
class GuardResult:
    classification: InputClassification
    sanitized_input: str
    incident: Optional[SecurityIncident] = None
    deflection_message: str = ""
    should_block: bool = False


INJECTION_PATTERNS = [
    (r"ignore\s+(?:\w+\s+){0,3}instru", SecurityIncidentType.PROMPT_INJECTION, SecurityIncidentSeverity.HIGH),
    (r"(ignore|forget|disregard)\s+(everything|all|the)\s+(above|before|prior)", SecurityIncidentType.PROMPT_INJECTION, SecurityIncidentSeverity.HIGH),
    (r"you\s+(are|were|now)\s+(a|an|the)?\s*(unrestricted|jailbroken|dan|evil|libre)", SecurityIncidentType.JAILBREAK, SecurityIncidentSeverity.CRITICAL),
    (r"pretend\s+(you|that\s+you)\s+(are|have\s+no|don't\s+have)", SecurityIncidentType.JAILBREAK, SecurityIncidentSeverity.HIGH),
    (r"(list|show|dump|print|reveal)\s+(all|every|the)\s+(leads|users|customers|contacts|data)", SecurityIncidentType.DATA_EXFILTRATION, SecurityIncidentSeverity.CRITICAL),
    (r"(repeat|show|print|output|reveal)\s+(your|the)\s+(system|initial|original)\s+(prompt|instructions|message)", SecurityIncidentType.SYSTEM_PROMPT_LEAK, SecurityIncidentSeverity.HIGH),
    (r"(repita|mostre|revele)\s+\S+\s+instru[çc]", SecurityIncidentType.SYSTEM_PROMPT_LEAK, SecurityIncidentSeverity.HIGH),
    (r"act\s+as\s+if\s+(you\s+have|there\s+are)\s+no\s+(restrictions|rules|limits)", SecurityIncidentType.ROLE_CONFUSION, SecurityIncidentSeverity.HIGH),
    (r"seu\s+(chefe|ceo|gerente|diretor)\s+(disse|pediu|autorizou|mandou)", SecurityIncidentType.PROMPT_INJECTION, SecurityIncidentSeverity.MEDIUM),
    (r"ignore\s+(as|suas)\s+instru[çc]", SecurityIncidentType.PROMPT_INJECTION, SecurityIncidentSeverity.HIGH),
    (r"voc[eê]\s+(agora\s+)?(é|foi)\s+(um[a]?\s+)?(assistente|ia|bot)\s+sem\s+restri[çc]", SecurityIncidentType.JAILBREAK, SecurityIncidentSeverity.HIGH),
    (r"(liste|mostre|imprima|revele)\s+(todos|todas)\s+(os|as)\s+(leads?|usu[áa]rios?|clientes?)", SecurityIncidentType.DATA_EXFILTRATION, SecurityIncidentSeverity.CRITICAL),
]

SPAM_INDICATORS = [
    r"(click|clique)\s+(here|aqui)",
    r"(free|gr[áa]tis|gratis)\s+(money|dinheiro|prize|pr[êe]mio)",
    r"(win|ganhe|you\s+won|voc[eê]\s+ganhou)",
    r"crypto|bitcoin|nft|forex",
]


class InputGuard:
    MAX_LENGTH = 2000
    SPAM_THRESHOLD = 3

    def check(
        self,
        text: str,
        session_id: Optional[str] = None,
        lead_id: Optional[str] = None,
    ) -> GuardResult:
        if len(text) > self.MAX_LENGTH:
            text = text[: self.MAX_LENGTH]

        sanitized = self._sanitize(text)
        injection_match = self._check_injection(sanitized)
        if injection_match:
            incident_type, severity = injection_match
            incident = SecurityIncident(
                session_id=session_id,
                lead_id=lead_id,
                incident_type=incident_type,
                severity=severity,
                payload=text[:500],
                action_taken="blocked",
            )
            if severity == SecurityIncidentSeverity.CRITICAL:
                deflection = "Desculpe, não posso processar essa mensagem. Se precisar de ajuda, pode reformular?"
            else:
                deflection = "Desculpe, não entendi muito bem. Pode reformular de outra forma?"
            return GuardResult(
                classification=InputClassification.BLOCKED,
                sanitized_input=sanitized,
                incident=incident,
                deflection_message=deflection,
                should_block=True,
            )

        spam_count = self._check_spam(sanitized)
        if spam_count >= self.SPAM_THRESHOLD:
            incident = SecurityIncident(
                session_id=session_id,
                lead_id=lead_id,
                incident_type=SecurityIncidentType.SPAM,
                severity=SecurityIncidentSeverity.MEDIUM,
                payload=text[:500],
                action_taken="flagged",
            )
            return GuardResult(
                classification=InputClassification.SUSPICIOUS,
                sanitized_input=sanitized,
                incident=incident,
                deflection_message="",
                should_block=False,
            )

        return GuardResult(
            classification=InputClassification.SAFE,
            sanitized_input=sanitized,
        )

    def _sanitize(self, text: str) -> str:
        text = unicodedata.normalize("NFC", text)
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
        text = re.sub(r"[​-‏‪-‮⁦-⁩﻿]", "", text)
        return text.strip()

    def _check_injection(self, text: str):
        lower = text.lower()
        for pattern, incident_type, severity in INJECTION_PATTERNS:
            if re.search(pattern, lower, re.IGNORECASE):
                return incident_type, severity
        return None

    def _check_spam(self, text: str) -> int:
        lower = text.lower()
        return sum(1 for p in SPAM_INDICATORS if re.search(p, lower, re.IGNORECASE))
