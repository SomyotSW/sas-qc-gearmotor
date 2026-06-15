from pathlib import Path

"""
Patch sas_quiz.html (Level 1) ให้บันทึกผล Quiz ลง Firebase ผ่าน API กลาง
วิธีใช้:
  1) วางไฟล์นี้ไว้ที่ root project หรือในโฟลเดอร์เดียวกับ sas_quiz.html
  2) รัน: python patch_sas_quiz_level1_server_sync.py
  3) Commit + Deploy Render ใหม่
"""

CANDIDATES = [
    Path("templates/sas_quiz.html"),
    Path("sas_quiz.html"),
]

path = next((p for p in CANDIDATES if p.exists()), None)
if not path:
    raise SystemExit("❌ ไม่พบ sas_quiz.html: กรุณาวาง script ที่ root project หรือ templates folder")

s = path.read_text(encoding="utf-8")
if "saveLevel1ResultServer" in s:
    print("✅ sas_quiz.html มีระบบ sync server แล้ว ไม่ต้อง patch ซ้ำ")
    raise SystemExit(0)

backup = path.with_suffix(path.suffix + ".backup_before_server_sync")
backup.write_text(s, encoding="utf-8")

inject = r'''

// ✅ SERVER SYNC: บันทึกผล Level 1 ลงฐานข้อมูลกลาง เพื่อให้ Level 2 ตรวจเจอแม้เปิดจากเครื่องใหม่
function saveLevel1ResultServer(name,pos,phone,scoreVal){
  try{
    fetch('/api/training-quiz/save',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        level:'level1',
        name:name,
        pos:pos,
        phone:phone,
        score:scoreVal,
        total:30,
        passed:Number(scoreVal)>=25
      })
    }).then(function(res){
      if(!res.ok) console.warn('Level 1 server sync failed');
    }).catch(function(err){
      console.warn('Level 1 server sync error',err);
    });
  }catch(e){
    console.warn('Level 1 server sync exception',e);
  }
}
'''

# ใส่ function ก่อน nextQ หรือ finishQuiz
marker = "function nextQ(){curr++;if(curr>=30)finishQuiz();else renderQ();}"
if marker in s:
    s = s.replace(marker, inject + "\n" + marker, 1)
else:
    marker2 = "function finishQuiz(){"
    if marker2 not in s:
        raise SystemExit("❌ หา function finishQuiz ไม่เจอ กรุณาส่งไฟล์ sas_quiz.html ให้แก้แบบเต็ม")
    s = s.replace(marker2, inject + "\n" + marker2, 1)

old = "updateLB(userData.name,userData.pos,userData.phone,score);"
new = old + "\n  saveLevel1ResultServer(userData.name,userData.pos,userData.phone,score);"
if old not in s:
    raise SystemExit("❌ หา updateLB(...) ไม่เจอ กรุณาส่งไฟล์ sas_quiz.html ให้แก้แบบเต็ม")
s = s.replace(old, new, 1)

path.write_text(s, encoding="utf-8")
print(f"✅ Patch สำเร็จ: {path}")
print(f"🧷 Backup: {backup}")
print("ต่อไปให้ Commit + Deploy Render ใหม่")
