FROM python:3.11-slim

WORKDIR /app

# Keine Compiler-Toolchain noetig - reine Python-Abhaengigkeiten
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

RUN useradd --create-home --shell /usr/sbin/nologin appuser
USER appuser

ENV PYTHONUNBUFFERED=1

CMD ["python", "app/main.py"]
