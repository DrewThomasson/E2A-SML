# E2A-SML: character voices for ebook2audiobook

E2A-SML uses BookNLP to find dialogue and speakers in an **English** book, lets you assign voices, and creates an SML text file for [ebook2audiobook](https://github.com/DrewThomasson/ebook2audiobook). It runs separately from E2A.

![E2A SML v26.10.1.2 GUI showing the character selector, voice selector, and audio preview](assets/web_gui.png)

## Quick start with Docker

Clone and run E2A-SML on its own:

```bash
git clone https://github.com/DrewThomasson/E2A-SML.git
cd E2A-SML
docker compose up --build
```

Open **http://localhost:7861**. The first start downloads the voice library into `data/`; later starts reuse it. BookNLP models also stay in `data/`. The GUI lets you download the finished SML file from your browser. You may clone this repo into E2A's `components/` folder, but E2A is not required to run the tool.

## Local installation

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and use a separate Python 3.10 environment:

```bash
cd E2A-SML
uv venv --python 3.10 .venv
source .venv/bin/activate
uv pip install -r requirements.txt
uv pip install "$(python -m spacy info en_core_web_sm --url)"
python cli.py --gui
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` and install the spaCy model with `uv pip install (python -m spacy info en_core_web_sm --url)`. Native runs download voices into `data/voices/` when needed and store BookNLP models in `data/models/`. Neither the GUI nor CLI needs an Ebook2audiobook path. Install [Calibre](https://calibre-ebook.com/download) to read formats other than `.txt` locally, and make sure `ebook-convert` is on your PATH.

## Make an audiobook with E2A

1. In **Book & Download**, upload an English book and select **Create SML file**. Download the generated `<book>.e2a.sml.txt` file.
2. In **Characters & Voices**, select a character and choose a voice. The preview plays the selected voice, and the SML download updates automatically when you change the voice.
3. Give that file to E2A as the book input. Library voice tags use paths such as `voices/eng/adult/female/Voice.wav`, relative to E2A's working directory. Run E2A from its project directory and ensure the selected voices exist in its `voices/` folder. Custom voices outside E2A-SML's library retain absolute paths and must be accessible to E2A at those paths.

E2A-SML produces one SML file for E2A. Its voice tags use paths to the assigned voice files.
Each voice tag is closed on the same text line so Calibre can split chapters without breaking E2A's SML parser.

For E2A's macOS command-line launcher, include `--headless` when passing options:

```bash
cd /path/to/ebook2audiobook
./ebook2audiobook.command --headless --tts_engine piper --ebook "/path/to/book.e2a.sml.txt"
```

## Command line

With the local environment activated, run from the E2A-SML folder:

```bash
python cli.py /path/to/book.txt -o output/
```

Use `python cli.py --help` for model size, custom voice folders, and other options. Book analysis supports English only; `--language` changes the voice-library language, not BookNLP's analysis language. For non-`.txt` books, install Calibre locally or use Docker.
