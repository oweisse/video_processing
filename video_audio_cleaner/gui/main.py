"""clean-audio-gui: start the local server and open the UI.

Default: a native window (pywebview, WKWebView) with native file dialogs and
drag-and-drop of videos. --browser serves the same UI to the default browser
instead (no pywebview needed).
"""
import argparse
import os
import threading
import time
import webbrowser

from . import settings

DEFAULT_PORT = 8787
VIDEO_TYPES = ("Videos (*.mp4;*.mov;*.m4v;*.mkv)", "All files (*.*)")
AUDIO_TYPES = ("Audio (*.mp3;*.m4a;*.aac;*.wav;*.flac;*.aiff;*.ogg)", "All files (*.*)")


def build_parser():
    p = argparse.ArgumentParser(
        prog="clean-audio-gui",
        description="Open the Video Audio Cleaner GUI over clean-audio, clean-audio-tune and "
                    "the outputs they wrote.")
    p.add_argument("--output_dir", default=None,
                   help="Project folder to open (default: the last one used).")
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help=f"Port on 127.0.0.1 (default: {DEFAULT_PORT}).")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--browser", dest="mode", action="store_const", const="browser",
                      help="Open the UI in the default browser instead of a window.")
    mode.add_argument("--no-open", dest="mode", action="store_const", const="none",
                      help="Only run the server (e.g. for `npm run dev`).")
    p.set_defaults(mode="window")
    return p


class NativeDialogs:
    """Native open dialogs for the API, available once the window exists."""

    def __init__(self):
        self.window = None

    def pick(self, kind, start=None):
        import webview
        if self.window is None:
            return None
        directory = os.path.expanduser(start or "~")
        if not os.path.isdir(directory):
            directory = os.path.dirname(directory) if os.path.isdir(
                os.path.dirname(directory)) else os.path.expanduser("~")
        if kind == "dir":
            result = self.window.create_file_dialog(webview.FileDialog.FOLDER,
                                                    directory=directory)
        else:
            result = self.window.create_file_dialog(
                webview.FileDialog.OPEN, directory=directory,
                file_types=AUDIO_TYPES if kind == "audio" else VIDEO_TYPES)
        return result[0] if result else None


def _serve(app, port):
    import uvicorn
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and thread.is_alive() and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise SystemExit(f"The GUI server could not start on port {port} (in use?).")
    return server, thread


def _window(app, url, dialogs):
    try:
        import webview
    except ImportError:
        raise SystemExit("The window needs pywebview: ./venv/bin/pip install -e '.[gui]' "
                         "(or run with --browser).") from None
    bounds = settings.load().get("window_bounds") or {}
    window = webview.create_window(
        "Video Audio Cleaner", url, width=bounds.get("width", 1440),
        height=bounds.get("height", 920), x=bounds.get("x"), y=bounds.get("y"),
        min_size=(1180, 700), background_color="#1c1c1f", text_select=True)
    dialogs.window = window

    def remember_bounds():
        try:
            settings.update(window_bounds={"width": window.width, "height": window.height,
                                           "x": window.x, "y": window.y})
        except Exception:   # noqa: BLE001 — never block closing
            pass

    window.events.closing += remember_bounds
    webview.start()          # runs the Cocoa loop on the main thread until the window closes


def main(argv=None):
    args = build_parser().parse_args(argv)
    from .app import create_app

    output_dir = args.output_dir or settings.load().get("last_output_dir")
    if output_dir and not os.path.isdir(os.path.expanduser(output_dir)):
        output_dir = None
    url = f"http://127.0.0.1:{args.port}/"
    dialogs = NativeDialogs() if args.mode == "window" else None
    app = create_app(output_dir, dialogs=dialogs)
    server, thread = _serve(app, args.port)
    print(f"Video Audio Cleaner GUI on {url}  (Ctrl+C to stop)")
    try:
        if args.mode == "window":
            _window(app, url, dialogs)
        else:
            if args.mode == "browser":
                webbrowser.open(url)
            while thread.is_alive():
                thread.join(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        thread.join(10)


if __name__ == "__main__":
    main()
