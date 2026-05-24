import os
import json

from src.agent import app
from src.state import create_initial_state


def load_mock_infra(file_path: str):
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    repo_root = os.path.abspath(os.path.dirname(__file__))
    mock_path = os.path.join(repo_root, "scanner", "mock_infra.json")
    if os.path.exists(mock_path):
        raw = load_mock_infra(mock_path)
        initial = create_initial_state(raw)
        print("Initializing Modular LangGraph Engine...")
        final = app.invoke(initial)
        print("--- FINAL STATE ---")
        print(final)
    else:
        print(f"Missing mock file: {mock_path}")
