FROM python:3.11-alpine

WORKDIR /app

RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple     --trusted-host pypi.tuna.tsinghua.edu.cn psutil

COPY aggregator.py alerter.py agent_common.py* ./

EXPOSE 9100

CMD ["python3", "-u", "aggregator.py"]
