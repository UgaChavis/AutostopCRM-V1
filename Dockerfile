FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MINIMAL_KANBAN_SUPPRESS_ERROR_DIALOGS=1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libdbus-1-3 \
        libegl1 \
        libfontconfig1 \
        libgl1 \
        libglib2.0-0 \
        libxkbcommon0 && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 41731 41831

CMD ["python", "main_mcp.py"]
