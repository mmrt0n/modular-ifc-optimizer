# -*- coding: utf-8 -*-
"""
석고보드 절단 최적화 시스템 v3 — M3시스템즈 시공방식 반영
=============================================================
v2 대비 변경사항 (M3_시공방식_확인결과.html 기준):
  1. [재사용 최소 규격] MIN_REUSE_W=300mm / MIN_REUSE_H=450mm (비대칭, 기존 450/450)
  2. [누적공차 대응]    끝(오른쪽)부터 배치 → 자투리가 왼쪽(시작점)에 위치
  3. [개구부 배치]      개구부 중심 대칭 배치 (기존: 왼쪽 or 개구부 왼쪽 기준)
  4. [합판 보강 추가]   TV·상부장 / 도어·선반 주변 12T 합판 보강 영역 마킹

변경 없음 (M3와 일치):
  - 각재 간격 450mm (STUD=450)
  - 세로(종방향) 시공 (BH=1800)
  - 1P 단겹 시공 (IS_2P=False 고정)
  - 바닥 밀착 (y=0 기준)
  - 부착 순서: 아래→위, 왼쪽→오른쪽
"""

import math
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ─────────────────────────────────────────
# 상수
# ─────────────────────────────────────────
BW            = 900    # 석고보드 너비 (mm)
BH            = 1800   # 석고보드 높이 (mm)
STUD          = 450    # 각재 간격 (mm)
MIN_REUSE_W   = 300    # [M3변경] 재사용 최소 폭 (mm) — 기존 450
MIN_REUSE_H   = 450    # [M3변경] 재사용 최소 높이 (mm) — 기존 450
CORNER_MIN    = 200    # 개구부 코너 이격 — 이음매가 코너에서 이 거리 이내면 경고
IS_2P         = False  # 2P 시공 여부 (1P 고정)

# 합판 보강 규격
PLY_T         = 12     # 보강 합판 두께 (mm)
PLY_DOOR_MARGIN  = 150  # 도어 주변 합판 여백 (mm)
PLY_TV_HEIGHT    = 600  # TV 보강 높이 (mm, 상단에서)
PLY_TV_WIDTH     = 1200 # TV 보강 너비 (mm, 중앙 기준)
PLY_SHELF_HEIGHT = 200  # 선반 보강 높이 (mm)


# ─────────────────────────────────────────
# [변경1] 비대칭 재사용 풀
# ─────────────────────────────────────────
class ReusePool:
    """
    자투리 보관소.
    [M3변경] 폭 ≥ MIN_REUSE_W(300), 높이 ≥ MIN_REUSE_H(450) 인 경우만 등록.
    [공장 단위] 모듈러(공장 제작) 방식 — 프로젝트 전체를 한 배치로 보고
    층(floor)·공간(space) 경계 없이 전역 재사용. 한 배치라 위치 우선순위는
    의미 없으므로, 크기 충족 자투리 중 면적이 가장 작은(딱 맞는) 것부터 소비.
    """
    MAX_TOTAL = 5000  # 전역 풀 상한 (과도 누적 방지)

    def __init__(self):
        self.items = []   # 전역 단일 풀

    def add(self, piece: dict, space_id: str, floor_id: str) -> bool:
        # 비대칭 최소 규격 체크
        if piece['w'] < MIN_REUSE_W or piece['h'] < MIN_REUSE_H:
            return False
        if len(self.items) >= self.MAX_TOTAL:
            return False
        self.items.append({**piece, 'space_id': space_id, 'floor_id': floor_id})
        return True

    def consume(self, need_w, need_h, space_id, floor_id):
        candidates = [(i, p) for i, p in enumerate(self.items)
                      if p['w'] >= need_w and p['h'] >= need_h]
        if not candidates:
            return None, []

        # [공장 단위] 위치 우선순위 없음 — 면적이 가장 작은(딱 맞는) 자투리부터 소비
        idx, chosen = min(candidates, key=lambda ip: ip[1]['w'] * ip[1]['h'])
        self.items.pop(idx)
        leftovers = self._split_leftover(chosen, need_w, need_h)
        return chosen, leftovers

    def _split_leftover(self, piece, used_w, used_h):
        leftovers = []
        rem_w = round(piece['w'] - used_w, 1)
        rem_h = round(piece['h'] - used_h, 1)
        if rem_w >= MIN_REUSE_W and piece['h'] >= MIN_REUSE_H:
            leftovers.append({'w': rem_w, 'h': piece['h']})
        if rem_h >= MIN_REUSE_H and used_w >= MIN_REUSE_W:
            leftovers.append({'w': used_w, 'h': rem_h})
        return leftovers

    def total(self):
        return len(self.items)


# ─────────────────────────────────────────
# [변경2] 이음매 — 끝(오른쪽)부터 배치
# ─────────────────────────────────────────
def _seam_positions_rtl(x_start: float, x_end: float,
                        layer_offset: int = 0) -> list:
    """
    [M3변경] 끝(오른쪽)부터 배치 — 자투리가 왼쪽(시작점)에 위치.
    layer_offset=0 : Layer 1
    layer_offset=STUD : Layer 2 (450mm 엇갈림)

    예) x_start=0, x_end=3613, BW=900, layer_offset=0
        seams: 3613, 2713, 1813, 913, 13, 0
        → 자투리 13mm 가 왼쪽에 위치
    """
    seams = set()
    seams.add(x_start)
    seams.add(x_end)

    # x_end - layer_offset 기준으로 BW씩 왼쪽으로 이동
    # layer_offset=0 (Layer1): x_end부터
    # layer_offset=STUD (Layer2): x_end-450부터 → Layer1과 이음매 450mm 엇갈림
    x = x_end - layer_offset
    while x > x_start:
        if x_start < x < x_end:
            seams.add(round(x, 1))
        x = round(x - BW, 1)

    return sorted(seams)


