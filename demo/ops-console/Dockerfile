FROM python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.org/simple/
ARG PIP_TRUSTED_HOST=
ARG PIP_DEFAULT_TIMEOUT=300
ARG PIP_RETRIES=5

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python seed.py

RUN useradd --system --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 9099

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9099"]
