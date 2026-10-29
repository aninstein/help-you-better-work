"""Complete, natural speech units, kept private until publication validation."""

import re


class SentenceBuffer:
    def __init__(self) -> None:
        self.text = ""
        self.pending = ""

    def push(self, text: str, final: bool = False) -> list[str]:
        self.text += text
        if len(self.text) + len(self.pending) > 4096:
            raise ValueError("response_buffer_limit")
        result = []

        def complete(unit: str) -> None:
            if self.pending:
                if len((self.pending + " " + unit).split()) <= 32:
                    unit = self.pending + " " + unit
                else:
                    result.append(self.pending)
                self.pending = ""
            # A tiny opening such as "I'd pick Arrival." belongs with its
            # explanation, so TTS can speak it with one coherent intonation.
            if len(unit.split()) < 8 and not final:
                self.pending = unit
            else:
                result.append(unit)

        # Commas and token counts do not end a speech unit.
        while match := re.search(r"[.!?][\"')]*\s+", self.text):
            end = match.end()
            unit = self.text[:end].strip()
            if re.search(r"\b(?:Mr|Mrs|Dr|Prof|St|e\.g|i\.e|U\.S)\.$", unit, re.I):
                # Wait for the next sentence boundary rather than reading half a name.
                following = re.search(r"[.!?]\s+", self.text[end:])
                if not following:
                    break
                end += following.end()
                unit = self.text[:end].strip()
            complete(unit)
            self.text = self.text[end:]
        if final and self.text.strip():
            complete(self.text.strip())
            self.text = ""
        if final and self.pending:
            result.append(self.pending)
            self.pending = ""
        return result
