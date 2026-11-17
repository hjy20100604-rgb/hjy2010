"""가족 게임방 | 실행: streamlit run app.py
Python 3.10 이상. 같은 기기를 번갈아 사용하는 신뢰 기반 가족용 앱.
SQLite는 실행 디스크에 저장됩니다. Streamlit Community Cloud의 디스크는
재배포/재시작 시 보존되지 않을 수 있으므로 설정에서 JSON 백업을 내려받으세요.
공개 인터넷용 로그인/권한 분리/다중 기기 실시간 대전 앱은 아닙니다.
"""
import json
import random
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
import streamlit as st

st.set_page_config(page_title="우리집 플레이", page_icon="🏡", layout="centered")
DB = Path(__file__).with_name("family_game.sqlite3")
WIN_POINTS = 30
CATALOG = {
    "용돈 부탁": {"cost": 150, "icon": "💌", "desc": "합의한 금액의 용돈을 부탁해요. 실제 송금 기능은 없습니다."},
    "설거지 부탁": {"cost": 90, "icon": "🫧", "desc": "내 차례 설거지를 한 번 부탁해요."},
    "방 청소 부탁": {"cost": 120, "icon": "🧹", "desc": "합의한 방 청소를 한 번 부탁해요."},
    "간식 선택권": {"cost": 60, "icon": "🍿", "desc": "함께 먹을 간식 메뉴를 정해요."},
    "영화 선택권": {"cost": 60, "icon": "🎬", "desc": "가족 영화 한 편을 골라요."},
}

def now():
    return datetime.now().isoformat(timespec="seconds")

def connect():
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    return c

