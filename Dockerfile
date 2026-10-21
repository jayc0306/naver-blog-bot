FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 TZ=Asia/Seoul
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "bot.py"]
