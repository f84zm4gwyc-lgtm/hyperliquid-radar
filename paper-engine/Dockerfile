FROM python:3.12-slim
WORKDIR /radar
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock && useradd --uid 1000 --create-home radar
COPY . /radar
RUN mkdir -p /radar/data && chown -R radar:radar /radar
USER radar
ENV RADAR_DATA_DIR=/radar/data
CMD ["python", "run.py", "trade"]
