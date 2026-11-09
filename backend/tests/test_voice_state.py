import pytest

from app.voice.state import InvalidTransition, VoiceState, VoiceStateMachine

S = VoiceState


async def _machine_in(state: VoiceState) -> VoiceStateMachine:
    machine = VoiceStateMachine()
    paths = {
        S.IDLE: [],
        S.LISTENING: [S.LISTENING],
        S.PROCESSING: [S.LISTENING, S.PROCESSING],
        S.GENERATING: [S.LISTENING, S.PROCESSING, S.GENERATING],
        S.SPEAKING: [S.LISTENING, S.PROCESSING, S.GENERATING, S.SPEAKING],
        S.INTERRUPTED: [S.LISTENING, S.PROCESSING, S.INTERRUPTED],
        S.RECONNECTING: [S.RECONNECTING],
        S.ERROR: [S.ERROR],
    }
    for step in paths[state]:
        await machine.transition(step)
    return machine


VALID = [
    (S.IDLE, S.LISTENING),
    (S.LISTENING, S.PROCESSING),
    (S.PROCESSING, S.GENERATING),
    (S.GENERATING, S.SPEAKING),
    (S.GENERATING, S.LISTENING),
    (S.SPEAKING, S.LISTENING),
    (S.PROCESSING, S.INTERRUPTED),
    (S.GENERATING, S.INTERRUPTED),
    (S.SPEAKING, S.INTERRUPTED),
    (S.INTERRUPTED, S.LISTENING),
    (S.RECONNECTING, S.LISTENING),
    (S.SPEAKING, S.RECONNECTING),
    (S.LISTENING, S.ERROR),
    (S.SPEAKING, S.IDLE),
]

INVALID = [
    (S.IDLE, S.PROCESSING),
    (S.LISTENING, S.SPEAKING),
    (S.LISTENING, S.INTERRUPTED),
    (S.PROCESSING, S.SPEAKING),
    (S.PROCESSING, S.LISTENING),
    (S.SPEAKING, S.GENERATING),
    (S.INTERRUPTED, S.PROCESSING),
    (S.RECONNECTING, S.SPEAKING),
    (S.ERROR, S.LISTENING),
]


@pytest.mark.parametrize(("source", "target"), VALID)
async def test_valid_transition(source: VoiceState, target: VoiceState) -> None:
    machine = await _machine_in(source)
    await machine.transition(target)
    assert machine.state is target


@pytest.mark.parametrize(("source", "target"), INVALID)
async def test_invalid_transition_raises_and_keeps_state(
    source: VoiceState, target: VoiceState
) -> None:
    machine = await _machine_in(source)
    with pytest.raises(InvalidTransition):
        await machine.transition(target)
    assert machine.state is source


async def test_on_change_receives_each_new_state() -> None:
    seen: list[VoiceState] = []

    async def record(state: VoiceState) -> None:
        seen.append(state)

    machine = VoiceStateMachine(on_change=record)
    await machine.transition(S.LISTENING)
    await machine.transition(S.PROCESSING)
    assert seen == [S.LISTENING, S.PROCESSING]


async def test_state_is_stored_before_callback_runs() -> None:
    async def explode(state: VoiceState) -> None:
        raise RuntimeError

    machine = VoiceStateMachine(on_change=explode)
    with pytest.raises(RuntimeError):
        await machine.transition(S.LISTENING)
    assert machine.state is S.LISTENING
