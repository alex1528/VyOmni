# HQ Aggregator API
# 接收分支 Agent 上报，HMAC 验证，聚合写入 status-branches.json

FROM python:3.11-slim

WORKDIR /app
COPY aggregator.py /app/

# 环境变量默认值
ENV AGG_LISTEN_HOST=0.0.0.0
ENV AGG_LISTEN_PORT=9100
ENV AGG_SECRETS=/etc/wg-monitor/secrets.json
ENV AGG_OUTPUT_DIR=/output

EXPOSE 9100

# 健康检查：HTTP 200 on /health
HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:9100/health',timeout=3)"

CMD ["python3", "-u", "aggregator.py"]
