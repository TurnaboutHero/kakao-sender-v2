"""
카카오톡 자동 발송기 v5
- 헤더 제거
- 회원 명단: 가나다순 자동 정렬
- 텍스트 파일(.txt) 불러오기 (중복 로그 처리)
- 로그창 크기 확대
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import threading
import time
import os
import json
import pyautogui
import pyperclip
from PIL import Image
import win32clipboard
import win32gui
import win32con
import io

# ══════════════════════════════════════════════
#  파일 경로
# ══════════════════════════════════════════════

# exe로 빌드된 경우 sys.executable(exe 위치) 기준,
# .py로 실행 시 __file__ 기준으로 저장 경로 설정
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)        # exe 파일이 있는 폴더
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # .py 파일이 있는 폴더

MEMBERS_FILE = os.path.join(BASE_DIR, "members.json")

# ══════════════════════════════════════════════
#  명단 저장 / 불러오기
# ══════════════════════════════════════════════

def load_members():
    if os.path.exists(MEMBERS_FILE):
        try:
            with open(MEMBERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_members(members):
    with open(MEMBERS_FILE, "w", encoding="utf-8") as f:
        json.dump(members, f, ensure_ascii=False, indent=2)

# ══════════════════════════════════════════════
#  카카오톡 제어
# ══════════════════════════════════════════════

def focus_kakao():
    hwnd = win32gui.FindWindow(None, "카카오톡")
    if not hwnd:
        raise RuntimeError("카카오톡 PC버전이 실행되어 있지 않습니다!")
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.5)

def image_to_clipboard(image_path):
    img  = Image.open(image_path)
    out  = io.BytesIO()
    img.convert("RGB").save(out, "BMP")
    data = out.getvalue()[14:]
    out.close()
    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
    win32clipboard.CloseClipboard()

def wait_until(condition_fn, timeout=3.0, interval=0.05):
    """
    condition_fn() 이 True 를 반환할 때까지 최대 timeout 초 대기.
    조건 충족 시 True, 타임아웃 시 False 반환.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition_fn():
            return True
        time.sleep(interval)
    return False

def get_kakao_pixel(rel_x, rel_y):
    """
    카카오톡 창 내 상대 좌표(비율)의 픽셀 색상 반환.
    rel_x, rel_y : 0.0 ~ 1.0
    """
    hwnd = win32gui.FindWindow(None, "카카오톡")
    if not hwnd:
        return None
    r   = win32gui.GetWindowRect(hwnd)
    lx, ly = r[0], r[1]
    lw, lh = r[2] - r[0], r[3] - r[1]
    px = lx + int(lw * rel_x)
    py = ly + int(lh * rel_y)
    return pyautogui.pixel(px, py)

def is_chat_input_ready():
    """
    채팅 입력창이 열렸는지 확인.
    카카오톡 하단 입력 영역(상대 y 약 0.92)의 배경이
    흰색 계열이면 준비 완료로 판단.
    """
    px = get_kakao_pixel(0.5, 0.92)
    if px is None:
        return False
    r, g, b = px
    # 흰색~밝은 회색 범위 (입력창 배경)
    return r > 200 and g > 200 and b > 200

def open_search():
    """검색창 최초 1회 열기"""
    focus_kakao()
    pyautogui.hotkey("ctrl", "f")
    time.sleep(0.25)

def close_search():
    """발송 완료 후 검색창 닫기"""
    pyautogui.press("escape")
    time.sleep(0.1)

