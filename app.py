import os
import sqlite3
from datetime import datetime, date
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash

app = Flask(__name__)
app.secret_key = 'super_secret_antigravity_key_for_session'

DATABASE = os.path.join(os.path.dirname(__file__), 'db', 'MyDatabase.db')

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def add_months(sourcedate, months):
    import calendar
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    day = min(sourcedate.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)

@app.before_request
def check_contract_activation():
    if request.path.startswith('/static'):
        return
    db = get_db()
    try:
        cursor = db.cursor()
        # 1. Tự động di cư các phiếu cọc cũ chưa có HĐ
        old_bookings = cursor.execute(
            """
            SELECT tk.*, ts.HanThanhToan
            FROM THONG_TIN_CHOT_KHACH tk
            JOIN TAI_SAN ts ON tk.MaTaiSan = ts.MaTaiSan
            WHERE tk.MaHopDong IS NULL AND tk.TrangThai = 'Da Duyet'
            """
        ).fetchall()
        
        if old_bookings:
            for b in old_bookings:
                cursor.execute(
                    "INSERT INTO KHACH_THUE (HoTen, SoDienThoai, CCCD) VALUES (?, ?, ?)",
                    (b['TenKhach'], b['SoDienThoaiKhach'], b['CCCD'])
                )
                khach_thue_id = cursor.lastrowid
                
                duration = 6
                start_dt = datetime.strptime(b['NgayDuKienVaoO'], '%Y-%m-%d').date()
                end_dt = add_months(start_dt, duration)
                
                cursor.execute(
                    """
                    INSERT INTO HOP_DONG (MaTaiSan, MaKhachThue, NgayBatDau, NgayKetThuc, ThoiHanThue, TienCoc, TrangThai, GhiChu, GiaThue)
                    VALUES (?, ?, ?, ?, ?, ?, 'Giữ phòng', ?, ?)
                    """,
                    (b['MaTaiSan'], khach_thue_id, b['NgayDuKienVaoO'], end_dt.isoformat(), duration, b['TienCoc'], b['GhiChu'], b['GiaThue'])
                )
                hop_dong_id = cursor.lastrowid
                
                han_thanh_toan = b['HanThanhToan'] if b['HanThanhToan'] is not None else 15
                if start_dt.day < han_thanh_toan:
                    next_ca_thu = date(start_dt.year, start_dt.month, han_thanh_toan)
                else:
                    next_month = start_dt.month + 1
                    next_year = start_dt.year
                    if next_month > 12:
                        next_month = 1
                        next_year += 1
                    next_ca_thu = date(next_year, next_month, han_thanh_toan)
                    
                actual_days = (next_ca_thu - start_dt).days
                if actual_days > 30:
                    actual_days = 30
                tien_nha_le = (b['GiaThue'] / 30.0) * actual_days
                
                config = cursor.execute("SELECT * FROM CAU_HINH_GIA WHERE TrangThai = 1 LIMIT 1").fetchone()
                gia_dien = config['GiaDien'] if config else 4000.00
                gia_dv = config['GiaDichVu'] if config else 250000.00
                tien_dich_vu_le = ((gia_dv / 1000.0) / 30.0) * actual_days
                tong_tien_hoa_don_1 = tien_nha_le + tien_dich_vu_le + b['TienCoc']
                
                cursor.execute(
                    """
                    INSERT INTO HOA_DON 
                    (MaHopDong, Thang, Nam, TienNha, SoDien, GiaDien, TienDien, GiaDichVu, TienDichVu, TongTien, NgayTao, TrangThaiThanhToan)
                    VALUES (?, ?, ?, ?, 0, ?, 0.00, ?, ?, ?, ?, 'Chưa thanh toán')
                    """,
                    (hop_dong_id, start_dt.month, start_dt.year, tien_nha_le, gia_dien, gia_dv, tien_dich_vu_le, tong_tien_hoa_don_1, date.today().isoformat())
                )
                
                cursor.execute(
                    """
                    UPDATE THONG_TIN_CHOT_KHACH 
                    SET TrangThai = 'Da Chuyen Thanh Hop Dong', MaKhachThue = ?, MaHopDong = ?
                    WHERE MaChotKhach = ?
                    """,
                    (khach_thue_id, hop_dong_id, b['MaChotKhach'])
                )
                
                cursor.execute(
                    "UPDATE TAI_SAN SET TrangThai = 'Giữ phòng' WHERE MaTaiSan = ?",
                    (b['MaTaiSan'],)
                )
            db.commit()

        # 2. Lập lịch tự động kích hoạt hợp đồng (Chỉ kích hoạt khi đóng đủ tiền hệ thống tính hoặc đã thanh toán hóa đơn đầu)
        today_str = date.today().isoformat()
        contracts = db.execute(
            """
            SELECT h.MaHopDong, h.MaTaiSan, h.TienCoc, 
                   (SELECT TongTien FROM HOA_DON WHERE MaHopDong = h.MaHopDong ORDER BY MaHoaDon ASC LIMIT 1) as TongTienHoaDon,
                   (SELECT TrangThaiThanhToan FROM HOA_DON WHERE MaHopDong = h.MaHopDong ORDER BY MaHoaDon ASC LIMIT 1) as TinhTrangHoaDon
            FROM HOP_DONG h 
            WHERE h.TrangThai = 'Giữ phòng'
            """
        ).fetchall()
        if contracts:
            for c in contracts:
                # Điều kiện: Không có hóa đơn, HOẶC cọc đủ tiền, HOẶC hóa đơn đầu đã thanh toán
                if (c['TongTienHoaDon'] is None) or (c['TienCoc'] >= c['TongTienHoaDon']) or (c['TinhTrangHoaDon'] == 'Đã thanh toán'):
                    db.execute(
                        "UPDATE HOP_DONG SET TrangThai = 'Đang kích hoạt' WHERE MaHopDong = ?",
                        (c['MaHopDong'],)
                    )
                    db.execute(
                        "UPDATE TAI_SAN SET TrangThai = 'DangThue' WHERE MaTaiSan = ?",
                        (c['MaTaiSan'],)
                    )
            db.commit()
    except Exception as e:
        print("Lỗi tự động kích hoạt/di cư hợp đồng:", e)
    finally:
        db.close()


