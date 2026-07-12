"""Centralized settings extracted from the codebase."""
import os

# Ollama / LLM settings
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen2.5-coder:1.5b")
NUM_CTX = int(os.getenv("NUM_CTX", "2048"))  # Memory cap for LLM

# Generic application settings
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "5"))

# Logging settings
DEBUG = os.getenv("DEBUG", "True").lower() in ("true", "1", "yes")

