from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from starlette.routing import Mount

from .api import router
from .config import get_settings
from .graph_store import GraphStore
from .mcp_server import create_mcp
from .service import MemoryService
from .session_store import SessionStore
from .ingestion import Ingestor
from .policy import PolicyError


settings = get_settings()
redis = Redis.from_url(settings.valkey_url, decode_responses=True)
graph = GraphStore(settings)
memory_service = MemoryService(graph, SessionStore(redis, settings.memory_session_ttl_seconds))
mcp = create_mcp(memory_service)
mcp.settings.streamable_http_path = "/"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await graph.initialize()
    if settings.memory_ingestion_mode in ("graphiti_async", "typesafe_async"):
        if (settings.memory_ingestion_mode == "typesafe_async"
                and not graph.search_reranker.backend.api_key):
            raise RuntimeError("a configured decision backend key is required for typesafe_async")
        graph.ingestor = Ingestor(graph, redis)
        await graph.ingestor.start()
    async with mcp.session_manager.run():
        yield
    if graph.ingestor:
        await graph.ingestor.close()
    await graph.close()
    await redis.aclose()


app = FastAPI(title="Agent Memory Gateway", version="1.0.0", lifespan=lifespan)


@app.exception_handler(PolicyError)
async def policy_error(request, exc):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


app.state.memory_service = memory_service
app.include_router(router)
app.routes.append(Mount("/mcp", app=mcp.streamable_http_app()))


if __name__ == "__main__":
    uvicorn.run(app, host=settings.memory_host, port=settings.memory_port)
