"""Short native-script phrases per language: voice previews and the live verification script."""

from __future__ import annotations

SAMPLE_TEXT: dict[str, str] = {
    "en-IN": "Hello, how are you doing today? I am happy to talk with you.",
    "hi-IN": "नमस्ते, आज आप कैसे हैं? मुझे आपसे बात करके बहुत खुशी हुई।",
    "bn-IN": "নমস্কার, আজ আপনি কেমন আছেন? আপনার সঙ্গে কথা বলে আমার খুব ভালো লাগছে।",
    "gu-IN": "નમસ્તે, આજે તમે કેમ છો? તમારી સાથે વાત કરીને મને ખૂબ આનંદ થયો.",
    "kn-IN": "ನಮಸ್ಕಾರ, ಇಂದು ನೀವು ಹೇಗಿದ್ದೀರಿ? ನಿಮ್ಮೊಂದಿಗೆ ಮಾತನಾಡಲು ನನಗೆ ತುಂಬಾ ಸಂತೋಷವಾಗಿದೆ.",
    "ml-IN": "നമസ്കാരം, ഇന്ന് നിങ്ങൾക്ക് സുഖമാണോ? നിങ്ങളോട് സംസാരിക്കുന്നതിൽ എനിക്ക് വളരെ സന്തോഷമുണ്ട്.",
    "mr-IN": "नमस्कार, आज तुम्ही कसे आहात? तुमच्याशी बोलून मला खूप आनंद झाला.",
    "pa-IN": "ਸਤ ਸ੍ਰੀ ਅਕਾਲ, ਅੱਜ ਤੁਸੀਂ ਕਿਵੇਂ ਹੋ? ਤੁਹਾਡੇ ਨਾਲ ਗੱਲ ਕਰਕੇ ਮੈਨੂੰ ਬਹੁਤ ਖੁਸ਼ੀ ਹੋਈ।",
    "ta-IN": "வணக்கம், இன்று நீங்கள் எப்படி இருக்கிறீர்கள்? உங்களுடன் பேசுவதில் எனக்கு மகிழ்ச்சி.",
    "te-IN": "నమస్కారం, ఈ రోజు మీరు ఎలా ఉన్నారు? మీతో మాట్లాడటం నాకు చాలా సంతోషంగా ఉంది.",
}

# Two-word greetings, used to smoke-test that every voice can speak its preferred language.
TWO_WORDS: dict[str, str] = {
    "en-IN": "Hello friend",
    "hi-IN": "नमस्ते दोस्त",
    "bn-IN": "নমস্কার বন্ধু",
    "gu-IN": "નમસ્તે મિત્ર",
    "kn-IN": "ನಮಸ್ಕಾರ ಸ್ನೇಹಿತ",
    "ml-IN": "നമസ്കാരം സുഹൃത്തേ",
    "mr-IN": "नमस्कार मित्रा",
    "pa-IN": "ਨਮਸਤੇ ਦੋਸਤ",
    "ta-IN": "வணக்கம் நண்பா",
    "te-IN": "నమస్కారం మిత్రమా",
}


def sample_for(language: str) -> str:
    return SAMPLE_TEXT[language]
