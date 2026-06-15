"""FrenchBookNLP: self-contained French literary NLP pipeline.

Uses spaCy's ``fr_core_news_sm`` model (already bundled in the Docker image)
to provide the same output files as booknlp-plus's EnglishBookNLP:

  {book_id}.tokens
  {book_id}.quotes
  {book_id}.entities
  {book_id}.book
  {book_id}.book.txt
  {book_id}.characters_simple.json

Quote-detection logic is adapted from the open-source DEV_BOOKNLP_FR project
(lattice-8094/DEV_BOOKNLP_FR, v0.1.0, MIT / CC-BY-4.0) which detects the
dominant French quotation style (guillemets «», single guillemet ‹›,
typographic curly quotes, or em-dash dialogue) and segments accordingly.
"""

import json
import os
import re
from collections import Counter, defaultdict

# ---------------------------------------------------------------------------
# Speech-verb lemmas used by the quote-attribution heuristic
# ---------------------------------------------------------------------------
_SPEECH_VERB_LEMMAS = frozenset(
    {
        "dire",
        "répondre",
        "demander",
        "répliquer",
        "exclamer",
        "crier",
        "ajouter",
        "murmurer",
        "continuer",
        "chuchoter",
        "ordonner",
        "supplier",
        "déclarer",
        "protester",
        "marmonner",
        "affirmer",
        "annoncer",
        "commencer",
        "reprendre",
        "lancer",
        "grommeler",
        "souffler",
        "hurler",
        "prononcer",
        "observer",
        "remarquer",
        "expliquer",
        "préciser",
        "conclure",
        "interrompre",
        "intervenir",
        "soupirer",
        "bafouiller",
        "bredouiller",
        "sangloter",
    }
)

# French gendered pronouns used for character gender inference
_MALE_PRONOUNS = frozenset({"il", "lui", "son", "le", "celui"})
_FEMALE_PRONOUNS = frozenset({"elle", "sa", "la", "celle"})


