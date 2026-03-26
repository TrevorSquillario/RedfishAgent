from fastapi import HTTPException
import traceback
from utils.logging import setup_logger

# initialize module logger; callers can pass a different logger if desired
logger = setup_logger("api")

def handle_api_exception(e, msg=None):
    error_msg = msg or str(e)
    logger.error(f"{error_msg}: {e}\n{traceback.format_exc()}")
    raise HTTPException(status_code=500, detail=str(e))