FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install build deps for possible compiled packages

# Install build deps and curl for the healthcheck
RUN apt-get update \
	&& apt-get install -y --no-install-recommends \
		build-essential \
		gcc \
		libffi-dev \
		curl \
		ca-certificates \
	&& rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY src/ /app/
COPY redfish-dell /app/redfish-dell

RUN pip install -e /app/redfish-dell

# Run as non-root user
RUN addgroup --system app && adduser --system --ingroup app app \
	&& chown -R app:app /app
USER app

EXPOSE 8080

CMD ["fastmcp", "run", "fastmcp_server.py", "--reload", "--transport", "http", "--host", "0.0.0.0", "--port", "8080"]

