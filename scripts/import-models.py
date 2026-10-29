#!/usr/bin/env python3
"""Probe reference endpoints and write only .env model configuration."""

import argparse
import asyncio
import json
import re
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def priority(name: str) -> int:
    return next(
        (
            i
            for i, prefix in enumerate(["DeepSeek", "Kimi", "GPT", "Seed"])
            if name.startswith(prefix)
        ),
        4,
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--primary", default="", help="Primary endpoint ID or display name")
    args = parser.parse_args()
    match = re.search(r"`(.*?)`", args.source.read_text(), re.DOTALL)
    if not match:
        raise SystemExit("No built-in JSON found")
    config = json.loads(match.group(1))
    candidates = []
    for provider in config["providers"].values():
        for model in provider["models"]:
            candidates.append(
                {
                    "provider": "openai",
                    "base_url": provider["base_url"],
                    "api_key": provider["api_key"],
                    "model": model["id"],
                    "model_label": model["name"],
                }
            )

    async def check(item: dict) -> bool:
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                response = await client.post(
                    item["base_url"].rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + item["api_key"]},
                    json={
                        "model": item["model"],
                        "messages": [{"role": "user", "content": "Reply READY."}],
                        "max_tokens": 1024,
                        "thinking": {"type": "disabled"},
                    },
                )
            reply = response.json().get("choices", [{}])[0].get("message", {}).get("content")
            ok = response.is_success and bool(reply)
            print(
                item["model_label"], "available" if ok else f"unavailable ({response.status_code})"
            )
            return ok
        except Exception as exc:
            print(item["model_label"], "unavailable", type(exc).__name__)
            return False

    available = await asyncio.gather(*(check(item) for item in candidates))
    models = sorted(
        [item for item, ok in zip(candidates, available, strict=True) if ok],
        key=lambda m: priority(m["model_label"]),
    )
    if not models:
        raise SystemExit("No usable models; existing .env kept")
    primary = args.primary or models[0]["model"]
    if not any(primary in [m["model"], m["model_label"]] for m in models):
        raise SystemExit("Primary model is not available; existing .env kept")
    entries = {
        "MODEL_CONFIG_JSON": json.dumps(models, ensure_ascii=False, separators=(",", ":")),
        "PRIMARY_MODEL": primary,
        "MODEL_TIMEOUT_SECONDS": "35",
        "MODEL_TEMPERATURE": "0.3",
        "STT_PROVIDER": "local",
        "STT_MODEL": "whisper-1",
        "WHISPER_MODEL": "tiny",
        "TALKER_USER_ID": "talker-001",
        "HINTER_USER_ID": "hinter-001",
    }
    env = ROOT / ".env"
    # Single quotes keep JSON and secrets literal for dotenv, shell and Compose.
    env.write_text(
        "\n".join(
            f"{key}='" + value.replace("'", "'\"'\"'") + "'" for key, value in entries.items()
        )
        + "\n"
    )
    env.chmod(0o600)
    print("Configured model order:", " -> ".join(m["model_label"] for m in models))
    print(
        "Primary:",
        next(m["model_label"] for m in models if primary in [m["model"], m["model_label"]]),
    )


if __name__ == "__main__":
    asyncio.run(main())
