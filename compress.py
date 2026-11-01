"""Build the EEPROM image without overwriting it on a failed build."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

BASE_DIR = Path(__file__).resolve().parent
EEPROM_SIZE = 4096


def encode(minified):
    # The Lua decoder also replaces markers inside strings. Reject collisions
    # rather than silently changing literals or Lua 5.3 operators.
    if any(marker in minified for marker in ("!", "?", "&", "@", "$")):
        raise ValueError("Source contains reserved compression markers: !, ?, &, @ or $")
    replaced = minified
    for old, new in (("function", "!"), ("return", "?"), ("then ", "$"),
                     ("local ", "&"), ("end", "@")):
        replaced = replaced.replace(old, new)
    escaped = (replaced.replace("\\", "\\\\").replace("'", "\\'")
               .replace("\r", "\\r").replace("\n", "\\n"))
    return (f"load(('{escaped}'):gsub(\"!\",\"function\")"
            ':gsub("%?","return"):gsub("%$","then ")'
            ':gsub("&","local "):gsub("@","end"))()')


def build(luamin="luamin", source=None, destination=None):
    source = Path(source) if source is not None else BASE_DIR / "nuclear_SourceCode.lua"
    destination = Path(destination) if destination is not None else BASE_DIR / "nuclear.lua"
    executable = shutil.which(luamin) or luamin
    # luamin's file mode treats piped stdin as another filename, even if empty.
    result = subprocess.run([executable, "-c"], input=source.read_text(encoding="utf-8"),
                            capture_output=True, text=True, encoding="utf-8", check=True)
    minified = result.stdout.rstrip("\r\n")
    if not minified:
        raise ValueError("luamin produced no code")
    code = encode(minified).encode("utf-8")
    if len(code) > EEPROM_SIZE:
        raise ValueError(f"EEPROM image is {len(code)} bytes; limit is {EEPROM_SIZE}")

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
            temporary = output.name
            output.write(code)
        os.replace(temporary, destination)
    finally:
        if temporary and os.path.exists(temporary):
            os.remove(temporary)
    return len(code)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--luamin", default="luamin", help="Path to the luamin executable")
    args = parser.parse_args()
    try:
        size = build(args.luamin)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or getattr(error, "stdout", "") or ""
        parser.exit(1, f"Build failed: {error}\n{detail}")
    print(f"Built nuclear.lua: {size}/{EEPROM_SIZE} bytes")
