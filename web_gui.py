#!/usr/bin/env python3
"""Web GUI for SML Book Dialog Extractor using Gradio."""

import json
import os
import shutil
import tempfile
from pathlib import Path

import gradio as gr

from sml_extractor.core import (
    convert_ebook_to_txt,
    extract_characters,
    load_booknlp_output,
    run_booknlp,
)
from sml_extractor.sml_generator import generate_characters_json, generate_sml_output
from sml_extractor.voice_matcher import (
    auto_assign_voices,
    get_voice_category_info,
    get_voice_display_name,
    scan_custom_voices,
    scan_voice_library,
)

# Global state for the current session
_session_state = {}


def _get_file_path(file_obj) -> str:
    """Extract file path from a Gradio file object or string."""
    return file_obj.name if hasattr(file_obj, "name") else str(file_obj)


def process_book(
    input_file,
    model_size,
    e2a_path,
    progress=gr.Progress(),
):
    """Process a book file through BookNLP and extract characters."""
    if input_file is None:
        raise gr.Error("Please upload a book file.")

    progress(0.05, desc="Preparing...")

    # Create temp working directory
    work_dir = tempfile.mkdtemp(prefix="sml_extractor_")
    _session_state["work_dir"] = work_dir

    input_path = _get_file_path(input_file)

    # Convert to txt if needed
    progress(0.1, desc="Converting to text...")
    try:
        txt_path = convert_ebook_to_txt(input_path, work_dir)
    except RuntimeError as e:
        raise gr.Error(str(e))

    # Run BookNLP
    booknlp_dir = os.path.join(work_dir, "booknlp")
    progress(0.15, desc=f"Running BookNLP ({model_size} model)... This may take a while.")

    try:
        result = run_booknlp(txt_path, booknlp_dir, model_size)
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
    characters = extract_characters(booknlp_data)
    _session_state["characters"] = characters

    progress(0.7, desc="Scanning voice library...")

    # Scan voice library if path provided
    voice_library = {}
    if e2a_path and e2a_path.strip() and os.path.isdir(e2a_path.strip()):
        voice_library = scan_voice_library(e2a_path.strip())
    _session_state["voice_library"] = voice_library
    _session_state["e2a_path"] = e2a_path.strip() if e2a_path else ""

    # Auto-assign voices
    voice_assignments = {}
    if voice_library:
        voice_assignments = auto_assign_voices(characters, voice_library)
    _session_state["voice_assignments"] = voice_assignments

    progress(0.8, desc="Preparing character editor...")

    # Build character table for display
    char_table = _build_character_table(characters, voice_assignments)

    # Build available voices list
    voices_list = _build_voices_list(voice_library)

    # Get preview of book text
    book_txt = booknlp_data.get("book_txt", "")
    preview = book_txt[:3000] + ("..." if len(book_txt) > 3000 else "")

    progress(1.0, desc="Done!")

    status_msg = (
        f"✅ Processed successfully!\n"
        f"📚 Book ID: {book_id}\n"
        f"👥 Characters found: {len(characters)}\n"
        f"🎤 Voices auto-assigned: {len(voice_assignments)}"
    )

    return (
        status_msg,
        char_table,
        preview,
        gr.update(visible=True),  # Show character editor
        gr.update(visible=True),  # Show generate button
        voices_list,
    )


def _build_character_table(characters, voice_assignments):
    """Build a list-of-lists table for the character editor."""
    rows = []
    for char in characters:
        name = char.get("normalized_name", "Unknown")
        gender = char.get("inferred_gender", "unknown")
        age = char.get("inferred_age_category", "unknown")
        voice = voice_assignments.get(name, "")
        voice_display = get_voice_display_name(voice) if voice else "(none)"
        rows.append([name, gender, age, voice_display, voice])
    return rows


def _build_voices_list(voice_library):
    """Build a formatted string of available voices."""
    if not voice_library:
        return "No voice library loaded. Provide ebook2audiobook path to auto-assign voices."

    lines = ["Available voices from ebook2audiobook:\n"]
    for age in ["adult", "teen", "child", "elder"]:
        if age not in voice_library:
            continue
        for gender in ["female", "male"]:
            voices = voice_library.get(age, {}).get(gender, [])
            if voices:
                lines.append(f"  {age}/{gender}: {len(voices)} voices")
                for v in voices[:5]:
                    lines.append(f"    - {get_voice_display_name(v)}")
                if len(voices) > 5:
                    lines.append(f"    ... and {len(voices) - 5} more")
    return "\n".join(lines)


