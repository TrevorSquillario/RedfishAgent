
FROM python:3.12-slim

WORKDIR /app

COPY ./redfish-mockup-server/requirements.txt /app/requirements.txt

RUN  apt-get update -y && apt-get install -y iputils-ping
RUN pip install --no-cache-dir --upgrade -r /app/requirements.txt

COPY ./redfish-mockup-server/src/ /app/

ENV PYTHONUNBUFFERED=1
EXPOSE 443

# Use explicit reload directory so uvicorn watches the mounted /app directory
CMD ["uvicorn", "server:app", "--reload", "--reload-dir", "/app", "--host", "0.0.0.0", "--port", "443", "--ssl-keyfile", "/certs/server.key", "--ssl-certfile", "/certs/server.crt"]