"""Control the test firmware without a deliberate DTR/RTS reset.

Closing the port leaves the requested PWM state in place. This command neither
reads flash nor copies the previous ESP32 firmware.
"""
import argparse
import re
import time

import serial


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("ON", "OFF", "STATUS"))
    parser.add_argument("--port", default="COM33")
    args = parser.parse_args()
    port = serial.Serial(port=None, baudrate=115200, timeout=0.2, write_timeout=2)
    port.dtr = False
    port.rts = False
    port.port = args.port
    with port:
        time.sleep(0.2)
        port.reset_input_buffer()
        port.write((args.command + "\n").encode("ascii"))
        port.flush()
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            line = port.readline().decode("ascii", errors="replace").strip()
            if not line:
                continue
            print(line, flush=True)
            match = re.fullmatch(
                r"DLA_TEST gpio=32 frequency=100000 duty=50 enabled=([01]) ready=([01])",
                line,
            )
            if match:
                if match[2] != "1":
                    raise RuntimeError("ESP32 reports failed PWM initialization")
                if args.command != "STATUS" and match[1] != str(int(args.command == "ON")):
                    raise RuntimeError("ESP32 did not confirm the requested output state")
                return
            if line.startswith("ERROR"):
                raise RuntimeError(line)
    raise RuntimeError("No valid PWM status received within four seconds")


if __name__ == "__main__":
    main()
