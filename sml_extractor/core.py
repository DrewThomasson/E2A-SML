"""Core module for running BookNLP and extracting dialog/character data."""

import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path


# Supported language configurations.
# booknlp_lang: language code passed to BookNLP("lang", ...)
# voice_lang:   sub-directory under voices/ in ebook2audiobook
# spacy_model:  spaCy model required by this language
# pipeline:     BookNLP pipeline components (French doesn't support supersense/event)
LANGUAGE_CONFIGS = {
    "en": {
        "booknlp_lang": "en",
        "voice_lang": "eng",
        "spacy_model": "en_core_web_sm",
        "pipeline": "entity,quote,supersense,event,coref",
        "display_name": "English",
    },
    "fr": {
        "booknlp_lang": "fr",
        "voice_lang": "fra",
        "spacy_model": "fr_core_news_sm",
        "pipeline": "entity,quote,coref",
        "display_name": "French",
    },
}

# Aliases that map common alternate codes to a canonical key in LANGUAGE_CONFIGS
_LANGUAGE_ALIASES = {
    "eng": "en",
    "english": "en",
    "fra": "fr",
    "french": "fr",
    "français": "fr",
    "francais": "fr",
}


def normalize_language_code(lang: str) -> str:
    """Normalise a user-supplied language string to a canonical two-letter code.

    Accepts values such as "en", "eng", "English", "fr", "fra", "French".
    Falls back to "en" for unrecognised inputs.
    """
    key = lang.strip().lower()
    # Direct match first
    if key in LANGUAGE_CONFIGS:
        return key
    # Alias lookup
    return _LANGUAGE_ALIASES.get(key, "en")


def check_booknlp_installation(language: str = "en") -> tuple[bool, str]:
    """Check if BookNLP and its dependencies are properly installed.

    Args:
        language: Language to check ('en' or 'fr').  The appropriate spaCy
                  model for that language is verified.

    Returns:
        Tuple of (is_ok, message). If not ok, message describes what's missing.
    """
    lang = normalize_language_code(language)
    lang_cfg = LANGUAGE_CONFIGS[lang]
    spacy_model = lang_cfg["spacy_model"]

    errors = []

    # Check booknlp package
    try:
        import booknlp  # noqa: F401
    except ImportError:
        errors.append(
            "booknlp-plus is not installed. Install it with:\n"
            "  pip install booknlp-plus"
        )
        return False, "\n".join(errors)

    # Check key dependencies that commonly fail
    dep_checks = [
        ("torch", "torch", "pip install torch"),
        ("transformers", "transformers", "pip install transformers>=4.30.0"),
        ("spacy", "spacy", "pip install spacy>=3.5.0"),
        ("sentence_transformers", "sentence-transformers", "pip install sentence-transformers"),
        ("tf_keras", "tf-keras", "pip install tf-keras"),
        ("numpy", "numpy", "pip install numpy>=1.24.0"),
        ("pandas", "pandas", "pip install pandas>=1.3.0"),
    ]

    for module_name, pkg_name, install_cmd in dep_checks:
        try:
            __import__(module_name)
        except ImportError:
            errors.append(f"  - {pkg_name} is missing. Fix: {install_cmd}")

    if errors:
        return False, (
            "BookNLP dependencies are missing:\n"
            + "\n".join(errors)
            + "\n\nOr install all at once:\n"
            "  pip install booknlp-plus\n"
            f"  python -m spacy download {spacy_model}"
        )

    # Check spacy model for the requested language
    try:
        import spacy
        spacy.load(spacy_model)
    except OSError:
        lang_display = lang_cfg["display_name"]
        errors.append(
            f"spaCy {lang_display} model not found. Install it with:\n"
            f"  python -m spacy download {spacy_model}"
        )

    if errors:
        return False, "\n".join(errors)

    # Try the actual BookNLP import that tends to fail
    try:
        from booknlp.booknlp import BookNLP  # noqa: F401
    except (ImportError, ModuleNotFoundError, AttributeError, OSError) as e:
        return False, (
            f"BookNLP failed to initialize: {e}\n\n"
            "This usually means a dependency version conflict.\n"
            "Try reinstalling in a clean environment:\n"
            "  pip install --force-reinstall booknlp-plus\n"
            f"  python -m spacy download {spacy_model}"
        )

    return True, "BookNLP is ready."


