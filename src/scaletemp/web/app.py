from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import socket
import uuid
from typing import Annotated, AsyncIterator

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
import uvicorn

from scaletemp.experiments.workflows import ExperimentRunner
from scaletemp.processing.service import ScaleService

PACKAGE_DIR = Path(__file__).resolve().parent
service = ScaleService()
runner = ExperimentRunner(service)
guided_sessions: dict[str, dict] = {}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start and stop the sampler using FastAPI's non-deprecated lifespan API."""

    del app
    service.start()
    try:
        yield
    finally:
        service.stop()


app = FastAPI(title="ScaleTemp HX711 Dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")


def current_ip_address() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="dashboard.html", context={"current_ip": current_ip_address()})


@app.get("/experiments", response_class=HTMLResponse)
def experiments(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="experiments.html", context={})


@app.get("/api/readings")
def readings() -> dict:
    payload = service.chart_payload()
    payload["current_ip"] = current_ip_address()
    return payload


@app.post("/api/tare")
def tare() -> dict:
    return {"zero_offset": service.tare()}


@app.post("/api/filter")
def filter_strength(strength: Annotated[float, Form()]) -> dict:
    service.set_filter_strength(strength)
    return service.metadata()


@app.post("/api/filter-window")
def filter_window(limit: Annotated[float, Form()]) -> dict:
    service.set_filter_window_limit(limit)
    return service.metadata()


@app.post("/api/calibration-point")
def calibration_point(grams: Annotated[float, Form()]) -> dict:
    model = service.add_calibration_point(grams)
    return {"degree": model.degree, "points": len(model.raw_points), "coefficients": model.coefficients, "calibration_points": service.calibration_points()}


@app.delete("/api/calibration-point/{index}")
def delete_calibration_point(index: int) -> dict:
    try:
        model = service.remove_calibration_point(index)
    except IndexError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"degree": model.degree, "points": len(model.raw_points), "coefficients": model.coefficients, "calibration_points": service.calibration_points()}


def _guided_steps(name: str, masses: str, trials: int) -> list[dict]:
    if name == "calibration":
        parsed = [float(x.strip()) for x in masses.split(",") if x.strip()]
        return [{"label": f"放置 {mass:g} g 标准砝码 / Place {mass:g} g", "mass": mass} for mass in parsed]
    if name == "repeatability":
        return [{"label": f"第 {i} 次放置同一载荷 / Trial {i}", "trial": i} for i in range(1, trials + 1)]
    if name == "dynamic":
        return [
            {"label": "准备并快速放置载荷 / Place load quickly", "phase": "place"},
            {"label": "快速移除载荷 / Remove load quickly", "phase": "remove"},
        ]
    return [{"label": "保持当前实验状态并采集 / Hold condition and collect", "phase": name}]


@app.post("/api/experiment-session/start")
def start_experiment_session(name: Annotated[str, Form()], duration_s: Annotated[float, Form()] = 5.0, masses: Annotated[str, Form()] = "0,100,200,500,1000", trials: Annotated[int, Form()] = 5) -> dict:
    steps = _guided_steps(name, masses, trials)
    session_id = uuid.uuid4().hex
    guided_sessions[session_id] = {"name": name, "duration_s": duration_s, "steps": steps, "captures": [], "index": 0}
    return {"session_id": session_id, "name": name, "steps": steps, "current_index": 0}


@app.post("/api/experiment-session/{session_id}/capture")
def capture_experiment_step(session_id: str) -> JSONResponse:
    session = guided_sessions.get(session_id)
    if not session:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    if session["index"] >= len(session["steps"]):
        return JSONResponse({"error": "session already complete"}, status_code=400)
    step = session["steps"][session["index"]]
    samples = service.sampler.collect(float(session["duration_s"]))
    session["captures"].append({"step": step, "samples": samples})
    session["index"] += 1
    if session["index"] < len(session["steps"]):
        return JSONResponse({"done": False, "current_index": session["index"], "next_step": session["steps"][session["index"]], "samples": len(samples)})

    name = session["name"]
    duration = float(session["duration_s"])
    if name == "calibration":
        groups = [(capture["step"].get("mass", 0.0), capture["samples"]) for capture in session["captures"]]
        result = runner.calibration_from_groups(groups, duration)
    elif name == "repeatability":
        result = runner.repeatability_from_groups([capture["samples"] for capture in session["captures"]], duration)
    elif name == "dynamic":
        all_samples = []
        for capture in session["captures"]:
            all_samples.extend(capture["samples"])
        # Reuse the existing dynamic processor by temporarily writing through a one-off helper path.
        # The same metrics are computed from the concatenated guided phases.
        original_collect = service.sampler.collect
        service.sampler.collect = lambda _duration: all_samples  # type: ignore[method-assign]
        try:
            result = runner.dynamic(duration_s=duration * len(session["captures"]))
        finally:
            service.sampler.collect = original_collect  # type: ignore[method-assign]
    else:
        all_samples = []
        for capture in session["captures"]:
            all_samples.extend(capture["samples"])
        original_collect = service.sampler.collect
        service.sampler.collect = lambda _duration: all_samples  # type: ignore[method-assign]
        try:
            result = getattr(runner, name)(duration_s=duration)
        finally:
            service.sampler.collect = original_collect  # type: ignore[method-assign]
    guided_sessions.pop(session_id, None)
    return JSONResponse({"done": True, "result": result.__dict__})


@app.post("/api/experiment/{name}")
def run_experiment(name: str, duration_s: Annotated[float, Form()] = 5.0, masses: Annotated[str, Form()] = "0,100,200,500,1000", trials: Annotated[int, Form()] = 5) -> JSONResponse:
    if name == "calibration":
        result = runner.calibration([float(x.strip()) for x in masses.split(",") if x.strip()], duration_s=duration_s)
    elif name == "filtering":
        result = runner.filtering(duration_s=duration_s)
    elif name == "dynamic":
        result = runner.dynamic(duration_s=duration_s)
    elif name == "repeatability":
        result = runner.repeatability(trials=trials, duration_s=duration_s)
    elif name == "drift":
        result = runner.drift(duration_s=duration_s)
    elif name == "auto_zero":
        result = runner.auto_zero(duration_s=duration_s)
    else:
        return JSONResponse({"error": "unknown experiment"}, status_code=404)
    return JSONResponse(result.__dict__)


@app.get("/download")
def download(path: str) -> FileResponse:
    target = Path(path).resolve()
    data_root = Path("data").resolve()
    if data_root not in target.parents and target != data_root:
        raise HTTPException(status_code=400, detail="download path must be inside data directory")
    return FileResponse(target, filename=target.name)


def main() -> None:
    uvicorn.run("scaletemp.web.app:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
