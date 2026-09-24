from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

from api.application_routes import router as application_router  # noqa: E402
from api.routes import router  # noqa: E402

app = FastAPI(title="AI Job Search & Career Agent")
app.include_router(router)
app.include_router(application_router)


@app.get("/health")
def health():
    return {"status": "ok"}
