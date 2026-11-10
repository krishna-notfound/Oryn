"""Backchannel classification and the contested-turn arbiter."""

from __future__ import annotations

import asyncio

import pytest

from app.voice.interruptions import (
    BackchannelClassifier,
    InterruptionArbiter,
    Verdict,
)

classifier = BackchannelClassifier()


@pytest.mark.parametrize(
    "text",
    [
        "haan",
        "Haan.",
        "hmm",
        "Hmmmm...",
        "uh-huh",
        "okay",
        "ok ok",
        "yeah right",
        "achha theek hai",
        "accha",
        "arre wah",
        "oh wow nice",
        "bilkul sahi",
        "हाँ",
        "हां।",
        "अच्छा",
        "ठीक",
        "जी हाँ",
        "हम्म",
        "वाह वाह",
        "haaan",
        "yessss",
        "haha",
        "hehehe",
        "",
        "   ",
        "...",
        "?!",
    ],
)
def test_backchannels_are_recognised(text: str) -> None:
    assert classifier.is_backchannel(text, "en-IN")


@pytest.mark.parametrize(
    "text",
    [
        "wait stop",
        "haan lekin ek minute",
        "no",
        "ok but what about my exam",
        "haan haan haan haan",  # four words is a sentence, not a nod
        "tell me something else",
        "रुको",
        "नहीं मुझे कुछ और पूछना है",
        "huh?",
    ],
)
def test_real_speech_is_not_a_backchannel(text: str) -> None:
    assert not classifier.is_backchannel(text, "hi-IN")


def test_regional_acknowledgements_count_for_their_language() -> None:
    assert classifier.is_backchannel("சரி", "ta-IN")
    assert classifier.is_backchannel("అవును", "te-IN")
    assert not classifier.is_backchannel("சரி", "en-IN")


async def test_a_backchannel_resumes_and_a_sentence_interrupts() -> None:
    arbiter = InterruptionArbiter(_never)
    arbiter.contest()
    assert arbiter.contested
    assert arbiter.judge("haan", "hi-IN") is Verdict.RESUME
    assert not arbiter.contested  # a verdict settles the contest

    arbiter.contest()
    assert arbiter.judge("actually can we talk about food", "en-IN") is Verdict.INTERRUPT
    assert not arbiter.contested


async def test_contest_is_idempotent() -> None:
    arbiter = InterruptionArbiter(_never)
    assert arbiter.contest() is True
    assert arbiter.contest() is False
    await arbiter.close()


async def test_contest_expires_without_a_transcript() -> None:
    expired = asyncio.Event()

    async def on_expired() -> None:
        expired.set()

    arbiter = InterruptionArbiter(on_expired, timeout_s=0.02)
    arbiter.contest()
    async with asyncio.timeout(1):
        await expired.wait()
    assert not arbiter.contested


async def test_touch_pushes_the_deadline_back() -> None:
    fired: list[bool] = []

    async def on_expired() -> None:
        fired.append(True)

    arbiter = InterruptionArbiter(on_expired, timeout_s=0.06)
    arbiter.contest()
    for _ in range(3):
        await asyncio.sleep(0.03)
        arbiter.touch()
    assert not fired
    await asyncio.sleep(0.1)
    assert fired == [True]


async def test_hold_keeps_the_contest_open_while_the_user_talks() -> None:
    fired: list[bool] = []

    async def on_expired() -> None:
        fired.append(True)

    arbiter = InterruptionArbiter(on_expired, timeout_s=0.02)
    arbiter.contest()
    arbiter.hold()
    await asyncio.sleep(0.08)
    assert arbiter.contested and not fired  # a long interruption is not timed out mid-sentence

    arbiter.touch()  # they stopped; the transcript has timeout_s to arrive
    await asyncio.sleep(0.08)
    assert fired == [True] and not arbiter.contested


async def test_a_verdict_cancels_the_timeout() -> None:
    fired: list[bool] = []

    async def on_expired() -> None:
        fired.append(True)

    arbiter = InterruptionArbiter(on_expired, timeout_s=0.02)
    arbiter.contest()
    arbiter.judge("haan", None)
    await asyncio.sleep(0.06)
    assert not fired


async def test_close_cancels_the_timer() -> None:
    fired: list[bool] = []

    async def on_expired() -> None:
        fired.append(True)

    arbiter = InterruptionArbiter(on_expired, timeout_s=0.02)
    arbiter.contest()
    await arbiter.close()
    await asyncio.sleep(0.05)
    assert not fired


async def test_a_failing_expiry_handler_does_not_kill_the_timer_task_silently(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def broken() -> None:
        raise RuntimeError("boom")

    arbiter = InterruptionArbiter(broken, timeout_s=0.01)
    arbiter.contest()
    await asyncio.sleep(0.05)
    assert "contest expiry failed" in caplog.text


async def _never() -> None:
    raise AssertionError("must not expire")
