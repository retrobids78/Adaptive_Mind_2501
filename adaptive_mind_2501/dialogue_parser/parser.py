"""Dialogue parser: rule-based NL intent extraction with optional Ollama hook."""

from __future__ import annotations

from typing import Optional

from adaptive_mind_2501.models import Intent

_NAVIGATE_KEYWORDS = ('vai', 'raggiungi', 'spostati', 'dirigiti')
_PICK_KEYWORDS = ('prendi', 'raccogli', 'afferra')
_STOP_KEYWORDS = ('stop', 'fermati', 'halt', 'emergenza', 'abort', 'annulla')


class DialogueParser:
    """Interpret user natural language into a structured Intent."""

    def __init__(
        self,
        ollama_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        self.ollama_url = ollama_url
        self.model = model

    def parse(self, text: str) -> Intent:
        raw = '' if text is None else str(text)
        text_clean = raw.strip().lower()

        if not text_clean:
            return Intent(
                name='user_intent',
                action='unknown',
                parameters={'raw_text': ''},
                confidence=0.0,
            )

        # Prefer stop before navigate/pick so "stop e vai" still stops
        tokens = set(text_clean.replace(',', ' ').split())
        if tokens & set(_STOP_KEYWORDS) or text_clean in {'ferma', 'stop'}:
            action = 'emergency_stop'
        elif any(w in text_clean for w in _NAVIGATE_KEYWORDS):
            action = 'navigate'
        elif any(w in text_clean for w in _PICK_KEYWORDS):
            action = 'pick_up'
        else:
            action = 'unknown'

        return Intent(
            name='user_intent',
            action=action,
            parameters={'raw_text': raw.strip()},
        )
