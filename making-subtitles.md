# Making subtitles locally for mixed Hebrew/English videos

Target machine: MacBook Pro, M5 Pro, 48 GB RAM. Everything below runs on the Mac; the only network use is downloading the tools and models once.

## Overview

The pipeline has three outputs per video:

| Output | How it is made | Tool |
|---|---|---|
| `video.mixed.srt` (as spoken) | Speech-to-text | whisper.cpp |
| `video.en.srt` (fully English) | Whisper's built-in translation, or the script | whisper.cpp or Ollama |
| `video.he.srt` (fully Hebrew) | Local LLM translates the mixed SRT | Ollama + `translate_srt.py` |

## One-time setup

```bash
brew install ffmpeg whisper-cpp ollama

# Whisper model (~3 GB)
curl -L -o ggml-large-v3.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin

# Translation model (~17 GB)
ollama serve &            # or launch the Ollama app
ollama pull gemma3:27b
```

## Step 1: Mixed subtitles

```bash
# Extract audio in the format Whisper wants
ffmpeg -i video.mp4 -ar 16000 -ac 1 -c:a pcm_s16le audio.wav

# Transcribe to video.mixed.srt
whisper-cli -m ggml-large-v3.bin -l auto -osrt -of video.mixed -f audio.wav
```

### The language-mixing problem

Whisper assumes one language at a time, and whisper.cpp detects the language once, from the start of the file. If the video opens in English, Hebrew sections may come out translated or garbled, and vice versa.

Recommendation: run the plain command above on one video first and see how bad the problem is. If it is bad, pick by video type:

- **Mostly Hebrew:** use the ivrit.ai fine-tuned model (`ivrit-ai/whisper-large-v3-turbo-ggml` on Hugging Face) with `-l he`. It roughly halves the Hebrew error rate compared with the stock turbo model (about 14% vs 29% word error rate on one benchmark). English stretches may suffer, since it was tuned on Hebrew.
- **Frequent switching:** use the Python library `faster-whisper` with `multilingual=True`, which re-detects the language on every segment. It runs on CPU on a Mac, so it is slower, but fine for home videos.

## Step 2: Fully English subtitles

There are two routes. Run both on one video and keep whichever reads better.

**Route A, from the audio (Whisper):**

```bash
whisper-cli -m ggml-large-v3.bin -l he -tr -osrt -of video.en -f audio.wav
```

Forcing `-l he` with `-tr` usually translates the Hebrew parts and passes the English through, but check the output. Use the full large-v3 model here, not a turbo model, because turbo was not trained for translation.

**Route B, from the mixed text (script):**

```bash
python3 translate_srt.py video.mixed.srt video.en.srt English
```

## Step 3: Fully Hebrew subtitles

Whisper only translates into English, so this step uses a local LLM on the mixed SRT:

```bash
python3 translate_srt.py video.mixed.srt video.he.srt Hebrew
```

`translate_srt.py` copies the numbering and timestamps untouched and sends only the text to Ollama, one subtitle at a time, with the previous three subtitles as context. It needs nothing beyond Python itself.

## Model choice for translation

- **Starting point:** Gemma 3 27B (`gemma3:27b`). It handles Hebrew reasonably and fits easily in 48 GB.
- **If the Hebrew sounds stiff:** try DictaLM 3.0, a Hebrew-focused model released in early 2026. Its Ollama packaging is unverified; it may need a GGUF build pulled from Hugging Face.
- Swapping models is a one-line change (`MODEL`) at the top of the script.

## Caveats

- The script's SRT parsing was tested, but the script has not been run end-to-end against Ollama. Try it on a short clip first.
- Local Hebrew translation is a notch below the best cloud models, mainly in gender agreement and casual phrasing. Plan on a quick skim of the output.
- A translated subtitle can come out longer than the original and may be tight for its time slot. Timestamps are never changed.

## Alternative without the terminal

MacWhisper is a GUI app that wraps the same Whisper models and runs locally. It gives less control over the mixed-language handling.
