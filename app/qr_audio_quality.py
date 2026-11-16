"""Reject uncertain complete ASR output; never execute a surviving fragment.

These conservative thresholds are application policy, not a confidence or
accuracy guarantee. Groq verbose_json documents these segment quality fields:
https://console.groq.com/docs/speech-to-text#understanding-metadata-fields
"""
import math


def reliable_transcript(payload, *, max_seconds):
    segments = payload.get("segments")
    duration = payload.get("duration")
    if (not isinstance(segments, list) or not 1 <= len(segments) <= max_seconds*4+8
            or type(duration) not in (int, float) or not math.isfinite(duration)):
        return False
    previous, pieces = 0, []
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
            return False
        keys = ("start", "end", "avg_logprob", "no_speech_prob", "compression_ratio")
        if any(type(segment.get(key)) not in (int, float) or not math.isfinite(segment[key]) for key in keys):
            return False
        start, end = segment["start"], segment["end"]
        if not 0 <= start < end <= min(duration+1, max_seconds+1) or start < previous-.1:
            return False
        previous = end
        if (not -100 <= segment["avg_logprob"] <= .05 or not 0 <= segment["no_speech_prob"] <= 1
                or not 0 < segment["compression_ratio"] <= 100):
            return False
        if segment["text"].strip():
            if (segment["avg_logprob"] < -1 or segment["no_speech_prob"] >= .8
                    or segment["compression_ratio"] > 2.4):
                return False
            pieces.append(segment["text"])
    # A missing middle/last segment cannot silently become a shorter command.
    normalize = lambda value: " ".join(value.split())
    return bool(pieces) and normalize(" ".join(pieces)) == normalize(payload.get("text", ""))
