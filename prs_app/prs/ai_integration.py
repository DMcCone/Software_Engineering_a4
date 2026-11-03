"""AI Integration package (CategorizationService).

Privacy rule: ONLY the visit-note text is ever passed in here. No name, ID, DOB,
phone or address. The AI has no authority: it returns a suggestion object only.

Classifiers (pluggable):
  * AnthropicClassifier      - real LLM via the Anthropic Messages API (needs ANTHROPIC_API_KEY)
  * KeywordFallbackClassifier- TEMPORARY keyword matcher used for offline demo/tests.
                               It is NOT an AI model.
"""
import json
import os
import urllib.request
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .models import Category

CONFIDENCE_THRESHOLD = 0.70   # AIR-01
TIMEOUT_SECONDS = 5           # NFR-03

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
    def classify(self, note_text: str) -> RawPrediction: ...


class AnthropicClassifier(Classifier):
    URL = "https://api.anthropic.com/v1/messages"
    SYSTEM = ("You classify the text of a clinic visit note into exactly one category: "
              "Respiratory, Digestive, Musculoskeletal, or General/Other. This is a filing "
              "label, not a diagnosis. Reply with ONLY JSON: "
              '{"category": "<one of the four>", "confidence": <number 0 to 1>}')

    def __init__(self, api_key: str, model: Optional[str] = None):
        self.api_key = api_key
        self.model = model or os.environ.get("PRS_AI_MODEL", "claude-haiku-5-5")
        self.name = f"Anthropic API ({self.model})"

    def classify(self, note_text: str) -> RawPrediction:
        body = json.dumps({"model": self.model, "max_tokens": 100, "system": self.SYSTEM,
                           "messages": [{"role": "user", "content": note_text}]}).encode()
        req = urllib.request.Request(self.URL, data=body, headers={
            "x-api-key": self.api_key, "anthropic-version": "2023-06-01",
            "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            text = json.load(resp)["content"][0]["text"]
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
        return RawPrediction(str(data["category"]), float(data["confidence"]))


class KeywordFallbackClassifier(Classifier):
    """TEMPORARY FALLBACK / TEST SUBSTITUTE - a keyword matcher, NOT an AI model."""
    name = "TEMPORARY keyword fallback (not an AI model)"
    KEYWORDS = {
        Category.RESPIRATORY: ("cough", "breath", "wheez", "asthma", "lung", "congestion", "sputum"),
        Category.DIGESTIVE: ("nausea", "vomit", "stomach", "diarrhea", "abdominal", "constipation", "reflux"),
        Category.MUSCULOSKELETAL: ("back pain", "joint", "knee", "shoulder", "sprain", "fracture", "muscle"),
    }

    def classify(self, note_text: str) -> RawPrediction:
        t = note_text.lower()
        scores = {c: sum(k in t for k in kws) for c, kws in self.KEYWORDS.items()}
        best, hits = max(scores.items(), key=lambda kv: kv[1])
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
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key and mode != "fallback":
        return AnthropicClassifier(key)
    return KeywordFallbackClassifier()


class CategorizationService:
    def __init__(self, classifier: Classifier, timeout: float = TIMEOUT_SECONDS,
                 threshold: float = CONFIDENCE_THRESHOLD):
        self.classifier, self.timeout, self.threshold = classifier, timeout, threshold

    def suggest(self, note_text: str) -> SuggestionResult:
        """Never raises: any failure becomes UNAVAILABLE so note saving is never blocked."""
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            raw = pool.submit(self.classifier.classify, note_text).result(timeout=self.timeout)
        except FutureTimeout:
            return SuggestionResult(SuggestionStatus.UNAVAILABLE, MSG_UNAVAILABLE,
                                    source=self.classifier.name)
        except Exception:
            return SuggestionResult(SuggestionStatus.UNAVAILABLE, MSG_UNAVAILABLE,
                                    source=self.classifier.name)
        finally:
            pool.shutdown(wait=False)
        try:
            cat = Category.from_label(raw.label)
            conf = float(raw.confidence)
        except (ValueError, TypeError):
            return SuggestionResult(SuggestionStatus.NO_SUGGESTION, MSG_NO_SUGGESTION,
                                    source=self.classifier.name)
        if not 0.0 <= conf <= 1.0 or conf < self.threshold:
            return SuggestionResult(SuggestionStatus.NO_SUGGESTION, MSG_NO_SUGGESTION,
                                    source=self.classifier.name)
        return SuggestionResult(SuggestionStatus.SUGGESTED,
                                "Suggested category (not a diagnosis)", cat, conf,
                                self.classifier.name)