from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import socket
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


@app.post("/api/calibration-point")
def calibration_point(grams: Annotated[float, Form()]) -> dict:
    model = service.add_calibration_point(grams)
    return {"degree": model.degree, "points": len(model.raw_points), "coefficients": model.coefficients, "calibration_points": service.calibration_points()}


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
