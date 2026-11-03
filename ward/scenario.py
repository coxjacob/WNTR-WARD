import json
from pathlib import Path
from typing import List, Dict, Any, Literal
from pydantic import BaseModel, Field, field_validator, model_validator

# Define your allowed attack types here
ALLOWED_ATTACK_TYPES = {"valve_closure", "pipe_burst", "pump_failure", "pump_off"}

class ScenarioTiming(BaseModel):
    start_time_sec: float = Field(..., description="Start time in seconds")
    duration_sec: float = Field(..., description="Duration in seconds")

    @model_validator(mode="after")
    def validate_time_range(self) -> "ScenarioTiming":
        """Rejects invalid time ranges (negative times or zero/negative duration)."""
        if self.start_time_sec < 0:
            raise ValueError("timing.start_time_sec must be greater than or equal to 0.")
        if self.duration_sec <= 0:
            raise ValueError("timing.duration_sec must be strictly greater than 0.")
        return self

class WaterScenario(BaseModel):
    schema_version: str = "1.0"
    scenario_id: str
    name: str
    description: str = ""
    attack_type: str
    model_id: str
    target_components: List[str]
    timing: ScenarioTiming
    parameters: Dict[str, Any]

    @field_validator("scenario_id", "model_id")
    @classmethod
    def validate_ids_not_empty(cls, v: str, info) -> str:
        """Rejects invalid or blank IDs/names."""
        if not v or not v.strip():
            raise ValueError(f"Field '{info.field_name}' cannot be empty or whitespace.")
        return v

    @field_validator("attack_type")
    @classmethod
    def validate_attack_type(cls, v: str) -> str:
        """Rejects unknown attack types."""
        if v not in ALLOWED_ATTACK_TYPES:
            raise ValueError(
                f"Unknown attack_type '{v}'. Must be one of: {', '.join(ALLOWED_ATTACK_TYPES)}"
            )
        return v

    def save_to_json(self, file_path: Path):
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(self.model_dump_json(indent=2))

    @classmethod
    def load_from_json(cls, file_path: Path) -> "WaterScenario":
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(**data)
