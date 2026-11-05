# 🔧 TODO: lengkapi Dockerfile berikut mengikuti pola yang sudah dipelajari
# di Sesi 14.2 (FROM python -> WORKDIR -> COPY -> RUN pip -> CMD).

FROM python:3.11-slim

WORKDIR /app

# TODO: copy requirements.txt terlebih dahulu, lalu install dependencies
# (praktik yang baik agar Docker layer caching bekerja optimal)

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# TODO: copy seluruh source code aplikasi ke dalam container

COPY . .

# TODO: tentukan port yang akan digunakan (misal 8000) dan expose port tersebut

EXPOSE 8000

# TODO: tentukan CMD untuk menjalankan aplikasi FastAPI menggunakan uvicorn

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]