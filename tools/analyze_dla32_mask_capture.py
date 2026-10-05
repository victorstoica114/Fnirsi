"""Compare unchanged DLA-32 mask-packed RAW with same-capture binary LOGIC.

The configured packing contract is ascending enabled physical indices, eight
LSB-first samples per lane byte. Stream rounds frame width to1/2/4/8/16/32;
Buffer rounds to1/8/16/32. Padding, complete read overshoot, and RAW tails are
preserved and hashed. Conversion agreement is not physical-origin or loss proof.
"""
from __future__ import annotations

import argparse
from array import array
import hashlib
import json
import os
from pathlib import Path
import sys

CHUNK_FRAMES = 16384


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def packing(mask, mode):
    if type(mask) is not int or not 0 < mask <= 0xffffffff or mode not in ("Buffer", "Stream"):
        raise ValueError("Require nonzero32-bit mask and explicit Buffer/Stream mode")
    channels = [i for i in range(32) if mask & (1 << i)]
    frame = 1 << (len(channels) - 1).bit_length()
    if mode == "Buffer" and 1 < frame < 8:
        frame = 8
    unit = 1 if channels[-1] < 8 else 2 if channels[-1] < 16 else 4
    return channels, frame, unit


def decode_block(raw, mask, mode):
    channels, frame, unit = packing(mask, mode)
    if len(raw) % frame:
        raise ValueError("Decoder block requires complete configured wire frames")
    # Independent arithmetic transposition, without DLL/C tables or a shift
    # guessed from observed activity. Physical output bits remain unchanged.
    spread = [sum(((value >> t) & 1) << (t * unit * 8) for t in range(8)) for value in range(256)]
    tables = [[value << channel for value in spread] for channel in channels]
    out = bytearray(len(raw) // frame * 8 * unit)
    for index, offset in enumerate(range(0, len(raw), frame)):
        word = 0
        for slot, table in enumerate(tables):
            word |= table[raw[offset + slot]]
        out[index * 8 * unit:(index + 1) * 8 * unit] = word.to_bytes(8 * unit, "little")
    return out


def words(data, unit):
    result = array({1: "B", 2: "H", 4: "I"}[unit])
    if result.itemsize != unit:
        raise ValueError("Unexpected native integer width")
    result.frombytes(data)
    if unit > 1 and sys.byteorder != "little":
        result.byteswap()
    return result


def compare_files(wire, logic, mask, mode, *, unitsize=None, chunk_frames=CHUNK_FRAMES):
    wire, logic = Path(wire), Path(logic)
    channels, frame, unit = packing(mask, mode)
    if unitsize is not None and (type(unitsize) is not int or unitsize != unit):
        raise ValueError("LOGIC unitsize must reflect highest enabled physical index")
    if type(chunk_frames) is not int or chunk_frames < 1:
        raise ValueError("Chunk frame count must be positive")
    if os.path.samefile(wire, logic):
        raise ValueError("RAW and LOGIC must be distinct acquisition files")
    stamps = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in (wire, logic)}
    wire_size, logic_size = stamps[wire][0], stamps[logic][0]
    if logic_size % unit:
        raise ValueError("Truncated LOGIC sample word")
    samples = logic_size // unit
    required_frames = (samples + 7) // 8
    required_raw = required_frames * frame
    if wire_size < required_raw:
        raise ValueError("RAW is shorter than the complete frames required for saved LOGIC")
    width = unit * 8
    mismatches, first = [0] * width, [None] * width
    raw_or = logic_or = raw_and = logic_and = (1 << width) - 1
    raw_or = logic_or = 0
    differing_words = sample_offset = 0
    prefix_digest = hashlib.sha256()
    padding_or = [0] * (frame - len(channels))
    with wire.open("rb") as raw, logic.open("rb") as saved:
        remaining_frames = required_frames
        while remaining_frames:
            nframes = min(chunk_frames, remaining_frames)
            block = raw.read(nframes * frame)
            if len(block) != nframes * frame:
                raise ValueError("RAW changed or truncated during comparison")
            prefix_digest.update(block)
            for slot in range(len(channels), frame):
                for value in block[slot::frame]:
                    padding_or[slot - len(channels)] |= value
            expected = decode_block(block, mask, mode)
            count = min(nframes * 8, samples - sample_offset)
            actual = saved.read(count * unit)
            if len(actual) != count * unit:
                raise ValueError("LOGIC changed or truncated during comparison")
            for i, (expected_word, actual_word) in enumerate(zip(words(expected[:count * unit], unit), words(actual, unit))):
                raw_or |= expected_word
                logic_or |= actual_word
                raw_and &= expected_word
                logic_and &= actual_word
                difference = expected_word ^ actual_word
                if difference:
                    differing_words += 1
                    for bit in range(width):
                        if difference & (1 << bit):
                            mismatches[bit] += 1
                            if first[bit] is None:
                                first[bit] = sample_offset + i
            sample_offset += count
            remaining_frames -= nframes
        if saved.read(1):
            raise ValueError("LOGIC grew during comparison")
    full_hashes = {p: sha(p) for p in (wire, logic)}
    if any((p.stat().st_size, p.stat().st_mtime_ns) != stamp for p, stamp in stamps.items()):
        raise ValueError("Input changed during analysis")
    lanes = [{"physical_bit": bit, "enabled": bit in channels,
        "mismatching_samples": mismatches[bit], "first_mismatch_sample": first[bit],
        "raw_seen_high": bool(raw_or & (1 << bit)),
        "raw_seen_low": samples > 0 and not bool(raw_and & (1 << bit)),
        "logic_seen_high": bool(logic_or & (1 << bit)),
        "logic_seen_low": samples > 0 and not bool(logic_and & (1 << bit))}
        for bit in range(width)]
    return {"analysis": "configured-mask original RAW prefix vs same-acquisition LOGIC",
        "mode": mode, "physical_channel_mask": f"{mask:08x}", "enabled_physical_channels": channels,
        "wire_frame_bytes": frame, "logic_unitsize": unit,
        "configured_wire_slot_to_output_physical_bit": channels,
        "padding_wire_slots": list(range(len(channels), frame)),
        "padding_slot_OR_in_required_frames": padding_or,
        "padding_is_not_assigned_to_physical_channels": True,
        "wire_file": str(wire.resolve()), "logic_file": str(logic.resolve()),
        "wire_sha256": full_hashes[wire], "logic_sha256": full_hashes[logic],
        "compared_complete_frame_prefix_sha256": prefix_digest.hexdigest(),
        "wire_bytes": wire_size, "logic_bytes": logic_size, "saved_samples": samples,
        "compared_complete_frame_prefix_bytes": required_raw,
        "extra_RAW_bytes_after_required_saved_frames": wire_size - required_raw,
        "wire_tail_mod_frame": wire_size % frame,
        "unused_packed_samples_in_final_required_frame": required_frames * 8 - samples,
        "all_encoded_output_bits_match": not differing_words,
        "nonempty_comparison": samples > 0,
        "encoded_output_bit_comparisons": samples * width,
        "enabled_channel_bit_comparisons": samples * len(channels),
        "disabled_encoded_bit_comparisons": samples * (width - len(channels)),
        "differing_sample_words": differing_words, "lanes": lanes,
        "raw_lanes_seen_both_levels": [r["physical_bit"] for r in lanes if r["raw_seen_high"] and r["raw_seen_low"]],
        "raw_lanes_seen_high": [r["physical_bit"] for r in lanes if r["raw_seen_high"]],
        "logic_bits_seen_high_outside_configured_mask": [r["physical_bit"] for r in lanes if not r["enabled"] and r["logic_seen_high"]],
        "chunk_frames": chunk_frames, "raw_prefix_skipped_bytes": 0,
        "activity_used_to_choose_rotation_or_channel_mapping": False,
        "lane_remapping_applied": False, "frame_origin_validated": False,
        "signal_integrity_verified": False, "lossless_physical_stream_verified": False,
        "sampling_rate_threshold_trigger_validated": False,
        "same_acquisition_association": "Caller must pair saved bytes with the exact capture record"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wire", type=Path)
    parser.add_argument("logic", type=Path)
    parser.add_argument("--mask", required=True, type=lambda s: int(s, 16), help="Nonzero physical32-bit hexadecimal mask")
    parser.add_argument("--mode", required=True, choices=("Buffer", "Stream"))
    parser.add_argument("--unitsize", type=int, choices=(1, 2, 4))
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists() or args.report.resolve() in (args.wire.resolve(), args.logic.resolve()):
        parser.error("Report must be a new output distinct from input files")
    result = compare_files(args.wire, args.logic, args.mask, args.mode, unitsize=args.unitsize)
    result["analyzer_sha256"] = sha(Path(__file__))
    result["conversion_verdict"] = "PENDING" if not result["nonempty_comparison"] else "PASS" if result["all_encoded_output_bits_match"] else "FAIL"
    with args.report.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({key: result[key] for key in ("conversion_verdict", "saved_samples",
        "physical_channel_mask", "logic_unitsize", "wire_frame_bytes", "differing_sample_words")}))
    return 2 if not result["nonempty_comparison"] else 0 if result["all_encoded_output_bits_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
