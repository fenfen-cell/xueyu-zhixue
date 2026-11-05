FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends nodejs curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . /app
ENV PORT=8080 PYTHONUTF8=1 PYTHONUNBUFFERED=1 XY_NODE_EXECUTABLE=/usr/bin/node
RUN if [ -d /app/local-ocr/vendor ] && [ ! -d /app/local-ocr/node_modules ]; then \
      cp -r /app/local-ocr/vendor /app/local-ocr/node_modules; fi
EXPOSE 8080
CMD ["python", "serve_cloud.py"]
