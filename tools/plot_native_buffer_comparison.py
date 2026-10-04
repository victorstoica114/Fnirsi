"""Plot finalized native CSV/.demo verification results; no hardware access.

Only cases with matching configuration and every logical D0/D1 sample verified
are accepted. Repetition counts can differ between settings. Capture means and
within-capture diagnostic windows are shown in separate standalone figures.
"""

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import sys

from plot_buffer_comparison import sha256


COLORS = {0.6: "#D55E00", 1.6: "#0072B2", 2.5: "#CC79A7"}
RATES = (50000000, 100000000, 250000000)
METRICS = (("high_mean_ns", "HIGH", "Width (ns)", 500),
           ("low_mean_ns", "LOW", "Width (ns)", 500),
           ("duty_percent", "Duty", "Duty (%)", 50))


def plotting_backend(directory):
    directory.mkdir(parents=True, exist_ok=True)
    os.environ["MPLCONFIGDIR"] = str(directory.resolve() / ".mplconfig")
    dependency_path = Path(__file__).parent / "plot-python-libs"
    if dependency_path.exists() and sys.version_info[:2] == (3, 13):
        sys.path.insert(0, str(dependency_path))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "pdf.fonttype": 42, "svg.fonttype": "none",
                         "axes.spines.top": False, "axes.spines.right": False})
    return matplotlib, plt


def verified_cases(path):
    report = json.loads(path.read_text(encoding="utf-8-sig"))
    if report.get("analysis") != "completed_native_FNIRSI_CSV_demo_strict_full_logical_prefix":
        raise ValueError("Expected finalized native full-prefix verification JSON")
    cases = report["cases"]
    if not cases or report.get("all_completed_cases_equal") is not True:
        raise ValueError("Native verification contains an error or a sample mismatch")
    seen = set()
    for case in cases:
        if case["status"] != "METADATA_AND_ALL_LOGICAL_SAMPLES_EQUAL":
            raise ValueError("Case has not passed export equality verification")
        if case["csv_sha256"] in seen:
            raise ValueError("Duplicated native capture would bias repetition statistics")
        seen.add(case["csv_sha256"])
        config = case["configuration"]
        if config["mode"] != "Buffer" or config["Fs_hz"] not in RATES:
            raise ValueError("Unexpected native mode/rate for this figure")
        if config["threshold_volts_saved"] not in COLORS:
            raise ValueError("Unexpected declared threshold for this figure")
        if {row["channel"] for row in case["channels"]} != {0, 1}:
            raise ValueError("Expected both physical sources, D0/D1")
        for row in case["channels"]:
            comparison = row["exact_full_logical_prefix_comparison"]
            if comparison["mismatch_count"] or comparison["compared_samples"] != config["logical_samples"]:
                raise ValueError("Native plot requires every logical sample to match")
    return report, cases


def save_figure(fig, prefix, source, metadata, matplotlib):
    paths = [prefix.with_suffix(suffix) for suffix in (".png", ".pdf", ".svg", ".figure.json")]
    if any(path.exists() for path in paths):
        raise ValueError("Refusing to overwrite an existing native figure")
    for path in paths[:3]:
        with path.open("xb") as destination:
            fig.savefig(destination, format=path.suffix[1:], dpi=300, facecolor="white")
    metadata.update({"source_verification_json": str(source.resolve()),
                     "source_verification_sha256": sha256(source),
                     "plot_script_sha256": sha256(Path(__file__)),
                     "matplotlib_version": matplotlib.__version__, "python_version": sys.version,
                     "hardware_access": False, "firmware_cause_established": False,
                     "artifacts": [{"path": str(path.resolve()), "sha256": sha256(path),
                                    "bytes": path.stat().st_size} for path in paths[:3]]})
    with paths[3].open("x", encoding="utf-8") as destination:
        json.dump(metadata, destination, indent=2)
        destination.write("\n")
    return metadata


