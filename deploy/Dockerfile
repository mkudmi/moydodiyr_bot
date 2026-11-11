FROM python:3.12-slim
WORKDIR /app
COPY deploy/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY main.py catalog.json ./
COPY moydodyr/ moydodyr/
COPY assets/ assets/
CMD ["python", "main.py"]
