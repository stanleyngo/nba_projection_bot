# Dockerfile: containerize backend + frontend as ONE image.

FROM node:20-slim AS frontend-build

WORKDIR /frontend

ARG VITE_GOOGLE_CLIENT_ID
ENV VITE_GOOGLE_CLIENT_ID=${VITE_GOOGLE_CLIENT_ID}

COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.14.4-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/

COPY --from=frontend-build /frontend/dist ./src/nba_projection_bot/static

ENV PYTHONPATH=/app/src

CMD ["python", "-m", "nba_projection_bot.api"]
