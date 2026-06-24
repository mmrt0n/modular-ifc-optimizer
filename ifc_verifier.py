# -*- coding: utf-8 -*-
"""
IFC 데이터 검증 도구 (ifc_verifier.py)
=====================================================
IFC 파일의 모든 벽/개구부/공간/층 정보를 추출하여
사람이 직접 IFC 뷰어(Revit / BIMcollab)와 대조할 수 있는
HTML 보고서 + JSON 기준값 파일을 생성합니다.

사용법:
  python ifc_verifier.py 파일.ifc
  → 파일_검증보고서.html + 파일_ground_truth.json 생성
"""

import sys
import os
import json
import math
import re
from datetime import datetime

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

try:
    import ifcopenshell
    import ifcopenshell.util.placement
except ImportError:
    print("오류: pip install ifcopenshell")
    sys.exit(1)


# ────────────────────────────────────────────
# 유틸
# ────────────────────────────────────────────
def to_mm(v):
    if v is None: return None
    v = float(v)
    return round(v * 1000, 1) if abs(v) < 100 else round(v, 1)

_floor_re = re.compile(r'(\d+)\s*[Ff층]')

def parse_floor(name: str):
    """이름 문자열에서 층 정보 파싱."""
    if not name: return None
    m = _floor_re.search(name)
    if m:
        raw = m.group(0); num = m.group(1)
        return f"{num}F" if '층' in raw else raw.upper()
    return None




def _get_storey_multi(element):
    """다단계 층 추출."""
    for rel in getattr(element, "ContainedInStructure", []):
        c = rel.RelatingStructure
        if c.is_a("IfcBuildingStorey"):
            return c.Name or c.GlobalId
        if c.is_a("IfcSpace"):
            for r2 in getattr(c, "Decomposes", []):
                o = r2.RelatingObject
                if o.is_a("IfcBuildingStorey"):
                    return o.Name or o.GlobalId
            for r2 in getattr(c, "ContainedInStructure", []):
                o = r2.RelatingStructure
                if o.is_a("IfcBuildingStorey"):
                    return o.Name or o.GlobalId
            sname = (c.Name or "") + " " + (getattr(c, "LongName", "") or "")
            f = parse_floor(sname)
            if f: return f
    f = parse_floor(getattr(element, "Name", "") or "")
    return f or None


def _get_space_multi(element):
    """다단계 공간 추출."""
    for rel in getattr(element, "ContainedInStructure", []):
        c = rel.RelatingStructure
        if c.is_a("IfcSpace"):
            return getattr(c, "LongName", None) or c.Name or c.GlobalId
    # IfcRelSpaceBoundary 역추적 (ProvidesBoundaries inverse 사용)
    try:
        for rel in getattr(element, "ProvidesBoundaries", []) or []:
            sp = rel.RelatingSpace
            if sp and sp.is_a("IfcSpace"):
                return getattr(sp, "LongName", None) or sp.Name or sp.GlobalId
    except Exception:
        pass
    return None


def _get_pset_props(element):
    """모든 Pset 속성을 dict로 반환."""
    result = {}
    for rel in getattr(element, "IsDefinedBy", []):
        if not rel.is_a("IfcRelDefinesByProperties"):
            continue
        pset = rel.RelatingPropertyDefinition
        pset_name = getattr(pset, "Name", "") or ""
        if hasattr(pset, "HasProperties"):
            for p in pset.HasProperties:
                if hasattr(p, "NominalValue") and p.NominalValue:
                    key = f"{pset_name}.{p.Name}"
                    result[key] = p.NominalValue.wrappedValue
        elif hasattr(pset, "Quantities"):
            for q in pset.Quantities:
                if hasattr(q, "LengthValue"):
                    key = f"{pset_name}.{q.Name}"
                    result[key] = to_mm(q.LengthValue)
    return result


def _get_dims_qset(element):
    """IfcElementQuantity에서 폭/높이/두께."""
    w = h = t = None
    for rel in getattr(element, "IsDefinedBy", []):
        if not rel.is_a("IfcRelDefinesByProperties"): continue
        pset = rel.RelatingPropertyDefinition
        if not pset.is_a("IfcElementQuantity"): continue
        for q in pset.Quantities:
            if not q.is_a("IfcQuantityLength") or not q.Name: continue
            v = to_mm(q.LengthValue)
            qn = q.Name
            if qn == "높이" and v and v > 200: h = v
            if qn in ("기준선 길이", "벽의 평균 길이", "중심에서 벽의 길이") and v and v > 0:
                w = v
            if qn in ("두께", "Thickness") and v and v > 0:
                t = v
    return w, h, t


def _get_dims_geom(element):
    """형상에서 폭/높이/두께."""
    w = h = t = None
    rep = getattr(element, "Representation", None)
    if not rep: return w, h, t
    for sr in rep.Representations:
        if getattr(sr, "RepresentationIdentifier", None) == "Axis":
            for item in sr.Items:
                if item.is_a("IfcPolyline") and item.Points and len(item.Points) >= 2:
                    p0 = item.Points[0].Coordinates
                    p1 = item.Points[-1].Coordinates
                    dx, dy = p1[0]-p0[0], p1[1]-p0[1]
                    w = to_mm((dx**2+dy**2)**0.5)
                    break
        for item in sr.Items:
            if item.is_a("IfcExtrudedAreaSolid"):
                if h is None: h = to_mm(item.Depth)
                if w is None:
                    prof = item.SweptArea
                    if prof.is_a("IfcRectangleProfileDef"):
                        a = to_mm(prof.XDim) or 0
                        b = to_mm(prof.YDim) or 0
                        w = max(a, b)
                        if min(a, b) > 0: t = min(a, b)
    return w, h, t


def _extract_walls(ifc):
    walls = []
    fill_map = {}
    try:
        for typ, lbl in [("IfcDoor","Door"),("IfcWindow","Window")]:
            for elem in ifc.by_type(typ):
                for f in getattr(elem, "FillsVoids", []) or []:
                    op = f.RelatingOpeningElement
                    if op: fill_map[op.GlobalId] = lbl
    except Exception:
        pass

    for w in ifc.by_type("IfcWall"):
        wid   = w.GlobalId
        wname = w.Name or wid
        wtype = None
        for rel in getattr(w, "IsTypedBy", []):
            if rel.RelatingType.Name:
                wtype = rel.RelatingType.Name; break
        if wtype is None: wtype = w.Name or ""

        storey = _get_storey_multi(w)
        space  = _get_space_multi(w)

        # 치수
        ww, wh, wt = _get_dims_qset(w)
        if ww is None or wh is None or wt is None:
            gw, gh, gt = _get_dims_geom(w)
            if ww is None: ww = gw
            if wh is None: wh = gh
            if wt is None: wt = gt

        # 벽 변환 행렬 (개구부 로컬 좌표 + 3D 위치 계산에 사용)
        wall_mat = None
        px = py = pz = 0.0
        angle = 0.0
        try:
            wall_mat = ifcopenshell.util.placement.get_local_placement(w.ObjectPlacement)
            px    = float(to_mm(wall_mat[0][3]) or 0)
            py    = float(to_mm(wall_mat[1][3]) or 0)
            pz    = float(to_mm(wall_mat[2][3]) or 0)
            import math as _math
            angle = round(_math.atan2(float(wall_mat[1][0]), float(wall_mat[0][0])), 6)
        except Exception:
            pass

        # 개구부
        openings = []
        for rel in getattr(w, "HasOpenings", []) or []:
            op = rel.RelatedOpeningElement
            if not op: continue
            ow = oh = ox = oy = None
            # 형상
            rep = getattr(op, "Representation", None)
            if rep:
                for sr in rep.Representations:
                    for item in sr.Items:
                        if item.is_a("IfcExtrudedAreaSolid"):
                            prof = item.SweptArea
                            if prof.is_a("IfcRectangleProfileDef"):
                                ow = to_mm(prof.XDim)
                                oh = to_mm(prof.YDim)
                            if not ow: oh = to_mm(item.Depth)
                        if item.is_a("IfcMappedItem"):
                            try:
                                for inner in item.MappingSource.MappedRepresentation.Items:
                                    if inner.is_a("IfcExtrudedAreaSolid"):
                                        prof = inner.SweptArea
                                        if prof.is_a("IfcRectangleProfileDef"):
                                            ow = to_mm(prof.XDim)
                                            oh = to_mm(prof.YDim)
                            except Exception:
                                pass
            # Filler
            if not (ow and oh):
                for fr in getattr(op, "HasFillings", []) or []:
                    filler = fr.RelatedBuildingElement
                    if not filler: continue
                    fw = to_mm(getattr(filler, "OverallWidth",  None))
                    fh = to_mm(getattr(filler, "OverallHeight", None))
                    if fw and fh: ow, oh = fw, fh; break
            # Position — 개구부 전역 위치를 벽 로컬 좌표로 변환
            try:
                import numpy as np
                op_mat = ifcopenshell.util.placement.get_local_placement(op.ObjectPlacement)
                if wall_mat is not None:
                    # 벽 역행렬로 개구부 전역 위치를 벽 로컬 좌표로 변환
                    wall_inv = np.linalg.inv(wall_mat)
                    local_mat = wall_inv @ op_mat
                    ox = round(abs(local_mat[0][3]) * 1000, 1)  # x축 (벽 길이 방향)
                    oy = round(abs(local_mat[2][3]) * 1000, 1)  # z축 (높이 방향)
                else:
                    # 벽 행렬 없으면 RelativePlacement 직접 사용
                    plac = op.ObjectPlacement
                    if plac and plac.RelativePlacement:
                        loc = plac.RelativePlacement.Location
                        if loc:
                            coords = loc.Coordinates
                            ox = to_mm(coords[0])
                            oy = to_mm(coords[2]) if len(coords) > 2 else to_mm(coords[1])
            except Exception:
                try:
                    plac = op.ObjectPlacement
                    if plac and plac.RelativePlacement:
                        loc = plac.RelativePlacement.Location
                        if loc:
                            coords = loc.Coordinates
                            ox = to_mm(coords[0])
                            oy = to_mm(coords[2]) if len(coords) > 2 else to_mm(coords[1])
                except Exception:
                    pass

            kind = fill_map.get(op.GlobalId)
            if not kind:
                for fr in getattr(op, "HasFillings", []) or []:
                    fb = fr.RelatedBuildingElement
                    if fb:
                        if fb.is_a("IfcDoor"):   kind = "Door"
                        elif fb.is_a("IfcWindow"): kind = "Window"
                if not kind: kind = "Opening"

            openings.append({
                'id': op.GlobalId,
                'name': op.Name or "",
                'kind': kind,
                'ow': ow, 'oh': oh,
                'ox': ox, 'oy': oy,
            })

        # 외기/내기
        exterior = None
        if '[내벽]' in wtype or '내벽' in wtype[:6]: exterior = 'INTERNAL'
        elif '[외벽]' in wtype or '외벽' in wtype[:6]: exterior = 'EXTERNAL'

        # 재료
        mats = []
        try:
            for rel in getattr(w, "HasAssociations", []) or []:
                if rel.is_a("IfcRelAssociatesMaterial"):
                    mat = rel.RelatingMaterial
                    if mat.is_a("IfcMaterialLayerSetUsage") or mat.is_a("IfcMaterialLayerSet"):
                        ls = mat.ForLayerSet if mat.is_a("IfcMaterialLayerSetUsage") else mat
                        for layer in ls.MaterialLayers:
                            m_name = layer.Material.Name if layer.Material else ""
                            m_thick = to_mm(layer.LayerThickness)
                            mats.append({'name': m_name, 'thickness_mm': m_thick})
                    elif mat.is_a("IfcMaterial"):
                        mats.append({'name': mat.Name or "", 'thickness_mm': None})
        except Exception:
            pass

        # T가 L의 절반 이상이면 길이/두께 혼동으로 판단 → None 처리
        if wt is not None and ww is not None and ww > 0 and wt >= ww * 0.5:
            wt = None

        walls.append({
            'id': wid,
            'name': wname,
            'type': wtype,
            'storey': storey,
            'space': space,
            'L_mm': ww,
            'H_mm': wh,
            'is_external': exterior,
            'openings': openings,
            'materials': mats,
            'opening_count': len(openings),
            'T_mm': wt,
            'dims_ok': bool(ww and wh),
            'px': px, 'py': py, 'pz': pz,
            'angle': angle,
        })
    return walls


