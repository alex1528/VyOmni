# Nginx + Dashboard 前端
# 托管静态文件 + 反向代理聚合器 API

FROM nginx:1.25-alpine

# 删除默认配置
RUN rm -f /etc/nginx/conf.d/default.conf

# 复制 Nginx 站点配置
COPY monitor.conf /etc/nginx/sites-enabled/monitor.conf

# 复制前端文件
COPY ../frontend/ /var/www/monitor/

# 创建数据目录
RUN mkdir -p /var/www/monitor/data

EXPOSE 8080

# 健康检查
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:8080/monitor/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