def update_voice_assignment(char_name, voice_path):
    """Update a single character's voice assignment."""
    if "voice_assignments" not in _session_state:
        _session_state["voice_assignments"] = {}

    if voice_path and voice_path.strip():
        _session_state["voice_assignments"][char_name] = voice_path.strip()
    elif char_name in _session_state["voice_assignments"]:
        del _session_state["voice_assignments"][char_name]

    return f"Updated: {char_name} → {voice_path if voice_path else '(none)'}"


def upload_custom_voice(voice_file, char_name):
    """Handle uploading a custom voice file for a character."""
    if voice_file is None or not char_name:
        return "Please select a character and upload a voice file."

    work_dir = _session_state.get("work_dir", tempfile.mkdtemp(prefix="sml_extractor_"))
    voices_dir = os.path.join(work_dir, "custom_voices")
    os.makedirs(voices_dir, exist_ok=True)

    voice_src = _get_file_path(voice_file)
    voice_dest = os.path.join(voices_dir, os.path.basename(voice_src))
    shutil.copy2(voice_src, voice_dest)

    if "voice_assignments" not in _session_state:
        _session_state["voice_assignments"] = {}
    _session_state["voice_assignments"][char_name] = voice_dest

    return f"✅ Assigned {os.path.basename(voice_dest)} to {char_name}"


def generate_output(progress=gr.Progress()):
    """Generate the SML output files."""
    if "booknlp_data" not in _session_state:
        raise gr.Error("Please process a book first.")

    booknlp_data = _session_state["booknlp_data"]
    characters = _session_state.get("characters", [])
    voice_assignments = _session_state.get("voice_assignments", {})
    book_id = _session_state.get("book_id", "book")
    work_dir = _session_state.get("work_dir", tempfile.mkdtemp(prefix="sml_extractor_"))

    book_txt = booknlp_data.get("book_txt", "")
    if not book_txt:
        raise gr.Error("No book text data found. BookNLP may not have generated book.txt.")

    progress(0.3, desc="Generating SML output...")

    output_dir = os.path.join(work_dir, "sml_output")
    os.makedirs(output_dir, exist_ok=True)

    # Generate SML text
    sml_path = os.path.join(output_dir, f"{book_id}.sml.txt")
    generate_sml_output(book_txt, characters, sml_path, voice_assignments)

    progress(0.6, desc="Generating characters JSON...")

    # Generate characters JSON
    char_json_path = os.path.join(output_dir, f"{book_id}.characters.json")
    generate_characters_json(characters, char_json_path, voice_assignments)

    progress(0.9, desc="Preparing download...")

    # Read generated content for preview
    with open(sml_path, "r", encoding="utf-8") as f:
        sml_content = f.read()

    sml_preview = sml_content[:5000] + ("..." if len(sml_content) > 5000 else "")

    progress(1.0, desc="Done!")

    return (
        f"✅ Generated successfully!\n\nFiles:\n  - {sml_path}\n  - {char_json_path}",
        sml_preview,
        sml_path,
        char_json_path,
    )


