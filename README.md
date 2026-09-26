# E2A-SML: character voices for ebook2audiobook

E2A-SML uses BookNLP to find dialogue and speakers in an **English** book, lets you assign voices, and creates an SML text file for [ebook2audiobook](https://github.com/DrewThomasson/ebook2audiobook). It runs separately from E2A.

![E2A-SML web GUI showing book upload and analysis options](assets/web_gui.png)

## Quick start with Docker

Clone this repo into an E2A checkout, or clone it elsewhere if you prefer:

```bash
cd /path/to/ebook2audiobook/components
git clone https://github.com/DrewThomasson/E2A-SML.git
cd E2A-SML
docker compose up --build
```

Open **http://localhost:7861**. The first start downloads the voice library into `data/`; later starts reuse it. The Docker container has its own voices and BookNLP models. The GUI lets you download the finished SML file from your browser.

## Local installation

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and use a separate Python 3.10 environment:

```bash
cd /path/to/ebook2audiobook/components/E2A-SML
uv venv --python 3.10 .venv
source .venv/bin/activate
uv pip install -r requirements.txt
uv pip install "$(python -m spacy info en_core_web_sm --url)"
python cli.py --gui
```

On Windows, activate with `.venv\Scripts\activate` instead. When cloned into E2A's `components/` folder, the GUI finds the E2A voice library automatically. If cloned elsewhere, enter your E2A checkout path in the GUI. Install [Calibre](https://calibre-ebook.com/download) to read formats other than `.txt` locally.

## Make an audiobook with E2A

1. In **Process Book**, upload an English book and select **Analyze Book**.
2. In **Characters & Voices**, review or change the assigned voices.
3. In **Preview & Generate**, select **Generate SML Output** and download the **E2A-ready SML** file named `<book>.e2a.sml.txt`.
4. Give that file to E2A as the book input. Its `voices/...` paths must refer to files in E2A's voice library. Run native E2A from its repository root; if you use separate containers, copy or mount the matching voices into E2A.

E2A-SML produces one SML file for E2A. Its voice tags use paths to the assigned voice files.

## Command line

With the local environment activated, run from the E2A-SML folder:

```bash
python cli.py /path/to/book.txt --e2a-path /path/to/ebook2audiobook -o output/
```

Use `python cli.py --help` for model size, custom voice folders, and other options. Book analysis supports English only; `--language` changes the voice-library language, not BookNLP's analysis language. For non-`.txt` books, install Calibre locally or use Docker.
