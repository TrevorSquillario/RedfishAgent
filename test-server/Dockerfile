
FROM python:3.9

WORKDIR /app

COPY ./requirements.txt /app/requirements.txt

RUN  apt-get update -y && apt-get install -y iputils-ping
RUN pip install --no-cache-dir --upgrade -r /app/requirements.txt

COPY ./src/ /app/

CMD ["uvicorn", "server:app", "--reload","--host", "0.0.0.0", "--port", "443", "--ssl-keyfile", "/certs/server.key", "--ssl-certfile", "/certs/server.crt"]