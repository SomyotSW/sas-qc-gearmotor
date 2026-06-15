from pathlib import Path

APP = Path('app.py')
if not APP.exists():
    raise SystemExit('ไม่พบ app.py — ให้วางไฟล์นี้ไว้ในโฟลเดอร์เดียวกับ app.py แล้วรันใหม่')

text = APP.read_text(encoding='utf-8')
backup = APP.with_suffix('.py.bak_training_level3')
backup.write_text(text, encoding='utf-8')

insert_blocks = []

if "/sas_training_level2.html" not in text or "/sas_quiz2.html" not in text or "/sas_training_level3.html" not in text or "/sas_quiz3.html" not in text:
    insert_blocks.append(r'''

# ============================================================
# 🎓 SAS Training Level 2 / Level 3 Routes
# ============================================================
# หมายเหตุ: ต้องวางไฟล์ sas_training_level2.html, sas_quiz2.html,
# sas_training_level3.html, sas_quiz3.html ไว้ในโฟลเดอร์ templates

@app.route('/sas_training_level2.html')
@app.route('/sas-training-level2')
def sas_training_level2():
    return render_template('sas_training_level2.html')


@app.route('/sas_quiz2.html')
@app.route('/sas-quiz2')
def sas_quiz2():
    return render_template('sas_quiz2.html')


@app.route('/sas_training_level3.html')
@app.route('/sas-training-level3')
def sas_training_level3():
    return render_template('sas_training_level3.html')


@app.route('/sas_quiz3.html')
@app.route('/sas-quiz3')
def sas_quiz3():
    return render_template('sas_quiz3.html')
''')

