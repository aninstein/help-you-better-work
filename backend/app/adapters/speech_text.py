"""Convert rendered Markdown to complete spoken text without rewriting the answer."""

import html
from html.parser import HTMLParser

from markdown_it import MarkdownIt


class PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def spoken_text(markdown: str) -> str:
    parser = MarkdownIt().enable("table")
    lines: list[str] = []
    for token in parser.parse(markdown):
        if token.type == "inline":
            text = ""
            for child in token.children or []:
                if child.type in ["text", "code_inline", "image"]:
                    text += child.content
                elif child.type in ["softbreak", "hardbreak"]:
                    text += " "
            lines.append(text)
        elif token.type in ["fence", "code_block"]:
            lines.append(token.content.strip())
        elif token.type == "html_block":
            plain = PlainText()
            plain.feed(token.content)
            lines.append("".join(plain.parts))
    return html.unescape("\n\n".join(line for line in lines if line.strip())).strip()
