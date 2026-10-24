# ParkiStep Twin — Deployment and Operational Readiness Guide

This document defines the deployment options, runtime prerequisites, resource requirements, and operational boundaries for hosting the ParkiStep Twin digital-twin platform and clinician dashboard.

---

## 1. System Architecture and Operational Footprint

The ParkiStep Twin platform operates as an offline-first, local-computation healthcare research prototype:
- **Frontend / Presentation Layer:** Streamlit (v1.66+) multi-page web application (`app/Home.py`).
- **Engine Layer:** Digital-Twin execution engine (`src/parkistep/twin/engine.py`) managing twin lifecycle, rolling sensor buffer (60 Hz sampling), and alert hysteresis.
- **Model Layer:** Pre-trained or regenerated Scikit-learn random forest and logistic forecasting pipelines serialized via Joblib.
- **Persistence Layer:** Local SQLite database (optional, default disabled in demo mode) for session state and warning logs.
- **Privacy & Telemetry:** Zero external telemetry. No user or patient telemetry is exfiltrated to any third-party cloud.

---

## 2. Resource & Runtime Prerequisites

| Parameter | Minimum Requirement | Recommended Specification |
|---|---|---|
| **OS** | Linux (Ubuntu 22.04+), Windows 10/11, macOS (x86_64 or Apple Silicon) | Linux (x86_64 container) |
| **Python** | 3.11 or 3.12 (supported up to 3.14) | 3.12 |
| **CPU** | 2 vCPUs | 4 vCPUs |
| **RAM** | 2.0 GB | 4.0 GB |
| **Disk Space** | 1.0 GB (code, models, dependencies) | 2.5 GB (including optional raw datasets) |
| **Port** | 8501 (TCP, HTTP) | 8501 (or behind HTTPS reverse proxy) |

---

## 3. Deployment Option A: Docker Container (Recommended)

### Build the Container Image
From the repository root:
```bash
docker build -t parkistep-twin:latest .
```

### Run the Container
```bash
docker run -d \
  --name parkistep-twin \
  -p 8501:8501 \
  --restart unless-stopped \
  parkistep-twin:latest
```

Access the application in your browser at `http://localhost:8501`.

### Persistent Storage (Optional)
To persist twin session states between container restarts, mount a host directory for the SQLite state store:
```bash
docker run -d \
  --name parkistep-twin \
  -p 8501:8501 \
  -v $(pwd)/state_data:/app/data/persistence \
  parkistep-twin:latest
```

---

## 4. Deployment Option B: Bare-Metal / Virtual Environment

### 1. Clone the Repository
```bash
git clone https://github.com/MeenalSinha/ParkiStep-Twin.git
cd ParkiStep-Twin
```

### 2. Configure Virtual Environment
```bash
python -m venv .venv
source .venv/bin/activate       # On Windows: .venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements-lock.txt
```

### 3. Launch Dashboard
```bash
streamlit run app/Home.py --server.port 8501 --server.address 0.0.0.0
```

---

## 5. Deployment Option C: Cloud Hosting (Cloud Run / AWS ECS)

### Environment Variables
Configure the following environment variables on your cloud provider:
- `STREAMLIT_SERVER_PORT`: `8501` (or `$PORT` for Google Cloud Run)
- `STREAMLIT_SERVER_ADDRESS`: `0.0.0.0`
- `STREAMLIT_SERVER_HEADLESS`: `true`
- `STREAMLIT_BROWSER_GATHER_USAGE_STATS`: `false`
- `PYTHONPATH`: `src`

### Cloud Run Example
```bash
gcloud run deploy parkistep-twin \
  --image gcr.io/<PROJECT_ID>/parkistep-twin:latest \
  --platform managed \
  --region us-central1 \
  --port 8501 \
  --memory 2Gi \
  --cpu 2 \
  --allow-unauthenticated
```

---

## 6. Safety & Clinical Use Disclaimer

> [!WARNING]
> **Research Prototype Only**: ParkiStep Twin is an experimental AI and digital-twin research prototype developed for educational, benchmarking, and demonstration purposes. It is **not** a cleared medical device, has **not** received FDA or CE-mark clinical clearance, and must **never** be used for diagnostic, prognostic, or treatment decisions in real patients without appropriate human clinical oversight and regulatory approval.
