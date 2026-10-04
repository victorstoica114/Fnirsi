"""Compare DLA-32 decoder input with saved sample bits from the same acquisition.

The caller supplies an unchanged wire trace and its corresponding .sr file.
Matching bytes establishes conversion agreement for the selected channels;
it does not establish physical signal integrity or the origin of lane alignment.
"""
import argparse
import json
from pathlib import Path
import tempfile
import zipfile

from analyze_duty_capture import capture_sha256, decode_wire_levels, read_capture


def compare(wire_path, capture_path, channels):
    wire = wire_path.read_bytes()
    comparisons = []
    samples = None
    for channel in channels:
        stored, rate, metadata = read_capture(capture_path, channel)
        if metadata["probes"] != 32 or metadata["sample_word_bytes"] != 4:
            raise ValueError("This comparison requires a DLA-32 32-probe/4-byte capture")
        if len(stored) % 8:
            raise ValueError("The selected capture must end at a complete eight-sample wire frame")
        if samples is not None and samples != len(stored):
            raise ValueError("Channel lengths differ")
        samples = len(stored)
        prefix_bytes = samples * 4
        if len(wire) < prefix_bytes:
            raise ValueError("Wire trace is shorter than the saved capture prefix")
        expected = decode_wire_levels(wire[:prefix_bytes], channel)
        first_mismatch = None
        mismatch_count = 0
        for index, (actual_bit, expected_bit) in enumerate(zip(stored, expected)):
            if actual_bit != expected_bit:
                mismatch_count += 1
                if first_mismatch is None:
                    first_mismatch = index
        comparisons.append({
            "physical_channel_bit": channel,
            "mismatching_samples": mismatch_count,
            "first_mismatch_sample": first_mismatch,
            "match": mismatch_count == 0,
        })
    return {
        "comparison": "decoder_input_wire_prefix_vs_saved_sr_physical_bits",
        "wire": str(wire_path), "wire_sha256": capture_sha256(wire_path),
        "capture": str(capture_path), "capture_sha256": capture_sha256(capture_path),
        "samplerate_hz": rate, "saved_samples": samples,
        "wire_bytes": len(wire), "compared_wire_prefix_bytes": samples * 4,
        "extra_wire_bytes_after_saved_samples": len(wire) - samples * 4,
        "channels": comparisons,
        "selected_channels_match": all(row["match"] for row in comparisons),
        "independent_wire_lane_bit_expansion": True,
        "same_acquisition_association": "Caller must pair the trace with its .sr acquisition log",
        "signal_integrity_verified": False, "firmware_fault_established": False,
    }


def self_test():
    with tempfile.TemporaryDirectory(prefix="dla32-wire-sr-test-") as directory:
        directory = Path(directory)
        wire_path, capture_path = directory / "wire.bin", directory / "known.sr"
        frame = bytearray(32)
        frame[0], frame[1], frame[24], frame[25] = 0x55, 0xaa, 0x03, 0xc0
        wire_path.write_bytes(frame + bytes([0xff]) * 32)
        words = [0x01000001, 0x01000002, 1, 2, 1, 2, 0x02000001, 0x02000002]

        def fixture(values):
            with zipfile.ZipFile(capture_path, "w") as archive:
                archive.writestr("metadata", "[device 1]\nunitsize=4\ntotal probes=32\n"
                                 "samplerate=50 MHz\ncapturefile=logic-1\n")
                archive.writestr("logic-1-1", b"".join(value.to_bytes(4, "little") for value in values))

        fixture(words)
        result = compare(wire_path, capture_path, list(range(32)))
        assert result["selected_channels_match"]
        assert result["extra_wire_bytes_after_saved_samples"] == 32
        changed = words.copy()
        changed[2] = 0
        fixture(changed)
        result = compare(wire_path, capture_path, [0, 1, 24, 25])
        assert not result["selected_channels_match"]
        assert result["channels"][0]["mismatching_samples"] == 1
        assert result["channels"][0]["first_mismatch_sample"] == 2
        fixture(words)
        wire_path.write_bytes(frame[:-1])
        try:
            compare(wire_path, capture_path, [0])
        except ValueError:
            pass
        else:
            raise AssertionError("A truncated wire prefix was accepted")
    print("Wire/.sr comparison self-check: all 32 bits, overshoot, corruption and truncation verified")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wire", type=Path, nargs="?")
    parser.add_argument("capture", type=Path, nargs="?")
    parser.add_argument("--channels", default="0,1,24,25",
                        help="Physical bits to compare; use all for every bit")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        if args.wire is None:
            return 0
    if args.wire is None or args.capture is None or args.report is None:
        parser.error("Provide wire, capture and --report, or only --self-test")
    try:
        channels = list(range(32)) if args.channels == "all" else sorted(set(map(int, args.channels.split(","))))
    except ValueError:
        parser.error("--channels must be comma-separated integers or all")
    if not channels or any(not 0 <= channel < 32 for channel in channels):
        parser.error("Channel bits must be in 0..31")
    result = compare(args.wire, args.capture, channels)
    with args.report.open("x", encoding="utf-8") as file:
        json.dump(result, file, indent=2)
        file.write("\n")
    print(json.dumps(result, indent=2))
    return 0 if result["selected_channels_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
