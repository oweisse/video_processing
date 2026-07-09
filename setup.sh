#!/bin/bash

# ANSI colors for styling
COLOR_BLUE="\033[94m"
COLOR_GREEN="\033[92m"
COLOR_YELLOW="\033[93m"
COLOR_RED="\033[91m"
COLOR_BOLD="\033[1m"
COLOR_RESET="\033[0m"

echo -e "${COLOR_BOLD}${COLOR_BLUE}=== Video Audio Denoising Setup ===${COLOR_RESET}\n"

# 1. Detect Operating System
OS="$(uname -s)"
echo -e "Detected OS: $OS"

# 2. Check & Install FFmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo -e "${COLOR_YELLOW}FFmpeg not found. Installing...${COLOR_RESET}"
    if [ "$OS" = "Darwin" ]; then
        if ! command -v brew &> /dev/null; then
            echo -e "${COLOR_RED}Error: Homebrew is not installed. Please install Homebrew first (https://brew.sh).${COLOR_RESET}"
            exit 1
        fi
        brew install ffmpeg
    elif [ "$OS" = "Linux" ]; then
        sudo apt update && sudo apt install -y ffmpeg
    else
        echo -e "${COLOR_RED}Unsupported OS for automatic FFmpeg installation. Please install FFmpeg manually.${COLOR_RESET}"
        exit 1
    fi
else
    echo -e "${COLOR_GREEN}✔ FFmpeg is already installed.${COLOR_RESET}"
fi

# 3. Check & Install pipx
if ! command -v pipx &> /dev/null; then
    echo -e "${COLOR_YELLOW}pipx not found. Installing...${COLOR_RESET}"
    if [ "$OS" = "Darwin" ]; then
        if ! command -v brew &> /dev/null; then
            echo -e "${COLOR_RED}Error: Homebrew is not installed. Please install Homebrew first (https://brew.sh).${COLOR_RESET}"
            exit 1
        fi
        brew install pipx
    elif [ "$OS" = "Linux" ]; then
        sudo apt update && sudo apt install -y pipx
    else
        echo -e "${COLOR_RED}Unsupported OS for automatic pipx installation. Please install pipx manually.${COLOR_RESET}"
        exit 1
    fi
    pipx ensurepath
    echo -e "${COLOR_YELLOW}Note: pipx path has been configured. You might need to restart your terminal after setup completes.${COLOR_RESET}"
else
    echo -e "${COLOR_GREEN}✔ pipx is already installed.${COLOR_RESET}"
fi

# 4. Check & Install DeepFilterNet via pipx
if ! command -v deepFilter &> /dev/null; then
    echo -e "${COLOR_YELLOW}Installing DeepFilterNet via pipx...${COLOR_RESET}"
    pipx install deepfilternet
else
    echo -e "${COLOR_GREEN}✔ DeepFilterNet is already installed.${COLOR_RESET}"
fi

# 5. Inject soundfile backend into deepfilternet venv
echo -e "${COLOR_BLUE}Injecting soundfile backend into DeepFilterNet environment...${COLOR_RESET}"
pipx inject deepfilternet soundfile

echo -e "\n${COLOR_BOLD}${COLOR_GREEN}✔ Setup completed successfully!${COLOR_RESET}"
echo -e "You can now run the denoising script."