# ─────────────────────────────────────────
# [M3 STEP3] 슬리버(끝칸 자투리) 보정
# ─────────────────────────────────────────
# ─────────────────────────────────────────
# x축 열 계획 (통합)
# ─────────────────────────────────────────
# ─────────────────────────────────────────
# y축 행 계획 (v2와 동일)
# ─────────────────────────────────────────
def _rows_in_region(y_start: float, height: float) -> list:
    """
    아래→위 시공 기준:
    full 보드가 바닥(y_start)부터 쌓이고, cut 조각이 천장 쪽(맨 위)에 위치.
    """
    if height <= 0.5:
        return []
    rows = []
    full_count = int(height // BH)
    for i in range(full_count):
        y = y_start + BH * i          # 바닥부터 위로
        rows.append(('full', round(y, 1), BH))
    remainder = round(height % BH, 1)
    if remainder > 0.5:
        y_top = y_start + full_count * BH   # 천장 쪽
        rows.append(('cut', round(y_top, 1), remainder))
    return rows


# ─────────────────────────────────────────
# [추가4] 합판 보강 영역 계산
# ─────────────────────────────────────────
def calc_plywood_zones(L: float, H: float,
                       openings: list,
                       has_tv: bool = False,
                       has_shelf: bool = False) -> list:
    """
    [M3추가] 12T 합판 보강 영역 계산.

    반환: [{'type': '도어보강'|'TV보강'|'선반보강', 'x', 'y', 'w', 'h'}, ...]

    규칙 (M3_시공방식_확인결과.html):
    - 도어·선반: 개구부 주변 PLY_DOOR_MARGIN(150mm) 여백으로 보강
    - TV·상부장: 벽 상단 PLY_TV_HEIGHT(600mm) × PLY_TV_WIDTH(1200mm), 중앙 배치
    - 선반: 벽 중단 (H/2 기준) PLY_SHELF_HEIGHT(200mm)
    """
    zones = []

    # 도어 주변 보강
    for op in openings:
        ox = op.get('ox', 0) or 0
        ow = op.get('ow', 0) or 0
        oy = op.get('oy', 0) or 0
        oh = op.get('oh', 0) or 0
        if not (ow and oh):
            continue
        # 개구부 둘레 150mm 여백
        zx = max(0, ox - PLY_DOOR_MARGIN)
        zy = max(0, oy - PLY_DOOR_MARGIN)
        zw = min(L, ox + ow + PLY_DOOR_MARGIN) - zx
        zh = min(H, oy + oh + PLY_DOOR_MARGIN) - zy
        zones.append({
            'type': '도어 보강',
            'x': round(zx, 1), 'y': round(zy, 1),
            'w': round(zw, 1), 'h': round(zh, 1),
            'thick': PLY_T,
        })

    # TV·상부장 보강 (상단 중앙)
    if has_tv:
        tv_x = max(0, (L - PLY_TV_WIDTH) / 2)
        tv_y = max(0, H - PLY_TV_HEIGHT)
        zones.append({
            'type': 'TV·상부장 보강',
            'x': round(tv_x, 1), 'y': round(tv_y, 1),
            'w': round(min(PLY_TV_WIDTH, L), 1), 'h': PLY_TV_HEIGHT,
            'thick': PLY_T,
        })

    # 선반 보강 (벽 중단)
    if has_shelf:
        shelf_y = round(H / 2 - PLY_SHELF_HEIGHT / 2, 1)
        zones.append({
            'type': '선반 보강',
            'x': 0, 'y': max(0, shelf_y),
            'w': round(L, 1), 'h': PLY_SHELF_HEIGHT,
            'thick': PLY_T,
        })

    return zones


# ─────────────────────────────────────────
# [M3 4-1 걸치기] 보드 그리드 + 개구부 관통 분류
# ─────────────────────────────────────────
def _calc_x_breaks(W, ops, bw, off=0):
    """X축 분할점. 개구부 경계를 삽입하지 않는 순수 보드폭 그리드(걸치기).
    개구부 있으면 LTR(왼쪽부터, 자투리 오른쪽) / 없으면 RTL(오른쪽부터, 자투리 왼쪽).
    (승훈 simulator_ui.html calcXBreaks 포팅)"""
    b = [0.0, W]
    if ops:
        x = off
        while x < W:
            if x > 0: b.append(x)
            x += bw
    else:
        x = W - off
        while x > 0:
            if x < W: b.append(x)
            x -= bw
    return sorted({round(v, 1) for v in b if 0 <= v <= W})


def _fix_first_thin(brk, min_w):
    """첫 칸(왼쪽 자투리)이 min_w 미만이면 다음 칸에서 빌려 키움 (RTL용)."""
    if len(brk) < 3:
        return brk
    r = brk[:]
    first_w = r[1] - r[0]
    if 0 < first_w < min_w:
        new_split = r[0] + min_w
        if new_split < r[2]:
            r[1] = round(new_split, 1)
    return r


def _fix_last_thin(brk, min_w):
    """마지막 칸(오른쪽 자투리)이 min_w 미만이면 앞 칸을 줄여 키움 (LTR용)."""
    if len(brk) < 3:
        return brk
    r = brk[:]
    last_w = r[-1] - r[-2]
    if 0 < last_w < min_w:
        new_split = r[-1] - min_w
        if new_split > r[-3]:
            r[-2] = round(new_split, 1)
    return r


def _cell_solid(x1, y1, x2, y2, ops):
    """셀에서 개구부를 제외한 실제 보드 면적.
    ops = merged_ops [(ox, ow, oh, oy)] (ox=개구부 왼쪽 끝)."""
    a = (x2 - x1) * (y2 - y1)
    for ox, ow, oh, oy in ops:
        o_left, o_right = ox, ox + ow
        o_top, o_bot = oy, oy + oh
        ix1, iy1 = max(x1, o_left), max(y1, o_top)
        ix2, iy2 = min(x2, o_right), min(y2, o_bot)
        if ix2 > ix1 and iy2 > iy1:
            a -= (ix2 - ix1) * (iy2 - iy1)
    return max(0.0, a)


def _classify_cell(solid, cw, ch, bw, bh, bx1, by1, bx2, by2, ops, stat):
    """셀 1개의 절단 형상 분류 (승훈 simulator_ui.html classify() 포팅).

      · skip     : 셀 전체가 개구부 안 → 보드 없음
      · full     : 온장 (bw×bh)
      · cut      : 개구부 무관 직선절단 (벽 가장자리·천장 자투리)
      · edge_cut : 개구부가 보드 모서리에만 걸침 → 직선 1회 절단
      · notch    : 개구부가 보드 내부를 관통(throughX/throughY) → ㄱ/ㄴ자 2회 절단

    이음매(보드 좌/우 끝)가 개구부 코너에서 CORNER_MIN 이내면 stat['corner_warn'] 증가."""
    if solid <= 0:
        return 'skip'
    is_notch = False
    has_overlap = False
    for ox, ow, oh, oy in ops:
        o_left, o_right = ox, ox + ow
        o_top, o_bot = oy, oy + oh
        ix1, iy1 = max(bx1, o_left), max(by1, o_top)
        ix2, iy2 = min(bx2, o_right), min(by2, o_bot)
        if ix2 <= ix1 or iy2 <= iy1:
            continue
        has_overlap = True
        through_x = (o_left > bx1 + 2 and o_right < bx2 - 2)
        through_y = (o_top > by1 + 2 and o_bot < by2 - 2)
        if through_x or through_y:
            is_notch = True
        for seam in (bx1, bx2):
            if (abs(seam - o_left) < CORNER_MIN or abs(seam - o_right) < CORNER_MIN):
                stat['corner_warn'] = stat.get('corner_warn', 0) + 1
                break
    if is_notch:
        return 'notch'
    if has_overlap:
        return 'edge_cut'
    if round(cw) == bw and round(ch) == bh:
        return 'full'
    return 'cut'


def _process_grid_cell(cx, cy, cw, ch, ctype, solid,
                       layer, space_id, floor_id, pool, stat):
    """그리드 셀 1개를 재사용 풀 반영하여 placement dict로 변환.
    온장/노치는 신규 보드, cut/edge_cut은 재사용 우선."""
    if ctype == 'full':
        stat['boards'] += 1
        return {'layer': layer, 'x': cx, 'y': cy, 'w': cw, 'h': ch, 'type': 'full'}

    # notch는 온장에서 ㄱ/ㄴ자로 도려내므로 재사용 불가(항상 신규). cut/edge_cut만 재사용 시도.
    if ctype != 'notch':
        found, leftovers = pool.consume(cw, ch, space_id, floor_id)
        if found:
            stat['reuse_in'] += 1
            for lf in leftovers:
                if pool.add(lf, space_id, floor_id):
                    stat['reuse_out'] += 1
            return {'layer': layer, 'x': cx, 'y': cy, 'w': cw, 'h': ch, 'type': 'reuse'}

    stat['boards'] += 1
    # 측면/상단 스트립 → 재사용 등록 (불가 시 폐기)
    off_w = round(BW - cw, 1)
    if off_w > 0.5:
        if pool.add({'w': off_w, 'h': BH}, space_id, floor_id):
            stat['reuse_out'] += 1
        else:
            stat['waste_mm2'] += off_w * BH
    off_h = round(BH - ch, 1)
    if off_h > 0.5:
        if pool.add({'w': cw, 'h': off_h}, space_id, floor_id):
            stat['reuse_out'] += 1
        else:
            stat['waste_mm2'] += cw * off_h
    # 개구부로 도려낸 부분(걸치기 절단) = 폐기
    op_cut = round(cw * ch - solid, 1)
    if op_cut > 0.5:
        stat['waste_mm2'] += op_cut
    return {'layer': layer, 'x': cx, 'y': cy, 'w': cw, 'h': ch,
            'type': ctype, 'solid': round(solid, 1)}


# ─────────────────────────────────────────
# 벽 1개 최적화 (합판 보강 포함)
# ─────────────────────────────────────────
def _merge_openings(openings, L, H):
    if not openings:
        return []
    intervals = sorted(
        [(op['ox'], op['ox'] + op['ow'], op['oh'], op['oy'])
         for op in openings if op.get('ow') and op.get('oh')],
        key=lambda t: t[0]
    )
    if not intervals:
        return []
    merged = []
    x0, x1, oh, oy = intervals[0]
    for nx0, nx1, noh, noy in intervals[1:]:
        if nx0 <= x1:
            x1 = max(x1, nx1); oh = max(oh, noh); oy = min(oy, noy)
        else:
            merged.append((round(x0,1), round(x1-x0,1), oh, oy)); x0,x1,oh,oy = nx0,nx1,noh,noy
    merged.append((round(x0,1), round(x1-x0,1), oh, oy))
    return merged


def optimize_wall(wall: dict, pool: ReusePool) -> dict:
    L        = wall['L']
    H        = wall['H']
    space_id = wall['space_id']
    floor_id = wall['floor_id']
    is_ext   = wall.get('is_external', False)
    layers   = [1, 2] if IS_2P else [1]

    raw_openings = wall.get('openings', [])
    if raw_openings:
        merged_ops = _merge_openings(raw_openings, L, H)
    else:
        ow = wall.get('ow', 0); oh = wall.get('oh', 0)
        ox = wall.get('ox', 0); oy = wall.get('oy', 0)
        merged_ops = [(ox, ow, oh, oy)] if (ow and oh) else []

    # 배치 방향: 개구부 있으면 LTR(자투리 오른쪽, M3 9번) / 없으면 RTL(자투리 왼쪽, 8-1)
    x_case_used = 'LTR' if merged_ops else 'RTL'

    # Pool 반영 실행 — [M3 4-1 걸치기] 균일 보드 그리드가 개구부를 가로지름
    stat = {'boards': 0, 'reuse_in': 0, 'reuse_out': 0, 'waste_mm2': 0.0,
            'corner_warn': 0}
    placements = []
    for layer in layers:
        offset = STUD if (IS_2P and layer == 2) else 0
        # X축: 개구부 경계를 삽입하지 않는 순수 보드폭 그리드 (걸치기)
        x_breaks = _calc_x_breaks(L, merged_ops, BW, offset)
        x_breaks = (_fix_last_thin(x_breaks, MIN_REUSE_W) if merged_ops
                    else _fix_first_thin(x_breaks, MIN_REUSE_W))
        # Y축: 바닥부터 온장, 자투리는 천장 쪽 (M3 2-1·9-1) — 기존 행 분할 유지
        y_bands = _rows_in_region(0, H)

        for _row_t, by1, ch in y_bands:
            by2 = by1 + ch
            for xi in range(len(x_breaks) - 1):
                bx1, bx2 = x_breaks[xi], x_breaks[xi + 1]
                cw = round(bx2 - bx1, 1)
                if cw < 1:
                    continue
                solid = _cell_solid(bx1, by1, bx2, by2, merged_ops)
                ctype = _classify_cell(solid, cw, ch, BW, BH,
                                       bx1, by1, bx2, by2, merged_ops, stat)
                if ctype == 'skip':           # 셀 전체가 개구부 안 → 보드 없음
                    continue
                placements.append(_process_grid_cell(
                    bx1, by1, cw, ch, ctype, solid,
                    layer, space_id, floor_id, pool, stat))

        # 개구부 마커 (도면 빗금용)
        for op_ox, op_ow, op_oh, op_oy in merged_ops:
            placements.append({'layer': layer, 'x': op_ox, 'y': op_oy,
                               'w': op_ow, 'h': op_oh, 'type': 'opening'})

    board_area = stat['boards'] * BW * BH
    loss = max(0.0, stat['waste_mm2'] / max(board_area, 1) * 100)

    # [추가4] 합판 보강 영역
    has_door = any(op.get('oh', 0) > 1500 for op in raw_openings)  # 문 높이 기준
    plywood_zones = calc_plywood_zones(
        L, H,
        openings=[{'ox': op[0], 'ow': op[1], 'oh': op[2], 'oy': op[3]}
                  for op in merged_ops],
        has_tv=is_ext,      # 외벽이면 TV 보강 기본 포함
        has_shelf=has_door,  # 도어 있으면 선반 보강
    )

    return {
        'layout'        : x_case_used + 'C',  # Y배치 항상 C안 (바닥부터 온장, M3 9-1)
        'boards'        : stat['boards'],
        'reuse_in'      : stat['reuse_in'],
        'reuse_out'     : stat['reuse_out'],
        'waste_mm2'     : round(stat['waste_mm2']),
        'loss_pct'      : round(loss, 2),
        'opening_count' : len(merged_ops),
        'corner_warn'   : stat['corner_warn'],
        'plywood_zones' : plywood_zones,
        'placements'    : placements,
        'L'             : L,
        'H'             : H,
    }


# ─────────────────────────────────────────
# 전체 건물 최적화
# ─────────────────────────────────────────
def optimize_building(walls: list) -> tuple:
    print(f"총 벽: {len(walls)}개")
    print(f"시공: {'2P' if IS_2P else '1P'}  |  보드: {BW}×{BH}mm  |  각재: {STUD}mm")
    print(f"재사용 최소: 폭 {MIN_REUSE_W}mm / 높이 {MIN_REUSE_H}mm  [M3기준]")

    ordered = sorted(walls, key=lambda w: (w['floor_id'], -w['L'] * w['H']))
    pool = ReusePool()
    results = []
    total_waste = total_board_area = 0.0

    for wall in ordered:
        res = optimize_wall(wall, pool)
        if res:
            results.append({
                'wall_id'     : wall['wall_id'],
                'floor_id'    : wall.get('floor_id', ''),
                'is_external' : wall.get('is_external', False),
                **res,
            })
            total_waste      += res['waste_mm2']
            total_board_area += res['boards'] * BW * BH

    # 미사용 재사용 풀 잔여 → 실제 손실로 계상
    leftover_waste = sum(p['w'] * p['h'] for p in pool.items)
    leftover_count = len(pool.items)
    total_waste += leftover_waste
    total_loss = max(0.0, total_waste / max(total_board_area, 1) * 100)

    # 발주량 집계
    total_new_boards = sum(r['boards'] for r in results)        # 신규 보드 수
    total_reuse_in   = sum(r['reuse_in'] for r in results)      # 재사용 소비 수
    PALETTE = 990 if BW == 900 else 100                         # 석고보드 990, 합판 100
    import math as _m
    palettes = _m.ceil(total_new_boards / PALETTE) if total_new_boards else 0

    print(f"  → 전체 로스율: {total_loss:.2f}%  (미사용 자투리 {leftover_count}개 포함)")
    print(f"  → 신규보드 {total_new_boards}장  재사용 {total_reuse_in}장  발주 {palettes}팔레트")
    return results, total_loss, {
        'new_boards'     : total_new_boards,
        'reuse_in'       : total_reuse_in,
        'leftover_count' : leftover_count,
        'leftover_waste_mm2': round(leftover_waste),
        'palettes'       : palettes,
        'palette_size'   : PALETTE,
    }


# ─────────────────────────────────────────
# HTML 리포트 생성
# ─────────────────────────────────────────
SVG_MAX_BOARDS = 80  # 레이어당 보드 수 초과면 SVG 생략
_svg_uid_seq = [0]   # 충돌 없는 고유 탭 컨테이너 ID 생성용


def _plywood_svg(r: dict, scale_h: int = 210) -> str:
    """합판 보강 전용 도면 SVG. 벽 윤곽 + 보강 구역만 표시."""
    L = r.get('L', 0)
    H = r.get('H', 0)
    zones = r.get('plywood_zones', [])
    if not (L and H):
        return ""
    if not zones:
        return '<div class="nocut">합판 보강 구역 없음</div>'

    sw = max(L * (scale_h / H), 120)
    if sw > 720:
        sw = 720
    s = sw / L  # sw 확정 후 scale 재계산 (sw < L*(scale_h/H) 시 비율 보정)
    sh = H * s

    def ty(y_mm, h_mm):
        return (H - y_mm - h_mm) * s

    zone_colors = {
        '도어 보강':      ('#fee2e2', '#dc2626'),
        'TV·상부장 보강': ('#fef9c3', '#ca8a04'),
        '선반 보강':      ('#ede9fe', '#7c3aed'),
    }

    lines = [f'<svg width="{sw:.0f}" height="{sh+18:.0f}" '
             f'viewBox="0 0 {sw:.0f} {sh+18:.0f}" '
             f'xmlns="http://www.w3.org/2000/svg" '
             f'style="display:block;background:#fff;'
             f'border:1.5px solid #94a3b8;border-radius:3px">']

    # 벽 배경
    lines.append(f'<rect x="0" y="0" width="{sw:.0f}" height="{sh:.0f}" '
                 f'fill="#f8fafc" stroke="#94a3b8" stroke-width="1"/>')

    # 각재 가이드라인 (연한 세로선)
    x = 0.0
    while x <= L:
        sx = x * s
        lines.append(f'<line x1="{sx:.1f}" y1="0" x2="{sx:.1f}" y2="{sh:.0f}" '
                     f'stroke="#e2e8f0" stroke-width="0.6"/>')
        x = round(x + STUD, 1)

    # 개구부 표시
    for p in r.get('placements', []):
        if p.get('type') == 'opening' and p.get('layer') == 1:
            x = p['x'] * s
            y = ty(p['y'], p['h'])
            w = p['w'] * s
            h = p['h'] * s
            if w > 1 and h > 1:
                lines.append(
                    f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                    f'fill="#e2e8f0" stroke="#94a3b8" stroke-width="0.8" '
                    f'stroke-dasharray="3 2"/>')

    # 합판 보강 구역
    for z in zones:
        fill, stroke = zone_colors.get(z['type'], ('#fef3c7', '#d97706'))
        zx = z['x'] * s
        zy = ty(z['y'], z['h'])
        zw = z['w'] * s
        zh = z['h'] * s
        lines.append(
            f'<rect x="{zx:.1f}" y="{zy:.1f}" width="{zw:.1f}" height="{zh:.1f}" '
            f'fill="{fill}" fill-opacity="0.75" stroke="{stroke}" '
            f'stroke-width="1.5"/>')
        if zw > 40 and zh > 14:
            cx = zx + zw / 2
            cy = zy + zh / 2 + 4
            lines.append(
                f'<text x="{cx:.0f}" y="{cy:.0f}" text-anchor="middle" '
                f'font-size="9" font-weight="700" fill="{stroke}">'
                f'{z["type"]} {z["w"]:.0f}×{z["h"]:.0f}mm {z["thick"]}T</text>')

    lines.append(
        f'<text x="{sw/2:.0f}" y="{sh+14:.0f}" text-anchor="middle" '
        f'font-size="9" fill="#64748b">L={L:.0f} × H={H:.0f} mm</text>')
    lines.append('</svg>')
    return ''.join(lines)


def _one_layer_svg(placements_l: list, L: float, H: float,
                   layer: int, scale_h: int) -> str:
    """레이어 1개 SVG. 겹침 없이 보드 배치를 명확하게 표시."""
    fill_map    = {1: '#dbeafe', 2: '#ffedd5'}   # 온장: 연파랑 / 연주황
    stroke_map  = {1: '#1d4ed8', 2: '#c2410c'}
    cut_fill    = {1: '#bfdbfe', 2: '#fed7aa'}   # 절단: 온장보다 진한 동색
    reuse_fill  = {1: '#bbf7d0', 2: '#bbf7d0'}   # 재사용: 연초록
    edge_fill   = '#fef08a'                        # 직선절단(개구부 모서리): 노란색
    edge_stroke = '#ca8a04'                        # 직선절단 테두리: 황갈색
    notch_fill  = '#ede9fe'                         # 노치절단(개구부 관통): 연보라
    notch_stroke = '#7c3aed'                        # 노치절단 테두리: 보라

    fill   = fill_map.get(layer, '#eee')
    stroke = stroke_map.get(layer, '#555')
    cf     = cut_fill.get(layer, '#eee')
    rf     = reuse_fill.get(layer, '#d1fae5')

    sw = max(L * (scale_h / H), 120)
    if sw > 720:
        sw = 720
    s = sw / L  # sw 확정 후 scale 재계산 (최소폭 120px 강제 시 보드가 벽 끝까지 안 그려지는 버그 수정)
    sh = H * s

    def ty(y_mm, h_mm):
        """벽 y좌표(바닥=0) → SVG y좌표(위=0)."""
        return (H - y_mm - h_mm) * s

    lines = [f'<svg width="{sw:.0f}" height="{sh+18:.0f}" '
             f'viewBox="0 0 {sw:.0f} {sh+18:.0f}" '
             f'xmlns="http://www.w3.org/2000/svg" '
             f'style="display:block;background:#f8fafc;'
             f'border:1.5px solid {stroke};border-radius:3px">']

    # 벽 외곽선
    lines.append(f'<rect x="0" y="0" width="{sw:.0f}" height="{sh:.0f}" '
                 f'fill="none" stroke="#94a3b8" stroke-width="1"/>')

    placed = 0
    for p in placements_l:
        x = p['x'] * s
        y = ty(p['y'], p['h'])
        w = p['w'] * s
        h = p['h'] * s
        if w < 1 or h < 1:
            continue
        t = p.get('type', 'full')

        if t == 'opening':
            # 개구부: 대각선 빗금
            lines.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                f'fill="#f1f5f9" stroke="#94a3b8" stroke-width="0.8" '
                f'stroke-dasharray="3 2"/>')
            cx, cy = x + w/2, y + h/2
            if w > 24 and h > 14:
                lines.append(
                    f'<text x="{cx:.0f}" y="{cy+4:.0f}" text-anchor="middle" '
                    f'font-size="8" fill="#94a3b8">개구부</text>')
            continue

        # 보드 색상 선택
        if t == 'reuse':
            board_fill, board_stroke = rf, stroke
        elif t == 'notch':
            board_fill, board_stroke = notch_fill, notch_stroke
        elif t == 'edge_cut':
            board_fill, board_stroke = edge_fill, edge_stroke
        elif t == 'cut':
            board_fill, board_stroke = cf, stroke
        else:
            board_fill, board_stroke = fill, stroke
        lines.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'fill="{board_fill}" stroke="{board_stroke}" stroke-width="1"/>')

        # 치수 라벨
        if w > 32 and h > 16:
            cx, cy = x + w/2, y + h/2 + 4
            icon = ('♻' if t == 'reuse' else 'ㄴ' if t == 'notch'
                    else '□' if t == 'edge_cut' else '✂' if t == 'cut' else '')
            lbl  = f'{icon} {p["w"]:.0f}×{p["h"]:.0f}'
            lines.append(
                f'<text x="{cx:.0f}" y="{cy:.0f}" text-anchor="middle" '
                f'font-size="8.5" fill="{stroke}" font-weight="600">{lbl}</text>')
        placed += 1

    # (합판 보강은 별도 탭에서 표시 — 석고보드 도면에는 생략)

    # 하단 치수 텍스트
    lines.append(
        f'<text x="{sw/2:.0f}" y="{sh+14:.0f}" text-anchor="middle" '
        f'font-size="9" fill="#64748b">L={L:.0f} × H={H:.0f} mm</text>')
    lines.append('</svg>')
    return ''.join(lines)


