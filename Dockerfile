# Base image with SSL working out of the box
FROM python:3.11

WORKDIR /opt/ping_setter_hll

# Install system packages
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY . /opt/ping_setter_hll/

# Install Python dependencies globally (this is okay in Docker)
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

EXPOSE 8000

CMD ["python", "manage.py", "run_discord"]

