"""Handles embedding logic."""

import logging

from sentence_transformers import SentenceTransformer

DEFAULT_MODEL = "sentence-transformers/all-mpnet-base-v2"

logger = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(self, model_type: str = DEFAULT_MODEL):
        self.model_type = model_type
        logger.info("Loading embedding model: %s", self.model_type)
        self.model = SentenceTransformer(self.model_type)

    def embed_text(self, text: str):
        """Return the embedding vector for the given text."""

        return self.model.encode(text).tolist()
