#!/usr/bin/env python3
"""Web GUI for SML Book Dialog Extractor using Gradio."""

import os
import tempfile
from pathlib import Path

from sml_extractor.core import configure_booknlp_cache
if 'HF_HOME' not in os.environ:
    configure_booknlp_cache()
import gradio as gr

from sml_extractor.core import (
    check_booknlp_installation,
    convert_ebook_to_txt,
    extract_characters,
    load_booknlp_output,
    run_booknlp,
)
from sml_extractor.sml_generator import generate_sml_output, portable_voice_assignments, speaking_characters
from sml_extractor.voice_library import configured_library_root, ensure_voice_library
from sml_extractor.voice_matcher import (
    auto_assign_voices,
    get_voice_category_info,
    get_voice_display_name,
    scan_voice_library,
)

# Global state for the current session
_session_state = {}
APP_CSS = ".gradio-container { max-width: 980px !important; margin-inline: auto !important; }"
VERSION = (Path(__file__).resolve().parent / "VERSION.txt").read_text().strip()


def _get_file_path(file_obj) -> str:
    """Extract file path from a Gradio file object or string."""
    return file_obj.name if hasattr(file_obj, "name") else str(file_obj)


def _get_all_voice_paths(voice_library: dict) -> list:
    """Collect all voice file paths from the voice library into a flat list."""
    paths = []
    for age in voice_library:
        for gender in voice_library.get(age, {}):
            paths.extend(voice_library[age][gender])
    return sorted(paths)


def _voice_display_label(voice_path: str) -> str:
    """Create a human-readable label from a voice path, e.g. 'AlexandraHisakawa (adult/female)'."""
    info = get_voice_category_info(voice_path)
    name = info["name"]
    age = info["age"]
    gender = info["gender"]
    if age != "unknown" and gender != "unknown":
        return f"{name} ({age}/{gender})"
    return name


def preview_voice(voice_path: str | None) -> str | None:
    """Return the selected library voice for the browser's audio player."""
    if not voice_path:
        return None
    library_root = str(_session_state.get('library_root', configured_library_root()))
    absolute_path = portable_voice_assignments({'preview': voice_path}, library_root)['preview']
    return absolute_path if os.path.isfile(absolute_path) else None


def process_book(
    input_file:str|None,
    model_size:str,
    progress:gr.Progress=gr.Progress(),
)->tuple[object,...]:
    """Process a book file through BookNLP and extract characters."""
    if input_file is None:
        raise gr.Error("Please upload a book file.")

    library_root = str(configured_library_root())
    try:
        ensure_voice_library(library_root)
    except Exception as exc:
        raise gr.Error(f"Unable to prepare the voice library: {exc}") from exc

    progress(0.05, desc="Preparing...")

    # Create temp working directory
    work_dir = tempfile.mkdtemp(prefix="sml_extractor_")
    _session_state["work_dir"] = work_dir
    _session_state["generation"] = 0

    input_path = _get_file_path(input_file)

    # Convert to txt if needed
    progress(0.1, desc="Converting to text...")
    try:
        txt_path = convert_ebook_to_txt(input_path, work_dir)
    except RuntimeError as e:
        raise gr.Error(str(e))

    # Run BookNLP
    booknlp_dir = os.path.join(work_dir, "booknlp")
    progress(0.12, desc="Checking BookNLP installation...")

    ok, msg = check_booknlp_installation()
    if not ok:
        raise gr.Error(f"BookNLP is not properly installed:\n{msg}")

    progress(0.15, desc=f"Running BookNLP ({model_size} model)... This may take a while.")

    try:
        result = run_booknlp(txt_path, booknlp_dir, model_size, data_root=library_root)
    except Exception as e:
        raise gr.Error(f"BookNLP processing failed: {e}")

    book_id = result["book_id"]
    _session_state["book_id"] = book_id
    _session_state["booknlp_dir"] = booknlp_dir

    progress(0.6, desc="Loading results...")

    # Load data
    booknlp_data = load_booknlp_output(booknlp_dir, book_id)
    _session_state["booknlp_data"] = booknlp_data

    # Extract characters
    characters = speaking_characters(booknlp_data, extract_characters(booknlp_data))
    _session_state["characters"] = characters

    progress(0.7, desc="Scanning voice library...")

    # Scan this tool's voice library
    voice_library = scan_voice_library(library_root)
    _session_state["voice_library"] = voice_library
    _session_state["library_root"] = library_root

    # Auto-assign voices based on each character's inferred gender and age
    voice_assignments = {}
    if voice_library:
        voice_assignments = auto_assign_voices(characters, voice_library)
    _session_state["voice_assignments"] = voice_assignments

    progress(0.8, desc="Preparing character editor...")

    # Build character table for display
    char_table = _build_character_table(characters, voice_assignments)

    # Build available voices dropdown choices
    char_names = [c.get("normalized_name", "Unknown") for c in characters]
    voice_choices = _build_voice_choices(voice_library)

    progress(0.9, desc="Generating SML...")
    gen_status, sml_preview, sml_path = generate_output()
    progress(1.0, desc="Done!")

    num_voices = len(voice_assignments)
    status_msg = (
        f"Ready to download: {book_id}.e2a.sml.txt\n"
        f"{len(characters)} speaking characters; {num_voices} voices assigned."
    )

    # Update character dropdown choices
    char_dropdown_update = gr.update(choices=char_names, value=char_names[0] if char_names else None)
    first_voice = voice_assignments.get(char_names[0], "") if char_names else ""
    voice_dropdown_update = gr.update(choices=voice_choices, value=first_voice)

    return (
        status_msg,                         # status_output
        char_table,                         # char_table
        gen_status,                         # gen_status
        sml_preview,                        # sml_preview
        sml_path,                           # download
        gr.update(visible=True),            # char_voice_section
        char_dropdown_update,               # char_selector
        voice_dropdown_update,              # voice_selector
        preview_voice(first_voice),
    )