def create_app():
    """Create the Gradio web application."""

    with gr.Blocks(
        title="SML Book Dialog Extractor",
        theme=gr.themes.Soft(),
    ) as app:

        gr.Markdown(
            """
            # 📚 SML Book Dialog Extractor

            Convert books to **SML format** for multi-speaker audiobook generation with
            [ebook2audiobook](https://github.com/DrewThomasson/ebook2audiobook).

            This tool uses [BookNLP](https://github.com/DrewThomasson/booknlp) to analyze books,
            identify characters and their dialog, then generates SML-tagged output with voice assignments.

            ### How it works:
            1. **Upload** a book file (.txt, .epub, .mobi, etc.)
            2. **Analyze** - BookNLP identifies characters, dialog, and narration
            3. **Assign voices** - Auto-assign from ebook2audiobook library or upload custom voices
            4. **Generate** - Download SML output ready for ebook2audiobook
            """
        )

        with gr.Tab("📖 Process Book"):
            with gr.Row():
                with gr.Column(scale=2):
                    input_file = gr.File(
                        label="📁 Upload Book File",
                        file_types=[".txt", ".epub", ".mobi", ".pdf", ".html", ".fb2", ".azw", ".azw3"],
                        type="filepath",
                    )
                with gr.Column(scale=1):
                    model_size = gr.Radio(
                        ["small", "big"],
                        value="small",
                        label="🧠 BookNLP Model",
                        info="'big' is more accurate but slower and requires more RAM/GPU",
                    )
                    e2a_path = gr.Textbox(
                        label="📂 ebook2audiobook Path (optional)",
                        placeholder="/path/to/ebook2audiobook",
                        info="Path to local ebook2audiobook repo for voice auto-assignment",
                    )

            process_btn = gr.Button("🔍 Analyze Book", variant="primary", size="lg")
            status_output = gr.Textbox(label="Status", interactive=False)

        with gr.Tab("👥 Characters & Voices"):
            voices_info = gr.Textbox(
                label="📋 Available Voices",
                interactive=False,
                lines=8,
            )

            char_table = gr.Dataframe(
                headers=["Character", "Gender", "Age", "Voice", "Voice Path"],
                datatype=["str", "str", "str", "str", "str"],
                label="Characters",
                interactive=False,
                visible=False,
            )

            with gr.Row(visible=False) as voice_editor:
                with gr.Column():
                    gr.Markdown("### Assign Custom Voice")
                    char_name_input = gr.Textbox(
                        label="Character Name",
                        placeholder="Enter character name exactly as shown above",
                    )
                    voice_path_input = gr.Textbox(
                        label="Voice File Path",
                        placeholder="/path/to/voice.wav",
                        info="Full path to a .wav voice file",
                    )
                    assign_btn = gr.Button("🎤 Assign Voice")
                    assign_status = gr.Textbox(label="Assignment Status", interactive=False)

                with gr.Column():
                    gr.Markdown("### Upload Voice File")
                    upload_char_name = gr.Textbox(
                        label="Character Name",
                        placeholder="Enter character name",
                    )
                    voice_upload = gr.File(
                        label="Upload Voice (.wav)",
                        file_types=[".wav", ".mp3", ".flac", ".ogg"],
                        type="filepath",
                    )
                    upload_btn = gr.Button("⬆️ Upload & Assign")
                    upload_status = gr.Textbox(label="Upload Status", interactive=False)

        with gr.Tab("📝 Preview & Generate"):
            book_preview = gr.Textbox(
                label="📖 Book Text Preview (BookNLP tagged)",
                lines=15,
                interactive=False,
            )

            generate_btn = gr.Button(
                "🎵 Generate SML Output",
                variant="primary",
                size="lg",
                visible=False,
            )

            gen_status = gr.Textbox(label="Generation Status", interactive=False)
            sml_preview = gr.Textbox(
                label="📄 SML Output Preview",
                lines=15,
                interactive=False,
            )

            with gr.Row():
                sml_download = gr.File(label="📥 Download SML Text", interactive=False)
                json_download = gr.File(label="📥 Download Characters JSON", interactive=False)

        # Wire up events
        process_btn.click(
            fn=process_book,
            inputs=[input_file, model_size, e2a_path],
            outputs=[
                status_output,
                char_table,
                book_preview,
                voice_editor,
                generate_btn,
                voices_info,
            ],
        )

        assign_btn.click(
            fn=update_voice_assignment,
            inputs=[char_name_input, voice_path_input],
            outputs=[assign_status],
        )

        upload_btn.click(
            fn=upload_custom_voice,
            inputs=[voice_upload, upload_char_name],
            outputs=[upload_status],
        )

        generate_btn.click(
            fn=generate_output,
            outputs=[gen_status, sml_preview, sml_download, json_download],
        )

    return app


if __name__ == "__main__":
    app = create_app()
    app.launch(server_name="127.0.0.1", server_port=7860)
