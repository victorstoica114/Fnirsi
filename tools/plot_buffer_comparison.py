"""Export a standalone scientific figure from the offline detailed PWM CSV.

Markers are unweighted means of three capture means. Bars span the minimum
and maximum of those captures, not confidence intervals or cycle jitter.
The comparison does not designate either device as an absolute reference.
"""

import argparse
from collections import defaultdict
import csv
import hashlib
import json
import os
from pathlib import Path
import sys


SERIES = (
    ("FNIRSI-Buffer", 0.6, "FNIRSI Buffer · 0.6 V", "#D55E00", "o", -0.24),
    ("FNIRSI-Buffer", 1.6, "FNIRSI Buffer · 1.6 V", "#0072B2", "o", -0.12),
    ("FNIRSI-Buffer", 2.5, "FNIRSI Buffer · 2.5 V", "#CC79A7", "o", 0.0),
    ("Kingst", 1.4, "Kingst · 1.4 V", "#009E73", "s", 0.12),
    ("Kingst", 2.5, "Kingst · 2.5 V", "#222222", "s", 0.24),
)
NODES = ("ESP32_GPIO32", "FNIRSI_PWM0")
METRICS = (("high_mean_ns", "HIGH", "Width (ns)", 500),
           ("low_mean_ns", "LOW", "Width (ns)", 500),
           ("duty_percent", "Duty", "Duty (%)", 50))
