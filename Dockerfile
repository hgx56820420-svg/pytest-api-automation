FROM python:3.13-slim

WORKDIR /app

# 先装依赖，利用 Docker 层缓存：requirements.txt 不变时不用重装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY examples/ ./examples/

EXPOSE 8010

# 密钥和管理员账号通过环境变量传入：APP_SECRET_KEY、APP_ADMIN_USERNAME、APP_ADMIN_PASSWORD
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/health')" || exit 1

CMD ["uvicorn", "examples.mini_shop.main:app", "--host", "0.0.0.0", "--port", "8010"]
