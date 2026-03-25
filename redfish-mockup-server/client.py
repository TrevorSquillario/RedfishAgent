import os
import json
import httpx
import asyncio
from asyncio import create_task
import time
from httpx_sse import connect_sse, aconnect_sse
from stamina import retry

idrac_username = os.environ.get('iDRAC_USERNAME')
idrac_password = os.environ.get('iDRAC_PASSWORD')
retries = 25

# async def get_test_event2():
    # url = "http://127.0.0.1:8000/"
    # async with aiohttp.ClientSession() as session:
        # async with session.get(url) as response:
            # while True:
                # data_chunk = await response.content.readline()

                # if not data_chunk:
                    # break
                # yield json. loads (data_chunk.decode("utf-8"))

# This works, non async client. Using httpx instead
# def get_idrac_sse_sseclient():
#     url = "https://169.254.1.1/redfish/v1/SSE?$filter=EventFormatType%20eq%20Event"
#     events = SSEClient(url, headers={'Content-type' : 'application/json'},
#         verify=False,
#         timeout=None,
#         auth=(user, password))
#     for event in events:
#         logger.debug(event)

# async def iter_sse_retrying(client, method, url, auth):
#     last_event_id = ""
#     reconnection_delay = 0.0

#     # `stamina` will apply jitter and exponential backoff on top of
#     # the `retry` reconnection delay sent by the server.
#     @retry(on=httpx.ReadError, attempts=retries)
#     async def _iter_sse():
#         nonlocal last_event_id, reconnection_delay

#         time.sleep(reconnection_delay)

#         headers = {"Accept": "text/event-stream"}

#         if last_event_id:
#             headers["Last-Event-ID"] = last_event_id

#         async with aconnect_sse(client, method, url, headers=headers, auth=auth) as event_source:
#             async for sse in event_source.aiter_sse():
#                 last_event_id = sse.id

#                 if sse.retry is not None:
#                     reconnection_delay = sse.retry / 1000

#                 yield sse

#     return _iter_sse()

# async def get_test_event2():
#    async with httpx.AsyncClient(verify=False, timeout=None) as client:
#         url = "http://127.0.0.1:8000/"
#         print("Connecting to %s" % (url))
#         async for sse in await iter_sse_retrying(client, "GET", url):
#         #async with aconnect_sse(client, "GET", url) as event_source:
#             #async for sse in event_source.aiter_sse():
#                 print("EVENT: %s" % sse.event)
#                 print("DATA: %s" % sse.data)
#                 print("ID: %s" % sse.id)
#                 print("RETRY: %s" % sse.retry)
#                 if sse.data != None and sse.data != "":
#                     print("DEBUG: %s" % len(sse.data))
#                     event_json = json.loads(sse.data)
#                     print(json.dumps(event_json, indent=4))

@retry(on=(httpx.HTTPError), attempts=retries)
async def get_test_event():
   async with httpx.AsyncClient(verify=False, timeout=10) as client:
        url = "http://127.0.0.1:8000/"
        print("Connecting to %s" % (url))
        async with aconnect_sse(client, "GET", url) as event_source:
            async for sse in event_source.aiter_sse():
                print("EVENT: %s" % sse.event)
                print("DATA: %s" % sse.data)
                print("ID: %s" % sse.id)
                print("RETRY :%s" % sse.retry)
                if sse.data != None and sse.data != "":
                    print("DEBUG: %s" % len(sse.data))
                    event_json = json.loads(sse.data)
                    print(json.dumps(event_json, indent=4))

async def get_idrac_sse():
   timeout = httpx.Timeout(10.0, read=None)
   async with httpx.AsyncClient(verify=False, timeout=timeout) as client:
        url = "https://192.168.5.69/redfish/v1/SSE?$filter=EventFormatType eq Event"
        print("Connecting to %s" % (url))
        print(idrac_username)
        print(idrac_password)
        async with aconnect_sse(client, "GET", url, auth=(idrac_username, idrac_password)) as event_source:
            async for sse in event_source.aiter_sse():
                print(sse.event, sse.data, sse.id, sse.retry)
                if sse.data != None and sse.data != "":
                    print("DEBUG: %s" % len(sse.data))
                    event_json = json.loads(sse.data)
                    print(json.dumps(event_json, indent=4))

async def main():
    tasks = [
        create_task(get_idrac_sse(), name="idrac_sse")
    ]

    for task in tasks:
        print(f"Started Task: {task.get_name()}")

    while tasks:    
        done, pending = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_COMPLETED
        )
        for task in done.copy():
            if task.exception() is not None:
                print('Task exited with exception:')
                task.print_stack()
                #print('Rescheduling the task\n')
                #coro, args, kwargs = tasks.pop(task)
                #tasks[asyncio.create_task(coro(*args, **kwargs))] = coro, args, kwargs

            #coro, args, kwargs = tasks.pop(0)
            # create_task(coro)
            await asyncio.sleep(10)
            tasks.append(create_task(get_idrac_sse()))
        
            print(f"Task completed {done.pop().get_name()}")

    #done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    #print(f"The first task completed was {done.pop().get_name()}")
          
asyncio.run(main())
#asyncio.run(get_test_event())
#asyncio.run(get_idrac_sse())