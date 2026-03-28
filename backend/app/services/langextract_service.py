from typing import Any, Sequence

import langextract as lx
from langextract import data


class LangExtractService:
    """Wrap ``lx.extract`` for processing text or conversation logs."""

    def __init__(self, prompt: str, examples: Sequence[data.ExampleData]):
        self.prompt = prompt
        self.examples = list(examples)

    def _to_dict(self, extraction: data.Extraction) -> dict[str, Any]:
        return {
            "class": extraction.extraction_class,
            "text": extraction.extraction_text,
            "attributes": extraction.attributes or {},
        }

    def _extract(self, text: str) -> list[dict[str, Any]]:
        doc = lx.extract(
            text,
            prompt_description=self.prompt,
            examples=self.examples,
        )
        return [self._to_dict(e) for e in doc.extractions or []]

    def from_text(self, text: str) -> list[dict[str, Any]]:
        """Extract structured data from a raw transcript."""
        return self._extract(text)

    def from_conversation(
        self, messages: Sequence[dict[str, str]]
    ) -> list[dict[str, Any]]:
        """Extract from messages with ``speaker`` and ``text`` fields."""
        parts = []
        for msg in messages:
            parts.append(f"{msg.get('speaker', '')}: {msg.get('text', '')}")
        return self._extract("\n".join(parts))
