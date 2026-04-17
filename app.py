from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, abort
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import sqlite3, os, io, base64, json, re
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
import warnings
warnings.filterwarnings('ignore')

app = Flask(__name__)
app.config['SECRET_KEY'] = 'taskpulse-secret-2024'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

DB_PATH = os.path.join(os.path.dirname(__file__), 'instance', 'taskpulse.db')

login_manager = LoginManager(app)
login_manager.login_view = 'login'

ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt'}

# ─── DB Connection ─────────────────────────────────────────────────────────────

def get_db():
    """Return a sqlite3 connection with dict-like row access."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row          # columns accessible by name
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

# ─── Schema ────────────────────────────────────────────────────────────────────

def init_db():
    with get_db() as conn:
        cur = conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS user (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT    NOT NULL UNIQUE,
                email         TEXT    NOT NULL UNIQUE,
                password_hash TEXT,
                role          TEXT    NOT NULL DEFAULT 'member',
                created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS project (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                description TEXT,
                deadline    TEXT    NOT NULL,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                created_by  INTEGER REFERENCES user(id)
            );
            CREATE TABLE IF NOT EXISTS task (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                title           TEXT    NOT NULL,
                description     TEXT,
                project_id      INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
                assigned_to     INTEGER REFERENCES user(id),
                deadline        TEXT    NOT NULL,
                estimated_hours REAL    NOT NULL DEFAULT 8,
                progress        REAL    NOT NULL DEFAULT 0,
                status          TEXT    NOT NULL DEFAULT 'Not Started',
                created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                priority        TEXT    NOT NULL DEFAULT 'Medium'
            );
        """)
        conn.commit()

# ─── Datetime helpers ──────────────────────────────────────────────────────────

DT_FMT = '%Y-%m-%d %H:%M:%S'

def _parse_dt(s):
    if not s:
        return datetime.utcnow()
    for fmt in (DT_FMT, '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return datetime.utcnow()

def _fmt_dt(dt):
    return dt.strftime(DT_FMT)

# ─── Row wrappers ──────────────────────────────────────────────────────────────

class RowObj:
    """Wraps a sqlite3.Row so columns are accessible as attributes."""
    def __init__(self, row):
        self._d = dict(row) if row else {}

    def __getattr__(self, name):
        try:
            val = self._d[name]
        except KeyError:
            raise AttributeError(name)
        if name in ('created_at', 'deadline') and isinstance(val, str):
            return _parse_dt(val)
        return val

    def __repr__(self):
        return f"<Row {self._d}>"


class UserObj(UserMixin, RowObj):
    """User row that works with Flask-Login."""
    def get_id(self):
        return str(self._d['id'])

    def check_password(self, pw):
        return check_password_hash(self._d.get('password_hash', ''), pw)


class ProjectObj(RowObj):
    @property
    def tasks(self):
        return db_get_tasks_by_project(self.id)


# ─── DB helpers (all use cursors) ─────────────────────────────────────────────

def db_get_user_by_id(uid):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM user WHERE id = ?", (uid,))
        row = cur.fetchone()
        return UserObj(row) if row else None

def db_get_user_by_email(email):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM user WHERE email = ?", (email,))
        row = cur.fetchone()
        return UserObj(row) if row else None

def db_create_user(username, email, password, role):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO user (username, email, password_hash, role) VALUES (?,?,?,?)",
            (username, email, generate_password_hash(password), role)
        )
        conn.commit()
        return cur.lastrowid

def db_get_all_users():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM user ORDER BY username")
        return [UserObj(r) for r in cur.fetchall()]

def db_get_members():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM user WHERE role='member' ORDER BY username")
        return [UserObj(r) for r in cur.fetchall()]

def db_get_project(pid):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM project WHERE id = ?", (pid,))
        row = cur.fetchone()
        return ProjectObj(row) if row else None

def db_get_projects_by_manager(manager_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM project WHERE created_by=? ORDER BY deadline", (manager_id,))
        return [ProjectObj(r) for r in cur.fetchall()]

def db_get_projects_for_user(uid):
    """Projects that contain at least one task assigned to the user."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT p.* FROM project p
            JOIN task t ON t.project_id = p.id
            WHERE t.assigned_to = ?
            ORDER BY p.deadline
        """, (uid,))
        return [ProjectObj(r) for r in cur.fetchall()]

def db_create_project(name, description, deadline, created_by):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO project (name, description, deadline, created_by) VALUES (?,?,?,?)",
            (name, description, _fmt_dt(deadline), created_by)
        )
        conn.commit()
        return cur.lastrowid

def db_update_project(pid, name, description, deadline):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE project SET name=?, description=?, deadline=? WHERE id=?",
            (name, description, _fmt_dt(deadline), pid)
        )
        conn.commit()

def db_delete_project(pid):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM task    WHERE project_id = ?", (pid,))
        cur.execute("DELETE FROM project WHERE id = ?",         (pid,))
        conn.commit()

def db_get_task(tid):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM task WHERE id = ?", (tid,))
        row = cur.fetchone()
        return RowObj(row) if row else None

def db_get_tasks_by_project(pid):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM task WHERE project_id=? ORDER BY deadline", (pid,))
        return [RowObj(r) for r in cur.fetchall()]

def db_get_tasks_by_assignee(uid):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM task WHERE assigned_to=? ORDER BY deadline", (uid,))
        return [RowObj(r) for r in cur.fetchall()]

