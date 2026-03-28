# Use an official Python base image
FROM python:3.12-slim

# Install Node.js, npm, and PostgreSQL 16 client
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/postgresql.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt/ $(. /etc/os-release && echo $VERSION_CODENAME)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y postgresql-client-16 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy Python requirements and install dependencies
COPY redfishagent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install uvicorn[standard] watchfiles

# Copy the rest of the application
COPY redfishagent/ .

# Set environment variables
ENV PYTHONPATH=/app

# Expose port
EXPOSE 8000

# Start both servers
CMD ["uvicorn", "main:app", "--reload", "--reload-exclude", "services/migrations/*.py", "--reload-exclude", "models/db.py", "--timeout-graceful-shutdown", "1", "--host", "0.0.0.0", "--port", "8000"]