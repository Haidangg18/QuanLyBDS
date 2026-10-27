import json
from datetime import datetime, date, timedelta

def normalize_vnd(val):
    """Chuẩn hóa giá tiền về đơn vị VNĐ đầy đủ."""
    if val is None or val == '':
        return 0.0
    try:
        v = float(val)
        if 0 < abs(v) < 100000:
            return v * 1000.0
        return v
    except (ValueError, TypeError):
        return 0.0

def format_vnd_str(val):
    """Format tiền VNĐ dễ đọc cho con người."""
    v = normalize_vnd(val)
    return f"{v:,.0f}".replace(',', '.') + " đ"

def evaluate_risk(db, user_id, role_id, action, table_name, old_data=None, new_data=None, note=None, ip=None):
    """
    Động cơ phân tích rủi ro & phát hiện bất thường dựa trên hành vi người dùng (20 Quy tắc: 17 cơ bản + 3 nâng cao).
    Trả về dict: {
        'score': int,
        'level': str ('An toàn', 'Cần lưu ý', 'Báo động đỏ'),
        'reasons': list of dict,
        'recommendation': str
    }
    """
    # Giám Đốc (Admin - role_id == 1 hoặc user_id == 1) có thẩm quyền cao nhất, mọi hành động là quyết định quản trị/phê duyệt hợp lệ
    if role_id == 1 or user_id == 1:
        return {
            'score': 0,
            'level': 'An toàn',
            'rules': [],
            'recommendation': 'Thao tác nghiệp vụ quản trị của Giám Đốc (Admin).'
        }

    old = old_data if isinstance(old_data, dict) else {}
    new = new_data if isinstance(new_data, dict) else {}
    note_str = str(note or '')
    action_str = str(action or '')
    table_str = str(table_name or '').upper()

    now = datetime.now()
    hour = now.hour
    is_night_time = hour in [23, 0, 1, 2, 3, 4]

    triggered_rules = []
    recommendations = []

    # =========================================================================
    # a) NHÓM BẤT THƯỜNG TÀI CHÍNH & BIỂU GIÁ (FIN_01 -> FIN_05)
    # =========================================================================

    # FIN_01: Sửa / Xóa hóa đơn đã thanh toán (100 điểm - Cực kỳ nguy hiểm)
    if table_str == 'HOA_DON':
        old_status = old.get('TrangThaiThanhToan')
        new_status = new.get('TrangThaiThanhToan')
        old_total = normalize_vnd(old.get('TongTien'))
        new_total = normalize_vnd(new.get('TongTien'))

        if 'Xóa' in action_str and old_status == 'Đã thanh toán':
            triggered_rules.append({
                'code': 'FIN_01',
                'name': 'Xóa hóa đơn đã thu tiền của khách',
                'desc': f"Hóa đơn #{old.get('MaHoaDon', '')} đã báo 'Đã thanh toán' ({format_vnd_str(old_total)}) bị xóa khỏi hệ thống.",
                'score': 100
            })
            recommendations.append("Kiểm tra lại sao kê ngân hàng và liên hệ khách thuê để xác minh số tiền thực tế đã thu trước khi xóa hóa đơn!")

        elif ('Sửa' in action_str or 'Cập nhật' in action_str) and old_status == 'Đã thanh toán':
            if new_status and new_status != 'Đã thanh toán':
                triggered_rules.append({
                    'code': 'FIN_01',
                    'name': 'Chuyển hóa đơn đã thu tiền thành chưa thanh toán',
                    'desc': f"Hóa đơn #{old.get('MaHoaDon', '')} từ trạng thái 'Đã thanh toán' bị đổi thành '{new_status}'.",
                    'score': 100
                })
                recommendations.append("Xác minh lý do hủy trạng thái thanh toán của hóa đơn, đề phòng thu tiền mặt nhưng báo nợ ảo!")
            elif new_total < old_total:
                giam = old_total - new_total
                triggered_rules.append({
                    'code': 'FIN_01',
                    'name': 'Sửa giảm số tiền trên hóa đơn đã thu tiền',
                    'desc': f"Hóa đơn #{old.get('MaHoaDon', '')} đã thanh toán bị giảm {format_vnd_str(giam)} (Từ {format_vnd_str(old_total)} xuống {format_vnd_str(new_total)}).",
                    'score': 100
                })
                recommendations.append(f"Kiểm tra lý do giảm {format_vnd_str(giam)} tiền phòng/dịch vụ, đối chiếu phiếu thu thực tế đã giao cho khách!")

    # FIN_02: Chốt giá thuê giảm sâu > 15% (60 điểm - Đáng ngờ)
    # FIN_02b (NÂNG CAO): Phá giá bậc thang 7%-15% (40 điểm - Cần lưu ý)
    #   → Lách ngưỡng 15% bằng cách giảm 14.9%? Quy tắc này bắt luôn!
    if table_str in ('THONG_TIN_CHOT_KHACH', 'HOP_DONG') and ('Chốt' in action_str or 'Sửa' in action_str or 'Thêm' in action_str):
        room_id = new.get('MaTaiSan') or old.get('MaTaiSan')
        new_rent = normalize_vnd(new.get('GiaThue'))
        if room_id and new_rent > 0:
            try:
                room_row = db.execute("SELECT GiaThue, SoPhong, DiaChi FROM TAI_SAN WHERE MaTaiSan = ?", (room_id,)).fetchone()
                if room_row and room_row['GiaThue']:
                    base_rent = normalize_vnd(room_row['GiaThue'])
                    if base_rent > 0:
                        pct = ((base_rent - new_rent) / base_rent) * 100
                        if new_rent < (base_rent * 0.85):  # Giảm > 15% → Báo đỏ
                            triggered_rules.append({
                                'code': 'FIN_02',
                                'name': 'Chốt giá thuê giảm sâu bất thường (> 15%)',
                                'desc': f"Giá chốt {format_vnd_str(new_rent)} thấp hơn {pct:.1f}% so với giá niêm yết {format_vnd_str(base_rent)} của phòng {room_row['SoPhong']}.",
                                'score': 60
                            })
                            recommendations.append("Xác minh chính sách ưu đãi hoặc văn bản phê duyệt giảm giá phòng từ Admin!")
                        elif new_rent < (base_rent * 0.93):  # Giảm 7%-15% → Cảnh báo bậc thang
                            triggered_rules.append({
                                'code': 'FIN_02b',
                                'name': 'Chốt giá thuê giảm vừa phải nhưng đáng ngờ (7%-15%)',
                                'desc': f"Giá chốt {format_vnd_str(new_rent)} thấp hơn {pct:.1f}% so với giá niêm yết {format_vnd_str(base_rent)} của phòng {room_row['SoPhong']} — nằm trong vùng xám, cần xác minh có văn bản phê duyệt.",
                                'score': 40
                            })
                            recommendations.append(f"Giá chốt giảm {pct:.1f}% so với niêm yết — yêu cầu nhân viên cung cấp lý do hoặc phê duyệt bằng văn bản từ quản lý cấp trên!")
            except Exception:
                pass

    # FIN_03: Tiền cọc giữ chỗ quá thấp < 500.000đ (50 điểm - Đáng chú ý)
    # FIN_03b (NÂNG CAO): Tỷ lệ cọc / giá thuê < 30% (50 điểm)
    #   → Ngưỡng tỷ lệ thích nghi theo giá phòng, không bị lách bằng con số cố định!
    #   Ví dụ: Phòng 5tr → cọc tối thiểu 1.5tr; Phòng 8tr → cọc tối thiểu 2.4tr
    if table_str == 'THONG_TIN_CHOT_KHACH' and ('Chốt' in action_str or 'Thêm' in action_str):
        deposit = normalize_vnd(new.get('TienCoc'))
        rent = normalize_vnd(new.get('GiaThue'))
        if 0 < deposit < 500000 and rent >= 2000000:
            triggered_rules.append({
                'code': 'FIN_03',
                'name': 'Tiền cọc giữ chỗ quá thấp (< 500.000 đ)',
                'desc': f"Chốt phòng với tiền cọc chỉ {format_vnd_str(deposit)} cho phòng giá thuê {format_vnd_str(rent)} (Dấu hiệu cọc ảo để ôm giữ phòng đẹp).",
                'score': 50
            })
            recommendations.append("Xác nhận biên nhận đặt cọc và ấn định thời hạn nộp đủ cọc, tránh để Sale ôm phòng cản trở đồng nghiệp!")
        elif deposit > 0 and rent > 0:
            # FIN_03b: Kiểm tra tỷ lệ cọc/thuê theo %, thích nghi mọi mức giá
            ratio = deposit / rent
            if ratio < 0.30:  # Cọc < 30% giá thuê tháng
                min_deposit = rent * 0.30
                triggered_rules.append({
                    'code': 'FIN_03b',
                    'name': 'Tỷ lệ tiền cọc / giá thuê quá thấp (< 30%)',
                    'desc': f"Cọc {format_vnd_str(deposit)} chỉ bằng {ratio*100:.1f}% giá thuê {format_vnd_str(rent)}/tháng (Tối thiểu nên đặt {format_vnd_str(min_deposit)}). Rủi ro nhân viên cho phép khách cọc tượng trưng để giữ phòng không có thực.",
                    'score': 50
                })
                recommendations.append(f"Yêu cầu khách bổ sung tiền cọc tối thiểu 30% giá thuê (tối thiểu {format_vnd_str(min_deposit)}), kiểm tra xem số tiền thực tế đã thu đủ chưa!")

    # FIN_04: Khai khống chi phí vận hành đột biến > 5.000.000đ (70 điểm - Nghiêm trọng)
    # FIN_04b (NÂNG CAO): Chi phí cộng dồn 30 ngày của 1 nhân viên > 10.000.000đ (70 điểm)
    #   → Phá vỡ chiêu "gom gió thành bão": mỗi phiếu chỉ 4.9tr nhưng 3 phiếu = 14.7tr!
    if table_str == 'CHI_PHI' and ('Thêm' in action_str or 'Tạo' in action_str):
        expense_amount = normalize_vnd(new.get('SoTien'))
        if expense_amount > 5000000:
            triggered_rules.append({
                'code': 'FIN_04',
                'name': 'Khai chi phí vận hành đột biến (> 5.000.000 đ)',
                'desc': f"Tạo khoản chi {format_vnd_str(expense_amount)} ({new.get('TenChiPhi', 'Chi phí')}) vượt ngưỡng chi thông thông thường.",
                'score': 70
            })
            recommendations.append("Yêu cầu Quản lý xuất trình hóa đơn VAT, chứng từ mua bán vật tư và ảnh nghiệm thu thực tế trước khi xuất quỹ!")
        # FIN_04b: Cộng dồn chi phí 30 ngày qua — bắt chiêu micro-slicing
        if user_id and expense_amount > 0:
            try:
                thirty_days_ago = (now - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
                cumulative_row = db.execute(
                    """
                    SELECT SUM(CAST(json_extract(DuLieuMoi, '$.SoTien') AS REAL)) as tong_chi
                    FROM AUDIT_LOG
                    WHERE MaTaiKhoan = ?
                      AND BangBiTacDong = 'CHI_PHI'
                      AND (HanhDong LIKE '%Thêm%' OR HanhDong LIKE '%Tạo%')
                      AND ThoiGian >= ?
                    """, (user_id, thirty_days_ago)
                ).fetchone()
                try:
                    tong_chi_raw = cumulative_row['tong_chi'] if (hasattr(cumulative_row, '__getitem__') and not isinstance(cumulative_row, tuple)) else cumulative_row[0]
                except Exception:
                    tong_chi_raw = None
                tong_chi = normalize_vnd(tong_chi_raw) + expense_amount  # Cộng thêm phiếu đang tạo
                if tong_chi > 10000000:  # Tổng 30 ngày vượt 10 triệu
                    triggered_rules.append({
                        'code': 'FIN_04b',
                        'name': 'Chi phí cộng dồn 30 ngày vượt ngưỡng (> 10.000.000 đ)',
                        'desc': f"Tổng phiếu chi do nhân viên này tạo trong 30 ngày qua đã đạt {format_vnd_str(tong_chi)} (Bao gồm khoản vừa tạo {format_vnd_str(expense_amount)}). Cảnh báo tích lũy chi phí nhiều phiếu nhỏ để vượt qua kiểm soát.",
                        'score': 70
                    })
                    recommendations.append(f"Rà soát toàn bộ phiếu chi 30 ngày gần nhất của nhân viên này (Tổng {format_vnd_str(tong_chi)}), yêu cầu chứng từ đầy đủ cho từng khoản chi!")
            except Exception:
                pass

    # FIN_05: Xóa khoản chi phí lớn >= 2.000.000đ (80 điểm - Nghiêm trọng)
    if table_str == 'CHI_PHI' and 'Xóa' in action_str:
        expense_amount = normalize_vnd(old.get('SoTien'))
        if expense_amount >= 2000000:
            triggered_rules.append({
                'code': 'FIN_05',
                'name': 'Xóa khoản chi phí lớn (>= 2.000.000 đ)',
                'desc': f"Xóa phiếu chi {format_vnd_str(expense_amount)} ({old.get('TenChiPhi', 'Chi phí')}) khỏi sổ sách kế toán.",
                'score': 80
            })
            recommendations.append("Đối chiếu sổ quỹ tiền mặt xem khoản tiền này đã xuất quỹ hay chưa để phòng ngừa tẩu tán chứng từ!")

    # =========================================================================
    # b) NHÓM BẤT THƯỜNG QUY TRÌNH & VÒNG ĐỜI VẬN HÀNH (OPS_01 -> OPS_03)
    # =========================================================================

    # OPS_01: Chỉ số điện tiêu thụ âm hoặc > 600 kWh/tháng (80 điểm - Nghiêm trọng)
    if table_str in ('DIEN_NUOC', 'HOA_DON'):
        elec_usage = None
        if 'SoDien' in new:
            try: elec_usage = float(new['SoDien'])
            except: pass
        elif 'ChiSoMoi' in new and 'ChiSoCu' in new:
            try: elec_usage = float(new['ChiSoMoi']) - float(new['ChiSoCu'])
            except: pass

        if elec_usage is not None:
            if elec_usage < 0:
                triggered_rules.append({
                    'code': 'OPS_01',
                    'name': 'Chỉ số điện tiêu thụ âm bất thường',
                    'desc': f"Số điện ghi nhận là {elec_usage:.0f} kWh (Chỉ số mới nhỏ hơn chỉ số cũ, ghi nhầm hoặc gian lận).",
                    'score': 80
                })
                recommendations.append("Yêu cầu chụp ảnh đồng hồ điện thực tế của phòng để đối soát và đính chính chỉ số!")
            elif elec_usage >= 600:
                triggered_rules.append({
                    'code': 'OPS_01',
                    'name': 'Chỉ số điện tiêu thụ đột biến (>= 600 kWh/tháng)',
                    'desc': f"Số điện tiêu thụ lên đến {elec_usage:.0f} kWh/tháng (Cao gấp nhiều lần mức sinh hoạt trung bình).",
                    'score': 80
                })
                recommendations.append("Kiểm tra công tơ điện và hệ thống dây dẫn xem có hiện tượng rò rỉ điện hoặc câu trộm điện không!")

    # OPS_02: Chuyển phòng đang thuê về Trống đột ngột (90 điểm - Khẩn cấp)
    if table_str == 'TAI_SAN' and ('Sửa' in action_str or 'Cập nhật' in action_str):
        old_status = old.get('TrangThai')
        new_status = new.get('TrangThai')
        if old_status == 'DangThue' and new_status == 'Trong' and 'Thanh lý' not in note_str:
            triggered_rules.append({
                'code': 'OPS_02',
                'name': 'Chuyển phòng đang thuê về Trống đột ngột (Ghost Tenancy)',
                'desc': f"Phòng {new.get('SoPhong', '')} ({new.get('DiaChi', '')}) bị đổi trạng thái từ 'Đang thuê' sang 'Trống' mà không thông qua biên bản thanh lý hợp đồng.",
                'score': 90
            })
            recommendations.append("Khẩn cấp: Kiểm tra thực địa phòng xem khách cũ đã trả phòng hay Quản lý đang cho thuê chui bỏ túi tiền mặt!")

    # OPS_03: Sửa lùi ngày bắt đầu Hợp đồng / Gia hạn ngày thanh toán (60 điểm - Đáng ngờ)
    if table_str == 'HOP_DONG' and ('Sửa' in action_str or 'Cập nhật' in action_str):
        old_start = old.get('NgayBatDau')
        new_start = new.get('NgayBatDau')
        if old_start and new_start and new_start < old_start:
            triggered_rules.append({
                'code': 'OPS_03',
                'name': 'Sửa lùi ngày bắt đầu hợp đồng thuê',
                'desc': f"Ngày bắt đầu hợp đồng bị lùi từ {old_start} về {new_start} (Dấu hiệu che giấu việc cho khách vào ở trước đó để ăn tiền ngày lẻ).",
                'score': 60
            })
            recommendations.append("Đối chiếu ngày khách dọn vào thực tế và kiểm tra tiền nhà ngày lẻ đã nộp về công ty chưa!")

    # =========================================================================
    # c) NHÓM BẤT THƯỜNG THỜI GIAN & MÔI TRƯỜNG THAO TÁC (TIME_01 -> TIME_03)
    # =========================================================================

    # TIME_01: Thao tác dữ liệu tài chính lúc đêm khuya 23:00 - 05:00 sáng (60 điểm - Đáng ngờ)
    if is_night_time and table_str in ('HOA_DON', 'CHI_PHI', 'THONG_TIN_CHOT_KHACH', 'HOP_DONG') and ('Sửa' in action_str or 'Xóa' in action_str or 'Hủy' in action_str):
        triggered_rules.append({
            'code': 'TIME_01',
            'name': 'Thao tác dữ liệu tài chính nhạy cảm vào ban đêm',
            'desc': f"Thực hiện '{action_str}' trên bảng {table_str} lúc {now.strftime('%H:%M:%S')} (Khung giờ vắng người giám sát).",
            'score': 60
        })
        recommendations.append("Kiểm tra lý do nhân viên chỉnh sửa số liệu kế toán/hợp đồng ngoài giờ hành chính!")

    # =========================================================================
    # d) NHÓM BẤT THƯỜNG TẦN SUẤT & HÀNH VI DỒN DẬP (VEL_01 -> VEL_02)
    # =========================================================================

    # VEL_01: Xóa dữ liệu dồn dập >= 3 bản ghi trong 60 giây (120 điểm - Khẩn cấp)
    if 'Xóa' in action_str and user_id:
        try:
            one_min_ago = (now - timedelta(seconds=60)).strftime('%Y-%m-%d %H:%M:%S')
            recent_deletes = db.execute(
                "SELECT COUNT(*) as cnt FROM AUDIT_LOG WHERE MaTaiKhoan = ? AND HanhDong LIKE '%Xóa%' AND ThoiGian >= ?",
                (user_id, one_min_ago)
            ).fetchone()
            if recent_deletes and recent_deletes['cnt'] >= 2: # Đây là lần xóa thứ 3 trở lên
                triggered_rules.append({
                    'code': 'VEL_01',
                    'name': 'Xóa dữ liệu dồn dập (>= 3 bản ghi / 60 giây)',
                    'desc': f"Tài khoản thực hiện xóa liên tiếp {recent_deletes['cnt'] + 1} bản ghi dữ liệu trong vòng 60 giây.",
                    'score': 120
                })
                recommendations.append("Báo động: Tạm khóa quyền thao tác của tài khoản và kiểm tra xem có dấu hiệu tẩu tán chứng từ hoặc phá hoại dữ liệu!")
        except Exception:
            pass

    # VEL_02: Sale spam chốt giữ chỗ >= 4 phòng trong 10 phút (70 điểm - Nghiêm trọng)
    if ('Chốt' in action_str or table_str == 'THONG_TIN_CHOT_KHACH') and user_id:
        try:
            ten_min_ago = (now - timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M:%S')
            recent_bookings = db.execute(
                "SELECT COUNT(*) as cnt FROM AUDIT_LOG WHERE MaTaiKhoan = ? AND (HanhDong LIKE '%Chốt Cọc%' OR HanhDong LIKE '%Tạo Phiếu%') AND ThoiGian >= ?",
                (user_id, ten_min_ago)
            ).fetchone()
            if recent_bookings and recent_bookings['cnt'] >= 3: # Lần chốt thứ 4 trở lên
                triggered_rules.append({
                    'code': 'VEL_02',
                    'name': 'Spam chốt giữ chỗ nhiều phòng (>= 4 phòng / 10 phút)',
                    'desc': f"Tài khoản liên tục tạo {recent_bookings['cnt'] + 1} phiếu chốt cọc trong 10 phút (Dấu hiệu chiếm giữ bảng hàng tạm thời).",
                    'score': 70
                })
                recommendations.append("Xác minh danh sách khách hàng và sao kê tiền cọc từng phòng để chống giữ phòng dạo!")
        except Exception:
            pass

    # =========================================================================
    # e) NHÓM CHUYÊN BIỆT GIÁM SÁT CHU TRÌNH HỦY CỌC (CANC_01 -> CANC_04)
    # =========================================================================

    # CANC_01a: Bắt vết ngay khi nhân viên Sửa giảm số tiền cọc
    if table_str in ('THONG_TIN_CHOT_KHACH', 'HOP_DONG') and ('Sửa' in action_str or 'Cập nhật' in action_str):
        old_dep = normalize_vnd(old.get('TienCoc'))
        new_dep = normalize_vnd(new.get('TienCoc'))
        if old_dep > 0 and new_dep < old_dep:
            diff_val = old_dep - new_dep
            ten_khach = new.get('TenKhach') or old.get('TenKhach') or 'khách thuê'
            triggered_rules.append({
                'code': 'CANC_01',
                'name': 'Sửa giảm số tiền cọc của khách thuê',
                'desc': f"Tiền cọc của {ten_khach} bị sửa giảm từ {format_vnd_str(old_dep)} xuống {format_vnd_str(new_dep)} (Chênh lệch {format_vnd_str(diff_val)}). Dấu hiệu chiếm dụng tiền cọc trước khi hủy cọc hoặc trích bớt tiền nộp về công ty.",
                'score': 100
            })
            recommendations.append(f"Cảnh báo chiếm dụng tiền cọc: Tiền cọc vừa bị sửa giảm {format_vnd_str(diff_val)}. Đề nghị gọi trực tiếp khách {ten_khach} để đối soát số tiền thực nộp!")

    if 'Hủy' in action_str and ('Cọc' in action_str or 'chot' in note_str.lower() or table_str in ('THONG_TIN_CHOT_KHACH', 'HOP_DONG')):
        # CANC_01: Sửa giảm tiền cọc trong vòng 24h trước khi hủy (100 điểm - Cực kỳ nguy hiểm)
        deal_id = old.get('MaChotKhach') or new.get('MaChotKhach')
        room_id = old.get('MaTaiSan') or new.get('MaTaiSan')
        if deal_id or room_id:
            try:
                twenty_four_hours_ago = (now - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
                # Tìm trong Audit Log xem có lần sửa phiếu chốt này trong 24h qua không
                contract_id = old.get('MaHopDong') or new.get('MaHopDong')
                edit_logs = db.execute(
                    """
                    SELECT DuLieuCu, DuLieuMoi FROM AUDIT_LOG 
                    WHERE (HanhDong LIKE '%Sửa%' OR HanhDong LIKE '%Cập nhật%') 
                      AND (BangBiTacDong = 'THONG_TIN_CHOT_KHACH' OR BangBiTacDong = 'HOP_DONG')
                      AND ThoiGian >= ?
                    ORDER BY MaLog DESC LIMIT 20
                    """, (twenty_four_hours_ago,)
                ).fetchall()

                deposit_reduced = False
                old_dep_val = 0
                new_dep_val = 0
                for el in edit_logs:
                    try:
                        try:
                            raw_old = el['DuLieuCu']
                            raw_new = el['DuLieuMoi']
                        except (TypeError, KeyError, IndexError):
                            raw_old = el[0]
                            raw_new = el[1]
                        el_old = json.loads(raw_old or '{}')
                        el_new = json.loads(raw_new or '{}')
                        match_deal = (deal_id and (el_old.get('MaChotKhach') == deal_id or el_new.get('MaChotKhach') == deal_id))
                        match_contract = (contract_id and (el_old.get('MaHopDong') == contract_id or el_new.get('MaHopDong') == contract_id))
                        match_room = (room_id and (el_old.get('MaTaiSan') == room_id or el_new.get('MaTaiSan') == room_id))
                        if match_deal or match_contract or match_room:
                            dep_before = normalize_vnd(el_old.get('TienCoc'))
                            dep_after = normalize_vnd(el_new.get('TienCoc'))
                            if dep_before > dep_after:
                                deposit_reduced = True
                                old_dep_val = dep_before
                                new_dep_val = dep_after
                                break
                    except Exception:
                        continue

                if deposit_reduced:
                    diff_val = old_dep_val - new_dep_val
                    triggered_rules.append({
                        'code': 'CANC_01',
                        'name': 'Sửa giảm tiền cọc trong vòng 24h trước khi hủy cọc',
                        'desc': f"Tiền cọc bị sửa giảm từ {format_vnd_str(old_dep_val)} xuống {format_vnd_str(new_dep_val)} (Chênh lệch {format_vnd_str(diff_val)}) ngay trước khi thực hiện hủy cọc.",
                        'score': 100
                    })
                    recommendations.append(f"Cảnh báo chiếm dụng tiền phạt: Khách đã cọc {format_vnd_str(old_dep_val)} nhưng nhân viên vừa sửa thành {format_vnd_str(new_dep_val)}. Đề nghị gọi trực tiếp khách thuê xác minh số tiền đã nộp trước khi duyệt!")
            except Exception:
                pass

        # CANC_02: Tỷ lệ hủy cọc của Sale cao đột biến >= 3 lần/tháng hoặc > 30% (70 điểm)
        if user_id:
            try:
                first_of_month = f"{now.year}-{now.month:02d}-01"
                deal_stats = db.execute(
                    """
                    SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN TrangThai LIKE '%Huy%' OR TrangThai LIKE '%Bỏ%' THEN 1 ELSE 0 END) as cancels
                    FROM THONG_TIN_CHOT_KHACH
                    WHERE MaTaiKhoanChot = ? AND NgayChot >= ?
                    """, (user_id, first_of_month)
                ).fetchone()
                try:
                    total_val = deal_stats['total'] if (hasattr(deal_stats, '__getitem__') and not isinstance(deal_stats, tuple)) else deal_stats[0]
                    cancels_val = deal_stats['cancels'] if (hasattr(deal_stats, '__getitem__') and not isinstance(deal_stats, tuple)) else deal_stats[1]
                except Exception:
                    total_val = 0
                    cancels_val = 0
                if deal_stats and total_val and total_val > 0:
                    cancels = cancels_val or 0
                    total = total_val
                    rate = cancels / total
                    if cancels >= 3 or (total >= 3 and rate >= 0.30):
                        triggered_rules.append({
                            'code': 'CANC_02',
                            'name': 'Tỷ lệ hủy cọc của nhân viên Sale cao bất thường',
                            'desc': f"Nhân viên có {cancels}/{total} lượt hủy cọc trong tháng (Tỷ lệ {rate*100:.1f}%). Dấu hiệu cọc ảo giữ phòng dạo.",
                            'score': 70
                        })
                        recommendations.append("Đánh giá lại chất lượng khách hàng của Sale, kiểm tra xem có hiện tượng tạo khách ảo giữ phòng không!")
            except Exception:
                pass

        # CANC_03: Yêu cầu hủy cọc siêu tốc < 24 giờ kể từ lúc chốt (40 điểm - Cảnh báo)
        ngay_chot_str = old.get('NgayChot') or new.get('NgayChot')
        if ngay_chot_str:
            try:
                ngay_chot_dt = datetime.strptime(ngay_chot_str[:10], '%Y-%m-%d')
                if (now - ngay_chot_dt).total_seconds() < 86400: # < 24 giờ
                    triggered_rules.append({
                        'code': 'CANC_03',
                        'name': 'Yêu cầu hủy cọc siêu tốc (< 24 giờ kể từ lúc chốt)',
                        'desc': f"Phiếu cọc vừa tạo chưa đầy 24 giờ đã báo hủy phòng (Chốt ngày {ngay_chot_str}).",
                        'score': 40
                    })
                    recommendations.append("Xác minh lý do khách đổi ý quá nhanh, tránh tình trạng Sale khóa phòng tạm thời rồi nhả lại!")
            except Exception:
                pass

        # CANC_04: Thao tác hủy cọc vào khung giờ đêm khuya 23:00 - 05:00 sáng (60 điểm)
        if is_night_time:
            triggered_rules.append({
                'code': 'CANC_04',
                'name': 'Thao tác hủy cọc vào ban đêm (23:00 - 05:00 sáng)',
                'desc': f"Thực hiện hủy cọc lúc {now.strftime('%H:%M:%S')} ngoài giờ làm việc.",
                'score': 60
            })
            recommendations.append("Xác minh xem việc hủy phòng có phát sinh giao dịch nhận tiền mặt riêng với khách ngoài giờ không!")

    # =========================================================================
    # TÍNH TOÁN ĐIỂM RỦI RO & PHÂN LOẠI
    # TotalScore = min(500, sum(Scores) * W_Role)
    # =========================================================================
    role_weight = 1.2 if role_id == 2 else 1.0  # Quản lý nắm quyền dữ liệu nhạy cảm phạt x1.2
    raw_score = sum(r['score'] for r in triggered_rules)
    total_score = min(500, int(raw_score * role_weight))

    if total_score >= 80:
        level = "Báo động đỏ"
    elif total_score >= 40:
        level = "Cần lưu ý"
    else:
        level = "An toàn"

    # Hợp nhất khuyến nghị thành 1 chỉ dẫn ngắn gọn, thực tế
    final_rec = " ".join(dict.fromkeys(recommendations)) if recommendations else "Thao tác hợp lệ, tiếp tục theo dõi định kỳ."

    return {
        'score': total_score,
        'level': level,
        'rules': triggered_rules,
        'recommendation': final_rec
    }