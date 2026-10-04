# DLA-32 Investigation Tools

These scripts are optional helpers used to reproduce the USB inspection and protocol analysis behind the overlay. They are not required to apply or build the patches.

Run them from the repository root so their relative paths resolve correctly:

```sh
python tools/inspect_libusb.py
python tools/inspect_usb_devices.py
python tools/parse_dumps.py
python tools/analyze_new_dumps.py
python tools/run_analysis.py
```

The inspection scripts expect a local PXView package and, where applicable, the Python `pyusb` package. Capture-analysis scripts use the protocol utilities under `DLNA32/` and may require local capture files that are intentionally excluded from version control.

## Attribution

These tools were generated with assistance from **GitHub Copilot**. The
underlying model identifier was not exposed by the workspace session. The
scripts were human-reviewed and used during hardware validation.
