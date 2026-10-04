"""Measure paired PWM cycles in srzip or DLA-32 raw wire data; diagnostic only.

Sampling rate and source frequency are separate quantities. Each completed
cycle contains a rising edge, its falling edge, and the next rising edge.
No acceptance criterion or firmware diagnosis is inferred from this report.
Raw mode reads each physical lane byte and expands its eight LSB-first samples
directly, independently of libsigrok's sample-word decoder.
"""

import argparse
from collections import Counter
import configparser
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import zipfile


def histogram_median(histogram):
    count = sum(histogram.values())
    if not count:
        return None
    targets = ((count - 1) // 2, count // 2)
    found = []
    cumulative = 0
    for value, occurrences in sorted(histogram.items()):
        previous = cumulative
        cumulative += occurrences
        for target in targets:
            if previous <= target < cumulative:
                found.append(value)
    return sum(found) / 2


def histogram_summary(histogram):
    count = sum(histogram.values())
    return {
        "count": count,
        "minimum": min(histogram) if count else None,
        "maximum": max(histogram) if count else None,
        "span": max(histogram) - min(histogram) if count else None,
        "mean": sum(value * n for value, n in histogram.items()) / count
        if count else None,
        "median": histogram_median(histogram),
        "histogram": {str(value): n for value, n in sorted(histogram.items())},
    }


def analyze_levels(levels, rate, frequency, example_limit):
    high_hist, low_hist, period_hist = Counter(), Counter(), Counter()
    imbalance_hist, pair_hist, duty_hist = Counter(), Counter(), Counter()
    rise_mod8, fall_mod8, imbalanced_mod8 = Counter(), Counter(), Counter()
    examples = []
    previous = levels[0] if levels else 0
    rising = falling = None
    edges = rises = falls = 0
    for index in range(1, len(levels)):
        level = levels[index]
        if level == previous:
            continue
        edges += 1
        if level:
            rises += 1
            if rising is not None and falling is not None:
                high = falling - rising
                low = index - falling
                period = index - rising
                imbalance = high - low
                high_hist[high] += 1
                low_hist[low] += 1
                period_hist[period] += 1
                imbalance_hist[imbalance] += 1
                pair_hist[high, low] += 1
                duty_hist[Fraction(high, period)] += 1
                rise_mod8[rising % 8] += 1
                fall_mod8[falling % 8] += 1
                if imbalance:
                    imbalanced_mod8[rising % 8] += 1
                    if len(examples) < example_limit:
                        examples.append({
                            "rising_sample": rising,
                            "falling_sample": falling,
                            "next_rising_sample": index,
                            "high_samples": high,
                            "low_samples": low,
                            "period_samples": period,
                            "duty_percent": 100 * high / period,
                            "high_minus_low_samples": imbalance,
                            "high_minus_low_ns": imbalance * 1e9 / rate,
                            "rising_mod8": rising % 8,
                            "falling_mod8": falling % 8,
                        })
            rising, falling = index, None
        else:
            falls += 1
            if rising is not None:
                falling = index
        previous = level

    count = sum(period_hist.values())
    total_high = sum(width * n for width, n in high_hist.items())
    total_low = sum(width * n for width, n in low_hist.items())
    total_period = total_high + total_low
    median_period = histogram_median(period_hist)
    mean_imbalance = sum(value * n for value, n in imbalance_hist.items()) / count if count else None
    widths_high, widths_low = histogram_summary(high_hist), histogram_summary(low_hist)
    flags = {
        "no_complete_cycles": count == 0,
        "fewer_than_eight_complete_cycles": count < 8,
        "nominal_samples_per_period_below_twenty": rate / frequency < 20,
        "paired_high_width_span_above_one_sample": bool(count and widths_high["span"] > 1),
        "paired_low_width_span_above_one_sample": bool(count and widths_low["span"] > 1),
        "periods_more_than_one_sample_from_median_count":
            sum(n for period, n in period_hist.items() if abs(period - median_period) > 1)
            if count else 0,
        "cycles_with_high_low_imbalance_above_one_sample_count":
            sum(n for difference, n in imbalance_hist.items() if abs(difference) > 1),
    }
    return {
        "samples": len(levels),
        "edges": edges,
        "rising_edges": rises,
        "falling_edges": falls,
        "complete_paired_cycles": count,
        "partial_cycles_excluded": True,
        "period_samples": histogram_summary(period_hist),
        "paired_high_samples": widths_high,
        "paired_low_samples": widths_low,
        "paired_high_low_histogram": [
            {"high_samples": high, "low_samples": low, "count": n}
            for (high, low), n in sorted(pair_hist.items())
        ],
        "per_cycle_duty_fraction_histogram": [
            {"fraction": str(duty), "percent": float(duty * 100), "count": n}
            for duty, n in sorted(duty_hist.items())
        ],
        "paired_time_weighted_duty_percent": 100 * total_high / total_period if count else None,
        "per_cycle_unweighted_duty_mean_percent":
            float(sum(duty * n for duty, n in duty_hist.items()) * 100 / count) if count else None,
        "high_minus_low_samples": histogram_summary(imbalance_hist),
        "mean_high_minus_low_ns": mean_imbalance * 1e9 / rate if count else None,
        "measured_frequency_hz_from_declared_samplerate": rate * count / total_period if count else None,
        "sample_tick_ns": 1e9 / rate,
        "nominal_source_period_samples": rate / frequency,
        "nominal_half_period_samples_at_50_percent": rate / frequency / 2,
        "one_sample_duty_step_percent_at_nominal_period": 100 * frequency / rate,
        "paired_rising_mod8_histogram": {str(i): rise_mod8[i] for i in range(8)},
        "paired_falling_mod8_histogram": {str(i): fall_mod8[i] for i in range(8)},
        "imbalanced_cycle_rising_mod8_histogram": {str(i): imbalanced_mod8[i] for i in range(8)},
        "diagnostic_flags": flags,
        "first_imbalanced_cycle_examples": examples,
    }


def read_capture(path, channel):
    with zipfile.ZipFile(path) as archive:
        metadata = configparser.ConfigParser()
        metadata.read_string(archive.read("metadata").decode("utf-8"))
        device = metadata["device 1"]
        unitsize, probes = int(device["unitsize"]), int(device["total probes"])
        if unitsize not in (1, 2, 4) or not 1 <= probes <= unitsize * 8:
            raise ValueError("Expected 1-, 2- or 4-byte logic words with matching probe capacity")
        if not isinstance(channel, int) or not 0 <= channel < probes:
            raise ValueError("Channel bit is outside this capture's physical probe range")
        number, unit = device["samplerate"].split()
        multipliers = {"Hz": 1, "kHz": 1000, "MHz": 1000000, "GHz": 1000000000}
        rate_value = Decimal(number) * multipliers[unit]
        if rate_value <= 0 or rate_value != int(rate_value):
            raise ValueError("Expected a positive integer sampling rate")
        prefix = device.get("capturefile", "logic-1") + "-"
        chunks = sorted((name for name in archive.namelist() if name.startswith(prefix)),
                        key=lambda name: int(name[len(prefix):]))
        if not chunks:
            raise ValueError("No logic sample chunks in capture")
        data = b"".join(archive.read(name) for name in chunks)
        if len(data) % unitsize:
            raise ValueError("Incomplete logic sample word")
        offset, bit = divmod(channel, 8)
        levels = bytes((value >> bit) & 1 for value in data[offset::unitsize])
        return levels, int(rate_value), {
            "input_encoding": f"little_endian_{unitsize * 8}bit_sample_words_in_srzip",
            "sampling_rate_provenance": "srzip_metadata",
            "sampling_rate_label": device["samplerate"],
            "probes": probes,
            "sample_word_bytes": unitsize,
            "channel_label": device.get("probe" + str(channel + 1), "D" + str(channel)),
            "data_chunks": len(chunks),
        }


def decode_wire_levels(payload, channel):
    if len(payload) % 32:
        raise ValueError("Raw input must contain complete 32-byte, 32-lane wire frames")
    # A frame is 32 physical-lane bytes, not eight 32-bit sample words.
    # Expand this lane explicitly; no driver table/SIMD decoder is used.
    return bytes((lane_byte >> sample_bit) & 1
                 for lane_byte in payload[channel::32]
                 for sample_bit in range(8))


def read_wire_capture(path, channel, rate):
    payload = path.read_bytes()
    return decode_wire_levels(payload, channel), rate, {
        "input_encoding": "32_physical_lane_bytes_per_frame_8_lsb_first_samples",
        "sampling_rate_provenance": "explicit_wire_rate_cli_argument",
        "sampling_rate_label": str(rate) + " Hz",
        "probes": 32,
        "channel_label": "D" + str(channel),
        "wire_frame_bytes": 32,
        "wire_samples_per_frame": 8,
        "wire_physical_lane": channel,
        "wire_payload_bytes": len(payload),
        "independent_python_lane_bit_expansion": True,
    }


def capture_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as capture:
        for block in iter(lambda: capture.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def self_test():
    import tempfile
    even = b"\0" + (b"\1" * 25 + b"\0" * 25) * 8 + b"\1"
    measured = analyze_levels(even, 50000000, 1000000, 4)
    assert measured["complete_paired_cycles"] == 8
    assert measured["paired_time_weighted_duty_percent"] == 50
    assert measured["period_samples"]["histogram"] == {"50": 8}
    odd = b"\0" + (b"\1" * 12 + b"\0" * 13 + b"\1" * 13 + b"\0" * 12) * 4 + b"\1"
    measured = analyze_levels(odd, 25000000, 1000000, 4)
    assert measured["complete_paired_cycles"] == 8
    assert measured["paired_time_weighted_duty_percent"] == 50
    assert measured["per_cycle_duty_fraction_histogram"] == [
        {"fraction": "12/25", "percent": 48.0, "count": 4},
        {"fraction": "13/25", "percent": 52.0, "count": 4},
    ]
    wire = bytearray(64)
    wire[0], wire[1], wire[31] = 0x81, 0x96, 0xA5
    wire[33], wire[63] = 0x01, 0xFF
    assert decode_wire_levels(wire, 1) == bytes([0, 1, 1, 0, 1, 0, 0, 1,
                                                1, 0, 0, 0, 0, 0, 0, 0])
    assert decode_wire_levels(wire, 31) == bytes([1, 0, 1, 0, 0, 1, 0, 1] + [1] * 8)
    try:
        decode_wire_levels(wire[:-1], 1)
    except ValueError:
        pass
    else:
        raise AssertionError("Incomplete wire frame was accepted")
    # Kingst retains physical bit positions in 16-bit words even when only
    # CH0/CH1 are enabled. Exercise bit 15 too, so byte-offset mistakes fail.
    with tempfile.TemporaryDirectory(prefix="dla32-duty-selftest-") as directory:
        capture = Path(directory) / "known.sr"
        expected = b"\0" + (b"\1" * 50 + b"\0" * 50) * 8 + b"\1"

        def fixture(unitsize, probes, payload, with_data=True):
            text = ("[device 1]\ncapturefile=logic-1\n"
                    f"unitsize={unitsize}\ntotal probes={probes}\n"
                    "samplerate=100 MHz\nprobe2=CH1\nprobe16=CH15\n")
            with zipfile.ZipFile(capture, "w") as archive:
                archive.writestr("metadata", text)
                if with_data:
                    archive.writestr("logic-1-1", payload)

        payload = b"".join((1 | ((1 << 1 | 1 << 15) if value else 0)).to_bytes(2, "little")
                           for value in expected)
        fixture(2, 16, payload)
        for channel in (1, 15):
            levels, rate, metadata = read_capture(capture, channel)
            assert levels == expected and rate == 100000000
            assert metadata["sample_word_bytes"] == 2
            assert analyze_levels(levels, rate, 1000000, 4)["paired_time_weighted_duty_percent"] == 50
        for channel in (-1, 16):
            try:
                read_capture(capture, channel)
            except ValueError:
                pass
            else:
                raise AssertionError("An out-of-range physical channel was accepted")
        for unitsize, probes, data, with_data in (
                (2, 16, payload[:-1], True), (2, 17, payload, True),
                (3, 16, payload, True), (2, 16, payload, False)):
            fixture(unitsize, probes, data, with_data)
            try:
                read_capture(capture, 1)
            except ValueError:
                pass
            else:
                raise AssertionError("Malformed srzip sample data was accepted")
        fixture(1, 8, bytes(value << 7 for value in expected))
        assert read_capture(capture, 7)[0] == expected
    print("Duty diagnostic self-check: even/odd cycles, physical wire lanes, "
          "8-/16-bit srzip channels and malformed-input rejection verified")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, nargs="?")
    parser.add_argument("--channel", type=int, default=1,
                        help="Physical sample-word bit: 0..31, bounded by capture metadata")
    parser.add_argument("--frequency", type=int, default=1000000, help="Nominal source frequency in Hz")
    parser.add_argument("--examples", type=int, default=12)
    parser.add_argument("--wire-rate", type=int,
                        help="Explicit raw mode: declared sampling rate in Hz; assumes all 32 physical lanes")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if (not 0 <= args.channel < 32 or args.frequency <= 0 or args.examples < 0
            or (args.wire_rate is not None and args.wire_rate <= 0)):
        parser.error("Channel must be 0..31; frequencies/rates positive; example count nonnegative")
    if args.self_test:
        self_test()
        if args.capture is None:
            return 0
    if args.capture is None or args.report is None:
        parser.error("A capture and --report are required unless running only --self-test")
    if args.wire_rate is None:
        levels, rate, metadata = read_capture(args.capture, args.channel)
    else:
        levels, rate, metadata = read_wire_capture(args.capture, args.channel, args.wire_rate)
    result = {
        "analysis": "paired_pwm_duty_diagnostic",
        "classification": "diagnostic_only",
        "capture": str(args.capture),
        "capture_sha256": capture_sha256(args.capture),
        "channel_physical_bit": args.channel,
        "sampling_rate_hz": rate,
        "sampling_rate_msps": rate / 1000000,
        "nominal_source_frequency_hz": args.frequency,
        "independent_clock_calibration": False,
        "firmware_fault_established": False,
        "long_stream_integrity_validated": False,
        "interpretation": "Single-cycle duty reflects sample resolution and edge timing. "
                          "Statistics alone neither establish a firmware fault nor prove lossless acquisition. "
                          "A constant odd sample count per period can produce non-50% individual cycles.",
        **metadata,
        **analyze_levels(levels, rate, args.frequency, args.examples),
    }
    output = json.dumps(result, indent=2) + "\n"
    args.report.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