def _wall_svg(r: dict, scale_h: int = 210) -> str:
    """
    벽 절단도면 HTML.
    - 1P: 레이어 1개 SVG
    - 2P: Layer1 / Layer2 탭 분리 (겹쳐 보이는 혼란 제거)
    """
    L = r.get('L', 0)
    H = r.get('H', 0)
    placements = r.get('placements', [])
    if not (L and H):
        return ""

    layers_present = sorted(set(p['layer'] for p in placements))

    # 레이어별 분류
    by_layer = {}
    for p in placements:
        by_layer.setdefault(p['layer'], []).append(p)

    for ln, lp in by_layer.items():
        n = sum(1 for p in lp if p.get('type') != 'opening')
        if n > SVG_MAX_BOARDS:
            return (f'<div class="svg-skip">📐 도면 생략 '
                    f'(레이어당 보드 {n}개 초과 — cut list 참조)</div>')

    ply_zones = r.get('plywood_zones', [])

    def _stats(lp):
        full  = sum(1 for p in lp if p.get('type') == 'full')
        cut   = sum(1 for p in lp if p.get('type') in ('cut', 'edge_cut'))
        notch = sum(1 for p in lp if p.get('type') == 'notch')
        reuse = sum(1 for p in lp if p.get('type') == 'reuse')
        return full, cut, notch, reuse

    _svg_uid_seq[0] += 1
    uid = _svg_uid_seq[0]

    # 탭 목록 구성: Layer1, (Layer2), (합판보강)
    tabs = []
    for ln in layers_present:
        st = _stats(by_layer.get(ln, []))
        notch_lbl = f' · 노치 {st[2]}' if st[2] else ''
        if len(layers_present) == 1:
            label = (f'석고보드 &nbsp;<span class="tstat">'
                     f'온장 {st[0]} · 절단 {st[1]}{notch_lbl} · 재사용 {st[3]}</span>')
        else:
            offset = f'+{STUD}mm 엇갈림 · ' if ln == 2 else ''
            label = (f'Layer {ln} &nbsp;'
                     f'<span class="tstat">{offset}온장 {st[0]} · 절단 {st[1]}'
                     f'{notch_lbl} · 재사용 {st[3]}</span>')
        svg = _one_layer_svg(by_layer.get(ln, []), L, H, ln, scale_h)
        tabs.append(('layer', ln, label, svg))

    if ply_zones:
        ply_svg = _plywood_svg(r, scale_h)
        zone_summary = ' · '.join(
            f'{z["type"]}({z["w"]:.0f}×{z["h"]:.0f})' for z in ply_zones[:3])
        if len(ply_zones) > 3:
            zone_summary += f' 외 {len(ply_zones)-3}곳'
        tabs.append(('ply', 99, f'합판 보강 <span class="tstat">{zone_summary}</span>', ply_svg))

    html = [f'<div class="layer-tabs" id="lt{uid}"><div class="tab-btns">']
    for i, (kind, key, label, _) in enumerate(tabs):
        active = ' active' if i == 0 else ''
        cls = 'tab-btn-ply' if kind == 'ply' else 'tab-btn'
        html.append(
            f'<button class="{cls}{active}" '
            f'onclick="switchTabK(\'lt{uid}\',{i})">{label}</button>')
    html.append('</div>')
    for i, (kind, key, label, svg) in enumerate(tabs):
        disp = 'block' if i == 0 else 'none'
        html.append(f'<div class="tab-pane" data-idx="{i}" style="display:{disp}">{svg}</div>')
    html.append('</div>')
    return ''.join(html)


