
from starlette.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse
import asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
import json
import ssl
import os
import logging
import random
from datetime import datetime, timezone
from fastapi import Request
import httpx
import uuid

logger = logging.getLogger('uvicorn')
logger.setLevel(logging.DEBUG)

app = FastAPI()

# In-memory subscription store for created subscriptions
SUBSCRIPTIONS = {}

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

async def idrac_generator(event_type):
    for i in range(random.randint(1,1000)):
        files = get_files(event_type)
        file = random.choice(files)
        with open(file) as f:
            idrac_sse_example_json = json.load(f)
            if event_type == "Event":
                idrac_sse_example_json = update_timestamp_on_logs(idrac_sse_example_json)
            else:
                idrac_sse_example_json = update_timestamp_on_metrics(idrac_sse_example_json)
            yield json.dumps(idrac_sse_example_json) + '\n'
        await asyncio.sleep(random.randint(1,10))

@app.get('/redfish/v1/Managers/iDRAC.Embedded.1')
def managers():
    idrac_json = """
    {
        "@odata.context":"/redfish/v1/$metadata#Manager.Manager",
        "@odata.id":"/redfish/v1/Managers/iDRAC.Embedded.1",
        "@odata.type":"#Manager.v1_19_1.Manager"
    }
    """
    idrac_json = json.loads(idrac_json)
    return JSONResponse(content=idrac_json)

@app.get('/redfish/v1/Systems/System.Embedded.1')
def systems_embedded(request: Request):
    select = request.query_params.get('$select', None)
    idrac_json = ""
    hostname = os.environ.get('HOSTNAME')
    if select:
        idrac_json = """
        {
        "@odata.context": "/redfish/v1/$metadata#ComputerSystem.ComputerSystem",
        "@odata.id": "/redfish/v1/Systems/System.Embedded.1",
        "@odata.type": "#ComputerSystem.v1_22_1.ComputerSystem",
        "HostName": "hostname"
        }
        """
    idrac_json = json.loads(idrac_json)
    idrac_json["HostName"] = hostname
    return JSONResponse(content=idrac_json)

@app.get('/redfish/v1/Managers/iDRAC.Embedded.1/Attributes')
def attributes():
    idrac_json = get_file_to_json("example_attributes.json")
    return JSONResponse(content=idrac_json)

@app.get('/redfish/v1/SSE')
def sse(request: Request):
    filter = request.query_params.get('$filter', None)
    event = ""
    if filter:
        event_type = filter.split(" ")[-1]
        logger.debug(f"Detected event type: {event_type}")
        event = idrac_generator(event_type)
        logger.info(event)
    return EventSourceResponse(event)


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


# Background task: send events produced by idrac_generator to listener URL
@app.on_event("startup")
async def startup_event_sender():
    listener_url = os.environ.get('REDFISH_LISTENER_URL') or os.environ.get('LISTENER_DEST') or 'http://127.0.0.1:8080/redfish/events'
    logger.info(f"Starting background event sender to {listener_url}")
    async def event_sender():
        async with httpx.AsyncClient() as client:
            async for item in idrac_generator('Event'):
                # idrac_generator yields JSON strings with a trailing newline
                payload = None
                try:
                    payload = json.loads(item)
                except Exception:
                    # fallback: send raw string
                    payload = item.strip()
                try:
                    resp = await client.post(listener_url, json=payload, timeout=10)
                    logger.debug(f"Posted event to {listener_url}: status={resp.status_code}")
                except Exception as e:
                    logger.error(f"Failed to post event to {listener_url}: {e}")
                await asyncio.sleep(15)

    asyncio.create_task(event_sender())

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
                    logger.debug(f"Posted metric to {listener_url}: status={resp.status_code}")
                except Exception as e:
                    logger.error(f"Failed to post metric to {listener_url}: {e}")
                await asyncio.sleep(15)

    asyncio.create_task(metric_sender())