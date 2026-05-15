// dashboard.js — CHARIOW v1
(async function load() {
  const p       = new URLSearchParams(location.search);
  const token   = p.get('token') || '';
  const headers = token ? { 'X-Dashboard-Token': token } : {};
  const qs      = token ? ('?token=' + encodeURIComponent(token)) : '';

  try {
    const resp = await fetch('/dashboard/metrics.json' + qs, { headers });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const m = await resp.json();

    // Utiliser le renderer défini dans index.html si disponible
    if (typeof window.renderDashboard === 'function') {
      window.renderDashboard(m);
    }
  } catch(e) {
    console.error('Erreur chargement dashboard:', e);
    const kpis = document.getElementById('kpis');
    if (kpis) {
      kpis.innerHTML = `
        <div style="grid-column:1/-1;padding:32px;text-align:center;color:var(--red);">
          <i class="ti ti-alert-triangle" style="font-size:24px;display:block;margin-bottom:8px;"></i>
          Erreur chargement des données — ${e.message}
        </div>`;
    }
  }
})();

// Fonction refresh manuelle
window.loadDashboard = function() {
  location.reload();
};