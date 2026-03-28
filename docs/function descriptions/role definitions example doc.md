  
― Everything is expressed in a Harmony-style block (YAML-ish, but comments sprinkled so it’s self-explaining).  
― Edit the model: lines to the concrete checkpoints you actually deploy.  
― Pipelines are assembled by flags; at runtime your router just picks the first pipeline whose provides: set is a superset of the request’s needs: set.

\# ─────────────────────────────────────────────────────────  
\# ROLES (single-purpose workers you can scale independently)  
\# ─────────────────────────────────────────────────────────  
roles:  
  \# ─ Text & Reasoning ────────────────────────────────────  
  LLM:  
    model: gpt-4o-mini  \# ⬅︎ swap for any text-+-tool model  
    provides:  \[text, reasoning, tool\_call\]  
    consumes:  \[text\]  
  \# ─ Vision ──────────────────────────────────────────────  
  VisionCaptioner:  
    model: clip-vit-L/14  \# or gpt-4o, blip2-flan, etc.  
    provides:  \[image\_caption\]  
    consumes:  \[image\]  
  ImageEmbedder:  
    model: clip-vit-L/14  
    provides:  \[image\_embed\]  
    consumes:  \[image\]  
  \# ─ Audio ───────────────────────────────────────────────  
  ASR:  
    model: whisper-large-v3  
    provides:  \[asr\]  
    consumes:  \[audio\]  
  TTS:  
    model: xtts-v2     \# or bark, elevenlabs  
    provides:  \[tts\]  
    consumes:  \[text\]  
  \# ─ Video / Stream helpers ──────────────────────────────  
  VAD:  
    model: silero-vad  \# voice-activity / turn detection  
    provides:  \[speech\_turn\]  
    consumes:  \[audio\]  
  KeyframeDetector:  
    model: scenedetect-r11  
    provides:  \[keyframe\]  
    consumes:  \[video\]  
\# ─────────────────────────────────────────────────────────  
\# PIPELINES (ordered chains; router selects by flags)  
\# ─────────────────────────────────────────────────────────  
pipelines:  
  text\_only:  
    needs:     \[text\]  
    stages:    \[LLM\]

  image\_plus\_text:  
    needs:     \[text, image\]  
    stages:    \[VisionCaptioner, LLM\]

  audio\_streaming:  
    \# buffer audio → whenever VAD triggers, flush to ASR → LLM  
    needs:     \[audio\]  
    stages:    \[VAD, ASR, LLM\]

  video\_streaming:  
    \# detect keyframe or silence → caption that frame \+ ASR → LLM  
    needs:     \[video\]  
    stages:    \[KeyframeDetector, VisionCaptioner, VAD, ASR, LLM\]

  voice\_chat\_full\_duplex:  
    \# like audio\_streaming, but ends with a spoken response  
    needs:     \[audio, tts\]  
    stages:    \[VAD, ASR, LLM, TTS\]

  multimodal\_all\_in\_one:  
    \# for models such as gpt-4o / Gemini 1.5 Pro  
    needs:     \[text|image|audio\]   \# any subset  
    stages:    \[LLM\]                \# because the model itself is multimodal  
\# ─────────────────────────────────────────────────────────  
\# ROUTING LOGIC (pseudo)  
\# ─────────────────────────────────────────────────────────  
\# 1\. Client request advertises needs, e.g.  needs=\[audio, tts\]  
\# 2\. Pick first pipeline whose “needs ⊆ provides(stages)”  
\# 3\. Execute each role in order, passing intermediate artifacts

\---