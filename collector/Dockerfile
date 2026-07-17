# HQ Tunnel Collector
# 采集 WireGuard 隧道状态，输出 status-tunnel.json

FROM python:3.11-slim

RUN pip install --no-cache-dir psutil

WORKDIR /app
COPY collector.py /app/

# 环境变量默认值
ENV WG_MONITOR_CONFIG=/etc/wg-monitor/config.json
ENV WG_MONITOR_OUTPUT=/output
ENV WG_MONITOR_INTERVAL=5

# 健康检查：检测输出文件是否在 30 秒内更新
HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD python3 -c "import os,time;f='/output/status-tunnel.json';exit(0 if os.path.exists(f) and time.time()-os.path.getmtime(f)<30 else 1)"

CMD ["python3", "-u", "collector.py"]