if "/api/training-quiz/check-level3-prereq" not in text:
    insert_blocks.append(r'''

# ============================================================
# 🏆 SAS Training Quiz Database API
# ใช้ Firebase Realtime Database เป็นฐานกลาง เพื่อเช็คข้ามเครื่อง
# ============================================================
training_quiz_ref = db.reference('/training_quiz')


def _digits_only(v):
    return ''.join(ch for ch in str(v or '') if ch.isdigit())


def _norm_text(v):
    return str(v or '').strip().lower().replace(' ', '')


def _training_level_key(level):
    s = str(level or '').strip().lower()
    if s in ('1', 'level1', 'quiz1', 'l1'):
        return 'level1'
    if s in ('2', 'level2', 'quiz2', 'l2'):
        return 'level2'
    if s in ('3', 'level3', 'quiz3', 'l3'):
        return 'level3'
    return s or 'level1'


def _training_total_score_for(level_key):
    return 40 if level_key == 'level3' else 30


def _training_pass_score_for(level_key):
    if level_key == 'level3':
        return 34
    return 25


def _training_doc_id(name, phone):
    n = _norm_text(name)[:80]
    p = _digits_only(phone)[-10:]
    return f'n_{n}_p_{p}' if (n or p) else 'unknown'


def _training_row_score(row):
    try:
        return int(row.get('score') or row.get('correct') or 0)
    except Exception:
        return 0


def _training_row_passed(level_key, row):
    return bool(row.get('passed')) or _training_row_score(row) >= _training_pass_score_for(level_key)


def _find_training_record(level_key, name='', phone=''):
    name_n = _norm_text(name)
    phone_n = _digits_only(phone)
    phone_last = phone_n[-10:] if phone_n else ''
    rows_obj = training_quiz_ref.child(level_key).get() or {}
    if not isinstance(rows_obj, dict):
        return None

    # 1) match เบอร์โทรก่อน เพราะข้ามเครื่องแม่นสุด
    if phone_last:
        for row in rows_obj.values():
            if not isinstance(row, dict):
                continue
            rp = _digits_only(row.get('phone'))
            if rp and rp[-10:] == phone_last:
                return row

    # 2) match ชื่อแบบตัดช่องว่าง
    if name_n:
        for row in rows_obj.values():
            if not isinstance(row, dict):
                continue
            rn = _norm_text(row.get('name'))
            if rn and rn == name_n:
                return row

    return None


@app.route('/api/training-quiz/save', methods=['POST'])
def training_quiz_save():
    try:
        data = request.get_json(silent=True) or {}
        level_key = _training_level_key(data.get('level') or data.get('quiz_level') or data.get('level_key'))
        name = str(data.get('name') or data.get('fullName') or data.get('fullname') or '').strip()
        pos = str(data.get('pos') or data.get('position') or data.get('team') or '').strip()
        phone = str(data.get('phone') or data.get('tel') or data.get('mobile') or '').strip()
        score = int(data.get('score') or data.get('correct') or 0)
        total = int(data.get('total') or _training_total_score_for(level_key))
        passed = bool(data.get('passed')) or score >= _training_pass_score_for(level_key)
        now = (datetime.datetime.utcnow() + datetime.timedelta(hours=7)).strftime('%Y-%m-%d %H:%M:%S')

        doc_id = _training_doc_id(name, phone)
        old = training_quiz_ref.child(level_key).child(doc_id).get() or {}
        old_score = int(old.get('score') or 0) if isinstance(old, dict) else 0

        payload = {
            'level': level_key,
            'name': name,
            'pos': pos,
            'phone': phone,
            'score': max(score, old_score),
            'total': total,
            'passed': passed or bool(old.get('passed')) if isinstance(old, dict) else passed,
            'updated_at': now,
            'created_at': old.get('created_at') if isinstance(old, dict) and old.get('created_at') else now,
        }
        training_quiz_ref.child(level_key).child(doc_id).set(payload)
        return jsonify({'ok': True, 'record': payload})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/training-quiz/check-level1', methods=['POST'])
def training_quiz_check_level1():
    try:
        data = request.get_json(silent=True) or {}
        row = _find_training_record('level1', data.get('name', ''), data.get('phone', ''))
        passed = bool(row) and _training_row_passed('level1', row)
        return jsonify({'ok': True, 'passed': passed, 'record': row or None})
    except Exception as e:
        return jsonify({'ok': False, 'passed': False, 'error': str(e)}), 500


@app.route('/api/training-quiz/check-level2', methods=['POST'])
def training_quiz_check_level2():
    try:
        data = request.get_json(silent=True) or {}
        row = _find_training_record('level2', data.get('name', ''), data.get('phone', ''))
        passed = bool(row) and _training_row_passed('level2', row)
        return jsonify({'ok': True, 'passed': passed, 'record': row or None})
    except Exception as e:
        return jsonify({'ok': False, 'passed': False, 'error': str(e)}), 500


@app.route('/api/training-quiz/check-level3-prereq', methods=['POST'])
def training_quiz_check_level3_prereq():
    try:
        data = request.get_json(silent=True) or {}
        name = data.get('name', '')
        phone = data.get('phone', '')
        row1 = _find_training_record('level1', name, phone)
        row2 = _find_training_record('level2', name, phone)
        passed1 = bool(row1) and _training_row_passed('level1', row1)
        passed2 = bool(row2) and _training_row_passed('level2', row2)
        return jsonify({
            'ok': True,
            'passed': bool(passed1 and passed2),
            'passed_level1': bool(passed1),
            'passed_level2': bool(passed2),
            'level1': row1 or None,
            'level2': row2 or None,
        })
    except Exception as e:
        return jsonify({'ok': False, 'passed': False, 'error': str(e)}), 500


def _training_overall_rows():
    bucket = {}

    def ident(row):
        return _training_doc_id(row.get('name', ''), row.get('phone', ''))

    for level_key in ('level1', 'level2', 'level3'):
        rows_obj = training_quiz_ref.child(level_key).get() or {}
        if not isinstance(rows_obj, dict):
            continue
        for row in rows_obj.values():
            if not isinstance(row, dict):
                continue
            key = ident(row)
            if not key or key == 'unknown':
                continue
            if key not in bucket:
                bucket[key] = {
                    'name': row.get('name', ''),
                    'pos': row.get('pos', ''),
                    'phone': row.get('phone', ''),
                    'level1': 0,
                    'level2': 0,
                    'level3': 0,
                    'passed_level1': False,
                    'passed_level2': False,
                    'passed_level3': False,
                    'date': '',
                }
            item = bucket[key]
            item['name'] = item.get('name') or row.get('name', '')
            item['pos'] = item.get('pos') or row.get('pos', '')
            item['phone'] = item.get('phone') or row.get('phone', '')
            score = _training_row_score(row)
            item[level_key] = max(int(item.get(level_key) or 0), score)
            item['passed_' + level_key] = bool(item.get('passed_' + level_key)) or _training_row_passed(level_key, row)
            d = row.get('updated_at') or row.get('created_at') or ''
            if d > item.get('date', ''):
                item['date'] = d

    rows = []
    for item in bucket.values():
        item['total_score'] = int(item.get('level1') or 0) + int(item.get('level2') or 0) + int(item.get('level3') or 0)
        item['total'] = item['total_score']
        item['total_possible'] = 100
        rows.append(item)
    rows.sort(key=lambda x: (x.get('total_score', 0), x.get('level3', 0), x.get('date', '')), reverse=True)
    return rows


@app.route('/api/training-quiz/leaderboard/total')
def training_quiz_leaderboard_total():
    try:
        return jsonify({'ok': True, 'level': 'total', 'rows': _training_overall_rows()[:200]})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e), 'rows': []}), 500


@app.route('/api/training-quiz/leaderboard/<level>')
def training_quiz_leaderboard(level):
    try:
        if str(level or '').strip().lower() in ('total', 'overall', 'all'):
            return jsonify({'ok': True, 'level': 'total', 'rows': _training_overall_rows()[:200]})
        level_key = _training_level_key(level)
        rows_obj = training_quiz_ref.child(level_key).get() or {}
        rows = []
        if isinstance(rows_obj, dict):
            for row in rows_obj.values():
                if not isinstance(row, dict):
                    continue
                rows.append({
                    'name': row.get('name', ''),
                    'pos': row.get('pos', ''),
                    'phone': row.get('phone', ''),
                    'score': int(row.get('score') or 0),
                    'total': int(row.get('total') or _training_total_score_for(level_key)),
                    'passed': _training_row_passed(level_key, row),
                    'date': row.get('updated_at') or row.get('created_at') or '',
                })
        rows.sort(key=lambda x: (x.get('score', 0), x.get('date', '')), reverse=True)
        return jsonify({'ok': True, 'level': level_key, 'rows': rows[:200]})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e), 'rows': []}), 500
''')

if not insert_blocks:
    print('app.py มี route/API Level 2-3 อยู่แล้ว ไม่ต้อง patch')
else:
    marker = "\nif __name__ == '__main__':"
    if marker not in text:
        raise SystemExit("หา if __name__ == '__main__': ไม่เจอ — ให้เพิ่ม route/API เองก่อน app.run(debug=True)")
    text = text.replace(marker, ''.join(insert_blocks) + marker, 1)
    APP.write_text(text, encoding='utf-8')
    print('✅ Patch สำเร็จ: เพิ่ม Level 2/3 routes และ Training Quiz API แล้ว')
    print(f'✅ Backup เดิมอยู่ที่: {backup}')
    print("\nรันเช็กต่อ:")
    print('python -c "from app import app; print([str(r) for r in app.url_map.iter_rules() if \'sas_\' in str(r)])"')