def plot_captures(cases, source, prefix):
    matplotlib, plt = plotting_backend(prefix.parent)
    from matplotlib.lines import Line2D
    grouped = defaultdict(list)
    for case in cases:
        config = case["configuration"]
        for channel in case["channels"]:
            grouped[channel["channel"], config["threshold_volts_saved"], config["Fs_hz"]].append({
                "csv": case["csv"], "csv_sha256": case["csv_sha256"],
                "demo_sha256": case["demo_sha256"], "configuration": config,
                **channel["full_capture_paired_measurement"],
            })
    fig, axes = plt.subplots(2, 3, figsize=(14, 8.6))
    summaries = []
    for row_index, channel in enumerate((0, 1)):
        for column, (metric, title, ylabel, nominal) in enumerate(METRICS):
            ax = axes[row_index, column]
            ax.axhline(nominal, color="#A0A0A0", linewidth=1, linestyle="--", zorder=0)
            ax.grid(axis="y", alpha=0.22, zorder=0)
            for threshold_index, (threshold, color) in enumerate(COLORS.items()):
                offset = (threshold_index - 1) * 0.15
                for rate_index, rate in enumerate(RATES):
                    values = grouped.get((channel, threshold, rate))
                    if not values:
                        continue
                    numbers = [value[metric] for value in values]
                    mean = sum(numbers) / len(numbers)
                    minimum, maximum = min(numbers), max(numbers)
                    x = rate_index + offset
                    ax.errorbar(x, mean, yerr=[[mean - minimum], [maximum - mean]],
                                fmt="o", color=color, markersize=5.5,
                                markerfacecolor="white", markeredgewidth=1.4,
                                capsize=3, elinewidth=1.4, zorder=3)
                    # Centered display offsets do not change measured values.
                    xs = [x + (index - (len(numbers)-1)/2) * 0.017 for index in range(len(numbers))]
                    ax.scatter(xs, numbers, color=color, s=11, alpha=0.45, zorder=2)
                    ax.annotate(f"n={len(numbers)}", (x, maximum), xytext=(3, 7),
                                textcoords="offset points", fontsize=8, color=color)
                    summaries.append({"channel": channel, "threshold_volts": threshold,
                                      "sampling_rate_hz": rate, "metric": metric,
                                      "capture_count": len(numbers), "capture_means": numbers,
                                      "mean": mean, "minimum": minimum, "maximum": maximum})
            ax.set_xticks(range(3), ["50", "100", "250"])
            ax.set_xlim(-0.4, 2.4)
            ax.set_ylim((477, 525) if column < 2 else (47.7, 52.5))
            ax.set_ylabel(ylabel)
            ax.set_xlabel("Declared sampling rate (MS/s)")
            if row_index == 0:
                ax.set_title(title, pad=10)
        label = "ESP32 GPIO32 · D0" if channel == 0 else "FNIRSI PWM0 · D1"
        axes[row_index, 0].text(0, 1.20, label, transform=axes[row_index, 0].transAxes,
                                fontsize=11, weight="bold", ha="left")
    fig.suptitle("Official FNIRSI application: paired Buffer PWM measurements", fontsize=16, y=0.985)
    fig.text(0.5, 0.95, "Sources configured nominally to 1 MHz / 50% duty · all finalized repetitions retained",
             ha="center", fontsize=11)
    handles = [Line2D([], [], marker="o", linestyle="none", color=color,
                      markerfacecolor="white", label=f"FNIRSI native · {threshold:g} V")
               for threshold, color in COLORS.items()]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.115), ncol=3, frameon=False)
    fig.text(0.5, 0.095, "Markers: mean of capture means. Bars: min–max between captures. Dots: individual captures. n is the capture count.",
             ha="center", fontsize=9)
    fig.text(0.5, 0.075, "Declared logical window 10 ms; complete cycles only. CSV and .demo agree on every logical D0/D1 sample.",
             ha="center", fontsize=9)
    fig.text(0.5, 0.055, "The first 0.6 V / 100 MS/s capture includes a temporal change; its whole-capture mean is retained here.",
             ha="center", fontsize=9)
    fig.text(0.5, 0.035, "Dashed lines are nominal settings. Clock/threshold calibration and the cause of the timing difference remain unresolved.",
             ha="center", fontsize=9)
    fig.subplots_adjust(left=0.075, right=0.985, top=0.865, bottom=0.22, hspace=0.55, wspace=0.26)
    result = save_figure(fig, prefix, source,
                         {"purpose": "native_buffer_full_capture_comparison", "native_data_included": True,
                          "capture_count": len(cases), "interval_definition": "between_capture_min_max_not_CI_not_cycle_jitter",
                          "points": summaries,
                          "groups": [{"channel": key[0], "threshold_volts": key[1], "sampling_rate_hz": key[2],
                                      "captures": values} for key, values in grouped.items()]}, matplotlib)
    plt.close(fig)
    return result


