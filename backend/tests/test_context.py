from app.conversation.context import ContextBuilder
from app.conversation.history import ConversationHistory
from app.conversation.ports import Memory, UserContext
from app.conversation.prompts import build_system_prompt

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


def test_cut_off_reply_is_stored_as_heard_text_only() -> None:
    history = ConversationHistory(max_turns=4)
    history.add_assistant("I think you should")
    history.add_assistant("")  # nothing was heard: no message at all
    assert [m.content for m in history.messages] == ["I think you should"]


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
    assert "never diagnose" in prompt and "markdown" in prompt


def test_prompt_is_compact_and_has_no_interruption_marker() -> None:
    prompt = build_system_prompt(USER)
    assert len(prompt) < 2_200  # keeps time-to-first-token low
    assert "interrupted by user" not in prompt


def test_auto_language_prompt_defers_to_the_per_turn_note() -> None:
    prompt = build_system_prompt(UserContext("u", language="auto"))
    assert "Default to" not in prompt and "note names" in prompt


def test_fixed_language_prompt_names_it() -> None:
    prompt = build_system_prompt(UserContext("u", language="ta-IN", extra_languages=("en-IN",)))
    assert "Default to Tamil" in prompt and "English" in prompt


def test_listen_first_toggles_the_advice_rule() -> None:
    assert "before offering advice" in build_system_prompt(UserContext("u", listen_first=True))
    assert "before offering advice" not in build_system_prompt(UserContext("u", listen_first=False))


def test_summary_language_and_cut_off_notes_precede_the_safety_note() -> None:
    messages = ContextBuilder(max_turns=12).build(
        USER,
        _history(0),
        [],
        "hi",
        "BE CAREFUL",
        summary="They talked about cricket.",
        language_note="Reply in Tamil (ta-IN).",
        cut_off="one two three",
    )
    system = messages[0].content
    assert "Conversation so far: They talked about cricket." in system
    order = [system.index(x) for x in ("Conversation so far", "Reply in Tamil", "cut off", "BE")]
    assert order == sorted(order)
    assert sum(m.role == "system" for m in messages) == 1


def test_cut_off_note_quotes_the_last_twelve_words_and_forbids_mentioning_it() -> None:
    heard = " ".join(f"w{i}" for i in range(30))
    messages = ContextBuilder(max_turns=12).build(USER, _history(0), [], "hi", cut_off=heard)
    system = messages[0].content
    assert '"...w18 w19' in system and 'w29".' in system and "w17" not in system
    assert "Do not mention being interrupted" in system


def test_no_notes_by_default() -> None:
    system = ContextBuilder(max_turns=12).build(USER, _history(0), [], "hi")[0].content
    assert "cut off" not in system and "Conversation so far" not in system
