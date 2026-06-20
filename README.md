# Hệ thống Quản lý Bất động sản

Đây là hệ thống quản lý bất động sản dành cho Quản trị viên, Quản lý và Nhân viên Sale.

## Tính năng nổi bật
- Quản lý Tài khoản (Phân quyền Admin, Manager, Sale)
- Quản lý Tài sản (Phòng trọ, Mặt bằng)
- Quản lý Chốt phòng (Giữ chỗ, Đặt cọc)
- Quản lý Hợp đồng và Khách thuê
- Quản lý Thanh toán (Hóa đơn, Thu tiền)
- Quản lý Kho vật tư (Nhập/Xuất thiết bị)
- Thống kê báo cáo doanh thu

## Công nghệ sử dụng
- **Backend:** Python (Flask)
- **Database:** SQLite
- **Frontend:** HTML, CSS, JavaScript (Giao diện thuần)
- **DevOps:** Docker, Docker Compose, GitHub Actions (CI/CD)

## Hướng dẫn cài đặt và chạy thử

Hệ thống đã được đóng gói sẵn bằng Docker, giúp chạy trên mọi môi trường mà không cần cấu hình phức tạp.

### Cách 1: Chạy bằng Docker (Khuyên dùng)
Yêu cầu máy tính đã cài đặt **Docker Desktop**. Mở Terminal tại thư mục này và gõ:
```bash
docker-compose up --build -d
```
Sau đó truy cập vào: `http://localhost:5000`

### Cách 2: Chạy thủ công bằng Python
1. Đảm bảo máy tính đã cài Python 3.10 trở lên.
2. Cài đặt thư viện:
```bash
pip install -r requirements.txt
```
3. Chạy Server:
```bash
python app.py
```
4. Truy cập vào: `http://127.0.0.1:5000`

## Thông tin đăng nhập Test
- Admin: `admin` / `admin`
- Quản lý: `manager1` / `123456`
- Sale: `sale1` / `123456`
