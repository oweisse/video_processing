"""Stands in for clean-audio in GUI job tests: emits a scripted JSON event stream.

  fake_tool.py ok        start, step, progress, message, result, done; exit 0
  fake_tool.py fail      start, error message, error; exit 1
  fake_tool.py slow      start, step, then waits; SIGINT → error event, exit 130
"""
import json
import signal
import sys
import time


def emit(event, **fields):
    sys.stdout.write(json.dumps({"v": 1, "event": event, **fields}) + "\n")
    sys.stdout.flush()


def main(mode):
    emit("start", tool="fake", argv=sys.argv[1:], pid=0, ts="now")
    if mode == "ok":
        emit("step", index=1, total=1, title="Working")
        emit("progress", step=1, prefix="Work", processed_sec=1.0, total_sec=2.0,
             speed=None, eta_sec=1.0)
        emit("message", level="warning", text="careful")
        print("not json, ignored", flush=True)
        sys.stderr.write("stderr chatter\n")
        emit("result", output="/x/out.mp4", sidecar="/x/out.params.json", replaced=False,
             results={"final_lufs": -17.0})
        emit("done", returncode=0, elapsed_sec=0.1)
        return 0
    if mode == "fail":
        emit("message", level="error", text="it broke")
        emit("error", text="it broke", returncode=1)
        return 1

    def stop(signum, frame):
        emit("error", text="Interrupted", returncode=130)
        sys.exit(130)

    signal.signal(signal.SIGINT, stop)
    emit("step", index=1, total=2, title="Waiting")
    time.sleep(60)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
