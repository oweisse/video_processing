"""Video audio cleaning pipeline."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("video-audio-cleaner")
except PackageNotFoundError:  # running from a checkout that was never installed
    __version__ = "0+unknown"
