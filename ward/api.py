import logging
import os
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.exceptions import HTTPException

from ward.scenario import ScenarioTiming, WaterScenario
from ward.simulation import (
    RunError,
    RunManager,
    load_network_topology,
    load_preview_network,
)
from ward.storage.sqlite import SQLiteStorage

ERRORS = {
    "INPUT_ERROR": (422, "Check the request fields and scenario definition."),
    "NOT_FOUND": (404, "The requested scenario, model, or run was not found."),
    "MODEL_LOADING_ERROR": (500, "The network model could not be loaded."),
    "ATTACK_SETUP_ERROR": (
        422,
        "The attack could not be applied to the selected model.",
    ),
    "SIMULATION_ERROR": (500, "The simulation could not be completed."),
    "CONVERGENCE_ERROR": (500, "The simulation did not converge."),
    "ANALYSIS_ERROR": (500, "The simulation results could not be analyzed."),
    "STORAGE_ERROR": (500, "The saved data could not be read or written."),
    "UNEXPECTED_ERROR": (
        500,
        "An unexpected error occurred. Check the application logs.",
    ),
    "RESULTS_NOT_READY": (409, "The run has not finished. Check its status again."),
    "RUN_FAILED": (409, "The run failed. Check its status for the error code."),
}


class ErrorInfo(BaseModel):
    code: str = Field(examples=["INPUT_ERROR"])
    message: str


class ErrorResponse(BaseModel):
    error: ErrorInfo


class RunRequest(BaseModel):
    model_config = {"extra": "forbid"}
    scenario_id: str = Field(min_length=1, pattern=r"\S", examples=["scenario_pump_01"])


class RunStatus(BaseModel):
    run_id: str
    scenario_id: str
    model_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    error: ErrorInfo | None = None


class RunResults(RunStatus):
    status: Literal["succeeded"]
    units: dict[str, str] = Field(examples=[{"time": "s", "pressure": "psi"}])
    time_sec: list[float] = Field(
        description="Simulation times in seconds, shared by all pressure series.",
        examples=[[0, 3600]],
    )
    pressures: dict[Literal["baseline", "attack"], dict[str, list[float | None]]] = (
        Field(
            description="Pressure in psi by case and node ID; null means a missing measurement.",
            examples=[
                {"baseline": {"node-id": [50, 50]}, "attack": {"node-id": [50, 30]}}
            ],
        )
    )
    analysis: dict[str, Any]
    timing: ScenarioTiming
    wntr_version: str


class NetworkNode(BaseModel):
    node_id: str
    type: str
    coordinates: tuple[float, float] | None


class NetworkLink(BaseModel):
    link_id: str
    type: str
    start_node: str
    end_node: str
    vertices: list[tuple[float, float]]


class NetworkTopology(BaseModel):
    model_id: str
    nodes: list[NetworkNode]
    links: list[NetworkLink]
    visualization: dict[str, Any]


@contextmanager
def _storage(database_path):
    storage = None
    try:
        storage = SQLiteStorage(database_path)
        yield storage
    except (sqlite3.Error, OSError) as error:
        raise RunError("STORAGE_ERROR", str(error)) from error
    finally:
        if storage is not None:
            storage.close()


