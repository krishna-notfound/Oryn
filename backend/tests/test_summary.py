"""Rolling summary: folded in the background, never on the reply path."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

import pytest

from app.conversation.history import ConversationHistory
from app.conversation.summary import SUMMARY_WORDS, RollingSummary
from app.providers.base import ChatMessage, LanguageModel, LLMChunk, ToolSpec


class SummarisingLLM(LanguageModel):
    name = "summariser"
    model = "summariser"

    def __init__(self, reply: str = "They talked about cricket.", delay: float = 0.0) -> None:
        self.reply = reply
        self.delay = delay
        self.prompts: list[list[ChatMessage]] = []
        self.max_tokens: list[int | None] = []
        self.fail = False
        self.release = asyncio.Event()
        self.release.set()

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[LLMChunk]:
        self.prompts.append(list(messages))
        self.max_tokens.append(max_tokens)
        await self.release.wait()
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("llm down")
        yield LLMChunk(text=self.reply)


def pairs(n: int, start: int = 0) -> list[ChatMessage]:
    out: list[ChatMessage] = []
    for i in range(start, start + n):
        out += [ChatMessage("user", f"q{i}"), ChatMessage("assistant", f"a{i}")]
    return out


def test_history_hands_out_what_it_drops_once() -> None:
    history = ConversationHistory(max_turns=2)
    for i in range(4):
        history.add_user(f"u{i}")
        history.add_assistant(f"a{i}")
    assert [m.content for m in history.messages] == ["u2", "a2", "u3", "a3"]
    assert [m.content for m in history.take_overflow()] == ["u0", "a0", "u1", "a1"]
    assert history.take_overflow() == []


def test_history_never_leaves_an_orphaned_reply_at_the_front() -> None:
    history = ConversationHistory(max_turns=1)
    history.add_user("u0")
    history.add_assistant("a0")
    history.add_user("u1")  # evicts u0, which would leave a0 first
    assert [m.content for m in history.messages] == ["u1"]
    assert [m.content for m in history.take_overflow()] == ["u0", "a0"]


async def test_fold_returns_immediately_and_updates_in_the_background() -> None:
    llm = SummarisingLLM(delay=0.02)
    summary = RollingSummary(llm)
    summary.fold(pairs(2))
    assert summary.text == ""  # not on the caller's path
    await summary.settle()
    assert summary.text == "They talked about cricket."
    assert llm.max_tokens == [300]
    request = llm.prompts[0][1].content
    assert "q0" in request and "a1" in request and "(none yet)" in request


async def test_next_fold_builds_on_the_previous_summary() -> None:
    llm = SummarisingLLM()
    summary = RollingSummary(llm)
    summary.fold(pairs(1))
    await summary.settle()
    summary.fold(pairs(1, start=1))
    await summary.settle()
    assert "They talked about cricket." in llm.prompts[1][1].content


async def test_summary_is_capped_in_words() -> None:
    summary = RollingSummary(SummarisingLLM("word " * 400))
    summary.fold(pairs(1))
    await summary.settle()
    assert len(summary.text.split()) == SUMMARY_WORDS


async def test_failure_is_logged_and_the_messages_are_retried(
    caplog: pytest.LogCaptureFixture,
) -> None:
    llm = SummarisingLLM()
    llm.fail = True
    summary = RollingSummary(llm)
    summary.fold(pairs(1))
    await summary.settle()
    assert summary.text == "" and "summary update failed" in caplog.text

    llm.fail = False
    summary.fold(pairs(1, start=1))
    await summary.settle()
    request = llm.prompts[-1][1].content
    assert "q0" in request and "q1" in request  # the failed batch was not lost


async def test_slow_llm_is_timed_out() -> None:
    llm = SummarisingLLM()
    llm.release.clear()
    summary = RollingSummary(llm, timeout_s=0.02)
    summary.fold(pairs(1))
    await summary.settle()
    assert summary.text == ""


async def test_queue_is_bounded() -> None:
    llm = SummarisingLLM()
    llm.fail = True
    summary = RollingSummary(llm, max_pending=6)
    for i in range(5):
        summary.fold(pairs(1, start=i))
        await summary.settle()
    llm.fail = False
    summary.fold(())
    summary.fold(pairs(1, start=5))
    await summary.settle()
    request = llm.prompts[-1][1].content
    assert "q5" in request and "q0" not in request


async def test_folds_during_a_running_update_are_picked_up_by_the_same_worker() -> None:
    llm = SummarisingLLM(delay=0.02)
    summary = RollingSummary(llm)
    summary.fold(pairs(1))
    await asyncio.sleep(0.005)
    summary.fold(pairs(1, start=1))
    await summary.settle()
    assert len(llm.prompts) == 2  # one worker, two sequential calls
    assert "q1" in llm.prompts[1][1].content


async def test_close_cancels_a_running_update() -> None:
    llm = SummarisingLLM()
    llm.release.clear()
    summary = RollingSummary(llm)
    summary.fold(pairs(1))
    await asyncio.sleep(0)
    await summary.close()
    assert summary.text == ""