def convert_ebook_to_txt(input_file: str, output_dir: str) -> str:
    """Convert an ebook file to plain text using calibre's ebook-convert if needed.

    Supports: .txt, .epub, .mobi, .pdf, .html, .fb2, .azw, .azw3
    Returns the path to the plain text file.
    """
    ext = Path(input_file).suffix.lower()
    if ext == ".txt":
        return input_file

    txt_path = os.path.join(output_dir, Path(input_file).stem + ".txt")
    try:
        import subprocess

        result = subprocess.run(
            ["ebook-convert", input_file, txt_path],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"ebook-convert failed: {result.stderr}"
            )
        return txt_path
    except FileNotFoundError:
        raise RuntimeError(
            "ebook-convert not found. Install Calibre to convert non-txt ebook formats. "
            "Download from: https://calibre-ebook.com/download"
        )


def run_booknlp(
    input_file: str,
    output_dir: str,
    model: str = "small",
    language: str = "en",
    progress_callback=None,
) -> dict:
    """Run BookNLP pipeline on a text file and return extracted data.

    Args:
        input_file: Path to the input text file.
        output_dir: Directory for BookNLP output files.
        model: BookNLP model size ('small' or 'big').
        language: Language code for BookNLP ('en' for English, 'fr' for French).
                  Also accepts aliases such as 'eng', 'fra'.
        progress_callback: Optional callable(message, pct) for progress updates.

    Returns:
        Dict with keys: 'book_id', 'output_dir', 'characters', 'tokens_file',
                        'quotes_file', 'entities_file', 'book_file', 'language'

    Raises:
        RuntimeError: If BookNLP or its dependencies are not properly installed.
    """
    lang = normalize_language_code(language)
    lang_cfg = LANGUAGE_CONFIGS[lang]

    # Pre-check installation before attempting import
    ok, msg = check_booknlp_installation(lang)
    if not ok:
        raise RuntimeError(f"BookNLP installation check failed:\n{msg}")

    from booknlp.booknlp import BookNLP

    os.makedirs(output_dir, exist_ok=True)
    book_id = Path(input_file).stem

    if progress_callback:
        progress_callback("Initializing BookNLP...", 5)

    model_params = {
        "pipeline": lang_cfg["pipeline"],
        "model": model,
    }

    booknlp = BookNLP(lang_cfg["booknlp_lang"], model_params)

    if progress_callback:
        progress_callback(f"Processing book with BookNLP ({model} model, {lang_cfg['display_name']})...", 10)

    booknlp.process(input_file, output_dir, book_id)

    if progress_callback:
        progress_callback("BookNLP processing complete.", 60)

    result = {
        "book_id": book_id,
        "output_dir": output_dir,
        "language": lang,
        "tokens_file": os.path.join(output_dir, f"{book_id}.tokens"),
        "quotes_file": os.path.join(output_dir, f"{book_id}.quotes"),
        "entities_file": os.path.join(output_dir, f"{book_id}.entities"),
        "book_file": os.path.join(output_dir, f"{book_id}.book"),
    }

    # Also check for generated character/book text files
    char_json = os.path.join(output_dir, f"{book_id}.characters_simple.json")
    book_txt = os.path.join(output_dir, f"{book_id}.book.txt")
    if os.path.exists(char_json):
        result["characters_simple_file"] = char_json
    if os.path.exists(book_txt):
        result["book_txt_file"] = book_txt

    return result


def load_booknlp_output(output_dir: str, book_id: str) -> dict:
    """Load BookNLP output files and return structured data.

    Returns dict with: tokens, quotes, entities, characters, book_data
    """
    data = {"book_id": book_id, "output_dir": output_dir}

    # Load tokens
    tokens_file = os.path.join(output_dir, f"{book_id}.tokens")
    if os.path.exists(tokens_file):
        data["tokens"] = _parse_tokens_file(tokens_file)

    # Load quotes
    quotes_file = os.path.join(output_dir, f"{book_id}.quotes")
    if os.path.exists(quotes_file):
        data["quotes"] = _parse_quotes_file(quotes_file)

    # Load entities
    entities_file = os.path.join(output_dir, f"{book_id}.entities")
    if os.path.exists(entities_file):
        data["entities"] = _parse_entities_file(entities_file)

    # Load book JSON
    book_file = os.path.join(output_dir, f"{book_id}.book")
    if os.path.exists(book_file):
        with open(book_file, "r", encoding="utf-8") as f:
            data["book_data"] = json.load(f)

    # Load characters_simple.json if exists
    char_file = os.path.join(output_dir, f"{book_id}.characters_simple.json")
    if os.path.exists(char_file):
        with open(char_file, "r", encoding="utf-8") as f:
            data["characters_simple"] = json.load(f)

    # Load book.txt if exists
    book_txt = os.path.join(output_dir, f"{book_id}.book.txt")
    if os.path.exists(book_txt):
        with open(book_txt, "r", encoding="utf-8") as f:
            data["book_txt"] = f.read()

    return data


