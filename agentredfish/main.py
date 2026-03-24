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
from app import AgentFishApp
from datetime import date, datetime

from utils.api import handle_api_exception  
from api.inventory_router import router as inventory_router

 # Initialize the app
agentfish_app = AgentFishApp()

app = FastAPI(
    title="AgentRedfish API",
    description="API for content generation",
    version="1.0.0"
)

app.include_router(inventory_router)

# Add CORS middleware to allow cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=False,
    allow_methods=["*"],  # Allow all methods
    allow_headers=["*"],  # Allow all headers
)

logger = setup_logger("api")

# Expose the initialized AgentFishApp via app.state so route dependencies can access it
app.state.agentfish_app = agentfish_app

@app.get("/healthz")
def healthz():
    logger.debug("Health check requested")
    return Response(content="ok", media_type="text/plain", status_code=status.HTTP_200_OK)

# 
# Main
#
if __name__ == "__main__":
    import uvicorn
    #import openlit
    #openlit.init()
    uvicorn.run(app, host="0.0.0.0", port=8000)