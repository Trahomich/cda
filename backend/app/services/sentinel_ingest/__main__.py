"""python -m app.services.sentinel_ingest — запуск воркера sentinel-ingest."""

from app.services.sentinel_ingest.worker import main

if __name__ == "__main__":
    main()
