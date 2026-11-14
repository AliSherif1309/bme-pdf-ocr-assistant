import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dependency_paths import find_tool, poppler_directory


class DependencyTests(unittest.TestCase):
    def test_explicit_path_takes_precedence(self):
        with patch.dict(os.environ, {'TESSERACT_PATH': '/chosen/tesseract'}):
            self.assertEqual(find_tool('TESSERACT_PATH', ['/bundled'], ['tesseract']), str(Path('/chosen/tesseract')))

    def test_path_installation_fallback(self):
        with patch.dict(os.environ, {}, clear=True), patch('shutil.which', return_value='/usr/bin/gs'):
            self.assertEqual(find_tool('GHOSTSCRIPT_PATH', ['/missing'], ['gs']), '/usr/bin/gs')

    def test_bundled_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            bundled = Path(folder) / 'tool.exe'
            bundled.touch()
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(find_tool('TOOL_PATH', [bundled], ['tool']), str(bundled))

    def test_poppler_override(self):
        with patch.dict(os.environ, {'POPPLER_PATH': '/opt/poppler/bin'}):
            self.assertEqual(poppler_directory('/app'), '/opt/poppler/bin')


if __name__ == '__main__':
    unittest.main()
