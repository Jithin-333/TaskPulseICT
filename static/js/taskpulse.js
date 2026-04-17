/* ─── TaskPulse Main JS ──────────────────────────────────────── */

// ─── Notifications ────────────────────────────────────────────
const NotifSystem = {
  panel: null,
  badge: null,
  list:  null,
  open:  false,

  init() {
    this.panel = document.getElementById('notif-panel');
    this.badge = document.getElementById('notif-badge');
    this.list  = document.getElementById('notif-list');
    if (!this.panel) return;

    // Bell click
    document.getElementById('notif-bell')?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.toggle();
    });

    // Outside click to close
    document.addEventListener('click', (e) => {
      if (this.open && !this.panel.contains(e.target)) this.close();
    });

    // Load immediately then every 30s
    this.load();
    setInterval(() => this.load(), 30000);
  },

  toggle() { this.open ? this.close() : this.openPanel(); },

  openPanel() {
    this.panel.style.display = 'block';
    this.open = true;
    this.load();
  },

  close() {
    if (this.panel) this.panel.style.display = 'none';
    this.open = false;
  },

  load() {
    fetch('/api/notifications')
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data) return;
        // Badge
        if (this.badge) {
          this.badge.textContent = data.count;
          this.badge.style.display = data.count > 0 ? 'flex' : 'none';
        }
        // List
        if (this.list) {
          if (data.items && data.items.length > 0) {
            this.list.innerHTML = data.items.map(n => `
              <div class="notif-item ${n.type}">
                ${n.type === 'danger' ? '🔴' : '🟡'} ${escHtml(n.msg)}
              </div>`).join('');
          } else {
            this.list.innerHTML = '<div class="notif-empty">✅ No active alerts</div>';
          }
        }
      })
      .catch(() => {});
  }
};

// ─── Progress Sliders ─────────────────────────────────────────
const ProgressSliders = {
  init() {
    document.querySelectorAll('.progress-slider').forEach(slider => {
      const taskId  = slider.dataset.taskId;
      const display = document.getElementById(`prog-display-${taskId}`);
      const bar     = document.getElementById(`prog-bar-${taskId}`);

      slider.addEventListener('input', () => {
        const val = parseInt(slider.value);
        if (display) display.textContent = val + '%';
        if (bar) {
          bar.style.width = val + '%';
          bar.className = 'progress-bar ' +
            (val >= 70 ? 'progress-bar-green' : val >= 40 ? 'progress-bar-amber' : 'progress-bar-red');
        }
      });

      slider.addEventListener('change', () => {
        const val = slider.value;
        fetch(`/task/update_progress/${taskId}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: `progress=${val}`
        })
        .then(r => r.json())
        .then(data => {
          if (data.success) {
            // Update risk badge if present
            const riskEl = document.getElementById(`risk-${taskId}`);
            if (riskEl && data.risk) {
              riskEl.textContent = data.risk;
              riskEl.className = 'badge-risk badge ' +
                { Low: 'risk-low', Medium: 'risk-medium', High: 'risk-high' }[data.risk];
            }
            // Reload after short delay to refresh charts
            setTimeout(() => location.reload(), 1200);
          }
        })
        .catch(() => {});
      });
    });
  }
};

// ─── Auto-dismiss Alerts ──────────────────────────────────────
const AlertAutoDismiss = {
  init(ms = 5000) {
    document.querySelectorAll('.alert.alert-success').forEach(el => {
      setTimeout(() => {
        el.style.transition = 'opacity .4s';
        el.style.opacity = '0';
        setTimeout(() => el.remove(), 400);
      }, ms);
    });
  }
};

// ─── Confirm Delete Buttons ───────────────────────────────────
const ConfirmDelete = {
  init() {
    document.querySelectorAll('[data-confirm]').forEach(el => {
      el.addEventListener('click', (e) => {
        if (!confirm(el.dataset.confirm)) e.preventDefault();
      });
    });
  }
};

// ─── Health Score Color ───────────────────────────────────────
const HealthColors = {
  init() {
    document.querySelectorAll('[data-health]').forEach(el => {
      const h = parseFloat(el.dataset.health);
      el.style.color = h >= 70 ? 'var(--accent3)' : h >= 40 ? 'var(--warn)' : 'var(--danger)';
    });
  }
};

// ─── Animate Progress Bars on Load ───────────────────────────
const AnimateBars = {
  init() {
    // Start at 0, animate to target width
    document.querySelectorAll('.progress-bar[data-width]').forEach(bar => {
      const target = bar.dataset.width;
      bar.style.width = '0%';
      requestAnimationFrame(() => {
        setTimeout(() => { bar.style.width = target + '%'; }, 100);
      });
    });
  }
};

// ─── Tooltip Init ─────────────────────────────────────────────
const TooltipInit = {
  init() {
    if (window.bootstrap?.Tooltip) {
      document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
        new bootstrap.Tooltip(el, { trigger: 'hover' });
      });
    }
  }
};

// ─── Utilities ────────────────────────────────────────────────
function escHtml(str) {
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function updateProgress(taskId, val) {
  fetch(`/task/update_progress/${taskId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: `progress=${val}`
  }).then(r => r.json()).then(d => {
    if (d.success) setTimeout(() => location.reload(), 800);
  });
}

// ─── Boot ─────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  NotifSystem.init();
  ProgressSliders.init();
  AlertAutoDismiss.init();
  ConfirmDelete.init();
  HealthColors.init();
  AnimateBars.init();
  TooltipInit.init();
});
