"""SML output generator - converts BookNLP output to SML format for ebook2audiobook."""

import json
import os
import re
from pathlib import Path


def generate_sml_output(
    book_txt_content: str,
    characters: list,
    output_path: str,
    voice_assignments: dict | None = None,
) -> str:
    """Generate SML-formatted text file from BookNLP book.txt and character data.

    The BookNLP book.txt format is:
        [CharacterName] Sentence text here. [/]
        [Narrator] Narration text here. [/]

    The SML output format for ebook2audiobook uses [voice:] tags:
        [voice:/path/to/voice.wav]Sentence text here.[/voice]

    Args:
        book_txt_content: Content of the BookNLP .book.txt file.
        characters: List of character dicts with voice assignments.
        output_path: Path to write the SML output file.
        voice_assignments: Optional dict mapping character names to voice file paths.

    Returns:
        Path to the generated SML file.
    """
    # Build character-to-voice mapping
    char_voice_map = _build_voice_map(characters, voice_assignments)

    # Parse book.txt lines
    lines = book_txt_content.strip().split("\n")

    sml_lines = []
    current_voice = None

    for line in lines:
        line = line.strip()
        if not line:
            sml_lines.append("")
            continue

        # Parse [CharacterName] text [/] format
        match = re.match(r"^\[([^\]]+)\]\s*(.*?)\s*\[/\]$", line)
        if match:
            char_name = match.group(1)
            text = match.group(2).strip()

            if not text:
                continue

            voice_path = char_voice_map.get(char_name)

            if voice_path and voice_path != current_voice:
                # Switch voice
                if current_voice is not None:
                    sml_lines.append("[/voice]")
                sml_lines.append(f"[voice:{voice_path}]")
                current_voice = voice_path

            sml_lines.append(text)
        else:
            # Line doesn't match expected format, keep as-is
            sml_lines.append(line)

    # Close any open voice tag
    if current_voice is not None:
        sml_lines.append("[/voice]")

    sml_content = "\n".join(sml_lines)

    # Write output
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(sml_content)

    return output_path


def generate_characters_json(
    characters: list,
    output_path: str,
    voice_assignments: dict | None = None,
) -> str:
    """Generate characters_simple.json with voice assignments.

    Args:
        characters: List of character dicts.
        output_path: Path to write the JSON file.
        voice_assignments: Optional dict mapping character names to voice paths.

    Returns:
        Path to the generated JSON file.
    """
    if voice_assignments:
        for char in characters:
            name = char.get("normalized_name", "")
            if name in voice_assignments:
                char["voice"] = voice_assignments[name]

    result = {"characters": characters}

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    return output_path


def _build_voice_map(
    characters: list, voice_assignments: dict | None = None
) -> dict:
    """Build a mapping of character names to voice file paths.

    Args:
        characters: List of character dicts with potential voice fields.
        voice_assignments: Optional override dict mapping names to voice paths.

    Returns:
        Dict mapping character normalized_name to voice file path.
    """
    voice_map = {}

    # First, collect from character data
    for char in characters:
        name = char.get("normalized_name", "")
        voice = char.get("voice")
        if name and voice:
            voice_map[name] = voice

    # Override with explicit assignments
    if voice_assignments:
        voice_map.update(voice_assignments)

    return voice_map
