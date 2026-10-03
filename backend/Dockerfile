# Cadastra Vision API
# Build from the repository root:  docker build -f backend/Dockerfile -t cadastra-vision-api .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# CPU-only PyTorch keeps the image small. For GPU inference, install the
# CUDA build of torch instead and run with the NVIDIA container runtime.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch \
 && pip install -r backend/requirements.txt

COPY backend backend

# Model weights and survey data are mounted at run time, not baked in:
#   -v /srv/cadastra/models:/app/backend/models
#   -v /srv/cadastra/data:/app/backend/data
#   -v /srv/cadastra/processing:/app/backend/processing
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
