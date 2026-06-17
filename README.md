# 모듈러 건축 IFC 최적화 시스템

> IFC 파일에서 벽체 정보를 자동 추출하여 석고보드·합판 시공 배치를 최적화하고,  
> 결과를 인터랙티브 시뮬레이터로 출력하는 자동화 도구입니다.

<br>

## ⬇️ 다운로드 (설치 없이 바로 실행)

| 파일 | 설명 |
|------|------|
| **[IFC최적화.exe](dist/IFC최적화.exe)** | Windows 64비트 실행 파일 (71MB) |

> Python, Node.js 등 별도 설치 불필요 — EXE 하나로 실행 가능  
> Windows 10/11 64비트 / Chrome·Edge 브라우저 권장

<br>

## ✨ 주요 기능

| 기능 | 설명 |
|------|------|
| 🔍 IFC 자동 파싱 | IfcWall 추출, 치수·개구부 자동 인식 및 단위 보정 |
| 📐 RTL 배치 최적화 | M3 시공방식 — 오른쪽 끝부터 배치, 자투리 위치 균일화 |
| ♻️ 재사용 로직 | 자투리 보드 자동 저장 → 같은 공간·층 내 재활용 (ReusePool) |
| 🖥️ 인터랙티브 시뮬레이터 | 승훈 UI + Python 최적화 결과 통합, 단계별 애니메이션 |
| 📊 자재 발주 일람표 | 벽별 수량 집계, 파레트 단위 발주량 산출 |

<br>

## 🎨 시뮬레이터 색상 가이드

| 색상 | 종류 | 설명 |
|------|------|------|
| 🟢 초록 | 온장 | 900×1800mm 보드 그대로 부착 |
| 🟡 노랑 | 직선절단 | 직선 1번으로 자른 보드 |
| 🟣 보라 | 노치절단 | 개구부 모서리 ㄱ/ㄴ형 절단 |
| 🔵 파랑 | 재사용 | 다른 벽 자투리를 재활용한 보드 |

<br>

## 🚀 사용 방법

1. `IFC최적화.exe` 실행
2. IFC 파일 선택
3. 자재(석고보드/합판) · 공법(1P/2P) · 재사용 여부 선택
4. 자동으로 브라우저에서 결과 확인

**출력 파일 (IFC 파일과 같은 폴더에 저장)**

```
프로젝트_검증결과.html        ← IFC 벽체 검증 리포트
프로젝트_최적화결과.html      ← 배치도 + 자재 집계
프로젝트_시뮬레이터.html      ← 인터랙티브 시뮬레이터 ★
```

<br>

## 🛠️ 개발 환경 설정 (소스 수정 시)

```bash
git clone https://github.com/mmrt0n/modular-ifc-optimizer.git
cd modular-ifc-optimizer
pip install ifcopenshell python-docx
python ifc_pipeline.py
```

**EXE 재빌드**

```bash
pip install pyinstaller
python -m PyInstaller IFC최적화.spec --noconfirm
```

> 맥에서도 소스 실행 및 수정 가능 (맥용 앱으로 빌드됨)

<br>

## 📁 파일 구조

```
modular-ifc-optimizer/
├── ifc_pipeline.py          # 전체 파이프라인 오케스트레이터
├── ifc_verifier.py          # IFC 파싱 및 벽체 검증
├── gypsum_optimizer_v3.py   # 석고보드/합판 배치 최적화 엔진
├── simulator_ui.html        # 승훈 시뮬레이터 UI 템플릿
├── IFC최적화.spec           # PyInstaller 빌드 설정
├── dist/
│   └── IFC최적화.exe        # 배포용 실행 파일 (Git LFS)
└── docs/
    ├── IFC최적화_최종정리_통합문서.docx
    └── IFC최적화_최종시스템_설명서.docx
```

<br>

## 📋 시공 로직 구현 현황 (M3 방식)

| 항목 | 구현 여부 |
|------|-----------|
| RTL (오른쪽 끝부터) 배치 | ✅ |
| 개구부 중심 대칭 (SYM) | ✅ |
| 각재 피치 450mm (STUD) | ✅ |
| 바닥 이격 5mm | ✅ |
| 직선절단 / 노치절단 분류 | ✅ |
| 재사용 풀 by_space → by_floor | ✅ |
| 재사용 보드 시각화 🔵 | ✅ |

<br>

## 📄 문서

- [최종정리 통합문서](docs/IFC최적화_최종정리_통합문서.docx) — 구조도 + 발표 요약 + 상세 설명서
- [시스템 설명서](docs/IFC최적화_최종시스템_설명서.docx) — 기술 상세 문서

<br>

---

인천대학교 건축환경연구실
