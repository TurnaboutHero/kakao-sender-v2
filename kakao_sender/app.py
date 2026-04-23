"""Tk UI — 명단 / 매핑 / 발송 / 로그 4탭.

원본 `App` 클래스를 분해해 탭별 클래스로 나누고, 발송 워커 스레드에서 Tk 위젯을
직접 건드리는 스레드 안전 이슈를 `root.after()` 마샬링으로 해결했다.

엔진(`engine.send`)과 명단(`roster`)을 완전히 분리 호출한다.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import ClassVar

from kakao_sender import config, logging_setup
from kakao_sender.engine.kakao import KakaoClient, KakaoError
from kakao_sender.engine.send import (
    SendController,
    SendJob,
    SendOutcome,
    SendResult,
    retry_failed,
    send_batch,
)
from kakao_sender.roster import (
    Mapping,
    Member,
    load_mapping,
    load_members,
    load_members_from_legacy_json,
    save_mapping,
    save_members,
)
from kakao_sender.roster.store import sorted_by_name

_log = logging_setup.get_logger(__name__)


# ── 테마 ──────────────────────────────────────

COLORS = {
    "yellow": "#FEE500",
    "brown": "#3C1E1E",
    "bg": "#F5F5F5",
    "white": "#FFFFFF",
    "border": "#E0E0E0",
    "txt": "#1A1A1A",
    "sub": "#888888",
    "green": "#2E7D32",
    "green_bg": "#E8F5E9",
    "red": "#C62828",
    "red_bg": "#FFEBEE",
    "blue": "#1565C0",
    "blue_bg": "#E3F2FD",
    "warn_bg": "#FFF8E1",
    "warn_txt": "#F57F17",
    "log_bg": "#1C1C1E",
    "log_ok": "#30D158",
    "log_fail": "#FF453A",
    "log_info": "#EBEBF5",
    "log_dim": "#8E8E93",
    "log_warn": "#FF9F0A",
    "disabled": "#BDBDBD",
}


def F(size: int, bold: bool = False) -> tuple:
    return ("맑은 고딕", size, "bold" if bold else "normal")


# ── 스레드 안전 로그 핸들러 ───────────────────


class _TkTextHandler(logging.Handler):
    """워커 스레드에서 emit() 호출이 들어와도 UI 조작은 메인 스레드에서 하도록 마샬링."""

    LEVEL_TAG: ClassVar[dict[int, str]] = {
        logging.DEBUG: "dim",
        logging.INFO: "info",
        logging.WARNING: "warn",
        logging.ERROR: "fail",
        logging.CRITICAL: "fail",
    }

    def __init__(self, text_widget: tk.Text, *, root: tk.Misc) -> None:
        super().__init__()
        self.text = text_widget
        self.root = root

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            return
        tag = self.LEVEL_TAG.get(record.levelno, "info")
        self.root.after(0, self._append, msg, tag)

    def _append(self, msg: str, tag: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", msg + "\n", tag)
        self.text.see("end")
        self.text.configure(state="disabled")


# ── 명단 탭 ───────────────────────────────────


class MembersTab(tk.Frame):
    def __init__(self, parent: tk.Widget, app: App) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        self.app = app
        self._build()
        self.refresh()

    def _build(self) -> None:
        top = tk.Frame(self, bg=COLORS["bg"])
        top.pack(fill="x", padx=14, pady=(14, 6))

        tk.Label(
            top,
            text="📋 회원 명단 (CSV)",
            font=F(11, True),
            bg=COLORS["bg"],
            fg=COLORS["txt"],
        ).pack(side="left")

        self.count_lbl = tk.Label(top, text="", font=F(9), bg=COLORS["bg"], fg=COLORS["sub"])
        self.count_lbl.pack(side="right")

        # 툴바
        bar = tk.Frame(self, bg=COLORS["bg"])
        bar.pack(fill="x", padx=14, pady=(0, 6))
        _btn(bar, "➕ 추가", self._add_member, "yellow").pack(side="left", padx=(0, 4))
        _btn(bar, "✏️ 수정", self._edit_member, "blue_bg", "blue").pack(side="left", padx=4)
        _btn(bar, "🗑️ 삭제", self._delete_member, "red_bg", "red").pack(side="left", padx=4)
        _btn(bar, "📂 legacy .json 불러오기", self._import_legacy, "blue_bg", "blue").pack(
            side="right", padx=(4, 0)
        )
        _btn(bar, "💾 저장", self._save, "green_bg", "green").pack(side="right")

        # Treeview
        cols = ("name", "group", "birthday", "phone_last4", "active")
        frame = tk.Frame(self, bg=COLORS["border"], padx=1, pady=1)
        frame.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        self.tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("name", text="이름")
        self.tree.heading("group", text="요회")
        self.tree.heading("birthday", text="생일")
        self.tree.heading("phone_last4", text="연락처 끝4")
        self.tree.heading("active", text="활성")
        self.tree.column("name", width=130, anchor="w")
        self.tree.column("group", width=80, anchor="w")
        self.tree.column("birthday", width=70, anchor="center")
        self.tree.column("phone_last4", width=90, anchor="center")
        self.tree.column("active", width=60, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-Button-1>", lambda e: self._edit_member())

    # ── 데이터 연결 ───────────────────────────

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        members = sorted_by_name(self.app.members)
        for m in members:
            self.tree.insert(
                "",
                "end",
                iid=m.id,
                values=(m.name, m.group, m.birthday, m.phone_last4, "✓" if m.active else "—"),
            )
        active_cnt = sum(1 for m in self.app.members if m.active)
        self.count_lbl.config(
            text=f"활성 {active_cnt} / 전체 {len(self.app.members)}",
            fg=COLORS["green"] if active_cnt else COLORS["sub"],
        )

    # ── 액션 ───────────────────────────────────

    def _selected_id(self) -> str | None:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _add_member(self) -> None:
        name = simpledialog.askstring("회원 추가", "이름:", parent=self.app.root)
        if not name:
            return
        name = name.strip()
        if any(m.name == name for m in self.app.members):
            messagebox.showwarning("중복", f"'{name}'은 이미 등록됨.")
            return
        group = (
            simpledialog.askstring("회원 추가", "요회/캠퍼스 (선택):", parent=self.app.root) or ""
        )
        birthday = (
            simpledialog.askstring(
                "회원 추가", "생일 (MM-DD 또는 YYYY-MM-DD, 선택):", parent=self.app.root
            )
            or ""
        )
        self.app.members.append(Member(name=name, group=group, birthday=birthday))
        self.refresh()

    def _edit_member(self) -> None:
        mid = self._selected_id()
        if not mid:
            return
        m = next((x for x in self.app.members if x.id == mid), None)
        if m is None:
            return
        new_name = simpledialog.askstring(
            "이름 수정", "이름:", initialvalue=m.name, parent=self.app.root
        )
        if new_name is None:
            return
        new_name = new_name.strip()
        if not new_name:
            messagebox.showwarning("오류", "이름은 비울 수 없음.")
            return
        new_group = (
            simpledialog.askstring("요회 수정", "요회:", initialvalue=m.group, parent=self.app.root)
            or ""
        )
        new_birthday = (
            simpledialog.askstring(
                "생일 수정", "생일 (MM-DD):", initialvalue=m.birthday, parent=self.app.root
            )
            or ""
        )
        m.name = new_name
        m.group = new_group
        m.birthday = new_birthday
        self.refresh()

    def _delete_member(self) -> None:
        ids = list(self.tree.selection())
        if not ids:
            return
        if not messagebox.askyesno("삭제", f"{len(ids)}명 삭제할까요?"):
            return
        self.app.members = [m for m in self.app.members if m.id not in set(ids)]
        self.refresh()

    def _import_legacy(self) -> None:
        path = filedialog.askopenfilename(
            title="원본 members.json 선택",
            filetypes=[("JSON", "*.json"), ("모든 파일", "*.*")],
        )
        if not path:
            return
        try:
            imported = load_members_from_legacy_json(Path(path))
        except Exception as e:
            messagebox.showerror("오류", f"가져오기 실패: {e}")
            return
        existing_names = {m.name for m in self.app.members}
        added = [m for m in imported if m.name not in existing_names]
        self.app.members.extend(added)
        self.refresh()
        messagebox.showinfo(
            "완료", f"{len(added)}명 추가 (중복 {len(imported) - len(added)}명 스킵)"
        )

    def _save(self) -> None:
        save_members(self.app.members)
        messagebox.showinfo("저장 완료", f"{len(self.app.members)}명 저장됨\n{config.ROSTER_CSV}")


# ── 매핑 탭 ───────────────────────────────────


class MappingTab(tk.Frame):
    def __init__(self, parent: tk.Widget, app: App) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        self.app = app
        self._entries: dict[str, tk.StringVar] = {}  # member_id → StringVar
        self._build()
        self.refresh()

    def _build(self) -> None:
        top = tk.Frame(self, bg=COLORS["bg"])
        top.pack(fill="x", padx=14, pady=(14, 6))
        tk.Label(
            top,
            text="🔗 매핑 — 정식명 → 담당자 카톡 저장명",
            font=F(11, True),
            bg=COLORS["bg"],
            fg=COLORS["txt"],
        ).pack(side="left")

        self.progress_lbl = tk.Label(top, text="", font=F(9), bg=COLORS["bg"], fg=COLORS["sub"])
        self.progress_lbl.pack(side="right")

        notice = tk.Label(
            self,
            text="ℹ️ 담당자 카톡에 저장된 이름 그대로 입력. 비어있으면 발송 대상에서 제외됨.",
            font=F(8),
            bg=COLORS["warn_bg"],
            fg=COLORS["warn_txt"],
            padx=10,
            pady=6,
            anchor="w",
        )
        notice.pack(fill="x", padx=14)

        bar = tk.Frame(self, bg=COLORS["bg"])
        bar.pack(fill="x", padx=14, pady=(6, 6))
        _btn(bar, "💾 매핑 저장", self._save, "green_bg", "green").pack(side="right")
        _btn(bar, "동명 자동 채우기", self._auto_fill, "blue_bg", "blue").pack(
            side="right", padx=(0, 6)
        )

        # 스크롤 가능한 영역
        outer = tk.Frame(self, bg=COLORS["border"], padx=1, pady=1)
        outer.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        canvas = tk.Canvas(outer, bg=COLORS["white"], highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        sb.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=sb.set)

        self._inner = tk.Frame(canvas, bg=COLORS["white"])
        self._inner_window = canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._canvas = canvas
        self._inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind(
            "<Configure>", lambda e: canvas.itemconfigure(self._inner_window, width=e.width)
        )
        # 마우스 휠 스크롤
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

    def refresh(self) -> None:
        for child in self._inner.winfo_children():
            child.destroy()
        self._entries.clear()

        members = sorted_by_name(self.app.members)
        for m in members:
            rowframe = tk.Frame(self._inner, bg=COLORS["white"])
            rowframe.pack(fill="x", padx=8, pady=2)

            # 정식명 + 요회
            label_text = f"  {m.name}" + (f"  ({m.group})" if m.group else "")
            tk.Label(
                rowframe,
                text=label_text,
                font=F(10),
                bg=COLORS["white"],
                fg=COLORS["txt"],
                width=22,
                anchor="w",
            ).pack(side="left")

            # 카톡 이름 입력
            current = self.app.mapping.get(m.id)
            var = tk.StringVar(value=current.kakao_name if current else "")
            self._entries[m.id] = var

            entry = tk.Entry(
                rowframe,
                textvariable=var,
                font=F(10),
                bg=COLORS["white"],
                fg=COLORS["txt"],
                relief="solid",
                bd=1,
                highlightbackground=COLORS["border"],
            )
            entry.pack(side="left", fill="x", expand=True, ipady=2, padx=(4, 4))

            # 상태 라벨
            state = tk.Label(
                rowframe,
                text=_mapping_state_label(var.get()),
                font=F(8),
                bg=COLORS["white"],
                fg=_mapping_state_color(var.get()),
                width=8,
            )
            state.pack(side="left")
            var.trace_add(
                "write",
                lambda *_, v=var, s=state: s.config(
                    text=_mapping_state_label(v.get()),
                    fg=_mapping_state_color(v.get()),
                ),
            )

        self._update_progress()

    def _update_progress(self) -> None:
        total = len(self.app.members)
        mapped = sum(1 for v in self._entries.values() if v.get().strip())
        self.progress_lbl.config(
            text=f"매핑 {mapped} / {total}",
            fg=COLORS["green"] if mapped == total else COLORS["warn_txt"],
        )

    def _auto_fill(self) -> None:
        """공식명과 카톡명이 같다고 가정하고 빈 칸만 채운다 (수정 전 초안용)."""
        filled = 0
        for m in self.app.members:
            var = self._entries.get(m.id)
            if var and not var.get().strip():
                var.set(m.name)
                filled += 1
        self._update_progress()
        messagebox.showinfo(
            "자동 채우기", f"{filled}개 자동 입력. 담당자가 검토·수정 후 저장하세요."
        )

    def _save(self) -> None:
        for mid, var in self._entries.items():
            name = var.get().strip()
            if name:
                # 기존 값과 같으면 verified_at 유지
                existing = self.app.mapping.get(mid)
                if existing and existing.kakao_name == name:
                    continue
                self.app.mapping.set(mid, name)
            else:
                self.app.mapping.remove(mid)
        save_mapping(self.app.mapping)
        self._update_progress()
        messagebox.showinfo("저장 완료", f"{len(self.app.mapping)}건 저장됨")


def _mapping_state_label(val: str) -> str:
    return "✓ 매핑됨" if val.strip() else "미매핑"


def _mapping_state_color(val: str) -> str:
    return COLORS["green"] if val.strip() else COLORS["red"]


# ── 발송 탭 ───────────────────────────────────


@dataclass(slots=True)
class _SendUIState:
    is_running: bool = False
    last_results: list[SendResult] | None = None


class SendTab(tk.Frame):
    def __init__(self, parent: tk.Widget, app: App) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        self.app = app
        self.state = _SendUIState()
        self.controller: SendController | None = None
        self.thread: threading.Thread | None = None
        self.image_var = tk.StringVar()
        self.use_variants_var = tk.BooleanVar(value=False)
        self.excluded_ids: set[str] = set()
        self._build()

    def _build(self) -> None:
        # 메시지
        tk.Label(
            self,
            text="✉️ 메시지",
            font=F(10, True),
            bg=COLORS["bg"],
            fg=COLORS["sub"],
        ).pack(anchor="w", padx=14, pady=(14, 2))

        mf = tk.Frame(self, bg=COLORS["border"], padx=1, pady=1)
        mf.pack(fill="x", padx=14, pady=(0, 4))
        inner = tk.Frame(mf, bg=COLORS["white"])
        inner.pack(fill="x")
        self.msg_text = tk.Text(
            inner,
            height=3,
            font=F(10),
            relief="flat",
            bd=0,
            bg=COLORS["white"],
            fg=COLORS["txt"],
            wrap="word",
            padx=10,
            pady=8,
        )
        self.msg_text.pack(fill="x")
        self.msg_text.insert("end", "생일 축하해요 🎂")

        variant_row = tk.Frame(self, bg=COLORS["bg"])
        variant_row.pack(fill="x", padx=14, pady=(2, 8))
        tk.Checkbutton(
            variant_row,
            text=f"문구 변주 사용 ({len(config.MESSAGES.birthday_default)}가지 기본 문구를 랜덤)",
            variable=self.use_variants_var,
            font=F(9),
            bg=COLORS["bg"],
            fg=COLORS["sub"],
            selectcolor=COLORS["white"],
            activebackground=COLORS["bg"],
        ).pack(side="left")

        # 이미지
        tk.Label(
            self,
            text="🖼️ 이미지 (선택)",
            font=F(10, True),
            bg=COLORS["bg"],
            fg=COLORS["sub"],
        ).pack(anchor="w", padx=14, pady=(6, 2))
        img_row = tk.Frame(self, bg=COLORS["bg"])
        img_row.pack(fill="x", padx=14, pady=(0, 8))
        tk.Entry(
            img_row,
            textvariable=self.image_var,
            font=F(9),
            state="readonly",
            readonlybackground=COLORS["white"],
        ).pack(side="left", fill="x", expand=True, ipady=3)
        _btn(img_row, "찾기", self._pick_image, "yellow").pack(side="left", padx=(4, 2))
        _btn(img_row, "✕", lambda: self.image_var.set(""), "red_bg", "red").pack(side="left")

        # 대상 관리
        target_frame = tk.Frame(self, bg=COLORS["bg"])
        target_frame.pack(fill="x", padx=14, pady=(6, 4))
        tk.Label(
            target_frame,
            text="👥 대상 (활성 회원 중 매핑 완료된 사람)",
            font=F(10, True),
            bg=COLORS["bg"],
            fg=COLORS["sub"],
        ).pack(side="left")
        _btn(target_frame, "🚫 생일자 제외 설정", self._exclude_dialog, "blue_bg", "blue").pack(
            side="right"
        )

        self.target_summary = tk.Label(self, text="", font=F(9), bg=COLORS["bg"], fg=COLORS["txt"])
        self.target_summary.pack(fill="x", padx=14, pady=(0, 4))

        # 컨트롤 + 진행
        ctrl_row = tk.Frame(self, bg=COLORS["bg"])
        ctrl_row.pack(fill="x", padx=14, pady=(6, 4))
        self.start_btn = _btn(ctrl_row, "▶ 발송 시작", self._start, "yellow")
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.stop_btn = _btn(ctrl_row, "■ 중지", self._stop, "disabled")
        self.stop_btn.pack(side="left", fill="x", expand=True)
        self.stop_btn.config(state="disabled")
        self.retry_btn = _btn(ctrl_row, "↻ 실패자만 재시도", self._retry_failed, "red_bg", "red")
        self.retry_btn.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.retry_btn.config(state="disabled")

        self.prog_var = tk.DoubleVar(value=0)
        ttk.Progressbar(self, variable=self.prog_var, maximum=100).pack(
            fill="x", padx=14, pady=(6, 6)
        )

        self.status_lbl = tk.Label(
            self, text="대기 중", font=F(9), bg=COLORS["bg"], fg=COLORS["sub"]
        )
        self.status_lbl.pack(anchor="w", padx=14)

        self.refresh_targets()

    # ── 대상 계산 ─────────────────────────────

    def _eligible_jobs(self) -> tuple[list[SendJob], list[Member], list[Member]]:
        """현재 상태에서 발송 가능한 jobs + 스킵 이유별 분류.

        반환:
          eligible_jobs, skipped_no_map, skipped_excluded
        """
        text = self.msg_text.get("1.0", "end").strip()
        image_path = Path(self.image_var.get()) if self.image_var.get().strip() else None

        jobs: list[SendJob] = []
        no_map: list[Member] = []
        excluded: list[Member] = []

        for m in self.app.members:
            if not m.active:
                continue
            if m.id in self.excluded_ids:
                excluded.append(m)
                continue
            entry = self.app.mapping.get(m.id)
            if entry is None or not entry.kakao_name:
                no_map.append(m)
                continue
            if not text and image_path is None:
                continue
            jobs.append(
                SendJob(
                    target_name=entry.kakao_name,
                    legal_name=m.name,
                    text=text or None,
                    image_path=image_path,
                    member_id=m.id,
                )
            )
        return jobs, no_map, excluded

    def refresh_targets(self) -> None:
        jobs, no_map, excluded = self._eligible_jobs()
        self.target_summary.config(
            text=f"발송 {len(jobs)}명  |  미매핑 제외 {len(no_map)}명  |  제외 목록 {len(excluded)}명"
        )

    # ── 다이얼로그 ────────────────────────────

    def _pick_image(self) -> None:
        p = filedialog.askopenfilename(filetypes=[("이미지", "*.jpg *.jpeg *.png *.bmp *.gif")])
        if p:
            self.image_var.set(p)

    def _exclude_dialog(self) -> None:
        dialog = tk.Toplevel(self.app.root)
        dialog.title("생일자 / 제외 대상 선택")
        dialog.geometry("420x500")
        dialog.configure(bg=COLORS["bg"])

        tk.Label(
            dialog,
            text="체크한 사람은 이번 발송에서 제외됩니다.\n(생일자 본인 실수 전송 방지)",
            font=F(9),
            bg=COLORS["warn_bg"],
            fg=COLORS["warn_txt"],
            padx=10,
            pady=6,
        ).pack(fill="x")

        frame = tk.Frame(dialog, bg=COLORS["white"])
        frame.pack(fill="both", expand=True, padx=10, pady=8)
        canvas = tk.Canvas(frame, bg=COLORS["white"], highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        sb.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=sb.set)
        inner = tk.Frame(canvas, bg=COLORS["white"])
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        vars_: dict[str, tk.BooleanVar] = {}
        for m in sorted_by_name(self.app.members):
            if not m.active:
                continue
            v = tk.BooleanVar(value=m.id in self.excluded_ids)
            vars_[m.id] = v
            tk.Checkbutton(
                inner,
                text=f"{m.name}  ({m.group})" if m.group else m.name,
                variable=v,
                font=F(10),
                bg=COLORS["white"],
                anchor="w",
                activebackground=COLORS["white"],
            ).pack(fill="x", padx=8, pady=1)

        def apply_and_close() -> None:
            self.excluded_ids = {mid for mid, v in vars_.items() if v.get()}
            self.refresh_targets()
            dialog.destroy()

        btn = tk.Frame(dialog, bg=COLORS["bg"])
        btn.pack(fill="x", padx=10, pady=8)
        _btn(btn, "확인", apply_and_close, "yellow").pack(side="right")
        _btn(btn, "취소", dialog.destroy, "red_bg", "red").pack(side="right", padx=(0, 4))

    # ── 실행 ──────────────────────────────────

    def _start(self) -> None:
        if self.state.is_running:
            return
        jobs, no_map, _ = self._eligible_jobs()
        if not jobs:
            messagebox.showwarning(
                "대상 없음", "발송 대상이 없습니다. 명단/매핑/메시지를 확인하세요."
            )
            return
        if no_map and not messagebox.askyesno(
            "매핑 누락 확인",
            f"매핑 안 된 {len(no_map)}명은 자동 스킵됩니다.\n계속할까요?",
        ):
            return
        if not messagebox.askyesno(
            "발송 확인",
            f"{len(jobs)}명에게 발송합니다.\n\n"
            "⚠️ 발송 중에는 카카오톡 창을 건드리지 마세요.\n"
            "카카오톡이 친구 탭에 있는지 확인하세요.",
        ):
            return

        self._run_batch(jobs)

    def _retry_failed(self) -> None:
        if self.state.is_running or not self.state.last_results:
            return
        failed_count = sum(1 for r in self.state.last_results if r.outcome == SendOutcome.FAILED)
        if not failed_count:
            messagebox.showinfo("재시도", "실패한 대상이 없습니다.")
            return
        if not messagebox.askyesno("재시도", f"실패한 {failed_count}명을 재시도합니다."):
            return
        # 상태를 워커에 넘기기 위해 별도 스레드
        prior = list(self.state.last_results)
        self._start_worker(
            lambda client, controller, variants: retry_failed(
                client,
                prior,
                controller=controller,
                on_result=self._on_result,
                message_variants=variants,
            )
        )

    def _stop(self) -> None:
        if self.controller:
            self.controller.stop()
            self.status_lbl.config(
                text="중지 요청 — 현재 건 끝내고 멈춥니다…", fg=COLORS["warn_txt"]
            )

    def _run_batch(self, jobs: list[SendJob]) -> None:
        self._start_worker(
            lambda client, controller, variants: send_batch(
                client,
                jobs,
                controller=controller,
                on_result=self._on_result,
                message_variants=variants,
            )
        )

    def _start_worker(
        self,
        run: Callable[[KakaoClient, SendController, tuple[str, ...] | None], list[SendResult]],
    ) -> None:
        self.state.is_running = True
        self.state.last_results = []
        self.prog_var.set(0)
        self._set_running_ui(True)
        self.controller = SendController()
        variants = config.MESSAGES.birthday_default if self.use_variants_var.get() else None
        self._total_planned = 0
        self._processed = 0

        def worker() -> None:
            try:
                with KakaoClient() as client:
                    results = run(client, self.controller, variants)
                self.app.root.after(0, self._worker_done, results)
            except KakaoError as e:
                self.app.root.after(0, self._worker_failed, str(e))
            except Exception as e:
                _log.exception("worker 예외")
                self.app.root.after(0, self._worker_failed, f"{type(e).__name__}: {e}")

        self.thread = threading.Thread(target=worker, daemon=True)
        self.thread.start()
        _log.info("워커 스레드 시작")

    def _on_result(self, result: SendResult) -> None:
        # 워커 스레드에서 호출됨 → 메인으로 마샬링
        self.app.root.after(0, self._append_result, result)

    def _append_result(self, result: SendResult) -> None:
        assert self.state.last_results is not None
        self.state.last_results.append(result)
        tag = {
            SendOutcome.SUCCESS: "ok",
            SendOutcome.FAILED: "fail",
            SendOutcome.SKIPPED: "warn",
            SendOutcome.STOPPED: "warn",
        }.get(result.outcome, "info")

        display = result.job.legal_name or result.job.target_name
        if result.outcome == SendOutcome.SUCCESS:
            _log.info("✅ %s (%.1fs)", display, result.duration_sec)
        else:
            _log.warning("%s %s — %s", _outcome_icon(result.outcome), display, result.reason)

        # 진행률
        if self.state.last_results:
            # 총 예상치 = 처음 _eligible_jobs의 길이 (제외 전). 여기선 간략히 current count vs expected
            done = len(self.state.last_results)
            # 어림잡은 전체 대상 수로 나눔
            total = max(done, self._guess_total())
            self.prog_var.set(done / total * 100)
            self.status_lbl.config(text=f"{done} / {total} 처리 중")
        # 탭 갱신은 생략 (복잡도 관리)
        _ = tag  # 로그 태깅은 로그 탭에서 레벨로 자동 처리

    def _guess_total(self) -> int:
        """진행률 추정용. 정확하지 않아도 UX상 지장 없음."""
        jobs, _, _ = self._eligible_jobs()
        return len(jobs) or 1

    def _worker_done(self, results: list[SendResult]) -> None:
        self.state.is_running = False
        self._set_running_ui(False)
        succeeded = sum(1 for r in results if r.outcome == SendOutcome.SUCCESS)
        failed = sum(1 for r in results if r.outcome == SendOutcome.FAILED)
        self.prog_var.set(100)
        self.status_lbl.config(
            text=f"완료 — 성공 {succeeded}, 실패 {failed}",
            fg=COLORS["green"] if not failed else COLORS["red"],
        )
        if failed:
            self.retry_btn.config(state="normal")
        messagebox.showinfo("발송 완료", f"성공 {succeeded}명 / 실패 {failed}명")

    def _worker_failed(self, message: str) -> None:
        self.state.is_running = False
        self._set_running_ui(False)
        self.status_lbl.config(text=f"오류 — {message}", fg=COLORS["red"])
        messagebox.showerror("발송 실패", message)

    def _set_running_ui(self, running: bool) -> None:
        if running:
            self.start_btn.config(state="disabled", bg=COLORS["disabled"], fg="white")
            self.stop_btn.config(state="normal", bg=COLORS["red"], fg="white")
            self.retry_btn.config(state="disabled")
        else:
            self.start_btn.config(state="normal", bg=COLORS["yellow"], fg=COLORS["brown"])
            self.stop_btn.config(state="disabled", bg=COLORS["disabled"], fg="white")


def _outcome_icon(o: SendOutcome) -> str:
    return {
        SendOutcome.SUCCESS: "✅",
        SendOutcome.FAILED: "❌",
        SendOutcome.SKIPPED: "⏭",
        SendOutcome.STOPPED: "🛑",
    }.get(o, "•")


# ── 로그 탭 ───────────────────────────────────


class LogTab(tk.Frame):
    def __init__(self, parent: tk.Widget, app: App) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        self.app = app
        self._build()
        self._attach_handler()

    def _build(self) -> None:
        tk.Label(
            self,
            text="📋 로그",
            font=F(11, True),
            bg=COLORS["bg"],
            fg=COLORS["txt"],
        ).pack(anchor="w", padx=14, pady=(14, 4))

        frame = tk.Frame(self, bg=COLORS["border"], padx=1, pady=1)
        frame.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        self.text = tk.Text(
            frame,
            font=("Consolas", 9),
            bg=COLORS["log_bg"],
            fg=COLORS["log_info"],
            relief="flat",
            bd=0,
            padx=10,
            pady=8,
            state="disabled",
            wrap="word",
        )
        self.text.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(frame, orient="vertical", command=self.text.yview)
        sb.pack(side="right", fill="y")
        self.text.configure(yscrollcommand=sb.set)

        self.text.tag_config("ok", foreground=COLORS["log_ok"])
        self.text.tag_config("fail", foreground=COLORS["log_fail"])
        self.text.tag_config("info", foreground=COLORS["log_info"])
        self.text.tag_config("warn", foreground=COLORS["log_warn"])
        self.text.tag_config("dim", foreground=COLORS["log_dim"])

    def _attach_handler(self) -> None:
        self.handler = _TkTextHandler(self.text, root=self.app.root)
        self.handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        logging_setup.add_handler(self.handler, level=logging.INFO)


# ── 앱 쉘 ─────────────────────────────────────


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("카카오톡 개인톡 자동 발송기 v2")
        self.root.geometry("720x820")
        self.root.configure(bg=COLORS["bg"])

        config.ensure_dirs()
        self.members: list[Member] = load_members()
        self.mapping: Mapping = load_mapping()

        style = ttk.Style()
        style.theme_use("default")
        style.configure("TNotebook", background=COLORS["bg"], borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background=COLORS["border"],
            foreground=COLORS["sub"],
            font=F(10, True),
            padding=[16, 8],
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", COLORS["yellow"])],
            foreground=[("selected", COLORS["brown"])],
        )

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True)

        self.tab_members = MembersTab(nb, self)
        self.tab_mapping = MappingTab(nb, self)
        self.tab_send = SendTab(nb, self)
        self.tab_log = LogTab(nb, self)

        nb.add(self.tab_members, text="👥 명단")
        nb.add(self.tab_mapping, text="🔗 매핑")
        nb.add(self.tab_send, text="📨 발송")
        nb.add(self.tab_log, text="📋 로그")

        nb.bind("<<NotebookTabChanged>>", self._on_tab_change)

    def _on_tab_change(self, _event: tk.Event) -> None:
        self.tab_members.refresh()
        self.tab_mapping.refresh()
        self.tab_send.refresh_targets()


def run_app() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


# ── helpers ───────────────────────────────────


def _btn(
    parent: tk.Widget,
    text: str,
    cmd: Callable[[], None],
    bg_key: str = "yellow",
    fg_key: str = "brown",
) -> tk.Button:
    return tk.Button(
        parent,
        text=text,
        command=cmd,
        font=F(9, True),
        bg=COLORS[bg_key],
        fg=COLORS.get(fg_key, fg_key),
        relief="flat",
        padx=10,
        pady=5,
        cursor="hand2",
    )