def _build_character_table(characters, voice_assignments):
    """Build a list-of-lists table for the character editor."""
    rows = []
    for char in characters:
        name = char.get("normalized_name", "Unknown")
        gender = char.get("inferred_gender", "unknown")
        age = char.get("inferred_age_category", "unknown")
        voice = voice_assignments.get(name, "")
        voice_display = _voice_display_label(voice) if voice else "(none)"
        rows.append([name, gender, age, voice_display])
    return rows


def _build_voice_choices(voice_library):
    """Build dropdown choices as (label, value) for available voices."""
    choices = [("(none)", "")]
    all_voices = _get_all_voice_paths(voice_library)
    for v in all_voices:
        choices.append((_voice_display_label(v), v))
    return choices


def on_char_selected(char_name):
    """Called when the user selects a character from the dropdown."""
    voice_assignments = _session_state.get("voice_assignments", {})
    # Pre-select the currently assigned voice in the voice dropdown
    current_voice = voice_assignments.get(char_name, "")
    return gr.update(value=current_voice), preview_voice(current_voice)


def reassign_voice(char_name, voice_path):
    """Assign a voice and immediately update the SML download."""
    if not char_name:
        return gr.update(), gr.update(), gr.update(), gr.update(), None

    if "voice_assignments" not in _session_state:
        _session_state["voice_assignments"] = {}

    if voice_path and voice_path.strip():
        _session_state["voice_assignments"][char_name] = voice_path.strip()
    else:
        _session_state["voice_assignments"].pop(char_name, None)

    char_table = _build_character_table(
        _session_state.get("characters", []), _session_state["voice_assignments"]
    )
    gen_status, sml_preview, sml_path = generate_output()

    return (
        char_table,
        gen_status,
        sml_path,
        sml_preview,
        preview_voice(voice_path),
    )


