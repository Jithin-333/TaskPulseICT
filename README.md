# ⚡ TaskPulse — Intelligent Project Risk Analyzer

A full-stack Flask web application: project management + AI risk detection +
7 Matplotlib charts + team analytics + resume analyzer.

## 🚀 Quick Start

    pip install -r requirements.txt
    python app.py
    # Open http://localhost:5000

## 🔐 Demo Credentials

| Role    | Email                     | Password    |
|---------|---------------------------|-------------|
| Manager | manager@taskpulse.com     | manager123  |
| Member  | alice@taskpulse.com       | member123   |
| Member  | bob@taskpulse.com         | member123   |
| Member  | carol@taskpulse.com       | member123   |

## ✨ Feature Summary

### User Management
- Register/login (bcrypt passwords), role-based access (Manager / Member)

### Project Management
- Create, edit, delete projects | Health Score 0-100 | CSV export

### Task Management
- Full CRUD, assign to members, inline AJAX progress slider
- Track: deadline, estimated hours, progress %, status, priority

### Risk Detection — Pandas + NumPy
- Expected progress = elapsed_time / total_time * 100
- Gap analysis drives risk: Low / Medium / High
- Overdue detection, near-deadline alerts

### 7 Matplotlib Charts (Project Detail page)
1. Task Progress — horizontal bars colored by risk
2. Actual vs Expected — grouped bars + days-left line
3. Risk Distribution — donut + status breakdown bar
4. Employee Workload — assigned vs completed per member
5. Gantt Timeline — progress bars with today marker + risk color
6. Burndown Chart — ideal vs actual remaining tasks line
7. Priority & Hours — stacked priority bar + estimated hours donut

### AI Insights Engine
- Auto-generates alerts: "Task X is 71% behind schedule"
- Actionable recommendations per insight
- Project health summary at top of insights panel

### Employee Dashboard
- Per-member: tasks assigned, completed, in-progress, avg progress %
- High-risk task count, recent task preview, workload chart

### Resume Analyzer
- Upload PDF / DOCX / TXT or paste text
- Compare against job description → keyword match score
- 4-panel visual: gauge, donut, section bars, keyword bars
- Matched + missing keyword badges, recommendations

### Live Notifications
- Bell icon in topbar polls /api/notifications every 30s
- Alerts for overdue, near-deadline low-progress, high-risk tasks

### REST API Endpoints
- GET  /api/notifications     — active risk alerts JSON
- GET  /api/task_summary      — task counts/averages JSON
- GET  /project/export/<id>   — CSV download
- POST /task/update_progress/<id> — AJAX slider update

## 📂 Project Architecture

For a detailed breakdown of the technical modules, risk engine logic, and analytics pipeline, please refer to the **[ARCHITECTURE.md](file:///d:/FlaskProjects/taskpulse_app/taskpulse/ARCHITECTURE.md)** file.

## 🛠 Stack
- **Backend**: Flask 3.0, SQLite3
- **Data & AI**: Pandas, NumPy, Scikit-Learn (Resume Analyzer)
- **Visualization**: Matplotlib (7+ Dynamic Charts)
- **Document Processing**: PyPDF2, python-docx
- **Auth**: Flask-Login, Werkzeug Security
- **Frontend**: Bootstrap 5, Jinja2, Space Grotesk, JetBrains Mono
