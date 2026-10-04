"""Read-only, fixed-evidence A07/V5 command comparison; no device APIs.

Writes one new report with exclusive creation when --output is supplied.
The command log records attempted writes, not independently sniffed USB traffic.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re


def sha(data):
    return hashlib.sha256(data).hexdigest()


def crc(body):
    value = 0
    for byte in body:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xEDB88320 if value & 1 else 0)
    return (~value) & 0xFFFFFFFF


def source_function(text, name):
    match = re.search(r"(?m)^static[^;]*?\b" + name + r"\s*\([^;]*?\)\s*\{", text)
    if not match:
        raise ValueError("Missing function " + name)
    position = text.index("{", match.start())
    depth, state = 0, "code"
    while position < len(text):
        char = text[position]
        pair = text[position:position + 2]
        if state == "line":
            if char == "\n":
                state = "code"
        elif state == "comment":
            if pair == "*/":
                state = "code"
                position += 1
        elif state in ('"', "'"):
            if char == "\\":
                position += 1
            elif char == state:
                state = "code"
        elif pair == "//":
            state = "line"
            position += 1
        elif pair == "/*":
            state = "comment"
            position += 1
        elif char in ('"', "'"):
            state = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[match.start():position + 1]
        position += 1
    raise ValueError("Unclosed function " + name)


def decode_command(opcode, prefix):
    packet = bytes.fromhex(prefix)
    if len(packet) != 54 or packet[8] != 10 or packet[9] != int(opcode, 16):
        raise ValueError("Malformed command prefix")
    body_length = int.from_bytes(packet[10:12], "little") + 1
    body = packet[9:9 + body_length]
    terminator = 9 + body_length
    if packet[terminator] != 11 or int.from_bytes(packet[terminator + 1:terminator + 5], "little") != crc(body):
        raise ValueError("Command framing/CRC mismatch")
    if any(packet[:8]) or any(packet[terminator + 5:]):
        raise ValueError("Nonzero observed padding")
    result = {"opcode": opcode, "prefix54": prefix, "body_hex": body.hex(), "CRC_verified": True}
    if opcode == "11":
        result["fields"] = {
            "rle": body[3], "buffer": body[4], "mode": body[5],
            "interval_ms": int.from_bytes(body[6:8], "little"),
            "samplerate_hz": int.from_bytes(body[8:12], "little"), "samplerate_index": body[12],
            "threshold_centivolts": int.from_bytes(body[13:15], "little"), "threshold_range": body[15],
            "duration_ms": int.from_bytes(body[16:18], "little"), "duration_index": body[18],
            "trigger_position_raw": int.from_bytes(body[19:23], "little"),
            "configured_samples": int.from_bytes(body[23:31], "little")}
    elif opcode == "12":
        result["fields"] = {"instantly": body[3], "physical_mask_hex": f"{int.from_bytes(body[4:8], 'little'):08x}",
                            "conditions_32": list(body[8:40])}
    return result


def capture(path, root):
    data = path.read_bytes()
    text = data.decode("utf-8-sig")
    commands = [decode_command(*match.groups()) for match in re.finditer(
        r"WIRE command: opcode ([0-9a-f]+), elapsed_us \d+, prefix54 ([0-9a-f]+)\.", text)]
    reads = []
    for match in re.finditer(r"WIRE read: phase ([\w-]+), start_us (\d+), duration_us (\d+), status (-?\d+), requested (\d+), timeout_ms (\d+), count (\d+),", text):
        phase, *values = match.groups()
        reads.append(dict(zip(("phase", "start_us", "duration_us", "status", "requested", "timeout_ms", "count"), (phase, *map(int, values)))))
    header = text.index("Received SR_DF_HEADER")
    setup = text.index("WIRE command: opcode 11")
    arm = text.index("WIRE command: opcode 12")
    return {"path": str(path.relative_to(root)).replace("\\", "/"), "sha256": sha(data),
            "mode": "Buffer" if "-Buffer.driver.log" in path.name else "Stream",
            "commands": commands, "commands_order": [item["opcode"] for item in commands],
            "SETUP_then_HEADER_then_ARM": setup < header < arm,
            "reads": reads, "level1_errors": re.findall(r"(?m)^.*level=1 .*$", text),
            "accepted_STOP_markers": re.findall(r"WCH capture STOP complete: phase ([\w-]+); command accepted\.", text)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    sources = {"A07": root / "artifacts/dla32-wch-timeout-source/libsigrok/src/hardware/fnirsi-dla32",
               "V5": root / "artifacts/dla32-wch-stop-v5-source/libsigrok/src/hardware/fnirsi-dla32"}
    blocks = {"A07": root / "captures/wch-timeout-2026-10-04-r1/A07-mixed-1",
              "V5": root / "captures/wch-stop-v5-sdk0-2026-10-04-r1/D-mixed-1"}
    functions = {"api.c": ("receive_events", "check_data_progress", "note_data_progress", "decode_transfer", "send_command",
                            "read_samples_sync", "configure_setup", "wait_before_arm", "finish_acquisition", "request_stop", "prepare_transfers"),
                 "transport-wch.h": ("dla_wch_operation_timeout", "dla_wch_write", "dla_wch_read", "dla_wch_open", "dla_wch_close", "dla_wch_model32")}
    source_hashes, comparisons = {}, []
    for file in ("api.c", "protocol.h", "transport-wch.h"):
        contents = {key: (folder / file).read_bytes() for key, folder in sources.items()}
        source_hashes[file] = {key: sha(data) for key, data in contents.items()}
        for name in functions.get(file, ()):
            definitions = {key: source_function(data.decode(), name) for key, data in contents.items()}
            comparisons.append({"file": file, "function": name, "identical": definitions["A07"] == definitions["V5"],
                                "sha256": {key: sha(text.encode()) for key, text in definitions.items()}})
    captures = {key: [capture(path, root) for path in sorted(block.glob("*.driver.log"))] for key, block in blocks.items()}
    if len(captures["A07"]) != 10 or len(captures["V5"]) != 3:
        raise ValueError("Frozen comparison matrix differs")
    references = {mode: next(item for item in captures["A07"] if item["mode"] == mode) for mode in ("Buffer", "Stream")}
    commands_equal = all(item["commands"] == references[item["mode"]]["commands"] for group in captures.values() for item in group)
    gates = {"all17_compared_functions_identical": all(item["identical"] for item in comparisons),
             "protocol_and_decoder_file_identical": source_hashes["protocol.h"]["A07"] == source_hashes["protocol.h"]["V5"],
             "all13_mode_matched_command_prefixes_identical": commands_equal,
             "all13_command_orders_STOP_SETUP_ARM_STOP": all(item["commands_order"] == ["15", "11", "12", "15"] for group in captures.values() for item in group),
             "all13_SETUP_HEADER_ARM_ordered": all(item["SETUP_then_HEADER_then_ARM"] for group in captures.values() for item in group)}
    result = {"created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "tool_sha256": sha(Path(__file__).read_bytes()),
              "hardware_access": False, "source_sha256": source_hashes, "function_comparisons": comparisons, "captures": captures, "comparison_gates": gates,
              "finding": "V5 SDK0 preserves the A07 success-path command bodies/order, decoder, read loop, stall criterion and direct WCH timeout/read/write helpers. The failed second Buffer has the same logged configuration as successful cases. V5 STOP ownership/configuration guards and accepted-STOP logging differ; logging/host timing can differ. This observation does not establish a V5 regression or firmware cause.",
              "limitations": ["WIRE command messages precede the write API. Equal prefixes establish equal attempted command bodies, not independently sniffed USB writes.",
                              "The identical protocol source zero-pads commands to 2048 bytes; logs preserve only 54 bytes, containing the complete relevant bodies and CRCs.",
                              "Exact 5M lifecycle/count does not prove Buffer signal activity/integrity; the first V5 Buffer requires the separate RAW content analysis.",
                              "A07 and V5 were tested at different times and with different physical signal wiring. A07's D0/D1 had separate sources; the current V5 wiring drives both from GPIO32. No controlled regression attribution follows.",
                              "Accepted STOP followed by two empty reads is endpoint quiet evidence, not a reset/alignment or stored-capture integrity proof."]}
    serialized = json.dumps(result, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(serialized)
    print(json.dumps({"comparison_gates": gates, "output": str(args.output) if args.output else None}, indent=2))
    return 0 if all(gates.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