def generate_output(progress:gr.Progress=gr.Progress())->tuple[str,str,str]:
    """Generate the SML file accepted directly by ebook2audiobook."""
    if "booknlp_data" not in _session_state:
        raise gr.Error("Please process a book first.")

    booknlp_data = _session_state["booknlp_data"]
    characters = _session_state.get("characters", [])
    voice_assignments = _session_state.get("voice_assignments", {})
    book_id = _session_state.get("book_id", "book")
    work_dir = _session_state.get("work_dir", tempfile.mkdtemp(prefix="sml_extractor_"))

    book_txt = booknlp_data.get("book_txt", "")
    has_tokens = bool(booknlp_data.get("tokens"))
    if not book_txt and not has_tokens:
        raise gr.Error("No book text data found. BookNLP may not have generated output files.")

    progress(0.3, desc="Generating SML output...")

    revision = _session_state.get('generation', 0) + 1
    _session_state['generation'] = revision
    output_dir = os.path.join(work_dir, "sml_output", str(revision))
    os.makedirs(output_dir, exist_ok=True)

    progress(0.5, desc="Generating E2A-ready SML...")

    # Generate path-based SML with voice paths E2A can resolve.
    e2a_sml_path = os.path.join(output_dir, f"{book_id}.e2a.sml.txt")
    portable_assignments = portable_voice_assignments(voice_assignments, _session_state["library_root"])
    generate_sml_output(booknlp_data, characters, e2a_sml_path, portable_assignments, use_macros=False)

    progress(0.9, desc="Preparing download...")

    # Read generated content for preview
    with open(e2a_sml_path, "r", encoding="utf-8") as f:
        sml_content = f.read()

    sml_preview = sml_content[:5000] + ("..." if len(sml_content) > 5000 else "")

    progress(1.0, desc="Done!")

    return (
        "SML is ready. Download it above and open it in ebook2audiobook.",
        sml_preview,
        e2a_sml_path,
    )


def create_app()->gr.Blocks:
    """Create the Gradio web interface."""

    with gr.Blocks(
        title="SML Book Dialog Extractor",
    ) as app:

        gr.Markdown(
            """
            # Book to voice script · v{VERSION}
            Upload an English book. The SML file for ebook2audiobook is generated automatically.
            Change voices in **Characters & Voices**; the download updates automatically.
            """.format(VERSION=VERSION)
        )

        with gr.Tab("Book & Download"):
            with gr.Row():
                with gr.Column(scale=2):
                    input_file = gr.File(
                        label="Book file",
                        file_types=[".txt", ".epub", ".mobi", ".pdf", ".html", ".fb2", ".azw", ".azw3"],
                        type="filepath",
                    )
                with gr.Column(scale=1):
                    model_size = gr.Radio(
                        ["small", "big"],
                        value="big",
                        label="Analysis model",
                        info="Big is more accurate; small is faster.",
                    )

            process_btn = gr.Button("Create SML file", variant="primary")
            status_output = gr.Textbox(label="Status", interactive=False)
            e2a_sml_download = gr.File(label="Download SML for ebook2audiobook", interactive=False)
            gen_status = gr.Textbox(label="Script status", interactive=False)
            with gr.Accordion("Preview script", open=False):
                sml_preview = gr.Textbox(label="SML preview", lines=10, interactive=False)

        with gr.Tab("Characters & Voices"):
            gr.Markdown("Choose a character and a voice. The SML download updates automatically.")

            with gr.Group(visible=False) as char_voice_section:
                with gr.Row():
                    char_selector = gr.Dropdown(
                        label="Character",
                        choices=[],
                        interactive=True,
                    )
                    voice_selector = gr.Dropdown(
                        label="Voice",
                        choices=[],
                        interactive=True,
                    )
                voice_preview = gr.Audio(label="Listen to voice", interactive=False)

            with gr.Accordion("All speaking characters", open=False):
                char_table = gr.Dataframe(
                    headers=["Character", "Gender", "Age", "Assigned Voice"],
                    datatype=["str", "str", "str", "str"],
                    label="Voice assignments",
                    interactive=False,
                )

        # --- Wire up events ---

        # Process book → populate character table, dropdowns, and preview
        process_btn.click(
            fn=process_book,
            inputs=[input_file, model_size],
            outputs=[
                status_output,
                char_table,
                gen_status,
                sml_preview,
                e2a_sml_download,
                char_voice_section,
                char_selector,
                voice_selector,
                voice_preview,
            ],
        )

        # Selecting a character → show details and current voice
        char_selector.change(
            fn=on_char_selected,
            inputs=[char_selector],
            outputs=[voice_selector, voice_preview],
        )

        # Only user changes trigger regeneration; selecting another character
        # updates the dropdown without changing its assigned voice.
        voice_selector.input(
            fn=reassign_voice,
            inputs=[char_selector, voice_selector],
            outputs=[char_table, gen_status, e2a_sml_download, sml_preview, voice_preview],
        )

    return app


if __name__ == "__main__":
    app = create_app()
    app.launch(server_name="127.0.0.1", server_port=7861, theme=gr.themes.Soft(), css=APP_CSS)
