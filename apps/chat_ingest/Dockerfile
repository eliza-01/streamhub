FROM python:3.12-slim
WORKDIR /opt/streamhub
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY packages/python_common ./packages/python_common
RUN pip install --no-cache-dir ./packages/python_common
COPY apps/chat_ingest ./apps/chat_ingest
WORKDIR /opt/streamhub/apps/chat_ingest
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
