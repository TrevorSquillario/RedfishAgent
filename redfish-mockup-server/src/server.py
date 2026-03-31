
from starlette.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse
import asyncio
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
import json
import ssl
import os
import logging
import random
from datetime import datetime, timezone
import utils
import httpx
import uuid

logger = logging.getLogger('uvicorn')
log_level = os.getenv('LOG_LEVEL',  logging.ERROR)
logger.setLevel(log_level)
# Set uvicorn access/error logger levels to honor LOG_LEVEL (default WARNING)
logging.getLogger("uvicorn.access").setLevel(os.getenv('LOG_LEVEL', log_level))
logging.getLogger("uvicorn.error").setLevel(os.getenv('LOG_LEVEL', log_level))

app = FastAPI()

# In-memory subscription store for created subscriptions
SUBSCRIPTIONS = {}
# In-memory session store for created sessions
SESSIONS = {}

# async def simple_generator():
#     for i in range(100):
#         yield json.dumps ({"event_id": i, "data": f"{i + 1} chunk of data", "is_final_event": i == 9}) + '\n' 
#         await asyncio.sleep(1)

def get_files(event_type):
    directory_path = ""
    files = []
    if event_type == "Event":
        directory_path = "/app/example_logs"
    else:
        directory_path = "/app/example_metrics"

    for filename in os.listdir(directory_path):
        file_path = os.path.join(directory_path, filename)
        if os.path.isfile(file_path):
            print(f"Found file: {file_path}")
            files.append(file_path)

    return files

def get_file_to_json(file):
    file_path = f"/app/{file}"
    with open(file_path) as f:
        idrac_json = json.load(f)
        return idrac_json

def get_current_datetime_with_offset():
    now = datetime.now(timezone.utc)
    formatted_datetime = now.strftime("%Y-%m-%dT%H:%M:%S%z")
    return formatted_datetime

def get_current_datetime_iso():
    now = datetime.now(timezone.utc)
    formatted_datetime = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return formatted_datetime

def update_timestamp_on_logs(event):
    for log in event["Events"]:
        log["EventTimestamp"] = get_current_datetime_with_offset()
    return event

def update_timestamp_on_metrics(event):
    now = get_current_datetime_iso()
    event["Timestamp"] = now
    for metric in event["MetricValues"]:
        metric["Timestamp"] = now
    return event


# Reusable helpers moved to src/utils.py

async def idrac_generator(event_type):
    for i in range(random.randint(1,1000)):
        files = get_files(event_type)
        file = random.choice(files)
        with open(file) as f:
            idrac_sse_example_json = json.load(f)
            if event_type == "Event":
                idrac_sse_example_json = update_timestamp_on_logs(idrac_sse_example_json)
                idrac_sse_example_json = utils.rewrite_ids_random(idrac_sse_example_json)
                idrac_sse_example_json = utils.modify_first_log(idrac_sse_example_json)
            else:
                idrac_sse_example_json = update_timestamp_on_metrics(idrac_sse_example_json)
                idrac_sse_example_json = utils.rewrite_ids_random(idrac_sse_example_json)
            yield json.dumps(idrac_sse_example_json) + '\n'
        await asyncio.sleep(random.randint(1,10))

@app.get('/redfish/v1/SSE')
def sse(request: Request):
    filter = request.query_params.get('$filter', None)
    event = ""
    if filter:
        event_type = filter.split(" ")[-1]
        logger.debug(f"Detected event type: {event_type}")
        event = idrac_generator(event_type)
        logger.debug(event)
    return EventSourceResponse(event)


