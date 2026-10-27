import os
import sqlite3
import time
from datetime import datetime, date, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash

app = Flask(__name__)
app.secret_key = 'super_secret_antigravity_key_for_session'

DATABASE = os.path.join(os.path.dirname(__file__), 'db', 'MyDatabase.db')

def get_db():
    conn = sqlite3.connect(DATABASE, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA cache_size = 10000;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    return conn

import json

from risk_engine import evaluate_risk

def log_audit(db, hanh_dong, bang_tac_dong, du_lieu_cu=None, du_lieu_moi=None, ghi_chu=None):
    if 'user_id' not in session: return
    try:
        def convert_dict(d):
            if d is None:
                return None
            if isinstance(d, dict):
                return d
            try:
                return dict(d)
            except Exception:
                return str(d)

        old_d = convert_dict(du_lieu_cu)
        new_d = convert_dict(du_lieu_moi)
        
        old_json = json.dumps(old_d, ensure_ascii=False) if old_d is not None else None
        new_json = json.dumps(new_d, ensure_ascii=False) if new_d is not None else None
        
        user_id = session.get('user_id')
        role_id = session.get('role_id', 3)
        client_ip = request.remote_addr if request else '127.0.0.1'
        
        # Đánh giá rủi ro tự động theo 17 quy tắc
        risk_result = evaluate_risk(
            db=db,
            user_id=user_id,
            role_id=role_id,
            action=hanh_dong,
            table_name=bang_tac_dong,
            old_data=old_d,
            new_data=new_d,
            note=ghi_chu,
            ip=client_ip
        )
        
        score = risk_result['score']
        level = risk_result['level']
        reasons_json = json.dumps(risk_result['rules'], ensure_ascii=False) if risk_result['rules'] else None
        recommendation = risk_result['recommendation']
        
        db.execute(
            """
            INSERT INTO AUDIT_LOG 
            (MaTaiKhoan, HoTen, HanhDong, BangBiTacDong, DuLieuCu, DuLieuMoi, GhiChu, DiemRuiRo, PhanLoaiRuiRo, LyDoRuiRo, KhuyenNghi, DiaChiIP, TrangThaiXuLy)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                session.get('fullname', ''),
                hanh_dong,
                bang_tac_dong,
                old_json,
                new_json,
                ghi_chu,
                score,
                level,
                reasons_json,
                recommendation,
                client_ip,
                'Chưa xử lý'
            )
        )
    except Exception as e: 
        print("Audit Log Error:", str(e))

@app.template_filter('format_vnd')
def format_vnd_filter(value):
    if value is None or value == '':
        return '0 đ'
    try:
        val = float(value)
        if 0 < abs(val) < 100000:
            val = val * 1000
        return f"{val:,.0f}".replace(',', '.') + " đ"
    except (ValueError, TypeError):
        return f"{value} đ"

@app.template_filter('format_vnd_num')
def format_vnd_num_filter(value):
    if value is None or value == '':
        return '0'
    try:
        val = float(value)
        if 0 < abs(val) < 100000:
            val = val * 1000
        return f"{val:,.0f}".replace(',', '.')
    except (ValueError, TypeError):
        return str(value)

def add_months(sourcedate, months):
    import calendar
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    day = min(sourcedate.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)

_thong_bao_cache = {
    'time': 0,
    'data': None
}

def invalidate_thong_bao_cache():
    global _thong_bao_cache
    _thong_bao_cache['time'] = 0

@app.context_processor
def inject_thong_bao():
    global _thong_bao_cache
    now = time.time()
    
    red_alerts = 0
    if session.get('role_id') == 1:
        try:
            db_alert = get_db()
            red_row = db_alert.execute(
                "SELECT COUNT(*) as cnt FROM AUDIT_LOG WHERE PhanLoaiRuiRo = 'Báo động đỏ' AND (TrangThaiXuLy = 'Chưa xử lý' OR TrangThaiXuLy IS NULL)"
            ).fetchone()
            red_alerts = red_row['cnt'] if red_row else 0
            db_alert.close()
        except:
            pass

    if _thong_bao_cache['data'] and (now - _thong_bao_cache['time'] < 15):
        cached = dict(_thong_bao_cache['data'])
        cached['unread_red_alerts_count'] = red_alerts
        return cached
    try:
        db = get_db()
        tb_list = db.execute("SELECT * FROM THONG_BAO WHERE TrangThai = 1 ORDER BY IsGhim DESC, MaThongBao DESC LIMIT 20").fetchall()
        recent_tb = [dict(t) for t in tb_list] if tb_list else []
        latest_tb = recent_tb[0] if recent_tb else None
        admin_pinned = db.execute("SELECT * FROM THONG_BAO WHERE TrangThai = 1 AND IsGhim = 1 ORDER BY MaThongBao DESC LIMIT 1").fetchone()
        db.close()
        res = dict(recent_thong_bao=recent_tb, latest_thong_bao=latest_tb, admin_pinned_thong_bao=dict(admin_pinned) if admin_pinned else None, unread_red_alerts_count=red_alerts)
        _thong_bao_cache['time'] = now
        _thong_bao_cache['data'] = res
        return res
    except Exception:
        return dict(recent_thong_bao=[], latest_thong_bao=None, admin_pinned_thong_bao=None, unread_red_alerts_count=red_alerts)

_last_activation_check = 0

@app.before_request
def check_contract_activation():
    global _last_activation_check
    if request.path.startswith('/static') or request.endpoint == 'static':
        return
    now = time.time()
    if now - _last_activation_check < 30:
        return
    _last_activation_check = now
    
    db = get_db()
    try:
        # Lập lịch tự động kích hoạt hợp đồng (Chỉ kích hoạt khi cọc đủ tiền hoặc hóa đơn đầu đã thanh toán)
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
                if (c['TongTienHoaDon'] is None) or (c['TienCoc'] >= (c['TongTienHoaDon'] - 1)) or (c['TinhTrangHoaDon'] == 'Đã thanh toán'):
                    db.execute(
                        "UPDATE HOA_DON SET TrangThaiThanhToan = 'Đã thanh toán' WHERE MaHopDong = ? AND TrangThaiThanhToan = 'Chưa thanh toán'",
                        (c['MaHopDong'],)
                    )
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
        print("Lỗi tự động kích hoạt hợp đồng:", e)
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


@app.route('/sale/bookings')
@role_required([3])
def sale_bookings():
    user_id = session['user_id']
    db = get_db()
    
    # 1. Phiếu chốt đang giữ phòng (hợp đồng có trạng thái 'Giữ phòng') của Sale này
    my_bookings = db.execute(
        """
        SELECT tk.*, ts.SoPhong, ts.DiaChi, ts.KhuVuc, ts.GiaThue, ts.HoaHong as HoaHongTaiSan, hd.NgayBatDau, hd.TrangThai as TrangThaiHopDong
        FROM THONG_TIN_CHOT_KHACH tk
        JOIN TAI_SAN ts ON tk.MaTaiSan = ts.MaTaiSan
        JOIN HOP_DONG hd ON tk.MaHopDong = hd.MaHopDong
        WHERE tk.MaTaiKhoanChot = ? AND hd.TrangThai = 'Giữ phòng'
        ORDER BY tk.MaChotKhach DESC
        """, (user_id,)
    ).fetchall()
    
    # 2. Hợp đồng đã hiệu lực của Sale này (hợp đồng có trạng thái khác 'Giữ phòng')
    my_contracts = db.execute(
        """
        SELECT hd.*, ts.SoPhong, ts.DiaChi, ts.KhuVuc, kt.HoTen as TenKhach, kt.SoDienThoai
        FROM HOP_DONG hd
        JOIN TAI_SAN ts ON hd.MaTaiSan = ts.MaTaiSan
        JOIN KHACH_THUE kt ON hd.MaKhachThue = kt.MaKhachThue
        JOIN THONG_TIN_CHOT_KHACH tk ON hd.MaHopDong = tk.MaHopDong
        WHERE tk.MaTaiKhoanChot = ? AND hd.TrangThai != 'Giữ phòng'
        ORDER BY hd.MaHopDong DESC
        """, (user_id,)
    ).fetchall()
    
    today = date.today()
    contracts_list = []
    for c in my_contracts:
        c_dict = dict(c)
        try:
            start_d = datetime.strptime(c['NgayBatDau'], '%Y-%m-%d').date()
            end_d = datetime.strptime(c['NgayKetThuc'], '%Y-%m-%d').date()
            days_from_start = (today - start_d).days
            days_to_end = (end_d - today).days
            c_dict['IsNew'] = (0 <= days_from_start <= 30)
            c_dict['IsExpired'] = (days_to_end < 0)
            c_dict['IsExpiringSoon'] = (0 <= days_to_end <= 30)
            c_dict['DaysRemaining'] = days_to_end
        except Exception:
            c_dict['IsNew'] = False
            c_dict['IsExpired'] = False
            c_dict['IsExpiringSoon'] = False
            c_dict['DaysRemaining'] = 999
        contracts_list.append(c_dict)
    
    # 3. Khách thuê của bản thân Sale này
    my_tenants = db.execute(
        """
        SELECT DISTINCT kt.*, ts.SoPhong, ts.DiaChi as DiaChiRoom, hd.MaHopDong, hd.TrangThai as TrangThaiHopDong
        FROM KHACH_THUE kt
        JOIN HOP_DONG hd ON kt.MaKhachThue = hd.MaKhachThue
        JOIN THONG_TIN_CHOT_KHACH tk ON hd.MaHopDong = tk.MaHopDong
        JOIN TAI_SAN ts ON hd.MaTaiSan = ts.MaTaiSan
        WHERE tk.MaTaiKhoanChot = ?
        ORDER BY kt.MaKhachThue DESC
        """, (user_id,)
    ).fetchall()
    
    db.close()
    current_date = date.today().strftime('%d/%m/%Y')
    
    return render_template(
        'sale_bookings.html',
        bookings=my_bookings,
        contracts=contracts_list,
        tenants=my_tenants,
        current_date=current_date
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
            # Nếu khách đã đóng đủ tiền đợt 1 (1 cọc 1 kỳ đầu, deposit_amount >= tong_tien_hoa_don_1 - 1)
            trang_thai_hd1 = 'Đã thanh toán' if deposit_amount >= (tong_tien_hoa_don_1 - 1) else 'Chưa thanh toán'
            cursor.execute(
                """
                INSERT INTO HOA_DON 
                (MaHopDong, Thang, Nam, TienNha, SoDien, GiaDien, TienDien, GiaDichVu, TienDichVu, TongTien, NgayTao, TrangThaiThanhToan)
                VALUES (?, ?, ?, ?, 0, ?, 0.00, ?, ?, ?, ?, ?)
                """,
                (hop_dong_id, start_dt.month, start_dt.year, tien_nha_le, gia_dien, gia_dv, tien_dich_vu_le, tong_tien_hoa_don_1, date.today().isoformat(), trang_thai_hd1)
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
            
            # 6. Khóa phòng lập tức ở trạng thái Đang thuê (nếu đã đóng đủ tiền) hoặc Giữ phòng (nếu cọc một phần)
            room_status = 'DangThue' if trang_thai_hd1 == 'Đã thanh toán' else 'Giữ phòng'
            contract_status = 'Đang kích hoạt' if trang_thai_hd1 == 'Đã thanh toán' else 'Giữ phòng'
            cursor.execute("UPDATE HOP_DONG SET TrangThai = ? WHERE MaHopDong = ?", (contract_status, hop_dong_id))
            cursor.execute(
                "UPDATE TAI_SAN SET TrangThai = ?, GiaThue = ? WHERE MaTaiSan = ?", (room_status, gia_thue, room_id)
            )
            
            # 7. Tự động phát thông báo tức thì lên toàn hệ thống để các Sale khác không tư vấn trùng
            sale_fullname = session.get('fullname', 'Sale')
            msg_thong_bao = f"{sale_fullname}: Phòng {room['SoPhong']} {room['DiaChi']} chốt"
            cursor.execute("INSERT INTO THONG_BAO (NoiDung, TrangThai, LoaiThongBao, IsGhim) VALUES (?, 1, 'CHOT', 0)", (msg_thong_bao,))
            
            # Ghi Audit Log cho hành động chốt cọc của Sale
            new_booking_data = {
                'MaTaiSan': room_id,
                'TenKhach': tenant_name,
                'SoDienThoaiKhach': tenant_phone,
                'CCCD': tenant_cccd,
                'TienCoc': deposit_amount,
                'GiaThue': gia_thue,
                'NgayDuKienVaoO': move_in_date,
                'SoDienDauVao': elec_start,
                'GhiChu': notes
            }
            log_audit(db, 'Thêm Phiếu Cọc', 'THONG_TIN_CHOT_KHACH', du_lieu_moi=new_booking_data, ghi_chu=f"Sale chốt giữ chỗ phòng {room['SoPhong']} cho khách {tenant_name}")
            
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
                
            # Ghi Audit Log cho hành động sửa phiếu cọc của Sale
            new_booking_data = {
                'MaChotKhach': deal_id,
                'TenKhach': tenant_name,
                'SoDienThoaiKhach': tenant_phone,
                'CCCD': tenant_cccd,
                'TienCoc': deposit_amount,
                'GiaThue': gia_thue,
                'NgayDuKienVaoO': move_in_date,
                'ThoiHanThue': duration,
                'SoDienDauVao': elec_start,
                'SoNguoi': so_nguoi,
                'GhiChu': notes
            }
            log_audit(db, 'Sửa Phiếu Cọc', 'THONG_TIN_CHOT_KHACH', du_lieu_cu=deal, du_lieu_moi=new_booking_data, ghi_chu=f"Sale chỉnh sửa phiếu cọc #{deal_id} cho khách {tenant_name}")

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

@app.route('/sale/booking/cancel/<int:deal_id>', methods=['POST'])
@role_required([3])
def sale_booking_cancel(deal_id):
    user_id = session['user_id']
    db = get_db()
    deal = db.execute(
        "SELECT * FROM THONG_TIN_CHOT_KHACH WHERE MaChotKhach = ? AND MaTaiKhoanChot = ?",
        (deal_id, user_id)
    ).fetchone()
    
    if not deal:
        db.close()
        flash("Phiếu chốt khách không tồn tại hoặc không thuộc quyền quản lý của bạn!", "error")
        return redirect(url_for('sale_bookings'))
        
    room = db.execute("SELECT * FROM TAI_SAN WHERE MaTaiSan = ?", (deal['MaTaiSan'],)).fetchone()
    if not room:
        db.close()
        flash("Thông tin phòng không tồn tại!", "error")
        return redirect(url_for('sale_bookings'))
        
    reason = request.form.get('reason', 'Khách đổi ý không thuê (bỏ cọc)')
    
    try:
        # 1. Cập nhật hợp đồng liên quan (nếu có) thành 'Bỏ cọc'
        if deal['MaHopDong']:
            db.execute("UPDATE HOP_DONG SET TrangThai = 'Bỏ cọc' WHERE MaHopDong = ?", (deal['MaHopDong'],))
            
        # 2. Cập nhật trạng thái phiếu chốt thành 'Bo coc'
        db.execute(
            "UPDATE THONG_TIN_CHOT_KHACH SET TrangThai = 'Bo coc', GhiChu = ? WHERE MaChotKhach = ?",
            (f"[BỎ CỌC] Lý do: {reason} | Ghi chú cũ: {deal['GhiChu'] or ''}", deal_id)
        )
        
        # 3. Mở lại phòng về trạng thái 'Trong'
        db.execute("UPDATE TAI_SAN SET TrangThai = 'Trong' WHERE MaTaiSan = ?", (deal['MaTaiSan'],))
        
        # 4. Tự động phát thông báo tức thì lên toàn hệ thống để các Sale khác biết ngay
        msg_thong_bao = f"Phòng {room['SoPhong']} {room['DiaChi']} trống khách bỏ cọc"
        db.execute("INSERT INTO THONG_BAO (NoiDung, TrangThai, LoaiThongBao, IsGhim) VALUES (?, 1, 'TRONG', 0)", (msg_thong_bao,))
        
        # 5. Ghi Audit Log và kích hoạt Động Cơ Phát Hiện Bất Thường Ngầm (CANC_01 đến CANC_04)
        new_data = {
            'MaChotKhach': deal_id,
            'TrangThai': 'Bo coc',
            'LyDoHuy': reason,
            'TienCoc': deal['TienCoc'],
            'NgayChot': deal['NgayChot'],
            'ThoiGianHuy': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        log_audit(db, 'Hủy Phiếu Cọc', 'THONG_TIN_CHOT_KHACH', du_lieu_cu=deal, du_lieu_moi=new_data, ghi_chu=f"Sale hủy cọc phiếu #{deal_id} phòng {room['SoPhong']}: {reason}")
        
        db.commit()
        flash(f"Đã hủy phiếu cọc phòng {room['SoPhong']} thành công. Phòng đã mở lại Trống và gửi thông báo tức thì tới toàn hệ thống!", "success")
    except Exception as e:
        db.rollback()
        flash(f"Lỗi khi hủy cọc: {str(e)}", "error")
    finally:
        db.close()
        
    return redirect(url_for('sale_bookings'))

@app.route('/sale/contract/renew/<int:contract_id>', methods=['POST'])
@role_required([3])
def sale_contract_renew(contract_id):
    user_id = session['user_id']
    db = get_db()
    contract = db.execute(
        """
        SELECT hd.*, ts.SoPhong, ts.DiaChi, kt.HoTen as TenKhach
        FROM HOP_DONG hd
        JOIN TAI_SAN ts ON hd.MaTaiSan = ts.MaTaiSan
        JOIN KHACH_THUE kt ON hd.MaKhachThue = kt.MaKhachThue
        JOIN THONG_TIN_CHOT_KHACH tk ON hd.MaHopDong = tk.MaHopDong
        WHERE hd.MaHopDong = ? AND tk.MaTaiKhoanChot = ?
        """, (contract_id, user_id)
    ).fetchone()
    
    if not contract:
        db.close()
        flash("Hợp đồng không tồn tại hoặc không thuộc quyền quản lý của bạn!", "error")
        return redirect(url_for('sale_bookings'))
        
    duration_months = int(request.form.get('duration_months', 6))
    new_price = request.form.get('new_price')
    new_price = float(new_price) if new_price else float(contract['GiaThue'])
    notes = request.form.get('notes', '')
    
    # Tính ngày bắt đầu và ngày kết thúc gia hạn
    old_end = datetime.strptime(contract['NgayKetThuc'], '%Y-%m-%d').date()
    today_d = date.today()
    if old_end >= today_d:
        new_start = old_end + timedelta(days=1)
    else:
        new_start = today_d
    new_end = add_months(new_start, duration_months)
    
    hoa_hong_gia_han = 60.0  # 60k VND
    
    try:
        old_data = dict(contract)
        # 1. Lưu bản ghi vào bảng GIA_HAN_HOP_DONG
        db.execute(
            """
            INSERT INTO GIA_HAN_HOP_DONG 
            (MaHopDong, MaTaiKhoanSale, NgayGiaHan, NgayBatDauMoi, NgayKetThucMoi, SoThangGiaHan, GiaThueMoi, HoaHongGiaHan, GhiChu)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (contract_id, user_id, today_d.isoformat(), new_start.isoformat(), new_end.isoformat(), duration_months, new_price, hoa_hong_gia_han, notes)
        )
        
        # 2. Cập nhật HĐ
        db.execute(
            """
            UPDATE HOP_DONG 
            SET NgayKetThuc = ?, ThoiHanThue = ThoiHanThue + ?, GiaThue = ?, TrangThai = 'Đang kích hoạt'
            WHERE MaHopDong = ?
            """,
            (new_end.isoformat(), duration_months, new_price, contract_id)
        )
        
        # 3. Tạo thông báo chung
        sale_name = session.get('fullname', 'Sale')
        db.execute(
            """
            INSERT INTO THONG_BAO (NoiDung, TrangThai, LoaiThongBao, IsGhim)
            VALUES (?, 1, 'CHOT', 0)
            """,
            (f"{sale_name}: Gia hạn HĐ-{contract_id:03d} (Phòng {contract['SoPhong']} {contract['DiaChi']}) thêm {duration_months} tháng 🎉",)
        )
        
        # 4. Ghi Audit Log
        new_data = dict(db.execute("SELECT * FROM HOP_DONG WHERE MaHopDong = ?", (contract_id,)).fetchone())
        log_audit(db, "Gia Hạn Hợp Đồng", "HOP_DONG", du_lieu_cu=old_data, du_lieu_moi=new_data, ghi_chu=f"Gia hạn HĐ-{contract_id:03d} thêm {duration_months} tháng (+60k hoa hồng)")
        
        db.commit()
        flash(f"Gia hạn hợp đồng HĐ-{contract_id:03d} thành công! Bạn nhận được +60.000đ hoa hồng gia hạn.", "success")
    except Exception as e:
        db.rollback()
        flash(f"Lỗi khi gia hạn hợp đồng: {str(e)}", "error")
    finally:
        db.close()
        
    return redirect(url_for('sale_bookings'))


@app.route('/sale/salary')
@role_required([3])
def sale_salary():
    user_id = session['user_id']
    db = get_db()
    
    today = date.today()
    try:
        month = int(request.args.get('month', today.month))
        year = int(request.args.get('year', today.year))
    except (ValueError, TypeError):
        month = today.month
        year = today.year
        
    start_date = f"{year}-{month:02d}-01"
    if month == 12:
        end_date = f"{year+1}-01-01"
    else:
        end_date = f"{year}-{month+1:02d}-01"
        
    # Thông tin Sale và KPI
    sale_info = db.execute("SELECT HoTen, LuongCung, SoDienThoai FROM TAI_KHOAN WHERE MaTaiKhoan = ?", (user_id,)).fetchone()
    luong_cung = float(sale_info['LuongCung'] if sale_info and sale_info['LuongCung'] else 0.0)
    
    config = db.execute("SELECT ChiTieuKPI FROM CAU_HINH_GIA WHERE TrangThai = 1 LIMIT 1").fetchone()
    chi_tieu_kpi = config['ChiTieuKPI'] if config else 6
    
    # 1. Danh sách phòng chốt mới trong tháng
    deals = db.execute(
        """
        SELECT tk.*, ts.SoPhong, ts.DiaChi, ts.KhuVuc, ts.GiaThue as GiaThuePhong, ts.HoaHong as HoaHongTaiSan,
               kt.HoTen as TenKhachThue, hd.MaHopDong, hd.TrangThai as TrangThaiHopDong
        FROM THONG_TIN_CHOT_KHACH tk
        JOIN TAI_SAN ts ON tk.MaTaiSan = ts.MaTaiSan
        LEFT JOIN KHACH_THUE kt ON tk.MaKhachThue = kt.MaKhachThue
        LEFT JOIN HOP_DONG hd ON tk.MaHopDong = hd.MaHopDong
        WHERE tk.MaTaiKhoanChot = ? AND tk.NgayChot >= ? AND tk.NgayChot < ?
        ORDER BY tk.NgayChot DESC
        """, (user_id, start_date, end_date)
    ).fetchall()
    
    # 2. Danh sách hợp đồng gia hạn trong tháng (mỗi HĐ +60k)
    renewals = db.execute(
        """
        SELECT gh.*, ts.SoPhong, ts.DiaChi, ts.KhuVuc, kt.HoTen as TenKhach, kt.SoDienThoai,
               hd.NgayBatDau, hd.NgayKetThuc as NgayKetThucHienTai
        FROM GIA_HAN_HOP_DONG gh
        JOIN HOP_DONG hd ON gh.MaHopDong = hd.MaHopDong
        JOIN TAI_SAN ts ON hd.MaTaiSan = ts.MaTaiSan
        JOIN KHACH_THUE kt ON hd.MaKhachThue = kt.MaKhachThue
        WHERE gh.MaTaiKhoanSale = ? AND gh.NgayGiaHan >= ? AND gh.NgayGiaHan < ?
        ORDER BY gh.NgayGiaHan DESC
        """, (user_id, start_date, end_date)
    ).fetchall()
    
    so_phong_chot = len(deals)
    hoa_hong_chot = sum(float(d['HoaHongTaiSan'] or 0.0) for d in deals)
    
    so_gia_han = len(renewals)
    hoa_hong_gia_han = sum(float(r['HoaHongGiaHan'] or 60.0) for r in renewals)
    
    dat_kpi = so_phong_chot >= chi_tieu_kpi
    luong_cung_nhan = luong_cung if dat_kpi else 0.0
    tong_thu_nhap = luong_cung_nhan + hoa_hong_chot + hoa_hong_gia_han
    
    # 3. Lịch sử bảng lương 12 tháng trong năm được chọn (Tối ưu thành 2 grouped queries duy nhất)
    year_start = f"{year}-01-01"
    year_end = f"{year+1}-01-01"
    
    deals_rows = db.execute(
        """
        SELECT CAST(strftime('%m', tk.NgayChot) AS INTEGER) as Thang, 
               COUNT(*) as count, 
               SUM(ts.HoaHong) as total_hh 
        FROM THONG_TIN_CHOT_KHACH tk 
        JOIN TAI_SAN ts ON tk.MaTaiSan = ts.MaTaiSan 
        WHERE tk.MaTaiKhoanChot = ? AND tk.NgayChot >= ? AND tk.NgayChot < ?
        GROUP BY Thang
        """, (user_id, year_start, year_end)
    ).fetchall()
    deals_by_month = {row['Thang']: row for row in deals_rows}
        
    renewals_rows = db.execute(
        """
        SELECT CAST(strftime('%m', NgayGiaHan) AS INTEGER) as Thang, 
               COUNT(*) as count, 
               SUM(HoaHongGiaHan) as total_hh 
        FROM GIA_HAN_HOP_DONG 
        WHERE MaTaiKhoanSale = ? AND NgayGiaHan >= ? AND NgayGiaHan < ?
        GROUP BY Thang
        """, (user_id, year_start, year_end)
    ).fetchall()
    renewals_by_month = {row['Thang']: row for row in renewals_rows}

    monthly_history = []
    for m in range(1, 13):
        m_deals = deals_by_month.get(m)
        m_renewals = renewals_by_month.get(m)
        
        m_chot = (m_deals['count'] if m_deals else 0) or 0
        m_hh_chot = float((m_deals['total_hh'] if m_deals else 0.0) or 0.0)
        m_gh = (m_renewals['count'] if m_renewals else 0) or 0
        m_hh_gh = float((m_renewals['total_hh'] if m_renewals else 0.0) or 0.0)
        m_kpi = m_chot >= chi_tieu_kpi
        m_lc = luong_cung if m_kpi else 0.0
        m_tong = m_lc + m_hh_chot + m_hh_gh
        
        monthly_history.append({
            'Thang': m,
            'Nam': year,
            'SoPhongChot': m_chot,
            'HoaHongChot': m_hh_chot,
            'SoGiaHan': m_gh,
            'HoaHongGiaHan': m_hh_gh,
            'DatKPI': m_kpi,
            'LuongCung': m_lc,
            'TongThuNhap': m_tong,
            'IsSelected': (m == month)
        })
        
    db.close()
    
    return render_template(
        'sale_salary.html',
        month=month,
        year=year,
        sale_info=sale_info,
        chi_tieu_kpi=chi_tieu_kpi,
        deals=deals,
        renewals=renewals,
        so_phong_chot=so_phong_chot,
        hoa_hong_chot=hoa_hong_chot,
        so_gia_han=so_gia_han,
        hoa_hong_gia_han=hoa_hong_gia_han,
        dat_kpi=dat_kpi,
        luong_cung=luong_cung,
        luong_cung_nhan=luong_cung_nhan,
        tong_thu_nhap=tong_thu_nhap,
        monthly_history=monthly_history
    )

# ==========================================
# PHÂN HỆ DÀNH CHO NHÂN VIÊN VẬN HÀNH (ROLE = 2)
# ==========================================



@app.route('/manager/dashboard')
@role_required([1, 2])
def manager_dashboard():
    return redirect(url_for('manager_thanhtoan'))

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
    
    # Lấy thông tin chốt mới nhất của các phòng trong 1 truy vấn duy nhất
    latest_deals_rows = db.execute(
        """
        SELECT tk.*
        FROM THONG_TIN_CHOT_KHACH tk
        INNER JOIN (
            SELECT MaTaiSan, MAX(MaChotKhach) as max_id
            FROM THONG_TIN_CHOT_KHACH
            GROUP BY MaTaiSan
        ) latest ON tk.MaTaiSan = latest.MaTaiSan AND tk.MaChotKhach = latest.max_id
        """
    ).fetchall()
    deals_by_room = {d['MaTaiSan']: d for d in latest_deals_rows}

    # Lấy danh sách phòng có nợ quá hạn trong 1 truy vấn duy nhất
    overdue_rows = db.execute(
        """
        SELECT DISTINCT h.MaTaiSan
        FROM HOA_DON hd
        JOIN HOP_DONG h ON hd.MaHopDong = h.MaHopDong
        WHERE h.TrangThai IN ('Đang kích hoạt', 'Giữ phòng') AND hd.TrangThaiThanhToan = 'Chưa thanh toán'
        """
    ).fetchall()
    overdue_room_ids = {row['MaTaiSan'] for row in overdue_rows}

    rooms = []
    for r in rooms_list:
        room_dict = dict(r)
        deal = deals_by_room.get(r['MaTaiSan'])
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
            
        room_dict['HasOverdue'] = r['MaTaiSan'] in overdue_room_ids
        room_dict['GiaThueThucTe'] = deal['GiaThue'] if (deal and deal['GiaThue']) else r['GiaThue']
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
            trang_thai_hd1 = 'Đã thanh toán' if deposit_received >= (tong_tien_hoa_don_1 - 1) else 'Chưa thanh toán'
            cursor.execute(
                """
                INSERT INTO HOA_DON 
                (MaHopDong, Thang, Nam, TienNha, SoDien, GiaDien, TienDien, GiaDichVu, TienDichVu, TongTien, NgayTao, TrangThaiThanhToan)
                VALUES (?, ?, ?, ?, 0, ?, 0.00, ?, ?, ?, ?, ?)
                """,
                (hop_dong_id, start_dt.month, start_dt.year, tien_nha_le, gia_dien, gia_dv, tien_dich_vu_le, tong_tien_hoa_don_1, date.today().isoformat(), trang_thai_hd1)
            )
            
            # Cập nhật trạng thái hợp đồng
            contract_status = 'Đang kích hoạt' if trang_thai_hd1 == 'Đã thanh toán' else 'Giữ phòng'
            cursor.execute("UPDATE HOP_DONG SET TrangThai = ? WHERE MaHopDong = ?", (contract_status, hop_dong_id))
            
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
            
            # Ghi nhật ký kiểm toán cho chỉ số điện
            log_audit(
                db,
                'Nhập Chỉ Số Điện',
                'CHI_SO_DIEN',
                du_lieu_cu={'SoPhong': room['SoPhong'], 'DiaChi': room['DiaChi'], 'ChiSoCu': old_index},
                du_lieu_moi={'SoPhong': room['SoPhong'], 'DiaChi': room['DiaChi'], 'ChiSoCu': old_index, 'ChiSoMoi': new_index, 'SoDien': power_consumed},
                ghi_chu=f"Nhập chỉ số điện tháng {month}/{year} phòng {room['SoPhong']} - {room['DiaChi']} (Tiêu thụ: {power_consumed} kWh)"
            )
            
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
                room_desc = "Kho chung / Khác"
                if room_id:
                    room = db.execute("SELECT ThietBi, SoPhong, DiaChi FROM TAI_SAN WHERE MaTaiSan = ?", (room_id,)).fetchone()
                    if room:
                        room_desc = f"Phòng {room['SoPhong']} ({room['DiaChi']})"
                        thiet_bi_cu = room['ThietBi']
                        ten_thiet_bi = vat_tu['TenVatTu']
                        
                        if thiet_bi_cu:
                            thiet_bi_moi = f"{thiet_bi_cu}, Lắp thêm {quantity} {ten_thiet_bi}"
                        else:
                            thiet_bi_moi = f"Lắp thêm {quantity} {ten_thiet_bi}"
                            
                        db.execute(
                            "UPDATE TAI_SAN SET ThietBi = ? WHERE MaTaiSan = ?", (thiet_bi_moi, room_id)
                        )
                
                # Ghi Audit Log cho Xuất kho vật tư
                log_audit(
                    db,
                    'Xuất Kho Vật Tư',
                    'LICH_SU_KHO',
                    du_lieu_cu={'VatTu': vat_tu['TenVatTu'], 'TonKhoCu': vat_tu['SoLuongTon']},
                    du_lieu_moi={
                        'VatTu': vat_tu['TenVatTu'],
                        'SoLuongXuat': quantity,
                        'TonKhoMoi': vat_tu['SoLuongTon'] - quantity,
                        'PhongNhan': room_desc,
                        'LyDo': reason
                    },
                    ghi_chu=f"Xuất {quantity} {vat_tu['TenVatTu']} lắp đặt cho {room_desc}. Lý do: {reason or 'Không ghi chú'}"
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
        expense_id = request.form.get('expense_id')
        ten_chi_phi = request.form.get('ten_chi_phi')
        so_tien = float(request.form.get('so_tien').replace(',', ''))
        ngay_nhap = request.form.get('ngay_nhap', date.today().isoformat())
        ghi_chu = request.form.get('ghi_chu')
        thang = int(ngay_nhap.split('-')[1])
        nam = int(ngay_nhap.split('-')[0])
        ma_tai_khoan = session.get('user_id')
        
        try:
            if expense_id:
                old_exp = db.execute("SELECT * FROM CHI_PHI WHERE MaChiPhi = ?", (expense_id,)).fetchone()
                db.execute(
                    """
                    UPDATE CHI_PHI 
                    SET Thang = ?, Nam = ?, TenChiPhi = ?, SoTien = ?, NgayNhap = ?, GhiChu = ?
                    WHERE MaChiPhi = ?
                    """,
                    (thang, nam, ten_chi_phi, so_tien, ngay_nhap, ghi_chu, expense_id)
                )
                new_exp = db.execute("SELECT * FROM CHI_PHI WHERE MaChiPhi = ?", (expense_id,)).fetchone()
                log_audit(db, 'Sửa Chi Phí', 'CHI_PHI', du_lieu_cu=old_exp, du_lieu_moi=new_exp, ghi_chu=f"Sửa khoản chi: {ten_chi_phi}")
                db.commit()
                flash("Cập nhật khoản chi phí thành công!", "success")
            else:
                cursor = db.execute(
                    """
                    INSERT INTO CHI_PHI (Thang, Nam, TenChiPhi, SoTien, NgayNhap, MaTaiKhoanNhap, GhiChu)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (thang, nam, ten_chi_phi, so_tien, ngay_nhap, ma_tai_khoan, ghi_chu)
                )
                new_exp = db.execute("SELECT * FROM CHI_PHI WHERE MaChiPhi = ?", (cursor.lastrowid,)).fetchone()
                log_audit(db, 'Thêm Chi Phí', 'CHI_PHI', du_lieu_moi=new_exp, ghi_chu=f"Thêm khoản chi mới: {ten_chi_phi}")
                db.commit()
                flash("Thêm khoản chi phí thành công!", "success")
        except Exception as e:
            db.rollback()
            flash(f"Lỗi ghi nhận chi phí: {str(e)}", "error")
            
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
    expense_old = db.execute("SELECT * FROM CHI_PHI WHERE MaChiPhi = ?", (expense_id,)).fetchone()
    db.execute("DELETE FROM CHI_PHI WHERE MaChiPhi = ?", (expense_id,))
    log_audit(db, "Xóa Chi Phí", "CHI_PHI", du_lieu_cu=expense_old, ghi_chu=f"Xóa chi phí ID {expense_id}")
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
               SUM(CASE WHEN TrangThaiThanhToan = 'Đã thanh toán' THEN (CASE WHEN TongTien < 100000 THEN TongTien * 1000 ELSE TongTien END) ELSE 0 END) as Revenue,
               SUM(CASE WHEN TrangThaiThanhToan = 'Chưa thanh toán' THEN (CASE WHEN TongTien < 100000 THEN TongTien * 1000 ELSE TongTien END) ELSE 0 END) as Debt
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
        monthly_data[key] = {'Nam': r['Nam'], 'Thang': r['Thang'], 'Revenue': r['Revenue'] or 0.0, 'Debt': r['Debt'] or 0.0, 'Expense': 0}
        
    for r in expense_rows:
        key = f"{r['Nam']}-{r['Thang']}"
        if key not in monthly_data:
            monthly_data[key] = {'Nam': r['Nam'], 'Thang': r['Thang'], 'Revenue': 0, 'Debt': 0, 'Expense': r['Expense'] or 0.0}
        else:
            monthly_data[key]['Expense'] = r['Expense'] or 0.0
            
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
        
    for row in sorted_months:
        chart_labels.append(f"T{row['Thang']}/{row['Nam']}")
        chart_revenue.append(row['Revenue'])
        chart_debt.append(row['Debt'])
        chart_expenses.append(row['Expense'])
        
    total_profit = total_revenue - total_expenses
        
    # Khối 3: Thống kê số lượng phòng theo trạng thái
    rooms = db.execute("SELECT TrangThai, COUNT(*) as count FROM TAI_SAN GROUP BY TrangThai").fetchall()
    
    room_stats = {'Trong': 0, 'Giữ phòng': 0, 'DangThue': 0}
    for r in rooms:
        st = r['TrangThai']
        if st in room_stats:
            room_stats[st] = r['count']
        elif st == 'DatCoc':
            room_stats['Giữ phòng'] += r['count']
            
    # Lấy toàn bộ danh sách thông báo cho Admin quản lý
    all_thong_bao = db.execute("SELECT * FROM THONG_BAO ORDER BY IsGhim DESC, MaThongBao DESC").fetchall()

    db.close()
    return render_template(
        'admin_dashboard.html', 
        total_revenue=total_revenue, 
        total_debt=total_debt,
        total_expenses=total_expenses,
        total_profit=total_profit,
        room_stats=room_stats,
        all_thong_bao=all_thong_bao,
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

@app.route('/admin/audit-log')
@role_required([1])
def admin_audit_log():
    db = get_db()
    now = datetime.now()
    
    # Mặc định chỉ hiển thị log hoạt động của tháng hiện tại (ví dụ: tháng 10)
    if 'month' not in request.args:
        selected_month = str(now.month)
    else:
        selected_month = request.args.get('month', '')
        
    if 'year' not in request.args:
        selected_year = str(now.year)
    else:
        selected_year = request.args.get('year', '')
        
    selected_action = request.args.get('action', '')
    selected_risk = request.args.get('risk', '')
    selected_status = request.args.get('status', '')
    
    query = "SELECT * FROM AUDIT_LOG WHERE 1=1"
    params = []
    
    if selected_year and selected_year != 'all':
        query += " AND strftime('%Y', ThoiGian) = ?"
        params.append(str(selected_year))
        
    if selected_month and selected_month != 'all':
        query += " AND CAST(strftime('%m', ThoiGian) AS INTEGER) = ?"
        params.append(int(selected_month))
        
    if selected_action:
        query += " AND HanhDong LIKE ?"
        params.append(f"%{selected_action}%")

    if selected_risk == 'red':
        query += " AND PhanLoaiRuiRo = 'Báo động đỏ'"
    elif selected_risk == 'yellow':
        query += " AND PhanLoaiRuiRo = 'Cần lưu ý'"
    elif selected_risk == 'green':
        query += " AND (PhanLoaiRuiRo = 'An toàn' OR PhanLoaiRuiRo IS NULL)"

    if selected_status == 'pending':
        query += " AND (TrangThaiXuLy = 'Chưa xử lý' OR TrangThaiXuLy IS NULL)"
    elif selected_status == 'verified':
        query += " AND TrangThaiXuLy = 'Đã xác minh'"
    elif selected_status == 'disputed':
        query += " AND TrangThaiXuLy = 'Cần giải trình'"
        
    query += " ORDER BY MaLog DESC LIMIT 300"
    
    logs = db.execute(query, params).fetchall()
    
    # Thống kê tổng quan KPI rủi ro
    stats_row = db.execute(
        """
        SELECT 
            COUNT(*) as total_count,
            SUM(CASE WHEN PhanLoaiRuiRo = 'Báo động đỏ' THEN 1 ELSE 0 END) as red_count,
            SUM(CASE WHEN PhanLoaiRuiRo = 'Cần lưu ý' THEN 1 ELSE 0 END) as yellow_count,
            SUM(CASE WHEN PhanLoaiRuiRo = 'An toàn' OR PhanLoaiRuiRo IS NULL THEN 1 ELSE 0 END) as green_count,
            SUM(CASE WHEN PhanLoaiRuiRo = 'Báo động đỏ' AND (TrangThaiXuLy = 'Chưa xử lý' OR TrangThaiXuLy IS NULL) THEN 1 ELSE 0 END) as pending_red_count
        FROM AUDIT_LOG
        """
    ).fetchone()
    
    stats = {
        'total': stats_row['total_count'] or 0,
        'red': stats_row['red_count'] or 0,
        'yellow': stats_row['yellow_count'] or 0,
        'green': stats_row['green_count'] or 0,
        'pending_red': stats_row['pending_red_count'] or 0
    }
    
    # Lấy danh sách các năm có trong log
    years_db = db.execute("SELECT DISTINCT strftime('%Y', ThoiGian) as Nam FROM AUDIT_LOG WHERE ThoiGian IS NOT NULL ORDER BY Nam DESC").fetchall()
    available_years = [y['Nam'] for y in years_db if y['Nam']]
    current_year_str = str(date.today().year)
    if current_year_str not in available_years:
        available_years.insert(0, current_year_str)
        
    # Danh sách các loại hành động để lọc
    actions_db = db.execute("SELECT DISTINCT HanhDong FROM AUDIT_LOG WHERE HanhDong IS NOT NULL ORDER BY HanhDong").fetchall()
    available_actions = [a['HanhDong'] for a in actions_db if a['HanhDong']]
    
    db.close()
    return render_template(
        'admin_audit_log.html', 
        logs=logs,
        stats=stats,
        selected_month=selected_month,
        selected_year=selected_year,
        selected_action=selected_action,
        selected_risk=selected_risk,
        selected_status=selected_status,
        available_years=available_years,
        available_actions=available_actions
    )

@app.route('/admin/audit-log/update-status/<int:log_id>', methods=['POST'])
@role_required([1])
def admin_audit_log_update_status(log_id):
    db = get_db()
    new_status = request.form.get('status', 'Đã xác minh')
    db.execute("UPDATE AUDIT_LOG SET TrangThaiXuLy = ? WHERE MaLog = ?", (new_status, log_id))
    db.commit()
    db.close()
    flash(f"Đã cập nhật trạng thái hồ sơ kiểm toán #{log_id} thành '{new_status}'!", "success")
    return redirect(request.referrer or url_for('admin_audit_log'))

@app.route('/admin/thong-bao', methods=['POST'])
@role_required([1])
def admin_thong_bao():
    noidung = request.form.get('noidung')
    if noidung:
        db = get_db()
        # Bỏ ghim các thông báo cũ của admin để ghim thông báo mới nhất
        db.execute("UPDATE THONG_BAO SET IsGhim = 0 WHERE LoaiThongBao = 'ADMIN'")
        db.execute("INSERT INTO THONG_BAO (NoiDung, TrangThai, LoaiThongBao, IsGhim) VALUES (?, 1, 'ADMIN', 1)", (noidung,))
        db.commit()
        db.close()
        flash("Đã phát và ghim thông báo của Sếp lên Bảng tin!", "success")
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/admin/thong-bao/delete/<int:id>', methods=['POST'])
@role_required([1])
def admin_thong_bao_delete(id):
    db = get_db()
    db.execute("DELETE FROM THONG_BAO WHERE MaThongBao = ?", (id,))
    db.commit()
    db.close()
    flash("Đã xóa thông báo khỏi Bảng tin thành công!", "success")
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/admin/thong-bao/toggle-pin/<int:id>', methods=['POST'])
@role_required([1])
def admin_thong_bao_toggle_pin(id):
    db = get_db()
    tb = db.execute("SELECT * FROM THONG_BAO WHERE MaThongBao = ?", (id,)).fetchone()
    if tb:
        new_pin = 0 if tb['IsGhim'] else 1
        if new_pin == 1:
            db.execute("UPDATE THONG_BAO SET IsGhim = 0 WHERE LoaiThongBao = 'ADMIN'")
        db.execute("UPDATE THONG_BAO SET IsGhim = ? WHERE MaThongBao = ?", (new_pin, id))
        db.commit()
        flash("Đã cập nhật trạng thái ghim thông báo!", "success")
    db.close()
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/admin/thong-bao/unpin/<int:id>', methods=['POST'])
@role_required([1])
def admin_thong_bao_unpin(id):
    db = get_db()
    db.execute("UPDATE THONG_BAO SET IsGhim = 0 WHERE MaThongBao = ?", (id,))
    db.commit()
    db.close()
    flash("Đã bỏ ghim thông báo!", "info")
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/admin/thong-bao/clear-all', methods=['POST'])
@role_required([1])
def admin_thong_bao_clear_all():
    db = get_db()
    db.execute("DELETE FROM THONG_BAO")
    db.commit()
    db.close()
    flash("Đã xóa sạch toàn bộ thông báo cũ trong Bảng tin!", "success")
    return redirect(request.referrer or url_for('admin_dashboard'))

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
@role_required([1])
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
            'SoGiaHan': 0,
            'HoaHongGiaHan': 0.0,
            'LuongCung': float(s['LuongCung'] if s['LuongCung'] else 0.0),
            'TongLuong': 0.0,
            'DatKPI': False
        }
        
    for b in bookings:
        sale_id = b['MaTaiKhoanChot']
        if sale_id in salary_summary_dict:
            salary_summary_dict[sale_id]['SoPhongChot'] += 1
            salary_summary_dict[sale_id]['HoaHong'] += float(b['HoaHongTaiSan'] or 0.0)

    # Bổ sung hoa hồng từ các lượt gia hạn hợp đồng thành công (+60k/HĐ)
    renewals_data = db.execute(
        """
        SELECT MaTaiKhoanSale, COUNT(*) as SoGiaHan, SUM(HoaHongGiaHan) as TongHoaHongGiaHan
        FROM GIA_HAN_HOP_DONG
        WHERE NgayGiaHan >= ? AND NgayGiaHan < ?
        GROUP BY MaTaiKhoanSale
        """, (start_date, end_date)
    ).fetchall()
    for r in renewals_data:
        s_id = r['MaTaiKhoanSale']
        if s_id in salary_summary_dict:
            salary_summary_dict[s_id]['SoGiaHan'] = r['SoGiaHan']
            salary_summary_dict[s_id]['HoaHongGiaHan'] = float(r['TongHoaHongGiaHan'] or 0.0)
            salary_summary_dict[s_id]['HoaHong'] += float(r['TongHoaHongGiaHan'] or 0.0)
        
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
        # Nếu là Sale, chỉ cho phép xem thông tin lương/KPI của chính mình
        salary_summary = [current_sale_stat]
    
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
        han_thanh_toan = request.form.get('han_thanh_toan')
        han_thanh_toan = int(han_thanh_toan) if han_thanh_toan else (room['HanThanhToan'] if room['HanThanhToan'] is not None else 15)
        
        try:
            room_old = dict(room)
            db.execute(
                """
                UPDATE TAI_SAN 
                SET DiaChi = ?, SoPhong = ?, Tang = ?, GiaThue = ?, GhiChu = ?, ThietBi = ?, 
                    NhanVienChotCu = ?, NgayTrong = ?, HoaHong = ?, HanThanhToan = ?
                WHERE MaTaiSan = ?
                """,
                (dia_chi, so_phong, tang, gia_thue, ghi_chu, thiet_bi, nv_chot_cu, ngay_trong, hoa_hong, han_thanh_toan, room_id)
            )
            room_new = db.execute("SELECT * FROM TAI_SAN WHERE MaTaiSan = ?", (room_id,)).fetchone()
            log_audit(db, "Cập nhật Thông Tin Phòng", "TAI_SAN", du_lieu_cu=room_old, du_lieu_moi=room_new, ghi_chu=f"Cập nhật phòng {so_phong} - {dia_chi} (Ca thu mùng {han_thanh_toan})")
            db.commit()
            flash("Cập nhật thông tin phòng thành công!", "success")
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
        room_old = db.execute("SELECT * FROM TAI_SAN WHERE MaTaiSan = ?", (room_id,)).fetchone()
        # Xóa phòng
        db.execute("DELETE FROM TAI_SAN WHERE MaTaiSan = ?", (room_id,))
        log_audit(db, "Xóa Tài Sản", "TAI_SAN", du_lieu_cu=room_old, ghi_chu=f"Xóa phòng ID {room_id}")
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
            
            new_data = {
                'MaChotKhach': deal_id, 'TenKhach': ten_khach, 'SoDienThoaiKhach': sdt_khach,
                'CCCD': cccd, 'TienCoc': tien_coc, 'GiaThue': gia_thue, 'NgayChot': ngay_chot,
                'NgayDuKienVaoO': ngay_vao, 'SoDienDauVao': dien_dau_vao, 'TrangThai': trang_thai, 'GhiChu': ghi_chu
            }
            log_audit(db, 'Sửa Phiếu Cọc', 'THONG_TIN_CHOT_KHACH', du_lieu_cu=deal, du_lieu_moi=new_data, ghi_chu=f"Quản trị/Quản lý sửa phiếu cọc #{deal_id}")
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
        
        # 4. Tự động phát thông báo tức thì lên toàn hệ thống
        room = db.execute("SELECT * FROM TAI_SAN WHERE MaTaiSan = ?", (deal['MaTaiSan'],)).fetchone()
        if room:
            msg_thong_bao = f"Phòng {room['SoPhong']} {room['DiaChi']} trống khách bỏ cọc"
            db.execute("INSERT INTO THONG_BAO (NoiDung, TrangThai, LoaiThongBao, IsGhim) VALUES (?, 1, 'TRONG', 0)", (msg_thong_bao,))
            
        log_audit(db, 'Hủy Phiếu Cọc', 'THONG_TIN_CHOT_KHACH', du_lieu_cu=deal, ghi_chu=f"Quản trị/Quản lý hủy bỏ cọc / trả phòng về Trống cho phiếu #{deal_id}")
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
               COALESCE(hd.GiaThue, tk.GiaThue, ts.GiaThue) as GiaThueThucTe,
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
    
    # Lấy danh sách tất cả các phòng có hóa đơn nợ trong 1 query duy nhất (giải quyết triệt để N+1)
    overdue_rows = db.execute(
        """
        SELECT DISTINCT h.MaTaiSan 
        FROM HOA_DON hd 
        JOIN HOP_DONG h ON hd.MaHopDong = h.MaHopDong 
        WHERE h.TrangThai IN ('Đang kích hoạt', 'Giữ phòng') AND hd.TrangThaiThanhToan = 'Chưa thanh toán'
        """
    ).fetchall()
    overdue_room_ids = {row['MaTaiSan'] for row in overdue_rows}

    # Nhóm theo ca thu (HanThanhToan) -> Địa chỉ nhà (DiaChi) -> các phòng
    shifts = {}
    for r in rooms_list:
        day = r['HanThanhToan'] if r['HanThanhToan'] is not None else 15
        if day not in shifts:
            shifts[day] = {}
        
        address = r['DiaChi']
        if address not in shifts[day]:
            shifts[day][address] = {
                'rooms': [],
                'has_overdue': False
            }
            
        r_dict = dict(r)
        r_dict['HasOverdue'] = r['MaTaiSan'] in overdue_room_ids
        
        if r_dict['HasOverdue']:
            shifts[day][address]['has_overdue'] = True
            
        shifts[day][address]['rooms'].append(r_dict)
        
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
    raw_contracts = db.execute(
        """
        SELECT hd.*, ts.SoPhong, ts.DiaChi, ts.KhuVuc, ts.MaTaiSan, kt.HoTen as TenKhach, kt.SoDienThoai
        FROM HOP_DONG hd
        JOIN TAI_SAN ts ON hd.MaTaiSan = ts.MaTaiSan
        JOIN KHACH_THUE kt ON hd.MaKhachThue = kt.MaKhachThue
        ORDER BY hd.MaHopDong DESC
        """
    ).fetchall()
    db.close()
    
    today = date.today()
    contracts = []
    for c in raw_contracts:
        c_dict = dict(c)
        try:
            start_d = datetime.strptime(c['NgayBatDau'], '%Y-%m-%d').date()
            end_d = datetime.strptime(c['NgayKetThuc'], '%Y-%m-%d').date()
            days_from_start = (today - start_d).days
            days_to_end = (end_d - today).days
            c_dict['IsNew'] = (0 <= days_from_start <= 30)
            c_dict['IsExpired'] = (days_to_end < 0)
            c_dict['IsExpiringSoon'] = (0 <= days_to_end <= 30)
            c_dict['DaysRemaining'] = days_to_end
        except Exception:
            c_dict['IsNew'] = False
            c_dict['IsExpired'] = False
            c_dict['IsExpiringSoon'] = False
            c_dict['DaysRemaining'] = 999
        contracts.append(c_dict)
        
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
    contract = db.execute(
        """
        SELECT hd.*, ts.SoPhong, ts.DiaChi, ts.KhuVuc, ts.GiaThue as GiaThuePhong, kt.HoTen as TenKhach, kt.SoDienThoai
        FROM HOP_DONG hd
        JOIN TAI_SAN ts ON hd.MaTaiSan = ts.MaTaiSan
        LEFT JOIN KHACH_THUE kt ON hd.MaKhachThue = kt.MaKhachThue
        WHERE hd.MaHopDong = ?
        """, (contract_id,)
    ).fetchone()
    
    if not contract or contract['TrangThai'] != 'Đang kích hoạt':
        db.close()
        flash("Hợp đồng không tồn tại hoặc đã được thanh lý!", "error")
        return redirect(url_for('shared_contracts'))
        
    try:
        today = date.today()
        # 1. Cập nhật trạng thái hợp đồng thành Đã kết thúc
        db.execute(
            "UPDATE HOP_DONG SET TrangThai = 'Đã kết thúc' WHERE MaHopDong = ?", (contract_id,)
        )
        
        # 2. Cập nhật trạng thái phòng thành Trống
        db.execute(
            "UPDATE TAI_SAN SET TrangThai = 'Trong' WHERE MaTaiSan = ?", (contract['MaTaiSan'],)
        )
        
        # 3. Tính đúng số tiền phòng để hoàn cọc (1 cọc 1)
        gia_phong = float(contract['GiaThue'] if contract['GiaThue'] else (contract['GiaThuePhong'] or 0.0))
        if gia_phong <= 0 and contract['TienCoc']:
            gia_phong = float(contract['TienCoc'])
            
        so_tien_hoan_coc = gia_phong * 1000 if 0 < gia_phong < 100000 else gia_phong
        so_tien_vnd = round(so_tien_hoan_coc)
        
        ten_chi_phi = f"Hoàn cọc cho phòng {contract['SoPhong']} ({contract['DiaChi']})"
        ghi_chu_chi = ""
        ma_tai_khoan = session.get('user_id')
        
        # Thêm bản ghi chi phí hoàn cọc
        db.execute(
            """
            INSERT INTO CHI_PHI (Thang, Nam, TenChiPhi, SoTien, NgayNhap, MaTaiKhoanNhap, GhiChu)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (today.month, today.year, ten_chi_phi, so_tien_vnd, today.isoformat(), ma_tai_khoan, ghi_chu_chi)
        )
        
        # 4. Ghi Audit Log cho Thanh lý hợp đồng
        log_audit(
            db,
            'Thanh Lý Hợp Đồng',
            'HOP_DONG',
            du_lieu_cu={
                'MaHopDong': contract_id,
                'SoPhong': contract['SoPhong'],
                'TenKhach': contract['TenKhach'],
                'TrangThai': contract['TrangThai'],
                'NgayBatDau': contract['NgayBatDau'],
                'NgayKetThuc': contract['NgayKetThuc'],
                'GiaThue': contract['GiaThue']
            },
            du_lieu_moi={
                'MaHopDong': contract_id,
                'TrangThai': 'Đã kết thúc',
                'TrangThaiPhong': 'Trong',
                'TienHoanCoc': so_tien_vnd,
                'TenChiPhi': ten_chi_phi
            },
            ghi_chu=f"Thanh lý HĐ #{contract_id:03d} phòng {contract['SoPhong']} ({contract['DiaChi']}). Tự động tạo khoản chi hoàn cọc {so_tien_vnd:,.0f} đ cho khách {contract['TenKhach'] or ''}."
        )
        
        db.commit()
        flash(f"Thanh lý hợp đồng thành công. Phòng {contract['SoPhong']} đã về Trống và hệ thống đã tự động ghi nhận chi phí hoàn cọc {so_tien_vnd:,.0f} đ!", "success")
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
        
        # Tự động phát thông báo tức thì lên toàn hệ thống khi bỏ cọc
        room = db.execute("SELECT * FROM TAI_SAN WHERE MaTaiSan = ?", (contract['MaTaiSan'],)).fetchone()
        if room:
            msg_thong_bao = f"Phòng {room['SoPhong']} {room['DiaChi']} trống khách bỏ cọc"
            db.execute("INSERT INTO THONG_BAO (NoiDung, TrangThai, LoaiThongBao, IsGhim) VALUES (?, 1, 'TRONG', 0)", (msg_thong_bao,))
            
        log_audit(db, 'Hủy Phiếu Cọc', 'HOP_DONG', du_lieu_cu=contract, ghi_chu=f"Quản trị/Quản lý ghi nhận bỏ cọc hợp đồng #{contract_id} phòng {room['SoPhong'] if room else ''}")
        
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
            invoice_new = db.execute("SELECT * FROM HOA_DON WHERE MaHoaDon = ?", (invoice_id,)).fetchone()
            log_audit(db, "Sửa Hóa Đơn", "HOA_DON", du_lieu_cu=invoice, du_lieu_moi=invoice_new, ghi_chu=f"Sửa hóa đơn ID {invoice_id}")
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
