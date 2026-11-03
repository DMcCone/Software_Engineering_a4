"""AI Integration package (CategorizationService).

Privacy rule: ONLY the visit-note text is ever passed in here (no name, ID, DOB, phone or
address). The AI has no authority: it only returns a suggestion object.

Classifiers (pluggable):
  * AnthropicClassifier       - real LLM via the Anthropic Messages API (needs ANTHROPIC_API_KEY)
  * KeywordFallbackClassifier - TEMPORARY keyword matcher for offline demo/tests. NOT an AI model.
"""
import json
import os
import urllib.request
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .models import Category, ValidationError

CONFIDENCE_THRESHOLD = 0.70   # AIR-01
TIMEOUT_SECONDS = 5           # NFR-03

MSG_SUGGESTED = "Suggested category (not a diagnosis)"
MSG_NO_SUGGESTION = "No AI suggestion: choose a category or leave Uncategorized"
MSG_UNAVAILABLE = "AI suggestion unavailable"


class SuggestionStatus(Enum):
    SUGGESTED = "suggested"
    NO_SUGGESTION = "no_suggestion"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class RawPrediction:
    label: str
    confidence: float


@dataclass(frozen=True)
class SuggestionResult:
    status: SuggestionStatus
    message: str
    category: Optional[Category] = None
    confidence: Optional[float] = None
    source: str = ""


class Classifier(ABC):
    name = "classifier"

    @abstractmethod
    def classify(self, note_text: str) -> RawPrediction:
        """Return the model's raw label and confidence. May raise on failure."""


class AnthropicClassifier(Classifier):
    URL = "https://api.anthropic.com/v1/messages"
    SYSTEM = ("You classify the text of a clinic visit note into exactly one category: "
              "Respiratory, Digestive, Musculoskeletal, or General/Other. This is a filing "
              "label, not a diagnosis. Reply with ONLY JSON: "
              '{"category": "<one of the four>", "confidence": <number 0 to 1>}')

    def __init__(self, api_key: str, model: Optional[str] = None):
        self._api_key = api_key
        self._model = model or os.environ.get("PRS_AI_MODEL", "claude-haiku-5-5")
        self.name = f"Anthropic API ({self._model})"

    def classify(self, note_text: str) -> RawPrediction:
        body = json.dumps({"model": self._model, "max_tokens": 100, "system": self.SYSTEM,
                           "messages": [{"role": "user", "content": note_text}]}).encode()
        request = urllib.request.Request(self.URL, data=body, headers={
            "x-api-key": self._api_key, "anthropic-version": "2023-06-01",
            "content-type": "application/json"})
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            reply = json.load(response)["content"][0]["text"]
        data = json.loads(reply[reply.index("{"): reply.rindex("}") + 1])
        return RawPrediction(str(data["category"]), float(data["confidence"]))


class KeywordFallbackClassifier(Classifier):
    """TEMPORARY FALLBACK / TEST SUBSTITUTE - a keyword matcher, NOT an AI model."""
    name = "TEMPORARY keyword fallback (not an AI model)"
    _KEYWORDS = {
        Category.RESPIRATORY: ("cough", "breath", "wheez", "asthma", "lung", "congestion", "sputum"),
        Category.DIGESTIVE: ("nausea", "vomit", "stomach", "diarrhea", "abdominal",
                             "constipation", "reflux"),
        Category.MUSCULOSKELETAL: ("back pain", "joint", "knee", "shoulder", "sprain",
                                   "fracture", "muscle"),
    }

    def classify(self, note_text: str) -> RawPrediction:
        text = note_text.lower()
        scores = {c: sum(k in text for k in words) for c, words in self._KEYWORDS.items()}
        best, hits = max(scores.items(), key=lambda item: item[1])
        if hits == 0:
            return RawPrediction(Category.GENERAL_OTHER.value, 0.50)
        return RawPrediction(best.value, 0.75 if hits == 1 else 0.90)


class UnavailableClassifier(Classifier):
    """Simulates an AI outage (PRS_AI=off); used to demonstrate NFR-03."""
    name = "AI disabled"

    def classify(self, note_text: str) -> RawPrediction:
        raise ConnectionError("AI service disabled")


def default_classifier() -> Classifier:
    mode = os.environ.get("PRS_AI", "").lower()
    if mode == "off":
        return UnavailableClassifier()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key and mode != "fallback":
        return AnthropicClassifier(api_key)
    return KeywordFallbackClassifier()


class CategorizationService:
    def __init__(self, classifier: Classifier, timeout: float = TIMEOUT_SECONDS,
                 threshold: float = CONFIDENCE_THRESHOLD):
        self._classifier = classifier
        self._timeout = timeout
        self._threshold = threshold

    @property
    def source_name(self) -> str:
        return self._classifier.name

    def suggest(self, note_text: str) -> SuggestionResult:
        """Never raises: any failure becomes UNAVAILABLE so saving a note is never blocked."""
        try:
            raw = self._call_classifier(note_text)
        except Exception:                      # includes timeouts and network errors
            return self._result(SuggestionStatus.UNAVAILABLE, MSG_UNAVAILABLE)
        try:
            category = Category.from_label(raw.label)
            confidence = float(raw.confidence)
        except (ValidationError, TypeError, ValueError):
            return self._result(SuggestionStatus.NO_SUGGESTION, MSG_NO_SUGGESTION)
        if not self._threshold <= confidence <= 1.0:
            return self._result(SuggestionStatus.NO_SUGGESTION, MSG_NO_SUGGESTION)
        return self._result(SuggestionStatus.SUGGESTED, MSG_SUGGESTED, category, confidence)

    def _call_classifier(self, note_text: str) -> RawPrediction:
        """Run the classifier in a worker thread with a hard overall timeout."""
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            return pool.submit(self._classifier.classify, note_text).result(timeout=self._timeout)
        finally:
            pool.shutdown(wait=False)

    def _result(self, status: SuggestionStatus, message: str,
                category: Optional[Category] = None,
                confidence: Optional[float] = None) -> SuggestionResult:
        return SuggestionResult(status, message, category, confidence, self.source_name)