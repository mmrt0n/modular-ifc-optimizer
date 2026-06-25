# -*- coding: utf-8 -*-
"""
IFC 최적화 통합 파이프라인
==========================
하나의 EXE로:
  ① 설정 + IFC 파일 선택 + (선택) 교차검증 입력 — 모두 동일 Tk 세션에서 처리
  ② IFC 검증 실행 + 검증 HTML 생성
  ③ 석고보드 절단 최적화 + 최적화 HTML 생성
  ④ 브라우저로 두 결과 각 1회씩 오픈

설계 원칙:
  - Tk 충돌 방지: 모든 다이얼로그는 ProgressWindow 띄우기 *전*에 끝낸다.
  - 한 번에 입력: 사용자 입력 단계를 하나의 마법사 창에 통합.
  - 멈춤 방지: 교차검증/메모는 모두 "건너뛰기" 가능.
"""

import sys
import os
import webbrowser
import traceback

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# ── 모듈 import ──────────────────────────────────────────
import ifc_verifier        as verifier
import gypsum_optimizer_v3 as opt


def _simulator_template_path() -> str:
    """EXE / 개발 환경 모두에서 simulator_ui.html 경로를 반환."""
    # PyInstaller: sys._MEIPASS 에 번들 데이터 위치
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, 'simulator_ui.html')


