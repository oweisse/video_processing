"""Single progress renderer used by every long-running pass.

On a TTY this draws a live bar with percentage, position, speed and ETA. When
stdout is redirected it emits a plain line every ``non_tty_interval`` seconds
instead, so piped runs still produce useful logs.
"""
import sys
import time

BAR_LEN = 30
_MIN_ELAPSED_FOR_ETA = 0.5


def format_eta(seconds):
    seconds = max(0, int(round(seconds)))
    hours, rem = divmod(seconds, 3600)
    mins, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


class ProgressReporter:
    def __init__(self, prefix, total_sec=None, stream=None, non_tty_interval=5.0):
        self.prefix = prefix
        self.total_sec = total_sec if (total_sec and total_sec > 0) else None
        self.stream = stream or sys.stdout
        self.non_tty_interval = non_tty_interval
        self.is_tty = self.stream.isatty()
        self.start_time = time.time()
        self._last_plain = 0.0
        self._last_plain_pct = None
        self._last_len = 0
        self._finished = False

    def _rate(self, processed_sec):
        elapsed = time.time() - self.start_time
        if elapsed <= 0 or processed_sec <= 0:
            return None, elapsed
        return processed_sec / elapsed, elapsed

    def _speed(self, processed_sec, speed=None):
        if speed:
            return speed
        rate, _ = self._rate(processed_sec)
        return f"{rate:.1f} sec/sec" if rate else "N/A"

    def _eta(self, processed_sec):
        """Time left, from this pass's own measured rate; None until it is known."""
        if not self.total_sec:
            return None
        rate, elapsed = self._rate(processed_sec)
        if not rate or elapsed < _MIN_ELAPSED_FOR_ETA:
            return None
        return format_eta((self.total_sec - processed_sec) / rate)

    def _write_tty(self, line, newline=False):
        pad = " " * max(0, self._last_len - len(line))
        self.stream.write("\r" + line + pad + ("\n" if newline else ""))
        self.stream.flush()
        self._last_len = 0 if newline else len(line)

    def update(self, processed_sec, speed=None):
        if self._finished:
            return
        pct = None
        if self.total_sec:
            pct = min(100.0, max(0.0, (processed_sec / self.total_sec) * 100.0))
        eta = self._eta(processed_sec)
        eta_part = f", ETA {eta}" if eta else ""

        if self.is_tty:
            speed_str = self._speed(processed_sec, speed)
            if pct is None:
                line = f"{self.prefix}: {processed_sec:.1f}s, {speed_str}"
            else:
                filled = int(BAR_LEN * pct / 100)
                bar = "█" * filled + "-" * (BAR_LEN - filled)
                line = (f"{self.prefix}: [{bar}] {pct:.1f}% "
                        f"({processed_sec:.1f}s/{self.total_sec:.1f}s) "
                        f"{speed_str}{eta_part}")
            self._write_tty(line)
            return

        now = time.time()
        if now - self._last_plain >= self.non_tty_interval:
            self._last_plain = now
            self._last_plain_pct = pct
            if pct is None:
                self.stream.write(f"{self.prefix}: {processed_sec:.1f}s\n")
            else:
                self.stream.write(f"{self.prefix}: {pct:.1f}% "
                                  f"({processed_sec:.1f}s/{self.total_sec:.1f}s){eta_part}\n")
            self.stream.flush()

    def finish(self, speed=None):
        if self._finished:
            return
        self._finished = True
        total = self.total_sec
        if self.is_tty:
            if total:
                bar = "█" * BAR_LEN
                line = (f"{self.prefix}: [{bar}] 100.0% ({total:.1f}s/{total:.1f}s) "
                        f"{self._speed(total, speed)}, "
                        f"took {format_eta(time.time() - self.start_time)}")
            else:
                line = f"{self.prefix}: Done!"
            self._write_tty(line, newline=True)
        else:
            if self._last_plain_pct is not None and self._last_plain_pct >= 99.95:
                return  # the last periodic line already reported completion
            if total:
                self.stream.write(f"{self.prefix}: 100.0% ({total:.1f}s/{total:.1f}s)\n")
            else:
                self.stream.write(f"{self.prefix}: done\n")
            self.stream.flush()
