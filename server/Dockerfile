FROM python:3.11-alpine

WORKDIR /app

RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple     --trusted-host pypi.tuna.tsinghua.edu.cn psutil

# 平台代码
COPY server/aggregator.py server/alerter.py ./

# Agent 文件（供 /api/deploy/files/ 下载端点使用）
COPY agent/ /app/agent/

EXPOSE 9100

CMD ["python3", "-u", "aggregator.py"]