def _write_json(data: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


class FrenchBookNLP:
    """French literary NLP pipeline backed by spaCy fr_core_news_sm.

    Drop-in replacement for ``BookNLP("fr", model_params)`` from booknlp-plus.
    Does not require any additional model downloads beyond the spaCy model that
    is already installed in the Docker image.

    Usage::

        nlp = FrenchBookNLP()
        nlp.process("/path/to/book.txt", "/path/to/output/", "my_book")
    """

    def __init__(self, model_params: dict | None = None) -> None:
        import spacy

        model_params = model_params or {}
        spacy_model = model_params.get("spacy_model", "fr_core_news_sm")
        self._nlp = spacy.load(spacy_model)
        # Allow processing full novels without hitting spaCy's default limit
        self._nlp.max_length = 10_000_000

    # ---------------------------------------------------------------------- #
    # Public interface                                                         #
    # ---------------------------------------------------------------------- #

    def process(self, input_file: str, output_dir: str, book_id: str) -> None:
        """Run the French NLP pipeline and write all BookNLP-format output files.

        Args:
            input_file:  Path to the plain-text input file.
            output_dir:  Directory for output files (created if absent).
            book_id:     Base name used for all output filenames.
        """
        os.makedirs(output_dir, exist_ok=True)

        with open(input_file, "r", encoding="utf-8") as fh:
            raw_text = fh.read()

        if not raw_text.strip():
            return

        # Single spaCy parse shared by all downstream steps
        doc = self._nlp(raw_text)

        tokens, spacy_to_doc_id = self._build_tokens(doc)
        entities = self._extract_entities(doc, spacy_to_doc_id)
        clusters, coref_id_to_name = self._cluster_entities(entities)
        quotes = self._detect_quotes(tokens)
        attributed = self._attribute_quotes(quotes, entities, clusters, tokens)
        genders = self._infer_genders(tokens, entities, clusters)

        self._write_tokens(tokens, output_dir, book_id)
        self._write_entities(entities, clusters, output_dir, book_id)
        self._write_quotes(
            quotes, attributed, entities, clusters, tokens, output_dir, book_id
        )
        book_data = self._build_book_data(
            tokens, entities, clusters, coref_id_to_name, genders
        )
        _write_json(book_data, os.path.join(output_dir, f"{book_id}.book"))
        self._write_book_txt(
            tokens, quotes, attributed, coref_id_to_name, output_dir, book_id
        )
        self._write_characters_simple(book_data, output_dir, book_id)

    # ---------------------------------------------------------------------- #
    # Step 1 – Tokenization                                                   #
    # ---------------------------------------------------------------------- #

    def _build_tokens(self, doc) -> tuple[list[dict], dict[int, int]]:
        """Parse the spaCy *doc* into a flat token record list.

        Whitespace tokens are skipped.  Paragraph boundaries are detected by
        the presence of ``\\n\\n`` in the whitespace between real tokens,
        matching the behaviour of DEV_BOOKNLP_FR's ``SpacyPipeline``.

        Returns:
            tokens: list of token dicts (one per non-whitespace token).
            spacy_to_doc_id: mapping from spaCy token index (``tok.i``) to the
                document-level sequential token ID used in all output files.
        """
        tokens: list[dict] = []
        # spaCy token.i → our sequential doc_token_id (whitespace skipped)
        spacy_to_doc_id: dict[int, int] = {}

        doc_token_id = 0
        sentence_id = 0
        paragraph_id = 0
        accumulated_ws = ""  # whitespace since last real token

        for sent in doc.sents:
            sent_token_id = 0
            has_real_token = False

            for tok in sent:
                if tok.is_space:
                    accumulated_ws += tok.text
                    continue

                # Detect paragraph break in accumulated whitespace
                if "\n\n" in accumulated_ws or "\r\n\r\n" in accumulated_ws:
                    paragraph_id += 1
                accumulated_ws = ""

                has_real_token = True
                spacy_to_doc_id[tok.i] = doc_token_id

                tokens.append(
                    {
                        "paragraph_ID": str(paragraph_id),
                        "sentence_ID": str(sentence_id),
                        "token_ID_within_sentence": str(sent_token_id),
                        "token_ID_within_document": str(doc_token_id),
                        "word": tok.text,
                        "lemma": tok.lemma_,
                        "byte_onset": str(tok.idx),
                        "byte_offset": str(tok.idx + len(tok.text)),
                        "POS_tag": tok.pos_,
                        "fine_POS_tag": tok.tag_,
                        "dependency_relation": tok.dep_,
                        # Store raw spaCy head index; resolved below
                        "_head_spacy_i": tok.head.i,
                        "_is_root": tok.dep_ in ("ROOT", "root"),
                        "syntactic_head_ID": "",
                        "event": "",
                    }
                )
                doc_token_id += 1
                sent_token_id += 1

            if has_real_token:
                sentence_id += 1

        # Resolve syntactic_head_ID now that the full map is built
        for rec in tokens:
            if rec.pop("_is_root"):
                rec["syntactic_head_ID"] = "-1"
            else:
                head_spacy_i = rec.pop("_head_spacy_i")
                rec["syntactic_head_ID"] = str(
                    spacy_to_doc_id.get(head_spacy_i, -1)
                )

        return tokens, spacy_to_doc_id

    # ---------------------------------------------------------------------- #
    # Step 2 – Entity extraction                                              #
    # ---------------------------------------------------------------------- #

    def _extract_entities(
        self, doc, spacy_to_doc_id: dict[int, int]
    ) -> list[tuple]:
        """Extract PER named-entity spans from the spaCy doc.

        Returns list of ``(start_doc_id, end_doc_id, prop_cat, text)`` tuples
        where *prop_cat* is ``"PROP_PER"`` (proper name) or ``"NOM_PER"``
        (common noun / lowercase start).
        """
        entities: list[tuple] = []

        for ent in doc.ents:
            if ent.label_ != "PER":
                continue

            # Find first and last non-whitespace token indices in the span
            first_idx = last_idx = None
            for tok in ent:
                if tok.is_space:
                    continue
                if first_idx is None:
                    first_idx = tok.i
                last_idx = tok.i

            if first_idx is None:
                continue

            start_doc = spacy_to_doc_id.get(first_idx)
            end_doc = spacy_to_doc_id.get(last_idx)

            if start_doc is None or end_doc is None or end_doc < start_doc:
                continue

            ent_text = ent.text.strip()
            prop_cat = "PROP_PER" if ent_text and ent_text[0].isupper() else "NOM_PER"
            entities.append((start_doc, end_doc, prop_cat, ent_text))

        return entities

    # ---------------------------------------------------------------------- #
    # Step 3 – Name coreference (entity clustering)                          #
    # ---------------------------------------------------------------------- #

    def _cluster_entities(
        self, entities: list[tuple]
    ) -> tuple[dict[int, int], dict[int, str]]:
        """Cluster entity mentions into character coreference chains.

        Uses title-cased name normalisation plus prefix/suffix overlap to link
        variants of the same name (e.g. "Marie" and "Marie Dupont").

        Returns:
            clusters:        entity_index → coref_id
            coref_id_to_name: coref_id → canonical name string
        """
        name_to_id: dict[str, int] = {}
        coref_id_to_name: dict[int, str] = {}
        next_id = 1  # 0 is reserved for the narrator

        def _normalise(name: str) -> str:
            return re.sub(r"[^\w\s]", "", name).strip().title()

        def _get_or_create(name: str) -> int:
            normed = _normalise(name)
            if not normed:
                return 0
            if normed in name_to_id:
                return name_to_id[normed]
            # Prefix/suffix overlap → same cluster
            for canonical, cid in list(name_to_id.items()):
                if canonical.startswith(normed) or normed.startswith(canonical):
                    name_to_id[normed] = cid
                    return cid
            nonlocal next_id
            cid = next_id
            next_id += 1
            name_to_id[normed] = cid
            coref_id_to_name[cid] = normed
            return cid

        # Narrator sentinel
        name_to_id["[NARRATOR]"] = 0
        coref_id_to_name[0] = "[NARRATOR]"

        clusters: dict[int, int] = {}
        for idx, (_s, _e, _p, text) in enumerate(entities):
            clusters[idx] = _get_or_create(text)

        return clusters, coref_id_to_name

    # ---------------------------------------------------------------------- #
    # Step 4 – Quote detection                                                #
    # ---------------------------------------------------------------------- #

    def _detect_quotes(self, tokens: list[dict]) -> list[tuple]:
        """Detect French quotation spans in the token stream.

        Determines the dominant quotation style in the text (guillemets «»,
        single guillemets ‹›, typographic curly quotes "", or em-dash — 
        paragraph-initial dialogue) then segments accordingly.

        Logic adapted from DEV_BOOKNLP_FR / QuoteTagger (lattice-8094,
        v0.1.0).

        Returns:
            Sorted list of ``(quote_start_doc_id, quote_end_doc_id)`` pairs.
        """
        style_votes: Counter = Counter()
        for rec in tokens:
            w = rec["word"]
            if w in ("«", "»"):
                style_votes["GUILLEMETS"] += 1
            elif w in ("‹", "›"):
                style_votes["GUILLEMET_SEUL"] += 1
            elif w in ("\u201c", "\u201d", "\u201e"):
                style_votes["CURLY_DOUBLE"] += 1
            elif w in ("—", "–"):
                style_votes["DASH"] += 1

        dominant = style_votes.most_common(1)[0][0] if style_votes else "GUILLEMETS"

        quotes: list[tuple] = []

        if dominant == "DASH":
            # Em-dash paragraph-initial dialogue: the whole paragraph that
            # begins with — is treated as a single speech span.
            current_para: str | None = None
            para_start_id: int | None = None
            para_end_id: int | None = None
            para_is_speech = False

            for rec in tokens:
                para_id = rec["paragraph_ID"]
                doc_id = int(rec["token_ID_within_document"])

                if para_id != current_para:
                    if para_is_speech and para_start_id is not None:
                        quotes.append((para_start_id, para_end_id))
                    current_para = para_id
                    para_start_id = doc_id
                    para_end_id = doc_id
                    para_is_speech = rec["word"] in ("—", "–", "--")
                else:
                    para_end_id = doc_id

            if para_is_speech and para_start_id is not None:
                quotes.append((para_start_id, para_end_id))

        else:
            open_tok, close_tok = {
                "GUILLEMETS": ("«", "»"),
                "GUILLEMET_SEUL": ("‹", "›"),
                "CURLY_DOUBLE": ("\u201c", "\u201d"),
            }[dominant]

            current_start: int | None = None
            current_words: list[str] = []
            last_para: str | None = None

            for rec in tokens:
                para_id = rec["paragraph_ID"]
                doc_id = int(rec["token_ID_within_document"])
                word = rec["word"]

                # Paragraph boundary closes any open quote
                if para_id != last_para and last_para is not None:
                    if current_start is not None and current_words:
                        quotes.append((current_start, doc_id - 1))
                    current_start = None
                    current_words = []

                if word == open_tok:
                    current_start = doc_id
                    current_words = []
                elif word == close_tok and current_start is not None:
                    if current_words:
                        quotes.append((current_start, doc_id))
                    current_start = None
                    current_words = []
                elif current_start is not None:
                    current_words.append(word)

                last_para = para_id

        return sorted(set(quotes))

    # ---------------------------------------------------------------------- #
    # Step 5 – Quote attribution                                              #
    # ---------------------------------------------------------------------- #

    def _attribute_quotes(
        self,
        quotes: list[tuple],
        entities: list[tuple],
        clusters: dict[int, int],
        tokens: list[dict],
    ) -> list[int | None]:
        """Attribute each quote span to a character coref_id.

        Heuristic: look for a PER entity adjacent to a speech verb within a
        window of tokens after the quote end (the most common French pattern:
        «…» dit Jean.), then before the quote start as fallback.
        """
        if not entities:
            return [None] * len(quotes)

        # doc_token_id → coref_id for all entity tokens
        doc_id_to_coref: dict[int, int] = {}
        for idx, (start, end, _p, _t) in enumerate(entities):
            cid = clusters[idx]
            for tid in range(start, end + 1):
                doc_id_to_coref[tid] = cid

        lemma_by_id = {
            int(r["token_ID_within_document"]): r["lemma"].lower()
            for r in tokens
        }

        max_doc_id = max(doc_id_to_coref.keys(), default=0)
        window = 25
        attributed: list[int | None] = []

        for q_start, q_end in quotes:
            speaker: int | None = None

            # Search AFTER the quote (« text » dit Jean)
            after_end = min(q_end + window, max_doc_id)
            for tid in range(q_end + 1, after_end + 1):
                if tid in doc_id_to_coref:
                    speaker = doc_id_to_coref[tid]
                    break
                if lemma_by_id.get(tid, "") in _SPEECH_VERB_LEMMAS:
                    for tid2 in range(tid + 1, min(tid + 8, after_end + 1)):
                        if tid2 in doc_id_to_coref:
                            speaker = doc_id_to_coref[tid2]
                            break
                    if speaker is not None:
                        break

            # Search BEFORE the quote as fallback
            if speaker is None:
                before_start = max(0, q_start - window)
                for tid in range(q_start - 1, before_start - 1, -1):
                    if tid in doc_id_to_coref:
                        speaker = doc_id_to_coref[tid]
                        break
                    if lemma_by_id.get(tid, "") in _SPEECH_VERB_LEMMAS:
                        for tid2 in range(
                            tid - 1, max(before_start - 1, tid - 8), -1
                        ):
                            if tid2 in doc_id_to_coref:
                                speaker = doc_id_to_coref[tid2]
                                break
                        if speaker is not None:
                            break

            attributed.append(speaker)

        return attributed

    # ---------------------------------------------------------------------- #
    # Step 6 – Gender inference                                               #
    # ---------------------------------------------------------------------- #

    def _infer_genders(
        self,
        tokens: list[dict],
        entities: list[tuple],
        clusters: dict[int, int],
    ) -> dict[int, dict]:
        """Infer character gender from French pronoun evidence.

        For each entity mention, look at the *window* tokens that follow it and
        tally masculine (il/lui/son/le…) vs. feminine (elle/sa/la…) pronouns.
        """
        male_votes: Counter = Counter()
        female_votes: Counter = Counter()
        window = 15

        for idx, (start, end, _p, _t) in enumerate(entities):
            cid = clusters[idx]
            window_slice = tokens[end + 1: end + 1 + window]
            for rec in window_slice:
                w = rec["word"].lower().rstrip("'")
                if w in _MALE_PRONOUNS:
                    male_votes[cid] += 1
                elif w in _FEMALE_PRONOUNS:
                    female_votes[cid] += 1

        genders: dict[int, dict] = {}
        all_cids = set(clusters.values()) - {0}
        for cid in all_cids:
            genders[cid] = {
                "il/lui": male_votes[cid],
                "elle/la": female_votes[cid],
            }
        return genders

    # ---------------------------------------------------------------------- #
    # Output writers                                                           #
    # ---------------------------------------------------------------------- #

    def _write_tokens(
        self, tokens: list[dict], output_dir: str, book_id: str
    ) -> None:
        header = "\t".join(
            [
                "paragraph_ID",
                "sentence_ID",
                "token_ID_within_sentence",
                "token_ID_within_document",
                "word",
                "lemma",
                "byte_onset",
                "byte_offset",
                "POS_tag",
                "fine_POS_tag",
                "dependency_relation",
                "syntactic_head_ID",
                "event",
            ]
        )
        path = os.path.join(output_dir, f"{book_id}.tokens")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(header + "\n")
            for rec in tokens:
                fh.write(
                    "\t".join(
                        [
                            rec["paragraph_ID"],
                            rec["sentence_ID"],
                            rec["token_ID_within_sentence"],
                            rec["token_ID_within_document"],
                            rec["word"],
                            rec["lemma"],
                            rec["byte_onset"],
                            rec["byte_offset"],
                            rec["POS_tag"],
                            rec["fine_POS_tag"],
                            rec["dependency_relation"],
                            rec["syntactic_head_ID"],
                            rec["event"],
                        ]
                    )
                    + "\n"
                )

    def _write_entities(
        self,
        entities: list[tuple],
        clusters: dict[int, int],
        output_dir: str,
        book_id: str,
    ) -> None:
        path = os.path.join(output_dir, f"{book_id}.entities")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("COREF\tstart_token\tend_token\tprop\tcat\ttext\n")
            for idx, (start, end, prop_cat, text) in enumerate(entities):
                cid = clusters.get(idx, -1)
                parts = prop_cat.split("_", 1)
                prop = parts[0]
                cat = parts[1] if len(parts) > 1 else "PER"
                fh.write(f"{cid}\t{start}\t{end}\t{prop}\t{cat}\t{text}\n")

    def _write_quotes(
        self,
        quotes: list[tuple],
        attributed: list[int | None],
        entities: list[tuple],
        clusters: dict[int, int],
        tokens: list[dict],
        output_dir: str,
        book_id: str,
    ) -> None:
        word_by_id = {
            int(r["token_ID_within_document"]): r["word"] for r in tokens
        }
        # coref_id → first (mention_start, mention_end, mention_phrase)
        coref_to_mention: dict[int, tuple] = {}
        for idx, (start, end, _p, text) in enumerate(entities):
            cid = clusters[idx]
            if cid not in coref_to_mention:
                coref_to_mention[cid] = (start, end, text)

        path = os.path.join(output_dir, f"{book_id}.quotes")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                "quote_start\tquote_end\tmention_start\tmention_end\t"
                "mention_phrase\tchar_id\tquote\n"
            )
            for (q_start, q_end), char_id in zip(quotes, attributed):
                words = [
                    word_by_id.get(tid, "") for tid in range(q_start, q_end + 1)
                ]
                quote_text = " ".join(words)
                if char_id is not None and char_id in coref_to_mention:
                    m_start, m_end, m_phrase = coref_to_mention[char_id]
                else:
                    m_start = m_end = m_phrase = None
                fh.write(
                    f"{q_start}\t{q_end}\t{m_start}\t{m_end}\t"
                    f"{m_phrase}\t{char_id}\t{quote_text}\n"
                )

    def _build_book_data(
        self,
        tokens: list[dict],
        entities: list[tuple],
        clusters: dict[int, int],
        coref_id_to_name: dict[int, str],
        genders: dict[int, dict],
    ) -> dict:
        """Build the character JSON structure that matches EnglishBookNLP."""
        char_proper: dict[int, Counter] = defaultdict(Counter)
        char_common: dict[int, Counter] = defaultdict(Counter)
        char_pron: dict[int, Counter] = defaultdict(Counter)
        char_count: Counter = Counter()
        agents: dict[int, list] = defaultdict(list)
        patients: dict[int, list] = defaultdict(list)

        doc_id_to_lemma = {
            int(r["token_ID_within_document"]): r["lemma"] for r in tokens
        }
        doc_id_to_pos = {
            int(r["token_ID_within_document"]): r["POS_tag"] for r in tokens
        }
        doc_id_to_dep = {
            int(r["token_ID_within_document"]): r["dependency_relation"]
            for r in tokens
        }
        doc_id_to_head = {
            int(r["token_ID_within_document"]): int(r["syntactic_head_ID"])
            for r in tokens
            if r["syntactic_head_ID"] not in ("-1", "")
        }

        for idx, (start, end, prop_cat, text) in enumerate(entities):
            cid = clusters[idx]
            if cid == 0:
                continue  # narrator
            prop = prop_cat.split("_")[0]
            normed = re.sub(r"[^\w\s]", "", text).strip().title()
            if not normed:
                continue

            char_count[cid] += 1
            if prop == "PROP":
                char_proper[cid][normed] += 1
            elif prop == "PRON":
                char_pron[cid][text.lower()] += 1
            else:
                char_common[cid][normed] += 1

            # Simple agent/patient from dependency labels
            head_id = doc_id_to_head.get(start)
            if head_id is not None:
                dep = doc_id_to_dep.get(start, "")
                head_pos = doc_id_to_pos.get(head_id, "")
                head_lemma = doc_id_to_lemma.get(head_id, "")
                if dep == "nsubj" and head_pos == "VERB":
                    agents[cid].append({"w": head_lemma, "i": head_id})
                elif dep in ("obj", "nsubj:pass") and head_pos == "VERB":
                    patients[cid].append({"w": head_lemma, "i": head_id})

        characters = []
        for cid, total in char_count.most_common():
            if total < 2:
                continue
            characters.append(
                {
                    "id": cid,
                    "count": total,
                    "mentions": {
                        "proper": [
                            {"n": n, "c": c}
                            for n, c in char_proper[cid].most_common()
                        ],
                        "common": [
                            {"n": n, "c": c}
                            for n, c in char_common[cid].most_common()
                        ],
                        "pronoun": [
                            {"n": n, "c": c}
                            for n, c in char_pron[cid].most_common()
                        ],
                    },
                    "g": genders.get(cid),
                    "agent": agents[cid],
                    "patient": patients[cid],
                    "mod": [],
                    "poss": [],
                }
            )

        return {"characters": characters}

    def _write_book_txt(
        self,
        tokens: list[dict],
        quotes: list[tuple],
        attributed: list[int | None],
        coref_id_to_name: dict[int, str],
        output_dir: str,
        book_id: str,
    ) -> None:
        """Write multi-speaker .book.txt using ``[CharName] text [/]`` format."""
        tok_to_char: dict[int, str] = {}
        for (q_start, q_end), cid in zip(quotes, attributed):
            if cid is not None:
                raw = coref_id_to_name.get(cid, f"Character{cid}")
                name = re.sub(r"[^a-zA-Z0-9]", "", raw) if raw != "[NARRATOR]" else "Narrator"
                for tid in range(q_start, q_end + 1):
                    tok_to_char[tid] = name

        lines: list[str] = []
        current_char: str | None = None
        current_words: list[str] = []
        last_para: str | None = None

        for rec in tokens:
            tid = int(rec["token_ID_within_document"])
            para_id = rec["paragraph_ID"]
            char = tok_to_char.get(tid, "Narrator")

            # Flush segment on paragraph change
            if last_para is not None and para_id != last_para:
                if current_words and current_char is not None:
                    lines.append(f"[{current_char}] {' '.join(current_words)} [/]")
                current_words = []
                current_char = None

            if char != current_char:
                if current_words and current_char is not None:
                    lines.append(f"[{current_char}] {' '.join(current_words)} [/]")
                current_words = []
                current_char = char

            current_words.append(rec["word"])
            last_para = para_id

        if current_words and current_char is not None:
            lines.append(f"[{current_char}] {' '.join(current_words)} [/]")

        path = os.path.join(output_dir, f"{book_id}.book.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))

    def _write_characters_simple(
        self, book_data: dict, output_dir: str, book_id: str
    ) -> None:
        """Write characters_simple.json for ebook2audiobook compatibility."""
        characters: list[dict] = [
            {
                "normalized_name": "Narrator",
                "inferred_gender": "unknown",
                "inferred_age_category": "unknown",
                "tts_engine": "XTTSv2",
                "language": "fra",
                "voice": None,
            }
        ]

        for char in book_data.get("characters", []):
            proper = char.get("mentions", {}).get("proper", [])
            if not proper:
                continue
            raw_name = proper[0].get("n", "")
            normalized = "".join(
                w.capitalize()
                for w in re.sub(r"[^a-zA-Z0-9\s]", "", raw_name).strip().split()
            )
            if not normalized:
                continue

            gender_data = char.get("g") or {}
            male_count = gender_data.get("il/lui", 0)
            female_count = gender_data.get("elle/la", 0)
            gender = "male" if male_count > female_count else ("female" if female_count > male_count else "unknown")

            characters.append(
                {
                    "normalized_name": normalized,
                    "inferred_gender": gender,
                    "inferred_age_category": "adult",
                    "tts_engine": "XTTSv2",
                    "language": "fra",
                    "voice": None,
                }
            )

        path = os.path.join(output_dir, f"{book_id}.characters_simple.json")
        _write_json({"characters": characters}, path)