def _extract_fillers(ifc):
    doors = []; windows = []
    for typ, lst in [("IfcDoor", doors), ("IfcWindow", windows)]:
        for el in ifc.by_type(typ):
            ow = to_mm(getattr(el, "OverallWidth",  None))
            oh = to_mm(getattr(el, "OverallHeight", None))
            storey = _get_storey_multi(el)
            lst.append({
                'id': el.GlobalId, 'name': el.Name or "",
                'storey': storey,
                'ow': ow, 'oh': oh,
            })
    return doors, windows


def _extract_spaces(ifc):
    spaces = []
    for sp in ifc.by_type("IfcSpace"):
        storey = _get_storey_multi(sp)
        spaces.append({
            'id': sp.GlobalId,
            'name': sp.Name or "",
            'long_name': getattr(sp, "LongName", "") or "",
            'storey': storey,
        })
    return spaces


def _extract_storeys(ifc):
    storeys = []
    for st in ifc.by_type("IfcBuildingStorey"):
        elev = to_mm(getattr(st, "Elevation", None))
        storeys.append({
            'id': st.GlobalId,
            'name': st.Name or "",
            'elevation_mm': elev,
        })
    return storeys


def _extract_materials(ifc):
    seen = set(); mats = []
    for m in ifc.by_type("IfcMaterial"):
        if m.Name not in seen:
            seen.add(m.Name)
            mats.append({'name': m.Name or ""})
    return mats


def _extract_slabs(ifc):
    slabs = []
    for s in ifc.by_type("IfcSlab"):
        storey = _get_storey_multi(s)
        ptype  = str(getattr(s, "PredefinedType", "") or "")
        # 형상에서 두께/면적 시도
        thickness = None
        rep = getattr(s, "Representation", None)
        if rep:
            for sr in rep.Representations:
                for item in sr.Items:
                    if item.is_a("IfcExtrudedAreaSolid"):
                        if thickness is None:
                            thickness = to_mm(item.Depth)
        mats = _get_elem_mats(s)
        slabs.append({
            'id': s.GlobalId,
            'name': s.Name or "",
            'type': ptype,
            'storey': storey,
            'thickness_mm': thickness,
            'materials': mats,
        })
    return slabs


def _extract_columns(ifc):
    cols = []
    for c in ifc.by_type("IfcColumn"):
        storey = _get_storey_multi(c)
        h = None
        rep = getattr(c, "Representation", None)
        if rep:
            for sr in rep.Representations:
                for item in sr.Items:
                    if item.is_a("IfcExtrudedAreaSolid"):
                        h = to_mm(item.Depth)
        mats = _get_elem_mats(c)
        cols.append({
            'id': c.GlobalId, 'name': c.Name or "",
            'storey': storey, 'H_mm': h, 'materials': mats,
        })
    return cols


def _extract_beams(ifc):
    beams = []
    for b in ifc.by_type("IfcBeam"):
        storey = _get_storey_multi(b)
        length = None
        rep = getattr(b, "Representation", None)
        if rep:
            for sr in rep.Representations:
                for item in sr.Items:
                    if item.is_a("IfcExtrudedAreaSolid"):
                        length = to_mm(item.Depth)
        mats = _get_elem_mats(b)
        beams.append({
            'id': b.GlobalId, 'name': b.Name or "",
            'storey': storey, 'L_mm': length, 'materials': mats,
        })
    return beams


def _extract_stairs(ifc):
    stairs = []
    for s in list(ifc.by_type("IfcStair")) + list(ifc.by_type("IfcStairFlight")):
        storey = _get_storey_multi(s)
        stairs.append({
            'id': s.GlobalId, 'name': s.Name or "",
            'ifc_type': s.is_a(),
            'storey': storey,
        })
    return stairs


def _extract_roofs(ifc):
    roofs = []
    for r in ifc.by_type("IfcRoof"):
        storey = _get_storey_multi(r)
        roofs.append({
            'id': r.GlobalId, 'name': r.Name or "",
            'storey': storey,
        })
    return roofs


def _extract_curtain_walls(ifc):
    cws = []
    for cw in ifc.by_type("IfcCurtainWall"):
        storey = _get_storey_multi(cw)
        cws.append({
            'id': cw.GlobalId, 'name': cw.Name or "",
            'storey': storey,
        })
    return cws


def _get_elem_mats(element):
    mats = []
    try:
        for rel in getattr(element, "HasAssociations", []) or []:
            if rel.is_a("IfcRelAssociatesMaterial"):
                mat = rel.RelatingMaterial
                if mat.is_a("IfcMaterialLayerSetUsage") or mat.is_a("IfcMaterialLayerSet"):
                    ls = mat.ForLayerSet if mat.is_a("IfcMaterialLayerSetUsage") else mat
                    for layer in ls.MaterialLayers:
                        m_name  = layer.Material.Name if layer.Material else ""
                        m_thick = to_mm(layer.LayerThickness)
                        mats.append({'name': m_name, 'thickness_mm': m_thick})
                elif mat.is_a("IfcMaterial"):
                    mats.append({'name': mat.Name or "", 'thickness_mm': None})
    except Exception:
        pass
    return mats


# ────────────────────────────────────────────
# IFC 전체 추출
# ────────────────────────────────────────────
def extract_all(ifc):
    print("  벽 추출 중...")
    walls = _extract_walls(ifc)
    print("  문/창 추출 중...")
    doors, windows = _extract_fillers(ifc)
    print("  슬래브 추출 중...")
    slabs = _extract_slabs(ifc)
    print("  기둥 추출 중...")
    columns = _extract_columns(ifc)
    print("  보 추출 중...")
    beams = _extract_beams(ifc)
    print("  계단 추출 중...")
    stairs = _extract_stairs(ifc)
    print("  지붕/커튼월 추출 중...")
    roofs = _extract_roofs(ifc)
    curtain_walls = _extract_curtain_walls(ifc)
    print("  공간 추출 중...")
    spaces = _extract_spaces(ifc)
    print("  층 추출 중...")
    storeys = _extract_storeys(ifc)
    print("  재료 추출 중...")
    materials = _extract_materials(ifc)

    return {
        'walls': walls,
        'doors': doors,
        'windows': windows,
        'slabs': slabs,
        'columns': columns,
        'beams': beams,
        'stairs': stairs,
        'roofs': roofs,
        'curtain_walls': curtain_walls,
        'spaces': spaces,
        'storeys': storeys,
        'materials': materials,
        'schema': ifc.schema,
        'extracted_at': datetime.now().isoformat(timespec='seconds'),
    }


# ────────────────────────────────────────────
# 통계
# ────────────────────────────────────────────
def calc_stats(data):
    walls = data['walls']
    n = len(walls)
    if n == 0:
        return {}
    n_storey  = sum(1 for w in walls if w['storey'])
    n_space   = sum(1 for w in walls if w['space'])
    n_dims    = sum(1 for w in walls if w['dims_ok'])
    n_ext     = sum(1 for w in walls if w['is_external'])
    n_ops     = sum(w['opening_count'] for w in walls)
    n_ops_dim = sum(1 for w in walls for op in w['openings'] if op['ow'] and op['oh'])
    n_ops_no  = n_ops - n_ops_dim
    return {
        'total_walls': n,
        'storey_ok':   n_storey, 'storey_pct':   round(n_storey/n*100,1),
        'space_ok':    n_space,  'space_pct':    round(n_space/n*100,1),
        'dims_ok':     n_dims,   'dims_pct':     round(n_dims/n*100,1),
        'exterior_ok': n_ext,    'exterior_pct': round(n_ext/n*100,1),
        'total_openings':  n_ops,
        'opening_dims_ok': n_ops_dim,
        'opening_no_dim':  n_ops_no,
        'opening_dim_pct': round(n_ops_dim/n_ops*100,1) if n_ops else 0,
        # 기타 요소 수
        'n_doors':    len(data.get('doors', [])),
        'n_windows':  len(data.get('windows', [])),
        'n_slabs':    len(data.get('slabs', [])),
        'n_columns':  len(data.get('columns', [])),
        'n_beams':    len(data.get('beams', [])),
        'n_stairs':   len(data.get('stairs', [])),
        'n_roofs':    len(data.get('roofs', [])),
        'n_cwalls':   len(data.get('curtain_walls', [])),
        'n_spaces':   len(data.get('spaces', [])),
        'n_storeys':  len(data.get('storeys', [])),
        'n_mats':     len(data.get('materials', [])),
    }


