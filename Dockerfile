FROM python:3.13-slim

WORKDIR /app

# 先装依赖，利用 Docker 层缓存：requirements.txt 不变时不用重装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷贝源码
COPY app/ ./app/

EXPOSE 8010

# 用 curl 探活 /health，配合 docker-compose 的 depends_on.condition: service_healthy
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]
