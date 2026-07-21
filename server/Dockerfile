FROM python:3.11-alpine

WORKDIR /app

RUN pip install --no-cache-dir aiohttp psutil

COPY aggregator.py alerter.py ./

EXPOSE 9100

CMD ["python3", "-u", "aggregator.py"]
