FROM python:3.11-slim

RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn psutil

WORKDIR /app
COPY collector.py /app/

ENV WG_MONITOR_CONFIG=/etc/wg-monitor/config.json
ENV WG_MONITOR_OUTPUT=/output
ENV WG_MONITOR_INTERVAL=5

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD python3 -c "import os,time;f='/output/status-tunnel.json';exit(0 if os.path.exists(f) and time.time()-os.path.getmtime(f)<30 else 1)"

CMD ["python3", "-u", "collector.py"]
