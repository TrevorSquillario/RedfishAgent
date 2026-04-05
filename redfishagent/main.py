# Upload photo endpoint
from fastapi import UploadFile, File, Form, status
from typing import List, Optional, Union, Dict, Any
import os
import logging
from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
import requests
from utils.logging import setup_logger
from app import RedfishAgentApp
from datetime import date, datetime

from utils.api import handle_api_exception  
from api.inventory_router import router as inventory_router
from api.llm_router import router as llm_router

# Create the FastAPI app first so it can be passed into RedfishAgentApp
# (trigger plugins need it to register routes at initialisation time)
app = FastAPI(
    title="RedfishAgent API",
    description="API for content generation",
    version="1.0.0"
)

# Initialize the core app, passing the FastAPI instance so trigger plugins
# that register endpoints (e.g. webhook_prometheus) can call app.include_router
redfishagent_app = RedfishAgentApp(fastapi_app=app)

app.include_router(inventory_router)
app.include_router(llm_router)

# Add CORS middleware to allow cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=False,
    allow_methods=["*"],  # Allow all methods
    allow_headers=["*"],  # Allow all headers
)

logger = setup_logger("api")

# Expose the initialized RedfishAgentApp via app.state so route dependencies can access it
app.state.redfishagent_app = redfishagent_app 

@app.get("/healthz")
def healthz():
    logger.debug("Health check requested")
    return Response(content="ok", media_type="text/plain", status_code=status.HTTP_200_OK)

# 
# Main
#
if __name__ == "__main__":
    import uvicorn
    #uvicorn.run(app, host="0.0.0.0", port=8000)
    uvicorn.dev(app, host="0.0.0.0", port=8000)