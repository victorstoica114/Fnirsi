"""Extract the actual GSource implementation for a hardware-free C test."""
from pathlib import Path
import hashlib
import json

root = Path(__file__).resolve().parent.parent
source = root / "artifacts/la1010-reference-source/libsigrok/src/usb.c"
text = source.read_text(encoding="utf-8")
marker = "/**\n * Extract VID:PID or bus.addr from a connection string."
if marker not in text:
    raise SystemExit("USB source boundary changed; review the test extraction.")
output = root / "artifacts/la1010-reference-tests"
output.mkdir(parents=True, exist_ok=True)
header = output / "usb-source-under-test.h"
header.write_text(text.split(marker, 1)[0], encoding="utf-8")
(output / "source-provenance.json").write_text(json.dumps({
    "source": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "extracted_header": str(header),
    "header_sha256": hashlib.sha256(header.read_bytes()).hexdigest(),
    "scope": "real GLib GSource, mocked libusb polling, no hardware access",
}, indent=2) + "\n", encoding="utf-8")
print(header)
