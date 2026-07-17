FROM nginx:1.25-alpine

RUN rm -f /etc/nginx/conf.d/default.conf

COPY monitor.conf /etc/nginx/conf.d/monitor.conf

COPY ../frontend/ /var/www/monitor/

RUN mkdir -p /var/www/monitor/data

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:8080/monitor/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