def send_one(name, message, image_path, first=False):
    """
    검색창이 이미 열려있는 상태에서 한 명에게 전송.
    first=True  -> 검색창에 바로 이름 입력
    first=False -> Ctrl+A 로 기존 이름 지우고 새 이름 입력
    """
    try:
        focus_kakao()

        # 1. 이름 교체 입력
        if not first:
            pyautogui.press("end")
            time.sleep(0.05)
            pyautogui.hotkey("shift", "home")
            time.sleep(0.05)

        pyperclip.copy(name)
        pyautogui.hotkey("ctrl", "v")

        # 검색 결과 등장 대기 (최대 1.5초)
        wait_until(
            lambda: get_kakao_pixel(0.5, 0.35) is not None,
            timeout=1.5, interval=0.05
        )
        time.sleep(0.1)

        # 2. 첫 번째 결과 선택
        pyautogui.press("enter")
        time.sleep(0.1)

        # 3. 1:1 채팅창 열기
        pyautogui.hotkey("alt", "enter")
        wait_until(is_chat_input_ready, timeout=2.0, interval=0.05)
        time.sleep(0.1)

        # 4. 이미지 전송
        if image_path:
            image_to_clipboard(image_path)
            time.sleep(0.1)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.25)
            pyautogui.press("enter")
            wait_until(is_chat_input_ready, timeout=3.0, interval=0.08)
            time.sleep(0.1)

        # 5. 텍스트 전송
        if message:
            pyperclip.copy(message)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.08)
            pyautogui.press("enter")
            time.sleep(0.1)

        # 6. 채팅창 닫기 -> 검색창으로 복귀
        pyautogui.hotkey("ctrl", "w")
        time.sleep(0.1)

        return True
    except Exception as e:
        return str(e)

# ══════════════════════════════════════════════
#  색상 / 폰트
# ══════════════════════════════════════════════

C = {
    "yellow"   : "#FEE500",
    "brown"    : "#3C1E1E",
    "bg"       : "#F5F5F5",
    "white"    : "#FFFFFF",
    "border"   : "#E0E0E0",
    "txt"      : "#1A1A1A",
    "sub"      : "#888888",
    "green"    : "#2E7D32",
    "green_bg" : "#E8F5E9",
    "red"      : "#C62828",
    "red_bg"   : "#FFEBEE",
    "blue"     : "#1565C0",
    "blue_bg"  : "#E3F2FD",
    "warn_bg"  : "#FFF8E1",
    "warn_txt" : "#F57F17",
    "log_bg"   : "#1C1C1E",
    "log_ok"   : "#30D158",
    "log_fail" : "#FF453A",
    "log_info" : "#FFD60A",
    "log_dim"  : "#636366",
    "log_warn" : "#FF9F0A",
    "disabled" : "#BDBDBD",
}

def F(size, bold=False):
    return ("맑은 고딕", size, "bold" if bold else "normal")

