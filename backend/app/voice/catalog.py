"""Languages and Gnani Timbre voices Oryn can speak with.

Source: https://docs.gnani.ai/api/TTS/available-voices.md (timbre-v2.5, 42 voices, each with a
preferred language). A voice is used for its preferred language; for any other language the
default voice of that language is chosen instead, keeping the user's gender preference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AUTO_LANGUAGE = "auto"  # profile/session value meaning "detect per utterance" (Prisma indic-auto)

# Languages both Gnani Prisma (STT) and Gnani Timbre (TTS) support.
LANGUAGES: dict[str, str] = {
    "en-IN": "English",
    "hi-IN": "Hindi",
    "bn-IN": "Bengali",
    "gu-IN": "Gujarati",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
    "mr-IN": "Marathi",
    "pa-IN": "Punjabi",
    "ta-IN": "Tamil",
    "te-IN": "Telugu",
}

Gender = Literal["female", "male"]


@dataclass(frozen=True, slots=True)
class Voice:
    name: str
    gender: Gender
    language: str  # preferred language code
    hinglish: bool = False


def _voices(language: str, female: str, male: str = "") -> list[Voice]:
    return [Voice(n, "female", language) for n in female.split()] + [
        Voice(n, "male", language) for n in male.split()
    ]


VOICES: tuple[Voice, ...] = (
    Voice("Poorvi", "female", "hi-IN", hinglish=True),
    *_voices(
        "hi-IN",
        "Ambuja Bhavna Chitra Jwala Nalini Urmila Yashvi",
        "Deepak Hemraj Jalaj Omkar Roopesh Vikrant",
    ),
    *_voices("en-IN", "Devika Kaveri Trupti", "Girish Pranav Shlok"),
    *_voices("ta-IN", "Asmita Brinda Noopur Trisha Vedika"),
    *_voices("te-IN", "Lavanya Lehara Suhana Varuni Yukti"),
    *_voices("kn-IN", "Saanvi", "Kavin"),
    *_voices("ml-IN", "Reshma", "Riyaan"),
    *_voices("mr-IN", "Zahira", "Ishaan"),
    *_voices("pa-IN", "Mehuli", "Zayan"),
    *_voices("bn-IN", "Kirra", "Dhruva"),
    *_voices("gu-IN", "Falak", "Veera"),
)
_BY_NAME = {v.name.lower(): v for v in VOICES}

# Poorvi is verified live for hi-IN, en-IN and Hinglish; the rest are the first listed voice.
DEFAULT_VOICE: dict[str, str] = {
    "hi-IN": "Poorvi",
    "en-IN": "Poorvi",
    "ta-IN": "Asmita",
    "te-IN": "Lavanya",
    "kn-IN": "Saanvi",
    "ml-IN": "Reshma",
    "mr-IN": "Zahira",
    "pa-IN": "Mehuli",
    "bn-IN": "Kirra",
    "gu-IN": "Falak",
}


def find_voice(name: str | None) -> Voice | None:
    return _BY_NAME.get(name.lower()) if name else None


def speaks(voice: Voice, language: str) -> bool:
    return voice.language == language or (voice.hinglish and language == "en-IN")


def resolve_voice(preferred: str | None, language: str) -> str:
    """The voice to synthesise `language` with, honouring the user's choice when it fits."""
    chosen = find_voice(preferred)
    if chosen is not None and speaks(chosen, language):
        return chosen.name
    default = DEFAULT_VOICE.get(language, DEFAULT_VOICE["hi-IN"])
    if chosen is not None:
        same_gender = [v for v in VOICES if v.language == language and v.gender == chosen.gender]
        if same_gender:
            return same_gender[0].name
    return default