def _parse_tokens_file(filepath: str) -> list:
    """Parse BookNLP tokens file into list of token dicts."""
    tokens = []
    with open(filepath, "r", encoding="utf-8") as f:
        header = f.readline().strip().split("\t")
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) >= len(header):
                token = dict(zip(header, parts))
                tokens.append(token)
    return tokens


def _parse_quotes_file(filepath: str) -> list:
    """Parse BookNLP quotes file into list of quote dicts."""
    quotes = []
    with open(filepath, "r", encoding="utf-8") as f:
        header = f.readline().strip().split("\t")
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) >= len(header):
                quote = dict(zip(header, parts))
                quotes.append(quote)
    return quotes


def _parse_entities_file(filepath: str) -> list:
    """Parse BookNLP entities file into list of entity dicts."""
    entities = []
    with open(filepath, "r", encoding="utf-8") as f:
        header = f.readline().strip().split("\t")
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) >= len(header):
                entity = dict(zip(header, parts))
                entities.append(entity)
    return entities


def extract_characters(booknlp_data: dict, language: str = "en") -> list:
    """Extract character information from BookNLP output.

    Args:
        booknlp_data: Dict returned by load_booknlp_output().
        language: Language code ('en' or 'fr').  Used to set the 'language'
                  field in the returned character dicts to the matching
                  ebook2audiobook voice-library language code (e.g. 'eng', 'fra').

    Returns:
        List of character dicts with:
            normalized_name, inferred_gender, inferred_age_category, voice, language
    """
    lang = normalize_language_code(language)
    voice_lang = LANGUAGE_CONFIGS[lang]["voice_lang"]

    # If characters_simple already exists from BookNLP, use that
    if "characters_simple" in booknlp_data:
        return booknlp_data["characters_simple"].get("characters", [])

    # Otherwise, extract from book_data
    characters = []

    # Always add narrator first
    characters.append(
        {
            "normalized_name": "Narrator",
            "inferred_gender": "unknown",
            "inferred_age_category": "unknown",
            "tts_engine": "XTTSv2",
            "language": voice_lang,
            "voice": None,
        }
    )

    if "book_data" not in booknlp_data:
        return characters

    book_data = booknlp_data["book_data"]
    char_data = book_data.get("characters", [])

    for char in char_data:
        gender = _infer_gender(char)
        age = _infer_age(char)
        proper_names = char.get("names", {}).get("proper", [])
        raw_name = proper_names[0] if proper_names else f"Character{char.get('id', 0)}"
        name = _normalize_name(raw_name)

        characters.append(
            {
                "normalized_name": name,
                "inferred_gender": gender,
                "inferred_age_category": age,
                "tts_engine": "XTTSv2",
                "language": voice_lang,
                "voice": None,
            }
        )

    return characters


def _infer_gender(char: dict) -> str:
    """Infer gender from character data."""
    gender_data = char.get("g", None)
    if gender_data is None:
        return "unknown"

    if isinstance(gender_data, dict):
        he_count = gender_data.get("he/him/his", 0)
        she_count = gender_data.get("she/her", 0)
        they_count = gender_data.get("they/them/their", 0)

        if he_count > she_count and he_count > they_count:
            return "male"
        elif she_count > he_count and she_count > they_count:
            return "female"
        else:
            return "unknown"
    elif isinstance(gender_data, str):
        if gender_data in ("he/him/his", "male"):
            return "male"
        elif gender_data in ("she/her", "female"):
            return "female"

    return "unknown"


def _infer_age(char: dict) -> str:
    """Infer age category from character data."""
    # Check if age was already inferred
    if "inferred_age_category" in char:
        return char["inferred_age_category"]

    # Look at modifiers and actions for age clues
    modifiers = char.get("modifiers", [])
    mod_text = " ".join(str(m) for m in modifiers).lower() if modifiers else ""

    child_words = {"child", "boy", "girl", "baby", "infant", "toddler", "kid", "little", "young"}
    teen_words = {"teen", "teenager", "adolescent", "youth", "teenage"}
    elder_words = {"old", "elderly", "aged", "ancient", "grandfather", "grandmother", "grandpa", "grandma"}

    for word in child_words:
        if word in mod_text:
            return "child"
    for word in teen_words:
        if word in mod_text:
            return "teen"
    for word in elder_words:
        if word in mod_text:
            return "elder"

    return "adult"


def _normalize_name(name: str) -> str:
    """Normalize a character name to CamelCase."""
    # Remove special characters
    name = re.sub(r"[^a-zA-Z0-9\s]", "", name)
    # Convert to CamelCase
    parts = name.strip().split()
    return "".join(word.capitalize() for word in parts) if parts else "Unknown"
