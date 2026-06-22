# 📊 Sales Dashboard — SYNERGY ASIA SOLUTION

> **ไฟล์:** `sales_dashboard.html`  
> **เข้าถึงได้จาก:** ปุ่ม "📊 Sales Dashboard" บนหน้า `index.html`  
> **โทนสี:** น้ำเงิน-ฟ้า (Dark Blue theme)

---

## โครงสร้างไฟล์

```
motor_app/
├── templates/
│   ├── index.html              ← หน้าหลัก (แก้ปุ่ม Monthly Sales → Sales Dashboard)
│   └── sales_dashboard.html   ← ✅ ไฟล์ใหม่ — Sales Dashboard แยกอิสระ
└── SALES_DASHBOARD.md          ← เอกสารนี้
```

---

## ฟีเจอร์ทั้งหมด

### 1. อัพโหลด PDF
- ปุ่ม **↑ อัพโหลด PDF** มุมขวาบน รองรับสูงสุด **20 ไฟล์พร้อมกัน**
- รองรับ **drag & drop** ลากมาวางที่ Drop Zone ได้เลย
- อ่านชื่อย่อ Sale จากชื่อไฟล์อัตโนมัติ เช่น `-TW-` → Tippawan, `-NM-` → Naphaphat
- อ่านวันที่จาก pattern `DD-MM-YYYY` ในชื่อไฟล์

### 2. กรองรายเดือน
- ปุ่มเดือนแสดงด้านบนตาราง กดสลับได้เลย
- กด **ทั้งหมด** เพื่อดูภาพรวม

### 3. Stat Cards (สรุปยอด)
- จำนวนใบเสนอราคา
- มูลค่ารวมทั้งหมด (animate count up)
- ยอดแยกรายชื่อ Sale
- นับสถานะ Forecast / Loss / Holding

### 4. Ticker Bar
- แถบวิ่งด้านบน แสดงยอดรวม + รายชื่อ Sale แบบ real-time
- อัพเดตทุกครั้งที่ข้อมูลเปลี่ยน

### 5. กราฟเส้นเปรียบเทียบ (Line Chart)
- แสดงยอดขายแต่ละ Sale Person แยกสีต่อเดือน
- Tooltip แสดงตัวเลขครบเมื่อ hover
- ใช้ Chart.js 4.4.1 — เร็ว ลื่นไหล

### 6. Top 5 Sellers
- Bar row เรียงจากยอดสูงสุด พร้อม medal 🥇🥈🥉
- Hover เพื่อ highlight

### 7. Team Cards
- สรุปยอดรายทีม — จัด Sale เข้าทีมตาม `SALE_INFO`
- แสดง Top Sale ของแต่ละทีม

### 8. ตารางใบเสนอราคา
- เรียงมูลค่าจากมากไปน้อยอัตโนมัติ
- **คลิกราคา** → เปิด Pop-up อ่านรายละเอียด (items, vat, สถานะ)
- ปุ่ม **Forecast / Loss / Holding** กดซ้ำเพื่อยกเลิก
- ช่องพิมพ์ **หมายเหตุ** ท้ายแถว

### 9. Modal รายละเอียด PDF
- แสดง item code, description, qty, unit price, total
- แสดง VAT และราคาสุทธิ
- กด X หรือคลิกนอกกรอบเพื่อปิด

---

## การเพิ่ม Sale Person ใหม่

เปิดไฟล์ `sales_dashboard.html` แล้วแก้ไข object `SALE_INFO`:

```javascript
const SALE_INFO = {
  TW: { name:'Tippawan',   cls:'bs-tw', team:'Team 1', color:'#3B82F6' },
  NM: { name:'Naphaphat',  cls:'bs-nm', team:'Team 2', color:'#10B981' },
  // เพิ่มตรงนี้:
  XX: { name:'ชื่อเต็ม',   cls:'bs-xx', team:'Team 1', color:'#F59E0B' },
};
```

> **ชื่อย่อ (key)** ต้องตรงกับส่วนที่อยู่ระหว่าง `-` ในชื่อไฟล์ PDF  
> เช่น ไฟล์ `QMO26-XX-001-Customer__01-06-2026_.pdf` → key = `XX`

---

## Convention ชื่อไฟล์ PDF

```
{QuotationNo}-{SaleShort}-{CustomerShort}__{DD-MM-YYYY}_.pdf
```

**ตัวอย่าง:**
```
QMO6905-0002-TW-Magna__05-05-2026_.pdf
QMO26-NM-023-TKD_DYNAMIC__18-05-2026_.pdf
```

---

## Dependencies (CDN — ไม่ต้อง install)

| Library | Version | ใช้ทำ |
|---------|---------|-------|
| Chart.js | 4.4.1 | กราฟเส้น |
| SheetJS (xlsx) | 0.20.1 | อ่านไฟล์ Excel (สำรองไว้) |

---

## การ Deploy

1. Copy `sales_dashboard.html` ไปที่ `motor_app/templates/`
2. Copy `index.html` ทับของเดิม
3. Restart Flask — ไม่ต้องแก้ `app.py` เพราะเป็น static HTML
4. เข้าถึงได้ที่ `/sales_dashboard.html`

> ถ้าต้องการให้ Flask serve ไฟล์นี้ผ่าน route ให้เพิ่มใน `app.py`:
> ```python
> @app.route('/sales_dashboard.html')
> def sales_dashboard():
>     return render_template('sales_dashboard.html')
> ```

---

## TODO / พัฒนาต่อ

- [ ] อ่าน PDF จริงด้วย pdf.js เพื่อดึงยอดอัตโนมัติ
- [ ] บันทึกข้อมูลลง Firebase / localStorage ระหว่าง session
- [ ] เพิ่ม Export Excel สรุปยอดรายเดือน
- [ ] เพิ่มกราฟแท่ง (Bar Chart) เปรียบเทียบ Forecast vs Actual
- [ ] รองรับหลาย Product Category (ไม่ใช่แค่ Motor)

---

*Last updated: 2026-06-22 · SYNERGY ASIA SOLUTION*
