#!/bin/bash

# ANSI colors for styling
COLOR_BLUE="\033[94m"
COLOR_GREEN="\033[92m"
COLOR_YELLOW="\033[93m"
COLOR_RED="\033[91m"
COLOR_BOLD="\033[1m"
COLOR_RESET="\033[0m"

echo -e "${COLOR_BOLD}${COLOR_BLUE}=== Real-ESRGAN Video Upscaler Setup ===${COLOR_RESET}\n"

# Detect OS
OS="$(uname -s)"
if [ "$OS" != "Darwin" ]; then
    echo -e "${COLOR_RED}Error: This script is only supported on macOS (Apple Silicon or Intel Mac).${COLOR_RESET}"
    exit 1
fi

# 1. Check for curl/unzip
if ! command -v curl &> /dev/null; then
    echo -e "${COLOR_RED}Error: curl is required but not installed.${COLOR_RESET}"
    exit 1
fi
if ! command -v unzip &> /dev/null; then
    echo -e "${COLOR_RED}Error: unzip is required but not installed.${COLOR_RESET}"
    exit 1
fi

# 2. Check & Install FFmpeg via brew if missing
if ! command -v ffmpeg &> /dev/null; then
    echo -e "${COLOR_YELLOW}FFmpeg not found. Installing via Homebrew...${COLOR_RESET}"
    if ! command -v brew &> /dev/null; then
        echo -e "${COLOR_RED}Error: Homebrew is not installed. Please install Homebrew first (https://brew.sh).${COLOR_RESET}"
        exit 1
    fi
    brew install ffmpeg
else
    echo -e "${COLOR_GREEN}✔ FFmpeg is already installed.${COLOR_RESET}"
fi

# 3. Create bin directory
BIN_DIR="./bin"
TARGET_DIR="${BIN_DIR}/realesrgan-ncnn-vulkan"
mkdir -p "$TARGET_DIR"

# 4. Download Real-ESRGAN macOS release
ZIP_URL="https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-macos.zip"
ZIP_FILE="${BIN_DIR}/realesrgan.zip"

if [ -f "${TARGET_DIR}/realesrgan-ncnn-vulkan" ]; then
    echo -e "${COLOR_GREEN}✔ Real-ESRGAN binary is already set up in ${TARGET_DIR}.${COLOR_RESET}"
else
    echo -e "${COLOR_YELLOW}Downloading Real-ESRGAN-ncnn-vulkan (macOS release)...${COLOR_RESET}"
    # Use curl with a progress bar
    curl -L --progress-bar -o "$ZIP_FILE" "$ZIP_URL"
    
    if [ $? -ne 0 ]; then
        echo -e "${COLOR_RED}Error: Download failed.${COLOR_RESET}"
        rm -f "$ZIP_FILE"
        exit 1
    fi
    
    echo -e "${COLOR_YELLOW}Extracting files to ${TARGET_DIR}...${COLOR_RESET}"
    unzip -q -o "$ZIP_FILE" -d "$TARGET_DIR"
    if [ $? -ne 0 ]; then
        echo -e "${COLOR_RED}Error: Extraction failed.${COLOR_RESET}"
        rm -f "$ZIP_FILE"
        exit 1
    fi
    rm -f "$ZIP_FILE"
    
    # Move nested folder contents up to the main target directory
    SUB_DIR=$(find "$TARGET_DIR" -maxdepth 1 -type d -name "realesrgan-ncnn-vulkan-*" | head -n 1)
    if [ -n "$SUB_DIR" ] && [ -d "$SUB_DIR" ]; then
        mv "$SUB_DIR"/* "$TARGET_DIR"/ 2>/dev/null || true
        rmdir "$SUB_DIR" 2>/dev/null || true
    fi
    
    # Make executable
    chmod +x "${TARGET_DIR}/realesrgan-ncnn-vulkan"
    if [ $? -ne 0 ]; then
        echo -e "${COLOR_RED}Error: Failed to make binary executable.${COLOR_RESET}"
        exit 1
    fi
    
    echo -e "${COLOR_GREEN}✔ Real-ESRGAN successfully set up!${COLOR_RESET}"
fi

echo -e "\n${COLOR_BOLD}${COLOR_GREEN}✔ Setup completed successfully!${COLOR_RESET}"
echo -e "You can now run: python3 improve_video.py --help"