@app.api_route('/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel/Entries', methods=["GET", "HEAD"])
def sel_entries_index(request: Request):
    """Load the local index.json for SEL entries, update the Created date to today (keep time/tz), and return it."""
    local_index = os.path.join('/redfish', 'v1', 'Managers', 'iDRAC.Embedded.1', 'LogServices', 'Sel', 'Entries', 'index.json')
    logger.debug("SEL entries request -> local file %s", local_index)

    if not os.path.exists(local_index) or not os.path.isfile(local_index):
        logger.debug("SEL index.json not found: %s", local_index)
        return JSONResponse(status_code=404, content={"error": "index.json not found"})

    # If this is a HEAD request, return headers-only response
    if request.method == 'HEAD':
        return Response(status_code=200)

    try:
        with open(local_index, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # update Created date on members
        data = utils.update_created_dates(data)

        # Rewrite Id fields for testing before returning
        data = utils.rewrite_ids_random(data)

        # apply $top/$skip after all modifications
        top, skip = utils.parse_top_skip(request.query_params)
        data = utils.apply_top_skip(data, top, skip)

        logger.debug("Serving modified SEL entries index.json (%d members)", len(data.get('Members', [])))
        return JSONResponse(content=data)
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in SEL index file %s: %s", local_index, e)
        return JSONResponse(status_code=500, content={"error": "invalid json in index.json"})
    except Exception as e:
        logger.exception("Error loading SEL index file %s", local_index)
        return JSONResponse(status_code=500, content={"error": "failed to load index.json"})


@app.api_route('/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Lclog/Entries', methods=["GET", "HEAD"])
def lclog_entries_index(request: Request):
    """Load the local index.json for Lclog entries, update the Created date to today (keep time/tz), and return it."""
    local_index = os.path.join('/redfish', 'v1', 'Managers', 'iDRAC.Embedded.1', 'LogServices', 'Lclog', 'Entries', 'index.json')
    logger.debug("Lclog entries request -> local file %s", local_index)

    if not os.path.exists(local_index) or not os.path.isfile(local_index):
        logger.debug("Lclog index.json not found: %s", local_index)
        return JSONResponse(status_code=404, content={"error": "index.json not found"})

    # If this is a HEAD request, return headers-only response
    if request.method == 'HEAD':
        return Response(status_code=200)

    try:
        with open(local_index, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # update Created date on members
        data = utils.update_created_dates(data)

        # Rewrite Id fields for testing before returning and apply first-log tweaks
        data = utils.rewrite_ids_random(data)
        data = utils.modify_first_log(data)

        # apply $top/$skip after all modifications
        top, skip = utils.parse_top_skip(request.query_params)
        data = utils.apply_top_skip(data, top, skip)

        logger.debug("Serving modified Lclog entries index.json (%d members)", len(data.get('Members', [])))
        return JSONResponse(content=data)
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in Lclog index file %s: %s", local_index, e)
        return JSONResponse(status_code=500, content={"error": "invalid json in index.json"})
    except Exception as e:
        logger.exception("Error loading Lclog index file %s", local_index)
        return JSONResponse(status_code=500, content={"error": "failed to load index.json"})


# Subscription endpoints to support clients creating/deleting subscriptions
@app.post('/redfish/v1/EventService/Subscriptions')
async def create_subscription(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    sub_id = uuid.uuid4().hex
    SUBSCRIPTIONS[sub_id] = payload

    headers = {"Location": f"/redfish/v1/EventService/Subscriptions/{sub_id}"}
    body = {"Id": sub_id}
    return JSONResponse(content=body, status_code=201, headers=headers)


@app.delete('/redfish/v1/EventService/Subscriptions/{sub_id}')
async def delete_subscription(sub_id: str):
    if sub_id in SUBSCRIPTIONS:
        del SUBSCRIPTIONS[sub_id]
        return JSONResponse(status_code=204, content=None)
    return JSONResponse(status_code=404, content={"error": "subscription not found"})


# Session creation endpoint
@app.post('/redfish/v1/SessionService/Sessions')
async def create_session(request: Request):
    """Create a new Redfish session and return session object + auth token."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    # Determine username from payload if provided, otherwise fallback
    username = payload.get('UserName') or payload.get('Username') or payload.get('user') or 'user'

    # Use a simple numeric id so test-friendly values like "1" appear
    session_id = str(len(SESSIONS) + 1)
    token = uuid.uuid4().hex

    SESSIONS[session_id] = {"UserName": username, "Token": token}

    body = {
        "@odata.type": "#Session.v1_1_2.Session",
        "@odata.id": f"/redfish/v1/SessionService/Sessions/{session_id}",
        "Id": session_id,
        "Name": "User Session",
        "Description": "Manager User Session",
        "UserName": username,
        "Oem": {},
    }

    headers = {"Location": f"/redfish/v1/SessionService/Sessions/{session_id}", "X-Auth-Token": token}
    return JSONResponse(content=body, status_code=201, headers=headers)

# Dynamic endpoint: map incoming /redfish/... URI to a local index.json file under /app
@app.api_route('/redfish/{full_path:path}', methods=["GET", "HEAD"])
def redfish_dynamic(request: Request, full_path: str):
    """Return an index.json file from the local filesystem that mirrors the requested URI.

    Example: request `/redfish/v1/Managers/iDRAC.Embedded.1` will try to return
    `/redfish/v1/Managers/iDRAC.Embedded.1/index.json` if present.
    """
    # build local file path (files are copied into /app in the container)
    local_index = os.path.join('/redfish', full_path, 'index.json')
    logger.debug("Dynamic redfish request for %s -> local file %s", full_path, local_index)

    if not os.path.exists(local_index):
        logger.debug("Index file not found: %s", local_index)
        return JSONResponse(status_code=404, content={"error": "index.json not found"})

    if not os.path.isfile(local_index):
        logger.warning("Index path exists but is not a file: %s", local_index)
        return JSONResponse(status_code=404, content={"error": "index.json not found"})

    # Mirror GET behavior for HEAD requests: same status, no body.
    if request.method == 'HEAD':
        return Response(status_code=200)

    try:
        # inspect file size and a small preview to aid debugging malformed/empty files
        size = os.path.getsize(local_index)
        logger.debug("Index file size=%d bytes: %s", size, local_index)
        with open(local_index, 'r', encoding='utf-8') as f:
            content = f.read()

        preview = repr(content[:200])
        logger.debug("Index file preview: %s", preview)

        if not content.strip():
            logger.error("Index file is empty: %s", local_index)
            return JSONResponse(status_code=500, content={"error": "index.json empty"})

        data = json.loads(content)

        # apply $top/$skip after loading and parsing the file
        top, skip = utils.parse_top_skip(request.query_params)
        data = utils.apply_top_skip(data, top, skip)

        logger.debug("Serving index file for %s", full_path)
        return JSONResponse(content=data)
    except json.JSONDecodeError as e:
        logger.error("JSON decode error for file %s: %s (preview=%s)", local_index, e, preview)
        return JSONResponse(status_code=500, content={"error": "invalid json in index.json"})
    except Exception as e:
        logger.exception("Failed to read/parse index file %s", local_index)
        return JSONResponse(status_code=500, content={"error": "failed to read index.json"})

# Background task: send events produced by idrac_generator to listener URL
@app.on_event("startup")
async def startup_event_sender():
    listener_url = os.environ.get('REDFISH_LISTENER_URL') or os.environ.get('LISTENER_DEST') or 'http://127.0.0.1:8080/redfish/events'
    enable_alerts = str(os.environ.get('ENABLE_ALERTS', 'True')).lower() in ('1', 'true', 'yes')
    enable_metrics = str(os.environ.get('ENABLE_METRICS', 'True')).lower() in ('1', 'true', 'yes')
    logger.debug(f"Startup senders config: listener={listener_url} ENABLE_ALERTS={enable_alerts} ENABLE_METRICS={enable_metrics}")

    async def event_sender():
        async with httpx.AsyncClient() as client:
            async for item in idrac_generator('Event'):
                payload = None
                try:
                    payload = json.loads(item)
                except Exception:
                    payload = item.strip()
                try:
                    resp = await client.post(listener_url, json=payload, timeout=10)
                    logger.info(f"Posted event to {listener_url}: status={resp.status_code}")
                except Exception as e:
                    logger.error(f"Failed to post event to {listener_url}: {e}")
                await asyncio.sleep(15)

    if enable_alerts:
        asyncio.create_task(event_sender())
    else:
        logger.info('Background event sender disabled via ENABLE_ALERTS')

    async def metric_sender():
        async with httpx.AsyncClient() as client:
            async for item in idrac_generator('MetricReport'):
                payload = None
                try:
                    payload = json.loads(item)
                except Exception:
                    payload = item.strip()
                try:
                    resp = await client.post(listener_url, json=payload, timeout=10)
                    logger.info(f"Posted metric to {listener_url}: status={resp.status_code}")
                except Exception as e:
                    logger.error(f"Failed to post metric to {listener_url}: {e}")
                await asyncio.sleep(15)

    if enable_metrics: 
        asyncio.create_task(metric_sender())
    else:
        logger.info('Background metric sender disabled via ENABLE_METRICS')


# 
# Main
#
if __name__ == "__main__":
    import uvicorn
    #import openlit
    #openlit.init()
    #uvicorn.run(app, host="0.0.0.0", port=8000)
    uvicorn.dev(app, host="0.0.0.0", port=443)