import json
from collections.abc import AsyncIterator, Iterator, Sequence

import pytest

from app.conversation.ports import UserContext
from app.memory.extractor import MemoryExtractor, parse_candidates
from app.memory.store import SQLiteMemoryStore
from app.providers.base import ChatMessage, LanguageModel, LLMChunk, ToolSpec
from app.providers.fakes import FakeEmbedder

ENABLED = UserContext(user_id="alice", memory_enabled=True)
DISABLED = UserContext(user_id="alice", memory_enabled=False)


class ScriptedLLM(LanguageModel):
    name = "scripted"
    model = "scripted"

    def __init__(self, reply: str = "[]", error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls = 0

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[LLMChunk]:
        self.calls += 1
        if self.error:
            raise self.error
        half = len(self.reply) // 2
        yield LLMChunk(text=self.reply[:half])
        yield LLMChunk(text=self.reply[half:])
        yield LLMChunk(finish_reason="stop")


def candidates(*items: tuple[str, str, float]) -> str:
    return json.dumps([{"content": c, "category": k, "confidence": p} for c, k, p in items])


def turn(user_text: str, reply: str = "Sure!") -> list[ChatMessage]:
    return [ChatMessage("user", user_text), ChatMessage("assistant", reply)]


@pytest.fixture
def store() -> Iterator[SQLiteMemoryStore]:
    s = SQLiteMemoryStore(":memory:", FakeEmbedder())
    yield s
    s.close()


async def stored(store: SQLiteMemoryStore) -> list[str]:
    return [m.content for m in await store.list("alice")]


async def test_disabled_memory_is_a_noop_and_never_calls_the_llm(store: SQLiteMemoryStore) -> None:
    llm = ScriptedLLM(candidates(("Likes tea", "preference", 0.9)))
    await MemoryExtractor(llm, store)(DISABLED, turn("I like tea"))
    assert llm.calls == 0 and await stored(store) == []


async def test_saves_candidates_parsed_from_fenced_json(store: SQLiteMemoryStore) -> None:
    reply = "```json\n" + candidates(("Is learning guitar", "interest", 0.92)) + "\n```"
    await MemoryExtractor(ScriptedLLM(reply), store)(ENABLED, turn("I'm learning guitar"))
    (memory,) = await store.list("alice")
    assert (memory.content, memory.category, memory.confidence) == (
        "Is learning guitar",
        "interest",
        0.92,
    )


async def test_drops_low_confidence_candidates(store: SQLiteMemoryStore) -> None:
    reply = candidates(("Maybe likes jazz", "preference", 0.69), ("Likes rock", "preference", 0.7))
    await MemoryExtractor(ScriptedLLM(reply), store)(ENABLED, turn("hmm music"))
    assert await stored(store) == ["Likes rock"]


@pytest.mark.parametrize("category", ["health", "religion", "sexuality", "finances", "credentials"])
async def test_drops_sensitive_categories_unless_user_asked_to_remember(
    store: SQLiteMemoryStore, category: str
) -> None:
    extractor = MemoryExtractor(ScriptedLLM(candidates(("Sensitive fact", category, 0.95))), store)
    await extractor(ENABLED, turn("this is just something I mentioned"))
    assert await stored(store) == []

    await extractor(ENABLED, turn("Please remember this about me"))
    assert await stored(store) == ["Sensitive fact"]


async def test_hinglish_yaad_rakhna_counts_as_explicit_request(store: SQLiteMemoryStore) -> None:
    llm = ScriptedLLM(candidates(("Takes thyroid medicine", "health", 0.9)))
    await MemoryExtractor(llm, store)(
        ENABLED, turn("ye yaad rakhna, main thyroid ki dawai leta hoon")
    )
    assert await stored(store) == ["Takes thyroid medicine"]


async def test_ignores_unknown_categories(store: SQLiteMemoryStore) -> None:
    llm = ScriptedLLM(candidates(("Something", "gossip", 0.99)))
    await MemoryExtractor(llm, store)(ENABLED, turn("hello"))
    assert await stored(store) == []


async def test_skips_near_duplicates_of_existing_memories(store: SQLiteMemoryStore) -> None:
    await store.add("alice", "Is learning guitar", "interest")
    reply = candidates(
        ("Is learning the guitar", "interest", 0.9), ("Works at a bank", "personal_context", 0.9)
    )
    await MemoryExtractor(ScriptedLLM(reply), store, FakeEmbedder())(ENABLED, turn("guitar, bank"))
    assert await stored(store) == ["Works at a bank", "Is learning guitar"]


async def test_skips_duplicates_within_one_response(store: SQLiteMemoryStore) -> None:
    reply = candidates(
        ("Likes spicy food", "preference", 0.9), ("Likes spicy food", "preference", 0.9)
    )
    await MemoryExtractor(ScriptedLLM(reply), store)(ENABLED, turn("I like spicy food"))
    assert await stored(store) == ["Likes spicy food"]


async def test_swallows_llm_errors(
    store: SQLiteMemoryStore, caplog: pytest.LogCaptureFixture
) -> None:
    extractor = MemoryExtractor(ScriptedLLM(error=RuntimeError("llm down")), store)
    await extractor(ENABLED, turn("I like tea"))
    assert await stored(store) == []
    assert "memory extraction failed" in caplog.text
    assert "I like tea" not in caplog.text


@pytest.mark.parametrize("garbage", ["", "no json here", "[1, 2", '{"content": "x"}', "[null, 5]"])
async def test_garbage_llm_output_saves_nothing(store: SQLiteMemoryStore, garbage: str) -> None:
    await MemoryExtractor(ScriptedLLM(garbage), store)(ENABLED, turn("hi"))
    assert await stored(store) == []


async def test_transcript_without_user_message_is_ignored(store: SQLiteMemoryStore) -> None:
    llm = ScriptedLLM(candidates(("x", "preference", 0.9)))
    await MemoryExtractor(llm, store)(ENABLED, [ChatMessage("assistant", "hello")])
    assert llm.calls == 0


def test_parse_candidates_tolerates_prose_and_skips_malformed_items() -> None:
    items = '[{"content": "a", "category": "Goal", "confidence": 0.8}, 7, {"content": 3}]'
    raw = f"Here you go:\n```\n{items}\n```"
    assert [(c.content, c.category) for c in parse_candidates(raw)] == [("a", "goal")]
