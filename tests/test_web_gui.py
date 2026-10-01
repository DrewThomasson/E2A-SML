import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import web_gui


class GuiFlowTests(unittest.TestCase):
    def test_analysis_generates_sml_and_voice_change_regenerates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            voices = []
            for name in ('one', 'two'):
                path = root / 'voices' / 'eng' / 'adult' / 'female' / f'{name}.wav'
                path.parent.mkdir(parents=True, exist_ok=True)
                with wave.open(str(path), 'wb') as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(8000)
                    audio.writeframes(b'\0' * 160)
                voices.append(path)

            data = {
                'tokens': [
                    {'token_ID_within_document': str(i), 'paragraph_ID': '0', 'word': word}
                    for i, word in enumerate(['“', 'Hello', '”', 'he', 'said', '.'])
                ],
                'quotes': [{'quote_start': '0', 'quote_end': '2', 'char_id': '1'}],
                'book_data': {'characters': [
                    {'id': 1, 'mentions': {'proper': [{'n': 'Jeremiah'}]}}
                ]},
            }
            chars = [
                {'normalized_name': 'Narrator', 'inferred_gender': 'unknown'},
                {'normalized_name': 'Jeremiah', 'inferred_gender': 'male'},
            ]
            library = {'adult': {'female': [str(path) for path in voices], 'male': []}}
            web_gui._session_state.clear()
            with (
                patch.object(web_gui, 'configured_library_root', return_value=root),
                patch.object(web_gui, 'ensure_voice_library'),
                patch.object(web_gui, 'convert_ebook_to_txt', return_value='book.txt'),
                patch.object(web_gui, 'check_booknlp_installation', return_value=(True, 'ok')),
                patch.object(web_gui, 'run_booknlp', return_value={'book_id': 'book'}),
                patch.object(web_gui, 'load_booknlp_output', return_value=data),
                patch.object(web_gui, 'extract_characters', return_value=chars),
                patch.object(web_gui, 'scan_voice_library', return_value=library),
            ):
                result = web_gui.process_book('book.epub', 'big')

            self.assertEqual(len(result), 9)
            initial_file = Path(result[4])
            self.assertTrue(initial_file.is_file())
            self.assertIn('Hello', initial_file.read_text())
            self.assertEqual(result[8], web_gui.preview_voice(result[7]['value']))

            new_voice = str(voices[0])
            if web_gui._session_state['voice_assignments']['Jeremiah'] == new_voice:
                new_voice = str(voices[1])
            changed = web_gui.reassign_voice('Jeremiah', new_voice)
            self.assertEqual(len(changed), 5)
            regenerated = changed[2]
            self.assertNotEqual(initial_file, Path(regenerated))
            self.assertIn(f'[voice:{Path(new_voice).resolve()}]', Path(regenerated).read_text())


if __name__ == '__main__':
    unittest.main()
