import pytest

from app.safety.crisis import CrisisDetector

detector = CrisisDetector()


@pytest.mark.parametrize(
    "text",
    [
        "I want to kill myself",
        "Sometimes I think everyone would be better off dead.",
        "i don't want to live anymore",
        "I DON’T WANT TO LIVE like this",
        "I'm feeling suicidal",
        "मैं आत्महत्या करने की सोच रहा हूँ",
        "मुझे लगता है मैं मरना चाहती हूँ",
        "yaar mujhe marna chahta hu",
        "mujhe lagta hai main khudkushi kar loonga",
        "ab jeene ka mann nahi karta",
        "I keep thinking about self-harm",
    ],
)
def test_detects_crisis_language(text: str) -> None:
    note = detector.check(text)
    assert note is not None
    assert "14416" in note and "Tele-MANAS" in note


@pytest.mark.parametrize(
    "text",
    [
        "I had a stressful day at work",
        "This homework is killing me",
        "I'm so tired of everything today",
        "aaj bahut thaka hua hoon",
        "मुझे आज थोड़ा अकेलापन लग रहा है",
        "",
    ],
)
def test_ignores_ordinary_conversation(text: str) -> None:
    assert detector.check(text) is None
