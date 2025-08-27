// dashboard.js
(async function load(){
  const p = new URLSearchParams(location.search);
  const token   = p.get('token') || '';
  const headers = token ? { 'X-Dashboard-Token': token } : {};
  const qs      = token ? ('?token=' + encodeURIComponent(token)) : '';

  const resp = await fetch('/dashboard/metrics.json' + qs, { headers });
  const m = await resp.json();

  const k = document.getElementById('kpis');
  const pct = (x)=> (x*100).toFixed(1)+'%';
  k.innerHTML = `
    <div class="card"><div class="muted">GMV 1j</div><div class="kpi">${(m.gmv_1d||0).toFixed(0)}</div></div>
    <div class="card"><div class="muted">GMV Hier</div><div class="kpi">${(m.gmv_yday||0).toFixed(0)}</div></div>
    <div class="card"><div class="muted">Commandes 1j</div><div class="kpi">${m.orders_1d||0}</div></div>
    <div class="card"><div class="muted">GMV 7j</div><div class="kpi">${(m.gmv_7d||0).toFixed(0)}</div></div>
    <div class="card"><div class="muted">Commandes 7j</div><div class="kpi">${m.orders_7d||0}</div></div>
    <div class="card"><div class="muted">AOV 7j</div><div class="kpi">${(m.aov_7d||0).toFixed(0)}</div></div>
    <div class="card"><div class="muted">Recovered GMV 7j</div><div class="kpi">${(m.recovered_gmv_7d||0).toFixed(0)}</div></div>
    <div class="card"><div class="muted">Recovered Orders 7j</div><div class="kpi">${m.recovered_orders_7d||0}</div></div>
    <div class="card"><div class="muted">Recovered Rate 7j</div><div class="kpi">${m.ab_failed_7d? pct(m.recovered_rate_7d||0): 'n/a'}</div></div>
    <div class="card"><div class="muted">Abandons today</div><div class="kpi">${m.abandoned_24h||0}</div></div>
    <div class="card"><div class="muted">Failed today</div><div class="kpi">${m.failed_24h||0}</div></div>
    <div class="card"><div class="muted">Relances échues</div><div class="kpi">${m.sent_due||0}</div></div>
    <div class="card"><div class="muted">Relances planifiées</div><div class="kpi">${m.pending_future||0}</div></div>
    <div class="card"><div class="muted">Clients programmés pour relance</div><div class="kpi">${m.relance_customer_pending_count||0}</div></div>
    <div class="card"><div class="muted">24h Email</div><div class="kpi">${m.sent_24h_email||0}</div></div>
    <div class="card"><div class="muted">24h WhatsApp</div><div class="kpi">${m.sent_24h_whatsapp||0}</div></div>
    <div class="card"><div class="muted">Erreurs 24h</div><div class="kpi ko">${m.errors_24h||0}</div></div>
  `;

  const steps = document.getElementById('steps');
  steps.innerHTML =
    (m.conversions_by_step_7d||[])
      .map(r=>`<span class="pill">${r.step}: ${r.conv} (${(r.gmv||0).toFixed(0)})</span>`)
      .join('') || '<div class="muted">Aucune donnée</div>';

  function table(id, rows, cols){
    const t = document.getElementById(id);
    if(!rows || !rows.length){t.innerHTML='<tr><td class="muted">Aucune donnée</td></tr>'; return;}
    t.innerHTML = '<thead><tr>'+cols.map(c=>`<th>${c[1]}</th>`).join('')+'</tr></thead>'+
                  '<tbody>'+rows.map(r=>'<tr>'+cols.map(c=>`<td>${r[c[0]]??''}</td>`).join('')+'</tr>').join('')+'</tbody>';
  }
  table('topprod', m.top_products_7d, [['product_id','Produit'],['orders','Cmds'],['gmv','GMV']]);
  table('countries', m.countries_7d, [['country','Pays'],['orders','Cmds'],['gmv','GMV']]);
  table('rfm', m.rfm_segments, [['segment','Segment'],['customers','Clients'],['gmv','GMV']]);
})();
