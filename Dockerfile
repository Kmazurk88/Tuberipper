FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Deno: yt-dlp needs a JavaScript runtime for YouTube (since Nov 2025).
# Latest release at build time; the weekly scheduled build keeps it current.
ADD https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip /tmp/deno.zip
RUN python -c "import zipfile; zipfile.ZipFile('/tmp/deno.zip').extract('deno', '/usr/local/bin')" \
    && chmod +x /usr/local/bin/deno && rm /tmp/deno.zip && deno --version

WORKDIR /app

COPY requirements.txt .
# --upgrade so every build (including the weekly scheduled one) gets the
# newest yt-dlp -- YouTube breaks old versions regularly.
RUN pip install --no-cache-dir --upgrade -r requirements.txt

COPY tuberipper ./tuberipper

EXPOSE 8000 8001

CMD ["python", "-m", "tuberipper.run"]
