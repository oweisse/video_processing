#!/bin/bash
set -e

COLOR_BLUE="\033[94m"
COLOR_GREEN="\033[92m"
COLOR_YELLOW="\033[93m"
COLOR_RED="\033[91m"
COLOR_BOLD="\033[1m"
COLOR_RESET="\033[0m"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

echo -e "${COLOR_BOLD}${COLOR_BLUE}=== Video Audio Cleaner Setup ===${COLOR_RESET}\n"

OS="$(uname -s)"
echo -e "Detected OS: $OS"

# ---------------------------------------------------------------------------
# 1. FFmpeg
# ---------------------------------------------------------------------------
if ! command -v ffmpeg &> /dev/null; then
    echo -e "${COLOR_YELLOW}FFmpeg not found. Installing...${COLOR_RESET}"
    if [ "$OS" = "Darwin" ]; then
        if ! command -v brew &> /dev/null; then
            echo -e "${COLOR_RED}Error: Homebrew is not installed. Please install it first (https://brew.sh).${COLOR_RESET}"
            exit 1
        fi
        brew install ffmpeg
    elif [ "$OS" = "Linux" ]; then
        sudo apt update && sudo apt install -y ffmpeg
    else
        echo -e "${COLOR_RED}Unsupported OS. Please install FFmpeg manually.${COLOR_RESET}"
        exit 1
    fi
else
    echo -e "${COLOR_GREEN}✔ FFmpeg is already installed.${COLOR_RESET}"
fi

# ---------------------------------------------------------------------------
# 2. Pick a supported Python
#    DeepFilterNet 0.5.6 needs torch/torchaudio < 2.2, which have no wheels for
#    Python 3.13+. Anything in 3.10 - 3.12 works.
# ---------------------------------------------------------------------------
PYTHON_BIN=""
for candidate in python3.12 python3.11 python3.10; do
    if command -v "$candidate" &> /dev/null; then
        PYTHON_BIN="$(command -v "$candidate")"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    if command -v python3 &> /dev/null; then
        if python3 -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info < (3,13) else 1)'; then
            PYTHON_BIN="$(command -v python3)"
        fi
    fi
fi

if [ -z "$PYTHON_BIN" ]; then
    echo -e "${COLOR_RED}Error: no Python between 3.10 and 3.12 found.${COLOR_RESET}"
    echo -e "Install one, for example:  ${COLOR_BOLD}brew install python@3.12${COLOR_RESET}"
    exit 1
fi

echo -e "${COLOR_GREEN}✔ Using Python: $PYTHON_BIN ($("$PYTHON_BIN" --version))${COLOR_RESET}"

# ---------------------------------------------------------------------------
# 3. Virtual environment
# ---------------------------------------------------------------------------
if [ ! -d "venv" ]; then
    echo -e "${COLOR_BLUE}Creating virtual environment in ./venv ...${COLOR_RESET}"
    "$PYTHON_BIN" -m venv venv
else
    echo -e "${COLOR_GREEN}✔ ./venv already exists.${COLOR_RESET}"
fi

echo -e "${COLOR_BLUE}Upgrading pip ...${COLOR_RESET}"
./venv/bin/python -m pip install --upgrade pip --quiet

# ---------------------------------------------------------------------------
# 4. Install the project (with Demucs separation extra)
# ---------------------------------------------------------------------------
echo -e "${COLOR_BLUE}Installing video-audio-cleaner and dependencies (this downloads PyTorch; it takes a few minutes) ...${COLOR_RESET}"
./venv/bin/pip install -e ".[separation]"

# ---------------------------------------------------------------------------
# 5. Smoke test
# ---------------------------------------------------------------------------
echo -e "\n${COLOR_BLUE}Verifying the installation ...${COLOR_RESET}"
./venv/bin/python - <<'PY'
import warnings
warnings.filterwarnings("ignore")
import torch, torchaudio, soundfile           # noqa: F401
from df.enhance import init_df, enhance       # noqa: F401
from df.io import load_audio                  # noqa: F401
import demucs.api                             # noqa: F401
print("  torch", torch.__version__, "| torchaudio", torchaudio.__version__)
print("  DeepFilterNet: OK")
print("  Demucs: OK")
print("  Metal (MPS) available:", torch.backends.mps.is_available())
PY

echo -e "\n${COLOR_BOLD}${COLOR_GREEN}✔ Setup completed successfully!${COLOR_RESET}"
echo -e "\nRun the tool with either:"
echo -e "  ${COLOR_BOLD}./venv/bin/clean-audio --help${COLOR_RESET}"
echo -e "or activate the environment first:"
echo -e "  ${COLOR_BOLD}source venv/bin/activate${COLOR_RESET}"
echo -e "  ${COLOR_BOLD}clean-audio --help${COLOR_RESET}"
echo -e "\nTip: audition settings on a short preview before a full run:"
echo -e "  ${COLOR_BOLD}./venv/bin/clean-audio INPUT.mp4 --output_dir OUT --skip_first_n_sec 8m50s --compare 20 --normalize${COLOR_RESET}"
