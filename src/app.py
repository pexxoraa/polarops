from fastapi import FastAPI

from api.routes.arctic_stations import router as arctic_stations_router
from api.routes.environment import router as environment_router
from api.routes.facilities import router as facilities_router
from api.routes.health import router as health_router
from api.routes.operations import router as operations_router
from core.config import APP_VERSION


def create_app() -> FastAPI:
    application = FastAPI(
        title="PolarOps Cloudflare",
        version=APP_VERSION,
    )
    application.include_router(health_router)
    application.include_router(environment_router)
    application.include_router(facilities_router)
    application.include_router(arctic_stations_router)
    application.include_router(operations_router)
    return application


app = create_app()
