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
        fonts-dejavu-core \
        libgl1 \
        libgbm1 \
        libglib2.0-0 \
        libnspr4 \
        libnss3 \
        libasound2t64 \
        libgssapi-krb5-2 \
        libice6 \
        libsm6 \
        libx11-xcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxrender1 \
        libxshmfence1 \
        libxcb-cursor0 \
        libxcb-glx0 \
        libxcb-icccm4 \
        libxcb-image0 \
        libxcb-keysyms1 \
        libxcb-randr0 \
        libxcb-render-util0 \
        libxcb-shape0 \
        libxcb-shm0 \
        libxcb-sync1 \
        libxcb-xfixes0 \
        libxcb-xinerama0 \
        libxcb-xkb1 \
        libxkbcommon0 \
        libxkbfile1 \
        libxrandr2 \
        libxtst6 && \
    rm -rf /var/lib/apt/lists/*

COPY requirements-runtime.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements-runtime.txt && \
    python -m playwright install --with-deps chromium

COPY . .

EXPOSE 41731 41831

CMD ["python", "main_mcp.py"]
