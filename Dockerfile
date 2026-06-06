FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app/src

# Instalar dependências do sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código
COPY src/ ./src/
COPY tenants/ ./tenants/
COPY harnesses/ ./harnesses/

# Usuário não-root
RUN useradd -m zwaf && chown -R zwaf:zwaf /app
USER zwaf

EXPOSE 8000

CMD ["uvicorn", "zwaf.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