RATES = (50000000, 100000000, 250000000)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_measurements(path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    grouped = defaultdict(list)
    for row in rows:
        if row["native_application"] != "False":
            raise ValueError("This figure expects the complete CLI-only matrix, not mixed native data")
        if int(row["source_frequency_hz"]) != 1000000 or float(row["source_nominal_duty_percent"]) != 50:
            raise ValueError("Figure's nominal labels require 1 MHz / 50% source configuration")
        if row["exit_code"] != "0":
            raise ValueError("Figure input contains a failed capture")
        key = (row["device"], float(row["threshold_volts"]), row["node"], int(row["sampling_rate_hz"]))
        values = {metric: float(row[metric]) for metric, *_ in METRICS}
        values.update({"repeat": int(row["repeat"]), "actual_samples": int(row["actual_samples"]),
                       "actual_duration_ms": float(row["actual_duration_ms"]),
                       "capture": row["capture"], "capture_sha256": row["capture_sha256"]})
        grouped[key].append(values)
    expected = set()
    for device, threshold, *_ in SERIES:
        rates = RATES if device == "FNIRSI-Buffer" else ((100000000,) if threshold == 1.4 else RATES[:2])
        expected.update((device, threshold, node, rate) for node in NODES for rate in rates)
    if set(grouped) != expected:
        raise ValueError("CSV does not contain the expected 24 node/rate/threshold groups")
    for key, values in grouped.items():
        if sorted(value["repeat"] for value in values) != [1, 2, 3]:
            raise ValueError(f"Expected three distinct repetitions for {key}")
    return grouped


def export_figure(source, prefix):
    targets = [prefix.with_suffix(suffix) for suffix in (".png", ".pdf", ".svg", ".figure.json")]
    if any(target.exists() for target in targets):
        raise ValueError("Refusing to overwrite an existing figure artifact")
    grouped = read_measurements(source)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    # Keep Matplotlib's cache within the owned artifact directory.
    os.environ["MPLCONFIGDIR"] = str(prefix.parent.resolve() / ".mplconfig")
    local_dependencies = Path(__file__).parent / "plot-python-libs"
    if local_dependencies.exists() and sys.version_info[:2] == (3, 13):
        sys.path.insert(0, str(local_dependencies))
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.lines import Line2D
    except ImportError as error:
        raise RuntimeError("Matplotlib is required; workspace dependencies use the PlatformIO Python 3.13 interpreter") from error
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.titlesize": 12, "axes.labelsize": 10,
                         "xtick.labelsize": 9, "ytick.labelsize": 9,
                         "pdf.fonttype": 42, "ps.fonttype": 42,
                         "svg.fonttype": "none", "axes.spines.top": False,
                         "axes.spines.right": False})
    fig, axes = plt.subplots(2, 3, figsize=(14, 8.6))
    points = []
    for row_index, node in enumerate(NODES):
        for column, (metric, title, ylabel, nominal) in enumerate(METRICS):
            ax = axes[row_index, column]
            ax.axhline(nominal, color="#A0A0A0", linestyle="--", linewidth=1, zorder=0)
            ax.grid(axis="y", alpha=0.22, zorder=0)
            for device, threshold, label, color, marker, offset in SERIES:
                for rate_index, rate in enumerate(RATES):
                    values = grouped.get((device, threshold, node, rate))
                    if not values:
                        continue
                    measurements = [value[metric] for value in values]
                    mean = sum(measurements) / len(measurements)
                    minimum, maximum = min(measurements), max(measurements)
                    x = rate_index + offset
                    ax.errorbar(x, mean, yerr=[[mean - minimum], [maximum - mean]],
                                fmt=marker, color=color, markersize=5.5,
                                markerfacecolor="white", markeredgewidth=1.4,
                                capsize=3, elinewidth=1.4, zorder=3)
                    ax.scatter([x - 0.022, x, x + 0.022], measurements,
                               s=11, color=color, alpha=0.45, zorder=2)
                    points.append({"node": node, "device": device, "threshold_volts": threshold,
                                   "sampling_rate_hz": rate, "metric": metric,
                                   "capture_means": measurements, "mean": mean,
                                   "minimum": minimum, "maximum": maximum})
            ax.set_xticks(range(3), ["50", "100", "250"])
            ax.set_xlim(-0.43, 2.43)
            ax.set_xlabel("Declared sampling rate (MS/s)")
            ax.set_ylabel(ylabel)
            ax.set_ylim((477, 523) if column < 2 else (47.7, 52.3))
            if row_index == 0:
                ax.set_title(title, pad=10)
        label = "ESP32 GPIO32 · input 0" if node == "ESP32_GPIO32" else "FNIRSI PWM0 · input 1"
        axes[row_index, 0].text(0, 1.20, label, transform=axes[row_index, 0].transAxes,
                                fontsize=11, weight="bold", ha="left")
    handles = [Line2D([], [], marker=marker, color=color, linestyle="none",
                      markerfacecolor="white", markeredgewidth=1.4,
                      markersize=6, label=label) for _, _, label, color, marker, _ in SERIES]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.115),
               ncol=5, frameon=False, fontsize=9)
    fig.suptitle("Paired PWM measurements: FNIRSI Buffer and Kingst", fontsize=16, y=0.985)
    fig.text(0.5, 0.95, "Sources configured nominally to 1 MHz / 50% duty · three captures per setting",
             ha="center", fontsize=11)
    fig.text(0.5, 0.095, "Markers: mean of three capture means. Bars: min–max between captures. Small dots: individual captures.",
             ha="center", fontsize=9)
    fig.text(0.5, 0.075, "Dashed lines: nominal HIGH/LOW 500 ns and duty 50%. Neither device is treated as an absolute time reference.",
             ha="center", fontsize=9)
    fig.text(0.5, 0.055, "Actual windows: FNIRSI 10 ms; Kingst 41.94304 ms at 50 MS/s, 20.97152 ms at 100 MS/s. Complete cycles only.",
             ha="center", fontsize=9)
    fig.text(0.5, 0.035, "CLI captures on the same nodes, taken successively. Declared thresholds and clocks are uncalibrated. Native results are separate.",
             ha="center", fontsize=9)
    fig.subplots_adjust(left=0.075, right=0.985, top=0.865, bottom=0.22, hspace=0.55, wspace=0.26)
    for target in targets[:3]:
        # Exclusive open retains the output guard even if another process races.
        with target.open("xb") as destination:
            fig.savefig(destination, format=target.suffix[1:], dpi=300, facecolor="white")
    plt.close(fig)
    provenance = {
        "purpose": "standalone_scientific_plot_from_existing_CLI_matrix_no_hardware",
        "source_csv": str(source.resolve()), "source_csv_sha256": sha256(source),
        "plot_script_sha256": sha256(Path(__file__)), "python_version": sys.version,
        "matplotlib_version": matplotlib.__version__, "native_data_included": False,
        "interval_definition": "min_max_of_3_capture_means_not_confidence_interval_not_cycle_jitter",
        "nominal_source_frequency_hz": 1000000, "nominal_source_duty_percent": 50,
        "absolute_reference_established": False, "points": points,
        "capture_groups": [dict(device=key[0], threshold_volts=key[1], node=key[2],
                                sampling_rate_hz=key[3], capture_records=values)
                           for key, values in grouped.items()],
        "artifacts": [{"path": str(target.resolve()), "sha256": sha256(target),
                       "bytes": target.stat().st_size} for target in targets[:3]],
    }
    with targets[3].open("x", encoding="utf-8") as destination:
        json.dump(provenance, destination, indent=2)
        destination.write("\n")
    return provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--prefix", type=Path, default=Path("artifacts/report/buffer-comparison"))
    args = parser.parse_args()
    result = export_figure(args.csv, args.prefix)
    print(json.dumps({"artifacts": result["artifacts"], "plotted_groups": len(result["capture_groups"]),
                      "native_data_included": False}))


if __name__ == "__main__":
    main()
