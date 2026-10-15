import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from dotenv import load_dotenv, find_dotenv

from .routes.sea_routes import router as sea_router
from .routes.boltz_routes import router as boltz_router
from .routes.admin_routes import router as admin_router
from .utils.sea_tools import get_target_files

load_dotenv(find_dotenv())

# Pre-load target files into cache at startup
get_target_files()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3001", "http://127.0.0.1:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sea_router, prefix="/api")
app.include_router(boltz_router, prefix="/api")
app.include_router(admin_router, prefix="/api")

frontend_path = os.path.join(os.path.dirname(__file__), "../../frontend/dist")


@app.exception_handler(StarletteHTTPException)
async def spa_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        if request.url.path.startswith("/api"):
            return JSONResponse(status_code=404, content={"detail": exc.detail})
        return FileResponse(os.path.join(frontend_path, "index.html"))
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
