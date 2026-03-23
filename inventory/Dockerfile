FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install build deps and runtime deps
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy application code
COPY src/ /app/

# Copy example config into /config so it can be overridden by a bind mount
COPY config/ /config/

EXPOSE 8080

CMD ["uvicorn", "inventory:app", "--host", "0.0.0.0", "--port", "8080"]
