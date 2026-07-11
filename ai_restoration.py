import os
import sys
import time
import shutil
import urllib.request

# 1. Apply runtime patch for torchvision functional_tensor removal (basicsr dependency bug)
import types
import torchvision
import torchvision.transforms.functional as trans_f
try:
    import torchvision.transforms.functional_tensor
except ImportError:
    m = types.ModuleType('torchvision.transforms.functional_tensor')
    m.rgb_to_grayscale = trans_f.rgb_to_grayscale
    sys.modules['torchvision.transforms.functional_tensor'] = m

import torch
import cv2
from PIL import Image

def download_file(url, dest_path, desc="file"):
    if os.path.exists(dest_path):
        return
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    print(f"Downloading {desc} weights from {url}...", flush=True)
    
    # Download with a simple progress reporting
    def progress_hook(count, block_size, total_size):
        percent = int(count * block_size * 100 / total_size)
        sys.stdout.write(f"\rDownloading: {percent}%")
        sys.stdout.flush()
        
    urllib.request.urlretrieve(url, dest_path, reporthook=progress_hook)
    print("\nDownload complete!", flush=True)

# Set MPS memory watermark ratio to 0.0 to allow PyTorch to utilize the full unified memory pool
os.environ["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] = "0.0"

def get_device_and_dtype():
    if torch.backends.mps.is_available():
        # MPS works best with float32 for certain Stable Diffusion ops
        return "mps", torch.float32
    elif torch.cuda.is_available():
        return "cuda", torch.float16
    else:
        return "cpu", torch.float32

def enhance_faces_gfpgan(input_dir, output_dir, limit_frames=None, progress_callback=None):
    """
    Option 1: Face Restoration using GFP-GAN.
    Enhances faces on already upscaled frames.
    """
    os.makedirs(output_dir, exist_ok=True)
    
    # Check/Download GFP-GAN weights
    weights_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights")
    gfpgan_path = os.path.join(weights_dir, "GFPGANv1.4.pth")
    download_file(
        "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth",
        gfpgan_path,
        "GFP-GAN v1.4"
    )
    
    # Import GFP-GAN after patch has been applied
    from gfpgan import GFPGANer
    
    device, _ = get_device_and_dtype()
    # Initialize GFPGANer
    # Use bg_upsampler=None because we already upscaled the background using Real-ESRGAN
    restorer = GFPGANer(
        model_path=gfpgan_path,
        upscale=1,
        arch='clean',
        channel_multiplier=2,
        bg_upsampler=None,
        device=torch.device(device)
    )
    
    files = sorted([f for f in os.listdir(input_dir) if f.endswith(".png")])
    if limit_frames is not None:
        files = files[:limit_frames]
        
    total = len(files)
    for idx, filename in enumerate(files):
        img_path = os.path.join(input_dir, filename)
        out_path = os.path.join(output_dir, filename)
        
        # Read image
        img = cv2.imread(img_path)
        if img is None:
            continue
            
        # Enhance faces
        _, _, restored_img = restorer.enhance(
            img,
            has_aligned=False,
            only_center_face=False,
            paste_back=True
        )
        
        # Save output
        cv2.imwrite(out_path, restored_img)
        
        if progress_callback:
            progress_callback(idx + 1, total)

def tiled_upscale(pipe, image, prompt, n_prompt, tile_size=512, overlap=64):
    import numpy as np
    w, h = image.size
    
    if w <= tile_size or h <= tile_size:
        return pipe(
            prompt=prompt,
            negative_prompt=n_prompt,
            image=image,
            control_image=image,
            strength=0.30,
            controlnet_conditioning_scale=1.0,
            num_inference_steps=20,
            guidance_scale=7.5
        ).images[0]

    canvas = np.zeros((h, w, 3), dtype=np.float32)
    weight_map = np.zeros((h, w, 1), dtype=np.float32)
    
    # Create feather mask for a single tile
    tile_mask = np.ones((tile_size, tile_size, 1), dtype=np.float32)
    for i in range(overlap):
        val = i / overlap
        tile_mask[i, :, :] *= val      # top edge
        tile_mask[-1-i, :, :] *= val   # bottom edge
        tile_mask[:, i, :] *= val      # left edge
        tile_mask[:, -1-i, :] *= val   # right edge
        
    stride = tile_size - overlap
    
    # Calculate grid coordinates
    y_coords = list(range(0, h - tile_size, stride))
    if not y_coords or y_coords[-1] + tile_size < h:
        y_coords.append(h - tile_size)
    x_coords = list(range(0, w - tile_size, stride))
    if not x_coords or x_coords[-1] + tile_size < w:
        x_coords.append(w - tile_size)
        
    total_tiles = len(y_coords) * len(x_coords)
    tile_idx = 0
    
    for y in y_coords:
        for x in x_coords:
            tile = image.crop((x, y, x + tile_size, y + tile_size))
            
            # Process tile through Stable Diffusion
            processed_tile = pipe(
                prompt=prompt,
                negative_prompt=n_prompt,
                image=tile,
                control_image=tile,
                strength=0.30,
                controlnet_conditioning_scale=1.0,
                num_inference_steps=20,
                guidance_scale=7.5
            ).images[0]
            
            tile_arr = np.array(processed_tile, dtype=np.float32)
            
            canvas[y:y+tile_size, x:x+tile_size, :] += tile_arr * tile_mask
            weight_map[y:y+tile_size, x:x+tile_size, :] += tile_mask
            
            tile_idx += 1
            
    weight_map = np.clip(weight_map, 1e-5, None)
    final_arr = np.clip(canvas / weight_map, 0, 255).astype(np.uint8)
    return Image.fromarray(final_arr)

def upscale_generative_sd(input_dir, output_dir, scale, limit_frames=None, progress_callback=None):
    """
    Option 2: Generative Upscaling using Stable Diffusion + ControlNet Tile.
    Upscales frames using overlapping tiles to prevent memory issues.
    """
    os.makedirs(output_dir, exist_ok=True)
    
    from diffusers import StableDiffusionControlNetImg2ImgPipeline, ControlNetModel
    
    device, dtype = get_device_and_dtype()
    
    print("Initializing Stable Diffusion + ControlNet Tile pipeline...", flush=True)
    controlnet = ControlNetModel.from_pretrained(
        "lllyasviel/control_v11f1e_sd15_tile",
        torch_dtype=dtype
    )
    pipe = StableDiffusionControlNetImg2ImgPipeline.from_pretrained(
        "runwayml/stable-diffusion-v1-5",
        controlnet=controlnet,
        torch_dtype=dtype,
        safety_checker=None
    )
    pipe.to(device)
    
    pipe.enable_attention_slicing()
    pipe.enable_vae_tiling()
    pipe.enable_vae_slicing()
        
    files = sorted([f for f in os.listdir(input_dir) if f.endswith(".png")])
    if limit_frames is not None:
        files = files[:limit_frames]
        
    total = len(files)
    prompt = "high resolution, sharp details, realistic skin texture, restored film, sharp focus"
    n_prompt = "blurry, low quality, waxy, watercolor, cartoon, distorted, compression artifacts"
    
    for idx, filename in enumerate(files):
        img_path = os.path.join(input_dir, filename)
        out_path = os.path.join(output_dir, filename)
        
        init_image = Image.open(img_path).convert("RGB")
        w, h = init_image.size
        
        # Calculate target size
        target_w = w * scale
        target_h = h * scale
        
        # To balance memory usage and generation speed, perform SD generation at max 2x scale
        sd_scale = min(scale, 2)
        sd_w = w * sd_scale
        sd_h = h * sd_scale
        
        resized_image = init_image.resize((sd_w, sd_h), Image.Resampling.LANCZOS)
        
        # Run Tiled SD Upscaling
        output_image = tiled_upscale(
            pipe=pipe,
            image=resized_image,
            prompt=prompt,
            n_prompt=n_prompt,
            tile_size=512,
            overlap=64
        )
        
        # If the requested scale is larger than 2, resize the output of SD to the final target size
        if scale > sd_scale:
            output_image = output_image.resize((target_w, target_h), Image.Resampling.LANCZOS)
            
        output_image.save(out_path)
        
        if progress_callback:
            progress_callback(idx + 1, total)
