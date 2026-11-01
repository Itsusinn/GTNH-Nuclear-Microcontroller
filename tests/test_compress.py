from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from lupa.lua52 import LuaRuntime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import compress


class CompressionTests(unittest.TestCase):
    def test_keyword_encoding_is_lossless(self):
        source = "local sender='function return then local end \\\\ \\' 中文';return sender"
        lua = LuaRuntime()
        lua.execute("load=function(code) decoded=code;return function() end end")
        lua.execute(compress.encode(source))
        self.assertEqual(lua.globals().decoded, source)

    def test_marker_collisions_are_rejected(self):
        for marker in ("!", "?", "&", "@", "$"):
            with self.subTest(marker=marker), self.assertRaises(ValueError):
                compress.encode(f'return "{marker}"')

    def test_build_failures_preserve_firmware(self):
        results = [
            subprocess.CalledProcessError(1, "luamin"),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess([], 0, stdout='return "' + "中" * 1400 + '"', stderr=""),
            subprocess.CompletedProcess([], 0, stdout='return "@"', stderr=""),
        ]
        for result in results:
            with self.subTest(result=result), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "source.lua"
                destination = Path(directory) / "nuclear.lua"
                source.write_text("return 1", encoding="utf-8")
                destination.write_bytes(b"previous firmware")
                options = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
                with patch("compress.subprocess.run", **options), self.assertRaises((ValueError, subprocess.CalledProcessError)):
                    compress.build(source=source, destination=destination)
                self.assertEqual(destination.read_bytes(), b"previous firmware")
                self.assertEqual(len(list(Path(directory).iterdir())), 2)

    def test_successful_build_writes_utf8_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.lua"
            destination = Path(directory) / "nuclear.lua"
            source.write_text("return 1", encoding="utf-8")
            minified = 'return "中文"'
            with patch("compress.subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout=minified)):
                size = compress.build(source=source, destination=destination)
            self.assertEqual(destination.read_bytes(), compress.encode(minified).encode("utf-8"))
            self.assertEqual(size, destination.stat().st_size)


if __name__ == "__main__":
    unittest.main()
