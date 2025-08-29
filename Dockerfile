# Monero Wallet Manager (FastAPI) - Alpine
FROM python:3.11-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Build deps for mariadb connector
RUN apk add --no-cache build-base gcc musl-dev linux-headers libffi-dev mariadb-connector-c-dev python3-dev

WORKDIR /app
COPY MoneroWalletManager/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy app
COPY MoneroWalletManager/app /app/app
COPY MoneroWalletManager/.env /app/.env

EXPOSE 8004

CMD ["/bin/sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${MONERO_WALLET_MANAGER_PORT:-8004}"]