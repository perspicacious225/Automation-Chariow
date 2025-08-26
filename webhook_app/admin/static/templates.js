async function doPreview(){
  const f = document.getElementById('tplForm');
  const fd = new FormData(f);
  const payload = {
    body: fd.get('body') || "",
    is_full_html: !!fd.get('is_full_html')
  };
  try{
    const res = await fetch('/dashboard/templates/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const html = await res.text();
    const w = window.open('', '_blank');
    w.document.open(); w.document.write(html); w.document.close();
  }catch(err){
    alert('Prévisualisation indisponible: ' + err);
  }
}