# ═════════════════════════════════════════════════════════
# 1. 통합 설정 마법사 (한 창에 모든 입력 통합)
# ═════════════════════════════════════════════════════════
def run_setup_wizard():
    """
    설정 + IFC 파일 + 교차검증 기준값을 한 창에서 모두 입력.
    반환: dict 또는 None (취소)
    """
    state = {
        'ifc_path': None,
        'mat':      '석고보드',
        'ply':      2,
        'reuse':    True,
        'expected': None,
    }

    root = tk.Tk()
    root.title("IFC 최적화 — 설정")
    root.resizable(False, False)
    root.configure(bg="#f0f4f8")

    # ───── 헤더 ─────
    tk.Label(root, text="🏗  IFC 최적화 통합 파이프라인",
             font=("맑은 고딕", 15, "bold"),
             bg="#f0f4f8", fg="#1a237e").pack(pady=(18, 2))
    tk.Label(root,
             text="① 옵션 선택  →  ② IFC 파일 선택  →  ③ (선택) 교차검증 기준값 입력  →  실행",
             font=("맑은 고딕", 9),
             bg="#f0f4f8", fg="#546e7a").pack(pady=(0, 14))

    # ───── 노트북 (탭) ─────
    nb = ttk.Notebook(root)
    nb.pack(fill="x", padx=20, pady=4)

    # ───── 탭 1: 시공 옵션 ─────
    tab_opt = tk.Frame(nb, bg="#f0f4f8", padx=16, pady=12)
    nb.add(tab_opt, text="  ① 시공 옵션  ")

    reuse_var = tk.BooleanVar(value=state['reuse'])

    def _radio_group(parent, title, color, var, options, r):
        lf = tk.LabelFrame(parent, text=f"  {title}  ",
                           font=("맑은 고딕", 9, "bold"),
                           bg="#f0f4f8", fg=color, padx=10, pady=4)
        lf.grid(row=r, column=0, sticky="ew", pady=4)
        for val, lbl in options:
            tk.Radiobutton(lf, text=lbl, variable=var, value=val,
                           font=("맑은 고딕", 10),
                           bg="#f0f4f8", activebackground="#f0f4f8"
                           ).pack(anchor="w", pady=1)

    tk.Label(tab_opt,
             text="자재(석고보드/합판)와 시공 겹수(1P/2P)는 HTML에서 자유롭게 전환 가능합니다.",
             font=("맑은 고딕", 9), bg="#f0f4f8", fg="#546e7a"
             ).grid(row=0, column=0, sticky="w", pady=(0, 6))

    tab_opt.columnconfigure(0, weight=1)
    _radio_group(tab_opt, "자투리 재사용", "#2e7d32", reuse_var, [
        (True,  "활성  (자투리 폭≥300mm, 높이≥450mm 재활용)"),
        (False, "비활성  (순수 신규 보드 수량만)"),
    ], 1)

    # ───── 탭 2: IFC 파일 ─────
    tab_ifc = tk.Frame(nb, bg="#f0f4f8", padx=16, pady=14)
    nb.add(tab_ifc, text="  ② IFC 파일  ")

    ifc_label = tk.Label(tab_ifc, text="(선택되지 않음)",
                         font=("맑은 고딕", 10),
                         bg="#fff", fg="#444",
                         relief="solid", bd=1, anchor="w",
                         padx=8, pady=8, width=48)
    ifc_label.grid(row=0, column=0, padx=4, pady=4, sticky="ew")

    def pick_ifc():
        p = filedialog.askopenfilename(
            title="IFC 파일 선택",
            filetypes=[("IFC files", "*.ifc"), ("All files", "*.*")]
        )
        if p:
            state['ifc_path'] = p
            ifc_label.config(text=os.path.basename(p), fg="#1565c0")
            _update_status()

    tk.Button(tab_ifc, text=" 📂 IFC 파일 선택... ",
              font=("맑은 고딕", 10, "bold"),
              bg="#1565c0", fg="white", relief="flat",
              padx=14, pady=6, cursor="hand2",
              command=pick_ifc).grid(row=1, column=0, pady=8)

    tk.Label(tab_ifc, text="*.ifc 파일을 선택해 주세요 (IFC2x3 / IFC4 지원)",
             font=("맑은 고딕", 9), bg="#f0f4f8", fg="#777"
             ).grid(row=2, column=0, pady=(0, 4))

    # ───── 탭 3: 교차검증 (선택) ─────
    tab_cv = tk.Frame(nb, bg="#f0f4f8", padx=16, pady=10)
    nb.add(tab_cv, text="  ③ 교차검증 (선택)  ")

    tk.Label(tab_cv,
             text="도면 / 설계 시방서 기준값을 입력하면 IFC 추출값과 비교합니다. (빈 칸은 무시)",
             font=("맑은 고딕", 9), bg="#f0f4f8", fg="#546e7a"
             ).grid(row=0, column=0, columnspan=4, pady=(0, 8), sticky="w")

    cv_fields = [
        ("총 층수",          "n_storeys",    "층"),
        ("총 문(Door) 개수", "n_doors",      "개"),
        ("층별 평균 문 수",  "doors_per_fl", "개/층"),
        ("총 공간(방) 수",   "n_spaces",     "개"),
        ("총 벽 개수",       "n_walls",      "개"),
    ]
    cv_entries = {}
    for i, (label, key, unit) in enumerate(cv_fields):
        tk.Label(tab_cv, text=label, font=("맑은 고딕", 10),
                 bg="#f0f4f8", anchor="w"
                 ).grid(row=i+1, column=0, sticky="w", pady=3, padx=(0,8))
        e = tk.Entry(tab_cv, width=10, font=("맑은 고딕", 10))
        e.grid(row=i+1, column=1, pady=3, sticky="w")
        tk.Label(tab_cv, text=unit, font=("맑은 고딕", 9),
                 bg="#f0f4f8", fg="#777"
                 ).grid(row=i+1, column=2, sticky="w", padx=4)
        cv_entries[key] = e

    tk.Label(tab_cv, text="모두 비워두면 교차검증은 건너뜁니다.",
             font=("맑은 고딕", 8, "italic"),
             bg="#f0f4f8", fg="#999"
             ).grid(row=len(cv_fields)+1, column=0, columnspan=3,
                    pady=(8, 0), sticky="w")

    # ───── 상태 표시 + 버튼 ─────
    bottom = tk.Frame(root, bg="#f0f4f8")
    bottom.pack(fill="x", padx=20, pady=14)

    status_label = tk.Label(bottom,
                            text="❗ IFC 파일을 선택해 주세요",
                            font=("맑은 고딕", 9, "bold"),
                            bg="#f0f4f8", fg="#c62828")
    status_label.pack(side="left")

    def _update_status():
        if state['ifc_path']:
            status_label.config(text="✅ 준비 완료 — 실행 버튼을 눌러주세요",
                                fg="#2e7d32")
            run_btn.config(state="normal", bg="#2e7d32")
        else:
            status_label.config(text="❗ IFC 파일을 선택해 주세요", fg="#c62828")
            run_btn.config(state="disabled", bg="#bdbdbd")

    def on_run():
        if not state['ifc_path']:
            messagebox.showwarning("선택 필요", "IFC 파일을 먼저 선택해 주세요.")
            nb.select(tab_ifc)
            return

        state['reuse'] = reuse_var.get()

        # 교차검증 기준값
        exp = {}
        for key, ent in cv_entries.items():
            v = ent.get().strip()
            if v:
                try:
                    exp[key] = float(v)
                except ValueError:
                    pass
        state['expected'] = exp if exp else None

        root.destroy()

    def on_cancel():
        state['ifc_path'] = None
        root.destroy()

    run_btn = tk.Button(bottom, text="  ▶  실행  ",
                       font=("맑은 고딕", 11, "bold"),
                       bg="#bdbdbd", fg="white", relief="flat",
                       padx=20, pady=8, cursor="hand2",
                       state="disabled", command=on_run)
    run_btn.pack(side="right", padx=4)
    tk.Button(bottom, text="취소",
              font=("맑은 고딕", 10),
              bg="#e0e0e0", fg="#333", relief="flat",
              padx=14, pady=8, cursor="hand2",
              command=on_cancel).pack(side="right", padx=4)

    root.update_idletasks()
    root.eval('tk::PlaceWindow . center')
    root.protocol("WM_DELETE_WINDOW", on_cancel)
    root.mainloop()

    if not state['ifc_path']:
        return None
    return state