# Helper decorator for authentication
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Helper decorator for role authorization
def role_required(roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            if session.get('role_id') not in roles:
                flash("Bạn không có quyền truy cập trang này!", "error")
                # Redirect to appropriate dashboard based on actual role
                actual_role = session.get('role_id')
                if actual_role == 1:
                    return redirect(url_for('admin_dashboard'))
                elif actual_role == 2:
                    return redirect(url_for('manager_dashboard'))
                elif actual_role == 3:
                    return redirect(url_for('sale_dashboard'))
                return redirect(url_for('logout'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@app.route('/')
def index():
    if 'user_id' in session:
        actual_role = session.get('role_id')
        if actual_role == 1:
            return redirect(url_for('admin_dashboard'))
        elif actual_role == 2:
            return redirect(url_for('shared_tracking'))
        elif actual_role == 3:
            return redirect(url_for('sale_dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        db = get_db()
        user = db.execute(
            'SELECT * FROM TAI_KHOAN WHERE TenDangNhap = ?', (username,)
        ).fetchone()
        db.close()
        
        if user and user['MatKhau'] == password:
            if user['TrangThai'] == 1:
                session['user_id'] = user['MaTaiKhoan']
                session['username'] = user['TenDangNhap']
                session['fullname'] = user['HoTen']
                session['role_id'] = user['MaVaiTro']
                
                flash(f"Chào mừng {user['HoTen']} đã đăng nhập thành công!", "success")
                return redirect(url_for('index'))
            else:
                flash("Tài khoản của bạn đã bị khóa! Vui lòng liên hệ Admin.", "error")
        else:
            flash("Tên đăng nhập hoặc mật khẩu không chính xác!", "error")
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash("Bạn đã đăng xuất khỏi hệ thống.", "success")
    return redirect(url_for('login'))


# ==========================================
# PHÂN HỆ DÀNH CHO CTV / SALE (ROLE = 3)
# ==========================================

@app.route('/sale/dashboard')
@role_required([3])
def sale_dashboard():
    user_id = session['user_id']
    db = get_db()
    
    # Khối 1: Tổng số phòng trống toàn hệ thống
    empty_rooms = db.execute(
        """
        SELECT ts.*, tk.HoTen as TenNhanVienChotCu 
        FROM TAI_SAN ts 
        LEFT JOIN TAI_KHOAN tk ON ts.NhanVienChotCu = tk.MaTaiKhoan 
        WHERE ts.TrangThai = 'Trong'
        ORDER BY ts.KhuVuc, ts.DiaChi, ts.SoPhong
        """
    ).fetchall()
    empty_rooms_count = len(empty_rooms)
    
    # Khối 2: Tổng số lượt chốt khách thành công của tài khoản Sale này
    my_deals_count = db.execute(
        "SELECT COUNT(*) FROM THONG_TIN_CHOT_KHACH WHERE MaTaiKhoanChot = ?", (user_id,)
    ).fetchone()[0]
    
    # Khối 3: Danh sách phòng chốt toàn hệ thống
    bookings = db.execute(
        """
        SELECT tk.*, ts.SoPhong, ts.DiaChi, ts.GiaThue, ts.HoaHong as HoaHongTaiSan,
               u.HoTen as TenNhanVienChot, kt.HoTen as TenKhachThue, hd.MaHopDong as HopDongKichHoat, hd.TrangThai as TrangThaiHopDong
        FROM THONG_TIN_CHOT_KHACH tk 
        JOIN TAI_SAN ts ON tk.MaTaiSan = ts.MaTaiSan 
        JOIN TAI_KHOAN u ON tk.MaTaiKhoanChot = u.MaTaiKhoan 
        LEFT JOIN KHACH_THUE kt ON tk.MaKhachThue = kt.MaKhachThue 
        LEFT JOIN HOP_DONG hd ON tk.MaHopDong = hd.MaHopDong 
        ORDER BY tk.MaChotKhach DESC
        """
    ).fetchall()
    
    # Lấy danh sách các Khu Vực để làm bộ lọc
    areas = [r['KhuVuc'] for r in db.execute(
        "SELECT DISTINCT KhuVuc FROM TAI_SAN WHERE TrangThai = 'Trong' ORDER BY KhuVuc"
    ).fetchall()]
    
    # KPI logic for current month
    config = db.execute("SELECT ChiTieuKPI FROM CAU_HINH_GIA WHERE TrangThai = 1 LIMIT 1").fetchone()
    chi_tieu_kpi = config['ChiTieuKPI'] if config else 6
    
    current_month = date.today().month
    current_year = date.today().year
    start_date = f"{current_year}-{current_month:02d}-01"
    if current_month == 12:
        end_date = f"{current_year+1}-01-01"
    else:
        end_date = f"{current_year}-{current_month+1:02d}-01"
        
    my_deals_this_month = db.execute(
        """
        SELECT COUNT(*) FROM THONG_TIN_CHOT_KHACH 
        WHERE MaTaiKhoanChot = ? AND NgayChot >= ? AND NgayChot < ?
        """, (user_id, start_date, end_date)
    ).fetchone()[0]
    
    sale_info = db.execute("SELECT LuongCung FROM TAI_KHOAN WHERE MaTaiKhoan = ?", (user_id,)).fetchone()
    luong_cung = float(sale_info['LuongCung'] if sale_info and sale_info['LuongCung'] else 0.0)
    dat_kpi = my_deals_this_month >= chi_tieu_kpi
    
    db.close()
    
    current_date = date.today().strftime('%d/%m/%Y')
    
    return render_template(
        'sale_dashboard.html', 
        total_empty_rooms=empty_rooms_count,
        total_my_deals=my_deals_count,
        bookings=bookings,
        empty_rooms=empty_rooms,
        areas=areas,
        current_date=current_date,
        chi_tieu_kpi=chi_tieu_kpi,
        my_deals_this_month=my_deals_this_month,
        luong_cung=luong_cung,
        dat_kpi=dat_kpi,
        current_month=current_month,
        current_year=current_year
    )




@app.route('/sale/search')
@role_required([3])
def sale_search():
    db = get_db()
    
    # Tra cứu toàn bộ các phòng trống
    rooms = db.execute(
        "SELECT * FROM TAI_SAN WHERE TrangThai = 'Trong' ORDER BY KhuVuc, DiaChi, SoPhong"
    ).fetchall()
    
    # Lấy danh sách các Khu Vực để làm bộ lọc
    areas = [r['KhuVuc'] for r in db.execute(
        "SELECT DISTINCT KhuVuc FROM TAI_SAN WHERE TrangThai = 'Trong' ORDER BY KhuVuc"
    ).fetchall()]
    
    db.close()
    
    return render_template('sale_search.html', rooms=rooms, areas=areas)

@app.route('/sale/chot-coc/<int:room_id>', methods=['GET', 'POST'])
@role_required([3])
def sale_chot_coc(room_id):
    db = get_db()
    room = db.execute(
        "SELECT * FROM TAI_SAN WHERE MaTaiSan = ? AND TrangThai = 'Trong'", (room_id,)
    ).fetchone()
    
    if not room:
        db.close()
        flash("Phòng này đã được thuê, được chốt cọc hoặc không tồn tại!", "error")
        return redirect(url_for('sale_dashboard'))
        
    if request.method == 'POST':
        tenant_name = request.form.get('tenant_name')
        tenant_phone = request.form.get('tenant_phone')
        tenant_cccd = request.form.get('tenant_cccd')
        deposit_amount = float(request.form.get('deposit_amount'))
        gia_thue = float(request.form.get('gia_thue'))
        
        if gia_thue % 100 != 0:
            flash("Giá thuê phải chẵn theo 100k (không được chốt lẻ 50k)!", "error")
            return redirect(url_for('sale_chot_coc', room_id=room_id))
            
        move_in_date = request.form.get('move_in_date')
        duration = int(request.form.get('duration'))
        so_nguoi = int(request.form.get('so_nguoi', 1))
        elec_start = int(request.form.get('elec_start'))
        notes = request.form.get('notes')
        
        try:
            cursor = db.cursor()
            # 1. Thêm khách thuê vào bảng KHACH_THUE
            cursor.execute(
                "INSERT INTO KHACH_THUE (HoTen, SoDienThoai, CCCD) VALUES (?, ?, ?)",
                (tenant_name, tenant_phone, tenant_cccd)
            )
            khach_thue_id = cursor.lastrowid
            
            # Tính toán ngày hợp đồng
            start_dt = datetime.strptime(move_in_date, '%Y-%m-%d').date()
            end_dt = add_months(start_dt, duration)
            end_date_str = end_dt.isoformat()
            
            # 2. Thêm hợp đồng mới với trạng thái Giữ phòng
            cursor.execute(
                """
                INSERT INTO HOP_DONG (MaTaiSan, MaKhachThue, NgayBatDau, NgayKetThuc, ThoiHanThue, TienCoc, TrangThai, GhiChu, GiaThue, SoNguoi)
                VALUES (?, ?, ?, ?, ?, ?, 'Giữ phòng', ?, ?, ?)
                """,
                (room_id, khach_thue_id, move_in_date, end_date_str, duration, deposit_amount, notes, gia_thue, so_nguoi)
            )
            hop_dong_id = cursor.lastrowid
            
            # 3. Thuật toán tính tiền lẻ ngày (Pro-rated Billing)
            han_thanh_toan = room['HanThanhToan'] if room['HanThanhToan'] is not None else 15
            
            # Tìm ngày ca thu tiếp theo
            if start_dt.day < han_thanh_toan:
                next_ca_thu = date(start_dt.year, start_dt.month, han_thanh_toan)
            else:
                next_month = start_dt.month + 1
                next_year = start_dt.year
                if next_month > 12:
                    next_month = 1
                    next_year += 1
                next_ca_thu = date(next_year, next_month, han_thanh_toan)
                
            actual_days = (next_ca_thu - start_dt).days
            if actual_days > 30:
                actual_days = 30
            
            # Tính tiền lẻ
            gia_phong_goc = gia_thue # in thousands (k)
            tien_nha_le = (gia_phong_goc / 30.0) * actual_days
            
            # Đọc cấu hình dịch vụ hiện tại
            config = db.execute(
                "SELECT * FROM CAU_HINH_GIA WHERE TrangThai = 1 LIMIT 1"
            ).fetchone()
            
            if not config:
                gia_dien = 4000.00
                gia_dv = 250000.00
            else:
                gia_dien = config['GiaDien']
                gia_dv = config['GiaDichVu']
                
            # Tiền dịch vụ lẻ ngày (trong DB lưu là nghìn đồng, nên chia 1000)
            tien_dich_vu_le = (((gia_dv * so_nguoi) / 1000.0) / 30.0) * actual_days
            
            # Tổng tiền lẻ ngày hóa đơn đầu tiên = Tiền nhà lẻ + Tiền dịch vụ lẻ + Tiền cọc thực tế (1 tháng tiền phòng)
            tong_tien_hoa_don_1 = tien_nha_le + tien_dich_vu_le + gia_thue
            
            # 4. Thêm bản ghi hóa đơn
            cursor.execute(
                """
                INSERT INTO HOA_DON 
                (MaHopDong, Thang, Nam, TienNha, SoDien, GiaDien, TienDien, GiaDichVu, TienDichVu, TongTien, NgayTao, TrangThaiThanhToan)
                VALUES (?, ?, ?, ?, 0, ?, 0.00, ?, ?, ?, ?, 'Chưa thanh toán')
                """,
                (hop_dong_id, start_dt.month, start_dt.year, tien_nha_le, gia_dien, gia_dv, tien_dich_vu_le, tong_tien_hoa_don_1, date.today().isoformat())
            )
            
            # 5. Ghi nhận chốt khách
            cursor.execute(
                """
                INSERT INTO THONG_TIN_CHOT_KHACH 
                (MaTaiSan, MaTaiKhoanChot, TenKhach, SoDienThoaiKhach, CCCD, TienCoc, NgayChot, NgayDuKienVaoO, SoDienDauVao, TrangThai, MaKhachThue, MaHopDong, GhiChu, GiaThue)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Da Chuyen Thanh Hop Dong', ?, ?, ?, ?)
                """,
                (room_id, session['user_id'], tenant_name, tenant_phone, tenant_cccd, deposit_amount, date.today().isoformat(), move_in_date, elec_start, khach_thue_id, hop_dong_id, notes, gia_thue)
            )
            
            # 6. Khóa phòng lập tức ở trạng thái 'Giữ phòng'
            cursor.execute(
                "UPDATE TAI_SAN SET TrangThai = 'Giữ phòng' WHERE MaTaiSan = ?", (room_id,)
            )
            db.commit()
            flash(f"Chốt giữ chỗ phòng {room['SoPhong']} cho khách {tenant_name} thành công và lập hợp đồng tự động!", "success")
        except Exception as e:
            db.rollback()
            flash(f"Lỗi hệ thống khi chốt cọc: {str(e)}", "error")
        finally:
            db.close()
            
        return redirect(url_for('sale_dashboard'))
        
    config = db.execute("SELECT * FROM CAU_HINH_GIA WHERE TrangThai = 1 LIMIT 1").fetchone()
    gia_dv = config['GiaDichVu'] / 1000.0 if config else 250.0
    db.close()
    return render_template('sale_chot_coc.html', room=room, gia_dv=gia_dv)


@app.route('/sale/booking/edit/<int:deal_id>', methods=['GET', 'POST'])
@role_required([3])
def sale_booking_edit(deal_id):
    db = get_db()
    # Kiểm tra phiếu chốt khách có tồn tại và thuộc về Sale này
    deal = db.execute(
        """
        SELECT tk.*, hd.ThoiHanThue
        FROM THONG_TIN_CHOT_KHACH tk
        LEFT JOIN HOP_DONG hd ON tk.MaHopDong = hd.MaHopDong
        WHERE tk.MaChotKhach = ? AND tk.MaTaiKhoanChot = ?
        """, (deal_id, session['user_id'])
    ).fetchone()
    
    if not deal:
        db.close()
        flash("Phiếu chốt khách không tồn tại hoặc không thuộc quyền quản lý của bạn!", "error")
        return redirect(url_for('sale_bookings'))
        
    # Kiểm tra hợp đồng xem có còn ở trạng thái Giữ phòng không
    contract = db.execute("SELECT * FROM HOP_DONG WHERE MaHopDong = ?", (deal['MaHopDong'],)).fetchone()
    if not contract or contract['TrangThai'] != 'Giữ phòng':
        db.close()
        flash("Hợp đồng này đã có hiệu lực hoặc kết thúc, không thể chỉnh sửa cọc!", "error")
        return redirect(url_for('sale_bookings'))
        
    if request.method == 'POST':
        tenant_name = request.form.get('tenant_name')
        tenant_phone = request.form.get('tenant_phone')
        tenant_cccd = request.form.get('tenant_cccd')
        deposit_amount = float(request.form.get('deposit_amount'))
        gia_thue = float(request.form.get('gia_thue'))
        move_in_date = request.form.get('move_in_date')
        duration = int(request.form.get('duration'))
        elec_start = int(request.form.get('elec_start'))
        so_nguoi = int(request.form.get('so_nguoi', 1))
        notes = request.form.get('notes')
        
        try:
            cursor = db.cursor()
            
            # Tính toán lại ngày hợp đồng
            start_dt = datetime.strptime(move_in_date, '%Y-%m-%d').date()
            end_dt = add_months(start_dt, duration)
            end_date_str = end_dt.isoformat()
            
            # Cập nhật THONG_TIN_CHOT_KHACH
            cursor.execute(
                """
                UPDATE THONG_TIN_CHOT_KHACH
                SET TenKhach = ?, SoDienThoaiKhach = ?, CCCD = ?, TienCoc = ?, NgayDuKienVaoO = ?, GhiChu = ?, SoDienDauVao = ?, GiaThue = ?
                WHERE MaChotKhach = ?
                """,
                (tenant_name, tenant_phone, tenant_cccd, deposit_amount, move_in_date, notes, elec_start, gia_thue, deal_id)
            )
            
            # Cập nhật KHACH_THUE
            if deal['MaKhachThue']:
                cursor.execute(
                    """
                    UPDATE KHACH_THUE
                    SET HoTen = ?, SoDienThoai = ?, CCCD = ?
                    WHERE MaKhachThue = ?
                    """,
                    (tenant_name, tenant_phone, tenant_cccd, deal['MaKhachThue'])
                )
                
            # Cập nhật HOP_DONG
            if deal['MaHopDong']:
                cursor.execute(
                    """
                    UPDATE HOP_DONG
                    SET NgayBatDau = ?, NgayKetThuc = ?, ThoiHanThue = ?, TienCoc = ?, GhiChu = ?, GiaThue = ?, SoNguoi = ?
                    WHERE MaHopDong = ?
                    """,
                    (move_in_date, end_date_str, duration, deposit_amount, notes, gia_thue, so_nguoi, deal['MaHopDong'])
                )
                
            # Tính toán lại hóa đơn đầu tiên (nếu chưa thanh toán)
            first_invoice = cursor.execute(
                "SELECT MaHoaDon FROM HOA_DON WHERE MaHopDong = ? AND TrangThaiThanhToan = 'Chưa thanh toán' ORDER BY MaHoaDon ASC LIMIT 1",
                (deal['MaHopDong'],)
            ).fetchone()
            
            if first_invoice:
                room = cursor.execute("SELECT * FROM TAI_SAN WHERE MaTaiSan = ?", (deal['MaTaiSan'],)).fetchone()
                han_thanh_toan = room['HanThanhToan'] if room['HanThanhToan'] is not None else 15
                
                if start_dt.day < han_thanh_toan:
                    next_ca_thu = date(start_dt.year, start_dt.month, han_thanh_toan)
                else:
                    next_month = start_dt.month + 1
                    next_year = start_dt.year
                    if next_month > 12:
                        next_month = 1
                        next_year += 1
                    next_ca_thu = date(next_year, next_month, han_thanh_toan)
                    
                actual_days = (next_ca_thu - start_dt).days
                if actual_days > 30:
                    actual_days = 30
                
                # Pro-rated calculations
                gia_phong_goc = gia_thue
                tien_nha_le = (gia_phong_goc / 30.0) * actual_days
                
                config = cursor.execute("SELECT * FROM CAU_HINH_GIA WHERE TrangThai = 1 LIMIT 1").fetchone()
                if not config:
                    gia_dien = 4000.00
                    gia_dv = 250000.00
                else:
                    gia_dien = config['GiaDien']
                    gia_dv = config['GiaDichVu']
                    
                tien_dich_vu_le = (((gia_dv * so_nguoi) / 1000.0) / 30.0) * actual_days
                tong_tien_hoa_don_1 = tien_nha_le + tien_dich_vu_le + gia_thue
                
                cursor.execute(
                    """
                    UPDATE HOA_DON
                    SET Thang = ?, Nam = ?, TienNha = ?, GiaDien = ?, TienDichVu = ?, TongTien = ?, NgayTao = ?
                    WHERE MaHoaDon = ?
                    """,
                    (start_dt.month, start_dt.year, tien_nha_le, gia_dien, tien_dich_vu_le, tong_tien_hoa_don_1, date.today().isoformat(), first_invoice['MaHoaDon'])
                )
                
            db.commit()
            flash("Cập nhật thông tin phiếu cọc và hóa đơn giữ chỗ thành công!", "success")
        except Exception as e:
            db.rollback()
            flash(f"Lỗi khi cập nhật thông tin: {str(e)}", "error")
        finally:
            db.close()
            
        return redirect(url_for('sale_bookings'))
        
    room = db.execute("SELECT * FROM TAI_SAN WHERE MaTaiSan = ?", (deal['MaTaiSan'],)).fetchone()
    config = db.execute("SELECT * FROM CAU_HINH_GIA WHERE TrangThai = 1 LIMIT 1").fetchone()
    gia_dv = config['GiaDichVu'] / 1000.0 if config else 250.0
    db.close()
    return render_template('sale_booking_edit.html', deal=deal, room=room, gia_dv=gia_dv, contract=contract)


# ==========================================
# PHÂN HỆ DÀNH CHO NHÂN VIÊN VẬN HÀNH (ROLE = 2)
# ==========================================



@app.route('/manager/ca-thu')
@role_required([2])
def manager_ca_thu():
    day = request.args.get('day', type=int)
    if day not in [5, 10, 15, 20, 25, 30]:
        flash("Ca thu không hợp lệ!", "error")
        return redirect(url_for('manager_dashboard'))
        
    db = get_db()
    # Lấy tài sản theo ca chốt
    rooms_list = db.execute(
        "SELECT * FROM TAI_SAN WHERE HanThanhToan = ? ORDER BY KhuVuc, DiaChi, SoPhong", (day,)
    ).fetchall()
    
    rooms = []
    for r in rooms_list:
        room_dict = dict(r)
        
        # Lấy thông tin chốt khách mới nhất cho phòng này
        deal = db.execute(
            """
            SELECT * FROM THONG_TIN_CHOT_KHACH 
            WHERE MaTaiSan = ? 
            ORDER BY MaChotKhach DESC LIMIT 1
            """, (r['MaTaiSan'],)
        ).fetchone()
        
        if deal:
            room_dict['TenKhachChot'] = deal['TenKhach']
            room_dict['SdtKhachChot'] = deal['SoDienThoaiKhach']
            room_dict['TienCocChot'] = deal['TienCoc']
            room_dict['DienDauVaoChot'] = deal['SoDienDauVao']
        else:
            room_dict['TenKhachChot'] = None
            room_dict['SdtKhachChot'] = None
            room_dict['TienCocChot'] = None
            room_dict['DienDauVaoChot'] = None
            
        rooms.append(room_dict)
        
    db.close()
    return render_template('manager_ca_thu.html', day=day, rooms=rooms)

@app.route('/manager/hop-dong/<int:room_id>', methods=['GET', 'POST'])
@role_required([2])
def manager_hop_dong(room_id):
    db = get_db()
    room = db.execute(
        "SELECT * FROM TAI_SAN WHERE MaTaiSan = ? AND TrangThai = 'DatCoc'", (room_id,)
    ).fetchone()
    
    if not room:
        db.close()
        flash("Phòng không ở trạng thái đặt cọc hoặc không tồn tại!", "error")
        return redirect(url_for('manager_dashboard'))
        
    deal = db.execute(
        """
        SELECT tk.*, u.HoTen as TenNguoiChot 
        FROM THONG_TIN_CHOT_KHACH tk 
        JOIN TAI_KHOAN u ON tk.MaTaiKhoanChot = u.MaTaiKhoan 
        WHERE tk.MaTaiSan = ? AND tk.TrangThai = 'Da Duyet' 
        ORDER BY tk.MaChotKhach DESC LIMIT 1
        """, (room_id,)
    ).fetchone()
    
    if not deal:
        db.close()
        flash("Không tìm thấy phiếu đặt cọc chưa duyệt cho phòng này!", "error")
        return redirect(url_for('manager_dashboard'))
        
    if request.method == 'POST':
        start_date_str = request.form.get('start_date')
        end_date_str = request.form.get('end_date')
        duration = int(request.form.get('duration'))
        deposit_received = float(request.form.get('deposit_received'))
        deal_id = int(request.form.get('deal_id'))
        tenant_name = request.form.get('tenant_name')
        tenant_phone = request.form.get('tenant_phone')
        tenant_cccd = request.form.get('tenant_cccd')
        contract_notes = request.form.get('contract_notes')
        
        try:
            # 1. Thêm khách thuê vào bảng KHACH_THUE
            cursor = db.cursor()
            cursor.execute(
                "INSERT INTO KHACH_THUE (HoTen, SoDienThoai, CCCD) VALUES (?, ?, ?)",
                (tenant_name, tenant_phone, tenant_cccd)
            )
            khach_thue_id = cursor.lastrowid
            
            # 2. Thêm hợp đồng mới
            cursor.execute(
                """
                INSERT INTO HOP_DONG (MaTaiSan, MaKhachThue, NgayBatDau, NgayKetThuc, ThoiHanThue, TienCoc, TrangThai, GhiChu, GiaThue)
                VALUES (?, ?, ?, ?, ?, ?, 'Đang kích hoạt', ?, ?)
                """,
                (room_id, khach_thue_id, start_date_str, end_date_str, duration, deposit_received, contract_notes, deal['GiaThue'] if deal['GiaThue'] is not None else room['GiaThue'])
            )
            hop_dong_id = cursor.lastrowid
            
            # 3. Thuật toán tính tiền lẻ ngày (Pro-rated Billing)
            start_dt = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            han_thanh_toan = room['HanThanhToan']
            
            # Tìm ngày ca thu tiếp theo
            if start_dt.day < han_thanh_toan:
                next_ca_thu = date(start_dt.year, start_dt.month, han_thanh_toan)
            else:
                next_month = start_dt.month + 1
                next_year = start_dt.year
                if next_month > 12:
                    next_month = 1
                    next_year += 1
                next_ca_thu = date(next_year, next_month, han_thanh_toan)
                
            actual_days = (next_ca_thu - start_dt).days
            if actual_days > 30:
                actual_days = 30
            
            # Tính tiền lẻ
            # Giá phòng gốc
            gia_phong_goc = deal['GiaThue'] if deal['GiaThue'] is not None else room['GiaThue'] # in thousands (k)
            tien_nha_le = (gia_phong_goc / 30.0) * actual_days
            
            # Đọc cấu hình dịch vụ hiện tại
            config = db.execute(
                "SELECT * FROM CAU_HINH_GIA WHERE TrangThai = 1 LIMIT 1"
            ).fetchone()
            
            if not config:
                # Fallback mặc định nếu không có cấu hình giá trong hệ thống
                gia_dien = 4000.00
                gia_dv = 250000.00
            else:
                gia_dien = config['GiaDien']
                gia_dv = config['GiaDichVu']
                
            # Tiền dịch vụ lẻ ngày (trong DB lưu là nghìn đồng, nên chia 1000)
            tien_dich_vu_le = ((gia_dv / 1000.0) / 30.0) * actual_days
            
            # Tổng tiền lẻ ngày hóa đơn đầu tiên = Tiền nhà lẻ + Tiền dịch vụ lẻ + Tiền cọc thực tế
            tong_tien_hoa_don_1 = tien_nha_le + tien_dich_vu_le + deposit_received
            
            # 4. Thêm bản ghi hóa đơn
            cursor.execute(
                """
                INSERT INTO HOA_DON 
                (MaHopDong, Thang, Nam, TienNha, SoDien, GiaDien, TienDien, GiaDichVu, TienDichVu, TongTien, NgayTao, TrangThaiThanhToan)
                VALUES (?, ?, ?, ?, 0, ?, 0.00, ?, ?, ?, ?, 'Chưa thanh toán')
                """,
                (hop_dong_id, start_dt.month, start_dt.year, tien_nha_le, gia_dien, gia_dv, tien_dich_vu_le, tong_tien_hoa_don_1, date.today().isoformat())
            )
            
            # 5. Cập nhật trạng thái phòng thành Đang thuê
            cursor.execute(
                "UPDATE TAI_SAN SET TrangThai = 'DangThue' WHERE MaTaiSan = ?", (room_id,)
            )
            
            # 6. Cập nhật phiếu chốt khách thành Đã chuyển thành hợp đồng
            cursor.execute(
                """
                UPDATE THONG_TIN_CHOT_KHACH 
                SET TrangThai = 'Da Chuyen Thanh Hop Dong', MaKhachThue = ?, MaHopDong = ? 
                WHERE MaChotKhach = ?
                """,
                (khach_thue_id, hop_dong_id, deal_id)
            )
            
            db.commit()
            flash(f"Kích hoạt hợp đồng thành công cho phòng {room['SoPhong']}. Hóa đơn đầu tiên đã được tạo: {tong_tien_hoa_don_1:,.2f}k.", "success")
        except Exception as e:
            db.rollback()
            flash(f"Lỗi khi kích hoạt hợp đồng: {str(e)}", "error")
        finally:
            db.close()
            
        return redirect(url_for('manager_ca_thu', day=room['HanThanhToan']))
        
    db.close()
    return render_template('manager_hop_dong.html', room=room, deal=deal)

@app.route('/manager/dien-nuoc')
@role_required([2])
def manager_dien_nuoc():
    db = get_db()
    # Hiển thị danh sách phòng DangThue kèm thông tin khách hàng
    rooms = db.execute(
        """
        SELECT ts.*, kt.HoTen as HoTenKhach, kt.SoDienThoai as SoDienThoaiKhach 
        FROM TAI_SAN ts 
        JOIN HOP_DONG hd ON ts.MaTaiSan = hd.MaTaiSan 
        JOIN KHACH_THUE kt ON hd.MaKhachThue = kt.MaKhachThue 
        WHERE ts.TrangThai = 'DangThue' AND hd.TrangThai = 'Đang kích hoạt'
        ORDER BY ts.KhuVuc, ts.DiaChi, ts.SoPhong
        """
    ).fetchall()
    db.close()
    return render_template('manager_dien_nuoc.html', rooms=rooms, room=None)

@app.route('/manager/dien-nuoc/<int:room_id>', methods=['GET', 'POST'])
@role_required([2])
def manager_dien_nuoc_form(room_id):
    db = get_db()
    room = db.execute(
        "SELECT * FROM TAI_SAN WHERE MaTaiSan = ? AND TrangThai = 'DangThue'", (room_id,)
    ).fetchone()
    
    if not room:
        db.close()
        flash("Phòng không ở trạng thái đang thuê hoặc không tồn tại!", "error")
        return redirect(url_for('manager_dien_nuoc'))
        
    contract = db.execute(
        """
        SELECT hd.*, kt.HoTen, kt.SoDienThoai 
        FROM HOP_DONG hd 
        JOIN KHACH_THUE kt ON hd.MaKhachThue = kt.MaKhachThue 
        WHERE hd.MaTaiSan = ? AND hd.TrangThai = 'Đang kích hoạt' 
        LIMIT 1
        """, (room_id,)
    ).fetchone()
    
    active_config = db.execute(
        "SELECT * FROM CAU_HINH_GIA WHERE TrangThai = 1 LIMIT 1"
    ).fetchone()
    
    if not active_config:
        # Giá trị cấu hình dự phòng mặc định nếu trống
        active_config = {'GiaDien': 4000.00, 'GiaDichVu': 250000.00}
        
    # Tính số điện cũ
    # 1. Tìm chỉ số mới nhất trong bảng CHI_SO_DIEN
    last_index = db.execute(
        "SELECT ChiSoMoi FROM CHI_SO_DIEN WHERE MaTaiSan = ? ORDER BY Nam DESC, Thang DESC, MaChiSo DESC LIMIT 1", (room_id,)
    ).fetchone()
    
    if last_index:
        old_index = last_index['ChiSoMoi']
    else:
        # 2. Nếu không có, lấy số điện đầu vào từ chốt khách
        deal = db.execute(
            "SELECT SoDienDauVao FROM THONG_TIN_CHOT_KHACH WHERE MaTaiSan = ? ORDER BY MaChotKhach DESC LIMIT 1", (room_id,)
        ).fetchone()
        old_index = deal['SoDienDauVao'] if deal else 0
        
    if request.method == 'POST':
        month = int(request.form.get('month'))
        year = int(request.form.get('year'))
        new_index = int(request.form.get('new_index'))
        
        if new_index < old_index:
            flash("Chỉ số mới không được nhỏ hơn chỉ số cũ!", "error")
            db.close()
            return redirect(url_for('manager_dien_nuoc_form', room_id=room_id))
            
        try:
            power_consumed = new_index - old_index
            # Tiền điện tính bằng nghìn đồng (k)
            tien_dien = (power_consumed * active_config['GiaDien']) / 1000.0
            
            # Tiền dịch vụ cố định (nhân với số người ở)
            so_nguoi = contract['SoNguoi'] if contract['SoNguoi'] else 1
            tien_dv = (active_config['GiaDichVu'] * so_nguoi) / 1000.0
            
            # Tiền nhà cố định của tháng
            tien_nha = contract['GiaThue'] if contract['GiaThue'] is not None else room['GiaThue']
            
            # Tổng hóa đơn tháng
            tong_tien = tien_nha + tien_dien + tien_dv
            
            # Thêm mới chỉ số điện
            db.execute(
                """
                INSERT INTO CHI_SO_DIEN (MaTaiSan, Thang, Nam, ChiSoCu, ChiSoMoi, NgayNhap)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (room_id, month, year, old_index, new_index, date.today().isoformat())
            )
            
            # Thêm hóa đơn mới
            cursor = db.cursor()
            cursor.execute(
                """
                INSERT INTO HOA_DON (MaHopDong, Thang, Nam, TienNha, SoDien, GiaDien, TienDien, GiaDichVu, TienDichVu, TongTien, NgayTao, TrangThaiThanhToan)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Chưa thanh toán')
                """,
                (contract['MaHopDong'], month, year, tien_nha, power_consumed, active_config['GiaDien'], tien_dien, active_config['GiaDichVu'], tien_dv, tong_tien, date.today().isoformat())
            )
            invoice_id = cursor.lastrowid
            
            db.commit()
            flash(f"Đã chốt số điện phòng {room['SoPhong']} tháng {month}/{year}. Tổng hóa đơn: {tong_tien:,.2f}k.", "success")
            return redirect(url_for('shared_invoice_view', invoice_id=invoice_id))
        except Exception as e:
            db.rollback()
            flash(f"Lỗi khi lưu chỉ số điện và xuất hóa đơn: {str(e)}", "error")
            return redirect(url_for('manager_dien_nuoc'))
        finally:
            db.close()
        
    db.close()
    
    current_month = date.today().month
    current_year = date.today().year
    
    return render_template(
        'manager_dien_nuoc.html', 
        room=room, 
        contract=contract, 
        active_config=active_config, 
        old_index=old_index,
        current_month=current_month,
        current_year=current_year
    )

@app.route('/manager/kho', methods=['GET', 'POST'])
@role_required([1, 2])
def manager_kho():
    db = get_db()
    
    if request.method == 'POST':
        vat_tu_id = int(request.form.get('vat_tu_id'))
        transaction_type = request.form.get('transaction_type')
        quantity = int(request.form.get('quantity'))
        room_id = request.form.get('room_id') # Có thể None nếu Nhập kho
        reason = request.form.get('reason')
        
        try:
            # 1. Thêm lịch sử kho
            db.execute(
                """
                INSERT INTO LICH_SU_KHO (MaVatTu, LoaiGiaoDich, SoLuong, NgayGiaoDich, NoiDung, MaTaiSan)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (vat_tu_id, transaction_type, quantity, date.today().isoformat(), reason, room_id if transaction_type == 'Xuất lắp đặt' else None)
            )
            
            # 2. Cập nhật tồn kho vật tư
            if transaction_type == 'Nhập kho':
                db.execute(
                    "UPDATE VAT_TU SET SoLuongTon = SoLuongTon + ? WHERE MaVatTu = ?", (quantity, vat_tu_id)
                )
            elif transaction_type == 'Xuất lắp đặt':
                # Kiểm tra tồn kho trước khi xuất
                vat_tu = db.execute("SELECT SoLuongTon, TenVatTu FROM VAT_TU WHERE MaVatTu = ?", (vat_tu_id,)).fetchone()
                if vat_tu['SoLuongTon'] < quantity:
                    flash(f"Số lượng tồn kho không đủ để xuất ({vat_tu['SoLuongTon']} < {quantity})!", "error")
                    db.close()
                    return redirect(url_for('manager_kho'))
                    
                db.execute(
                    "UPDATE VAT_TU SET SoLuongTon = SoLuongTon - ? WHERE MaVatTu = ?", (quantity, vat_tu_id)
                )
                
                # 3. Nối chuỗi vào danh sách thiết bị của phòng nhận
                if room_id:
                    room = db.execute("SELECT ThietBi, SoPhong FROM TAI_SAN WHERE MaTaiSan = ?", (room_id,)).fetchone()
                    thiet_bi_cu = room['ThietBi']
                    ten_thiet_bi = vat_tu['TenVatTu']
                    
                    if thiet_bi_cu:
                        thiet_bi_moi = f"{thiet_bi_cu}, Lắp thêm {quantity} {ten_thiet_bi}"
                    else:
                        thiet_bi_moi = f"Lắp thêm {quantity} {ten_thiet_bi}"
                        
                    db.execute(
                        "UPDATE TAI_SAN SET ThietBi = ? WHERE MaTaiSan = ?", (thiet_bi_moi, room_id)
                    )
                    
            db.commit()
            flash("Thực hiện giao dịch kho thành công!", "success")
        except Exception as e:
            db.rollback()
            flash(f"Lỗi khi thực hiện giao dịch kho: {str(e)}", "error")
            
        return redirect(url_for('manager_kho'))
        
    # GET: Truy vấn danh mục vật tư tồn kho và danh sách phòng
    items = db.execute("SELECT * FROM VAT_TU ORDER BY TenVatTu").fetchall()
    rooms = db.execute("SELECT * FROM TAI_SAN ORDER BY KhuVuc, SoPhong").fetchall()
    db.close()
    
    return render_template('manager_kho.html', items=items, rooms=rooms)

@app.route('/manager/expenses', methods=['GET', 'POST'])
@role_required([1, 2])
def manager_expenses():
    db = get_db()
    current_month = int(request.args.get('month', date.today().month))
    current_year = int(request.args.get('year', date.today().year))
    
    if request.method == 'POST':
        ten_chi_phi = request.form.get('ten_chi_phi')
        so_tien = float(request.form.get('so_tien').replace(',', ''))
        ngay_nhap = request.form.get('ngay_nhap', date.today().isoformat())
        ghi_chu = request.form.get('ghi_chu')
        thang = int(ngay_nhap.split('-')[1])
        nam = int(ngay_nhap.split('-')[0])
        ma_tai_khoan = session.get('user_id')
        
        try:
            db.execute(
                """
                INSERT INTO CHI_PHI (Thang, Nam, TenChiPhi, SoTien, NgayNhap, MaTaiKhoanNhap, GhiChu)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (thang, nam, ten_chi_phi, so_tien, ngay_nhap, ma_tai_khoan, ghi_chu)
            )
            db.commit()
            flash("Thêm khoản chi phí thành công!", "success")
        except Exception as e:
            db.rollback()
            flash(f"Lỗi thêm chi phí: {str(e)}", "error")
            
        return redirect(url_for('manager_expenses', month=current_month, year=current_year))
        
    expenses = db.execute(
        """
        SELECT c.*, t.HoTen as NguoiNhap 
        FROM CHI_PHI c 
        JOIN TAI_KHOAN t ON c.MaTaiKhoanNhap = t.MaTaiKhoan
        WHERE c.Thang = ? AND c.Nam = ?
        ORDER BY c.NgayNhap DESC, c.MaChiPhi DESC
        """, (current_month, current_year)
    ).fetchall()
    
    total_expenses = sum(e['SoTien'] for e in expenses)
    db.close()
    
    return render_template('manager_expenses.html', expenses=expenses, current_month=current_month, current_year=current_year, total_expenses=total_expenses)

@app.route('/manager/expenses/delete/<int:expense_id>', methods=['POST'])
@role_required([1, 2])
def manager_expense_delete(expense_id):
    db = get_db()
    db.execute("DELETE FROM CHI_PHI WHERE MaChiPhi = ?", (expense_id,))
    db.commit()
    db.close()
    flash("Đã xóa khoản chi phí!", "success")
    return redirect(request.referrer or url_for('manager_expenses'))

# ==========================================
# PHÂN HỆ DÀNH CHO ADMIN (ROLE = 1)
# ==========================================

@app.route('/admin/dashboard')
@role_required([1])
def admin_dashboard():
    db = get_db()
    
    monthly_rows = db.execute("""
        SELECT Nam, Thang, 
               SUM(CASE WHEN TrangThaiThanhToan = 'Đã thanh toán' THEN TongTien ELSE 0 END) as Revenue,
               SUM(CASE WHEN TrangThaiThanhToan = 'Chưa thanh toán' THEN TongTien ELSE 0 END) as Debt
        FROM HOA_DON
        GROUP BY Nam, Thang
    """).fetchall()
    
    expense_rows = db.execute("""
        SELECT Nam, Thang, SUM(SoTien) as Expense
        FROM CHI_PHI
        GROUP BY Nam, Thang
    """).fetchall()
    
    monthly_data = {}
    for r in monthly_rows:
        key = f"{r['Nam']}-{r['Thang']}"
        monthly_data[key] = {'Nam': r['Nam'], 'Thang': r['Thang'], 'Revenue': r['Revenue'], 'Debt': r['Debt'], 'Expense': 0}
        
    for r in expense_rows:
        key = f"{r['Nam']}-{r['Thang']}"
        if key not in monthly_data:
            monthly_data[key] = {'Nam': r['Nam'], 'Thang': r['Thang'], 'Revenue': 0, 'Debt': 0, 'Expense': r['Expense']}
        else:
            monthly_data[key]['Expense'] = r['Expense']
            
    # Sort and Limit to last 12
    sorted_months = sorted(monthly_data.values(), key=lambda x: (x['Nam'], x['Thang']))[-12:]
    
    chart_labels = []
    chart_revenue = []
    chart_debt = []
    chart_expenses = []
    
    total_revenue = 0.0
    total_debt = 0.0
    total_expenses = 0.0
    
    for row in sorted_months:
        total_revenue += row['Revenue']
        total_debt += row['Debt']
        total_expenses += row['Expense']
        
    import datetime
    import random
    
    needed_mock_months = 6 - len(sorted_months)
    if needed_mock_months > 0:
        now = datetime.datetime.now()
        for i in range(needed_mock_months, 0, -1):
            m = now.month - i
            y = now.year
            while m <= 0:
                m += 12
                y -= 1
            chart_labels.append(f"T{m}/{y}")
            chart_revenue.append(random.randint(60, 150) * 1000000)
            chart_debt.append(random.randint(2, 15) * 1000000)
            chart_expenses.append(random.randint(10, 40) * 1000000)
            
    for row in sorted_months:
        chart_labels.append(f"T{row['Thang']}/{row['Nam']}")
        chart_revenue.append(row['Revenue'])
        chart_debt.append(row['Debt'])
        chart_expenses.append(row['Expense'])
        
    total_profit = total_revenue - total_expenses
        
    # Khối 3: Thống kê số lượng phòng theo trạng thái
    rooms = db.execute("SELECT TrangThai, COUNT(*) as count FROM TAI_SAN GROUP BY TrangThai").fetchall()
    
    room_stats = {'Trong': 0, 'DatCoc': 0, 'DangThue': 0}
    for r in rooms:
        if r['TrangThai'] in room_stats:
            room_stats[r['TrangThai']] = r['count']
            
    db.close()
    return render_template(
        'admin_dashboard.html', 
        total_revenue=total_revenue, 
        total_debt=total_debt,
        total_expenses=total_expenses,
        total_profit=total_profit,
        room_stats=room_stats,
        chart_labels=chart_labels,
        chart_revenue=chart_revenue,
        chart_debt=chart_debt,
        chart_expenses=chart_expenses
    )

@app.route('/admin/employees', methods=['GET', 'POST'])
@role_required([1])
def admin_employees():
    db = get_db()
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        fullname = request.form.get('fullname')
        phone = request.form.get('phone')
        role_id = int(request.form.get('role_id'))
        luong_cung = float(request.form.get('luong_cung', 0))
        
        # Kiểm tra trùng tên đăng nhập
        check_user = db.execute("SELECT * FROM TAI_KHOAN WHERE TenDangNhap = ?", (username,)).fetchone()
        if check_user:
            flash(f"Tên đăng nhập '{username}' đã được sử dụng!", "error")
        else:
            try:
                db.execute(
                    """
                    INSERT INTO TAI_KHOAN (TenDangNhap, MatKhau, HoTen, SoDienThoai, MaVaiTro, TrangThai, LuongCung)
                    VALUES (?, ?, ?, ?, ?, 1, ?)
                    """,
                    (username, password, fullname, phone, role_id, luong_cung)
                )
                db.commit()
                flash(f"Tạo tài khoản thành công cho {fullname}!", "success")
            except Exception as e:
                db.rollback()
                flash(f"Lỗi khi thêm tài khoản nhân sự: {str(e)}", "error")
                
        return redirect(url_for('admin_employees'))
        
    # GET: Lấy danh sách tài khoản
    users = db.execute("SELECT * FROM TAI_KHOAN ORDER BY MaVaiTro, TenDangNhap").fetchall()
    db.close()
    return render_template('admin_employees.html', users=users)

@app.route('/admin/employees/toggle/<int:user_id>', methods=['POST'])
@role_required([1])
def admin_employees_toggle(user_id):
    db = get_db()
    user = db.execute("SELECT TrangThai, HoTen FROM TAI_KHOAN WHERE MaTaiKhoan = ?", (user_id,)).fetchone()
    
    if user:
        new_status = 1 - user['TrangThai']
        action_name = "Mở khóa" if new_status == 1 else "Khóa"
        try:
            db.execute(
                "UPDATE TAI_KHOAN SET TrangThai = ? WHERE MaTaiKhoan = ?", (new_status, user_id)
            )
            db.commit()
            flash(f"Đã {action_name.lower()} tài khoản của {user['HoTen']} thành công!", "success")
        except Exception as e:
            db.rollback()
            flash(f"Lỗi khi thay đổi trạng thái tài khoản: {str(e)}", "error")
            
    db.close()
    return redirect(url_for('admin_employees'))

@app.route('/admin/employees/edit/<int:user_id>', methods=['GET', 'POST'])
@role_required([1])
def admin_employees_edit(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM TAI_KHOAN WHERE MaTaiKhoan = ?", (user_id,)).fetchone()
    
    if not user:
        db.close()
        flash("Tài khoản không tồn tại!", "error")
        return redirect(url_for('admin_employees'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        fullname = request.form.get('fullname')
        phone = request.form.get('phone')
        role_id = int(request.form.get('role_id'))
        luong_cung = float(request.form.get('luong_cung', 0))
        
        # Kiểm tra trùng tên đăng nhập
        check_user = db.execute(
            "SELECT * FROM TAI_KHOAN WHERE TenDangNhap = ? AND MaTaiKhoan != ?", (username, user_id)
        ).fetchone()
        
        if check_user:
            flash(f"Tên đăng nhập '{username}' đã được sử dụng bởi tài khoản khác!", "error")
        else:
            try:
                db.execute(
                    """
                    UPDATE TAI_KHOAN 
                    SET TenDangNhap = ?, MatKhau = ?, HoTen = ?, SoDienThoai = ?, MaVaiTro = ?, LuongCung = ?
                    WHERE MaTaiKhoan = ?
                    """,
                    (username, password, fullname, phone, role_id, luong_cung, user_id)
                )
                db.commit()
                # Cập nhật session nếu tự sửa chính mình
                if user_id == session.get('user_id'):
                    session['username'] = username
                    session['fullname'] = fullname
                    session['role_id'] = role_id
                flash("Cập nhật thông tin tài khoản thành công!", "success")
            except Exception as e:
                db.rollback()
                flash(f"Lỗi khi cập nhật tài khoản: {str(e)}", "error")
            db.close()
            return redirect(url_for('admin_employees'))
            
    db.close()
    return render_template('admin_employees_edit.html', user=user)

@app.route('/admin/config', methods=['GET', 'POST'])
@role_required([1])
def admin_config():
    db = get_db()
    
    if request.method == 'POST':
        elec_price = float(request.form.get('elec_price'))
        service_price = float(request.form.get('service_price'))
        chi_tieu_kpi = int(request.form.get('chi_tieu_kpi', 6))
        
        try:
            # 1. Chuyển toàn bộ cấu hình cũ về TrangThai = 0
            db.execute("UPDATE CAU_HINH_GIA SET TrangThai = 0")
            
            # 2. Thêm cấu hình mới với TrangThai = 1
            db.execute(
                """
                INSERT INTO CAU_HINH_GIA (GiaDien, GiaDichVu, NgayApDung, TrangThai, ChiTieuKPI)
                VALUES (?, ?, ?, 1, ?)
                """,
                (elec_price, service_price, date.today().isoformat(), chi_tieu_kpi)
            )
            db.commit()
            flash("Cập nhật biểu giá mới của hệ thống thành công!", "success")
        except Exception as e:
            db.rollback()
            flash(f"Lỗi khi áp dụng cấu hình giá mới: {str(e)}", "error")
            
        return redirect(url_for('admin_config'))
        
    # GET: Xem lịch sử cấu hình
    configs = db.execute("SELECT * FROM CAU_HINH_GIA ORDER BY MaCauHinh DESC").fetchall()
    db.close()
    return render_template('admin_config.html', configs=configs)

@app.route('/admin/update_kpi', methods=['POST'])
@role_required([1, 2])
def admin_update_kpi():
    db = get_db()
    chi_tieu_kpi = int(request.form.get('chi_tieu_kpi', 6))
    return_tab = request.form.get('return_tab', 'salary')
    
    try:
        # Lấy cấu hình hiện hành để duplicate (vì mình chỉ muốn đổi KPI)
        current_config = db.execute("SELECT * FROM CAU_HINH_GIA WHERE TrangThai = 1 LIMIT 1").fetchone()
        
        if current_config:
            db.execute("UPDATE CAU_HINH_GIA SET TrangThai = 0")
            db.execute(
                """
                INSERT INTO CAU_HINH_GIA (GiaDien, GiaDichVu, NgayApDung, TrangThai, ChiTieuKPI)
                VALUES (?, ?, ?, 1, ?)
                """,
                (current_config['GiaDien'], current_config['GiaDichVu'], date.today().isoformat(), chi_tieu_kpi)
            )
            db.commit()
            flash("Cập nhật Chỉ tiêu KPI thành công!", "success")
        else:
            flash("Không tìm thấy cấu hình giá hiện tại để cập nhật KPI.", "error")
    except Exception as e:
        db.rollback()
        flash(f"Lỗi khi cập nhật KPI: {str(e)}", "error")
        
    db.close()
    return redirect(url_for('shared_tracking', tab=return_tab))

@app.route('/shared/tracking')
@login_required
def shared_tracking():
    db = get_db()
    
    current_month = int(request.args.get('month', date.today().month))
    current_year = int(request.args.get('year', date.today().year))
    
    # Query 1: Danh sách phòng trống (trạng thái 'Trong')
    rooms = db.execute(
        """
        SELECT ts.*, tk.HoTen as TenNhanVienChotCu 
        FROM TAI_SAN ts 
        LEFT JOIN TAI_KHOAN tk ON ts.NhanVienChotCu = tk.MaTaiKhoan 
        WHERE ts.TrangThai = 'Trong'
        ORDER BY ts.KhuVuc, ts.DiaChi, ts.SoPhong
        """
    ).fetchall()
    
    # Lọc thời gian cho phòng chốt (Từ đầu tháng đến cuối tháng)
    start_date = f"{current_year}-{current_month:02d}-01"
    if current_month == 12:
        end_date = f"{current_year+1}-01-01"
    else:
        end_date = f"{current_year}-{current_month+1:02d}-01"
        
    # Query 2: Tổng hợp phòng chốt (THONG_TIN_CHOT_KHACH)
    bookings = db.execute(
        """
        SELECT tk.*, ts.SoPhong, ts.DiaChi, ts.GiaThue, ts.HoaHong as HoaHongTaiSan,
               u.HoTen as TenNhanVienChot, kt.HoTen as TenKhachThue, hd.MaHopDong as HopDongKichHoat, hd.TrangThai as TrangThaiHopDong
        FROM THONG_TIN_CHOT_KHACH tk 
        JOIN TAI_SAN ts ON tk.MaTaiSan = ts.MaTaiSan 
        JOIN TAI_KHOAN u ON tk.MaTaiKhoanChot = u.MaTaiKhoan 
        LEFT JOIN KHACH_THUE kt ON tk.MaKhachThue = kt.MaKhachThue 
        LEFT JOIN HOP_DONG hd ON tk.MaHopDong = hd.MaHopDong 
        WHERE tk.NgayChot >= ? AND tk.NgayChot < ?
        ORDER BY tk.NgayChot DESC, tk.MaChotKhach DESC
        """, (start_date, end_date)
    ).fetchall()
    
    # Tính bảng lương (Summary)
    config = db.execute("SELECT ChiTieuKPI FROM CAU_HINH_GIA WHERE TrangThai = 1 LIMIT 1").fetchone()
    chi_tieu_kpi = config['ChiTieuKPI'] if config else 6
    
    salary_summary_dict = {}
    sales_users = db.execute("SELECT MaTaiKhoan, HoTen, LuongCung FROM TAI_KHOAN WHERE MaVaiTro = 3").fetchall()
    for s in sales_users:
        salary_summary_dict[s['MaTaiKhoan']] = {
            'HoTen': s['HoTen'],
            'SoPhongChot': 0,
            'HoaHong': 0.0,
            'LuongCung': float(s['LuongCung'] if s['LuongCung'] else 0.0),
            'TongLuong': 0.0,
            'DatKPI': False
        }
        
    for b in bookings:
        sale_id = b['MaTaiKhoanChot']
        if sale_id in salary_summary_dict:
            salary_summary_dict[sale_id]['SoPhongChot'] += 1
            salary_summary_dict[sale_id]['HoaHong'] += float(b['HoaHongTaiSan'] or 0.0)
        
    for sale_id, stat in salary_summary_dict.items():
        if stat['SoPhongChot'] >= chi_tieu_kpi:
            stat['DatKPI'] = True
            stat['TongLuong'] = stat['HoaHong'] + stat['LuongCung']
        else:
            stat['DatKPI'] = False
            stat['TongLuong'] = stat['HoaHong']
            
    # Sắp xếp theo tổng lương
    salary_summary = sorted(salary_summary_dict.values(), key=lambda x: x['TongLuong'], reverse=True)
    
    # Lấy thông tin sale hiện tại nếu user là sale
    current_sale_stat = None
    if session.get('role_id') == 3:
        if session.get('user_id') in salary_summary_dict:
            current_sale_stat = salary_summary_dict[session.get('user_id')]
        else:
            sale_info = db.execute("SELECT HoTen, LuongCung FROM TAI_KHOAN WHERE MaTaiKhoan = ?", (session.get('user_id'),)).fetchone()
            current_sale_stat = {
                'HoTen': sale_info['HoTen'] if sale_info else 'Sale',
                'SoPhongChot': 0,
                'HoaHong': 0.0,
                'LuongCung': float(sale_info['LuongCung'] if sale_info and sale_info['LuongCung'] else 0.0),
                'TongLuong': 0.0,
                'DatKPI': False
            }
    
    # Lấy danh sách khu vực có trong hệ thống
    areas_query = db.execute("SELECT DISTINCT KhuVuc FROM TAI_SAN WHERE KhuVuc IS NOT NULL AND KhuVuc != ''").fetchall()
    areas = [a['KhuVuc'] for a in areas_query]
    
    db.close()
    current_date = date.today().strftime('%d/%m/%Y')
    return render_template('shared_tracking.html', rooms=rooms, bookings=bookings, current_date=current_date, areas=areas,
                           current_month=current_month, current_year=current_year, salary_summary=salary_summary, chi_tieu_kpi=chi_tieu_kpi,
                           current_sale_stat=current_sale_stat)

@app.route('/shared/room/edit/<int:room_id>', methods=['GET', 'POST'])
@role_required([1, 2])
def shared_room_edit(room_id):
    db = get_db()
    room = db.execute("SELECT * FROM TAI_SAN WHERE MaTaiSan = ?", (room_id,)).fetchone()
    
    if not room:
        db.close()
        flash("Phòng không tồn tại!", "error")
        return redirect(url_for('shared_tracking'))
        
    if request.method == 'POST':
        dia_chi = request.form.get('dia_chi')
        so_phong = request.form.get('so_phong')
        tang = int(request.form.get('tang'))
        gia_thue = float(request.form.get('gia_thue'))
        
        if gia_thue % 100 != 0:
            db.close()
            flash("Giá thuê phải chẵn theo 100k (không được chốt lẻ 50k)!", "error")
            return redirect(url_for('shared_room_edit', room_id=room_id))
            
        ghi_chu = request.form.get('ghi_chu')
        thiet_bi = request.form.get('thiet_bi')
        nv_chot_cu = request.form.get('nv_chot_cu')
        nv_chot_cu = int(nv_chot_cu) if nv_chot_cu else None
        ngay_trong = request.form.get('ngay_trong')
        hoa_hong = float(request.form.get('hoa_hong'))
        
        try:
            db.execute(
                """
                UPDATE TAI_SAN 
                SET DiaChi = ?, SoPhong = ?, Tang = ?, GiaThue = ?, GhiChu = ?, ThietBi = ?, 
                    NhanVienChotCu = ?, NgayTrong = ?, HoaHong = ?
                WHERE MaTaiSan = ?
                """,
                (dia_chi, so_phong, tang, gia_thue, ghi_chu, thiet_bi, nv_chot_cu, ngay_trong, hoa_hong, room_id)
            )
            db.commit()
            flash("Cập nhật thông tin phòng trống thành công!", "success")
        except Exception as e:
            db.rollback()
            flash(f"Lỗi khi cập nhật phòng: {str(e)}", "error")
        db.close()
        return redirect(url_for('shared_tracking'))
        
    users = db.execute("SELECT MaTaiKhoan, HoTen FROM TAI_KHOAN ORDER BY HoTen").fetchall()
    db.close()
    return render_template('shared_room_edit.html', room=room, users=users)

@app.route('/shared/room/delete/<int:room_id>', methods=['POST'])
@role_required([1, 2])
def shared_room_delete(room_id):
    db = get_db()
    
    try:
        # Xóa phòng
        db.execute("DELETE FROM TAI_SAN WHERE MaTaiSan = ?", (room_id,))
        db.commit()
        flash("Xóa phòng thành công!", "success")
    except Exception as e:
        db.rollback()
        flash(f"Lỗi khi xóa phòng (có thể do đang có dữ liệu liên quan): {str(e)}", "error")
        
    db.close()
    return redirect(url_for('shared_tracking'))

@app.route('/shared/booking/edit/<int:deal_id>', methods=['GET', 'POST'])
@role_required([1, 2])
def shared_booking_edit(deal_id):
    db = get_db()
    deal = db.execute("SELECT * FROM THONG_TIN_CHOT_KHACH WHERE MaChotKhach = ?", (deal_id,)).fetchone()
    
    if not deal:
        db.close()
        flash("Phiếu chốt khách không tồn tại!", "error")
        return redirect(url_for('shared_tracking'))
        
    if request.method == 'POST':
        ten_khach = request.form.get('ten_khach')
        sdt_khach = request.form.get('sdt_khach')
        cccd = request.form.get('cccd')
        tien_coc = float(request.form.get('tien_coc'))
        gia_thue = float(request.form.get('gia_thue'))
        ngay_chot = request.form.get('ngay_chot')
        ngay_vao = request.form.get('ngay_vao')
        dien_dau_vao = int(request.form.get('dien_dau_vao'))
        ghi_chu = request.form.get('ghi_chu')
        trang_thai = request.form.get('trang_thai')
        
        try:
            db.execute(
                """
                UPDATE THONG_TIN_CHOT_KHACH 
                SET TenKhach = ?, SoDienThoaiKhach = ?, CCCD = ?, TienCoc = ?, NgayChot = ?,
                    NgayDuKienVaoO = ?, SoDienDauVao = ?, GhiChu = ?, TrangThai = ?, GiaThue = ?
                WHERE MaChotKhach = ?
                """,
                (ten_khach, sdt_khach, cccd, tien_coc, ngay_chot, ngay_vao, dien_dau_vao, ghi_chu, trang_thai, gia_thue, deal_id)
            )
            if deal['MaHopDong']:
                db.execute("UPDATE HOP_DONG SET GiaThue = ? WHERE MaHopDong = ?", (gia_thue, deal['MaHopDong']))
            db.commit()
            flash("Cập nhật phiếu chốt khách thành công!", "success")
        except Exception as e:
            db.rollback()
            flash(f"Lỗi khi cập nhật phiếu chốt: {str(e)}", "error")
        db.close()
        return redirect(url_for('shared_tracking'))
        
    db.close()
    return render_template('shared_booking_edit.html', deal=deal)


@app.route('/shared/booking/cancel/<int:deal_id>', methods=['POST'])
@role_required([1, 2])
def shared_booking_cancel(deal_id):
    db = get_db()
    deal = db.execute("SELECT * FROM THONG_TIN_CHOT_KHACH WHERE MaChotKhach = ?", (deal_id,)).fetchone()
    
    if not deal:
        db.close()
        flash("Phiếu chốt khách không tồn tại!", "error")
        return redirect(url_for('shared_tracking'))
        
    contract = db.execute("SELECT * FROM HOP_DONG WHERE MaHopDong = ?", (deal['MaHopDong'],)).fetchone()
    if not contract or contract['TrangThai'] != 'Giữ phòng':
        db.close()
        flash("Chỉ có thể bỏ cọc cho phiếu chốt có hợp đồng đang ở trạng thái 'Giữ phòng'!", "error")
        return redirect(url_for('shared_tracking'))
        
    try:
        # 1. Cập nhật trạng thái hợp đồng thành 'Bỏ cọc'
        db.execute(
            "UPDATE HOP_DONG SET TrangThai = 'Bỏ cọc' WHERE MaHopDong = ?", (deal['MaHopDong'],)
        )
        # 2. Cập nhật trạng thái phiếu chốt thành 'Bỏ cọc'
        db.execute(
            "UPDATE THONG_TIN_CHOT_KHACH SET TrangThai = 'Bỏ cọc' WHERE MaChotKhach = ?", (deal_id,)
        )
        # 3. Cập nhật phòng về trạng thái 'Trong'
        db.execute(
            "UPDATE TAI_SAN SET TrangThai = 'Trong' WHERE MaTaiSan = ?", (deal['MaTaiSan'],)
        )
        db.commit()
        flash("Đã thực hiện bỏ cọc thành công. Phòng đã quay lại trạng thái Trống!", "success")
    except Exception as e:
        db.rollback()
        flash(f"Lỗi khi huỷ bỏ cọc: {str(e)}", "error")
    finally:
        db.close()
        
    return redirect(url_for('shared_tracking'))


@app.route('/manager/thanhtoan')
@role_required([1, 2])
def manager_thanhtoan():
    db = get_db()
    
    # Lấy toàn bộ phòng kèm thông tin khách đang thuê hoặc thông tin chốt cọc
    rooms_list = db.execute(
        """
        SELECT ts.*, 
               hd.MaHopDong, hd.NgayBatDau, hd.NgayKetThuc, hd.TienCoc as TienCocHD, hd.TrangThai as TrangThaiHD,
               kt1.HoTen as TenKhachThue, kt1.SoDienThoai as SdtKhachThue,
               tk.MaChotKhach, tk.TenKhach as TenKhachChot, tk.SoDienThoaiKhach as SdtKhachChot, tk.TienCoc as TienCocChot, tk.SoDienDauVao
        FROM TAI_SAN ts
        LEFT JOIN HOP_DONG hd ON ts.MaTaiSan = hd.MaTaiSan AND hd.TrangThai IN ('Đang kích hoạt', 'Giữ phòng')
        LEFT JOIN KHACH_THUE kt1 ON hd.MaKhachThue = kt1.MaKhachThue
        LEFT JOIN THONG_TIN_CHOT_KHACH tk ON hd.MaHopDong = tk.MaHopDong
        ORDER BY ts.HanThanhToan, ts.DiaChi, ts.SoPhong
        """
    ).fetchall()
    
    # Nhóm theo ca thu (HanThanhToan) -> Địa chỉ nhà (DiaChi) -> các phòng
    shifts = {}
    for r in rooms_list:
        day = r['HanThanhToan'] if r['HanThanhToan'] is not None else 15
        if day not in shifts:
            shifts[day] = {}
        
        address = r['DiaChi']
        if address not in shifts[day]:
            shifts[day][address] = []
            
        shifts[day][address].append(dict(r))
        
    # Sắp xếp các ca theo thứ tự ngày tăng dần
    sorted_shifts = {k: shifts[k] for k in sorted(shifts.keys())}
    
    db.close()
    return render_template('manager_thanhtoan.html', shifts=sorted_shifts)

@app.route('/shared/room/add', methods=['GET', 'POST'])
@role_required([1, 2])
def shared_room_add():
    db = get_db()
    if request.method == 'POST':
        property_type = int(request.form.get('property_type'))
        room_code = request.form.get('room_code')
        if not room_code:
            # Fallback nếu form ko gửi room_code
            last_id = db.execute("SELECT MAX(MaTaiSan) as max_id FROM TAI_SAN").fetchone()
            next_id = (last_id['max_id'] or 0) + 1
            room_code = f"TS{next_id:04d}"
            
        area = request.form.get('area')
        address = request.form.get('address')
        room_number = request.form.get('room_number')
        floor = int(request.form.get('floor'))
        rent_price = float(request.form.get('rent_price'))
        
        if rent_price % 100 != 0:
            flash("Giá thuê phải chẵn theo 100k (không được chốt lẻ 50k)!", "error")
            return redirect(url_for('shared_room_add'))
            
        commission = float(request.form.get('commission'))
        payment_day = int(request.form.get('payment_day'))
        devices = request.form.get('devices')
        notes = request.form.get('notes')
        
        # Kiểm tra trùng mã số tài sản
        check_code = db.execute("SELECT * FROM TAI_SAN WHERE MaSoTaiSan = ?", (room_code,)).fetchone()
        if check_code:
            flash(f"Mã số tài sản '{room_code}' đã tồn tại!", "error")
            db.close()
            return redirect(url_for('shared_room_add'))
            
        try:
            db.execute(
                """
                INSERT INTO TAI_SAN 
                (MaLoaiTaiSan, MaSoTaiSan, KhuVuc, DiaChi, SoPhong, Tang, GiaThue, HoaHong, TrangThai, HanThanhToan, ThietBi, GhiChu, DaTungChoThue, NgayTrong)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Trong', ?, ?, ?, 0, ?)
                """,
                (property_type, room_code, area.upper(), address, room_number, floor, rent_price, commission, payment_day, devices, notes, date.today().isoformat())
            )
            db.commit()
            flash(f"Thêm tài sản mới '{room_code}' thành công!", "success")
        except Exception as e:
            db.rollback()
            flash(f"Lỗi khi lưu tài sản mới: {str(e)}", "error")
        db.close()
        db.close()
        return redirect(url_for('shared_tracking'))
        
    last_id = db.execute("SELECT MAX(MaTaiSan) as max_id FROM TAI_SAN").fetchone()
    next_id = (last_id['max_id'] or 0) + 1
    suggested_code = f"TS{next_id:04d}"
    
    areas_query = db.execute("SELECT DISTINCT KhuVuc FROM TAI_SAN WHERE KhuVuc IS NOT NULL AND KhuVuc != ''").fetchall()
    areas = [a['KhuVuc'] for a in areas_query]
    
    db.close()
    return render_template('shared_room_add.html', suggested_code=suggested_code, areas=areas)

@app.route('/shared/contracts')
@role_required([1, 2])
def shared_contracts():
    db = get_db()
    contracts = db.execute(
        """
        SELECT hd.*, ts.SoPhong, ts.DiaChi, ts.KhuVuc, ts.MaTaiSan, kt.HoTen as TenKhach, kt.SoDienThoai
        FROM HOP_DONG hd
        JOIN TAI_SAN ts ON hd.MaTaiSan = ts.MaTaiSan
        JOIN KHACH_THUE kt ON hd.MaKhachThue = kt.MaKhachThue
        ORDER BY hd.MaHopDong DESC
        """
    ).fetchall()
    db.close()
    return render_template('shared_contracts.html', contracts=contracts)

@app.route('/shared/contract/edit/<int:contract_id>', methods=['GET', 'POST'])
@role_required([1, 2])
def shared_contract_edit(contract_id):
    db = get_db()
    
    contract = db.execute("SELECT * FROM HOP_DONG WHERE MaHopDong = ?", (contract_id,)).fetchone()
    if not contract:
        db.close()
        flash("Không tìm thấy hợp đồng!", "error")
        return redirect(url_for('shared_contracts'))
        
    tenant = db.execute("SELECT * FROM KHACH_THUE WHERE MaKhachThue = ?", (contract['MaKhachThue'],)).fetchone()
    
    if request.method == 'POST':
        # Retrieve form data
        tenant_name = request.form.get('tenant_name')
        tenant_phone = request.form.get('tenant_phone')
        tenant_cccd = request.form.get('tenant_cccd')
        
        start_date = request.form.get('start_date')
        end_date = request.form.get('end_date')
        duration = request.form.get('duration')
        rent_price = request.form.get('rent_price')
        deposit = request.form.get('deposit')
        so_nguoi = int(request.form.get('so_nguoi', 1))
        notes = request.form.get('notes')
        
        # Update KHACH_THUE
        db.execute(
            """
            UPDATE KHACH_THUE 
            SET HoTen = ?, SoDienThoai = ?, CCCD = ? 
            WHERE MaKhachThue = ?
            """,
            (tenant_name, tenant_phone, tenant_cccd, contract['MaKhachThue'])
        )
        
        # Update HOP_DONG
        db.execute(
            """
            UPDATE HOP_DONG 
            SET NgayBatDau = ?, NgayKetThuc = ?, ThoiHanThue = ?, GiaThue = ?, TienCoc = ?, GhiChu = ?, SoNguoi = ? 
            WHERE MaHopDong = ?
            """,
            (start_date, end_date, duration, rent_price, deposit, notes, so_nguoi, contract_id)
        )
        
        db.commit()
        db.close()
        flash("Cập nhật thông tin hợp đồng thành công!", "success")
        return redirect(url_for('shared_contracts'))
        
    db.close()
    return render_template('shared_contract_edit.html', contract=contract, tenant=tenant)

@app.route('/shared/contract/terminate/<int:contract_id>', methods=['POST'])
@role_required([1, 2])
def shared_contract_terminate(contract_id):
    db = get_db()
    contract = db.execute("SELECT * FROM HOP_DONG WHERE MaHopDong = ?", (contract_id,)).fetchone()
    
    if not contract or contract['TrangThai'] != 'Đang kích hoạt':
        db.close()
        flash("Hợp đồng không tồn tại hoặc đã được thanh lý!", "error")
        return redirect(url_for('shared_contracts'))
        
    try:
        # Cập nhật trạng thái hợp đồng thành Đã kết thúc
        db.execute(
            "UPDATE HOP_DONG SET TrangThai = 'Đã kết thúc' WHERE MaHopDong = ?", (contract_id,)
        )
        
        # Cập nhật trạng thái phòng thành Trống
        db.execute(
            "UPDATE TAI_SAN SET TrangThai = 'Trong' WHERE MaTaiSan = ?", (contract['MaTaiSan'],)
        )
        
        db.commit()
        flash("Thanh lý hợp đồng thành công. Phòng đã được chuyển về trạng thái Trống!", "success")
    except Exception as e:
        db.rollback()
        flash(f"Lỗi khi thanh lý hợp đồng: {str(e)}", "error")
        
    db.close()
    return redirect(url_for('shared_contracts'))


@app.route('/shared/contract/forfeit/<int:contract_id>', methods=['POST'])
@role_required([1, 2])
def shared_contract_forfeit(contract_id):
    db = get_db()
    contract = db.execute("SELECT * FROM HOP_DONG WHERE MaHopDong = ?", (contract_id,)).fetchone()
    
    if not contract or contract['TrangThai'] not in ['Đang kích hoạt', 'Giữ phòng']:
        db.close()
        flash("Hợp đồng không tồn tại hoặc đã được xử lý!", "error")
        return redirect(url_for('shared_contracts'))
        
    try:
        # Cập nhật trạng thái hợp đồng thành Bỏ cọc
        db.execute(
            "UPDATE HOP_DONG SET TrangThai = 'Bỏ cọc' WHERE MaHopDong = ?", (contract_id,)
        )
        
        # Cập nhật trạng thái phiếu chốt thành Bỏ cọc
        db.execute(
            "UPDATE THONG_TIN_CHOT_KHACH SET TrangThai = 'Bỏ cọc' WHERE MaHopDong = ?", (contract_id,)
        )
        
        # Cập nhật trạng thái phòng thành Trống
        db.execute(
            "UPDATE TAI_SAN SET TrangThai = 'Trong' WHERE MaTaiSan = ?", (contract['MaTaiSan'],)
        )
        
        db.commit()
        flash("Đã ghi nhận bỏ cọc/phá hợp đồng. Phòng đã được chuyển về trạng thái Trống!", "success")
    except Exception as e:
        db.rollback()
        flash(f"Lỗi khi xử lý bỏ cọc: {str(e)}", "error")
        
    db.close()
    return redirect(url_for('shared_contracts'))


@app.route('/shared/invoices/<int:room_id>')
@role_required([1, 2])
def shared_invoices(room_id):
    db = get_db()
    room = db.execute("SELECT * FROM TAI_SAN WHERE MaTaiSan = ?", (room_id,)).fetchone()
    if not room:
        db.close()
        flash("Tài sản không tồn tại!", "error")
        return redirect(url_for('shared_tracking'))
        
    # Lấy hợp đồng đang hoạt động hoặc đang giữ phòng
    contract = db.execute(
        """
        SELECT hd.*, kt.HoTen as TenKhach, kt.SoDienThoai
        FROM HOP_DONG hd
        JOIN KHACH_THUE kt ON hd.MaKhachThue = kt.MaKhachThue
        WHERE hd.MaTaiSan = ? AND hd.TrangThai IN ('Đang kích hoạt', 'Giữ phòng')
        LIMIT 1
        """, (room_id,)
    ).fetchone()
    
    invoices = []
    if contract:
        invoices = db.execute(
            "SELECT * FROM HOA_DON WHERE MaHopDong = ? ORDER BY Nam DESC, Thang DESC, MaHoaDon DESC",
            (contract['MaHopDong'],)
        ).fetchall()
        
    db.close()
    return render_template('shared_invoices.html', room=room, contract=contract, invoices=invoices)


@app.route('/admin/invoice/pay/<int:invoice_id>', methods=['POST'])
@role_required([1])
def admin_invoice_pay(invoice_id):
    db = get_db()
    invoice = db.execute(
        """
        SELECT hd_ct.MaTaiSan 
        FROM HOA_DON hd
        JOIN HOP_DONG hd_ct ON hd.MaHopDong = hd_ct.MaHopDong
        WHERE hd.MaHoaDon = ?
        """, (invoice_id,)
    ).fetchone()
    
    if not invoice:
        db.close()
        flash("Hóa đơn không tồn tại!", "error")
        return redirect(url_for('shared_tracking'))
        
    try:
        db.execute(
            "UPDATE HOA_DON SET TrangThaiThanhToan = 'Đã thanh toán' WHERE MaHoaDon = ?",
            (invoice_id,)
        )
        db.commit()
        flash("Xác nhận thanh toán hóa đơn thành công!", "success")
    except Exception as e:
        db.rollback()
        flash(f"Lỗi khi xác nhận thanh toán: {str(e)}", "error")
        
    db.close()
    return redirect(url_for('shared_invoices', room_id=invoice['MaTaiSan']))

@app.route('/manager/invoice/edit/<int:invoice_id>', methods=['GET', 'POST'])
@role_required([1, 2])
def manager_invoice_edit(invoice_id):
    db = get_db()
    invoice = db.execute("SELECT * FROM HOA_DON WHERE MaHoaDon = ?", (invoice_id,)).fetchone()
    
    if not invoice or invoice['TrangThaiThanhToan'] == 'Đã thanh toán':
        db.close()
        flash("Hóa đơn không tồn tại hoặc đã thanh toán (không thể sửa)!", "error")
        return redirect(url_for('shared_tracking'))
        
    contract = db.execute("SELECT * FROM HOP_DONG WHERE MaHopDong = ?", (invoice['MaHopDong'],)).fetchone()
    room = db.execute("SELECT * FROM TAI_SAN WHERE MaTaiSan = ?", (contract['MaTaiSan'],)).fetchone()
    
    chi_so_dien = db.execute(
        "SELECT * FROM CHI_SO_DIEN WHERE MaTaiSan = ? AND Thang = ? AND Nam = ?", 
        (room['MaTaiSan'], invoice['Thang'], invoice['Nam'])
    ).fetchone()
    
    if request.method == 'POST':
        new_index = int(request.form.get('new_index'))
        
        if chi_so_dien and new_index < chi_so_dien['ChiSoCu']:
            db.close()
            flash("Chỉ số mới không được nhỏ hơn chỉ số cũ!", "error")
            return redirect(url_for('manager_invoice_edit', invoice_id=invoice_id))
            
        try:
            power_consumed = new_index - chi_so_dien['ChiSoCu'] if chi_so_dien else 0
            tien_dien = (power_consumed * invoice['GiaDien']) / 1000.0 if chi_so_dien else invoice['TienDien']
            tong_tien = invoice['TienNha'] + invoice['TienDichVu'] + tien_dien
            
            if chi_so_dien:
                db.execute(
                    "UPDATE CHI_SO_DIEN SET ChiSoMoi = ? WHERE MaChiSo = ?",
                    (new_index, chi_so_dien['MaChiSo'])
                )
                
            db.execute(
                "UPDATE HOA_DON SET SoDien = ?, TienDien = ?, TongTien = ? WHERE MaHoaDon = ?",
                (power_consumed, tien_dien, tong_tien, invoice_id)
            )
            
            db.commit()
            flash("Cập nhật hóa đơn thành công!", "success")
            return redirect(url_for('shared_invoice_view', invoice_id=invoice_id))
        except Exception as e:
            db.rollback()
            flash(f"Lỗi khi cập nhật hóa đơn: {str(e)}", "error")
        finally:
            db.close()
            
        return redirect(url_for('manager_invoice_edit', invoice_id=invoice_id))
        
    db.close()
    return render_template('manager_invoice_edit.html', invoice=invoice, room=room, chi_so_dien=chi_so_dien)

@app.route('/shared/invoice/view/<int:invoice_id>')
@role_required([1, 2])
def shared_invoice_view(invoice_id):
    db = get_db()
    invoice = db.execute(
        """
        SELECT hd.*, hd_ct.NgayBatDau, hd_ct.NgayKetThuc, 
               ts.MaTaiSan, ts.SoPhong, ts.DiaChi, ts.KhuVuc,
               kt.HoTen as TenKhach, kt.SoDienThoai
        FROM HOA_DON hd
        JOIN HOP_DONG hd_ct ON hd.MaHopDong = hd_ct.MaHopDong
        JOIN TAI_SAN ts ON hd_ct.MaTaiSan = ts.MaTaiSan
        JOIN KHACH_THUE kt ON hd_ct.MaKhachThue = kt.MaKhachThue
        WHERE hd.MaHoaDon = ?
        """, (invoice_id,)
    ).fetchone()
    
    if not invoice:
        db.close()
        flash("Hóa đơn không tồn tại!", "error")
        return redirect(url_for('shared_tracking'))
        
    db.close()
    return render_template('shared_invoice_view.html', invoice=invoice)


@app.route('/shared/tenants')
@role_required([1, 2])
def shared_tenants():
    db = get_db()
    tenants = db.execute(
        """
        SELECT kt.*, ts.SoPhong, ts.DiaChi as DiaChiRoom, hd.TrangThai as TrangThaiHD
        FROM KHACH_THUE kt
        LEFT JOIN HOP_DONG hd ON kt.MaKhachThue = hd.MaKhachThue AND hd.TrangThai IN ('Đang kích hoạt', 'Giữ phòng')
        LEFT JOIN TAI_SAN ts ON hd.MaTaiSan = ts.MaTaiSan
        ORDER BY kt.MaKhachThue DESC
        """
    ).fetchall()
    db.close()
    return render_template('shared_tenants.html', tenants=tenants)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
