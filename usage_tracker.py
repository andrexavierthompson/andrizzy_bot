import json
import os
from pathlib import Path
from datetime import date

DATA_DIR = Path(os.environ.get("DATA_PATH", "data"))
USAGE_FILE = DATA_DIR / "usage.json"

INPUT_COST_PER_M = 3.00   # $ per million input tokens (Sonnet 4.6)
OUTPUT_COST_PER_M = 15.00  # $ per million output tokens (Sonnet 4.6)


def track_usage(input_tokens: int, output_tokens: int) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if not USAGE_FILE.exists():
        data = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "since": str(date.today())}
    else:
        data = json.loads(USAGE_FILE.read_text())
    data["input_tokens"] += input_tokens
    data["output_tokens"] += output_tokens
    data["calls"] += 1
    USAGE_FILE.write_text(json.dumps(data, indent=2))


def load_usage() -> dict:
    if not USAGE_FILE.exists():
        return {"input_tokens": 0, "output_tokens": 0, "calls": 0, "since": str(date.today())}
    return json.loads(USAGE_FILE.read_text())


def reset_usage() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    data = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "since": str(date.today())}
    USAGE_FILE.write_text(json.dumps(data, indent=2))


def calc_cost(input_tokens: int, output_tokens: int) -> tuple[float, float, float]:
    input_cost = (input_tokens / 1_000_000) * INPUT_COST_PER_M
    output_cost = (output_tokens / 1_000_000) * OUTPUT_COST_PER_M
    return input_cost, output_cost, input_cost + output_cost