def db_get_tasks_by_manager(manager_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT t.* FROM task t
            JOIN project p ON t.project_id = p.id
            WHERE p.created_by = ?
            ORDER BY t.deadline
        """, (manager_id,))
        return [RowObj(r) for r in cur.fetchall()]

def db_get_all_tasks():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM task ORDER BY deadline")
        return [RowObj(r) for r in cur.fetchall()]

def db_create_task(title, description, project_id, assigned_to,
                   deadline, estimated_hours, priority, created_at=None):
    with get_db() as conn:
        cur = conn.cursor()
        cat = _fmt_dt(created_at or datetime.utcnow())
        cur.execute("""
            INSERT INTO task
              (title, description, project_id, assigned_to, deadline,
               estimated_hours, priority, created_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (title, description, project_id, assigned_to,
              _fmt_dt(deadline), estimated_hours, priority, cat))
        conn.commit()
        return cur.lastrowid

def db_update_task(tid, title, description, assigned_to, deadline,
                   estimated_hours, priority, progress, status):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE task
            SET title=?, description=?, assigned_to=?, deadline=?,
                estimated_hours=?, priority=?, progress=?, status=?
            WHERE id=?
        """, (title, description, assigned_to, _fmt_dt(deadline),
              estimated_hours, priority, progress, status, tid))
        conn.commit()

def db_update_task_progress(tid, progress, status):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE task SET progress=?, status=? WHERE id=?",
                    (progress, status, tid))
        conn.commit()

def db_delete_task(tid):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM task WHERE id=?", (tid,))
        conn.commit()

# ─── Flask-Login ───────────────────────────────────────────────────────────────

@login_manager.user_loader
def load_user(uid):
    return db_get_user_by_id(int(uid))

# ─── Business logic helpers ────────────────────────────────────────────────────

def allowed_file(fn):
    return '.' in fn and fn.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def compute_risk(task):
    now      = datetime.utcnow()
    total    = (task.deadline - task.created_at).total_seconds()
    elapsed  = (now - task.created_at).total_seconds()
    if total <= 0:
        return 'High'
    expected  = min(100, (elapsed / total) * 100)
    gap       = expected - task.progress
    days_left = (task.deadline - now).days
    if task.status == 'Completed':            return 'Low'
    if days_left < 0:                         return 'High'
    if gap >= 40 or (days_left <= 1 and task.progress < 80): return 'High'
    if gap >= 20 or (days_left <= 3 and task.progress < 50): return 'Medium'
    return 'Low'

def project_health(tasks):
    if not tasks:
        return 100
    risks = [compute_risk(t) for t in tasks]
    score = 100
    for r in risks:
        if r == 'High':   score -= 20
        elif r == 'Medium': score -= 10
    completed       = sum(1 for t in tasks if t.status == 'Completed')
    completion_rate = (completed / len(tasks)) * 30
    return max(0, min(100, score + completion_rate - 30))

def generate_insights(project, tasks):
    insights = []
    now = datetime.utcnow()
    for t in tasks:
        if t.status == 'Completed':
            continue
        total   = (t.deadline - t.created_at).total_seconds()
        elapsed = (now - t.created_at).total_seconds()
        if total > 0:
            expected  = min(100, (elapsed / total) * 100)
            gap       = expected - t.progress
            days_left = (t.deadline - now).days
            if gap >= 30:
                insights.append({'type': 'danger', 'icon': '⚠️',
                    'text': f'"{t.title}" is delayed by {gap:.0f}% behind expected progress.',
                    'action': 'Consider reassigning or breaking into subtasks.'})
            elif gap >= 15:
                insights.append({'type': 'warning', 'icon': '⏰',
                    'text': f'"{t.title}" is {gap:.0f}% behind schedule.',
                    'action': 'Schedule a check-in with the assignee.'})
            if days_left <= 2 and t.progress < 70:
                insights.append({'type': 'danger', 'icon': '🔥',
                    'text': f'"{t.title}" deadline in {days_left} day(s) with only {t.progress:.0f}% complete.',
                    'action': 'Escalate immediately.'})
    score = project_health(tasks)
    if score < 40:
        insights.insert(0, {'type': 'danger', 'icon': '🚨',
            'text': f'Project "{project.name}" is at HIGH RISK (Health: {score:.0f}/100).',
            'action': 'Immediate manager intervention required.'})
    elif score < 70:
        insights.insert(0, {'type': 'warning', 'icon': '📊',
            'text': f'Project "{project.name}" health is MODERATE ({score:.0f}/100).',
            'action': 'Review task assignments and timelines.'})
    return insights

def fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight', dpi=110, facecolor=fig.get_facecolor())
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()

# ─── Chart constants ───────────────────────────────────────────────────────────

DARK_BG  = '#f0f2f5'
CARD_BG  = '#ffffff'
ACCENT   = '#4f8ef7'
ACCENT2  = '#f43f5e'
ACCENT3  = '#06d6a0'
TEXT     = '#1c1e21'
GRID     = '#dee2e6'

def style_ax(ax, title=''):
    ax.set_facecolor(CARD_BG)
    ax.tick_params(colors=TEXT, labelsize=8)
    for spine in ax.spines.values():
        spine.set_edgecolor(GRID)
    ax.xaxis.label.set_color(TEXT)
    ax.yaxis.label.set_color(TEXT)
    if title:
        ax.set_title(title, color=TEXT, fontsize=10, fontweight='bold', pad=8)
    ax.grid(axis='y', color=GRID, linewidth=0.5, alpha=0.7)

# ─── Charts ────────────────────────────────────────────────────────────────────

def make_task_progress_chart(tasks):
    fig, ax = plt.subplots(figsize=(9, max(3, len(tasks) * 0.55 + 1)))
    fig.patch.set_facecolor(DARK_BG); ax.set_facecolor(CARD_BG)
    if not tasks:
        ax.text(0.5, 0.5, 'No tasks yet', ha='center', va='center', color=TEXT, fontsize=12)
        ax.axis('off'); return fig_to_b64(fig)
    names    = [t.title[:22] + '…' if len(t.title) > 22 else t.title for t in tasks]
    progress = [t.progress for t in tasks]
    colors   = [{'Low': ACCENT3, 'Medium': '#fbbf24', 'High': ACCENT2}[compute_risk(t)] for t in tasks]
    y        = np.arange(len(tasks))
    bars     = ax.barh(y, progress, color=colors, height=0.5, alpha=0.85)
    ax.barh(y, [100] * len(tasks), color=GRID, height=0.5, alpha=0.3)
    for bar, pct in zip(bars, progress):
        ax.text(min(pct + 2, 95), bar.get_y() + bar.get_height() / 2,
                f'{pct:.0f}%', va='center', color=TEXT, fontsize=8)
    ax.set_yticks(y); ax.set_yticklabels(names, color=TEXT, fontsize=8)
    ax.set_xlim(0, 110); ax.set_xlabel('Progress (%)', color=TEXT)
    style_ax(ax, 'Task Progress Overview')
    patches = [mpatches.Patch(color=ACCENT3, label='Low Risk'),
               mpatches.Patch(color='#fbbf24', label='Medium'),
               mpatches.Patch(color=ACCENT2,   label='High Risk')]
    ax.legend(handles=patches, loc='lower right', facecolor=CARD_BG, labelcolor=TEXT, fontsize=7)
    plt.tight_layout(); return fig_to_b64(fig)

def make_deadline_chart(tasks):
    fig, ax = plt.subplots(figsize=(9, 4))
    fig.patch.set_facecolor(DARK_BG); ax.set_facecolor(CARD_BG)
    now    = datetime.utcnow()
    active = [t for t in tasks if t.status != 'Completed']
    if not active:
        ax.text(0.5, 0.5, 'All tasks completed!', ha='center', va='center', color=ACCENT3, fontsize=14)
        ax.axis('off'); return fig_to_b64(fig)
    names     = [t.title[:18] + '…' if len(t.title) > 18 else t.title for t in active]
    days_left = [(t.deadline - now).days for t in active]
    expected  = []
    for t in active:
        total   = (t.deadline - t.created_at).total_seconds()
        elapsed = (now - t.created_at).total_seconds()
        expected.append(min(100, (elapsed / total) * 100) if total > 0 else 0)
    x = np.arange(len(active))
    ax.bar(x - 0.2, [t.progress for t in active], 0.35, label='Actual %',   color=ACCENT,  alpha=0.85)
    ax.bar(x + 0.2, expected,                      0.35, label='Expected %', color=ACCENT2, alpha=0.65)
    ax.set_xticks(x); ax.set_xticklabels(names, rotation=30, ha='right', color=TEXT, fontsize=8)
    ax.set_ylabel('Progress (%)', color=TEXT); ax.set_ylim(0, 110)
    style_ax(ax, 'Actual vs Expected Progress')
    ax.legend(facecolor=CARD_BG, labelcolor=TEXT, fontsize=8)
    ax2 = ax.twinx()
    ax2.plot(x, days_left, 'o--', color='#fbbf24', linewidth=1.5, markersize=5, label='Days Left')
    ax2.set_ylabel('Days Left', color='#fbbf24', fontsize=9)
    ax2.tick_params(colors='#fbbf24', labelsize=8)
    ax2.spines['right'].set_edgecolor('#fbbf24')
    plt.tight_layout(); return fig_to_b64(fig)

def make_risk_chart(tasks):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 4))
    fig.patch.set_facecolor(DARK_BG)
    if not tasks:
        ax1.text(0.5, 0.5, 'No tasks yet', ha='center', va='center', color=TEXT); ax1.axis('off')
        ax2.text(0.5, 0.5, 'Waiting for data', ha='center', va='center', color=TEXT); ax2.axis('off')
        return fig_to_b64(fig)
    risks  = [compute_risk(t) for t in tasks]
    counts = {'Low': risks.count('Low'), 'Medium': risks.count('Medium'), 'High': risks.count('High')}
    ax1.set_facecolor(CARD_BG)
    wedges, texts, autos = ax1.pie(
        list(counts.values()), labels=list(counts.keys()),
        colors=[ACCENT3, '#fbbf24', ACCENT2], autopct='%1.0f%%',
        startangle=90, wedgeprops=dict(width=0.55), pctdistance=0.75)
    for t  in texts:  t.set_color(TEXT);      t.set_fontsize(9)
    for at in autos:  at.set_color('#0f1117'); at.set_fontsize(8); at.set_fontweight('bold')
    ax1.set_title('Risk Distribution', color=TEXT, fontsize=10, fontweight='bold')
    ax2.set_facecolor(CARD_BG)
    statuses = {}
    for t in tasks:
        statuses[t.status] = statuses.get(t.status, 0) + 1
    if statuses:
        s_colors = {'Completed': ACCENT3, 'In Progress': ACCENT, 'Not Started': GRID, 'On Hold': '#fbbf24'}
        s_names  = list(statuses.keys())
        bars = ax2.bar(s_names, list(statuses.values()),
                       color=[s_colors.get(s, ACCENT) for s in s_names], alpha=0.85)
        for bar in bars:
            ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.1,
                     str(int(bar.get_height())), ha='center', color=TEXT, fontsize=9)
        ax2.set_xticklabels(s_names, rotation=20, ha='right', color=TEXT, fontsize=8)
        style_ax(ax2, 'Task Status Distribution')
    plt.tight_layout(); return fig_to_b64(fig)

def make_employee_chart(users, tasks):
    members = [u for u in users if u.role == 'member']
    if not members:
        fig, ax = plt.subplots(figsize=(8, 3))
        fig.patch.set_facecolor(DARK_BG); ax.set_facecolor(CARD_BG)
        ax.text(0.5, 0.5, 'No team members yet', ha='center', va='center', color=TEXT, fontsize=12)
        ax.axis('off'); return fig_to_b64(fig)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    fig.patch.set_facecolor(DARK_BG)
    names = [u.username for u in members]
    avg_progress, task_counts, completed_counts = [], [], []
    for u in members:
        utasks = [t for t in tasks if t.assigned_to == u.id]
        task_counts.append(len(utasks))
        completed_counts.append(sum(1 for t in utasks if t.status == 'Completed'))
        avg_progress.append(np.mean([t.progress for t in utasks]) if utasks else 0)
    x  = np.arange(len(members))
    ax = axes[0]; ax.set_facecolor(CARD_BG)
    ax.bar(x, task_counts,      0.35, label='Assigned',  color=ACCENT,  alpha=0.85)
    ax.bar(x, completed_counts, 0.35, label='Completed', color=ACCENT3, alpha=0.85)
    ax.set_xticks(x); ax.set_xticklabels(names, rotation=20, ha='right', color=TEXT, fontsize=8)
    style_ax(ax, 'Task Assignment per Employee')
    ax.legend(facecolor=CARD_BG, labelcolor=TEXT, fontsize=8)
    ax2 = axes[1]; ax2.set_facecolor(CARD_BG)
    bar_colors = [ACCENT3 if p >= 70 else '#fbbf24' if p >= 40 else ACCENT2 for p in avg_progress]
    bars = ax2.bar(names, avg_progress, color=bar_colors, alpha=0.85)
    for bar, p in zip(bars, avg_progress):
        ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                 f'{p:.0f}%', ha='center', color=TEXT, fontsize=8)
    ax2.set_ylim(0, 115)
    ax2.set_xticklabels(names, rotation=20, ha='right', color=TEXT, fontsize=8)
    style_ax(ax2, 'Average Task Progress per Employee')
    plt.tight_layout(); return fig_to_b64(fig)

def make_gantt_chart(tasks, project):
    fig, ax = plt.subplots(figsize=(10, max(3, len(tasks) * 0.6 + 1.5)))
    fig.patch.set_facecolor(DARK_BG); ax.set_facecolor(CARD_BG)
    if not tasks:
        ax.text(0.5, 0.5, 'No tasks yet', ha='center', va='center', color=TEXT, fontsize=12)
        ax.axis('off'); return fig_to_b64(fig)
    now        = datetime.utcnow()
    proj_start = min(t.created_at for t in tasks)
    proj_end   = max(t.deadline   for t in tasks)
    total_days = max((proj_end - proj_start).days, 1)
    for i, t in enumerate(tasks):
        start_offset = (t.created_at - proj_start).days
        duration     = max((t.deadline - t.created_at).days, 1)
        bar_color    = {'Low': ACCENT3, 'Medium': '#f59e0b', 'High': ACCENT2}[compute_risk(t)]
        ax.barh(i, duration, left=start_offset, height=0.45, color=GRID, alpha=0.4)
        ax.barh(i, duration * (t.progress / 100), left=start_offset, height=0.45, color=bar_color, alpha=0.85)
        label = t.title[:22] + '…' if len(t.title) > 22 else t.title
        ax.text(start_offset + 0.3, i, label, va='center', color=TEXT, fontsize=7.5)
        ax.text(start_offset + duration + 0.3, i, f'{t.progress:.0f}%', va='center', color=bar_color, fontsize=7.5)
    today_offset = (now - proj_start).days
    if 0 <= today_offset <= total_days:
        ax.axvline(today_offset, color='#fbbf24', linewidth=1.5, linestyle='--', alpha=0.8)
        ax.text(today_offset + 0.3, len(tasks) - 0.2, 'Today', color='#fbbf24', fontsize=7)
    tick_days      = max(1, total_days // 6)
    tick_positions = list(range(0, total_days + 1, tick_days))
    tick_labels    = [(proj_start + timedelta(days=d)).strftime('%b %d') for d in tick_positions]
    ax.set_xticks(tick_positions); ax.set_xticklabels(tick_labels, color=TEXT, fontsize=7, rotation=20, ha='right')
    ax.set_yticks(range(len(tasks))); ax.set_yticklabels([''] * len(tasks))
    ax.set_xlim(-0.5, total_days + 3)
    style_ax(ax, 'Project Timeline (Gantt)')
    ax.grid(axis='x', color=GRID, linewidth=0.5, alpha=0.6)
    patches = [mpatches.Patch(color=ACCENT3, label='Low Risk'),
               mpatches.Patch(color='#f59e0b', label='Medium'),
               mpatches.Patch(color=ACCENT2,   label='High Risk')]
    ax.legend(handles=patches, loc='lower right', facecolor=CARD_BG, labelcolor=TEXT, fontsize=7)
    plt.tight_layout(); return fig_to_b64(fig)

def make_burndown_chart(tasks, project):
    fig, ax = plt.subplots(figsize=(9, 4))
    fig.patch.set_facecolor(DARK_BG); ax.set_facecolor(CARD_BG)
    if not tasks:
        ax.text(0.5, 0.5, 'No tasks', ha='center', va='center', color=TEXT)
        ax.axis('off'); return fig_to_b64(fig)
    now         = datetime.utcnow()
    start       = project.created_at
    total_days  = max((project.deadline - start).days, 1)
    total_tasks = len(tasks)
    days        = list(range(total_days + 1))
    ideal       = [total_tasks - (total_tasks / total_days) * d for d in days]
    elapsed     = max((now - start).days, 0)
    actual_days = list(range(min(elapsed + 1, total_days + 1)))
    completed_n = sum(1 for t in tasks if t.status == 'Completed')
    actual = [max(0, total_tasks - round(completed_n * (d / max(elapsed, 1)))) for d in actual_days]
    ax.plot(days, ideal, '--', color=ACCENT, linewidth=1.8, label='Ideal Burndown', alpha=0.8)
    ax.plot(actual_days, actual, '-o', color=ACCENT2, linewidth=2, markersize=4, label='Actual Remaining', alpha=0.9)
    ax.fill_between(actual_days, ideal[:len(actual_days)], actual, alpha=0.08,
                    color=ACCENT2 if (actual and actual[-1] > ideal[len(actual) - 1]) else ACCENT3)
    ax.axvline(elapsed, color='#fbbf24', linewidth=1.2, linestyle=':', alpha=0.7)
    ax.text(elapsed + 0.3, total_tasks * 0.95, 'Now', color='#fbbf24', fontsize=7)
    ax.set_xlabel('Days', color=TEXT); ax.set_ylabel('Tasks Remaining', color=TEXT)
    ax.set_xlim(0, total_days); ax.set_ylim(0, total_tasks + 0.5)
    style_ax(ax, 'Burndown Chart')
    ax.legend(facecolor=CARD_BG, labelcolor=TEXT, fontsize=8)
    plt.tight_layout(); return fig_to_b64(fig)

def make_velocity_chart(tasks):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))
    fig.patch.set_facecolor(DARK_BG)
    ax1.set_facecolor(CARD_BG)
    priorities    = ['High', 'Medium', 'Low']
    statuses      = ['Completed', 'In Progress', 'Not Started']
    status_colors = [ACCENT3, ACCENT, GRID]
    data          = {p: {s: sum(1 for t in tasks if t.priority == p and t.status == s) for s in statuses}
                     for p in priorities}
    bottoms = [0] * 3
    for s, col in zip(statuses, status_colors):
        vals = [data[p][s] for p in priorities]
        ax1.bar(priorities, vals, bottom=bottoms, color=col, alpha=0.85, label=s)
        bottoms = [b + v for b, v in zip(bottoms, vals)]
    ax1.set_xticklabels(priorities, color=TEXT, fontsize=9)
    style_ax(ax1, 'Tasks by Priority & Status')
    ax1.legend(facecolor=CARD_BG, labelcolor=TEXT, fontsize=7, loc='upper right')
    ax2.set_facecolor(CARD_BG)
    total_est     = sum(t.estimated_hours for t in tasks)
    completed_est = sum(t.estimated_hours for t in tasks if t.status == 'Completed')
    if total_est > 0:
        wedges, texts, autos = ax2.pie(
            [completed_est, total_est - completed_est], labels=['Done', 'Remaining'],
            colors=[ACCENT3, ACCENT2], autopct='%1.0f%%', startangle=90,
            wedgeprops=dict(width=0.55), pctdistance=0.75)
        for t  in texts:  t.set_color(TEXT);      t.set_fontsize(9)
        for at in autos:  at.set_color('#0f1117'); at.set_fontsize(8); at.set_fontweight('bold')
        ax2.text(0, 0, f'{total_est:.0f}h\ntotal', ha='center', va='center', color=TEXT, fontsize=8)
    else:
        ax2.text(0.5, 0.5, 'No hours set', ha='center', va='center', color=TEXT); ax2.axis('off')
    ax2.set_title('Estimated Hours Distribution', color=TEXT, fontsize=10, fontweight='bold')
    plt.tight_layout(); return fig_to_b64(fig)

def make_cross_project_chart(rows):
    if not rows: return None
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4))
    fig.patch.set_facecolor(DARK_BG)
    names    = [r['project'].name[:16] + '…' if len(r['project'].name) > 16 else r['project'].name for r in rows]
    healths  = [r['health'] for r in rows]
    h_colors = [ACCENT3 if h >= 70 else '#f59e0b' if h >= 40 else ACCENT2 for h in healths]
    x = np.arange(len(rows))
    ax1.set_facecolor(CARD_BG)
    bars = ax1.bar(x, healths, color=h_colors, alpha=0.85, width=0.55)
    for bar, v in zip(bars, healths):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1, f'{v:.0f}', ha='center', color=TEXT, fontsize=8)
    ax1.set_xticks(x); ax1.set_xticklabels(names, rotation=25, ha='right', color=TEXT, fontsize=8)
    ax1.set_ylim(0, 115); style_ax(ax1, 'Project Health Scores')
    ax1.axhline(70, color=ACCENT,  linewidth=1, linestyle='--', alpha=0.5)
    ax1.axhline(40, color=ACCENT2, linewidth=1, linestyle='--', alpha=0.5)
    ax2.set_facecolor(CARD_BG)
    ax2.bar(x, [r['total']     for r in rows], color=GRID,    alpha=0.4,  width=0.55, label='Total')
    ax2.bar(x, [r['completed'] for r in rows], color=ACCENT3, alpha=0.8,  width=0.55, label='Completed')
    ax2.bar(x, [r['high_risk'] for r in rows], color=ACCENT2, alpha=0.75, width=0.55,
            bottom=[r['completed'] for r in rows], label='High Risk')
    ax2.set_xticks(x); ax2.set_xticklabels(names, rotation=25, ha='right', color=TEXT, fontsize=8)
    style_ax(ax2, 'Tasks: Completed vs High Risk')
    ax2.legend(facecolor=CARD_BG, labelcolor=TEXT, fontsize=7)
    plt.tight_layout(); return fig_to_b64(fig)

def make_timeline_overview(tasks):
    fig, ax = plt.subplots(figsize=(10, 4.5))
    fig.patch.set_facecolor(DARK_BG); ax.set_facecolor(CARD_BG)
    now_t  = datetime.utcnow()
    active = [t for t in tasks if t.status != 'Completed']
    if not active:
        ax.text(0.5, 0.5, 'All tasks completed!', ha='center', va='center', color=ACCENT3, fontsize=13)
        ax.axis('off'); return fig_to_b64(fig)
    xs     = [(t.deadline - now_t).days for t in active]
    ys     = [t.progress for t in active]
    sizes  = [max(30, t.estimated_hours * 12) for t in active]
    colors = [{'Low': ACCENT3, 'Medium': '#f59e0b', 'High': ACCENT2}[compute_risk(t)] for t in active]
    ax.scatter(xs, ys, c=colors, s=sizes, alpha=0.8, edgecolors='none', zorder=3)
    for t, x, y in zip(active, xs, ys):
        ax.annotate(t.title[:14], (x, y), textcoords='offset points', xytext=(5, 4), fontsize=6.5, color=TEXT, alpha=0.8)
    ax.axvline(0, color=ACCENT2, linewidth=1.2, linestyle='--', alpha=0.6)
    ax.axhline(50, color=GRID, linewidth=1, linestyle=':', alpha=0.5)
    ax.set_xlabel('Days Until Deadline', color=TEXT); ax.set_ylabel('Progress (%)', color=TEXT)
    ax.set_ylim(-5, 110)
    style_ax(ax, 'Risk Scatter: Days Left vs Progress')
    patches = [mpatches.Patch(color=ACCENT3, label='Low'),
               mpatches.Patch(color='#f59e0b', label='Medium'),
               mpatches.Patch(color=ACCENT2,   label='High')]
    ax.legend(handles=patches, facecolor=CARD_BG, labelcolor=TEXT, fontsize=7, loc='lower right')
    ax.grid(color=GRID, linewidth=0.4, alpha=0.6)
    plt.tight_layout(); return fig_to_b64(fig)

# ─── Resume Analyzer ───────────────────────────────────────────────────────────

def extract_text(filepath):
    ext  = filepath.rsplit('.', 1)[1].lower()
    text = ''
    if ext == 'pdf':
        try:
            import PyPDF2
            with open(filepath, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages: text += page.extract_text() or ''
        except: pass
    elif ext in ('doc', 'docx'):
        try:
            from docx import Document
            text = '\n'.join(p.text for p in Document(filepath).paragraphs)
        except: pass
    elif ext == 'txt':
        with open(filepath, 'r', errors='ignore') as f: text = f.read()
    return text.lower()

def analyze_resume(resume_text, jd_text):
    stop = {'the','a','an','and','or','but','in','on','at','to','for','of','with',
            'is','are','was','were','be','been','have','has','will','can','this',
            'that','which','their','they','we','our','you','your'}
    def keywords(text, top=30):
        words = re.findall(r'\b[a-z][a-z+#\.]{2,}\b', text.lower())
        freq  = {}
        for w in words:
            if w not in stop: freq[w] = freq.get(w, 0) + 1
        return sorted(freq, key=freq.get, reverse=True)[:top]
    jd_kw  = keywords(jd_text, 40)
    matched = [k for k in jd_kw if k in resume_text]
    missing = [k for k in jd_kw if k not in resume_text]
    section_kw = {
        'Skills':        ['skills','technologies','tools','programming','languages'],
        'Experience':    ['experience','work','employment','job','position','role'],
        'Education':     ['education','degree','university','college','bachelor','master'],
        'Projects':      ['project','built','developed','created','implemented'],
        'Certifications':['certif','license','award','achievement'],
    }
    sections = {s: sum(1 for kw in kws if kw in resume_text) for s, kws in section_kw.items()}
    section_scores = {s: min(100, v / len(section_kw[s]) * 100) for s, v in sections.items()}
    return {
        'match_score':    round(len(matched) / max(len(jd_kw), 1) * 100, 1),
        'matched':        matched[:15],
        'missing':        missing[:10],
        'section_scores': section_scores,
        'jd_keywords':    jd_kw[:20],
    }

def make_resume_charts(analysis):
    fig = plt.figure(figsize=(12, 8))
    fig.patch.set_facecolor(DARK_BG)
    gs  = GridSpec(2, 3, figure=fig, hspace=0.45, wspace=0.4)
    # Gauge
    ax0   = fig.add_subplot(gs[0, 0]); ax0.set_facecolor(CARD_BG)
    score = analysis['match_score']
    color = ACCENT3 if score >= 70 else '#fbbf24' if score >= 40 else ACCENT2
    theta = np.linspace(np.pi, 0, 100)
    ax0.plot(np.cos(theta), np.sin(theta), color=GRID, linewidth=12)
    theta2 = np.linspace(np.pi, np.pi - (score / 100) * np.pi, 100)
    ax0.plot(np.cos(theta2), np.sin(theta2), color=color, linewidth=12)
    ax0.text(0, 0.1, f'{score:.0f}%', ha='center', va='center', color=color, fontsize=20, fontweight='bold')
    ax0.text(0, -0.3, 'Match Score', ha='center', color=TEXT, fontsize=9)
    ax0.set_xlim(-1.3, 1.3); ax0.set_ylim(-0.5, 1.3); ax0.axis('off')
    ax0.set_title('JD Match', color=TEXT, fontsize=10, fontweight='bold')
    # Sections
    ax1  = fig.add_subplot(gs[0, 1]); ax1.set_facecolor(CARD_BG)
    secs = list(analysis['section_scores'].keys())
    vals = list(analysis['section_scores'].values())
    bars = ax1.barh(secs, vals, color=[ACCENT3 if v >= 60 else '#fbbf24' if v >= 30 else ACCENT2 for v in vals], alpha=0.85)
    ax1.barh(secs, [100] * len(secs), color=GRID, alpha=0.2)
    for bar, v in zip(bars, vals):
        ax1.text(v + 2, bar.get_y() + bar.get_height() / 2, f'{v:.0f}%', va='center', color=TEXT, fontsize=8)
    ax1.set_xlim(0, 120); style_ax(ax1, 'Resume Sections')
    ax1.set_yticklabels(secs, color=TEXT, fontsize=8)
    # Keyword donut
    ax2 = fig.add_subplot(gs[0, 2]); ax2.set_facecolor(CARD_BG)
    mn, mi = len(analysis['matched']), len(analysis['missing'])
    if mn + mi > 0:
        wedges, texts, autos = ax2.pie(
            [mn, mi], labels=['Matched','Missing'], colors=[ACCENT3, ACCENT2],
            autopct='%1.0f%%', startangle=90, wedgeprops=dict(width=0.55), pctdistance=0.75)
        for t  in texts:  t.set_color(TEXT);      t.set_fontsize(8)
        for at in autos:  at.set_color('#0f1117'); at.set_fontsize(8); at.set_fontweight('bold')
    ax2.set_title('Keyword Coverage', color=TEXT, fontsize=10, fontweight='bold')
    # Found/missing bar
    ax3 = fig.add_subplot(gs[1, :2]); ax3.set_facecolor(CARD_BG)
    kws      = analysis['matched'][:8] + analysis['missing'][:7]
    kw_colors = [ACCENT3] * len(analysis['matched'][:8]) + [ACCENT2] * len(analysis['missing'][:7])
    if kws:
        ax3.barh(kws, [1] * len(kws), color=kw_colors, alpha=0.85)
        ax3.set_xlim(0, 1.5); ax3.set_yticks(range(len(kws))); ax3.set_yticklabels(kws, color=TEXT, fontsize=8)
        ax3.tick_params(bottom=False, labelbottom=False)
        patches = [mpatches.Patch(color=ACCENT3, label='Found'), mpatches.Patch(color=ACCENT2, label='Missing')]
        ax3.legend(handles=patches, facecolor=CARD_BG, labelcolor=TEXT, fontsize=8, loc='lower right')
    style_ax(ax3, 'JD Keywords: Found vs Missing')
    # Missing list
    ax4 = fig.add_subplot(gs[1, 2]); ax4.set_facecolor(CARD_BG); ax4.axis('off')
    ax4.set_title('Top Missing Keywords', color=TEXT, fontsize=10, fontweight='bold')
    ax4.text(0.05, 0.9, '\n'.join(f'• {k}' for k in analysis['missing'][:8]) or 'Great match!',
             transform=ax4.transAxes, color=ACCENT2, fontsize=9, va='top', family='monospace')
    return fig_to_b64(fig)

# ─── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return redirect(url_for('dashboard') if current_user.is_authenticated else url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('dashboard'))
    if request.method == 'POST':
        user = db_get_user_by_email(request.form['email'])
        if user and user.check_password(request.form['password']):
            login_user(user); return redirect(url_for('dashboard'))
        flash('Invalid credentials.', 'danger')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        if db_get_user_by_email(request.form['email']):
            flash('Email already exists.', 'danger'); return redirect(url_for('register'))
        db_create_user(request.form['username'], request.form['email'],
                       request.form['password'], request.form['role'])
        flash('Account created! Please login.', 'success'); return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/logout')
@login_required
def logout():
    logout_user(); return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'manager':
        projects  = db_get_projects_by_manager(current_user.id)
        all_tasks = db_get_tasks_by_manager(current_user.id)
    else:
        all_tasks = db_get_tasks_by_assignee(current_user.id)
        projects  = db_get_projects_for_user(current_user.id)
    stats = {
        'total_projects': len(projects),
        'total_tasks':    len(all_tasks),
        'completed':      sum(1 for t in all_tasks if t.status == 'Completed'),
        'high_risk':      sum(1 for t in all_tasks if compute_risk(t) == 'High'),
        'avg_progress':   round(np.mean([t.progress for t in all_tasks]) if all_tasks else 0, 1),
    }
    project_data = []
    for p in projects:
        tasks = db_get_tasks_by_project(p.id)
        project_data.append({
            'project':     p,
            'health':      round(project_health(tasks), 1),
            'risk_counts': {'Low':    sum(1 for t in tasks if compute_risk(t) == 'Low'),
                            'Medium': sum(1 for t in tasks if compute_risk(t) == 'Medium'),
                            'High':   sum(1 for t in tasks if compute_risk(t) == 'High')},
            'total':       len(tasks),
            'completed':   sum(1 for t in tasks if t.status == 'Completed'),
        })
    return render_template('dashboard.html', stats=stats, project_data=project_data)

@app.route('/project/new', methods=['GET', 'POST'])
@login_required
def new_project():
    if current_user.role != 'manager':
        flash('Only managers can create projects.', 'warning'); return redirect(url_for('dashboard'))
    if request.method == 'POST':
        pid = db_create_project(request.form['name'], request.form['description'],
                                datetime.strptime(request.form['deadline'], '%Y-%m-%d'), current_user.id)
        flash('Project created!', 'success'); return redirect(url_for('project_detail', pid=pid))
    return render_template('project_form.html', project=None)

@app.route('/project/<int:pid>')
@login_required
def project_detail(pid):
    p = db_get_project(pid)
    if not p: abort(404)
    tasks      = db_get_tasks_by_project(pid)
    users      = db_get_all_users()
    now        = datetime.utcnow()
    insights   = generate_insights(p, tasks)
    health     = round(project_health(tasks), 1)
    task_risks = {t.id: compute_risk(t) for t in tasks}
    chart1 = make_task_progress_chart(tasks)
    chart2 = make_deadline_chart(tasks)
    chart3 = make_risk_chart(tasks)
    chart4 = make_employee_chart(users, tasks)
    chart5 = make_gantt_chart(tasks, p)
    chart6 = make_burndown_chart(tasks, p)
    chart7 = make_velocity_chart(tasks)
    return render_template('project_detail.html',
        project=p, tasks=tasks, users=users, now=now,
        insights=insights, health=health, task_risks=task_risks,
        chart1=chart1, chart2=chart2, chart3=chart3, chart4=chart4,
        chart5=chart5, chart6=chart6, chart7=chart7)

@app.route('/project/edit/<int:pid>', methods=['GET', 'POST'])
@login_required
def edit_project(pid):
    p = db_get_project(pid)
    if not p: abort(404)
    if current_user.role != 'manager':
        flash('Only managers can edit projects.', 'warning'); return redirect(url_for('dashboard'))
    if request.method == 'POST':
        db_update_project(pid, request.form['name'], request.form['description'],
                          datetime.strptime(request.form['deadline'], '%Y-%m-%d'))
        flash('Project updated!', 'success'); return redirect(url_for('project_detail', pid=pid))
    return render_template('project_form.html', project=p)

@app.route('/project/delete/<int:pid>', methods=['POST'])
@login_required
def delete_project(pid):
    db_delete_project(pid); flash('Project deleted.', 'info'); return redirect(url_for('dashboard'))

@app.route('/project/export/<int:pid>')
@login_required
def export_project(pid):
    p = db_get_project(pid)
    if not p: abort(404)
    tasks     = db_get_tasks_by_project(pid)
    users_map = {u.id: u.username for u in db_get_all_users()}
    now       = datetime.utcnow()
    rows = []
    for t in tasks:
        total_s   = (t.deadline - t.created_at).total_seconds()
        elapsed_s = (now - t.created_at).total_seconds()
        expected  = min(100, (elapsed_s / total_s) * 100) if total_s > 0 else 0
        rows.append({'Task': t.title, 'Assignee': users_map.get(t.assigned_to, 'Unassigned'),
                     'Deadline': t.deadline.strftime('%Y-%m-%d'), 'Estimated Hours': t.estimated_hours,
                     'Progress (%)': t.progress, 'Expected Progress (%)': round(expected, 1),
                     'Gap (%)': round(expected - t.progress, 1), 'Status': t.status,
                     'Priority': t.priority, 'Risk Level': compute_risk(t)})
    df  = pd.DataFrame(rows); buf = io.StringIO(); df.to_csv(buf, index=False); buf.seek(0)
    return send_file(io.BytesIO(buf.getvalue().encode()), mimetype='text/csv',
                     as_attachment=True, download_name=f'{p.name.replace(" ","_")}_tasks.csv')

@app.route('/task/new/<int:pid>', methods=['GET', 'POST'])
@login_required
def new_task(pid):
    p = db_get_project(pid)
    if not p: abort(404)
    members = db_get_members()
    if request.method == 'POST':
        raw = request.form.get('assigned_to')
        db_create_task(request.form['title'], request.form['description'], pid,
                       int(raw) if raw else None,
                       datetime.strptime(request.form['deadline'], '%Y-%m-%d'),
                       float(request.form['estimated_hours']), request.form['priority'])
        flash('Task added!', 'success'); return redirect(url_for('project_detail', pid=pid))
    return render_template('task_form.html', project=p, task=None, users=members)

@app.route('/task/edit/<int:tid>', methods=['GET', 'POST'])
@login_required
def edit_task(tid):
    t = db_get_task(tid)
    if not t: abort(404)
    p = db_get_project(t.project_id); members = db_get_members()
    if request.method == 'POST':
        raw = request.form.get('assigned_to')
        db_update_task(tid, request.form['title'], request.form['description'],
                       int(raw) if raw else None,
                       datetime.strptime(request.form['deadline'], '%Y-%m-%d'),
                       float(request.form['estimated_hours']), request.form['priority'],
                       float(request.form['progress']), request.form['status'])
        flash('Task updated!', 'success'); return redirect(url_for('project_detail', pid=t.project_id))
    return render_template('task_form.html', project=p, task=t, users=members)

@app.route('/task/delete/<int:tid>', methods=['POST'])
@login_required
def delete_task(tid):
    t = db_get_task(tid)
    if not t: abort(404)
    pid = t.project_id; db_delete_task(tid); flash('Task deleted.', 'info')
    return redirect(url_for('project_detail', pid=pid))

@app.route('/task/update_progress/<int:tid>', methods=['POST'])
@login_required
def update_progress(tid):
    t = db_get_task(tid)
    if not t: return jsonify({'success': False}), 404
    progress = float(request.form['progress'])
    status   = 'Completed' if progress >= 100 else ('In Progress' if progress > 0 else t.status)
    db_update_task_progress(tid, progress, status)
    return jsonify({'success': True, 'risk': compute_risk(db_get_task(tid))})

@app.route('/employees')
@login_required
def employees():
    members   = db_get_members()
    all_tasks = db_get_all_tasks()
    employee_data = []
    for u in members:
        utasks = [t for t in all_tasks if t.assigned_to == u.id]
        employee_data.append({
            'user': u, 'tasks': utasks, 'total': len(utasks),
            'completed':   sum(1 for t in utasks if t.status == 'Completed'),
            'in_progress': sum(1 for t in utasks if t.status == 'In Progress'),
            'avg_progress':round(np.mean([t.progress for t in utasks]) if utasks else 0, 1),
            'high_risk':   sum(1 for t in utasks if compute_risk(t) == 'High'),
        })
    chart = make_employee_chart(members, all_tasks)
    return render_template('employees.html', employee_data=employee_data, chart=chart)

@app.route('/resume_analyzer', methods=['GET', 'POST'])
@login_required
def resume_analyzer():
    result = None
    if request.method == 'POST':
        jd_text     = request.form.get('job_description', '')
        resume_text = ''
        if 'resume_file' in request.files and request.files['resume_file'].filename:
            f = request.files['resume_file']
            if allowed_file(f.filename):
                fn = secure_filename(f.filename)
                path = os.path.join(app.config['UPLOAD_FOLDER'], fn)
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                f.save(path); resume_text = extract_text(path); os.remove(path)
        if not resume_text:
            resume_text = request.form.get('resume_text', '')
        if resume_text and jd_text:
            analysis = analyze_resume(resume_text, jd_text)
            result   = {'analysis': analysis, 'chart': make_resume_charts(analysis)}
        else:
            flash('Please provide both resume and job description.', 'warning')
    return render_template('resume_analyzer.html', result=result)

@app.route('/analytics')
@login_required
def analytics():
    if current_user.role == 'manager':
        projects  = db_get_projects_by_manager(current_user.id)
        all_tasks = db_get_tasks_by_manager(current_user.id)
    else:
        all_tasks = db_get_tasks_by_assignee(current_user.id)
        projects  = db_get_projects_for_user(current_user.id)
    now  = datetime.utcnow()
    rows = []
    for p in projects:
        tasks = db_get_tasks_by_project(p.id)
        if not tasks: continue
        df = pd.DataFrame([{'progress': t.progress, 'est_hours': t.estimated_hours,
                             'risk': compute_risk(t), 'status': t.status,
                             'days_remaining': (t.deadline - now).days} for t in tasks])
        rows.append({'project': p, 'health': round(project_health(tasks), 1),
                     'avg_prog': round(float(df['progress'].mean()), 1),
                     'total_hrs': round(float(df['est_hours'].sum()), 1),
                     'completed': int((df['status'] == 'Completed').sum()),
                     'total': len(tasks),
                     'high_risk': int((df['risk'] == 'High').sum()),
                     'overdue': int((df['days_remaining'] < 0).sum())})
    total_tasks     = len(all_tasks)
    total_completed = sum(1 for t in all_tasks if t.status == 'Completed')
    total_high_risk = sum(1 for t in all_tasks if compute_risk(t) == 'High')
    total_overdue   = sum(1 for t in all_tasks if (t.deadline - now).days < 0 and t.status != 'Completed')
    total_hours     = round(sum(t.estimated_hours for t in all_tasks), 1)
    avg_health      = round(float(np.mean([r['health'] for r in rows])) if rows else 0, 1)
    return render_template('analytics.html', projects=projects, rows=rows,
        total_tasks=total_tasks, total_completed=total_completed,
        total_high_risk=total_high_risk, total_overdue=total_overdue,
        total_hours=total_hours, avg_health=avg_health,
        cross_chart=make_cross_project_chart(rows) if rows else None,
        timeline_chart=make_timeline_overview(all_tasks) if all_tasks else None, now=now)

@app.route('/api/notifications')
@login_required
def api_notifications():
    tasks  = db_get_tasks_by_manager(current_user.id) if current_user.role == 'manager' \
             else db_get_tasks_by_assignee(current_user.id)
    notifs = []
    now_dt = datetime.utcnow()
    for t in tasks:
        if t.status == 'Completed': continue
        days_left = (t.deadline - now_dt).days
        risk      = compute_risk(t)
        if days_left < 0:
            notifs.append({'type': 'danger',  'msg': f'"{t.title}" is OVERDUE by {-days_left} day(s).'})
        elif days_left <= 2 and t.progress < 80:
            notifs.append({'type': 'warning', 'msg': f'"{t.title}" deadline in {days_left}d — only {t.progress:.0f}% done.'})
        elif risk == 'High':
            notifs.append({'type': 'danger',  'msg': f'"{t.title}" is HIGH RISK.'})
    return jsonify({'count': len(notifs), 'items': notifs[:8]})

@app.route('/api/task_summary')
@login_required
def api_task_summary():
    tasks = db_get_tasks_by_manager(current_user.id) if current_user.role == 'manager' \
            else db_get_tasks_by_assignee(current_user.id)
    return jsonify({
        'total':       len(tasks),
        'completed':   sum(1 for t in tasks if t.status == 'Completed'),
        'in_progress': sum(1 for t in tasks if t.status == 'In Progress'),
        'not_started': sum(1 for t in tasks if t.status == 'Not Started'),
        'high_risk':   sum(1 for t in tasks if compute_risk(t) == 'High'),
        'avg_progress':round(float(np.mean([t.progress for t in tasks])) if tasks else 0, 1),
    })

# ─── Seed demo data ────────────────────────────────────────────────────────────

def seed_demo():
    with get_db() as conn:
        cur = conn.cursor()

        # Users
        cur.execute("SELECT COUNT(*) FROM user")
        if cur.fetchone()[0] == 0:
            for username, email, pw, role in [
                ('Manager', 'manager@taskpulse.com', 'manager123', 'manager'),
                ('Alice',   'alice@taskpulse.com',   'member123',  'member'),
                ('Bob',     'bob@taskpulse.com',     'member123',  'member'),
                ('Carol',   'carol@taskpulse.com',   'member123',  'member'),
            ]:
                cur.execute(
                    "INSERT INTO user (username, email, password_hash, role) VALUES (?,?,?,?)",
                    (username, email, generate_password_hash(pw), role)
                )
            conn.commit()

        # Project + tasks
        cur.execute("SELECT COUNT(*) FROM project")
        if cur.fetchone()[0] == 0:
            cur.execute("SELECT id FROM user WHERE role='manager' LIMIT 1"); mgr_id   = cur.fetchone()[0]
            cur.execute("SELECT id FROM user WHERE username='Alice'  LIMIT 1"); alice_id = cur.fetchone()[0]
            cur.execute("SELECT id FROM user WHERE username='Bob'    LIMIT 1"); bob_id   = cur.fetchone()[0]
            cur.execute("SELECT id FROM user WHERE username='Carol'  LIMIT 1"); carol_id = cur.fetchone()[0]

            cur.execute(
                "INSERT INTO project (name, description, deadline, created_by) VALUES (?,?,?,?)",
                ('TaskPulse MVP Launch', 'Build and ship the TaskPulse product.',
                 _fmt_dt(datetime.utcnow() + timedelta(days=30)), mgr_id)
            )
            pid = cur.lastrowid

            # (title, uid, hrs, progress, status, priority, days_dl, created_ago)
            for title, uid, hrs, prog, status, priority, days_dl, created_ago in [
                ('UI Design & Prototyping',  alice_id, 10, 85,  'In Progress', 'High',   5,  -20),
                ('Backend API Development',  bob_id,   20, 60,  'In Progress', 'High',   8,  -15),
                ('Database Schema Setup',    bob_id,   5,  100, 'Completed',   'Medium', 11, -15),
                ('Frontend Integration',     carol_id, 15, 20,  'In Progress', 'High',   2,  -20),
                ('Testing & QA',             alice_id, 10, 10,  'In Progress', 'Medium', 1,  -18),
                ('Documentation',            carol_id, 8,  0,   'Not Started', 'Low',    14, -10),
                ('Deployment Setup',         bob_id,   6,  50,  'In Progress', 'Medium', 17, -15),
            ]:
                cur.execute("""
                    INSERT INTO task
                      (title, project_id, assigned_to, deadline, estimated_hours,
                       progress, status, priority, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (title, pid, uid,
                      _fmt_dt(datetime.utcnow() + timedelta(days=days_dl)),
                      hrs, prog, status, priority,
                      _fmt_dt(datetime.utcnow() + timedelta(days=created_ago))))
            conn.commit()

# ─── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    seed_demo()
    app.run(debug=True, port=5000)
