# syntax=docker/dockerfile:1.7
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS sqlite-build

# Debian's runtime SQLite does not yet include the upstream WAL-reset fix.
# Build the full 3.51.3 sources so Debian's UPDATE/DELETE LIMIT syntax is kept.
# https://sqlite.org/releaselog/3_51_3.html
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libc6-dev make tcl unzip && \
    rm -rf /var/lib/apt/lists/*

ADD --checksum=sha256:f8a67a1f5b5cae7c6d42f0994ca7bf1a4a5858868c82adc9fc1340bed5eb8cd2 \
    https://sqlite.org/2026/sqlite-src-3510300.zip /tmp/sqlite-src.zip

RUN unzip -q /tmp/sqlite-src.zip -d /tmp && \
    cd /tmp/sqlite-src-3510300 && \
    test "$(cat manifest.uuid)" = "737ae4a34738ffa0c3ff7f9bb18df914dd1cad163f28fd6b6e114a344fe6d618" && \
    CFLAGS="-O2 -fno-strict-aliasing -DSQLITE_SECURE_DELETE \
        -DSQLITE_ENABLE_COLUMN_METADATA -DSQLITE_ENABLE_FTS3_PARENTHESIS \
        -DSQLITE_ENABLE_FTS3_TOKENIZER -DSQLITE_SOUNDEX \
        -DSQLITE_ENABLE_UNLOCK_NOTIFY -DSQLITE_ENABLE_DBSTAT_VTAB \
        -DSQLITE_ENABLE_DBPAGE_VTAB -DSQLITE_ALLOW_ROWID_IN_VIEW \
        -DSQLITE_LIKE_DOESNT_MATCH_BLOBS -DSQLITE_USE_URI \
        -DSQLITE_MAX_SCHEMA_RETRY=25 -DSQLITE_ENABLE_STMTVTAB \
        -DSQLITE_MAX_VARIABLE_NUMBER=250000 \
        -DSQLITE_MAX_DEFAULT_PAGE_SIZE=32768" \
        ./configure --prefix=/usr/local --disable-static --soname=legacy --enable-threadsafe \
            --enable-load-extension --enable-fts4 --enable-fts5 --enable-rtree \
            --enable-session --enable-update-limit && \
    make -j2 libsqlite3.so && \
    install -D -m 755 libsqlite3.so /sqlite-runtime/libsqlite3.so.0

FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MINIMAL_KANBAN_SUPPRESS_ERROR_DIALOGS=1 \
    AUTOSTOP_DEPLOYMENT_ENV=production \
    HOME=/home/autostop \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN groupadd --gid 10001 autostop && \
    useradd --uid 10001 --gid 10001 --create-home \
        --home-dir /home/autostop --shell /usr/sbin/nologin autostop

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

COPY requirements-common.txt requirements-runtime.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements-runtime.txt && \
    python -m playwright install --with-deps chromium --only-shell && \
    rm -rf /var/lib/apt/lists/* && \
    chmod -R a+rX /ms-playwright

COPY --from=sqlite-build /sqlite-runtime/libsqlite3.so.0 /usr/local/lib/libsqlite3.so.0
RUN ldconfig && \
    python -c "import sqlite3; assert sqlite3.sqlite_version == '3.51.3', sqlite3.sqlite_version; source = sqlite3.connect(':memory:').execute('select sqlite_source_id()').fetchone()[0]; assert source == '2026-03-13 10:38:09 737ae4a34738ffa0c3ff7f9bb18df914dd1cad163f28fd6b6e114a344fe6d618', source; print('SQLite runtime:', sqlite3.sqlite_version, source)"

COPY . .

RUN test -s /app/src/minimal_kanban/static/favicon.png

RUN chown -R autostop:autostop /app /home/autostop

USER 10001:10001

EXPOSE 41731 41831

CMD ["python", "scripts/container_entrypoint.py"]
