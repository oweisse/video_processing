#!/bin/bash
set -e

echo "=== Initializing Python AI Virtual Environment (venv_ai) ==="
python3 -m venv venv_ai
source venv_ai/bin/activate

echo "=== Upgrading pip ==="
python3 -m pip install --upgrade pip

echo "=== Installing PyTorch and Torchvision ==="
python3 -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

echo "=== Installing Hugging Face Diffusers & Transformers ==="
python3 -m pip install diffusers transformers accelerate opencv-python-headless

echo "=== Installing GFP-GAN Dependencies ==="
python3 -m pip install setuptools
python3 -m pip install basicsr
python3 -m pip install facexlib
python3 -m pip install gfpgan

echo "=== Setup Complete! ==="
echo "To activate the environment, run: source venv_ai/bin/activate"
