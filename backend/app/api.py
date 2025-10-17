import os
from typing import Union
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI(docs_url="/api/py/docs", openapi_prefix="/api", openapi_url="/api/py/openapi.json")

# Allow requests from your frontend origin
origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,          # You can set ["*"] for all (not recommended in prod)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# frontend import
frontend_path = os.path.join(os.path.dirname(__file__), "../../frontend/out")
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")

@app.get("/{full_path:path}")
async def serve_frontend(full_path: str) -> FileResponse:
    index_path = os.path.join(frontend_path, "index.html")
    return FileResponse(index_path)

@app.get("/")
def read_root() -> dict[str, Union[str, int]]:
    return {"Hello": "World", "version": 1}

@app.get("/items/{item_id}")
def read_item(item_id: int, q: Union[str, None] = None) -> dict[str, Union[int, str, None]]:
    return {"item_id": item_id, "q2": q}