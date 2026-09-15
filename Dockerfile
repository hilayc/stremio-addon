FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home addon && mkdir /data && chown addon:addon /data
COPY addon ./addon
COPY generate_session.py .
USER addon
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('port',os.getenv('PORT','8000'))+'/healthz')"
CMD ["python", "-m", "addon"]
