# Lesen Für Studenten (학생용 시선 추적 분석 시스템)

## 🎯 프로젝트 개요

이 프로젝트는 웹캠 기반의 시선 추적(Eye-Tracking) 기술을 활용하여, 사용자가 독일어 기사를 읽는 집중도 및 이해도를 측정하고 분석하는 하이브리드 시스템입니다.

**주요 특징:**

- **하이브리드 AI:** WebGazer.js (클라이언트 측)와 YOLOv8-Pose (서버 측)를 융합하여 시선 추적 정확도를 극대화합니다.
- **다단계 캘리브레이션:** 훈련(클릭), 격자(응시), 선형 보강(응시)의 3단계 캘리브레이션을 통해 머리 움직임에 강건한 좌표를 얻습니다.
- **점수 기반 관리:** 관리자 패널은 평균이 아닌 사용자의 **가장 최신 점수**를 기준으로 그룹 분류 및 모니터링을 수행합니다.

## 🛠️ 기술 스택

| **분류** | **기술** | **역할** |
| --- | --- | --- |
| **백엔드 (API & AI)** | Python 3.12+ | 서버 로직, 데이터 저장/관리, AI 추론 |
|  | FastAPI | 비동기 API 및 WebSocket 통신 |
|  | PyTorch, Ultralytics (YOLOv8-Pose), OpenCV | 실시간 웹캠 이미지 처리 및 눈동자 좌표 추정 |
| **프론트엔드 (Client & Admin)** | HTML/JavaScript/CSS | 사용자 인터페이스, 캔버스 렌더링 |
|  | WebGazer.js | 클라이언트 측 시선 예측, 웹캠 접근 |
|  | WebSocket API | 실시간 시선 데이터 스트리밍 및 관리자 모니터링 |

## 🚀 설치 및 실행 방법

이 프로젝트는 2개의 서버(백엔드 API/AI, 프론트엔드 파일 서버)로 구성됩니다.

### 1. 환경 설정 (필수)

1. **가상 환경 생성 및 활성화:**
    
    ```
    # venv 폴더 및 .venv 폴더 모두 삭제 후 3.12 기반으로 새로 생성 권장
    python3.12 -m venv .venv
    source .venv/bin/activate
    
    ```
    
2. **필수 라이브러리 설치:**
    
    ```
    pip install -r requirements.txt
    
    ```
    
    > 참고: Uvicorn 실행 시 YOLOv8 모델(yolov8n-pose.pt)이 자동으로 다운로드됩니다.

### 2. 서버 실행

프로젝트 루트 폴더(`project`)에서 2개의 터미널을 사용합니다.

### 터미널 1: 백엔드 서버 (API & AI)

- **HTTP 8000 포트**로 실행합니다.
    
    ```
    python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
    
    ```
    

### 터미널 2: 프론트엔드 서버 (HTML 파일 제공)

- `frontend` 폴더를 기준으로 HTTP 8080 포트에서 실행합니다.
    
    ```
    python -m http.server 8080 -d frontend
    
    ```
    

### 3. 웹사이트 접속

| **페이지** | **주소** | **역할** |
| --- | --- | --- |
| **학생용** | `http://localhost:8080/index.html` | 시선 추적 및 퀴즈 응시 |
| **관리자용** | `http://localhost:8080/admin.html` | 실시간 모니터링 및 설정 |

## 🔑 관리자 인증 정보 (필수 입력)

관리자 페이지에 접속할 때 필요한 토큰 정보입니다.

| **항목** | **값** | **사용처** |
| --- | --- | --- |
| **원본 토큰 (입력값)** | `goethe-literacy-admin-token` | `admin.html`의 'Admin Token' 입력란 |
| **서버 저장 (해시)** | `7677c1e67d477f43131129b8ce3ad62e2d84b1ec1eb74f81fd5457e8fac07d79` | `main.py`에서 검증용 (노출 안 됨) |

## 📋 주요 기능 상세 설명

### A. 하이브리드 시선 추적 및 캘리브레이션

사용자 측(`index.html`)에서 시선 예측과 비디오 프레임 전송을 병렬로 수행하며, 서버가 이 데이터를 융합하여 정확도를 높입니다.

- **WebGazer (Local):** 빠르고 연속적인 예측(`lastWebgazer`)을 제공합니다.
- **YOLOv8-Pose (Server):** 웹캠 이미지(`Blob`)를 전송받아 랜드마크(눈동자) 좌표를 찾아 `server_gaze`를 클라이언트에게 반환합니다. (정확도 담당)
- **Fusion:** 최종 시선 좌표(`currentGaze`)는 `WebGazer`의 속도와 `YOLOv8`의 정확도를 **가중 평균**하여 계산됩니다.

### B. 다단계 캘리브레이션 (3 Stages)

1. **0단계 (훈련):** 9개 점을 바라보며 마우스로 **5회 클릭**하여 WebGazer의 내부 회귀 모델을 훈련합니다. (정확도 향상에 필수)
2. **1단계 (격자):** 9개 격자점을 바라보는 동안 **응시(Dwell-time) 데이터**를 수집합니다.
3. **2단계 (보강):** 수평/수직/대각선상의 7개 점을 통해 텍스트 읽기에 특화된 추가 보정 데이터를 수집합니다.
    - **결과:** 1, 2단계에서 수집된 총 16개의 데이터를 기반으로 **Affine Transformation 행렬**(`calibMatrix`)을 계산하여 시선 좌표를 보정합니다.

### C. 점수 계산 및 관리 (Last Score)

- 점수 계산:
    
    ### $\text{Final Score} = (\text{Gaze Coverage Score} \times 0.6) + (\text{Quiz Score} \times 0.4)$ 
    
    - **Gaze Coverage Score:** 히트맵의 활성화된 셀 수를 기반으로, 사용자가 텍스트 영역을 얼마나 넓게 커버했는지 측정합니다. (집중적인 읽기 패턴 점수)
- **관리자 페이지:** `main.py`는 `last_score` 필드를 사용하여, 사용자가 점수를 제출할 때마다 관리자 페이지를 실시간(WebSocket)으로 갱신합니다. 모든 그룹 분류(High/Medium/Low) 및 테이블 정렬은 이 `last_score`를 기준으로 합니다.
