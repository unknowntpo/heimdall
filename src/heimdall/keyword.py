"""Keyword / regex baseline. Score is 1.0 on any match, else 0.0."""

from __future__ import annotations

import re

MODEL_VERSION = "heimdall-keyword-0.0.1"

_EN = [
    r"\b(ignore|disregard|forget|skip)\b.{0,30}\b(previous|prior|above|earlier|all|your|the)\b.{0,20}\b(instructions?|prompts?|rules|directions|context|tasks?)\b",
    r"\bforget (everything|all of that|what i said)\b",
    r"\bnew (task|instructions?)\b",
    r"\byou are now\b",
    r"\bpretend (to be|you are|that you)\b",
    r"\b(system|initial|original|hidden) prompt\b",
    r"\b(reveal|print|show|output|repeat) (your|the) (prompt|instructions|rules)\b",
    r"\bjailbreak\b",
    r"\bdo anything now\b|\bDAN\b",
    r"\bdeveloper mode\b",
    r"\bno (restrictions|rules|filters|limits)\b",
]

_ZH = [
    r"(忽略|無視|忽視|不要理會|忘記|忘掉).{0,8}(之前|先前|以上|上述|前面|原本|所有|你的).{0,6}(指示|指令|規則|提示|設定)",
    r"你現在是",
    r"假裝你是|扮演",
    r"(系統|system) ?(提示|prompt)",
    r"越獄",
    r"沒有任何?限制",
]

_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _EN + _ZH]


def score(text: str) -> float:
    return 1.0 if any(p.search(text) for p in _PATTERNS) else 0.0


def classify(text: str) -> dict:
    s = score(text)
    return {"label": "injection" if s >= 0.5 else "safe", "score": s, "model_version": MODEL_VERSION}
