"""Duet AI service package; local .env is loaded before settings are constructed."""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