# ═════════════════════════════════════════════════════════
# 2. 진행 창 (실행 단계 표시)
# ═════════════════════════════════════════════════════════
class ProgressWindow:
    STEPS = [
        "① IFC 파일 로딩",
        "② 데이터 추출 (벽/문/공간/층)",
        "③ 이중검증 — 정규식 신뢰도 확인",
        "④ 검증 체크 실행",
        "⑤ 교차검증 비교",
        "⑥ 검증 보고서 HTML 생성",
        "⑦ 석고보드 최적화 계산",
        "⑧ 최적화 결과 HTML 생성",
        "⑨ 시뮬레이터 UI 생성",
        "✅ 완료 — 브라우저 오픈",
    ]

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("IFC 최적화 — 실행 중")
        self.root.geometry("540x300")
        self.root.resizable(False, False)
        self.root.configure(bg="#f5f5f5")

        tk.Label(self.root, text="🛠  IFC 최적화 실행 중",
                 font=("맑은 고딕", 13, "bold"),
                 bg="#f5f5f5", fg="#1a237e").pack(pady=(14, 6))

        self._detail = tk.Label(self.root, text="",
                                font=("맑은 고딕", 9),
                                bg="#f5f5f5", fg="#666")
        self._detail.pack()

        # 단계 목록
        list_frame = tk.Frame(self.root, bg="#fff", relief="solid", bd=1)
        list_frame.pack(fill="both", expand=True, padx=24, pady=12)

        self._step_labels = []
        for s in self.STEPS:
            lbl = tk.Label(list_frame, text=f"   ◯  {s}",
                           font=("맑은 고딕", 10),
                           bg="#fff", fg="#999", anchor="w")
            lbl.pack(fill="x", padx=12, pady=2)
            self._step_labels.append(lbl)

        self._bar = ttk.Progressbar(self.root, length=480,
                                    mode="determinate", maximum=len(self.STEPS))
        self._bar.pack(pady=(0, 12))

        self.root.eval('tk::PlaceWindow . center')
        self.root.update()

    def step(self, idx: int, detail: str = ""):
        for i, lbl in enumerate(self._step_labels):
            if i < idx:
                lbl.config(text=f"   ✔  {self.STEPS[i]}", fg="#2e7d32")
            elif i == idx:
                lbl.config(text=f"   ▶  {self.STEPS[i]}", fg="#1565c0",
                           font=("맑은 고딕", 10, "bold"))
            else:
                lbl.config(text=f"   ◯  {self.STEPS[i]}", fg="#999",
                           font=("맑은 고딕", 10))
        self._detail.config(text=detail)
        self._bar["value"] = idx
        self.root.update()

    def done(self, detail: str = "두 결과를 브라우저에서 확인하세요."):
        for i, lbl in enumerate(self._step_labels):
            lbl.config(text=f"   ✔  {self.STEPS[i]}", fg="#2e7d32")
        self._detail.config(text=detail, fg="#2e7d32",
                            font=("맑은 고딕", 10, "bold"))
        self._bar["value"] = len(self.STEPS)
        self.root.update()

    def close(self):
        try:
            self.root.destroy()
        except Exception:
            pass


