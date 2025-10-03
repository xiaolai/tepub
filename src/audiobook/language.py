from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from langdetect import DetectorFactory, LangDetectException, detect

DetectorFactory.seed = 0


def detect_language(text_samples: Iterable[str]) -> str | None:
    cleaned = [txt.strip() for txt in text_samples if txt and txt.strip()]
    if not cleaned:
        return None
    votes = Counter()
    for sample in cleaned:
        try:
            lang = detect(sample)
            votes[lang] += 1
        except LangDetectException:
            continue
        if sum(votes.values()) >= 20:
            break
    if not votes:
        return None
    return votes.most_common(1)[0][0]
