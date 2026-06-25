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
  - 2P 이음매 교차 (layer_offset=STUD)
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
IS_2P         = True   # 2P 시공 여부

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
    """
    def __init__(self):
        self.by_space = {}
        self.by_floor = {}

    MAX_PER_BUCKET = 30

    def add(self, piece: dict, space_id: str, floor_id: str) -> bool:
        # [변경] 비대칭 최소 규격 체크
        if piece['w'] < MIN_REUSE_W or piece['h'] < MIN_REUSE_H:
            return False
        bucket = self.by_floor.setdefault(floor_id, [])
        if len(bucket) >= self.MAX_PER_BUCKET:
            return False
        item = {**piece, 'space_id': space_id, 'floor_id': floor_id}
        self.by_space.setdefault(space_id, []).append(item)
        bucket.append(item)
        return True

    def consume(self, need_w, need_h, space_id, floor_id):
        for pool in [self.by_space.get(space_id, []),
                     self.by_floor.get(floor_id, [])]:
            candidates = [(i, p) for i, p in enumerate(pool)
                          if p['w'] >= need_w and p['h'] >= need_h]
            if not candidates:
                continue
            idx, chosen = min(candidates, key=lambda ip: ip[1]['w'] * ip[1]['h'])
            pool.pop(idx)
            self._remove_from_other(chosen, space_id, floor_id)
            leftovers = self._split_leftover(chosen, need_w, need_h)
            return chosen, leftovers
        return None, []

    def _remove_from_other(self, piece, space_id, floor_id):
        for pool in [self.by_floor.get(floor_id, []),
                     self.by_space.get(space_id, [])]:
            if piece in pool:
                pool.remove(piece)

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
        return sum(len(v) for v in self.by_space.values())


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


def _columns_rtl(x_start: float, length: float,
                 layer_offset: int = 0) -> list:
    """끝부터 배치한 열 목록."""
    if length <= 0:
        return []
    x_end = x_start + length
    seams = _seam_positions_rtl(x_start, x_end, layer_offset)
    cols = []
    for i in range(len(seams) - 1):
        cx = seams[i]
        cw = round(seams[i + 1] - seams[i], 1)
        if cw < 0.5:
            continue
        t = 'full' if abs(cw - BW) < 0.5 else 'cut'
        cols.append((t, cx, cw))
    return cols


def _columns_ltr(x_start: float, length: float,
                 layer_offset: int = 0) -> list:
    """시작부터 배치한 열 목록 (자투리가 오른쪽 끝에 위치).
    개구부 오른쪽 구역에서 사용 → 자투리가 개구부 옆이 아닌 벽 오른쪽 끝으로 감."""
    if length <= 0:
        return []
    x_end = x_start + length
    seams = {round(x_start, 1), round(x_end, 1)}
    x = round(x_start + layer_offset, 1)
    while x < x_end:
        if x_start < x < x_end:
            seams.add(round(x, 1))
        x = round(x + BW, 1)
    seams = sorted(seams)
    cols = []
    for i in range(len(seams) - 1):
        cx = seams[i]
        cw = round(seams[i + 1] - seams[i], 1)
        if cw < 0.5:
            continue
        t = 'full' if abs(cw - BW) < 0.5 else 'cut'
        cols.append((t, cx, cw))
    return cols


def _split_span(x_start: float, w: float) -> list:
    """구간 [x_start, x_start+w]를 표준폭(BW) 열로 분할.
    개구부 위·아래 보드가 BW보다 넓은 단일판이 되지 않도록. 반환: [(x, w), ...]"""
    if w <= BW + 0.5:
        return [(round(x_start, 1), round(w, 1))]
    cols = _fix_thin_edge_columns(_columns_ltr(x_start, w, 0))
    return [(cx, cw) for t, cx, cw in cols if t != 'opening']


# ─────────────────────────────────────────
# [M3 STEP3] 슬리버(끝칸 자투리) 보정
# ─────────────────────────────────────────
def _fix_thin_edge_columns(cols, min_col=300):
    """
    벽 양 끝(첫/마지막) 비개구부 열이 min_col(300mm) 미만이면 인접 열에서
    폭을 빌려 min_col을 확보한다. 인접 열은 줄어들기만 하므로 모든 열이
    표준규격(≤BW)을 유지한다. 인접 열과 경계를 공유하지 않거나(개구부 사이)
    빌릴 폭이 부족하면 그대로 둔다. — 베이스 정리본 STEP3 반영.
    """
    if len(cols) < 2:
        return cols
    cols = [list(c) for c in cols]
    nidx = [i for i, c in enumerate(cols) if c[0] != 'opening']
    if len(nidx) < 2:
        return [tuple(c) for c in cols]

    def _borrow(thin_i, donor_i):
        _, x, w = cols[thin_i]
        _, dx, dw = cols[donor_i]
        adjacent = abs((x + w) - dx) < 0.5 or abs((dx + dw) - x) < 0.5
        if not adjacent:
            return
        need = round(min_col - w, 1)
        if dw - need <= 0.5:
            return
        if dx < x:   # donor 왼쪽 → thin은 마지막 열
            cols[donor_i][2] = round(dw - need, 1)
            cols[thin_i][1] = round(x - need, 1)
            cols[thin_i][2] = min_col
        else:        # donor 오른쪽 → thin은 첫 열
            cols[thin_i][2] = min_col
            cols[donor_i][1] = round(dx + need, 1)
            cols[donor_i][2] = round(dw - need, 1)

    if cols[nidx[0]][2] < min_col:
        _borrow(nidx[0], nidx[1])
    if cols[nidx[-1]][2] < min_col:
        _borrow(nidx[-1], nidx[-2])

    # 개구부 바로 옆 얇은 열도 보정 (중간 열)
    for ni, idx in enumerate(nidx):
        if ni in (0, len(nidx) - 1):
            continue
        if cols[idx][2] >= min_col:
            continue
        adj_opening = (
            (idx > 0 and cols[idx - 1][0] == 'opening') or
            (idx + 1 < len(cols) and cols[idx + 1][0] == 'opening')
        )
        if not adj_opening:
            continue
        # 왼쪽 비개구부 이웃 먼저 시도, 안 되면 오른쪽
        if ni > 0:
            _borrow(idx, nidx[ni - 1])
        if cols[idx][2] < min_col and ni + 1 < len(nidx):
            _borrow(idx, nidx[ni + 1])

    return [tuple(c) for c in cols]


# ─────────────────────────────────────────
# x축 열 계획 (통합)
# ─────────────────────────────────────────
def build_column_plan(L: float, ops_xw: list,
                      is_external: bool, layer: int = 1):
    """
    x방향 열 계획. 다중 개구부 지원.

    ops_xw: [(ox, ow), ...] — 개구부 x시작·폭 목록 (0개 이상)
    배치 규칙:
      · 왼쪽 끝 구역 : RTL → 자투리가 왼쪽 벽 가장자리
      · 개구부 사이 구역: LTR → 자투리가 다음 개구부 쪽, 온장이 이전 개구부 오른쪽에 밀착
      · 오른쪽 끝 구역: LTR → 자투리가 오른쪽 벽 가장자리
    결과: 자투리가 양쪽 벽 가장자리에 위치, 개구부 사이 구역은 온장이 이전 개구부에 밀착

    반환: (columns, 'RTLC')
    """
    offset = STUD if (IS_2P and layer == 2) else 0

    if not ops_xw:
        cols = _fix_thin_edge_columns(_columns_rtl(0, L, offset))
        return cols, 'RTL'

    ops = sorted(ops_xw, key=lambda o: o[0])
    cols = []
    prev_end = 0.0

    for i, (ox, ow) in enumerate(ops):
        zone_w = ox - prev_end
        if zone_w > 0.5:
            if i == 0:
                # 왼쪽 끝 구역: RTL → 자투리가 왼쪽 벽 가장자리
                cols += _columns_rtl(prev_end, zone_w, offset)
            else:
                # 개구부 사이 구역: LTR → 온장이 이전 개구부 오른쪽에 밀착, 자투리는 다음 개구부 쪽
                cols += _columns_ltr(prev_end, zone_w, offset)
        cols.append(('opening', round(ox, 1), round(ow, 1)))
        prev_end = ox + ow

    right_w = L - prev_end
    if right_w > 0.5:
        # 오른쪽 끝 구역: LTR → 자투리가 오른쪽 벽 가장자리
        cols += _columns_ltr(prev_end, right_w, offset)

    cols = _fix_thin_edge_columns(cols)
    return cols, 'RTLC'


def _col_waste_rate(cols: list) -> float:
    waste = 0.0
    board_area = 0.0
    for t, x, w in cols:
        if t == 'opening':
            continue
        board_area += BW * BH
        off_w = BW - w
        if 0 < off_w < MIN_REUSE_W:
            waste += off_w * BH
    return waste / max(board_area, 1)


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


def _simple_rows(H: float) -> list:
    return sorted(_rows_in_region(0, H), key=lambda r: r[1])


def build_row_plan(H: float, oh: float, oy: float, col_type: str):
    if col_type != 'opening':
        return _simple_rows(H), '-'

    opening_top = oy + oh
    rows_c = sorted(
        _rows_in_region(0, oy) +
        [('opening', round(oy, 1), oh)] +
        _rows_in_region(opening_top, H - opening_top),
        key=lambda r: r[1]
    )

    base = _simple_rows(H)
    rows_d = []
    for t, y, h in base:
        row_top = y + h
        op_top = oy + oh
        if y < op_top and row_top > oy:
            rows_d.append(('opening_overlap', y, h))
        else:
            rows_d.append((t, y, h))

    loss_c = _row_waste_rate(rows_c)
    loss_d = _row_waste_rate(rows_d)

    if loss_d < loss_c - 1e-6:
        return rows_d, 'D'
    return rows_c, 'C'


def _row_waste_rate(rows: list) -> float:
    waste = 0.0
    board_area = 0.0
    for t, y, h in rows:
        if t in ('opening', 'opening_overlap'):
            continue
        board_area += BW * BH
        off_h = BH - h
        if 0 < off_h < MIN_REUSE_H:
            waste += BW * off_h
    return waste / max(board_area, 1)


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
# 셀 처리 (v2와 동일)
# ─────────────────────────────────────────
def _process_cell_pair(col_w, col_x, h1, y1, h2, y2,
                       layer, space_id, floor_id, pool, stat):
    """개구부 아래(h1) + 위(h2) 보드를 원판 1장에서 재단.
    h1+h2 <= BH 인 경우에만 호출. 재사용 풀을 먼저 확인하고,
    둘 다 신규라면 보드 1장으로 낭비 계산. 반환: placement dict 리스트."""
    out = []
    found1, lf1 = pool.consume(col_w, h1, space_id, floor_id)
    found2, lf2 = pool.consume(col_w, h2, space_id, floor_id)

    if found1:
        stat['reuse_in'] += 1
        for lf in lf1:
            if pool.add(lf, space_id, floor_id): stat['reuse_out'] += 1
        out.append({'layer': layer, 'x': col_x, 'y': y1, 'w': col_w, 'h': h1, 'type': 'reuse'})
    if found2:
        stat['reuse_in'] += 1
        for lf in lf2:
            if pool.add(lf, space_id, floor_id): stat['reuse_out'] += 1
        out.append({'layer': layer, 'x': col_x, 'y': y2, 'w': col_w, 'h': h2, 'type': 'reuse'})

    need_new1 = not found1
    need_new2 = not found2

    if need_new1 and need_new2:
        # 둘 다 신규 → 원판 1장으로 처리
        stat['boards'] += 1
        off_w = round(BW - col_w, 1)
        if off_w > 0.5:
            if not pool.add({'w': off_w, 'h': BH}, space_id, floor_id):
                stat['waste_mm2'] += off_w * BH
        off_h = round(BH - h1 - h2, 1)
        if off_h > 0.5:
            if not pool.add({'w': col_w, 'h': off_h}, space_id, floor_id):
                stat['waste_mm2'] += col_w * off_h
        out.append({'layer': layer, 'x': col_x, 'y': y1, 'w': col_w, 'h': h1, 'type': 'cut_op'})
        out.append({'layer': layer, 'x': col_x, 'y': y2, 'w': col_w, 'h': h2, 'type': 'cut_op'})
    elif need_new1:
        stat['boards'] += 1
        off_w = round(BW - col_w, 1)
        if off_w > 0.5:
            if not pool.add({'w': off_w, 'h': BH}, space_id, floor_id):
                stat['waste_mm2'] += off_w * BH
        off_h = round(BH - h1, 1)
        if off_h > 0.5:
            if not pool.add({'w': col_w, 'h': off_h}, space_id, floor_id):
                stat['waste_mm2'] += col_w * off_h
        out.append({'layer': layer, 'x': col_x, 'y': y1, 'w': col_w, 'h': h1, 'type': 'cut_op'})
    elif need_new2:
        stat['boards'] += 1
        off_w = round(BW - col_w, 1)
        if off_w > 0.5:
            if not pool.add({'w': off_w, 'h': BH}, space_id, floor_id):
                stat['waste_mm2'] += off_w * BH
        off_h = round(BH - h2, 1)
        if off_h > 0.5:
            if not pool.add({'w': col_w, 'h': off_h}, space_id, floor_id):
                stat['waste_mm2'] += col_w * off_h
        out.append({'layer': layer, 'x': col_x, 'y': y2, 'w': col_w, 'h': h2, 'type': 'cut_op'})

    return out


def _process_cell(col_w, col_x, row_h, row_y, col_t, row_t,
                  layer, space_id, floor_id, pool, stat):
    is_full = (abs(col_w - BW) < 0.5 and abs(row_h - BH) < 0.5)
    found, leftovers = pool.consume(col_w, row_h, space_id, floor_id)
    if found:
        stat['reuse_in'] += 1
        for lf in leftovers:
            if pool.add(lf, space_id, floor_id):
                stat['reuse_out'] += 1
        return {'layer': layer, 'x': col_x, 'y': row_y, 'w': col_w, 'h': row_h, 'type': 'reuse'}

    stat['boards'] += 1
    # 오프컷 = 표준보드(BW×BH) − 사용(col_w×row_h) = L자.
    # 길로틴 분할: 측면 전체높이 스트립(off_w×BH) + 상단 스트립(col_w×off_h).
    # (기존엔 측면 높이를 row_h로 잡아 off_w×(BH−row_h) 면적이 누락됐었음)
    off_w = round(BW - col_w, 1)
    if off_w > 0.5:
        if pool.add({'w': off_w, 'h': BH}, space_id, floor_id):
            stat['reuse_out'] += 1
        else:
            stat['waste_mm2'] += off_w * BH

    off_h = round(BH - row_h, 1)
    if off_h > 0.5:
        if pool.add({'w': col_w, 'h': off_h}, space_id, floor_id):
            stat['reuse_out'] += 1
        else:
            stat['waste_mm2'] += col_w * off_h

    return {'layer': layer, 'x': col_x, 'y': row_y, 'w': col_w, 'h': row_h,
            'type': 'full' if is_full else 'cut'}


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

    ops_xw = [(ox, ow) for ox, ow, oh, oy in merged_ops]

    # 최적 y_case 추정
    best_y = 'C'; best_est = float('inf'); best_xc = 'RTL'
    for y_case in ('C', 'D'):
        waste = board_area = 0.0
        xc_used = 'RTL'
        for layer in layers:
            cols, xc = build_column_plan(L, ops_xw, is_ext, layer)
            xc_used = xc
            for col_t, col_x, col_w in cols:
                if col_t == 'opening': continue
                active_oh = active_oy = 0
                for op_ox, op_ow, op_oh, op_oy in merged_ops:
                    if op_ox < col_x + col_w and op_ox + op_ow > col_x:
                        active_oh = op_oh; active_oy = op_oy; break
                rows, _ = build_row_plan(H, active_oh, active_oy,
                                         col_t if active_oh else 'solid')
                for row_t, row_y, row_h in rows:
                    if row_t in ('opening', 'opening_overlap'): continue
                    board_area += BW * BH
                    off_w = BW - col_w; off_h = BH - row_h
                    if 0 < off_w < MIN_REUSE_W: waste += off_w * row_h
                    if 0 < off_h < MIN_REUSE_H: waste += col_w * off_h
        est = waste / max(board_area, 1)
        if est < best_est:
            best_est = est; best_y = y_case; best_xc = xc_used

    # Pool 반영 실행
    stat = {'boards': 0, 'reuse_in': 0, 'reuse_out': 0, 'waste_mm2': 0.0}
    placements = []
    x_case_used = best_xc
    for layer in layers:
        cols, _ = build_column_plan(L, ops_xw, is_ext, layer)
        for col_t, col_x, col_w in cols:
            if col_t == 'opening':
                # 실제 개구부 y·h 찾기 (전체 높이 대신 실제 문/창 치수 사용)
                op_oy_actual, op_oh_actual = 0, H
                for op_ox, op_ow, op_oh_m, op_oy_m in merged_ops:
                    if op_ox < col_x + col_w and op_ox + op_ow > col_x:
                        op_oy_actual = op_oy_m
                        op_oh_actual = op_oh_m
                        break
                op_top = op_oy_actual + op_oh_actual
                # 개구부 폭이 BW보다 넓으면 위/아래 보드도 표준폭으로 분할
                sub_cols = _split_span(col_x, col_w)

                above_h = H - op_top
                below_rows = _rows_in_region(0, op_oy_actual) if op_oy_actual > 0.5 else []
                above_rows = _rows_in_region(op_top, above_h) if above_h > 0.5 else []

                for sx, sw in sub_cols:
                    # 아래+위 각 1행씩이고 합이 BH 이하 → 원판 1장 페어링
                    if (len(below_rows) == 1 and len(above_rows) == 1 and
                            below_rows[0][2] + above_rows[0][2] <= BH):
                        _, by, bh = below_rows[0]
                        _, ay, ah = above_rows[0]
                        for p in _process_cell_pair(sw, sx, bh, by, ah, ay,
                                                    layer, space_id, floor_id, pool, stat):
                            placements.append(p)
                    else:
                        # 개구부 아래 보드
                        for row_t, row_y, row_h in below_rows:
                            p = _process_cell(sw, sx, row_h, row_y,
                                              'cut', row_t, layer,
                                              space_id, floor_id, pool, stat)
                            if p:
                                p['type'] = 'cut_op'
                                placements.append(p)
                        # 개구부 위 보드
                        for row_t, row_y, row_h in above_rows:
                            p = _process_cell(sw, sx, row_h, row_y,
                                              'cut', row_t, layer,
                                              space_id, floor_id, pool, stat)
                            if p:
                                p['type'] = 'cut_op'
                                placements.append(p)

                # 개구부 마커
                placements.append({'layer': layer, 'x': col_x, 'y': op_oy_actual,
                                   'w': col_w, 'h': op_oh_actual, 'type': 'opening'})
                continue

            active_oh = active_oy = 0
            for op_ox, op_ow, op_oh, op_oy in merged_ops:
                if op_ox < col_x + col_w and op_ox + op_ow > col_x:
                    active_oh = op_oh; active_oy = op_oy; break
            rows, _ = build_row_plan(H, active_oh, active_oy,
                                     col_t if active_oh else 'solid')
            for row_t, row_y, row_h in rows:
                if row_t in ('opening', 'opening_overlap'):
                    placements.append({'layer': layer, 'x': col_x, 'y': row_y,
                                       'w': col_w, 'h': row_h, 'type': 'opening'})
                    continue
                p = _process_cell(col_w, col_x, row_h, row_y, col_t, row_t,
                                  layer, space_id, floor_id, pool, stat)
                if p:
                    placements.append(p)

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
        'layout'        : x_case_used + best_y,
        'boards'        : stat['boards'],
        'reuse_in'      : stat['reuse_in'],
        'reuse_out'     : stat['reuse_out'],
        'waste_mm2'     : round(stat['waste_mm2']),
        'loss_pct'      : round(loss, 2),
        'opening_count' : len(merged_ops),
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

    total_loss = max(0.0, total_waste / max(total_board_area, 1) * 100)
    print(f"  → 전체 로스율: {total_loss:.2f}%")
    return results, total_loss


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
    cut_op_fill = '#fef08a'                        # 개구부 처리 보드: 노란색
    cut_op_stroke = '#ca8a04'                      # 개구부 처리 보드 테두리: 황갈색

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
        elif t == 'cut_op':
            board_fill, board_stroke = cut_op_fill, cut_op_stroke
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
            icon = '♻' if t == 'reuse' else ('□' if t == 'cut_op' else ('✂' if t == 'cut' else ''))
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
        cut   = sum(1 for p in lp if p.get('type') == 'cut')
        reuse = sum(1 for p in lp if p.get('type') == 'reuse')
        return full, cut, reuse

    _svg_uid_seq[0] += 1
    uid = _svg_uid_seq[0]

    # 탭 목록 구성: Layer1, (Layer2), (합판보강)
    tabs = []
    for ln in layers_present:
        st = _stats(by_layer.get(ln, []))
        if len(layers_present) == 1:
            label = f'석고보드 &nbsp;<span class="tstat">온장 {st[0]} · 절단 {st[1]} · 재사용 {st[2]}</span>'
        else:
            offset = f'+{STUD}mm 엇갈림 · ' if ln == 2 else ''
            label = (f'Layer {ln} &nbsp;'
                     f'<span class="tstat">{offset}온장 {st[0]} · 절단 {st[1]} · 재사용 {st[2]}</span>')
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


def make_opt_html(results: list, total_loss: float,
                  ifc_path: str, mat: str = "석고보드", ply: int = 2) -> str:
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
      <span><i class="sw" style="background:#fef08a;border:1px solid #ca8a04"></i>□ 개구부 처리</span>
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
  <h1>🏗  석고보드 절단 최적화 결과 <small>— M3시스템즈 시공방식 (각재+세로 시공 / 자투리 재사용)</small></h1>
  <div class="sub">파일: {ifc_name} &nbsp;│&nbsp; 자재: {mat} {bw_label} &nbsp;│&nbsp; 시공: {ply}P &nbsp;│&nbsp; 생성: {now}</div>
</div>

<div class="summary">
  <div class="card"><div class="val">{len(results)}</div><div class="lbl">처리 벽 수</div></div>
  <div class="card"><div class="val">{total_boards}</div><div class="lbl">총 사용 온장 (장)</div></div>
  <div class="card ok"><div class="val">{total_reuse}</div><div class="lbl">재사용 = 발주 절감 (장)<br><span style="font-size:10px;opacity:.7">신규 대비 {(total_reuse/(total_boards+total_reuse)*100) if (total_boards+total_reuse)>0 else 0:.1f}% 절감</span></div></div>
  <div class="card {'bad' if total_loss>10 else ('warn' if total_loss>5 else 'ok')}">
    <div class="val">{total_loss:.2f}%</div><div class="lbl">전체 로스율</div></div>
  <div class="card warn"><div class="val">{total_waste_m2:.2f}</div><div class="lbl">폐기량 (㎡)</div></div>
  <div class="card"><div class="val">{cut_count}</div><div class="lbl">절단 횟수</div></div>
  <div class="card"><div class="val">{plywood_total}</div><div class="lbl">합판 보강 (곳)</div></div>
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
  {'2P 이중겹 (이음매 ' + str(STUD) + 'mm 엇갈림)' if IS_2P else '1P 단겹'}
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

    results, total_loss = optimize_building(sample_walls)

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
      <span><i style="background:#fef08a;border-color:#ca8a04"></i>개구부처리</span>
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
  cut_op_1:{f:'#fef08a',s:'#ca8a04'},cut_op_2:{f:'#fef08a',s:'#ca8a04'},
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
  if(d&&d.ops){d.ops.forEach(function(o){
    var ox=Math.round(o.x*sc),oy=Math.round((H-o.y-o.h)*sc),ow=Math.round(o.w*sc),oh=Math.round(o.h*sc);
    p.push('<rect x="'+ox+'" y="'+oy+'" width="'+ow+'" height="'+oh+'" fill="#e2e8f0" stroke="#94a3b8" stroke-dasharray="5 3" stroke-width="1"/>');
    if(ow>28&&oh>16)p.push('<text x="'+(ox+ow/2)+'" y="'+(oy+oh/2+4)+'" text-anchor="middle" font-size="9" fill="#94a3b8">개구부</text>');
  });}
  vis.forEach(function(pl){
    var px=Math.round(pl.x*sc),py=Math.round((H-pl.y-pl.h)*sc);
    var pw=Math.max(1,Math.round(pl.w*sc)),ph=Math.max(1,Math.round(pl.h*sc));
    var t=pl.type||'full',c=CMAP[ck(t)]||CMAP.full_1;
    p.push('<rect x="'+px+'" y="'+py+'" width="'+pw+'" height="'+ph+'" fill="'+c.f+'" stroke="'+c.s+'" stroke-width="1"/>');
    if(pw>36&&ph>18){
      var ic=t==='reuse'?'♻':(t.indexOf('cut')>=0?'✂':'');
      p.push('<text x="'+(px+pw/2)+'" y="'+(py+ph/2+4)+'" text-anchor="middle" font-size="9" fill="'+c.s+'" font-weight="600">'+ic+' '+pl.w+'×'+pl.h+'</text>');
    }
  });
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