def plot_transition(cases, source, prefix, csv_name):
    selected = [case for case in cases if Path(case["csv"]).name == csv_name]
    if len(selected) != 1:
        raise ValueError("Transition figure needs one exact verified CSV filename")
    case = selected[0]
    matplotlib, plt = plotting_backend(prefix.parent)
    fig, axes = plt.subplots(2, 2, figsize=(12, 7.8))
    windows = []
    for row_index, channel in enumerate(case["channels"]):
        data = channel["diagnostic_tenths"]
        times = [(window["start_ms"] + window["end_ms_exclusive"]) / 2 for window in data]
        left, right = axes[row_index]
        for metric, label, color in (("high_mean_ns", "HIGH", "#D55E00"),
                                     ("low_mean_ns", "LOW", "#0072B2")):
            left.plot(times, [window[metric] for window in data], marker="o", color=color, label=label)
        right.plot(times, [window["duty_percent"] for window in data], marker="o", color="#7C3D75")
        for ax, nominal in ((left, 500), (right, 50)):
            ax.axhline(nominal, color="#A0A0A0", linestyle="--", linewidth=1)
            ax.grid(alpha=0.22)
            ax.set_xlim(0, case["configuration"]["duration_ms"])
            ax.set_xlabel("Time in captured record (ms)")
        left.set_ylim(477, 523)
        right.set_ylim(47.7, 52.3)
        left.set_ylabel("Mean paired width (ns)")
        right.set_ylabel("Time-weighted paired duty (%)")
        left.legend(loc="best", frameon=False)
        label = "ESP32 GPIO32 · D0" if channel["channel"] == 0 else "FNIRSI PWM0 · D1"
        left.set_title(label, loc="left", weight="bold", pad=10)
        windows.append({"channel": channel["channel"], "diagnostic_tenths": data})
    config = case["configuration"]
    fig.suptitle("Temporal change within one official FNIRSI Buffer capture", fontsize=15, y=0.975)
    fig.text(0.5, 0.935, f"Saved setting {config['threshold_volts_saved']:g} V · {config['Fs_hz']/1e6:g} MS/s · nominal sources 1 MHz / 50%",
             ha="center", fontsize=10)
    fig.text(0.5, 0.11, "Each point summarizes complete paired cycles in one diagnostic window. The complete captured record is preserved.",
             ha="center", fontsize=9)
    fig.text(0.5, 0.085, "CSV and .demo match on every logical sample. Saved settings do not establish a physical threshold change during acquisition.",
             ha="center", fontsize=9)
    fig.text(0.5, 0.06, "The temporal change is observed; its cause is not identified. Lines connect diagnostic-window means, not individual edges.",
             ha="center", fontsize=9)
    fig.subplots_adjust(left=0.08, right=0.98, top=0.86, bottom=0.19, hspace=0.42, wspace=0.28)
    result = save_figure(fig, prefix, source,
                         {"purpose": "native_within_capture_diagnostic_transition", "csv": case["csv"],
                          "csv_sha256": case["csv_sha256"], "demo_sha256": case["demo_sha256"],
                          "configuration": config, "windows": windows,
                          "whole_capture_preserved": True,
                          "physical_threshold_change_during_capture_established": False}, matplotlib)
    plt.close(fig)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verification_json", type=Path)
    parser.add_argument("--prefix", type=Path, default=Path("artifacts/report/buffer-native"))
    parser.add_argument("--transition-csv", default="FNIRSI-native-100MSps-th0p6-repeat1.csv")
    args = parser.parse_args()
    _, cases = verified_cases(args.verification_json)
    main_result = plot_captures(cases, args.verification_json, args.prefix)
    transition_prefix = args.prefix.with_name(args.prefix.name + "-transition")
    transition = plot_transition(cases, args.verification_json, transition_prefix, args.transition_csv)
    print(json.dumps({"native_capture_count": len(cases), "artifacts": main_result["artifacts"],
                      "transition_artifacts": transition["artifacts"]}))


if __name__ == "__main__":
    main()
