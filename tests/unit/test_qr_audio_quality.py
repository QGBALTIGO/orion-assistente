"""Provider quality must gate the entire transcript, including a spoken consent."""
import copy

import pytest

from app.qr_audio_quality import reliable_transcript


def payload():
    return {"text": "Sim, mas não confirme. Quero outro horário.", "duration": 5,
        "segments": [
            {"text": "Sim, mas não confirme.", "start": 0, "end": 2,
             "avg_logprob": -.2, "no_speech_prob": .02, "compression_ratio": 1.2},
            {"text": "Quero outro horário.", "start": 2, "end": 5,
             "avg_logprob": -.3, "no_speech_prob": .03, "compression_ratio": 1.3},
        ]}


def test_complete_clear_multi_segment_audio_is_accepted():
    assert reliable_transcript(payload(), max_seconds=120)


@pytest.mark.parametrize("field,value", [
    ("avg_logprob", -1.01), ("no_speech_prob", .8), ("compression_ratio", 2.41),
    ("avg_logprob", float("nan")), ("no_speech_prob", float("inf")),
    ("no_speech_prob", True), ("no_speech_prob", -1), ("compression_ratio", 0),
    ("avg_logprob", .5), ("start", -1), ("end", 999), ("end", "5"),
])
def test_unclear_or_invalid_segment_rejects_whole_message(field, value):
    sample = payload(); sample["segments"][1][field] = value
    assert not reliable_transcript(sample, max_seconds=120)


@pytest.mark.parametrize("change", ["missing_segments", "empty_segments", "dropped_last", "different_text", "reordered", "missing_metric", "non_dict"])
def test_no_partial_or_unverifiable_spoken_confirmation(change):
    sample = payload()
    if change == "missing_segments": sample.pop("segments")
    if change == "empty_segments": sample["segments"] = []
    if change == "dropped_last": sample["segments"] = sample["segments"][:1]
    if change == "different_text": sample["text"] = "Sim"
    if change == "reordered": sample["segments"].reverse()
    if change == "missing_metric": sample["segments"][0].pop("no_speech_prob")
    if change == "non_dict": sample["segments"][0] = "Sim"
    assert not reliable_transcript(sample, max_seconds=120)


def test_silence_misrecognized_as_yes_is_rejected():
    sample = {"text": "Sim", "duration": 5, "segments": [
        {"text": "Sim", "start": 0, "end": 5, "avg_logprob": -.2,
         "no_speech_prob": .99, "compression_ratio": 1.1}]}
    assert not reliable_transcript(sample, max_seconds=120)


def test_accented_clear_speech_has_no_wordlist_requirement():
    sample = payload(); sample["segments"][0]["avg_logprob"] = -.7
    assert reliable_transcript(sample, max_seconds=120)
