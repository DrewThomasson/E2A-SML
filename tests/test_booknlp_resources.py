import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import booknlp.english

from sml_extractor.core import check_booknlp_installation


class BookNLPResourceTests(unittest.TestCase):
    def test_bundled_resources_are_present(self):
        resource_dir = Path(booknlp.english.__file__).parent / "data"
        for name in (
            "aliases.txt",
            "entity_cat.tagset",
            "gutenberg_prop_gender_terms.txt",
            "supersense.tagset",
            "wordnet.first.sense",
        ):
            with self.subTest(name=name):
                self.assertTrue((resource_dir / name).is_file())

    def test_missing_resources_get_clear_error(self):
        with tempfile.TemporaryDirectory() as root:
            fake_package = Path(root) / "__init__.py"
            with patch.object(booknlp.english, "__file__", str(fake_package)):
                ok, message = check_booknlp_installation()

        self.assertFalse(ok)
        self.assertIn("entity_cat.tagset", message)
        self.assertIn("complete checkout", message)


if __name__ == "__main__":
    unittest.main()
