FROM python:3.12-slim
WORKDIR /opt/streamhub
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt "streamlink>=7,<9"
COPY packages/python_common ./packages/python_common
RUN pip install --no-cache-dir ./packages/python_common
COPY apps/video_recorder ./apps/video_recorder
WORKDIR /opt/streamhub/apps/video_recorder
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