def _cut_list(r: dict) -> list:
    """벽 1개에서 잘라야 할 부분 보드 목록 (cut만)."""
    items = []
    for p in r.get('placements', []):
        if p.get('type') == 'cut':
            items.append(p)
    return items


def _order_card(order_info, surcharge_pct, surcharge_boards):
    if not order_info:
        if surcharge_pct > 0 and surcharge_boards:
            return (f'<div class="card warn"><div class="val">{surcharge_boards}</div>'
                    f'<div class="lbl">할증 발주량 ({surcharge_pct:.0f}%)</div></div>')
        return ''
    base = order_info['new_boards']
    palettes = order_info['palettes']
    palette_size = order_info['palette_size']
    surplus = order_info.get('leftover_count', 0)
    if surcharge_pct > 0:
        ordered = surcharge_boards if surcharge_boards else math.ceil(base * (1 + surcharge_pct / 100))
        pal = math.ceil(ordered / palette_size) if ordered else 0
        return (f'<div class="card warn"><div class="val">{ordered}장 / {pal}팔레트</div>'
                f'<div class="lbl">발주량 (할증 {surcharge_pct:.0f}%)<br>'
                f'<span style="font-size:10px;opacity:.7">'
                f'순수 {base}장 × {1+surcharge_pct/100:.2f} / 팔레트 {palette_size}장'
                f'{"  ⚠미사용자투리 "+str(surplus)+"개" if surplus else ""}'
                f'</span></div></div>')
    return (f'<div class="card warn"><div class="val">{base}장 / {palettes}팔레트</div>'
            f'<div class="lbl">발주량<br>'
            f'<span style="font-size:10px;opacity:.7">팔레트 {palette_size}장 기준'
            f'{"  ⚠미사용자투리 "+str(surplus)+"개" if surplus else ""}'
            f'</span></div></div>')


def make_opt_html(results: list, total_loss: float,
                  ifc_path: str, mat: str = "석고보드", ply: int = 1,
                  surcharge_pct: float = 0.0, direction: str = '세로',
                  order_info: dict = None) -> str:
    from datetime import datetime
    import os

    bw_label   = f"{BW}×{BH}mm"
    total_boards   = sum(r['boards']    for r in results)
    total_reuse    = sum(r['reuse_in']  for r in results)
    total_waste_m2 = sum(r['waste_mm2'] for r in results) / 1e6

    # cut 통계
    all_cuts = []
    for r in results:
        for p in r.get('placements', []):
            if p.get('type') == 'cut':
                all_cuts.append((round(p['w']), round(p['h'])))
    cut_count = len(all_cuts)

    # 사이즈별 그룹 (같은 절단 사이즈 묶음 — 한 보드에서 여러 개 잘라낼 수 있음 = 가이드)
    from collections import Counter
    cut_freq = Counter(all_cuts).most_common(15)

    # 합판 통계
    plywood_total = sum(len(r.get('plywood_zones', [])) for r in results)

    # 할증 발주량
    surcharge_boards = math.ceil(total_boards * (1 + surcharge_pct / 100)) if surcharge_pct > 0 else 0
    dir_label = {
        '세로': '세로(종방향)', '가로': '가로(횡방향)',
        '자동→세로': '자동→세로', '자동→가로': '자동→가로',
    }.get(direction, direction)

    ifc_name = os.path.basename(ifc_path) if ifc_path else ""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 층별 그룹
    floors = {}
    for r in results:
        floors.setdefault(r['floor_id'], []).append(r)

    floor_html = ""
    for fid in sorted(floors.keys()):
        wall_rows = ""
        for r in floors[fid]:
            ext = "외기" if r['is_external'] else "내기"
            loss_cls = "loss-high" if r['loss_pct'] > 10 else (
                       "loss-mid"  if r['loss_pct'] > 5  else "loss-ok")
            cut_n = sum(1 for p in r.get('placements', [])
                        if p.get('type') == 'cut')
            full_n = sum(1 for p in r.get('placements', [])
                         if p.get('type') == 'full')
            reuse_n = r['reuse_in']
            ply_n = len(r.get('plywood_zones', []))

            svg = _wall_svg(r)
            cuts = _cut_list(r)
            cut_table = ""
            if cuts:
                rows_c = ""
                for i, c in enumerate(cuts, 1):
                    rows_c += (f"<tr><td>{i}</td><td>L{c['layer']}</td>"
                               f"<td>{c['w']:.0f}×{c['h']:.0f}</td>"
                               f"<td>{c['x']:.0f}, {c['y']:.0f}</td></tr>")
                cut_table = (f"<table class='cut-tbl'><thead><tr>"
                             f"<th>#</th><th>겹</th><th>사이즈 (mm)</th>"
                             f"<th>위치 (x, y)</th></tr></thead>"
                             f"<tbody>{rows_c}</tbody></table>")
            else:
                cut_table = "<div class='nocut'>절단 없음 (모두 온장)</div>"

            ply_rows = ""
            for z in r.get('plywood_zones', []):
                ply_rows += (f"<tr><td>{z['type']}</td>"
                             f"<td>{z['w']:.0f}×{z['h']:.0f}</td>"
                             f"<td>x={z['x']:.0f}, y={z['y']:.0f}</td>"
                             f"<td>{z['thick']}T</td></tr>")
            ply_block = ""
            if ply_rows:
                ply_block = (f"<div class='ply-block'><b>합판 보강</b>"
                             f"<table class='ply-tbl'><thead><tr>"
                             f"<th>구분</th><th>사이즈</th><th>위치</th><th>두께</th>"
                             f"</tr></thead><tbody>{ply_rows}</tbody></table></div>")

            wall_rows += f"""
            <details class="wall-card">
              <summary>
                <span class="wid">{r['wall_id']}</span>
                <span class="{'ext' if r['is_external'] else 'int'}">{ext}</span>
                <span class="pill">배치 {r['layout']}</span>
                <span class="pill">온장 {full_n}</span>
                <span class="pill">절단 {cut_n}</span>
                <span class="pill">재사용 {reuse_n}</span>
                <span class="pill {loss_cls}">로스 {r['loss_pct']:.1f}%</span>
                {f'<span class="pill ply-pill">합판 {ply_n}</span>' if ply_n else ''}
                <span class="dim">{r['L']:.0f}×{r['H']:.0f}mm</span>
              </summary>
              <div class="wall-body">
                <div class="svg-wrap">{svg}</div>
                <div class="cut-wrap">{cut_table}{ply_block}</div>
              </div>
            </details>"""

        floor_html += f"""
        <section class="floor-section">
          <h2>📍 {fid} <span class="floor-meta">({len(floors[fid])}개 벽)</span></h2>
          <div class="walls">{wall_rows}</div>
        </section>"""

    # cut 빈도 표
    cut_freq_html = ""
    if cut_freq:
        rows = ""
        for (w, h), n in cut_freq:
            rows += f"<tr><td>{w:.0f}×{h:.0f}mm</td><td class='num'>{n}회</td></tr>"
        cut_freq_html = f"""
        <div class="cut-freq">
          <h3>🔁 자주 등장하는 절단 사이즈 (상위 {len(cut_freq)}개)</h3>
          <table><thead><tr><th>사이즈</th><th>등장 횟수</th></tr></thead>
          <tbody>{rows}</tbody></table>
        </div>"""

    legend = """
    <div class="legend">
      <span><i class="sw" style="background:#dbeafe;border:1px solid #1d4ed8"></i>● 온장 (Layer 1)</span>
      <span><i class="sw" style="background:#ffedd5;border:1px solid #c2410c"></i>● 온장 (Layer 2)</span>
      <span><i class="sw" style="background:#bfdbfe;border:1px solid #1d4ed8"></i>✂ 절단</span>
      <span><i class="sw" style="background:#fef08a;border:1px solid #ca8a04"></i>□ 직선절단(개구부)</span>
      <span><i class="sw" style="background:#ede9fe;border:1px solid #7c3aed"></i>ㄴ 노치절단</span>
      <span><i class="sw" style="background:#bbf7d0;border:1px solid #16a34a"></i>♻ 재사용</span>
      <span><i class="sw" style="background:#f1f5f9;border:1px dashed #94a3b8"></i>개구부</span>
    </div>"""

    html = f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<title>석고보드 절단 최적화 — {ifc_name}</title>
