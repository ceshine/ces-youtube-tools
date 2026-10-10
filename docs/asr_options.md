# ASR options for the audio transcript provider

Options for a future `TranscriptProvider` that transcribes a video's audio when subtitles are missing (for example
the unlisted membership perk videos with no `en-orig` captions, see [implementation_notes.md](implementation_notes.md)).
Not needed for the first version.

**Status: a survey, not test results.** Written on 2026-10-09 from general knowledge, without searching or running
anything. Model names, accuracy rankings, memory use and prices change quickly. Check the current
[Open ASR Leaderboard](https://huggingface.co/spaces/hf-audio/open_asr_leaderboard) and each project's documentation
before choosing, and move confirmed findings to `implementation_notes.md` with a date and a recheck command.

Claude cannot do this step: it has no audio input (see "Claude cannot transcribe audio" in the implementation notes).

## Requirements

| Requirement | Why |
| --- | --- |
| Segment-level timestamps (word-level is a bonus) | Guides link to moments in the video. |
| Long recordings (3-hour livestreams) | Needs built-in chunking or a model that handles long audio. |
| English first | Other languages are a bonus. |
| Speaker labels (optional) | Clearer guides for multi-host shows. |
| Local processing preferred | Members-only audio is paid content. Uploading it to a hosted service is a privacy and terms question. |

## Hardware on the development machine

Checked on 2026-10-09 with `nvidia-smi` and `ffmpeg -version`:

- NVIDIA GeForce RTX 4060 Ti, 16 GB VRAM, driver 580.178.04 (CUDA 13.0).
- `ffmpeg` is installed. yt-dlp needs it to extract audio (`-x`), and most ASR tools use it to decode and resample.

16 GB is enough for every local option below except the largest general audio models.

## Local options (open weights)

| Option | Strengths | Weaknesses | Fits 16 GB |
| --- | --- | --- | --- |
| **NVIDIA Parakeet** (TDT/CTC, ~0.6B) | Near the top of the Open ASR Leaderboard for English, very fast on GPU, word timestamps | English-focused (check for multilingual versions); NVIDIA NeMo is a heavy dependency | Yes, easily |
| **NVIDIA Canary** | Multilingual, can also translate | Larger and slower than Parakeet; same NeMo dependency | Yes |
| **faster-whisper** (Whisper `large-v3-turbo` / `large-v3` on CTranslate2) | Mature, simple Python API, voice-activity filtering, segment and word timestamps, many languages | Whisper can repeat itself or invent text during silence and music | Yes |
| **WhisperX** | faster-whisper plus precise word timing and **speaker diarization** (pyannote) | Diarization models need a Hugging Face token and accepting the model terms; more moving parts | Yes |
| **whisper.cpp** | No Python dependencies, runs on CPU or GPU | Command-line integration through files; fewer features than WhisperX | Yes (CPU fallback too) |
| **Distil-Whisper** | Faster, smaller Whisper | Mostly English; somewhat less accurate | Yes |
| **Mistral Voxtral** | Open-weight speech model that can also answer questions about audio | Newer; check timestamp support | Small versions only |
| **Moonshine** | Tiny and fast, built for devices | Less accurate on long, noisy audio | Yes |
| **Vosk / Kaldi**, **wav2vec2 / MMS** | Offline, lightweight, or research-oriented | Clearly less accurate than the options above | Yes |

## Hosted options

| Service | Notes |
| --- | --- |
| **Deepgram**, **AssemblyAI** | Specialist ASR services: long audio, diarization, timestamps, fast turnaround. |
| **OpenAI** (`gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, `whisper-1`) | Simple API with a file-size limit, so a 3-hour stream has to be split into chunks. |
| **ElevenLabs Scribe**, **Speechmatics**, **Rev.ai** | Specialist services, several with good diarization. |
| **Google (Chirp), AWS Transcribe, Azure Speech** | Large cloud providers. Worth it if you already use one of them. |
| **Groq** | Hosted Whisper, run very fast. |
| **Gemini** | General model with audio input. Timestamps are less reliable than from dedicated ASR. |

Prices are per audio hour and change often, so compare them when choosing.

## Suggested path

1. **Start with faster-whisper (`large-v3-turbo`) on the GPU.** It has the simplest install (`pip`-level, no NeMo)
   and a Python API that returns timestamped segments, which map directly to `Transcript`. It runs much faster than
   real time on this card. Use its voice-activity filter to reduce hallucinations during music and silence.
2. **Try Parakeet** if accuracy or speed on English needs to be better. It's worth trying alongside, since it ranks
   at or near the top of the Open ASR Leaderboard.
3. **Add WhisperX diarization** only if guides for multi-host shows need speaker labels.
4. **Keep hosted APIs as the fallback** for machines without a GPU. They mean uploading members-only audio.

## Fit with the pipeline

```
yt-dlp -x --audio-format m4a (cookies for members-only)  ─►  ASR backend  ─►  Transcript segments
```

- One `TranscriptProvider` per backend (for example `audio-faster-whisper`, `audio-parakeet`), chosen by
  `transcript.provider` in the config. Backend settings go in a per-provider table, like `[guide.options.<name>]`.
- A fallback provider can wrap two others: subtitles first, audio when no subtitle track exists.
- Cache the downloaded audio and the transcript in the work directory, because transcribing 3 hours is the
  slowest step.
- ASR libraries pull in CUDA-enabled PyTorch or CTranslate2. Make them an optional extra
  (`ces-youtube-tools[asr]`), so the default install stays light.

## To verify before building

- faster-whisper and PyTorch versions that work with CUDA 13 / driver 580 on this machine (CUDA library versions
  bundled in the wheels).
- Real speed and VRAM use on a 3-hour TYT stream, for each candidate.
- Accuracy against YouTube's own captions on a video that has both.
- Whether diarization is worth its setup cost for the guides.