# ═════════════════════════════════════════════════════════
# 3. 검증 데이터 → 최적화 입력 변환
# ═════════════════════════════════════════════════════════
def _fix_unit(v: float, ref: float) -> float:
    """단위 추정 보정: v가 ref의 10배 이상이면 mm→m 환산 오류로 보고 /1000."""
    if v is None:
        return 0.0
    v = abs(float(v))
    if ref > 0 and v > ref * 10:
        v = v / 1000.0
    return v


def _walls_for_optimizer(verifier_walls: list) -> list:
    """검증 결과 → optimizer 입력 변환. 개구부 좌표 단위 오류 자동 보정."""
    result = []
    skipped_unit = 0
    seen_ids: dict = {}  # wall_id → count (중복 이름에 고유 suffix 부여)
    for w in verifier_walls:
        if not w.get('dims_ok') or not w.get('L_mm') or not w.get('H_mm'):
            continue
        L = float(w['L_mm']); H = float(w['H_mm'])

        ops = []
        for op in w.get('openings', []):
            if not (op.get('ow') and op.get('oh') and op.get('ox') is not None):
                continue
            ox = _fix_unit(op.get('ox'), L)
            oy = _fix_unit(op.get('oy'), H)
            ow = _fix_unit(op.get('ow'), L)
            oh = _fix_unit(op.get('oh'), H)
            # 보정 후에도 비정상 (벽 범위 초과)이면 스킵
            if ox > L or ox + ow > L + 1 or oy + oh > H + 1:
                skipped_unit += 1
                continue
            ops.append({'ox': ox, 'ow': ow, 'oh': oh, 'oy': oy})

        base_id = w.get('name') or w.get('id', '?')
        if base_id in seen_ids:
            seen_ids[base_id] += 1
            wall_id = f"{base_id}_{seen_ids[base_id]}"
        else:
            seen_ids[base_id] = 1
            wall_id = base_id

        result.append({
            'wall_id':     wall_id,
            'space_id':    w.get('space') or 'unknown',
            'floor_id':    w.get('storey') or 'unknown',
            'L':           L,
            'H':           H,
            'is_external': w.get('is_external') == 'EXTERNAL',
            'openings':    ops,
        })
    if skipped_unit:
        print(f"  ⚠ 개구부 {skipped_unit}개: 좌표 범위 이상으로 무시")
    return result