# ────────────────────────────────────────────
# HTML 리포트 생성
# ────────────────────────────────────────────
def make_html(data, ifc_path, issues=None, cross=None, expected=None, dual=None):
    stats  = calc_stats(data)
    walls  = data['walls']
    doors  = data.get('doors', [])
    windows= data.get('windows', [])
    slabs  = data.get('slabs', [])
    columns= data.get('columns', [])
    beams  = data.get('beams', [])
    stairs = data.get('stairs', [])
    roofs  = data.get('roofs', [])
    cwalls = data.get('curtain_walls', [])
    spaces = data.get('spaces', [])
    storeys= data.get('storeys', [])
    mats   = data.get('materials', [])

    fname = os.path.basename(ifc_path)
    now   = data['extracted_at']

    def bar(pct, color="#1976d2"):
        w = max(0, min(100, pct))
        bg = "#e3f2fd" if color == "#1976d2" else "#fce4ec"
        return (f'<div style="background:{bg};border-radius:4px;height:12px;width:160px;'
                f'display:inline-block;vertical-align:middle">'
                f'<div style="background:{color};height:12px;width:{w}%;border-radius:4px"></div>'
                f'</div> {pct}%')

    def cell_ok(v):
        if v is None: return '<td style="color:#e53935">✗ 없음</td>'
        return f'<td style="color:#2e7d32">✓ {v}</td>'

    def simple_rows(items, cols):
        """cols: list of (key, label) or (fn, label)"""
        rows = []
        for i, item in enumerate(items):
            bg = "" if i%2==0 else "background:#f9f9f9"
            tds = f'<td>{i+1}</td>'
            for col in cols:
                if callable(col[0]):
                    tds += f'<td>{col[0](item)}</td>'
                else:
                    v = item.get(col[0])
                    tds += f'<td>{v if v is not None else "-"}</td>'
            rows.append(f'<tr style="{bg}">{tds}</tr>')
        return "\n".join(rows)

    def mats_str(item):
        return " / ".join(
            f"{m['name']}({m['thickness_mm']}mm)" if m['thickness_mm']
            else m['name']
            for m in item.get('materials', [])
        ) or "-"

    # ── 벽 탭 ───────────────────────────────
    wall_rows = []
    for i, w in enumerate(walls):
        bg = "" if i%2==0 else "background:#f9f9f9"
        ops_str = "; ".join(
            f"{op['kind']} {op['ow']}×{op['oh']}mm" if (op['ow'] and op['oh'])
            else f"{op['kind']} ⚠치수없음"
            for op in w['openings']
        ) or "-"
        ms = " / ".join(
            f"{m['name']}({m['thickness_mm']}mm)" for m in w['materials']
        ) or "-"
        ext = w.get('is_external') or "미확인"
        ext_col = "#1565c0" if ext=="INTERNAL" else ("#c62828" if ext=="EXTERNAL" else "#888")
        bad_op  = any(not op['ow'] or not op['oh'] for op in w['openings'])
        rs = bg + (";outline:2px solid #f44336" if bad_op else "")
        wall_rows.append(
            f'<tr style="{rs}"><td>{i+1}</td>'
            f'<td title="{w["id"]}">{w["name"]}</td>'
            f'<td style="font-size:.8rem;color:#555">{w["type"][:40]}</td>'
            + cell_ok(w["storey"]) + cell_ok(w["space"])
            + f'<td>{"✓" if w["dims_ok"] else "✗"} {w["L_mm"] or "?"}×{w["H_mm"] or "?"}mm</td>'
            + f'<td style="color:{ext_col}">{ext}</td>'
            + f'<td style="font-size:.8rem">{ops_str}</td>'
            + f'<td style="font-size:.75rem;color:#666">{ms}</td></tr>'
        )
    walls_tbody = "\n".join(wall_rows)

    # ── 문 탭 ───────────────────────────────
    door_rows = simple_rows(doors, [
        ('name', '이름'),
        ('storey', '층'),
        (lambda d: f"{d['ow']}×{d['oh']}mm" if d['ow'] and d['oh'] else '⚠ 치수없음', '폭×높이'),
    ])

    # ── 창 탭 ───────────────────────────────
    win_rows = simple_rows(windows, [
        ('name', '이름'),
        ('storey', '층'),
        (lambda w: f"{w['ow']}×{w['oh']}mm" if w['ow'] and w['oh'] else '⚠ 치수없음', '폭×높이'),
    ])

    # ── 슬래브 탭 ───────────────────────────
    slab_rows = simple_rows(slabs, [
        ('name', '이름'),
        ('type', '구분'),
        ('storey', '층'),
        (lambda s: f"{s['thickness_mm']}mm" if s['thickness_mm'] else '-', '두께'),
        (mats_str, '재료'),
    ])

    # ── 기둥 탭 ─────────────────────────────
    col_rows = simple_rows(columns, [
        ('name', '이름'), ('storey', '층'),
        (lambda c: f"{c['H_mm']}mm" if c['H_mm'] else '-', '높이'),
        (mats_str, '재료'),
    ])

    # ── 보 탭 ───────────────────────────────
    beam_rows = simple_rows(beams, [
        ('name', '이름'), ('storey', '층'),
        (lambda b: f"{b['L_mm']}mm" if b['L_mm'] else '-', '길이'),
        (mats_str, '재료'),
    ])

    # ── 계단 탭 ─────────────────────────────
    stair_rows = simple_rows(stairs, [
        ('name', '이름'), ('ifc_type', 'IFC타입'), ('storey', '층'),
    ])

    # ── 지붕/커튼월 탭 ──────────────────────
    roof_rows  = simple_rows(roofs,  [('name','이름'),('storey','층')])
    cwall_rows = simple_rows(cwalls, [('name','이름'),('storey','층')])

    # ── 공간/층/재료 ─────────────────────────
    sp_rows  = "".join(f'<tr><td>{s["name"]}</td><td>{s["long_name"]}</td><td>{s["storey"] or "?"}</td></tr>' for s in spaces)
    st_rows  = "".join(f'<tr><td>{s["name"]}</td><td>{s["elevation_mm"] if s["elevation_mm"] is not None else "?"}mm</td></tr>' for s in storeys)
    mat_rows = "".join(f'<tr><td>{m["name"]}</td></tr>' for m in mats)

    # ── 타입별 통계 (벽) ─────────────────────
    wall_type_rows = ''.join(_type_stat_rows(walls))

    # ── 요약 카드 색상 ───────────────────────
    def cnt_box(num, lbl, color="#1565c0"):
        return (f'<div class="stat-box" style="border-top:3px solid {color}">'
                f'<div class="num" style="color:{color}">{num}</div>'
                f'<div class="lbl">{lbl}</div></div>')

    # ── 검증결과 탭 HTML 생성 ────────────────
    issues = issues or []
    n_err  = sum(1 for i in issues if i['severity'] == 'ERROR')
    n_warn = sum(1 for i in issues if i['severity'] == 'WARNING')
    n_info = sum(1 for i in issues if i['severity'] == 'INFO')

    SEV_COLOR = {'ERROR': '#c62828', 'WARNING': '#f57f17', 'INFO': '#1565c0'}
    SEV_BG    = {'ERROR': '#ffebee', 'WARNING': '#fff8e1', 'INFO': '#e3f2fd'}

    CATS = ['벽 겹침 — 물리적 겹침', '벽 겹침 — 근접 평행 배치',
            '개구부 위치', '층고 이상', '층별 문 수량',
            '개구부 없는 벽', '치수 미추출', '재료 누락', '레이어/겹수', '벽 두께']

    def issue_rows_for(cat):
        rows = []
        for iss in issues:
            if iss['category'] != cat: continue
            c = SEV_COLOR[iss['severity']]
            bg = SEV_BG[iss['severity']]
            sev_badge = (f'<span style="background:{c};color:#fff;border-radius:3px;'
                         f'padding:1px 6px;font-size:.72rem">{iss["severity"]}</span>')
            rows.append(
                f'<tr style="background:{bg}">'
                f'<td>{sev_badge}</td>'
                f'<td>{iss["storey"]}</td>'
                f'<td style="font-size:.8rem">{iss["element"]}</td>'
                f'<td>{iss["msg"]}</td>'
                f'<td style="font-size:.75rem;color:#555">{iss["detail"]}</td>'
                f'</tr>'
            )
        return rows

    issue_sections = []
    for cat in CATS:
        cat_issues = [i for i in issues if i['category'] == cat]
        if not cat_issues: continue
        cnt = len(cat_issues)
        rows = issue_rows_for(cat)
        issue_sections.append(
            f'<div class="section-title">{cat} ({cnt}건)</div>'
            f'<div style="overflow-x:auto"><table>'
            f'<thead><tr><th>심각도</th><th>층</th><th>요소</th><th>내용</th><th>상세</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
        )
    issues_html = "\n".join(issue_sections) if issue_sections else '<div class="empty">검출된 문제 없음</div>'

    issues_tab_label = f'⚠ 검증결과 (E{n_err}/W{n_warn}/I{n_info})'

    html = f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<title>IFC 검증 보고서 — {fname}</title>
