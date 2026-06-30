# THÔNG TIN TÀI KHOẢN ĐĂNG NHẬP (TEST)

Mật khẩu chung cho tất cả các tài khoản là: `123456`

- **Tài khoản Admin:** `admin`
- **Tài khoản Quản lý:** `giang01`
- **Tài khoản Sale:** `dang01`

---

# CHẠY MÃ NGUỒN BẰNG DOCKER DESKTOP

## Yêu cầu chuẩn bị
- Tải và cài đặt **Docker Desktop**.
- Mở ứng dụng Docker Desktop lên và đảm bảo nó đang chạy.

## Các bước khởi chạy

1. **Mở Terminal / Command Prompt** tại thư mục gốc của dự án (nơi có chứa file `docker-compose.yml`).
2. **Chạy lệnh sau để build và khởi động hệ thống:**
   ```bash
   docker-compose up -d --build
   ```
3. **Chờ quá trình hoàn tất**, Docker sẽ tự động tải các thành phần cần thiết và khởi tạo ứng dụng.
4. Mở trình duyệt web và truy cập vào địa chỉ:
   **[http://localhost:5000](http://localhost:5000)**

## Tắt ứng dụng
Khi không muốn sử dụng nữa, mở lại Terminal tại thư mục dự án và chạy:
```bash
docker-compose down
```