# ══════════════════════════════════════════════
#  앱
# ══════════════════════════════════════════════

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("카카오톡 자동 발송기")
        self.root.geometry("500x800")
        self.root.resizable(False, False)
        self.root.configure(bg=C["bg"])

        self.members    = load_members()
        self.image_path = tk.StringVar()
        self.delay_var  = tk.DoubleVar(value=2.5)
        self.is_running = False
        self.stop_flag  = False

        self._build()

    # ── 전체 구조 ──────────────────────────────

    def _build(self):
        style = ttk.Style()
        style.theme_use("default")
        style.configure("TNotebook",     background=C["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=C["border"],
                        foreground=C["sub"], font=("맑은 고딕", 10, "bold"),
                        padding=[20, 10], borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", C["yellow"])],
                  foreground=[("selected", C["brown"])])

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True)

        self.tab_members = tk.Frame(nb, bg=C["bg"])
        self.tab_send    = tk.Frame(nb, bg=C["bg"])
        nb.add(self.tab_members, text="👥  회원 명단 관리")
        nb.add(self.tab_send,    text="📨  메시지 전송")

        self._build_tab_members()
        self._build_tab_send()

        # Ctrl+S 전역 단축키로 저장
        self.root.bind("<Control-s>", lambda e: self._save())

    # ══════════════════════════════════════════
    #  TAB 1: 회원 명단 관리
    # ══════════════════════════════════════════

    def _build_tab_members(self):
        p = self.tab_members

        # ── 안내 문구 ──
        notice = tk.Frame(p, bg=C["warn_bg"], padx=14, pady=10)
        notice.pack(fill="x", padx=14, pady=(14, 0))
        tk.Label(notice, text="⚠️  이름 등록 시 주의사항",
                 font=F(9, True), bg=C["warn_bg"], fg=C["warn_txt"]).pack(anchor="w")
        tk.Label(notice,
                 text="회원 이름은 카카오톡에 표시된 이름과 정확히 동일해야 합니다.",
                 font=F(8), bg=C["warn_bg"], fg=C["warn_txt"],
                 justify="left").pack(anchor="w", pady=(2, 0))

        # ── 이름 직접 입력 ──
        tk.Label(p, text="이름 직접 입력", font=F(9, True),
                 bg=C["bg"], fg=C["sub"]).pack(anchor="w", padx=14, pady=(12, 0))

        input_row = tk.Frame(p, bg=C["bg"])
        input_row.pack(fill="x", padx=14, pady=(4, 0))

        ef = tk.Frame(input_row, bg=C["border"], padx=1, pady=1)
        ef.pack(side="left", fill="x", expand=True)
        self.name_entry = tk.Entry(ef, font=F(11), relief="flat", bd=0,
                                   bg=C["white"], fg=C["txt"],
                                   insertbackground=C["txt"])
        self.name_entry.pack(fill="x", ipady=7, padx=8)
        self.name_entry.bind("<Return>", lambda e: self._add_one())

        tk.Button(input_row, text="➕ 추가",
                  command=self._add_one,
                  font=F(10, True), bg=C["yellow"], fg=C["brown"],
                  relief="flat", padx=14, pady=7, cursor="hand2",
                  activebackground="#f0d800").pack(side="left", padx=(8, 0))

        # ── 텍스트 파일 불러오기 ──
        tk.Label(p, text="메모장(.txt) 파일에서 불러오기",
                 font=F(9, True), bg=C["bg"], fg=C["sub"]).pack(anchor="w", padx=14, pady=(12, 0))

        file_row = tk.Frame(p, bg=C["bg"])
        file_row.pack(fill="x", padx=14, pady=(4, 0))

        file_info = tk.Frame(file_row, bg=C["border"], padx=1, pady=1)
        file_info.pack(side="left", fill="x", expand=True)
        self.file_lbl = tk.Label(file_info, text="  파일을 선택하세요 (한 줄에 한 명씩)",
                                 font=F(8), bg=C["white"], fg=C["sub"],
                                 anchor="w")
        self.file_lbl.pack(fill="x", ipady=6, padx=4)

        tk.Button(file_row, text="📂 불러오기",
                  command=self._import_txt,
                  font=F(10, True), bg=C["blue_bg"], fg=C["blue"],
                  relief="flat", padx=12, pady=7, cursor="hand2").pack(side="left", padx=(8, 0))

        # ── 회원 수 표시 + 전체 삭제 ──
        count_row = tk.Frame(p, bg=C["bg"])
        count_row.pack(fill="x", padx=14, pady=(12, 4))

        self.count_lbl = tk.Label(count_row, text="",
                                  font=F(9, True), bg=C["bg"], fg=C["green"])
        self.count_lbl.pack(side="left")

        tk.Button(count_row, text="전체 삭제",
                  command=self._clear_all,
                  font=F(8), bg=C["red_bg"], fg=C["red"],
                  relief="flat", padx=8, pady=3, cursor="hand2").pack(side="right")

        # ── 검색창 ──
        search_row = tk.Frame(p, bg=C["bg"])
        search_row.pack(fill="x", padx=14, pady=(0, 4))

        sf = tk.Frame(search_row, bg=C["border"], padx=1, pady=1)
        sf.pack(side="left", fill="x", expand=True)

        search_inner = tk.Frame(sf, bg=C["white"])
        search_inner.pack(fill="x")

        tk.Label(search_inner, text="🔍", font=F(9),
                 bg=C["white"], fg=C["sub"]).pack(side="left", padx=(6, 0))

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._refresh_list())
        tk.Entry(search_inner, textvariable=self.search_var,
                 font=F(10), relief="flat", bd=0,
                 bg=C["white"], fg=C["txt"],
                 insertbackground=C["txt"]).pack(side="left", fill="x", expand=True,
                                                 ipady=6, padx=6)

        tk.Button(search_inner, text="✕",
                  command=lambda: self.search_var.set(""),
                  font=F(8), bg=C["white"], fg=C["sub"],
                  relief="flat", padx=4, cursor="hand2").pack(side="right", padx=(0, 4))

        # ── 리스트박스 ──
        list_frame = tk.Frame(p, bg=C["border"], padx=1, pady=1)
        list_frame.pack(fill="both", expand=True, padx=14, pady=(0, 6))

        inner = tk.Frame(list_frame, bg=C["white"])
        inner.pack(fill="both", expand=True)

        sb = ttk.Scrollbar(inner, orient="vertical")
        sb.pack(side="right", fill="y")

        self.listbox = tk.Listbox(inner, font=F(10),
                                  bg=C["white"], fg=C["txt"],
                                  selectbackground=C["yellow"],
                                  selectforeground=C["brown"],
                                  activestyle="none",
                                  relief="flat", bd=0,
                                  yscrollcommand=sb.set)
        self.listbox.pack(fill="both", expand=True, padx=4, pady=4)
        sb.config(command=self.listbox.yview)
        self.listbox.bind("<Double-Button-1>", lambda e: self._edit_member())

        # ── 수정 / 삭제 / 저장 버튼 ──
        btn_row = tk.Frame(p, bg=C["bg"])
        btn_row.pack(fill="x", padx=14, pady=(0, 14))

        tk.Button(btn_row, text="✏️  수정",
                  command=self._edit_member,
                  font=F(10, True), bg=C["blue_bg"], fg=C["blue"],
                  relief="flat", padx=16, pady=7, cursor="hand2").pack(side="left", padx=(0, 6))

        tk.Button(btn_row, text="🗑️  삭제",
                  command=self._delete_member,
                  font=F(10, True), bg=C["red_bg"], fg=C["red"],
                  relief="flat", padx=16, pady=7, cursor="hand2").pack(side="left")

        tk.Button(btn_row, text="💾  저장  (Ctrl+S)",
                  command=self._save,
                  font=F(10, True), bg=C["green_bg"], fg=C["green"],
                  relief="flat", padx=16, pady=7, cursor="hand2").pack(side="right")

        self._refresh_list()

    # ── 명단 관리 핵심 메서드 ──────────────────

    def _sorted_insert(self, name):
        """가나다순 유지하며 삽입 위치 반환"""
        self.members.append(name)
        self.members.sort(key=lambda x: x.lower())

    def _refresh_list(self):
        keyword = self.search_var.get().strip() if hasattr(self, "search_var") else ""
        filtered = [n for n in self.members if keyword.lower() in n.lower()] if keyword else self.members
        self.listbox.delete(0, "end")
        for i, name in enumerate(filtered, 1):
            self.listbox.insert("end", f"  {i}.  {name}")
        total = len(self.members)
        matched = len(filtered)
        if keyword:
            self.count_lbl.config(
                text=f"'{keyword}' 검색 결과: {matched}명 / 전체 {total}명",
                fg=C["blue"] if matched else C["red"])
        else:
            self.count_lbl.config(
                text=f"총 {total}명 등록됨" if total else "등록된 명단이 없습니다.",
                fg=C["green"] if total else C["sub"])

    def _add_one(self):
        name = self.name_entry.get().strip()
        if not name:
            messagebox.showwarning("입력 오류", "이름을 입력해주세요!"); return
        if name in self.members:
            messagebox.showwarning("중복", f"'{name}'은 이미 등록된 이름입니다!"); return
        self._sorted_insert(name)
        self.name_entry.delete(0, "end")
        self._refresh_list()

    def _import_txt(self):
        """
        .txt 파일을 열어 한 줄씩 이름으로 읽어 추가.
        중복은 명단 탭 로그 없이 팝업으로 요약 안내.
        """
        path = filedialog.askopenfilename(
            title="명단 텍스트 파일 선택",
            filetypes=[("텍스트 파일", "*.txt"), ("모든 파일", "*.*")]
        )
        if not path: return

        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except UnicodeDecodeError:
            # UTF-8 실패 시 CP949(한글 윈도우 기본 인코딩) 재시도
            with open(path, "r", encoding="cp949") as f:
                lines = f.readlines()

        added    = []
        skipped  = []   # 중복

        for line in lines:
            name = line.strip()
            if not name:       continue   # 빈 줄 무시
            if name in self.members or name in added:
                skipped.append(name)
            else:
                added.append(name)

        # 추가
        for name in added:
            self.members.append(name)
        self.members.sort(key=lambda x: x.lower())
        self._refresh_list()

        # 결과 팝업
        summary = f"✅ {len(added)}명 추가되었습니다."
        if skipped:
            summary += f"\n\n⏭ 중복으로 건너뜀 ({len(skipped)}명):\n"
            summary += "\n".join(f"  • {n}" for n in skipped)
        messagebox.showinfo("불러오기 완료", summary)

        self.file_lbl.config(
            text=f"  ✅ {os.path.basename(path)}  ({len(added)}명 추가)",
            fg=C["green"])

    def _edit_member(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("안내", "수정할 회원을 선택해주세요."); return
        # 검색 필터 중일 수 있으므로 리스트박스 텍스트에서 이름 추출
        raw      = self.listbox.get(sel[0])
        old_name = raw.strip().split(".  ", 1)[-1].strip()
        new_name = simpledialog.askstring(
            "이름 수정",
            "새 이름을 입력하세요.\n\n⚠️ 카카오톡에 표시된 이름과 정확히 동일해야 합니다.",
            initialvalue=old_name, parent=self.root)
        if new_name is None: return
        new_name = new_name.strip()
        if not new_name:
            messagebox.showwarning("오류", "이름을 입력해주세요!"); return
        if new_name != old_name and new_name in self.members:
            messagebox.showwarning("중복", f"'{new_name}'은 이미 존재하는 이름입니다!"); return
        idx = self.members.index(old_name)
        self.members[idx] = new_name
        self.members.sort(key=lambda x: x.lower())
        self._refresh_list()

    def _delete_member(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("안내", "삭제할 회원을 선택해주세요."); return
        raw  = self.listbox.get(sel[0])
        name = raw.strip().split(".  ", 1)[-1].strip()
        if not messagebox.askyesno("삭제 확인", f"'{name}'을 명단에서 삭제할까요?"): return
        self.members.remove(name)
        self._refresh_list()

    def _clear_all(self):
        if not self.members: return
        if not messagebox.askyesno("전체 삭제",
                f"전체 {len(self.members)}명을 모두 삭제할까요?\n이 작업은 되돌릴 수 없습니다."): return
        self.members.clear()
        self._refresh_list()

    def _save(self):
        save_members(self.members)
        messagebox.showinfo("저장 완료", f"✅ {len(self.members)}명의 명단이 저장되었습니다.")

    # ══════════════════════════════════════════
    #  TAB 2: 메시지 전송
    # ══════════════════════════════════════════

    def _build_tab_send(self):
        p = self.tab_send

        # ── 명단 현황 ──
        notice = tk.Frame(p, bg=C["blue_bg"], padx=14, pady=10)
        notice.pack(fill="x", padx=14, pady=(14, 0))
        self.send_count_lbl = tk.Label(notice, text="",
                                       font=F(9, True), bg=C["blue_bg"], fg=C["blue"])
        self.send_count_lbl.pack(side="left")
        tk.Button(notice, text="🔄 새로고침",
                  command=self._reload_members,
                  font=F(8), bg=C["blue"], fg="white",
                  relief="flat", padx=8, pady=3, cursor="hand2").pack(side="right")
        self._update_send_count()

        # ── 메시지 ──
        tk.Label(p, text="✉️  메시지 내용", font=F(9, True),
                 bg=C["bg"], fg=C["sub"]).pack(anchor="w", padx=14, pady=(12, 0))
        mf = tk.Frame(p, bg=C["border"], padx=1, pady=1)
        mf.pack(fill="x", padx=14, pady=(4, 0))
        self.msg_text = tk.Text(tk.Frame(mf, bg=C["white"]),
                                height=4, font=F(10), relief="flat", bd=0,
                                bg=C["white"], fg=C["txt"],
                                insertbackground=C["txt"],
                                wrap="word", padx=10, pady=8)
        self.msg_text.master.pack(fill="x")
        self.msg_text.pack(fill="x")
        self.msg_text.insert("end", "안녕하세요! 여기에 보낼 메시지를 입력하세요.")

        # ── 이미지 첨부 ──
        tk.Label(p, text="🖼️  이미지 첨부 (선택)", font=F(9, True),
                 bg=C["bg"], fg=C["sub"]).pack(anchor="w", padx=14, pady=(10, 0))
        img_outer = tk.Frame(p, bg=C["border"], padx=1, pady=1)
        img_outer.pack(fill="x", padx=14, pady=(4, 0))
        img_inner = tk.Frame(img_outer, bg=C["white"], padx=10, pady=7)
        img_inner.pack(fill="x")
        tk.Entry(img_inner, textvariable=self.image_path, font=F(8),
                 relief="flat", bd=0, bg=C["white"], fg=C["sub"],
                 state="readonly", readonlybackground=C["white"]
                 ).pack(side="left", fill="x", expand=True)
        tk.Button(img_inner, text="찾아보기", command=self._browse,
                  font=F(8), bg=C["yellow"], fg=C["brown"],
                  relief="flat", padx=8, pady=3, cursor="hand2",
                  activebackground="#f0d800").pack(side="right", padx=(6, 0))
        tk.Button(img_inner, text="✕", command=lambda: self.image_path.set(""),
                  font=F(8), bg="#eee", fg="#555",
                  relief="flat", padx=6, pady=3, cursor="hand2").pack(side="right")
        self.img_lbl = tk.Label(p, text="이미지 없음", font=F(8),
                                bg=C["bg"], fg=C["sub"])
        self.img_lbl.pack(anchor="e", padx=14, pady=(3, 0))
        self.image_path.trace_add("write", self._on_img)

        # ── 전송 간격 슬라이더 ──
        tk.Label(p, text="⏱️  전송 간격", font=F(9, True),
                 bg=C["bg"], fg=C["sub"]).pack(anchor="w", padx=14, pady=(10, 0))
        sl_row = tk.Frame(p, bg=C["bg"])
        sl_row.pack(fill="x", padx=14, pady=(4, 0))
        tk.Label(sl_row, text="1초\n빠름", font=F(7),
                 bg=C["bg"], fg=C["sub"], justify="center").pack(side="left")
        ttk.Scale(sl_row, from_=1.0, to=5.0, variable=self.delay_var,
                  orient="horizontal", length=330,
                  command=self._upd_delay).pack(side="left", padx=6)
        tk.Label(sl_row, text="5초\n안전", font=F(7),
                 bg=C["bg"], fg=C["sub"], justify="center").pack(side="left")

        di = tk.Frame(p, bg=C["bg"])
        di.pack(fill="x", padx=14, pady=(4, 0))
        tk.Label(di, text="현재:", font=F(8), bg=C["bg"], fg=C["sub"]).pack(side="left")
        self.delay_lbl = tk.Label(di, text="2.5초", font=F(9, True),
                                  bg=C["bg"], fg=C["brown"])
        self.delay_lbl.pack(side="left", padx=(3, 0))
        self.eta_lbl = tk.Label(di, text="", font=F(8), bg=C["bg"], fg=C["sub"])
        self.eta_lbl.pack(side="left", padx=(10, 0))
        self._upd_delay()

        # ── 발송 / 중지 버튼 ──
        btn_f = tk.Frame(p, bg=C["bg"])
        btn_f.pack(fill="x", padx=14, pady=(12, 6))
        self.start_btn = tk.Button(btn_f, text="▶  발송 시작",
                                   command=self._start,
                                   font=F(11, True), bg=C["yellow"], fg=C["brown"],
                                   relief="flat", height=1, padx=10, pady=6,
                                   cursor="hand2", activebackground="#f0d800")
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.stop_btn = tk.Button(btn_f, text="■  중지",
                                  command=self._stop,
                                  font=F(11, True), bg=C["disabled"], fg="white",
                                  relief="flat", height=1, padx=10, pady=6,
                                  cursor="hand2", state="disabled")
        self.stop_btn.pack(side="left", fill="x", expand=True)

        # ── 로그 (expand=True 로 남은 공간 전부 사용) ──
        log_outer = tk.Frame(p, bg=C["border"], padx=1, pady=1)
        log_outer.pack(fill="both", expand=True, padx=14, pady=(6, 14))

        log_card = tk.Frame(log_outer, bg=C["log_bg"])
        log_card.pack(fill="both", expand=True)

        log_hdr = tk.Frame(log_card, bg="#2C2C2E", padx=12, pady=6)
        log_hdr.pack(fill="x")
        tk.Label(log_hdr, text="📋  발송 로그", font=F(8, True),
                 bg="#2C2C2E", fg="#EBEBF5").pack(side="left")

        self.prog_var = tk.DoubleVar(value=0)
        ttk.Progressbar(log_card, variable=self.prog_var,
                        maximum=100, mode="determinate").pack(fill="x")

        log_inner = tk.Frame(log_card, bg=C["log_bg"])
        log_inner.pack(fill="both", expand=True)

        self.log_box = tk.Text(log_inner, font=("Consolas", 9),
                               relief="flat", bd=0, state="disabled",
                               bg=C["log_bg"], fg="#EBEBF5",
                               padx=12, pady=8, wrap="word")
        self.log_box.pack(side="left", fill="both", expand=True)

        log_sb = ttk.Scrollbar(log_inner, orient="vertical",
                               command=self.log_box.yview)
        log_sb.pack(side="right", fill="y")
        self.log_box.configure(yscrollcommand=log_sb.set)

        for tag, fg in [("ok",   C["log_ok"]),  ("fail", C["log_fail"]),
                        ("info", C["log_info"]), ("warn", C["log_warn"]),
                        ("dim",  C["log_dim"])]:
            self.log_box.tag_config(tag, foreground=fg)

        self._log("대기 중 — 회원 명단을 확인하고 발송 시작을 눌러주세요.", "dim")

    # ── 공통 헬퍼 ──────────────────────────────

    def _reload_members(self):
        self.members = load_members()
        self._refresh_list()
        self._update_send_count()
        self._upd_delay()
        self._log(f"🔄 명단 새로고침 — {len(self.members)}명", "info")

    def _update_send_count(self):
        n = len(self.members)
        self.send_count_lbl.config(
            text=f"📋 저장된 회원 {n}명에게 전송합니다" if n
                 else "등록된 명단이 없습니다.",
            fg=C["blue"] if n else C["warn_txt"])

    def _browse(self):
        p = filedialog.askopenfilename(
            title="이미지 선택",
            filetypes=[("이미지 파일", "*.jpg *.jpeg *.png *.bmp *.gif")])
        if p: self.image_path.set(p)

    def _on_img(self, *_):
        p = self.image_path.get()
        self.img_lbl.config(
            text=f"✅ {os.path.basename(p)}" if p else "이미지 없음",
            fg=C["green"] if p else C["sub"])

    def _upd_delay(self, _=None):
        v = self.delay_var.get()
        self.delay_lbl.config(text=f"{v:.1f}초")
        n     = len(self.members)
        total = int(v * n + n * 1.5)
        m, s  = divmod(total, 60)
        self.eta_lbl.config(text=f"({n}명 기준 약 {m}분 {s}초)" if n else "")

    def _log(self, msg, tag="info"):
        self.log_box.config(state="normal")
        self.log_box.insert("end", msg + "\n", tag)
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    # ── 발송 시작 / 중지 ───────────────────────

    def _start(self):
        self.members = load_members()
        self._update_send_count()
        self._upd_delay()

        if not self.members:
            messagebox.showwarning("명단 없음",
                "등록된 회원이 없습니다!\n'회원 명단 관리' 탭에서 먼저 추가해주세요.")
            return

        message = self.msg_text.get("1.0", "end").strip()
        image   = self.image_path.get().strip()

        if not message and not image:
            messagebox.showwarning("입력 오류", "메시지 또는 이미지를 입력해주세요!"); return
        if image and not os.path.exists(image):
            messagebox.showerror("파일 오류", "이미지 파일을 찾을 수 없습니다."); return
        if not messagebox.askyesno("발송 확인",
                f"저장된 회원 {len(self.members)}명에게 메시지를 전송할까요?\n\n"
                "⚠️ 발송 중에는 카카오톡 창 위에서\n마우스 / 키보드를 사용하지 마세요."): return

        self.is_running = True
        self.stop_flag  = False
        self.start_btn.config(state="disabled", bg=C["disabled"], fg="white")
        self.stop_btn.config(state="normal", bg=C["red"], fg="white",
                             activebackground="#B71C1C")
        self.prog_var.set(0)

        threading.Thread(
            target=self._run,
            args=(list(self.members), message, image or None, self.delay_var.get()),
            daemon=True
        ).start()

    def _stop(self):
        self.stop_flag = True
        self._log("⚠️  중지 요청 — 현재 전송 완료 후 멈춥니다.", "warn")

    def _run(self, members, message, image, delay):
        total = len(members)
        self._log(f"🚀 발송 시작 — 총 {total}명", "info")
        self._log("   5초 뒤 자동 시작됩니다. 카카오톡을 건드리지 마세요!", "warn")
        for i in range(5, 0, -1):
            self._log(f"   {i}...", "dim"); time.sleep(1)

        ok, fail = [], []

        # 검색창 최초 1회 열기
        open_search()

        for idx, name in enumerate(members, 1):
            if self.stop_flag:
                self._log(f"🛑 {idx-1}명 완료 후 중지됨.", "warn"); break

            self._log(f"[{idx}/{total}]  {name}", "info")
            result = send_one(name, message, image, first=(idx == 1))

            if result is True:
                ok.append(name);   self._log("  ✅ 전송 완료", "ok")
            else:
                fail.append(name); self._log(f"  ❌ 실패: {result}", "fail")

            self.root.after(0, lambda v=idx/total*100: self.prog_var.set(v))
            time.sleep(delay)

        # 모든 전송 후 검색창 닫기
        close_search()

        self._log("─" * 36, "dim")
        self._log(f"🎉 완료   ✅ 성공 {len(ok)}명   ❌ 실패 {len(fail)}명", "info")
        if fail:
            self._log("실패 목록:", "fail")
            for n in fail: self._log(f"  • {n}", "fail")

        self.root.after(0, self._reset)
        messagebox.showinfo("발송 완료",
            f"✅ 성공: {len(ok)}명\n❌ 실패: {len(fail)}명"
            + ("\n\n실패 목록:\n" + "\n".join(f"• {n}" for n in fail) if fail else ""))

    def _reset(self):
        self.is_running = False
        self.start_btn.config(state="normal", bg=C["yellow"], fg=C["brown"])
        self.stop_btn.config(state="disabled", bg=C["disabled"], fg="white")


# ══════════════════════════════════════════════
if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