# ═════════════════════════════════════════════════════════
# 4. 메인
# ═════════════════════════════════════════════════════════
def main():
    # ── STEP 0: 통합 마법사 ─────────────────────────────
    settings = run_setup_wizard()
    if not settings:
        sys.exit(0)

    ifc_path = settings['ifc_path']
    mat      = settings['mat']
    reuse    = settings['reuse']
    ply      = settings['ply']
    expected = settings['expected']

    if not os.path.exists(ifc_path):
        root = tk.Tk(); root.withdraw()
        messagebox.showerror("오류", f"파일을 찾을 수 없습니다:\n{ifc_path}")
        root.destroy()
        sys.exit(1)

    # 옵션 → optimizer 모듈에 반영
    if mat == "합판":
        opt.BW = 1220; opt.BH = 2440
    else:
        opt.BW = 900;  opt.BH = 1800

    opt.IS_2P = (ply >= 2)
    if not reuse:
        opt.MIN_REUSE_W = 99999
        opt.MIN_REUSE_H = 99999
    else:
        opt.MIN_REUSE_W = 300
        opt.MIN_REUSE_H = 450

    base     = os.path.splitext(ifc_path)[0]
    ifc_name = os.path.basename(ifc_path)
    prog     = ProgressWindow()

    verify_html = None
    opt_html    = None
    n_err = n_warn = 0
    results = []
    total_loss = 0.0
    dual = None

    try:
        import ifcopenshell

        # ── STEP 1: 로딩 ──────────────────────────────
        prog.step(0, ifc_name)
        ifc = ifcopenshell.open(ifc_path)

        # ── STEP 2: 데이터 추출 ───────────────────────
        prog.step(1, f"스키마: {ifc.schema}")
        data = verifier.extract_all(ifc)

        # ── STEP 3: 이중검증 (정규식 vs ifcopenshell) ─
        prog.step(2, "정규식으로 IFC 텍스트 직접 파싱 중...")
        regex_data = verifier.extract_by_regex(ifc_path)
        dual = verifier.dual_verify(data, regex_data)
        n_match = sum(1 for r in dual if r['match'])
        print(f"  이중검증: {n_match}/{len(dual)} 항목 일치"
              + (" — 신뢰" if n_match == len(dual) else " — 불일치 있음"))

        # ── STEP 4: 검증 체크 ─────────────────────────
        prog.step(3, "ERROR/WARNING/INFO 분류 중...")
        issues = verifier.run_checks(data)
        n_err  = sum(1 for i in issues if i['severity'] == 'ERROR')
        n_warn = sum(1 for i in issues if i['severity'] == 'WARNING')
        n_info = sum(1 for i in issues if i['severity'] == 'INFO')

        # ── STEP 5: 교차검증 ──────────────────────────
        prog.step(4,
                  f"입력값 비교 ({len(expected) if expected else 0}개 항목)"
                  if expected else "건너뜀 (입력 없음)")
        cross = verifier.run_cross_checks(data, expected) if expected else []

        # ── STEP 6: 검증 HTML 생성 ────────────────────
        prog.step(5, f"ERROR {n_err} / WARNING {n_warn} / INFO {n_info}")
        verify_html = base + "_검증보고서.html"
        html_txt = verifier.make_html(data, ifc_path, issues, cross, expected, dual=dual)
        with open(verify_html, "w", encoding="utf-8") as f:
            f.write(html_txt)
        print(f"✓ 검증 HTML 저장: {verify_html}")

        # ── STEP 7: 최적화 계산 (석고1P·2P / 합판1P·2P 4조합) ──
        opt_walls = _walls_for_optimizer(data['walls'])
        if not opt_walls:
            raise RuntimeError(
                "최적화 가능한 벽이 없습니다 (모든 벽의 치수 추출 실패).")

        _COMBOS = [
            ('gyp1',  900, 1800, False),
            ('gyp2',  900, 1800, True),
            ('ply1', 1220, 2440, False),
            ('ply2', 1220, 2440, True),
        ]
        default_key = ('ply' if mat == '합판' else 'gyp') + str(ply)

        prog.step(6, f"{len(opt_walls)}개 벽 × 4조합 계산 중 — 재사용={'활성' if reuse else '비활성'}")
        all_opt_results = {}
        results = []
        total_loss = 0.0
        for cfg_key, bw, bh, is_2p in _COMBOS:
            opt.BW = bw; opt.BH = bh; opt.IS_2P = is_2p
            r, loss = opt.optimize_building(opt_walls)
            all_opt_results[cfg_key] = r
            if cfg_key == default_key:
                results = r
                total_loss = loss

        # globals 원래 선택으로 복원
        opt.BW = 1220 if mat == '합판' else 900
        opt.BH = 2440 if mat == '합판' else 1800
        opt.IS_2P = (ply >= 2)

        # ── STEP 8: 최적화 HTML 생성 ──────────────────
        prog.step(7, f"전체 로스율 {total_loss:.2f}% / "
                     f"온장 {sum(r['boards'] for r in results)}장")
        suffix   = "" if reuse else "_노재사용"
        opt_html = base + f"_{mat}_{ply}P최적화결과{suffix}.html"
        opt_txt  = opt.make_opt_html(results, total_loss, ifc_path, mat, ply)
        with open(opt_html, "w", encoding="utf-8") as f:
            f.write(opt_txt)
        print(f"✓ 최적화 HTML 저장: {opt_html}")

        # ── STEP 9: 시뮬레이터 UI HTML 생성 ──────────
        sim_html = None
        prog.step(8, f"벽 {len(data['walls'])}개 UI 데이터 주입 중 (4조합 사전계산)...")
        try:
            tmpl = _simulator_template_path()
            sim_walls = verifier.export_simulator_walls(data['walls'])
            # opt_walls와 wall_id 일치: 같은 중복제거 적용
            _seen2: dict = {}
            for _sw in sim_walls:
                _base = _sw['wall_id']
                if _base in _seen2:
                    _seen2[_base] += 1
                    _sw['wall_id'] = f"{_base}_{_seen2[_base]}"
                else:
                    _seen2[_base] = 1
            sim_txt   = opt.make_simulator_html(
                sim_walls, ifc_name, tmpl,
                opt_results_all=all_opt_results,
                default_key=default_key)
            sim_html  = base + "_시뮬레이터.html"
            with open(sim_html, "w", encoding="utf-8") as f:
                f.write(sim_txt)
            print(f"✓ 시뮬레이터 HTML 저장: {sim_html}")
        except Exception as e_sim:
            print(f"  ⚠ 시뮬레이터 UI 생성 실패 (계속 진행): {e_sim}")

        # ── STEP 10: 브라우저 오픈 ────────────────────
        prog.step(9, "브라우저에서 결과를 확인하세요...")
        # new=2: 각 파일을 새 탭으로 열어 file:// cross-origin 경고 방지
        webbrowser.open(verify_html, new=2)
        if sim_html:
            webbrowser.open(sim_html, new=2)
        else:
            webbrowser.open(opt_html, new=2)
        prog.done()

    except Exception as e:
        prog.close()
        root = tk.Tk(); root.withdraw()
        messagebox.showerror(
            "오류 발생",
            f"{type(e).__name__}: {e}\n\n{traceback.format_exc()[-700:]}")
        root.destroy()
        sys.exit(1)

    prog.close()

    # ── 완료 요약 다이얼로그 ───────────────────────
    summary = tk.Tk()
    summary.title("✅ IFC 최적화 완료")
    summary.resizable(False, False)
    summary.configure(bg="#f0f4f8")

    tk.Label(summary, text="✅  분석 완료",
             font=("맑은 고딕", 15, "bold"),
             bg="#f0f4f8", fg="#2e7d32").pack(pady=(18, 4))

    info = (
        f"파일:    {ifc_name}\n"
        f"자재:    {mat}  /  {ply}P 시공  /  재사용 {'활성' if reuse else '비활성'}\n\n"
        f"① IFC 검증:  ERROR {n_err}건  /  WARNING {n_warn}건\n"
        f"② 최적화:    {len(results)}개 벽  /  로스율 {total_loss:.2f}%\n"
        f"             온장 {sum(r['boards'] for r in results)}장  /  "
        f"재사용 {sum(r['reuse_in'] for r in results)}장"
    )
    tk.Label(summary, text=info, font=("맑은 고딕", 10),
             bg="#f0f4f8", fg="#333", justify="left",
             padx=24).pack(pady=(0, 12))

    fnames = tk.Frame(summary, bg="#fff", relief="solid", bd=1)
    fnames.pack(fill="x", padx=24, pady=(0, 10))
    tk.Label(fnames, text=f"📄 검증보고서:  {os.path.basename(verify_html)}",
             font=("맑은 고딕", 9), bg="#fff", fg="#1565c0",
             anchor="w", padx=10, pady=6).pack(fill="x")
    if sim_html:
        tk.Label(fnames, text=f"🏗 시뮬레이터:  {os.path.basename(sim_html)}",
                 font=("맑은 고딕", 9), bg="#fff", fg="#2e7d32",
                 anchor="w", padx=10, pady=6).pack(fill="x")
    tk.Label(fnames, text=f"📊 최적화결과:  {os.path.basename(opt_html)}",
             font=("맑은 고딕", 9), bg="#fff", fg="#6a1b9a",
             anchor="w", padx=10, pady=6).pack(fill="x")

    tk.Button(summary, text=" 닫기 ",
              font=("맑은 고딕", 10, "bold"),
              bg="#1565c0", fg="white", relief="flat",
              padx=18, pady=6, cursor="hand2",
              command=summary.destroy).pack(pady=10)

    summary.update_idletasks()
    summary.eval('tk::PlaceWindow . center')
    summary.mainloop()


if __name__ == "__main__":
    main()
