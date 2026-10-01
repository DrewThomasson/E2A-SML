import unittest
from pathlib import Path

from sml_extractor.sml_generator import (
    _generate_from_book_txt, _generate_from_tokens,
    portable_voice_assignments, speaking_characters,
)


class VoiceTransitionTests(unittest.TestCase):
    def test_speaking_characters_include_narrator_and_attributed_speakers(self):
        data = {
            'book_data': {'characters': [
                {'id': 167, 'mentions': {'proper': [{'n': 'Jeremiah'}]}},
                {'id': 3, 'mentions': {'proper': []}},
            ]},
            'quotes': [{'char_id': '167'}, {'char_id': '3'}],
        }
        characters = [
            {'normalized_name': 'Narrator'},
            {'normalized_name': 'Jeremiah'},
            {'normalized_name': 'Unused'},
        ]
        names = [c['normalized_name'] for c in speaking_characters(data, characters)]
        self.assertEqual(names, ['Narrator', 'Jeremiah', 'Character3'])

    def test_voice_paths_are_absolute_for_e2a(self):
        paths = portable_voice_assignments(
            {'Jeremiah': 'voices/eng/adult/male/voice.wav'}, '/tmp/e2a-sml/data'
        )
        expected = Path('/tmp/e2a-sml/data/voices/eng/adult/male/voice.wav').resolve()
        self.assertEqual(paths['Jeremiah'], str(expected))

    def test_dialogue_narration_dialogue_uses_correct_voices(self):
        words = ['“', 'Hello', '”', 'he', 'said', '.', '“', 'Again', '”']
        tokens = [
            {'token_ID_within_document': str(i), 'paragraph_ID': '0', 'word': word}
            for i, word in enumerate(words)
        ]
        quotes = [
            {'quote_start': '0', 'quote_end': '2', 'char_id': '167'},
            {'quote_start': '6', 'quote_end': '8', 'char_id': '167'},
        ]
        book_data = {'characters': [
            {'id': 167, 'mentions': {'proper': [{'n': 'Jeremiah'}]}}
        ]}
        output = _generate_from_tokens(
            tokens, quotes, book_data,
            {'Narrator': 'narrator.wav', 'Jeremiah': 'jeremiah.wav'},
            use_macros=False,
        )
        self.assertEqual(output, (
            '[voice:jeremiah.wav]“Hello”[/voice]\n'
            '[voice:narrator.wav]he said.[/voice]\n'
            '[voice:jeremiah.wav]“Again”[/voice]'
        ))

    def test_unassigned_narrator_does_not_inherit_previous_voice(self):
        output = _generate_from_book_txt(
            '[Jeremiah] Hello [/]\n[Narrator] he said [/]\n[Jeremiah] Again [/]',
            {'Jeremiah': 'jeremiah.wav'},
            use_macros=False,
        )
        self.assertEqual(output, (
            '[voice:jeremiah.wav]Hello[/voice]\n'
            'he said\n[voice:jeremiah.wav]Again[/voice]'
        ))

    def test_all_caps_line_keeps_voice_tags_together_for_calibre(self):
        output = _generate_from_book_txt(
            '[Narrator] before [/]\n[Narrator] PRINCESS MATTERS [/]\n'
            '[Narrator] after [/]',
            {'Narrator': 'narrator.wav'},
            use_macros=False,
        )
        for line in output.splitlines():
            self.assertEqual(line.count('[voice:'), line.count('[/voice]'))
        self.assertIn('[voice:narrator.wav]PRINCESS MATTERS[/voice]', output)


if __name__ == '__main__':
    unittest.main()