def initialize():
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS members(id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL, points INTEGER NOT NULL DEFAULT 0 CHECK(points>=0));
        CREATE TABLE IF NOT EXISTS rounds(id TEXT PRIMARY KEY, game TEXT NOT NULL,
            winner TEXT, detail TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ledger(id TEXT PRIMARY KEY, member TEXT NOT NULL,
            delta INTEGER NOT NULL, reason TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS gifts(id TEXT PRIMARY KEY, sender TEXT NOT NULL,
            receiver TEXT NOT NULL, item TEXT NOT NULL, cost INTEGER NOT NULL,
            note TEXT NOT NULL, status TEXT NOT NULL, created TEXT NOT NULL);
        ''')

def rows(sql, args=()):
    with connect() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]

def add_member(name, role):
    name = name.strip()
    if not name or len(name) > 16:
        return "이름은 공백 없이 1~16자로 입력해 주세요."
    try:
        with connect() as c:
            c.execute("INSERT INTO members VALUES(?,?,?,0)", (uuid.uuid4().hex, name, role))
    except sqlite3.IntegrityError:
        return "이미 있는 이름이에요. 다른 이름을 사용해 주세요."
    return None

def award(rid, game, winner, detail):
    # 라운드 고유 ID와 하나의 트랜잭션으로 중복 지급 방지.
    with connect() as c:
        inserted = c.execute("INSERT OR IGNORE INTO rounds VALUES(?,?,?,?,?)",
                             (rid, game, winner, detail, now())).rowcount
        if inserted and winner:
            changed = c.execute("UPDATE members SET points=points+? WHERE id=?", (WIN_POINTS, winner)).rowcount
            if not changed:
                raise ValueError("가족 정보를 찾지 못했습니다.")
            c.execute("INSERT INTO ledger VALUES(?,?,?,?,?)",
                      (uuid.uuid4().hex, winner, WIN_POINTS, f"{game} 승리", now()))

def purchase(sender, receiver, item, note):
    price = CATALOG[item]["cost"]
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        if sender == receiver:
            return "다른 가족을 골라 주세요."
        if not c.execute("SELECT 1 FROM members WHERE id=?", (receiver,)).fetchone():
            return "받을 가족을 찾지 못했어요."
        changed = c.execute("UPDATE members SET points=points-? WHERE id=? AND points>=?",
                            (price, sender, price)).rowcount
        if not changed:
            return "포인트가 부족해요. 게임에서 승리해 포인트를 모아 보세요!"
        c.execute("INSERT INTO gifts VALUES(?,?,?,?,?,?,?,?)",
                  (uuid.uuid4().hex, sender, receiver, item, price, note.strip()[:200], "대기", now()))
        c.execute("INSERT INTO ledger VALUES(?,?,?,?,?)",
                  (uuid.uuid4().hex, sender, -price, f"{item} 쿠폰 발송", now()))
    return None

def transition(gid, actor, target):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        g = c.execute("SELECT * FROM gifts WHERE id=?", (gid,)).fetchone()
        if not g:
            return
        valid = (target in ("수락", "거절") and g["status"] == "대기" and actor == g["receiver"])
        valid |= (target == "취소" and g["status"] == "대기" and actor == g["sender"])
        valid |= (target == "완료" and g["status"] == "수락" and actor == g["receiver"])
        if not valid:
            return
        c.execute("UPDATE gifts SET status=? WHERE id=?", (target, gid))
        if target in ("거절", "취소"):
            c.execute("UPDATE members SET points=points+? WHERE id=?", (g["cost"], g["sender"]))
            c.execute("INSERT INTO ledger VALUES(?,?,?,?,?)", (uuid.uuid4().hex, g["sender"],
                      g["cost"], f'{g["item"]} {target} 환불', now()))

def winner_line(board, row, col, need):
    piece = board[row][col]
    if not piece:
        return False
    h, w = len(board), len(board[0])
    for dr, dc in ((1,0),(0,1),(1,1),(1,-1)):
        count = 1
        for direction in (1,-1):
            r, c = row + dr*direction, col + dc*direction
            while 0 <= r < h and 0 <= c < w and board[r][c] == piece:
                count += 1
                r, c = r + dr*direction, c + dc*direction
        if count >= need:
            return True
    return False

def new_match(game, players):
    h, w = {"오목": (9,9), "삼목": (3,3), "사목": (6,7), "주사위 대결": (1,1)}[game]
    return {"id": uuid.uuid4().hex, "game": game, "players": players,
            "board": [[0]*w for _ in range(h)], "turn": 0, "done": False,
            "winner": None, "message": "", "rolls": [], "scores": [0,0]}

def finish(match, winner, message):
    award(match["id"], match["game"], winner, message)
    match.update(done=True, winner=winner, message=message)

def place(match, r, c):
    b = match["board"]
    if match["done"] or b[r][c]:
        return
    b[r][c] = match["turn"] + 1
    if winner_line(b, r, c, {"오목":5, "삼목":3, "사목":4}[match["game"]]):
        finish(match, match["players"][match["turn"]], "연결 성공!")
    elif all(all(row) for row in b):
        finish(match, None, "무승부예요. 한 번 더 해볼까요?")
    else:
        match["turn"] = 1-match["turn"]

def backup_bytes():
    with connect() as c:
        c.execute("BEGIN")
        data = {t:[dict(r) for r in c.execute(f"SELECT * FROM {t}")] for t in ("members","rounds","ledger","gifts")}
    return json.dumps({"version":1, "saved_at":now(), "data":data}, ensure_ascii=False, indent=2).encode("utf-8")

def restore(raw):
    if len(raw) > 5_000_000:
        raise ValueError("백업 파일은 5MB 이하만 지원해요.")
    obj = json.loads(raw)
    if obj.get("version") != 1:
        raise ValueError("지원하지 않는 백업 형식이에요.")
    data = obj["data"]
    columns = {"members":["id","name","role","points"], "rounds":["id","game","winner","detail","created"],
               "ledger":["id","member","delta","reason","created"],
               "gifts":["id","sender","receiver","item","cost","note","status","created"]}
    ids, names = set(), set()
    for table, fields in columns.items():
        if not isinstance(data.get(table), list) or len(data[table]) > 20000:
            raise ValueError("백업 데이터가 올바르지 않아요.")
        for row in data[table]:
            if not isinstance(row, dict) or set(row) != set(fields):
                raise ValueError("백업 항목 구성이 올바르지 않아요.")
            for k, v in row.items():
                if k in ("points","delta","cost"):
                    if type(v) is not int or abs(v)>1_000_000_000:
                        raise ValueError("포인트 값이 올바르지 않아요.")
                elif not (k == "winner" and v is None) and (not isinstance(v,str) or len(v)>1000):
                    raise ValueError("문자열 값이 올바르지 않아요.")
    for m in data["members"]:
        if not m["id"] or not m["name"].strip() or len(m["name"])>16 or m["points"]<0 or m["id"] in ids or m["name"] in names:
            raise ValueError("가족 정보가 올바르지 않아요.")
        ids.add(m["id"]); names.add(m["name"])
    for g in data["gifts"]:
        if g["sender"] not in ids or g["receiver"] not in ids or g["sender"]==g["receiver"] or g["cost"]<0 or g["status"] not in ("대기","수락","거절","취소","완료"):
            raise ValueError("쿠폰 정보가 올바르지 않아요.")
    if any(x["member"] not in ids for x in data["ledger"]) or any(x["winner"] is not None and x["winner"] not in ids for x in data["rounds"]):
        raise ValueError("기록의 가족 정보가 일치하지 않아요.")
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        for table, fields in columns.items():
            c.execute(f"DELETE FROM {table}")
            marks = ','.join('?' for _ in fields)
            c.executemany(f"INSERT INTO {table} ({','.join(fields)}) VALUES ({marks})",
                          [tuple(row[k] for k in fields) for row in data[table]])

initialize()
st.markdown('''<style>
.stApp {background: #F7F8FC;}
[data-testid="stMetric"] {background:white;padding:16px;border-radius:16px;border:1px solid #E5E7EB;}
h1,h2,h3 {color:#242447;}
.stButton>button {border-radius:12px;min-height:44px;}
div[data-testid="stSidebar"] {background:#EEEDFF;}
</style>''', unsafe_allow_html=True)
st.title("🏡 우리집 플레이")
st.caption("같이 웃고, 포인트 모으고, 다정한 부탁을 나눠요.")
with st.sidebar:
    st.header("우리 가족")
    with st.form("member_add", clear_on_submit=True):
        name = st.text_input("가족 이름 / 별명", max_chars=16)
        role = st.selectbox("가족 구분", ["부모", "자녀", "조부모", "가족"])
        if st.form_submit_button("가족 추가", use_container_width=True):
            error = add_member(name, role)
            if error: st.error(error)
            else: st.rerun()
    st.info("가족은 2명부터, 인원 제한 없이 등록할 수 있어요. 게임은 두 명씩 대결해요.")
    st.caption("🔓 로그인 없는 공동 기기용입니다. 현재 사용자를 바꾸면 다른 가족의 포인트·쿠폰도 조작할 수 있으니 신뢰하는 가족끼리 사용하세요. 공개 링크에 개인정보를 넣지 마세요.")

members = rows("SELECT * FROM members ORDER BY rowid")
lookup = {m["id"]:m for m in members}
def label(mid):
    return lookup.get(mid, {}).get("name", "알 수 없는 가족")

pages = st.tabs(["🎮 게임", "🏆 가족 랭킹", "🛍️ 포인트 상점", "📮 쿠폰함", "⚙️ 보관·도움말"])
with pages[0]:
    st.subheader("오늘은 어떤 게임을 할까요?")
    st.caption(f"승리 +{WIN_POINTS}P · 패배/무승부 0P · 중도 포기 0P")
    if len(members)<2:
        st.info("왼쪽 메뉴에서 가족을 두 명 이상 등록하면 게임을 시작할 수 있어요.")
    else:
        with st.form("start_match"):
            game = st.selectbox("게임 선택", ["오목","삼목","사목","주사위 대결"])
            p1 = st.selectbox("첫 번째 선수", list(lookup), format_func=label)
            p2 = st.selectbox("두 번째 선수", list(lookup), index=1, format_func=label)
            start = st.form_submit_button("새 대결 시작", type="primary", use_container_width=True)
        if start:
            if p1 == p2:
                st.error("서로 다른 두 가족을 골라 주세요.")
            else:
                st.session_state.match = new_match(game,[p1,p2])
                st.rerun()
        st.caption("진행 중에 새 대결을 시작하면 이전 판은 보상 없이 종료돼요. 첫 번째 선수가 선공입니다.")
        m = st.session_state.get("match")
        if m and all(p in lookup for p in m["players"]):
            st.divider()
            st.subheader(f'{m["game"]} · {label(m["players"][0])} vs {label(m["players"][1])}')
            if m["done"]:
                if m["winner"]:
                    st.success(f'🎉 {label(m["winner"])} 승리! {WIN_POINTS}P 지급 완료')
                else: st.info(m["message"])
            else:
                st.info(f'지금은 {label(m["players"][m["turn"]])} 차례예요!')
            if m["game"] == "주사위 대결":
                st.write("한 명씩 주사위 2개를 던져요. 각자 3번 던진 총점이 높은 사람이 승리! 동점이면 무승부예요.")
                a,b = st.columns(2)
                a.metric(label(m["players"][0]), f'{m["scores"][0]}점')
                b.metric(label(m["players"][1]), f'{m["scores"][1]}점')
                if st.button("🎲 주사위 두 개 던지기", disabled=m["done"], key=f'roll_{m["id"]}', use_container_width=True):
                    turn = m["turn"]
                    dice = [random.randint(1,6), random.randint(1,6)]
                    m["rolls"].append((turn, dice))
                    m["scores"][turn] += sum(dice)
                    if len(m["rolls"]) == 6:
                        s = m["scores"]
                        win = None if s[0]==s[1] else m["players"][0 if s[0]>s[1] else 1]
                        finish(m, win, "주사위 대결 종료 · 동점이면 무승부")
                    else: m["turn"] = 1-turn
                    st.rerun()
                for i, (who, dice) in enumerate(m["rolls"],1):
                    st.write(f'{i}번째 · {label(m["players"][who])}: 🎲 {dice[0]} + {dice[1]} = {sum(dice)}')
            else:
                rules = {"오목":"9×9 미니 오목 · 가로·세로·대각선 5개 이상 연결! 흑 금수 없는 가족용 자유 규칙입니다.",
                         "삼목":"3×3 칸에서 가로·세로·대각선 3개를 먼저 연결하세요.",
                         "사목":"열을 누르면 아래부터 돌이 쌓여요. 가로·세로·대각선 4개를 먼저 연결하세요."}
                st.caption(rules[m["game"]])
                st.caption(f'⚫ {label(m["players"][0])} / 🔴 {label(m["players"][1])}')
                board = m["board"]
                if m["game"] == "사목":
                    cols = st.columns(7)
                    for c, col in enumerate(cols):
                        if col.button(f"↓{c+1}", key=f'drop_{m["id"]}_{c}', disabled=m["done"] or board[0][c]!=0, use_container_width=True):
                            r = next(r for r in range(5,-1,-1) if board[r][c]==0)
                            place(m,r,c); st.rerun()
                for r, row in enumerate(board):
                    cols = st.columns(len(row), gap="small")
                    for c, value in enumerate(row):
                        text = ["·","⚫","🔴"][value]
                        if cols[c].button(text, key=f'cell_{m["id"]}_{r}_{c}',
                                          disabled=m["done"] or value!=0 or m["game"]=="사목", use_container_width=True):
                            place(m,r,c); st.rerun()
                st.caption("오목판은 태블릿·PC 가로 화면에서 가장 편해요.")

with pages[1]:
    st.subheader("🏆 우리 가족 포인트")
    ranking = rows("SELECT m.*, (SELECT COUNT(*) FROM rounds r WHERE r.winner=m.id) wins FROM members m ORDER BY points DESC, wins DESC, name")
    for i, person in enumerate(ranking,1):
        st.write(f'{i}. **{person["name"]}** · {person["role"]} · **{person["points"]}P** · {person["wins"]}승')
    if not ranking: st.info("먼저 가족을 등록해 주세요.")
    st.caption("순위는 현재 잔액 기준이에요. 쿠폰을 사면 잔액과 순위가 바뀔 수 있어요.")
    with st.expander("최근 대결 기록"):
        for r in rows("SELECT * FROM rounds ORDER BY rowid DESC LIMIT 30"):
            st.write(f'{r["created"]} · {r["game"]} · {label(r["winner"]) + " 승리" if r["winner"] else "무승부"}')

with pages[2]:
    st.subheader("🛍️ 다정한 부탁 상점")
    st.warning("쿠폰은 명령이 아니라 부탁이에요. 금액·범위·시기는 가족끼리 합의하세요. 받은 가족이 거절하면 전액 환불돼요.")
    for title, item in CATALOG.items():
        with st.container(border=True):
            st.markdown(f'### {item["icon"]} {title} · {item["cost"]}P')
            st.write(item["desc"])
    if len(members)>=2:
        with st.form("buy"):
            sender = st.selectbox("구매하는 가족", list(lookup), format_func=label, key="buyer")
            receiver = st.selectbox("부탁을 받을 가족", list(lookup), index=1, format_func=label, key="recipient")
            item = st.selectbox("살 쿠폰", list(CATALOG), format_func=lambda x:f'{x} ({CATALOG[x]["cost"]}P)')
            note = st.text_input("메시지 (예: 이번 토요일, 내 방 바닥 청소 부탁해요)", max_chars=200)
            agree = st.checkbox("가족의 동의가 필요한 부탁이며, 실제 현금 결제가 아님을 알아요.")
            buy = st.form_submit_button("포인트로 구매하고 보내기", type="primary", use_container_width=True)
        if buy:
            if not agree: st.error("쿠폰 약속을 확인해 주세요.")
            else:
                error = purchase(sender, receiver, item, note)
                if error: st.error(error)
                else:
                    st.session_state.notice = "쿠폰을 보냈어요! 쿠폰함에서 확인해 주세요."
                    st.rerun()
        st.caption("위 가족 랭킹에서 구매 전 현재 포인트를 확인할 수 있어요.")
    else: st.info("두 명 이상 등록하면 상점을 이용할 수 있어요.")
    if st.session_state.get("notice"):
        st.success(st.session_state.pop("notice"))

with pages[3]:
    st.subheader("📮 우리 가족 쿠폰함")
    st.caption("전송은 이 앱의 쿠폰함 안에서만 이뤄져요. 문자·카카오톡·이메일은 보내지 않습니다.")
    if members:
        actor = st.selectbox("현재 확인하는 가족", list(lookup), format_func=label)
        mode = st.radio("보기", ["받은 쿠폰","보낸 쿠폰"], horizontal=True)
        field = "receiver" if mode=="받은 쿠폰" else "sender"
        gifts = rows(f"SELECT * FROM gifts WHERE {field}=? ORDER BY rowid DESC", (actor,))
        if not gifts: st.info("아직 쿠폰이 없어요. 첫 번째 다정한 부탁을 보내보세요!")
        for g in gifts:
            with st.container(border=True):
                st.markdown(f'**{g["item"]}** · {g["cost"]}P · {g["status"]}')
                st.write(f'{label(g["sender"])} → {label(g["receiver"])}')
                if g["note"]: st.write(g["note"])
                st.caption(g["created"])
                actions = []
                if g["status"] == "대기":
                    actions = ["수락","거절"] if actor==g["receiver"] else ["취소"]
                elif g["status"]=="수락" and actor==g["receiver"]:
                    actions = ["완료"]
                for action, col in zip(actions, st.columns(max(1,len(actions)))):
                    if col.button(action, key=f'{g["id"]}_{action}', use_container_width=True):
                        transition(g["id"],actor,action); st.rerun()
                if g["status"]=="수락": st.caption("부탁을 실제로 해준 뒤 받은 가족이 '완료'를 눌러요.")
    else: st.info("먼저 가족을 등록해 주세요.")

with pages[4]:
    st.subheader("⚙️ 기록 보관 & 시작 안내")
    st.markdown('''**GitHub → Streamlit 배포**
1. GitHub 저장소에 `app.py`, `requirements.txt` 두 파일을 올려요.
2. Streamlit Community Cloud에서 저장소를 연결하고 Main file path를 `app.py`로 지정해요.
3. Deploy를 누르고 가족 이름 두 명 이상을 등록해요.

**내 컴퓨터에서 실행**
```bash
pip install -r requirements.txt
streamlit run app.py
```
**꼭 알아두세요**
- 가족 구성원 수에 앱 자체 제한은 없고, 각 경기는 두 명씩 진행합니다.
- 현재 대결은 브라우저 세션에만 있습니다. 새로고침·연결 종료 시 진행 중인 판이 사라질 수 있어요.
- 이름·점수·쿠폰·승리 기록은 서버의 SQLite 파일에 저장됩니다.
- 클라우드 재시작·절전 복구·재배포 시 파일이 사라질 수 있어요. 사용 후 백업을 내려받으세요.
- 영구 보관과 개별 로그인이 필요하다면 외부 DB와 인증을 추가해야 합니다.
- 서로 다른 기기의 동시 대전·실시간 알림은 지원하지 않습니다. 한 기기를 번갈아 사용하세요.
- 같은 배포 주소를 쓰는 사람은 같은 가족 명단을 봅니다. 여러 가정 분리 기능은 없습니다.
- 쿠폰은 가족 사이의 약속일 뿐 실제 돈을 지급하거나 집안일을 강제하지 않습니다.
- 로그인 없는 신뢰 기반 앱이라 사용자 전환·백업 수정에 따른 부정 사용은 막지 않습니다.
''')
    st.download_button("💾 전체 기록 JSON 백업", backup_bytes(), file_name=f'family_backup_{datetime.now():%Y%m%d_%H%M%S}.json', mime="application/json", use_container_width=True)
    st.caption("백업에는 가족 이름·포인트·메시지가 들어 있어요. 안전한 곳에 보관해 주세요.")
    with st.expander("백업 복원 (현재 기록을 교체합니다)"):
        with st.form("restore"):
            upload = st.file_uploader("이 앱에서 받은 JSON 백업", type=["json"])
            confirm = st.checkbox("기존 가족·포인트·쿠폰·경기 기록이 백업 내용으로 바뀌는 데 동의합니다.")
            submitted = st.form_submit_button("백업으로 복원")
        if submitted:
            if not confirm or upload is None: st.error("백업 파일과 교체 동의를 확인해 주세요.")
            else:
                try:
                    restore(upload.getvalue())
                    st.session_state.pop("match", None)
                    st.rerun()
                except (ValueError, KeyError, TypeError, sqlite3.Error, UnicodeDecodeError):
                    st.error("복원하지 못했어요. 올바른 앱 백업 파일인지 확인해 주세요. 기존 기록은 변경하지 않았습니다.")
    if members:
        with st.expander("최근 포인트 내역"):
            for entry in rows("SELECT * FROM ledger ORDER BY rowid DESC LIMIT 50"):
                st.write(f'{entry["created"]} · {label(entry["member"])} · {entry["delta"]:+}P · {entry["reason"]}')