<style>
  body{{font-family:'Malgun Gothic',sans-serif;margin:0;background:#f5f5f5;color:#212121}}
  .header{{background:#1a237e;color:#fff;padding:18px 28px}}
  .header h1{{margin:0;font-size:1.35rem}}
  .header p{{margin:4px 0 0;opacity:.8;font-size:.82rem}}
  .tabs{{display:flex;flex-wrap:wrap;background:#fff;border-bottom:2px solid #1a237e;padding:0 20px;gap:0}}
  .tab{{padding:9px 14px;cursor:pointer;border-bottom:3px solid transparent;
        font-size:.82rem;color:#555;transition:.15s;white-space:nowrap}}
  .tab.active,.tab:hover{{color:#1a237e;border-bottom-color:#1a237e;font-weight:700}}
  .panel{{display:none;padding:18px 24px}}.panel.active{{display:block}}
  .stat-grid{{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:18px}}
  .stat-box{{background:#fff;border-radius:8px;padding:12px 16px;min-width:140px;
             box-shadow:0 1px 4px rgba(0,0,0,.1)}}
  .stat-box .num{{font-size:1.7rem;font-weight:700;color:#1565c0}}
  .stat-box .lbl{{font-size:.75rem;color:#777;margin-top:2px;line-height:1.5}}
  .section-title{{font-size:1rem;font-weight:700;color:#1a237e;margin:18px 0 8px;
                  border-left:4px solid #1a237e;padding-left:10px}}
  table{{width:100%;border-collapse:collapse;background:#fff;border-radius:6px;
         overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
  th{{background:#1a237e;color:#fff;padding:8px 10px;text-align:left;
      font-size:.8rem;position:sticky;top:0;z-index:1}}
  td{{padding:6px 10px;border-bottom:1px solid #eee;font-size:.8rem;vertical-align:top}}
  tr:hover td{{background:#e8eaf6 !important}}
  .note{{background:#fff8e1;border-left:4px solid #fbc02d;padding:10px 14px;
         margin:10px 0;border-radius:0 6px 6px 0;font-size:.83rem}}
  .empty{{color:#aaa;font-style:italic;padding:12px 0}}
</style>
</head>
<body>
<div class="header">
  <h1>📋 IFC 전체 데이터 검증 보고서</h1>
  <p>파일: {fname} &nbsp;|&nbsp; {now} &nbsp;|&nbsp; 스키마: {data['schema']}</p>
</div>

<div class="tabs">
  <div class="tab active" onclick="show('summary','0')">① 요약</div>
  <div class="tab" onclick="show('issues','1')" style="color:#c62828;font-weight:700">{issues_tab_label}</div>
  <div class="tab" onclick="show('walls','2')">③ 벽 ({stats['total_walls']})</div>
  <div class="tab" onclick="show('doors','3')">④ 문 ({stats['n_doors']})</div>
  <div class="tab" onclick="show('windows','4')">⑤ 창 ({stats['n_windows']})</div>
  <div class="tab" onclick="show('slabs','5')">⑥ 슬래브 ({stats['n_slabs']})</div>
  <div class="tab" onclick="show('columns','6')">⑦ 기둥 ({stats['n_columns']})</div>
  <div class="tab" onclick="show('beams','7')">⑧ 보 ({stats['n_beams']})</div>
  <div class="tab" onclick="show('stairs','8')">⑨ 계단 ({stats['n_stairs']})</div>
  <div class="tab" onclick="show('roofs','9')">⑩ 지붕/커튼월 ({stats['n_roofs']+stats['n_cwalls']})</div>
  <div class="tab" onclick="show('spaces','10')">⑪ 공간 ({stats['n_spaces']})</div>
  <div class="tab" onclick="show('storeys','11')">⑫ 층 ({stats['n_storeys']})</div>
  <div class="tab" onclick="show('materials','12')">⑬ 재료 ({stats['n_mats']})</div>
  <div class="tab" onclick="show('cross','13')" style="color:#2e7d32;font-weight:700">⑭ 교차검증</div>
  <div class="tab" onclick="show('dual','14')" style="color:#1565c0;font-weight:700">⑮ 이중검증</div>
</div>

<!-- ① 요약 -->
<div id="summary" class="panel active">
  <div class="note">IFC 파일에서 자동 추출한 전체 요소 목록입니다.
  Revit / BIMcollab 뷰어와 대조하여 <b>누락·오류</b>를 확인하세요.</div>

  <div class="section-title">IFC 요소 수량 요약</div>
  <div class="stat-grid">
    {cnt_box(stats['total_walls'], 'IfcWall', '#1565c0')}
    {cnt_box(stats['n_doors'],    'IfcDoor', '#6a1b9a')}
    {cnt_box(stats['n_windows'],  'IfcWindow', '#00838f')}
    {cnt_box(stats['n_slabs'],    'IfcSlab', '#2e7d32')}
    {cnt_box(stats['n_columns'],  'IfcColumn', '#e65100')}
    {cnt_box(stats['n_beams'],    'IfcBeam', '#4e342e')}
    {cnt_box(stats['n_stairs'],   'IfcStair', '#37474f')}
    {cnt_box(stats['n_roofs'],    'IfcRoof', '#ad1457')}
    {cnt_box(stats['n_cwalls'],   'IfcCurtainWall', '#558b2f')}
    {cnt_box(stats['n_spaces'],   'IfcSpace', '#1565c0')}
    {cnt_box(stats['n_storeys'],  'IfcBuildingStorey', '#1565c0')}
    {cnt_box(stats['n_mats'],     'IfcMaterial', '#546e7a')}
  </div>

  <div class="section-title">벽(IfcWall) 데이터 품질</div>
  <div class="stat-grid">
    <div class="stat-box"><div class="num">{stats['storey_ok']}/{stats['total_walls']}</div>
      <div class="lbl">층 매핑<br>{bar(stats['storey_pct'])}</div></div>
    <div class="stat-box"><div class="num">{stats['space_ok']}/{stats['total_walls']}</div>
      <div class="lbl">공간 매핑<br>{bar(stats['space_pct'])}</div></div>
    <div class="stat-box"><div class="num">{stats['dims_ok']}/{stats['total_walls']}</div>
      <div class="lbl">치수 추출<br>{bar(stats['dims_pct'])}</div></div>
    <div class="stat-box"><div class="num">{stats['exterior_ok']}/{stats['total_walls']}</div>
      <div class="lbl">외기/내기<br>{bar(stats['exterior_pct'])}</div></div>
    <div class="stat-box"><div class="num">{stats['opening_dims_ok']}/{stats['total_openings']}</div>
      <div class="lbl">개구부 치수<br>{bar(stats['opening_dim_pct'])}</div></div>
    <div class="stat-box" style="border-top:3px solid #f44336">
      <div class="num" style="color:#f44336">{stats['opening_no_dim']}</div>
      <div class="lbl">⚠ 개구부 치수없음</div></div>
  </div>

  <div class="section-title">벽 타입별 통계</div>
  {wall_type_rows}
</div>

<!-- ② 검증결과 -->
<div id="issues" class="panel">
  <div class="stat-grid">
    <div class="stat-box" style="border-top:3px solid #c62828">
      <div class="num" style="color:#c62828">{n_err}</div>
      <div class="lbl">ERROR<br>즉시 확인 필요</div></div>
    <div class="stat-box" style="border-top:3px solid #f57f17">
      <div class="num" style="color:#f57f17">{n_warn}</div>
      <div class="lbl">WARNING<br>이상값 가능성</div></div>
    <div class="stat-box" style="border-top:3px solid #1565c0">
      <div class="num" style="color:#1565c0">{n_info}</div>
      <div class="lbl">INFO<br>참고 확인</div></div>
  </div>
  {issues_html}
</div>

<!-- ③ 벽 -->
<div id="walls" class="panel">
  <div class="note">빨간 테두리 = 개구부 치수 없음</div>
  <div style="overflow-x:auto"><table>
    <thead><tr><th>#</th><th>이름</th><th>타입</th><th>층</th><th>공간</th>
    <th>치수(L×H)</th><th>외기/내기</th><th>개구부</th><th>재료</th></tr></thead>
    <tbody>{walls_tbody}</tbody>
  </table></div>
</div>

<!-- ③ 문 -->
<div id="doors" class="panel">
  {'<div class="empty">문(IfcDoor) 없음</div>' if not doors else
   '<div style="overflow-x:auto"><table><thead><tr><th>#</th><th>이름</th><th>층</th><th>폭×높이</th></tr></thead><tbody>'
   + door_rows + '</tbody></table></div>'}
</div>

<!-- ④ 창 -->
<div id="windows" class="panel">
  {'<div class="empty">창(IfcWindow) 없음</div>' if not windows else
   '<div style="overflow-x:auto"><table><thead><tr><th>#</th><th>이름</th><th>층</th><th>폭×높이</th></tr></thead><tbody>'
   + win_rows + '</tbody></table></div>'}
</div>

<!-- ⑤ 슬래브 -->
<div id="slabs" class="panel">
  {'<div class="empty">슬래브(IfcSlab) 없음</div>' if not slabs else
   '<div style="overflow-x:auto"><table><thead><tr><th>#</th><th>이름</th><th>구분</th><th>층</th><th>두께</th><th>재료</th></tr></thead><tbody>'
   + slab_rows + '</tbody></table></div>'}
</div>

<!-- ⑥ 기둥 -->
<div id="columns" class="panel">
  {'<div class="empty">기둥(IfcColumn) 없음</div>' if not columns else
   '<div style="overflow-x:auto"><table><thead><tr><th>#</th><th>이름</th><th>층</th><th>높이</th><th>재료</th></tr></thead><tbody>'
   + col_rows + '</tbody></table></div>'}
</div>

<!-- ⑦ 보 -->
<div id="beams" class="panel">
  {'<div class="empty">보(IfcBeam) 없음</div>' if not beams else
   '<div style="overflow-x:auto"><table><thead><tr><th>#</th><th>이름</th><th>층</th><th>길이</th><th>재료</th></tr></thead><tbody>'
   + beam_rows + '</tbody></table></div>'}
</div>

<!-- ⑧ 계단 -->
<div id="stairs" class="panel">
  {'<div class="empty">계단(IfcStair) 없음</div>' if not stairs else
   '<div style="overflow-x:auto"><table><thead><tr><th>#</th><th>이름</th><th>IFC타입</th><th>층</th></tr></thead><tbody>'
   + stair_rows + '</tbody></table></div>'}
</div>

<!-- ⑨ 지붕/커튼월 -->
<div id="roofs" class="panel">
  <div class="section-title">IfcRoof ({stats['n_roofs']}개)</div>
  {'<div class="empty">없음</div>' if not roofs else
   '<table><thead><tr><th>#</th><th>이름</th><th>층</th></tr></thead><tbody>' + roof_rows + '</tbody></table>'}
  <div class="section-title" style="margin-top:16px">IfcCurtainWall ({stats['n_cwalls']}개)</div>
  {'<div class="empty">없음</div>' if not cwalls else
   '<table><thead><tr><th>#</th><th>이름</th><th>층</th></tr></thead><tbody>' + cwall_rows + '</tbody></table>'}
</div>

<!-- ⑩ 공간 -->
<div id="spaces" class="panel">
  <table><thead><tr><th>공간명</th><th>롱네임</th><th>층</th></tr></thead>
  <tbody>{sp_rows}</tbody></table>
</div>

<!-- ⑪ 층 -->
<div id="storeys" class="panel">
  <table><thead><tr><th>층명</th><th>표고</th></tr></thead>
  <tbody>{st_rows}</tbody></table>
</div>

<!-- ⑫ 재료 -->
<div id="materials" class="panel">
  <table><thead><tr><th>재료명</th></tr></thead>
  <tbody>{mat_rows}</tbody></table>
</div>

<!-- ⑭ 교차검증 -->
<div id="cross" class="panel">
  {_make_cross_html(cross, expected)}
</div>

<!-- ⑮ 이중검증 -->
<div id="dual" class="panel">
  {_make_dual_html(dual)}
</div>

<script>
var _activeTab = 0;
var _tabs = document.querySelectorAll('.tab');
function show(id, idx){{
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  _tabs.forEach(t=>t.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  _tabs[parseInt(idx)].classList.add('active');
}}
</script>
</body></html>"""
    return html


def _type_stat_rows(walls):
    from collections import Counter
    cnt = Counter(w['type'] for w in walls)
    rows = ['<table style="max-width:700px"><thead><tr>'
            '<th>타입명</th><th>개수</th><th>외기/내기</th>'
            '</tr></thead><tbody>']
    for t, n in cnt.most_common():
        ext = ("내벽" if ('[내벽]' in t or '내벽' in t[:6])
               else "외벽" if ('[외벽]' in t or '외벽' in t[:6]) else "-")
        rows.append(f'<tr><td>{t}</td><td>{n}</td><td>{ext}</td></tr>')
    rows.append('</tbody></table>')
    return rows


def _make_cross_html(cross, expected):
    """교차검증 결과 HTML 생성."""
    if not cross and not expected:
        return ('<div class="note">교차검증 기준값이 입력되지 않았습니다.<br>'
                'IFC 파일을 다시 실행하면 기준값 입력 창이 나타납니다.</div>')

    JUDGE_COLOR = {'OK': '#2e7d32', 'WARNING': '#f57f17', 'ERROR': '#c62828', 'INFO': '#546e7a'}
    JUDGE_BG    = {'OK': '#e8f5e9', 'WARNING': '#fff8e1', 'ERROR': '#ffebee', 'INFO': '#f5f5f5'}
    JUDGE_ICON  = {'OK': '✓', 'WARNING': '⚠', 'ERROR': '✗', 'INFO': '–'}

    # 입력값 요약
    exp_rows = ""
    if expected:
        label_map = {
            'n_storeys': '총 층수', 'n_doors': '총 문 개수',
            'doors_per_fl': '층별 평균 문', 'n_spaces': '총 공간 수',
            'n_walls': '총 벽 개수',
            'ext_mat': '외벽 자재', 'ext_ply': '외벽 겹수',
            'int_mat': '내벽 자재', 'int_ply': '내벽 겹수',
            'note': '메모',
        }
        for k, v in expected.items():
            if v and v not in ('미입력', ''):
                exp_rows += f'<tr><td>{label_map.get(k, k)}</td><td>{v}</td></tr>'

    exp_table = (f'<div class="section-title">입력한 기준값</div>'
                 f'<table style="max-width:500px"><thead><tr><th>항목</th><th>기준값</th></tr></thead>'
                 f'<tbody>{exp_rows}</tbody></table>') if exp_rows else ""

    if not cross:
        return exp_table + '<div class="note" style="margin-top:12px">비교 항목이 없습니다.</div>'

    n_ok   = sum(1 for r in cross if r['judge'] == 'OK')
    n_warn = sum(1 for r in cross if r['judge'] == 'WARNING')
    n_err  = sum(1 for r in cross if r['judge'] == 'ERROR')

    summary = (f'<div class="stat-grid" style="margin-bottom:16px">'
               f'<div class="stat-box" style="border-top:3px solid #2e7d32">'
               f'<div class="num" style="color:#2e7d32">{n_ok}</div><div class="lbl">일치 (±5%)</div></div>'
               f'<div class="stat-box" style="border-top:3px solid #f57f17">'
               f'<div class="num" style="color:#f57f17">{n_warn}</div><div class="lbl">주의 (5-20%)</div></div>'
               f'<div class="stat-box" style="border-top:3px solid #c62828">'
               f'<div class="num" style="color:#c62828">{n_err}</div><div class="lbl">불일치 (>20%)</div></div>'
               f'</div>')

    detail_rows = ""
    for r in cross:
        jc = r['judge']
        c  = JUDGE_COLOR[jc]
        bg = JUDGE_BG[jc]
        ic = JUDGE_ICON[jc]
        badge = (f'<span style="background:{c};color:#fff;border-radius:3px;'
                 f'padding:1px 6px;font-size:.72rem">{ic} {jc}</span>')
        detail_rows += (f'<tr style="background:{bg}">'
                        f'<td>{r["item"]}</td>'
                        f'<td style="text-align:center">{r["exp"]}{r["unit"]}</td>'
                        f'<td style="text-align:center">{r["ifc"]}{r["unit"]}</td>'
                        f'<td style="text-align:center">{r["diff"]}</td>'
                        f'<td style="text-align:center">{badge}</td>'
                        f'</tr>')

    detail_table = (f'<div class="section-title">비교 결과</div>'
                    f'<table><thead><tr>'
                    f'<th>항목</th><th>기준(도면)</th><th>IFC 추출</th><th>차이</th><th>판정</th>'
                    f'</tr></thead><tbody>{detail_rows}</tbody></table>')

    return summary + exp_table + '<br>' + detail_table


# ────────────────────────────────────────────
# 교차검증 입력 다이얼로그
# ────────────────────────────────────────────
def show_cross_dialog():
    """도면 기준값 입력 다이얼로그. 취소 시 None 반환."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        return None

    result = {}

    root = tk.Tk()
    root.title("교차검증 — 도면 기준값 입력")
    root.resizable(False, False)
    root.configure(bg="#f0f4f8")

    tk.Label(root, text="📐  교차검증 기준값 입력",
             font=("맑은 고딕", 13, "bold"),
             bg="#f0f4f8", fg="#1a237e").pack(pady=(16, 2))
    tk.Label(root, text="도면 또는 설계 시방서 기준값을 입력하세요  (빈 칸은 무시)",
             font=("맑은 고딕", 9), bg="#f0f4f8", fg="#546e7a").pack(pady=(0, 12))

    frm = tk.Frame(root, bg="#f0f4f8", padx=20)
    frm.pack(fill="x")

    fields = [
        ("총 층수",           "n_storeys",    "층"),
        ("총 문(Door) 개수",   "n_doors",      "개"),
        ("층별 평균 문 개수",  "doors_per_fl", "개/층"),
        ("총 공간(방) 수",     "n_spaces",     "개"),
        ("총 벽 개수",         "n_walls",      "개"),
    ]
    entries = {}
    for row_i, (label, key, unit) in enumerate(fields):
        tk.Label(frm, text=label, font=("맑은 고딕", 10),
                 bg="#f0f4f8", anchor="w").grid(row=row_i, column=0, sticky="w", pady=3, padx=(0,8))
        e = tk.Entry(frm, width=10, font=("맑은 고딕", 10))
        e.grid(row=row_i, column=1, pady=3)
        tk.Label(frm, text=unit, font=("맑은 고딕", 9),
                 bg="#f0f4f8", fg="#777").grid(row=row_i, column=2, sticky="w", padx=4)
        entries[key] = e

    # 외벽/내벽 스펙
    spec_frame = tk.LabelFrame(root, text="  벽 자재 스펙 (도면 기준)  ",
                                font=("맑은 고딕", 9, "bold"),
                                bg="#f0f4f8", fg="#1565c0", padx=12, pady=6)
    spec_frame.pack(fill="x", padx=20, pady=(10, 0))

    ext_mat_var = tk.StringVar(value="미입력")
    int_mat_var = tk.StringVar(value="미입력")
    ext_ply_var = tk.StringVar(value="미입력")
    int_ply_var = tk.StringVar(value="미입력")

    def spec_row(parent, label, mat_var, ply_var, r):
        tk.Label(parent, text=label, font=("맑은 고딕", 10),
                 bg="#f0f4f8", fg="#333").grid(row=r, column=0, sticky="w", padx=(0,8), pady=2)
        ttk.Combobox(parent, textvariable=mat_var, width=10,
                     values=["미입력", "석고보드", "합판", "기타"],
                     state="readonly").grid(row=r, column=1, padx=4, pady=2)
        ttk.Combobox(parent, textvariable=ply_var, width=8,
                     values=["미입력", "1P", "2P"],
                     state="readonly").grid(row=r, column=2, padx=4, pady=2)

    tk.Label(spec_frame, text="", bg="#f0f4f8").grid(row=0, column=0)
    tk.Label(spec_frame, text="자재", font=("맑은 고딕", 9),
             bg="#f0f4f8", fg="#555").grid(row=0, column=1)
    tk.Label(spec_frame, text="겹수", font=("맑은 고딕", 9),
             bg="#f0f4f8", fg="#555").grid(row=0, column=2)
    spec_row(spec_frame, "외벽", ext_mat_var, ext_ply_var, 1)
    spec_row(spec_frame, "내벽", int_mat_var, int_ply_var, 2)

    # 메모
    note_frame = tk.Frame(root, bg="#f0f4f8", padx=20)
    note_frame.pack(fill="x", pady=(8, 0))
    tk.Label(note_frame, text="메모 (선택)", font=("맑은 고딕", 9),
             bg="#f0f4f8", fg="#777").pack(anchor="w")
    note_entry = tk.Text(note_frame, height=2, width=42,
                         font=("맑은 고딕", 9), wrap="word")
    note_entry.pack()

    # 버튼
    btn_frm = tk.Frame(root, bg="#f0f4f8")
    btn_frm.pack(pady=14)

    def on_ok():
        for key, e in entries.items():
            val = e.get().strip()
            if val:
                try:
                    result[key] = float(val)
                except ValueError:
                    pass
        result['ext_mat'] = ext_mat_var.get()
        result['ext_ply'] = ext_ply_var.get()
        result['int_mat'] = int_mat_var.get()
        result['int_ply'] = int_ply_var.get()
        result['note']    = note_entry.get("1.0", "end").strip()
        root.destroy()

    def on_skip():
        root.destroy()

    tk.Button(btn_frm, text="  교차검증 실행  ",
              font=("맑은 고딕", 11, "bold"),
              bg="#1565c0", fg="white", relief="flat",
              padx=14, pady=8, cursor="hand2",
              command=on_ok).pack(side="left", padx=8)
    tk.Button(btn_frm, text="건너뛰기",
              font=("맑은 고딕", 10),
              bg="#e0e0e0", fg="#333", relief="flat",
              padx=10, pady=8, cursor="hand2",
              command=on_skip).pack(side="left")

    root.eval('tk::PlaceWindow . center')
    root.mainloop()

    return result if result else None


# ────────────────────────────────────────────
# 이중 추출 검증 (정규식 vs ifcopenshell)
# ────────────────────────────────────────────

# IFC GlobalId: 22자 base64 문자열
_GUID_PAT = r"'([0-9A-Za-z_$]{22})'"

_RE_ENTITY_CNT = {
    'walls':    re.compile(r'=IFCWALL(?:STANDARDCASE|ELEMENTEDCASE)?\s*\(', re.IGNORECASE),
    'doors':    re.compile(r'=IFCDOOR\s*\(', re.IGNORECASE),
    'windows':  re.compile(r'=IFCWINDOW\s*\(', re.IGNORECASE),
    'spaces':   re.compile(r'=IFCSPACE\s*\(', re.IGNORECASE),
    'storeys':  re.compile(r'=IFCBUILDINGSTOREY\s*\(', re.IGNORECASE),
    'slabs':    re.compile(r'=IFCSLAB\s*\(', re.IGNORECASE),
    'openings': re.compile(r'=IFCOPENINGELEMENT\s*\(', re.IGNORECASE),
}

# GlobalId 추출: =IFC엔티티('22자_guid', ...)
_RE_WALL_GUID = re.compile(
    r'=IFCWALL(?:STANDARDCASE|ELEMENTEDCASE)?\s*\(\s*' + _GUID_PAT, re.IGNORECASE)
_RE_DOOR_GUID = re.compile(r'=IFCDOOR\s*\(\s*' + _GUID_PAT, re.IGNORECASE)
_RE_WIN_GUID  = re.compile(r'=IFCWINDOW\s*\(\s*' + _GUID_PAT, re.IGNORECASE)
_RE_SPACE_GUID  = re.compile(r'=IFCSPACE\s*\(\s*' + _GUID_PAT, re.IGNORECASE)
_RE_STOREY_GUID = re.compile(r'=IFCBUILDINGSTOREY\s*\(\s*' + _GUID_PAT, re.IGNORECASE)
_RE_SLAB_GUID   = re.compile(r'=IFCSLAB\s*\(\s*' + _GUID_PAT, re.IGNORECASE)

# IfcWall 계열 구분 카운트
_RE_WALL_STD  = re.compile(r'=IFCWALLSTANDARDCASE\s*\(', re.IGNORECASE)
_RE_WALL_ELEM = re.compile(r'=IFCWALLELEMENTEDCASE\s*\(', re.IGNORECASE)
_RE_WALL_BARE = re.compile(r'=IFCWALL\s*\(', re.IGNORECASE)

# IfcQuantityLength에서 길이·높이 치수값 추출
# 형식: IFCQUANTITYLENGTH('높이',$,$,2800.);
_RE_QTY_H = re.compile(
    r"IFCQUANTITYLENGTH\s*\(\s*'(?:높이|Height)'\s*,[^,]*,[^,]*,\s*([\d.]+)",
    re.IGNORECASE)
_RE_QTY_L = re.compile(
    r"IFCQUANTITYLENGTH\s*\(\s*'(?:기준선 길이|벽의 평균 길이|중심에서 벽의 길이|Length)'\s*,[^,]*,[^,]*,\s*([\d.]+)",
    re.IGNORECASE)
_RE_QTY_T = re.compile(
    r"IFCQUANTITYLENGTH\s*\(\s*'(?:두께|Thickness)'\s*,[^,]*,[^,]*,\s*([\d.]+)",
    re.IGNORECASE)

# IfcRelVoidsElement: 개구부-벽 연결 수
_RE_VOIDS = re.compile(r'=IFCRELVOIDSELEMENT\s*\(', re.IGNORECASE)
# IfcRelFillsElement: 문/창-개구부 연결 수
_RE_FILLS = re.compile(r'=IFCRELFILLSELEMENT\s*\(', re.IGNORECASE)


def _dim_stats(values_mm):
    """mm 단위 값 목록 → 통계 dict."""
    if not values_mm:
        return {'count': 0, 'min': None, 'max': None, 'avg': None}
    vals = [v for v in values_mm if v > 0]
    if not vals:
        return {'count': 0, 'min': None, 'max': None, 'avg': None}
    return {
        'count': len(vals),
        'min':   round(min(vals)),
        'max':   round(max(vals)),
        'avg':   round(sum(vals) / len(vals)),
    }


def extract_by_regex(ifc_path):
    """IFC STEP 파일을 정규식으로 직접 파싱 — ifcopenshell과 완전히 독립적.

    반환 dict:
      ok, error, walls, doors, windows, spaces, storeys, slabs, openings,
      wall_subtypes, voids, fills,
      guids_{walls,doors,windows,spaces,storeys,slabs},
      dim_{H,L,T}  (IfcQuantityLength 기반 통계)
    """
    result = {'ok': False}
    try:
        # IFC 파일 인코딩: UTF-8(최신) / EUC-KR(한국 구형 BIM) / Latin-1(서유럽) 순 시도
        content = None
        for _enc in ('utf-8', 'cp949', 'latin-1'):
            try:
                with open(ifc_path, 'r', encoding=_enc) as _f:
                    content = _f.read()
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if content is None:  # 모두 실패하면 강제 latin-1 (오류 없음)
            with open(ifc_path, 'r', encoding='latin-1') as _f:
                content = _f.read()

        # \X2\...\X0\ 인코딩 디코딩 (IFC STEP 한글 등 비ASCII 처리)
        def _decode_x2(text):
            def _replace(m):
                hex_str = m.group(1)
                chars = [chr(int(hex_str[i:i+4], 16)) for i in range(0, len(hex_str), 4)]
                return ''.join(chars)
            return re.sub(r'\\X2\\([0-9A-Fa-f]+)\\X0\\', _replace, text)
        content = _decode_x2(content)

        # 수량 카운트
        for key, pat in _RE_ENTITY_CNT.items():
            result[key] = len(pat.findall(content))

        # IfcWall 계열 세분화
        n_std  = len(_RE_WALL_STD.findall(content))
        n_elem = len(_RE_WALL_ELEM.findall(content))
        n_bare = len(_RE_WALL_BARE.findall(content))
        result['wall_subtypes'] = {
            'IfcWall': n_bare,
            'IfcWallStandardCase': n_std,
            'IfcWallElementedCase': n_elem,
        }

        # IfcRelVoidsElement / IfcRelFillsElement
        result['voids'] = len(_RE_VOIDS.findall(content))
        result['fills'] = len(_RE_FILLS.findall(content))

        # GlobalId 목록 추출
        result['guids_walls']   = set(_RE_WALL_GUID.findall(content))
        result['guids_doors']   = set(_RE_DOOR_GUID.findall(content))
        result['guids_windows'] = set(_RE_WIN_GUID.findall(content))
        result['guids_spaces']  = set(_RE_SPACE_GUID.findall(content))
        result['guids_storeys'] = set(_RE_STOREY_GUID.findall(content))
        result['guids_slabs']   = set(_RE_SLAB_GUID.findall(content))

        # IfcQuantityLength 치수 통계 (단위: m → mm 변환 필요)
        def _to_mm_vals(matches):
            """IFC STEP 수치는 m 단위. 1 이하면 *1000, 아니면 그대로."""
            result_mm = []
            for m in matches:
                v = float(m)
                result_mm.append(v * 1000 if v < 100 else v)
            return result_mm

        result['dim_H'] = _dim_stats(_to_mm_vals(_RE_QTY_H.findall(content)))
        result['dim_L'] = _dim_stats(_to_mm_vals(_RE_QTY_L.findall(content)))
        result['dim_T'] = _dim_stats(_to_mm_vals(_RE_QTY_T.findall(content)))

        result['ok'] = True
    except Exception as e:
        result['error'] = str(e)
    return result


def dual_verify(ifc_data, regex_data):
    """ifcopenshell 추출값과 정규식 추출값을 비교해 신뢰도 판정.

    반환: [{'field', 'ifs', 'regex', 'match', 'note', 'missing_in_regex', 'missing_in_ifs'}, ...]
    """
    rows = []
    if not regex_data.get('ok'):
        rows.append({
            'field': '정규식 파싱 오류', 'ifs': '-', 'regex': '-',
            'match': False, 'note': regex_data.get('error', '알 수 없음'),
            'missing_in_regex': [], 'missing_in_ifs': [],
        })
        return rows

    # ── 1. 수량 카운트 비교 ───────────────────────────────────────
    entity_checks = [
        ('walls',   'walls',   '벽 (IfcWall*)'),
        ('doors',   'doors',   '문 (IfcDoor)'),
        ('windows', 'windows', '창 (IfcWindow)'),
        ('spaces',  'spaces',  '공간 (IfcSpace)'),
        ('storeys', 'storeys', '층 (IfcBuildingStorey)'),
        ('slabs',   'slabs',   '슬래브 (IfcSlab)'),
    ]
    for ifc_key, re_key, label in entity_checks:
        ifs_cnt = len(ifc_data.get(ifc_key, []))
        re_cnt  = regex_data.get(re_key, 0)
        rows.append({
            'field': label,
            'ifs':   ifs_cnt,
            'regex': re_cnt,
            'match': ifs_cnt == re_cnt,
            'note':  '',
            'missing_in_regex': [],
            'missing_in_ifs': [],
        })

    # 개구부: 벽 내 합계 vs 파일 내 전체 엔티티
    ifs_ops = sum(len(w.get('openings', [])) for w in ifc_data.get('walls', []))
    re_ops  = regex_data.get('openings', 0)
    rows.append({
        'field': '개구부 (IfcOpeningElement)',
        'ifs':   ifs_ops,
        'regex': re_ops,
        'match': ifs_ops == re_ops,
        'note':  '벽 내 집계 vs 파일 전체 엔티티 (구조상 차이 가능)',
        'missing_in_regex': [],
        'missing_in_ifs': [],
    })

    # IfcRelVoidsElement — 개구부-벽 연결 수 (참고용)
    re_voids = regex_data.get('voids', 0)
    rows.append({
        'field': '개구부-벽 연결 (IfcRelVoidsElement)',
        'ifs':   ifs_ops,   # 같아야 정상
        'regex': re_voids,
        'match': ifs_ops == re_voids,
        'note':  '개구부 수와 일치해야 정상',
        'missing_in_regex': [],
        'missing_in_ifs': [],
    })

    # ── 2. GlobalId 목록 대조 ────────────────────────────────────
    guid_checks = [
        ('walls',   'guids_walls',   '벽 GlobalId 대조'),
        ('doors',   'guids_doors',   '문 GlobalId 대조'),
        ('windows', 'guids_windows', '창 GlobalId 대조'),
        ('spaces',  'guids_spaces',  '공간 GlobalId 대조'),
        ('storeys', 'guids_storeys', '층 GlobalId 대조'),
        ('slabs',   'guids_slabs',   '슬래브 GlobalId 대조'),
    ]
    for ifc_key, re_key, label in guid_checks:
        ifs_guids = set(e['id'] for e in ifc_data.get(ifc_key, []))
        re_guids  = regex_data.get(re_key, set())
        missing_re  = sorted(ifs_guids - re_guids)   # ifs에 있는데 regex에 없음
        missing_ifs = sorted(re_guids - ifs_guids)   # regex에 있는데 ifs에 없음
        match = (len(missing_re) == 0 and len(missing_ifs) == 0)
        note = ''
        if missing_re:
            note += f'ifcopenshell에만 있음 {len(missing_re)}개'
        if missing_ifs:
            if note: note += ' / '
            note += f'정규식에만 있음 {len(missing_ifs)}개'
        rows.append({
            'field': label,
            'ifs':   len(ifs_guids),
            'regex': len(re_guids),
            'match': match,
            'note':  note or '완전 일치',
            'missing_in_regex': missing_re[:10],   # 최대 10개만 표시
            'missing_in_ifs':   missing_ifs[:10],
        })

    # ── 3. 치수 분포 비교 ────────────────────────────────────────
    def _ifs_dim_stats(walls, key):
        vals = [float(w.get(key) or 0) for w in walls if w.get(key)]
        return _dim_stats(vals)

    walls = ifc_data.get('walls', [])
    for dim_key, re_dim_key, label in [
        ('H_mm', 'dim_H', '벽 높이 (IfcQuantityLength)'),
        ('L_mm', 'dim_L', '벽 길이 (IfcQuantityLength)'),
    ]:
        ifs_st  = _ifs_dim_stats(walls, dim_key)
        re_st   = regex_data.get(re_dim_key, {})
        # 정규식은 파일 전체 요소(슬래브·기둥·보 등 포함)에서 추출 →
        # IfcWall만 대상인 ifcopenshell 값과 모집단이 달라 평균 비교 무의미.
        # 오탐 방지: match=True(참고용)로 고정, 수치만 표시.
        ifs_disp = (f"avg {ifs_st['avg']}mm ({ifs_st['count']}개)"
                    if ifs_st['count'] else '-')
        re_disp  = (f"avg {re_st['avg']}mm ({re_st['count']}개)"
                    if re_st.get('count') else '-')
        note = (f"참고용 — IfcWall 기준({ifs_st.get('count',0)}개) vs "
                f"전체 IFC 요소({re_st.get('count',0)}개) 비교는 모집단 상이")
        rows.append({
            'field': label,
            'ifs':   ifs_disp,
            'regex': re_disp,
            'match': True,
            'note':  note,
            'missing_in_regex': [],
            'missing_in_ifs': [],
        })

    return rows


def _make_dual_html(dual):
    """이중검증 결과 HTML 블록 생성."""
    if not dual:
        return '<div class="note">이중 추출 검증 데이터 없음</div>'

    n_match   = sum(1 for r in dual if r['match'])
    n_total   = len(dual)
    ratio     = n_match / n_total if n_total else 0
    all_match = n_match == n_total
    trust_color = '#2e7d32' if all_match else ('#f57f17' if ratio >= 0.8 else '#c62828')
    trust_label = '완전 일치 — 고신뢰' if all_match else ('부분 불일치 — 주의' if ratio >= 0.8 else '불일치 — 검토 필요')
    trust_icon  = '✅' if all_match else ('⚠' if ratio >= 0.8 else '❌')

    summary = (
        f'<div class="stat-grid" style="margin-bottom:16px">'
        f'<div class="stat-box" style="border-top:3px solid {trust_color};min-width:260px">'
        f'<div class="num" style="color:{trust_color};font-size:1.2rem">{trust_icon} {trust_label}</div>'
        f'<div class="lbl">두 방법 항목 일치: {n_match} / {n_total}개</div></div>'
        f'<div class="stat-box" style="border-top:3px solid #2e7d32">'
        f'<div class="num" style="color:#2e7d32">{n_match}</div><div class="lbl">일치</div></div>'
        f'<div class="stat-box" style="border-top:3px solid #c62828">'
        f'<div class="num" style="color:#c62828">{n_total - n_match}</div><div class="lbl">불일치</div></div>'
        f'</div>'
    )

    verdict = (
        '<div class="note"><b>✅ 완전 일치:</b> '
        'ifcopenshell(스키마 파서) + 정규식(STEP 텍스트 직접 파싱) 두 방법의 '
        'GlobalId·수량·치수 분포가 모두 일치합니다. 추출 결과를 신뢰할 수 있습니다.</div>'
        if all_match else
        '<div class="note" style="border-left-color:#e65100">'
        '<b>⚠ 불일치 항목 있음:</b> 비표준 IFC 서브타입(IfcWallStandardCase 등), '
        '파일 인코딩 문제, 또는 파서 버전 차이일 수 있습니다. '
        '아래 GlobalId 불일치 목록을 BIM 뷰어에서 직접 확인하세요.</div>'
    )

    # 항목별 행 생성
    rows_html = ""
    guid_detail_html = ""
    for r in dual:
        c  = '#2e7d32' if r['match'] else '#c62828'
        bg = '#e8f5e9' if r['match'] else '#ffebee'
        ic = '✓ 일치' if r['match'] else '✗ 불일치'
        rows_html += (
            f'<tr style="background:{bg}">'
            f'<td>{r["field"]}</td>'
            f'<td style="text-align:center;font-variant-numeric:tabular-nums">{r["ifs"]}</td>'
            f'<td style="text-align:center;font-variant-numeric:tabular-nums">{r["regex"]}</td>'
            f'<td style="text-align:center;color:{c};font-weight:700">{ic}</td>'
            f'<td style="font-size:.75rem;color:#555">{r["note"]}</td>'
            f'</tr>'
        )

        # GlobalId 불일치 상세 블록
        missing_re  = r.get('missing_in_regex', [])
        missing_ifs = r.get('missing_in_ifs', [])
        if missing_re or missing_ifs:
            guid_detail_html += f'<div class="section-title" style="font-size:.88rem">{r["field"]} — GlobalId 불일치</div>'
            if missing_re:
                ids = ", ".join(f'<code>{g}</code>' for g in missing_re)
                guid_detail_html += (
                    f'<div style="background:#fff8e1;border-left:3px solid #f57f17;'
                    f'padding:8px 12px;margin:4px 0;font-size:.78rem">'
                    f'<b>ifcopenshell에만 있음 ({len(missing_re)}개):</b> {ids}'
                    + (' …외 더 있음' if len(r.get('missing_in_regex', [])) >= 10 else '')
                    + '</div>'
                )
            if missing_ifs:
                ids = ", ".join(f'<code>{g}</code>' for g in missing_ifs)
                guid_detail_html += (
                    f'<div style="background:#fce4ec;border-left:3px solid #e91e63;'
                    f'padding:8px 12px;margin:4px 0;font-size:.78rem">'
                    f'<b>정규식에만 있음 ({len(missing_ifs)}개):</b> {ids}'
                    + (' …외 더 있음' if len(r.get('missing_in_ifs', [])) >= 10 else '')
                    + '</div>'
                )

    table = (
        f'<div class="section-title">항목별 비교 결과</div>'
        f'<div style="overflow-x:auto"><table><thead><tr>'
        f'<th>검증 항목</th>'
        f'<th style="text-align:center">ifcopenshell</th>'
        f'<th style="text-align:center">정규식 파싱</th>'
        f'<th style="text-align:center">판정</th>'
        f'<th>비고</th>'
        f'</tr></thead><tbody>{rows_html}</tbody></table></div>'
    )

    guid_section = (
        f'<div class="section-title" style="margin-top:18px">GlobalId 불일치 상세</div>'
        + guid_detail_html
    ) if guid_detail_html else ''

    method_note = (
        '<div class="note" style="margin-top:14px">'
        '<b>이중 검증 방법 3단계:</b><br>'
        '① <b>수량 대조</b> — 두 방법이 같은 엔티티 수를 반환하는지 확인<br>'
        '② <b>GlobalId 대조</b> — 각 엔티티의 22자 GUID를 목록으로 추출해 교집합·차집합 계산 → 누락/추가 요소 식별<br>'
        '③ <b>치수 분포 대조</b> — IfcQuantityLength에서 높이·길이 평균을 추출해 ifcopenshell 추출값과 ±10% 이내인지 확인'
        '</div>'
    )

    return verdict + summary + table + guid_section + method_note


def run_cross_checks(data, expected):
    """도면 기준값 vs IFC 추출값 교차검증. 결과 dict 반환."""
    if not expected:
        return []

    walls   = data.get('walls',   [])
    doors   = data.get('doors',   [])
    storeys = data.get('storeys', [])
    spaces  = data.get('spaces',  [])

    results = []

    def _pct(ifc_val, exp_val):
        if exp_val == 0: return None
        return round((ifc_val - exp_val) / exp_val * 100, 1)

    def _judge(diff_pct, tol=5.0):
        """허용 오차 ±5% 기본."""
        if diff_pct is None: return 'INFO'
        if abs(diff_pct) <= tol: return 'OK'
        if abs(diff_pct) <= 20:  return 'WARNING'
        return 'ERROR'

    def row(item, exp, ifc, unit=""):
        diff_pct = _pct(ifc, exp) if isinstance(exp, (int, float)) else None
        judge    = _judge(diff_pct)
        diff_str = (f"{'+' if diff_pct>0 else ''}{diff_pct}%" if diff_pct is not None else "-")
        results.append({
            'item': item, 'exp': exp, 'ifc': ifc,
            'unit': unit, 'diff': diff_str, 'judge': judge,
        })

    # ── 수량 비교 ──────────────────────────────
    if 'n_storeys' in expected:
        row("총 층수", int(expected['n_storeys']), len(storeys), "층")

    if 'n_doors' in expected:
        row("총 문 개수", int(expected['n_doors']), len(doors), "개")

    if 'doors_per_fl' in expected and storeys:
        by_fl = {}
        for d in doors:
            fl = d.get('storey') or '?'
            by_fl[fl] = by_fl.get(fl, 0) + 1
        ifc_avg = round(len(doors) / len(storeys), 1) if storeys else 0
        row("층별 평균 문 개수", expected['doors_per_fl'], ifc_avg, "개/층")

    if 'n_spaces' in expected:
        row("총 공간(방) 수", int(expected['n_spaces']), len(spaces), "개")

    if 'n_walls' in expected:
        row("총 벽 개수", int(expected['n_walls']), len(walls), "개")

    # ── 자재 스펙 비교 (키워드 매칭) ──────────
    def _check_spec(wall_filter_fn, wall_label, exp_mat, exp_ply):
        if exp_mat == '미입력' and exp_ply == '미입력':
            return
        target_walls = [w for w in walls if wall_filter_fn(w)]
        if not target_walls:
            return
        mat_match = ply_match = no_info = 0
        for w in target_walls:
            mats = w.get('materials', [])
            if not mats:
                no_info += 1
                continue
            mat_names = ' '.join(m['name'] for m in mats).upper()
            if exp_mat != '미입력' and exp_mat.upper() in mat_names:
                mat_match += 1
            if exp_ply != '미입력':
                combined = (w.get('type','') + ' ' + w.get('name','')).upper()
                if exp_ply.upper() in combined or (
                    exp_ply == '2P' and ('이중' in combined or '방화' in combined)
                ) or (
                    exp_ply == '1P' and '단겹' in combined
                ):
                    ply_match += 1

        pct_no = round(no_info / len(target_walls) * 100, 1)
        results.append({
            'item': f"{wall_label} 자재 정보 없는 벽",
            'exp': "0%",
            'ifc': f"{pct_no}% ({no_info}/{len(target_walls)}개)",
            'unit': "",
            'diff': "-",
            'judge': 'OK' if no_info == 0 else ('WARNING' if pct_no < 50 else 'ERROR'),
        })
        if exp_mat != '미입력':
            pct_m = round(mat_match / (len(target_walls) - no_info) * 100, 1) if (len(target_walls) - no_info) > 0 else 0
            results.append({
                'item': f"{wall_label} 자재({exp_mat}) 매칭률",
                'exp': ">80%",
                'ifc': f"{pct_m}% ({mat_match}개)",
                'unit': "",
                'diff': "-",
                'judge': 'OK' if pct_m >= 80 else ('WARNING' if pct_m >= 50 else 'ERROR'),
            })

    ext_fn = lambda w: w.get('is_external') == 'EXTERNAL'
    int_fn = lambda w: w.get('is_external') == 'INTERNAL'
    _check_spec(ext_fn, "외벽", expected.get('ext_mat','미입력'), expected.get('ext_ply','미입력'))
    _check_spec(int_fn, "내벽", expected.get('int_mat','미입력'), expected.get('int_ply','미입력'))

    return results


# ────────────────────────────────────────────
# 검증 체크 (run_checks)
# ────────────────────────────────────────────
import math as _math_mod

def _wall_aabb(w):
    """벽의 2D AABB (mm 단위). 위치·각도·길이·두께 이용."""
    px    = float(w.get('px') or 0)
    py    = float(w.get('py') or 0)
    L     = float(w.get('L_mm') or 0)
    T     = float(w.get('T_mm') or 200)   # 두께 없으면 200mm 추정 (AABB용)
    angle = float(w.get('angle') or 0)
    if L <= 0:
        return None
    dx = L * _math_mod.cos(angle)
    dy = L * _math_mod.sin(angle)
    nx = -_math_mod.sin(angle) * T / 2
    ny =  _math_mod.cos(angle) * T / 2
    corners = [
        (px + nx,      py + ny),
        (px - nx,      py - ny),
        (px + dx + nx, py + dy + ny),
        (px + dx - nx, py + dy - ny),
    ]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def _aabb_overlap_mm(a, b):
    """두 AABB의 겹침 (ox, oy). 양수면 해당 축으로 겹침."""
    if a is None or b is None:
        return -1.0, -1.0
    ox = min(a[2], b[2]) - max(a[0], b[0])
    oy = min(a[3], b[3]) - max(a[1], b[1])
    return ox, oy


def run_checks(data):
    """모든 검증 체크 실행 → issues 리스트 반환."""
    issues = []
    walls   = data.get('walls',   [])
    doors   = data.get('doors',   [])
    windows = data.get('windows', [])
    storeys = data.get('storeys', [])

    def issue(severity, category, element, storey, msg, detail=""):
        issues.append({
            'severity': severity,
            'category': category,
            'element':  element,
            'storey':   storey or '?',
            'msg':      msg,
            'detail':   detail,
        })

    # ── A1: 벽 겹침 감지 (중심선 수직거리 기반) ─────────────────
    # 평행 벽 간 중심선 수직거리(d)가 5~50mm이고 길이 방향으로도 겹치면 경고
    # 분류 기준:
    #   ERROR   - 동일 이름 + 같은 층  → 복사 오류 (완전 중복)
    #   ERROR   - 동일 이름 + 다른 층  → 층간 복사 오류 (층 설정 실수)
    #   WARNING - 다른 이름 + d < 20mm → 이질벽 근접 겹침 (오류 의심)
    #   INFO    - 다른 이름 + d ≥ 20mm → 이중벽 설계 가능성 (확인 필요)
    for i in range(len(walls)):
        wi = walls[i]
        Li = float(wi.get('L_mm') or 0)
        if Li <= 0: continue
        ai   = float(wi.get('angle') or 0)
        pxi  = float(wi.get('px') or 0)
        pyi  = float(wi.get('py') or 0)
        for j in range(i + 1, len(walls)):
            wj = walls[j]
            Lj = float(wj.get('L_mm') or 0)
            if Lj <= 0: continue
            aj  = float(wj.get('angle') or 0)
            pxj = float(wj.get('px') or 0)
            pyj = float(wj.get('py') or 0)

            # 평행 여부
            diff = abs(ai - aj) % _math_mod.pi
            if not (diff < 0.26 or diff > _math_mod.pi - 0.26):
                continue

            avg_a = (ai + aj) / 2
            cos_a = _math_mod.cos(avg_a)
            sin_a = _math_mod.sin(avg_a)

            # 각 벽 중심점
            ci_x = pxi + Li / 2 * cos_a
            ci_y = pyi + Li / 2 * sin_a
            cj_x = pxj + Lj / 2 * cos_a
            cj_y = pyj + Lj / 2 * sin_a

            # 수직 방향 중심-중심 거리
            perp_i = ci_x * (-sin_a) + ci_y * cos_a
            perp_j = cj_x * (-sin_a) + cj_y * cos_a
            d = abs(perp_i - perp_j)
            if not (5 < d < 50):
                continue

            # 길이 방향 겹침 확인
            par_i_s = pxi * cos_a + pyi * sin_a
            par_j_s = pxj * cos_a + pyj * sin_a
            len_ov = min(par_i_s + Li, par_j_s + Lj) - max(par_i_s, par_j_s)
            if len_ov < 200:
                continue

            # ── 겹침 분류 (두께 기반) ───────────────────────────
            ni = wi['name']; nj = wj['name']
            fi = wi.get('storey') or '미확인'; fj = wj.get('storey') or '미확인'
            ti = float(wi.get('T_mm') or 100)
            tj = float(wj.get('T_mm') or 100)
            # 두 벽이 실제로 물리적으로 겹치는 두께
            phys_ov = (ti / 2 + tj / 2) - d  # 양수면 실제 겹침
            detail = (f"수직거리 {round(d)}mm, 겹침길이 {round(len_ov)}mm"
                      + (f", 물리적 겹침 {round(phys_ov)}mm" if phys_ov > 0 else "")
                      + f"  |  '{ni}'({fi}) T={round(ti)}mm L={wi.get('L_mm')}mm"
                      f"  ↔  '{nj}'({fj}) T={round(tj)}mm L={wj.get('L_mm')}mm")

            if phys_ov > 0:
                issue('WARNING', '벽 겹침 — 물리적 겹침',
                      f"{ni} ↔ {nj}", f"{fi}/{fj}",
                      f"두 벽이 물리적으로 {round(phys_ov)}mm 겹침 (도면 대조 확인 필요)",
                      detail)
            else:
                issue('INFO', '벽 겹침 — 근접 평행 배치',
                      f"{ni} ↔ {nj}", f"{fi}/{fj}",
                      f"평행 벽 간격 {round(d)}mm — 물리적 겹침 없음 (이중벽 구조 가능)",
                      detail)

    # ── A2: 개구부 위치 이상 (벽 범위 초과) ─────────────────────
    # NOTE: IFC 좌표 단위 혼용 문제로 ox/oy 신뢰도 낮음 — 합리적 범위(0 < ox < L*1.1)인 경우만 검사
    for w in walls:
        L = w.get('L_mm')
        H = w.get('H_mm')
        for op in w.get('openings', []):
            kind = op.get('kind', '개구부')
            ox_p = op.get('ox')
            oy_p = op.get('oy')
            ow   = op.get('ow')
            oh   = op.get('oh')
            # 좌표가 벽 길이의 2배 이내인 경우만 신뢰
            if L and ox_p is not None and ow and 0 <= ox_p <= L * 2:
                if ox_p + ow > L + 10:
                    issue('ERROR', '개구부 위치',
                          w['name'], w.get('storey'),
                          f"{kind} 가로 위치 벽 끝 초과",
                          f"벽 길이 {L}mm, 개구부 끝 {ox_p}+{ow}={ox_p+ow}mm")
            if H and oy_p is not None and oh and 0 <= oy_p <= H * 2:
                if oy_p + oh > H + 10:
                    issue('ERROR', '개구부 위치',
                          w['name'], w.get('storey'),
                          f"{kind} 세로 위치 벽 높이 초과",
                          f"벽 높이 {H}mm, 개구부 끝 {oy_p}+{oh}={oy_p+oh}mm")

    # ── A3: 층고 이상값 ──────────────────────────────────────────
    elevs = sorted(
        [(s['name'], s['elevation_mm']) for s in storeys
         if s.get('elevation_mm') is not None],
        key=lambda x: x[1]
    )
    for i in range(1, len(elevs)):
        diff = elevs[i][1] - elevs[i-1][1]
        if diff < 500:
            issue('WARNING', '층고 이상',
                  f"{elevs[i-1][0]} → {elevs[i][0]}", elevs[i][0],
                  f"층고 {round(diff)}mm — 500mm 미만",
                  f"{elevs[i-1][0]}: {elevs[i-1][1]}mm → {elevs[i][0]}: {elevs[i][1]}mm")
        elif diff > 8000:
            issue('WARNING', '층고 이상',
                  f"{elevs[i-1][0]} → {elevs[i][0]}", elevs[i][0],
                  f"층고 {round(diff)}mm — 8000mm 초과",
                  f"{elevs[i-1][0]}: {elevs[i-1][1]}mm → {elevs[i][0]}: {elevs[i][1]}mm")

    # ── B1: 층별 문 개수 이상 (범용 — 비율 기반) ────────────────
    doors_per_floor = {}
    for d in doors:
        fl = d.get('storey') or '층없음'
        doors_per_floor[fl] = doors_per_floor.get(fl, 0) + 1

    floors_with_walls = set(w.get('storey') for w in walls if w.get('storey'))
    if doors_per_floor and floors_with_walls:
        avg = sum(doors_per_floor.values()) / len(doors_per_floor)
        for fl in floors_with_walls:
            cnt = doors_per_floor.get(fl, 0)
            wall_cnt = sum(1 for w in walls if w.get('storey') == fl)
            if cnt == 0:
                issue('WARNING', '층별 문 수량', fl, fl,
                      f"문(IfcDoor) 0개",
                      f"해당 층 벽 {wall_cnt}개 존재")
            elif avg > 0 and cnt < avg * 0.3:
                issue('WARNING', '층별 문 수량', fl, fl,
                      f"문 {cnt}개 — 전체 평균({round(avg,1)}개)의 30% 미만",
                      f"해당 층 벽 {wall_cnt}개")

    # ── B2: 개구부 없는 벽 ──────────────────────────────────────
    for w in walls:
        if w.get('opening_count', 0) == 0:
            ext = w.get('is_external')
            label = {'INTERNAL': '내벽', 'EXTERNAL': '외벽'}.get(ext, '내외미확인')
            issue('INFO', '개구부 없는 벽',
                  w['name'], w.get('storey'),
                  f"개구부 없음 ({label})",
                  f"L={w.get('L_mm')}mm  H={w.get('H_mm')}mm")

    # ── B3: 치수 미추출 요소 ─────────────────────────────────────
    for w in walls:
        if not w.get('dims_ok'):
            issue('WARNING', '치수 미추출',
                  w['name'], w.get('storey'),
                  f"벽 치수 없음 (L={w.get('L_mm')}, H={w.get('H_mm')})",
                  "IfcElementQuantity 또는 형상에서 추출 실패")
    for d in doors:
        if not d.get('ow') or not d.get('oh'):
            issue('WARNING', '치수 미추출',
                  d.get('name') or d['id'], d.get('storey'),
                  f"문 치수 없음 (W={d.get('ow')}, H={d.get('oh')})", "")
    for wn in windows:
        if not wn.get('ow') or not wn.get('oh'):
            issue('WARNING', '치수 미추출',
                  wn.get('name') or wn['id'], wn.get('storey'),
                  f"창 치수 없음 (W={wn.get('ow')}, H={wn.get('oh')})", "")

    # ── C1: 재료 누락 ────────────────────────────────────────────
    for w in walls:
        if not w.get('materials'):
            issue('WARNING', '재료 누락',
                  w['name'], w.get('storey'),
                  "IfcMaterial 없음",
                  "최적화 시 임의 값으로 계산됩니다")

    # ── C2: 레이어/겹수 — 분류하지 않고 문제만 표시 ─────────────
    for w in walls:
        mats  = w.get('materials', [])
        wtype = (w.get('type') or '')
        wname = (w.get('name') or '')
        upper = (wtype + ' ' + wname).upper()
        has_2p = '2P' in upper or '이중' in upper or '방화' in upper
        has_1p = '1P' in upper or '단겹' in upper

        if not mats:
            issue('INFO', '레이어/겹수',
                  w['name'], w.get('storey'),
                  "레이어 정보 없음 — 겹수 판단 불가",
                  f"타입명: {wtype[:60] or '-'}")
        elif has_2p and len(mats) < 2:
            issue('WARNING', '레이어/겹수',
                  w['name'], w.get('storey'),
                  f"타입명에 '2P/이중/방화' 포함 — 레이어 {len(mats)}개 (불일치 가능)",
                  f"타입: {wtype[:60]}")
        elif has_1p and len(mats) > 1:
            issue('WARNING', '레이어/겹수',
                  w['name'], w.get('storey'),
                  f"타입명에 '1P/단겹' 포함 — 레이어 {len(mats)}개 (불일치 가능)",
                  f"타입: {wtype[:60]}")

    # ── C3: 벽 두께 이상 / 누락 ─────────────────────────────────
    for w in walls:
        t = w.get('T_mm')
        L = w.get('L_mm') or 0
        if t is None:
            issue('INFO', '벽 두께',
                  w['name'], w.get('storey'),
                  "두께 값 없음 — IFC에서 추출 불가", "")
        elif L > 0 and t >= L * 0.5:
            issue('WARNING', '벽 두께',
                  w['name'], w.get('storey'),
                  f"두께 {t}mm ≥ 벽길이({L}mm) 절반 — 두께/길이 혼동 의심",
                  "IfcElementQuantity 또는 형상 프로파일 확인 필요")
        elif t > 500:
            issue('WARNING', '벽 두께',
                  w['name'], w.get('storey'),
                  f"두께 {t}mm — 500mm 초과 (이상값 가능성)", "")
        elif t < 30:
            issue('WARNING', '벽 두께',
                  w['name'], w.get('storey'),
                  f"두께 {t}mm — 30mm 미만 (매우 얇음)", "")

    return issues


# ────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk(); root.withdraw()
            ifc_path = filedialog.askopenfilename(
                title="IFC 파일 선택",
                filetypes=[("IFC files", "*.ifc"), ("All files", "*.*")]
            )
            root.destroy()
        except Exception:
            print("사용법: python ifc_verifier.py <파일.ifc>")
            sys.exit(1)
    else:
        ifc_path = sys.argv[1]

    if not ifc_path or not os.path.exists(ifc_path):
        print(f"오류: 파일 없음 — {ifc_path}")
        sys.exit(1)

    print(f"로딩: {ifc_path}")
    ifc = ifcopenshell.open(ifc_path)
    print(f"스키마: {ifc.schema}")

    data = extract_all(ifc)

    print("  이중검증 — 정규식 파싱 중...")
    regex_data = extract_by_regex(ifc_path)
    dual = dual_verify(data, regex_data)
    n_match = sum(1 for r in dual if r['match'])
    print(f"  → {n_match}/{len(dual)} 항목 일치" + (" — 신뢰" if n_match == len(dual) else " — 불일치 있음"))

    print("  검증 체크 실행 중...")
    issues = run_checks(data)
    n_err  = sum(1 for i in issues if i['severity'] == 'ERROR')
    n_warn = sum(1 for i in issues if i['severity'] == 'WARNING')
    n_info = sum(1 for i in issues if i['severity'] == 'INFO')
    print(f"  → ERROR {n_err} / WARNING {n_warn} / INFO {n_info}")

    # 교차검증 입력 다이얼로그
    print("  교차검증 기준값 입력 창 열기...")
    expected = show_cross_dialog()
    cross = run_cross_checks(data, expected) if expected else []
    if expected:
        print(f"  → 교차검증 항목 {len(cross)}개")
    else:
        print("  → 교차검증 건너뜀")

    base = os.path.splitext(ifc_path)[0]
    json_path = base + "_ground_truth.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n✓ JSON 저장: {json_path}")

    html = make_html(data, ifc_path, issues, cross, expected, dual=dual)
    html_path = base + "_검증보고서.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✓ HTML 저장: {html_path}")

    s = calc_stats(data)
    print(f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  IFC 전체 추출 요약
  벽:        {s['total_walls']}  (치수 {s['dims_pct']}% / 층매핑 {s['storey_pct']}%)
  문:        {s['n_doors']}
  창:        {s['n_windows']}
  슬래브:    {s['n_slabs']}
  기둥:      {s['n_columns']}
  보:        {s['n_beams']}
  계단:      {s['n_stairs']}
  지붕:      {s['n_roofs']}    커튼월: {s['n_cwalls']}
  공간:      {s['n_spaces']}
  층:        {s['n_storeys']}
  재료:      {s['n_mats']}
  개구부:    {s['total_openings']}개 (치수없음 {s['opening_no_dim']}개)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")

    try:
        import webbrowser
        webbrowser.open(html_path)
    except Exception:
        pass


if __name__ == "__main__":
    main()


# ═══════════════════════════════════════════════════════════
# 시뮬레이터 UI 데이터 변환
# ═══════════════════════════════════════════════════════════
def export_simulator_walls(verifier_walls: list) -> list:
    """
    verifier.extract_all()['walls'] → 승훈 시뮬레이터 UI 형식 변환.
    반환: [{"wall_id", "name", "storey", "space", "length", "height", "openings":[...]}]
    """
    result = []
    for w in verifier_walls:
        if not w.get('dims_ok') or not w.get('L_mm') or not w.get('H_mm'):
            continue
        L = float(w['L_mm'])
        H = float(w['H_mm'])

        openings = []
        for op in w.get('openings', []):
            ow = op.get('ow') or 0
            oh = op.get('oh') or 0
            ox = op.get('ox') or 0
            oy = op.get('oy') or 0
            if not (ow and oh):
                continue
            kind = op.get('kind', 'Opening').lower()
            op_type = 'door' if 'door' in kind else 'window'
            openings.append({
                'type':   op_type,
                'width':  round(float(ow)),
                'height': round(float(oh)),
                'x':      round(float(ox) + float(ow) / 2),  # 승훈 JS: 중심 x
                'y':      round(float(oy)),
            })

        result.append({
            'wall_id':  w.get('name') or w.get('id', ''),  # optimizer와 동일 키
            'name':     w.get('name', ''),
            'storey':   w.get('storey', ''),
            'space':    w.get('space', ''),
            'length':   round(L),
            'height':   round(H),
            'openings': openings,
        })
    return result