<style>
  *{{box-sizing:border-box}}
  body{{font-family:'Malgun Gothic','나눔고딕',sans-serif;margin:0;
        background:#f4f6fb;color:#1a1a2e;line-height:1.55}}
  .header{{background:linear-gradient(135deg,#1a237e 0%,#283593 100%);
            color:#fff;padding:32px 40px 24px}}
  .header h1{{margin:0;font-size:24px;font-weight:900;letter-spacing:-.3px}}
  .header h1 small{{font-size:14px;font-weight:400;opacity:.85}}
  .header .sub{{font-size:12.5px;opacity:.8;margin-top:8px;font-family:Consolas,monospace}}
  .summary{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
            gap:14px;padding:24px 40px}}
  .card{{background:#fff;border-radius:12px;padding:18px 20px;
         box-shadow:0 2px 8px rgba(0,0,0,.06);text-align:center;
         border-top:3px solid #3949ab}}
  .card .val{{font-size:28px;font-weight:900;color:#1a237e;line-height:1.1}}
  .card .lbl{{font-size:11.5px;color:#666;margin-top:6px;font-weight:600;letter-spacing:.3px}}
  .card.ok{{border-top-color:#2e7d32}}
  .card.warn{{border-top-color:#e65100}}
  .card.bad{{border-top-color:#c62828}}
  .legend{{display:flex;gap:18px;flex-wrap:wrap;
           padding:10px 40px;background:#fff;border-top:1px solid #e0e0e0;
           border-bottom:1px solid #e0e0e0;font-size:11.5px;color:#555}}
  .legend i.sw{{display:inline-block;width:14px;height:14px;
                vertical-align:middle;margin-right:4px;border-radius:2px}}
  .cut-freq{{margin:24px 40px;background:#fff;border-radius:10px;padding:16px 22px;
              box-shadow:0 1px 4px rgba(0,0,0,.07)}}
  .cut-freq h3{{margin:0 0 10px;font-size:13px;color:#283593;font-weight:700}}
  .cut-freq table{{width:100%;border-collapse:collapse;font-size:12px}}
  .cut-freq th{{background:#f5f5f5;padding:6px 10px;text-align:left;color:#555}}
  .cut-freq td{{padding:5px 10px;border-bottom:1px solid #f0f0f0}}
  .cut-freq td.num{{text-align:right;font-weight:700;color:#1565c0}}
  .floor-section{{margin:0 40px 28px;background:#fff;border-radius:12px;
                   box-shadow:0 2px 8px rgba(0,0,0,.06)}}
  .floor-section h2{{margin:0;padding:14px 22px;
                      background:linear-gradient(90deg,#e8eaf6 0%,#f3e5f5 100%);
                      font-size:15px;color:#283593;
                      border-bottom:1px solid #c5cae9}}
  .floor-meta{{font-size:11px;color:#888;font-weight:400;margin-left:8px}}
  .walls{{padding:8px 14px 14px}}
  details.wall-card{{margin:6px 0;border:1px solid #e0e0e0;border-radius:8px;
                      background:#fafbfc;overflow:hidden}}
  details.wall-card[open]{{box-shadow:0 2px 6px rgba(0,0,0,.08);
                            background:#fff;border-color:#3949ab}}
  details.wall-card summary{{padding:10px 14px;cursor:pointer;
                              display:flex;gap:8px;flex-wrap:wrap;align-items:center;
                              font-size:12.5px;list-style:none}}
  details.wall-card summary::-webkit-details-marker{{display:none}}
  details.wall-card summary::before{{content:"▸";color:#3949ab;font-weight:900;
                                      margin-right:4px;transition:transform .15s}}
  details.wall-card[open] summary::before{{content:"▾"}}
  .wid{{font-weight:700;color:#1a237e;font-family:Consolas,monospace;
        font-size:12px;min-width:90px}}
  .ext{{color:#b71c1c;font-weight:700;background:#ffebee;
        padding:2px 8px;border-radius:10px;font-size:11px}}
  .int{{color:#1b5e20;font-weight:700;background:#e8f5e9;
        padding:2px 8px;border-radius:10px;font-size:11px}}
  .pill{{background:#eceff1;padding:2px 9px;border-radius:10px;
         font-size:11px;color:#37474f}}
  .pill.ply-pill{{background:#f3e5f5;color:#6a1b9a}}
  .loss-ok{{background:#c8e6c9;color:#2e7d32}}
  .loss-mid{{background:#ffe0b2;color:#e65100}}
  .loss-high{{background:#ffcdd2;color:#b71c1c}}
  .dim{{margin-left:auto;color:#888;font-size:11px;font-family:Consolas,monospace}}
  .wall-body{{padding:14px;border-top:1px solid #eee;
              display:grid;grid-template-columns:auto 1fr;gap:20px}}
  @media (max-width:900px){{.wall-body{{grid-template-columns:1fr}}}}
  .svg-wrap{{padding:6px;background:#fff;border-radius:6px;overflow-x:auto}}
  .svg-single svg{{max-width:100%}}
  .svg-skip{{padding:12px;background:#f8f8f8;border-radius:6px;
              color:#888;font-size:11px;text-align:center}}
  .layer-tabs{{}}
  .tab-btns{{display:flex;gap:2px;margin-bottom:6px;flex-wrap:wrap}}
  .tab-btn{{padding:5px 12px;font-size:11.5px;font-family:inherit;
             border:1px solid #c5cae9;border-radius:6px 6px 0 0;
             background:#f5f5f5;cursor:pointer;color:#555;transition:all .15s}}
  .tab-btn.active{{background:#1d4ed8;color:#fff;border-color:#1d4ed8;font-weight:700}}
  .tab-btn-ply{{padding:5px 12px;font-size:11.5px;font-family:inherit;
                border:1px solid #fca5a5;border-radius:6px 6px 0 0;
                background:#fff7f7;cursor:pointer;color:#b91c1c;transition:all .15s}}
  .tab-btn-ply.active{{background:#dc2626;color:#fff;border-color:#dc2626;font-weight:700}}
  .tab-pane{{}}
  .tstat{{font-size:10px;font-weight:400;opacity:.85}}
  .cut-wrap{{font-size:11.5px}}
  table.cut-tbl,table.ply-tbl{{width:100%;border-collapse:collapse;
                                font-size:11.5px;margin-bottom:8px}}
  table.cut-tbl th,table.ply-tbl th{{background:#f5f5f5;padding:5px 8px;
                                       text-align:left;color:#555;
                                       border-bottom:2px solid #ddd}}
  table.cut-tbl td,table.ply-tbl td{{padding:4px 8px;
                                       border-bottom:1px solid #f0f0f0}}
  .nocut{{padding:14px;text-align:center;color:#888;background:#f5f5f5;
          border-radius:6px;font-size:11px}}
  .ply-block{{margin-top:10px;padding:8px 12px;
              background:#fce4ec;border-left:3px solid #d32f2f;
              border-radius:4px}}
  .ply-block b{{color:#b71c1c;font-size:11.5px}}
  .footer{{text-align:center;padding:20px;font-size:11px;color:#999;
           border-top:1px solid #e0e0e0;margin-top:20px;background:#fff}}
  .footer code{{background:#f5f5f5;padding:1px 6px;border-radius:3px;
                font-family:Consolas,monospace;color:#555}}
</style>
</head><body>
<div class="header">
  <h1>🏗  석고보드 절단 최적화 결과 <small>— M3시스템즈 시공방식 (각재+{dir_label} / 1P / 자투리 재사용)</small></h1>
  <div class="sub">파일: {ifc_name} &nbsp;│&nbsp; 자재: {mat} {bw_label} &nbsp;│&nbsp; 시공: 1P {dir_label} &nbsp;│&nbsp; 생성: {now}</div>
</div>

<div class="summary">
  <div class="card"><div class="val">{len(results)}</div><div class="lbl">처리 벽 수</div></div>
  <div class="card"><div class="val">{total_boards}</div><div class="lbl">신규 보드 사용 (장)</div></div>
  <div class="card ok"><div class="val">{total_reuse}</div><div class="lbl">재사용 절감 (장)<br><span style="font-size:10px;opacity:.7">신규 대비 {(total_reuse/(total_boards+total_reuse)*100) if (total_boards+total_reuse)>0 else 0:.1f}% 절감</span></div></div>
  <div class="card {'bad' if total_loss>10 else ('warn' if total_loss>5 else 'ok')}">
    <div class="val">{total_loss:.2f}%</div><div class="lbl">전체 로스율<br><span style="font-size:10px;opacity:.7">미사용 자투리 포함</span></div></div>
  <div class="card warn"><div class="val">{total_waste_m2:.2f}</div><div class="lbl">폐기량 (㎡)</div></div>
  <div class="card"><div class="val">{cut_count}</div><div class="lbl">절단 횟수</div></div>
  <div class="card"><div class="val">{plywood_total}</div><div class="lbl">합판 보강 (곳)</div></div>
  {_order_card(order_info, surcharge_pct, surcharge_boards)}
</div>

{legend}
{cut_freq_html}
{floor_html}

<script>
function switchTabK(containerId, idx) {{
  var c = document.getElementById(containerId);
  c.querySelectorAll('.tab-pane').forEach(function(p) {{
    p.style.display = parseInt(p.dataset.idx) === idx ? 'block' : 'none';
  }});
  c.querySelectorAll('.tab-btn,.tab-btn-ply').forEach(function(b, i) {{
    b.classList.toggle('active', i === idx);
  }});
}}
</script>
<div class="footer">
  📐 M3시스템즈 시공 기준 적용 &nbsp;·&nbsp;
  보드 <code>{BW}×{BH}mm</code> &nbsp;·&nbsp;
  각재 <code>{STUD}mm</code> &nbsp;·&nbsp;
  재사용 최소 <code>{MIN_REUSE_W}×{MIN_REUSE_H}mm</code> &nbsp;·&nbsp;
  방향 {dir_label} &nbsp;·&nbsp; 1P 단겹
</div>
</body></html>"""
    return html


# ─────────────────────────────────────────
# 테스트
# ─────────────────────────────────────────
if __name__ == '__main__':
    sample_walls = [
        {'wall_id': 'W001', 'space_id': 'SP001', 'floor_id': '2F',
         'L': 3613, 'H': 2800, 'ow': 1000, 'oh': 2100, 'ox': 1200, 'oy': 0,
         'is_external': False,
         'openings': [{'ox': 1200, 'ow': 1000, 'oh': 2100, 'oy': 0}]},
        {'wall_id': 'W002', 'space_id': 'SP001', 'floor_id': '2F',
         'L': 4500, 'H': 2800, 'ow': 0, 'oh': 0, 'ox': 0, 'oy': 0,
         'is_external': True, 'openings': []},
        {'wall_id': 'W003', 'space_id': 'SP002', 'floor_id': '3F',
         'L': 3600, 'H': 2800, 'ow': 900, 'oh': 1200, 'ox': 1350, 'oy': 800,
         'is_external': False,
         'openings': [{'ox': 1350, 'ow': 900, 'oh': 1200, 'oy': 800}]},
    ]

    print("=" * 60)
    print("석고보드 최적화 v3 — M3시스템즈 시공방식 반영")
    print("=" * 60)

    results, total_loss, order_info = optimize_building(sample_walls)

    print(f"\n{'ID':<6} {'층':<4} {'면':<4} {'배치':>5} {'온장':>4} "
          f"{'재사용':>5} {'로스율':>7} {'합판보강':>10}")
    print("─" * 60)
    for r in results:
        ext = "외기" if r['is_external'] else "내기"
        ply = f"{len(r['plywood_zones'])}곳" if r['plywood_zones'] else "-"
        print(f"{r['wall_id']:<6} {r['floor_id']:<4} {ext:<4} "
              f"{r['layout']:>5} {r['boards']:>4} {r['reuse_in']:>5} "
              f"{r['loss_pct']:>6.1f}% {ply:>10}")
        for z in r['plywood_zones']:
            print(f"       └ [{z['type']}] x={z['x']} y={z['y']} "
                  f"{z['w']}×{z['h']}mm  {z['thick']}T 합판")

    print(f"\n전체 로스율: {total_loss:.2f}%")


# ═══════════════════════════════════════════════════════════
# 시뮬레이터 HTML 생성 (v4 — 자체완결형, 4조합 라이브 전환)
#   · 템플릿(simulator_ui.html) 불필요 — 모든 코드/데이터를 직접 생성
#   · f-string 중괄호 escaping 회피: 일반 문자열 + 토큰 치환 방식
# ═══════════════════════════════════════════════════════════

_CONFIG_DIMS = {'gyp1': (900, 1800), 'gyp2': (900, 1800),
                'ply1': (1220, 2440), 'ply2': (1220, 2440)}

_COMBO_LABELS = {'gyp1': '석고보드 1P', 'gyp2': '석고보드 2P',
                 'ply1': '합판 1P', 'ply2': '합판 2P'}


def _sim_safe_json(obj):
    """JSON을 <script> 안에 안전하게 삽입 — </script> 분리 방지."""
    import json as _json
    return _json.dumps(obj, ensure_ascii=False).replace('</', '<\\/')


def _wall_combo_data(r: dict) -> dict:
    """optimize_wall 결과 1개 → 시뮬레이터 조합 데이터."""
    pls = r.get('placements', [])
    def _lp(layer):
        return [{'x': p['x'], 'y': p['y'], 'w': p['w'], 'h': p['h'], 'type': p['type']}
                for p in pls if p.get('layer', 1) == layer and p.get('type') != 'opening']
    def _ops(layer):
        return [{'x': p['x'], 'y': p['y'], 'w': p['w'], 'h': p['h']}
                for p in pls if p.get('layer', 1) == layer and p.get('type') == 'opening']
    pls_L1 = _lp(1)
    pls_L2 = _lp(2)
    return {
        'boards':        r.get('boards', 0),
        'reuse_in':      r.get('reuse_in', 0),
        'loss_pct':      round(r.get('loss_pct', 0), 1),
        'layout':        r.get('layout', ''),
        'pls_L1':        pls_L1,
        'pls_L2':        pls_L2,
        'ops':           _ops(1),
        'plywood_zones': r.get('plywood_zones', []),
        'is_2p':         bool(pls_L2),
    }


def make_simulator_html(sim_walls, ifc_name, template_path=None,
                        opt_results=None, opt_results_all=None,
                        default_key='gyp1'):
    """자체완결형 시뮬레이터 HTML (템플릿 불필요).

    sim_walls       : export_simulator_walls() 결과 (벽 메타데이터)
    opt_results     : 단일 조합 결과 (opt_results_all 없을 때)
    opt_results_all : {'gyp1':results, 'gyp2':..., 'ply1':..., 'ply2':...}
    default_key     : 시작 조합
    """
    proj_name = ifc_name.replace('.ifc', '').replace('.IFC', '')

    # ── 조합 정규화 ──
    combos = {}
    if opt_results_all:
        combos = {k: v for k, v in opt_results_all.items() if v}
    elif opt_results:
        combos = {'gyp1': opt_results}
        default_key = 'gyp1'
    if combos and default_key not in combos:
        default_key = next(iter(combos))

    # ── 정적 벽 목록 (조합 무관: id/층/치수/내외기) ──
    sim_map = {w['wall_id']: w for w in sim_walls}
    ref = combos.get(default_key) if combos else None
    if ref is None and combos:
        ref = next(iter(combos.values()))

    walls_static = []
    if ref:
        for r in ref:
            sw = sim_map.get(r['wall_id'], {})
            walls_static.append({
                'wall_id':     r['wall_id'],
                'floor_id':    r.get('floor_id', sw.get('storey', '')),
                'storey':      sw.get('storey', r.get('floor_id', '')),
                'is_external': r.get('is_external', False),
                'L':           r['L'],
                'H':           r['H'],
            })
    else:
        for sw in sim_walls:
            walls_static.append({
                'wall_id': sw['wall_id'], 'floor_id': sw.get('storey', ''),
                'storey': sw.get('storey', ''), 'is_external': False,
                'L': sw.get('length', 0), 'H': sw.get('height', 0),
            })

    # ── 조합별 데이터 ──
    data = {}
    for key, res in combos.items():
        data[key] = {r['wall_id']: _wall_combo_data(r) for r in res}

    combo_list = [{'key': k, 'label': _COMBO_LABELS.get(k, k)}
                  for k in ['gyp1', 'gyp2', 'ply1', 'ply2'] if k in combos]
    if not combo_list:
        combo_list = [{'key': 'none', 'label': '결과 없음'}]
        data = {'none': {}}
        default_key = 'none'

    html = _SIM_TEMPLATE
    html = html.replace('__PROJ__', proj_name)
    html = html.replace('__WALLCNT__', str(len(walls_static)))
    html = html.replace('__STUD__', str(STUD))
    html = html.replace('/*__WALLS__*/[]', _sim_safe_json(walls_static))
    html = html.replace('/*__DATA__*/{}', _sim_safe_json(data))
    html = html.replace('/*__COMBOS__*/[]', _sim_safe_json(combo_list))
    html = html.replace('/*__DEFKEY__*/"gyp1"', _sim_safe_json(default_key))
    return html


_SIM_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"><title>시뮬레이터 — __PROJ__</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Malgun Gothic','나눔고딕',sans-serif;display:flex;flex-direction:column;height:100vh;background:#f0f4f8;overflow:hidden}
.hdr{background:#1a237e;color:#fff;padding:8px 16px;display:flex;align-items:center;gap:14px;flex-shrink:0}
.hdr-title{font-size:14px;font-weight:700}
.hdr-sub{font-size:10.5px;opacity:.72}
.hdr-sp{flex:1}
.combo-tog{display:flex;gap:3px}
.cbtn{padding:3px 11px;font-size:11px;border:1px solid rgba(255,255,255,.4);border-radius:4px;background:transparent;color:#fff;cursor:pointer;font-family:inherit}
.cbtn.active{background:#fff;color:#1a237e;font-weight:700}
.ltog{display:none;gap:3px;margin-left:10px}
.lbtn{padding:3px 10px;font-size:11px;border:1px solid rgba(255,255,255,.4);border-radius:4px;background:transparent;color:#fff;cursor:pointer;font-family:inherit}
.lbtn.active{background:#ffd54f;color:#1a237e;font-weight:700}
.main{display:flex;flex:1;overflow:hidden}
.sidebar{width:204px;flex-shrink:0;background:#fff;border-right:1px solid #e0e0e0;overflow-y:auto;display:flex;flex-direction:column}
.sb-search{padding:7px;border-bottom:1px solid #eee;position:sticky;top:0;background:#fff;z-index:2}
.sb-search input{width:100%;padding:5px 8px;border:1px solid #ddd;border-radius:5px;font-size:11px;font-family:inherit}
.fhdr{padding:5px 12px;font-size:10px;font-weight:700;color:#777;background:#f5f5f5;border-bottom:1px solid #eee}
.witem{padding:6px 12px;cursor:pointer;font-size:11px;border-bottom:1px solid #f0f0f0}
.witem:hover{background:#e8eaf6}
.witem.active{background:#1a237e;color:#fff}
.wi-id{font-weight:700;font-family:Consolas,monospace;font-size:10.5px}
.wi-dim{font-size:9.5px;opacity:.65;margin-top:1px}
.wi-info{font-size:9.5px;margin-top:1px}
.wi-info.ok{color:#2e7d32}.wi-info.warn{color:#e65100}.wi-info.bad{color:#c62828}
.witem.active .wi-info{color:rgba(255,255,255,.82)}
.ca{flex:1;display:flex;flex-direction:column;overflow:hidden}
.stats{background:#fff;border-bottom:1px solid #e0e0e0;padding:7px 14px;display:flex;gap:16px;align-items:center;flex-shrink:0;flex-wrap:wrap;min-height:46px}
.si{display:flex;flex-direction:column;align-items:center;min-width:52px}
.si .v{font-size:15px;font-weight:900;color:#1a237e;line-height:1}
.si .l{font-size:9px;color:#888;margin-top:2px;font-weight:600}
.ssep{width:1px;height:28px;background:#e0e0e0}
.sw{flex:1;overflow:auto;display:flex;align-items:center;justify-content:center;background:#f0f4f8;padding:14px}
.emsg{color:#aaa;font-size:12px;text-align:center}
.legend{display:flex;gap:9px;flex-wrap:wrap;padding:4px 14px 6px;font-size:10px;color:#555;background:#fff;border-top:1px solid #e8e8e8}
.legend i{display:inline-block;width:11px;height:11px;vertical-align:middle;margin-right:2px;border-radius:2px;border:1px solid #ccc}
.ctrl{background:#fff;border-top:1px solid #e0e0e0;padding:8px 14px;align-items:center;gap:8px;flex-shrink:0;display:none}
.cb{padding:4px 12px;font-size:11.5px;font-family:inherit;border:1px solid #c5cae9;border-radius:6px;background:#f5f5f5;cursor:pointer;color:#333}
.cb:hover{background:#e8eaf6}
.cb.play{background:#1a237e;color:#fff;border-color:#1a237e;font-weight:700}
.sinfo{font-size:10.5px;color:#666;min-width:64px;text-align:center;font-family:Consolas,monospace}
.prog{flex:1;min-width:80px;max-width:260px;height:5px;background:#e0e0e0;border-radius:3px;overflow:hidden}
.progf{height:100%;background:#1a237e;border-radius:3px;transition:width .08s}
.spd{display:flex;align-items:center;gap:5px;font-size:10.5px;color:#666}
.spd input{width:72px}
</style></head>
<body>
<div class="hdr">
  <div><div class="hdr-title">🏗 석고보드·합판 배치 시뮬레이터</div><div class="hdr-sub">__PROJ__ · __WALLCNT__개 벽</div></div>
  <div class="hdr-sp"></div>
  <div class="combo-tog" id="comboTog"></div>
  <div class="ltog" id="ltog">
    <button class="lbtn active" id="lb1" onclick="setLayer(1)">Layer 1</button>
    <button class="lbtn" id="lb2" onclick="setLayer(2)">Layer 2</button>
    <button class="lbtn" id="lb0" onclick="setLayer(0)" style="display:none">합판보강</button>
  </div>
</div>
<div class="main">
  <div class="sidebar">
    <div class="sb-search"><input type="text" placeholder="벽 검색..." id="sbSearch" oninput="buildSidebar()"/></div>
    <div id="wlist"></div>
  </div>
  <div class="ca">
    <div class="stats" id="stats"><div class="emsg" style="width:100%">← 벽을 선택하세요</div></div>
    <div class="sw" id="svgWrap"><div class="emsg">벽을 선택하면 시뮬레이션이 시작됩니다</div></div>
    <div class="legend">
      <span><i style="background:#dbeafe;border-color:#1d4ed8"></i>온장 L1</span>
      <span><i style="background:#ffedd5;border-color:#c2410c"></i>온장 L2</span>
      <span><i style="background:#bfdbfe;border-color:#1d4ed8"></i>절단 L1</span>
      <span><i style="background:#fed7aa;border-color:#c2410c"></i>절단 L2</span>
      <span><i style="background:#fef08a;border-color:#ca8a04"></i>직선절단(개구부)</span>
      <span><i style="background:#ede9fe;border-color:#7c3aed"></i>ㄴ노치절단</span>
      <span><i style="background:#bbf7d0;border-color:#16a34a"></i>♻재사용</span>
      <span><i style="background:#e2e8f0;border-color:#94a3b8"></i>개구부</span>
    </div>
    <div class="ctrl" id="ctrl">
      <button class="cb" onclick="goFirst()">⏮</button>
      <button class="cb" onclick="goPrev()">◀</button>
      <button class="cb play" id="playBtn" onclick="togglePlay()">▶ 재생</button>
      <button class="cb" onclick="goNext()">▶|</button>
      <button class="cb" onclick="goLast()">⏭</button>
      <span class="sinfo" id="sinfo">0 / 0</span>
      <div class="prog"><div class="progf" id="progf" style="width:0%"></div></div>
      <div class="spd">속도<input type="range" id="spd" min="50" max="1200" value="350" step="50" oninput="speed=+this.value;document.getElementById('spdLbl').textContent=this.value+'ms'"><span id="spdLbl">350ms</span></div>
    </div>
  </div>
</div>
<script>
var WALLS=/*__WALLS__*/[];
var DATA=/*__DATA__*/{};
var COMBOS=/*__COMBOS__*/[];
var DEFKEY=/*__DEFKEY__*/"gyp1";
var STUD=__STUD__;
var cur=null,combo=DEFKEY,layer=1,step=0,timer=null,playing=false,speed=350;

var CMAP={
  full_1:{f:'#dbeafe',s:'#1d4ed8'},full_2:{f:'#ffedd5',s:'#c2410c'},
  cut_1:{f:'#bfdbfe',s:'#1d4ed8'},cut_2:{f:'#fed7aa',s:'#c2410c'},
  edge_cut_1:{f:'#fef08a',s:'#ca8a04'},edge_cut_2:{f:'#fef08a',s:'#ca8a04'},
  notch_1:{f:'#ede9fe',s:'#7c3aed'},notch_2:{f:'#ede9fe',s:'#7c3aed'},
  reuse_1:{f:'#bbf7d0',s:'#16a34a'},reuse_2:{f:'#bbf7d0',s:'#16a34a'},
  opening:{f:'#e2e8f0',s:'#94a3b8'}
};
function ck(t){return t==='opening'?'opening':(t||'full')+'_'+layer;}

function wd(){
  if(!cur)return null;
  var d=DATA[combo];
  if(!d)return null;
  return d[cur.wall_id]||null;
}
function getPls(){
  var d=wd();
  if(!d)return[];
  if(layer===1)return d.pls_L1||[];
  if(layer===2)return d.pls_L2||[];
  return[];
}

function buildComboTog(){
  var h='';
  COMBOS.forEach(function(c){
    var ac=c.key===combo?' active':'';
    h+='<button class="cbtn'+ac+'" data-k="'+c.key+'" onclick="setCombo(this.dataset.k)">'+c.label+'</button>';
  });
  document.getElementById('comboTog').innerHTML=h;
}

function buildSidebar(){
  var q=(document.getElementById('sbSearch').value||'').toLowerCase();
  var d=DATA[combo]||{};
  var fl={};
  WALLS.forEach(function(w){var f=w.floor_id||w.storey||'?';if(!fl[f])fl[f]=[];fl[f].push(w);});
  var h='';
  Object.keys(fl).sort().forEach(function(f){
    var ws=fl[f];
    if(q)ws=ws.filter(function(w){return w.wall_id.toLowerCase().indexOf(q)>=0;});
    if(!ws.length)return;
    h+='<div class="fhdr">📍 '+f+'</div>';
    ws.forEach(function(w){
      var wc=d[w.wall_id]||{loss_pct:0,boards:0};
      var lc=wc.loss_pct>10?'bad':(wc.loss_pct>5?'warn':'ok');
      var ac=cur&&cur.wall_id===w.wall_id?' active':'';
      h+='<div class="witem'+ac+'" data-wid="'+w.wall_id+'" onclick="selWall(this.dataset.wid)">'+
        '<div class="wi-id">'+w.wall_id+(w.is_external?' 🔵':'')+'</div>'+
        '<div class="wi-dim">'+w.L+'×'+w.H+'mm</div>'+
        '<div class="wi-info'+(ac?'':' '+lc)+'">로스 '+wc.loss_pct+'% · '+wc.boards+'장</div>'+
        '</div>';
    });
  });
  document.getElementById('wlist').innerHTML=h||'<div class="emsg" style="padding:20px">검색 결과 없음</div>';
}

function selWall(wid){
  cur=null;
  for(var i=0;i<WALLS.length;i++){if(WALLS[i].wall_id===wid){cur=WALLS[i];break;}}
  if(!cur)return;
  stopAnim();layer=1;step=0;
  buildSidebar();
  syncLayerTog();
  document.getElementById('ctrl').style.display='flex';
  updStats();render();startAnim();
}

function setCombo(k){
  if(!DATA[k])return;
  combo=k;step=0;layer=1;stopAnim();
  buildComboTog();buildSidebar();syncLayerTog();
  if(cur){updStats();render();startAnim();}
}

function syncLayerTog(){
  var d=wd();
  var lt=document.getElementById('ltog');
  if(d&&d.is_2p){
    lt.style.display='flex';
    document.getElementById('lb0').style.display=(d.plywood_zones&&d.plywood_zones.length)?'':'none';
  }else if(d&&d.plywood_zones&&d.plywood_zones.length){
    lt.style.display='flex';
    document.getElementById('lb2').style.display='none';
    document.getElementById('lb0').style.display='';
  }else{
    lt.style.display='none';
  }
  ['lb1','lb2','lb0'].forEach(function(id,i){var el=document.getElementById(id);if(el)el.classList.toggle('active',[1,2,0][i]===layer);});
}

function setLayer(l){
  layer=l;step=0;stopAnim();
  ['lb1','lb2','lb0'].forEach(function(id,i){var el=document.getElementById(id);if(el)el.classList.toggle('active',[1,2,0][i]===l);});
  render();
  if(l!==0)startAnim();
}

function render(){
  if(!cur)return;
  if(layer===0){document.getElementById('svgWrap').innerHTML=plySVG();updProg();return;}
  var L=cur.L,H=cur.H,pls=getPls(),vis=pls.slice(0,step);
  var wrap=document.getElementById('svgWrap');
  var mW=Math.max(200,wrap.clientWidth-28),mH=Math.max(150,wrap.clientHeight-28);
  var sc=Math.min(mW/L,mH/H);
  var sw=Math.round(L*sc),sh=Math.round(H*sc);
  var d=wd();
  var sc2=layer===1?'#1d4ed8':'#c2410c';
  var p=['<svg viewBox="0 0 '+sw+' '+(sh+22)+'" width="'+sw+'" height="'+(sh+22)+'" xmlns="http://www.w3.org/2000/svg">'];
  p.push('<rect x="0" y="0" width="'+sw+'" height="'+sh+'" fill="#f8fafc" stroke="'+sc2+'" stroke-width="2"/>');
  var x=0;
  while(x<=L+0.5){var gx=Math.round(x*sc);p.push('<line x1="'+gx+'" y1="0" x2="'+gx+'" y2="'+sh+'" stroke="#d1d5db" stroke-width="0.6" stroke-dasharray="4 3"/>');x+=STUD;}
  // [걸치기] 보드가 개구부를 가로지르므로 보드 먼저, 개구부 빗금을 그 위에 덮어 그림
  vis.forEach(function(pl){
    var px=Math.round(pl.x*sc),py=Math.round((H-pl.y-pl.h)*sc);
    var pw=Math.max(1,Math.round(pl.w*sc)),ph=Math.max(1,Math.round(pl.h*sc));
    var t=pl.type||'full',c=CMAP[ck(t)]||CMAP.full_1;
    p.push('<rect x="'+px+'" y="'+py+'" width="'+pw+'" height="'+ph+'" fill="'+c.f+'" stroke="'+c.s+'" stroke-width="1"/>');
    if(pw>36&&ph>18){
      var ic=t==='reuse'?'♻':(t==='notch'?'ㄴ':(t.indexOf('cut')>=0?'✂':''));
      p.push('<text x="'+(px+pw/2)+'" y="'+(py+ph/2+4)+'" text-anchor="middle" font-size="9" fill="'+c.s+'" font-weight="600">'+ic+' '+pl.w+'×'+pl.h+'</text>');
    }
  });
  if(d&&d.ops){d.ops.forEach(function(o){
    var ox=Math.round(o.x*sc),oy=Math.round((H-o.y-o.h)*sc),ow=Math.round(o.w*sc),oh=Math.round(o.h*sc);
    p.push('<rect x="'+ox+'" y="'+oy+'" width="'+ow+'" height="'+oh+'" fill="#e2e8f0" stroke="#94a3b8" stroke-dasharray="5 3" stroke-width="1"/>');
    if(ow>28&&oh>16)p.push('<text x="'+(ox+ow/2)+'" y="'+(oy+oh/2+4)+'" text-anchor="middle" font-size="9" fill="#94a3b8">개구부</text>');
  });}
  p.push('<text x="'+(sw/2)+'" y="'+(sh+16)+'" text-anchor="middle" font-size="10" fill="#64748b">L='+L+' × H='+H+'mm · '+(cur.is_external?'외기':'내기')+(d?' · '+d.layout:'')+'</text>');
  p.push('</svg>');
  wrap.innerHTML=p.join('');
  updProg();
}

function plySVG(){
  var d=wd();
  if(!d||!d.plywood_zones||!d.plywood_zones.length)return'<div class="emsg">합판 보강 없음</div>';
  var L=cur.L,H=cur.H,wrap=document.getElementById('svgWrap');
  var mW=Math.max(200,wrap.clientWidth-28),mH=Math.max(150,wrap.clientHeight-28);
  var sc=Math.min(mW/L,mH/H),sw=Math.round(L*sc),sh=Math.round(H*sc);
  var ZC={'도어 보강':['#fee2e2','#dc2626'],'TV·상부장 보강':['#fef9c3','#ca8a04'],'선반 보강':['#ede9fe','#7c3aed']};
  var p=['<svg viewBox="0 0 '+sw+' '+(sh+22)+'" width="'+sw+'" height="'+(sh+22)+'" xmlns="http://www.w3.org/2000/svg">'];
  p.push('<rect x="0" y="0" width="'+sw+'" height="'+sh+'" fill="#f8fafc" stroke="#94a3b8" stroke-width="1.5"/>');
  var x=0;
  while(x<=L+0.5){var gx=Math.round(x*sc);p.push('<line x1="'+gx+'" y1="0" x2="'+gx+'" y2="'+sh+'" stroke="#e2e8f0" stroke-width="0.5"/>');x+=STUD;}
  if(d.ops){d.ops.forEach(function(o){
    var ox=Math.round(o.x*sc),oy=Math.round((H-o.y-o.h)*sc),ow=Math.round(o.w*sc),oh=Math.round(o.h*sc);
    p.push('<rect x="'+ox+'" y="'+oy+'" width="'+ow+'" height="'+oh+'" fill="#e2e8f0" stroke="#94a3b8" stroke-dasharray="3 2" stroke-width="0.8"/>');
  });}
  d.plywood_zones.forEach(function(z){
    var c=ZC[z.type]||['#fef3c7','#d97706'];
    var zx=Math.round(z.x*sc),zy=Math.round((H-z.y-z.h)*sc),zw=Math.round(z.w*sc),zh=Math.round(z.h*sc);
    p.push('<rect x="'+zx+'" y="'+zy+'" width="'+zw+'" height="'+zh+'" fill="'+c[0]+'" fill-opacity="0.7" stroke="'+c[1]+'" stroke-width="1.5"/>');
    if(zw>40&&zh>14)p.push('<text x="'+(zx+zw/2)+'" y="'+(zy+zh/2+4)+'" text-anchor="middle" font-size="9" font-weight="700" fill="'+c[1]+'">'+z.type+' '+z.w+'×'+z.h+'mm '+z.thick+'T</text>');
  });
  p.push('<text x="'+(sw/2)+'" y="'+(sh+16)+'" text-anchor="middle" font-size="10" fill="#64748b">합판 보강 구역</text>');
  p.push('</svg>');
  return p.join('');
}

function updStats(){
  var d=wd();
  if(!cur||!d)return;
  var lc=d.loss_pct>10?'#c62828':(d.loss_pct>5?'#e65100':'#2e7d32');
  function si(v,l){return'<div class="si"><div class="v">'+v+'</div><div class="l">'+l+'</div></div>';}
  function sep(){return'<div class="ssep"></div>';}
  document.getElementById('stats').innerHTML=
    si(d.boards,'온장 (장)')+sep()+
    si(d.reuse_in>0?d.reuse_in+'장':'—','재사용')+sep()+
    '<div class="si"><div class="v" style="color:'+lc+'">'+d.loss_pct+'%</div><div class="l">로스율</div></div>'+sep()+
    si(cur.L+'×'+cur.H,'치수(mm)')+sep()+
    si(cur.is_external?'외기':'내기','구분')+sep()+
    si(d.is_2p?'2P':'1P','겹수');
}

function updProg(){
  var tot=getPls().length;
  document.getElementById('sinfo').textContent=step+' / '+tot;
  document.getElementById('progf').style.width=(tot>0?(step/tot*100):0)+'%';
}

function startAnim(){
  if(playing||layer===0)return;
  if(step>=getPls().length)step=0;
  playing=true;
  var b=document.getElementById('playBtn');
  b.textContent='⏸ 일시정지';b.style.background='#455a64';b.style.borderColor='#455a64';
  adv();
}
function stopAnim(){
  if(timer){clearTimeout(timer);timer=null;}
  playing=false;
  var b=document.getElementById('playBtn');
  if(b){b.textContent='▶ 재생';b.style.background='#1a237e';b.style.borderColor='#1a237e';}
}
function adv(){
  if(!playing||!cur)return;
  if(step<getPls().length){step++;render();timer=setTimeout(adv,speed);}
  else stopAnim();
}
function togglePlay(){if(playing)stopAnim();else startAnim();}
function goFirst(){stopAnim();step=0;render();}
function goLast(){stopAnim();step=getPls().length;render();}
function goNext(){stopAnim();if(step<getPls().length){step++;render();}}
function goPrev(){stopAnim();if(step>0){step--;render();}}

window.addEventListener('resize',function(){if(cur)render();});

buildComboTog();
buildSidebar();
if(WALLS.length>0)selWall(WALLS[0].wall_id);
</script>
</body></html>"""
