"""Check a short PWM capture against an independent, nominal source clock."""
import argparse
from collections import Counter
import configparser
import json
from pathlib import Path
import statistics
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--rate", type=int, default=50000000)
    parser.add_argument("--samples", type=int, default=50000)
    parser.add_argument("--channel", type=int, default=0)
    parser.add_argument("--frequency", type=int, default=100000)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not 0 <= args.channel < 32 or args.rate <= 0 or args.frequency <= 0:
        parser.error("invalid channel, sample rate or frequency")
    unitsize = 4
    actual_rate = args.rate
    probes = 32
    if args.capture.suffix == ".sr":
        with zipfile.ZipFile(args.capture) as archive:
            metadata = configparser.ConfigParser()
            metadata.read_string(archive.read("metadata").decode())
            device = metadata["device 1"]
            unitsize = int(device["unitsize"])
            probes = int(device["total probes"])
            number, unit = device["samplerate"].split()
            actual_rate = int(float(number) * {"Hz": 1, "kHz": 1000, "MHz": 1000000, "GHz": 1000000000}[unit])
            chunks = sorted((name for name in archive.namelist() if name.startswith("logic-1-")),
                            key=lambda name: int(name.rsplit("-", 1)[1]))
            data = b"".join(archive.read(name) for name in chunks)
    else:
        data = args.capture.read_bytes()
    if unitsize != 4 or len(data) % unitsize:
        raise RuntimeError("Expected complete 32-bit sample words")
    offset, bit = divmod(args.channel, 8)
    levels = [(byte >> bit) & 1 for byte in data[offset::unitsize]]
    edges = [i for i in range(1, len(levels)) if levels[i] != levels[i-1]]
    rising = [i for i in edges if levels[i]]
    periods = [b-a for a, b in zip(rising, rising[1:])]
    high = [b-a for a, b in zip(edges, edges[1:]) if levels[a]]
    low = [b-a for a, b in zip(edges, edges[1:]) if not levels[a]]
    nominal_period = args.rate / args.frequency
    median_period = statistics.median(periods) if periods else 0
    measured_frequency = actual_rate / statistics.mean(periods) if periods else None
    measured_duty = 100 * statistics.mean(high) / (statistics.mean(high) + statistics.mean(low)) if high and low else None
    # A +/-0.5% nominal frequency allowance and +/-1-sample residual allowance
    # account for independent oscillators and quantization on this short test.
    # They do not prove absence of every lost sample or calibrate either clock.
    regular = bool(periods and max(abs(p-median_period) for p in periods) <= 1)
    widths_regular = bool(high and low and max(high)-min(high) <= 1 and max(low)-min(low) <= 1)
    passed = bool(len(levels) == args.samples and probes == 32 and actual_rate == args.rate
                  and len(periods) >= 8 and regular and widths_regular
                  and abs(median_period-nominal_period) <= max(1, nominal_period*0.005)
                  and abs(measured_frequency/args.frequency - 1) <= 0.005
                  and abs(measured_duty-50) <= 0.5)
    result = dict(capture=str(args.capture), samples=len(levels), probes=probes,
                  unitsize=unitsize, samplerate_hz=actual_rate, channel=args.channel,
                  nominal_frequency_hz=args.frequency, measured_frequency_hz=measured_frequency,
                  measured_duty_percent=measured_duty, edges=len(edges), rising=len(rising),
                  complete_periods=len(periods), period_histogram=dict(Counter(periods)),
                  high_width_histogram=dict(Counter(high)), low_width_histogram=dict(Counter(low)),
                  independent_clock_calibration=False, long_stream_integrity_validated=False,
                  passed=passed)
    args.report.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
