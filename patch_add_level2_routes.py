from pathlib import Path

app_path = Path('app.py')
if not app_path.exists():
    raise SystemExit('ไม่พบ app.py: กรุณารันไฟล์นี้ในโฟลเดอร์ root ของโปรเจกต์ sas_qc_gearmotor_app')

text = app_path.read_text(encoding='utf-8')

level2_block = r'''

# ============================================================
# 🎓 SAS Training Level 2
# ============================================================
@app.route('/sas_training_level2.html')
@app.route('/sas-training-level2')
def sas_training_level2():
    return render_template('sas_training_level2.html')

@app.route('/sas_quiz2.html')
@app.route('/sas-quiz2')
def sas_quiz2():
    return render_template('sas_quiz2.html')
'''

if "def sas_training_level2" in text or "/sas_training_level2.html" in text:
    print('พบ route Level 2 อยู่แล้ว ไม่ได้แก้ซ้ำ')
else:
    marker = "if __name__ == '__main__':"
    if marker in text:
        text = text.replace(marker, level2_block + "\n" + marker, 1)
    else:
        text = text.rstrip() + level2_block + "\n"
    app_path.write_text(text, encoding='utf-8')
    print('เพิ่ม route Level 2 ใน app.py เรียบร้อยแล้ว')

print('ทดสอบด้วยคำสั่งนี้:')
print('python -c "from app import app; print([str(r) for r in app.url_map.iter_rules() if \'sas_\' in str(r)])"')
