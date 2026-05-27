FROM python:3.12-slim

WORKDIR /app
COPY . .

ENV PORT=8765
ENV SMP_DATA_DIR=/data

EXPOSE 8765
CMD ["python", "app.py"]
