FROM python:3.12-slim
WORKDIR /opt/streamhub
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY packages/python_common ./packages/python_common
RUN pip install --no-cache-dir ./packages/python_common
COPY apps/registration_bot ./apps/registration_bot
WORKDIR /opt/streamhub/apps/registration_bot
CMD ["python", "-m", "app.main"]
