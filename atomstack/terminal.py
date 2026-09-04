"""Diagnostic-only terminal frontend. No motion or settings-write command channel."""
import argparse
import json
import queue
import sys
import threading
import time

from .controller import Controller, GuardError
from .diagnostics import Reporter, snapshot
from .protocol import READ_COMMANDS
from .transports import SerialTransport, Simulator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Explicit USB COM port, e.g. COM3")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--seconds", type=float, default=30, help="Observation period; default 30 seconds")
    parser.add_argument("--report", help="JSON snapshot updated once per second")
    parser.add_argument("--interactive", action="store_true", help="Read diagnostics, status or quit from stdin")
    args = parser.parse_args()
    if args.demo == bool(args.port):
        parser.error("Specify exactly one of --port or --demo")
    if not 1 <= args.seconds <= 86400:
        parser.error("--seconds must be between 1 and 86400")
    controller = Controller()
    reporter = Reporter(args.report)
    inputs = queue.Queue()
    if args.interactive:
        def read_input():
            for line in sys.stdin:
                inputs.put(line.strip())
            inputs.put("quit")
        threading.Thread(target=read_input, daemon=True).start()
    controller.attach(Simulator() if args.demo else SerialTransport(args.port), settle=0 if args.demo else 2)
    deadline = time.monotonic() + args.seconds
    observed_ready = False
    result = 0
    last_print = 0
    print("Diagnostics only. Allowed inputs: $I, $$, ?, $#, $G, status, quit.", flush=True)
    try:
        while time.monotonic() < deadline:
            controller.tick()
            reporter.write(controller, args.port or "simulator")
            if controller.ready and not observed_ready:
                observed_ready = True
                print("CONNECTED: " + controller.firmware + "; profile verified; motion locked.", flush=True)
            if not controller.connected:
                print("FAILED: " + controller.message, flush=True)
                result = 1
                break
            if time.monotonic() - last_print >= 5:
                print(json.dumps({k: v for k, v in snapshot(controller, args.port).items()
                                  if k in ("connected", "ready", "state", "machine_position", "rx_count", "tx_count")}), flush=True)
                last_print = time.monotonic()
            try:
                command = inputs.get_nowait()
                if command == "quit":
                    break
                if command == "status":
                    print(json.dumps(snapshot(controller, args.port), indent=2), flush=True)
                elif command in READ_COMMANDS:
                    # Retain only this diagnostic request until the current poll completes.
                    if controller.ready and not controller.pending and not controller.queue:
                        controller.query(command)
                    else:
                        inputs.put(command)
                else:
                    print("Rejected: diagnostic queries only.", flush=True)
            except queue.Empty:
                pass
            time.sleep(0.025)
        if not observed_ready:
            result = 1
        reporter.write(controller, args.port or "simulator", force=True)
        print("PASS: connection remained ready for the observation period." if result == 0 else "FAIL: connection was not verified.", flush=True)
    finally:
        controller.disconnect()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
