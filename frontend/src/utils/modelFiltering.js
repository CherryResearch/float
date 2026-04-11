const normalizeModelName = (value) => {
  if (typeof value !== "string") return "";
  return value.trim();
};

const includesAny = (haystack, needles) =>
  needles.some((needle) => haystack.includes(needle));

export const isGemmaFamilyModelName = (name) => {
  const value = normalizeModelName(name).toLowerCase();
  return value.startsWith("gemma-");
};

export const isLikelyEmbeddingModelName = (name) => {
  const value = normalizeModelName(name).toLowerCase();
  if (!value) return false;
  return includesAny(value, [
    "sentence-transformer",
    "sentence_transformer",
    "minilm",
    "mpnet",
    "instructor",
    "nomic-embed",
    "text-embedding",
    "embedding",
    "bge",
    "e5",
    "gte",
    "sbert",
    "retriever",
    "reranker",
    "-embed",
    "_embed",
  ]);
};

const isLikelySpeechModelName = (name) => {
  const value = normalizeModelName(name).toLowerCase();
  if (!value) return false;
  return includesAny(value, ["whisper", "wav2vec", "speech"]);
};

const isLikelyVisionModelName = (name) => {
  const value = normalizeModelName(name).toLowerCase();
  if (!value) return false;
  return includesAny(value, ["clip", "pixtral", "paligemma", "llava", "siglip", "blip"]);
};

const isLikelyTtsModelName = (name) => {
  const value = normalizeModelName(name).toLowerCase();
  if (!value) return false;
  return includesAny(value, ["tts", "kokoro", "kitten", "bark"]);
};

const isLikelyVoiceModelName = (name) => {
  const value = normalizeModelName(name).toLowerCase();
  if (!value) return false;
  return includesAny(value, ["voxtral"]);
};

export const isModelRelevantForField = (field, modelName) => {
  const name = normalizeModelName(modelName);
  if (!name) return false;

  // Embedding checkpoints are configured separately via RAG embedding settings;
  // keep them out of the general model dropdowns by default.
  if (isLikelyEmbeddingModelName(name)) return false;

  const isSpeech = isLikelySpeechModelName(name);
  const isVision = isLikelyVisionModelName(name);
  const isTts = isLikelyTtsModelName(name);
  const isVoice = isLikelyVoiceModelName(name);

  if (field === "transformer_model") {
    return !(isSpeech || isVision || isTts || isVoice);
  }
  if (field === "stt_model") return isSpeech;
  if (field === "vision_model") return isVision;
  if (field === "tts_model") return isTts;
  if (field === "voice_model") return isVoice;
  return true;
};

export const filterAvailableModelsForField = (
  field,
  models,
  { includeAll = false } = {},
) => {
  if (!Array.isArray(models)) return [];
  const out = [];
  const seen = new Set();
  for (const raw of models) {
    const name = normalizeModelName(raw);
    if (!name) continue;
    if (!includeAll && !isModelRelevantForField(field, name)) continue;
    if (seen.has(name)) continue;
    seen.add(name);
    out.push(name);
  }
  return out;
};
