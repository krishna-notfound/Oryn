from app.conversation.context import ContextBuilder
from app.conversation.history import ConversationHistory
from app.conversation.ports import Memory, UserContext
from app.conversation.prompts import INTERRUPTED_MARKER, build_system_prompt

USER = UserContext(user_id="u1", preferred_name="Asha", interests=("cricket",))


def _history(pairs: int) -> ConversationHistory:
    history = ConversationHistory(max_turns=12)
    for i in range(pairs):
        history.add_user(f"question {i}")
        history.add_assistant(f"answer {i}")
    return history


def test_history_is_bounded_by_turns() -> None:
    history = ConversationHistory(max_turns=2)
    for i in range(5):
        history.add_user(f"u{i}")
        history.add_assistant(f"a{i}")
    assert [m.content for m in history.messages] == ["u3", "a3", "u4", "a4"]


def test_interrupted_reply_is_stored_truncated_with_marker() -> None:
    history = ConversationHistory(max_turns=4)
    history.add_assistant("I think you should", interrupted=True)
    history.add_assistant("", interrupted=True)
    assert history.messages[0].content == f"I think you should {INTERRUPTED_MARKER}"
    assert history.messages[1].content == INTERRUPTED_MARKER


def test_complete_reply_has_no_marker() -> None:
    history = ConversationHistory(max_turns=4)
    history.add_assistant("All good.")
    assert history.messages[0].content == "All good."


def test_build_orders_system_history_then_current_user_text() -> None:
    messages = ContextBuilder(max_turns=12).build(USER, _history(2), [], "what next?")
    assert [m.role for m in messages] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert messages[-1].content == "what next?"


def test_build_limits_turns() -> None:
    messages = ContextBuilder(max_turns=2).build(USER, _history(5), [], "now")
    assert [m.content for m in messages[1:]] == [
        "question 3",
        "answer 3",
        "question 4",
        "answer 4",
        "now",
    ]


def test_build_limits_total_chars_dropping_oldest_and_never_starting_on_assistant() -> None:
    history = ConversationHistory(max_turns=12)
    history.add_user("a" * 100)
    history.add_assistant("b" * 100)
    history.add_user("c" * 100)
    history.add_assistant("d" * 100)
    system_len = len(build_system_prompt(USER))
    builder = ContextBuilder(max_turns=12, max_chars=system_len + 5 + 150)

    messages = builder.build(USER, history, [], "hello")

    assert [m.role for m in messages] == ["system", "user"]
    assert messages[-1].content == "hello"


def test_memories_and_safety_note_go_into_the_system_message() -> None:
    memories = [Memory(id="m1", content="Has a dog named Bruno", category="fact")]
    messages = ContextBuilder(max_turns=12).build(USER, _history(0), memories, "hi", "BE CAREFUL")
    system = messages[0].content
    assert "Has a dog named Bruno" in system and system.endswith("BE CAREFUL")
    assert sum(m.role == "system" for m in messages) == 1


def test_prompt_uses_profile_lightly_and_states_the_rules() -> None:
    prompt = build_system_prompt(USER)
    assert "Asha" in prompt and "cricket" in prompt
    assert "Never claim to be human" in prompt and "Hinglish" in prompt
    assert "diagnose" in prompt and "no markdown" in prompt.lower()


def test_listen_first_toggles_the_advice_rule() -> None:
    assert "before offering advice" in build_system_prompt(UserContext("u", listen_first=True))
    assert "before offering advice" not in build_system_prompt(UserContext("u", listen_first=False))
