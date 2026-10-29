"""Discover SKILL.md metadata, then read instructions/references on demand."""

import shutil
from typing import Any

import yaml

from app.agent.tool_config import ToolConfig


class SkillCatalog:
    def __init__(self, config: ToolConfig) -> None:
        self.config = config
        self.items: dict[str, dict[str, Any]] = {}
        for directory in config.skill_dirs:
            root = config.resolve(directory)
            if not root.is_dir():
                continue
            for path in sorted(root.glob("*/SKILL.md")):
                try:
                    source = path.read_text(encoding="utf-8")
                    parts = source.split("---", 2)
                    meta = yaml.safe_load(parts[1]) if source.startswith("---\n") else {}
                    if not isinstance(meta, dict):
                        continue
                    name = str(meta.get("name", path.parent.name))
                    metadata = meta.get("metadata") or {}
                    if not isinstance(metadata, dict):
                        continue
                    requires = metadata.get("requires") or {}
                    if not isinstance(requires, dict) or any(
                        not isinstance(requires.get(key, []), list)
                        or any(not isinstance(value, str) for value in requires.get(key, []))
                        for key in ["bins", "skills"]
                    ):
                        continue
                    self.items.setdefault(
                        name,
                        {
                            "name": name,
                            "description": str(meta.get("description", "")),
                            "path": path,
                            "requires": requires,
                        },
                    )
                except (OSError, ValueError, IndexError, yaml.YAMLError):
                    continue

    def inventory(self) -> list[dict[str, Any]]:
        return [
            {
                "name": item["name"],
                "description": item["description"],
                "path": str(item["path"]),
                "requires": item["requires"],
                "missing_bins": [
                    b for b in item["requires"].get("bins", []) if not shutil.which(b)
                ],
                "missing_skills": [
                    s for s in item["requires"].get("skills", []) if s not in self.items
                ],
            }
            for item in self.items.values()
        ]

    def prompt(self) -> str:
        return "\n".join(
            f"- {x['name']} ({x['path'].parent}): {x['description']}" for x in self.items.values()
        )

    def read(self, name: str, resource: str = "SKILL.md") -> str:
        item = self.items.get(name)
        if not item:
            raise ValueError(f"Skill is not installed: {name}")
        root = item["path"].parent.resolve()
        path = (root / resource).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Skill resource must be an existing file inside this skill")
        text = path.read_text(encoding="utf-8")
        return text[: self.config.max_output_chars] + (
            "\n[Skill resource truncated]" if len(text) > self.config.max_output_chars else ""
        )
