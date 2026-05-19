# syntax=docker/dockerfile:1
# QQ 机器人容器镜像（部署预留）
# 构建：docker build -t chinese-semantle-qqbot -f qqbot/Dockerfile .
# 运行：docker run -d --name semantle-qqbot --restart=always \
#         -v $(pwd)/qqbot/config.yaml:/app/qqbot/config.yaml:ro \
#         chinese-semantle-qqbot
#
# 或使用环境变量注入凭据（更推荐用于服务器部署）：
#   docker run -d --name semantle-qqbot --restart=always \
#     -e QQBOT_APPID=xxx -e QQBOT_APPSECRET=xxx \
#     -v $(pwd)/qqbot/config.yaml:/app/qqbot/config.yaml:ro \
#     chinese-semantle-qqbot

FROM python:3.11-slim

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=https://mirrors.tencent.com/pypi/simple

WORKDIR /app

# 仅安装 qqbot 依赖（与 backend 完全隔离）
COPY qqbot/requirements.txt /app/qqbot/requirements.txt
RUN pip install --no-cache-dir -r /app/qqbot/requirements.txt

# 复制源代码
COPY qqbot/ /app/qqbot/

# 时区（建议挂载主机时区，或在编排里覆盖）
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 默认入口（长连接进程，Docker restart 策略保活）
CMD ["python", "-m", "qqbot.bot"]
