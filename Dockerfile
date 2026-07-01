FROM python:3.10-slim

# Cài đặt thư mục làm việc trong container
WORKDIR /app

# Thiết lập biến môi trường để log chạy mượt mà
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Copy file requirements.txt vào container
COPY requirements.txt /app/

# Cài đặt các thư viện cần thiết
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ mã nguồn dự án vào container
COPY . /app/

# Mở cổng 5000 cho Flask
EXPOSE 5000

# Lệnh khởi chạy server bằng gunicorn (Production-ready)
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]