def create_app(database_path=None, model_paths=None):
    database_path = Path(
        database_path or os.environ.get("WARD_DATABASE_PATH", "data/ward.sqlite")
    ).resolve()
    root = Path(__file__).resolve().parent
    models = (
        model_paths
        if model_paths is not None
        else {"ctown": root.parent / "examples" / "networks" / "CTOWN.inp"}
    )

    @asynccontextmanager
    async def lifespan(application):
        with _storage(database_path) as storage:
            if "ctown" in models and storage.get_scenario("scenario_pump_01") is None:
                if storage.get_model("ctown") is None:
                    storage.register_model({"model_id": "ctown", "name": "CTOWN"})
                scenario = WaterScenario.load_from_json(
                    root / "scenarios" / "pump_poweroff_basic.json"
                )
                storage.create_scenario(scenario.model_dump(mode="json"))
        application.state.run_manager = RunManager(database_path)
        try:
            yield
        finally:
            application.state.run_manager.close()

    application = FastAPI(
        title="WARD demo API",
        lifespan=lifespan,
        responses={status: {"model": ErrorResponse} for status in (404, 409, 422, 500)},
    )

    async def handle_error(request, error):
        if isinstance(error, RunError):
            code = error.code
        elif isinstance(error, (RequestValidationError, ValidationError)):
            code = "INPUT_ERROR"
        elif isinstance(error, (sqlite3.Error, OSError)):
            code = "STORAGE_ERROR"
        elif isinstance(error, HTTPException):
            code = "NOT_FOUND" if error.status_code == 404 else "INPUT_ERROR"
        else:
            code = "UNEXPECTED_ERROR"
        if code not in ERRORS:
            code = "UNEXPECTED_ERROR"
        status, message = ERRORS[code]
        if isinstance(error, HTTPException):
            status = error.status_code
        logging.getLogger(__name__).error("%s", code, exc_info=error)
        return JSONResponse(
            status_code=status, content={"error": {"code": code, "message": message}}
        )

    for error_type in (
        RunError,
        RequestValidationError,
        ValidationError,
        sqlite3.Error,
        HTTPException,
        Exception,
    ):
        application.add_exception_handler(error_type, handle_error)

    @application.get("/scenarios", response_model=list[WaterScenario])
    def list_scenarios():
        """List saved scenarios, including the preloaded demo scenario."""
        with _storage(database_path) as storage:
            return storage.list_scenarios()

    @application.get("/scenarios/{scenario_id}", response_model=WaterScenario)
    def get_scenario(scenario_id: str):
        """Retrieve the scenario definition accepted by the simulation runner."""
        with _storage(database_path) as storage:
            scenario = storage.get_scenario(scenario_id)
            if scenario is None:
                raise RunError("NOT_FOUND", scenario_id)
            return scenario

    @application.get("/models/{model_id}/topology", response_model=NetworkTopology)
    def topology(model_id: str):
        """Return network assets, connectivity, and model coordinates."""
        if model_id not in models:
            raise RunError("NOT_FOUND", model_id)
        return {"model_id": model_id, **load_network_topology(models[model_id])}

    @application.post("/runs", status_code=202, response_model=RunStatus)
    def submit_run(body: RunRequest):
        """Queue a saved scenario and return the run ID for status polling."""
        with _storage(database_path) as storage:
            definition = storage.get_scenario(body.scenario_id)
        if definition is None:
            raise RunError("NOT_FOUND", body.scenario_id)
        scenario = WaterScenario.model_validate(definition)
        if scenario.model_id not in models:
            raise RunError("NOT_FOUND", scenario.model_id)
        run_id = application.state.run_manager.submit(
            scenario, models[scenario.model_id]
        )
        return {
            "run_id": run_id,
            "scenario_id": scenario.scenario_id,
            "model_id": scenario.model_id,
            "status": "queued",
        }

    @application.get("/runs/{run_id}", response_model=RunStatus)
    def run_status(run_id: str):
        """Return saved run status and a safe error if the run failed."""
        run = application.state.run_manager.get_run(run_id)
        if run is None:
            raise RunError("NOT_FOUND", run_id)
        if run["status"] == "failed":
            code = (run["error_message"] or "").split(":", 1)[0]
            if code not in ERRORS:
                code = "UNEXPECTED_ERROR"
            run["error"] = {"code": code, "message": ERRORS[code][1]}
        return run

    @application.get("/runs/{run_id}/results", response_model=RunResults)
    def run_results(run_id: str):
        """Retrieve saved pressures and analysis; return 409 until results are available."""
        return application.state.run_manager.get_results(run_id)

    application.get("/preview")(preview)
    return application


def preview():
    network = load_preview_network()  # call the function from simulation.py to load the preset network and return a dictionary with junctions and network HTML
    return {  # return a dictionary with the junctions, network HTML, and sample data for the dashboard to use
        "junctions": network["junctions"],
        "network_html": network["network_html"],
        "time_hours": [0, 1, 2, 3],
        "attack_hours": [1, 2],
        "sample_pressures": {
            "baseline": [50, 50, 50, 50],
            "attack": [50, 15, 18, 50],
        },
    }


app = create_app()
