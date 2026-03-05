FROM python:3.11-slim

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -U pip \
 && if [ -f pyproject.toml ]; then pip install --no-cache-dir .; else pip install --no-cache-dir -r requirements.txt; fi

CMD ["python", "main.py"]
