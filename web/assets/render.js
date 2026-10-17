// Render a forged tool's output safely. Output is produced by agent-written code, so:
// text/json/table use textContent, markdown is sanitized, html runs in a script-less iframe.
window.renderOutput = function (el, res) {
  el.innerHTML = '';
  if (!res) return;
  if (!res.ok) {
    const box = document.createElement('div'); box.className = 'out-err';
    box.textContent = res.error || 'The tool failed.'; el.appendChild(box); return;
  }
  const kind = res.type, out = res.output;
  if (kind === 'image' && typeof out === 'string' && out.startsWith('data:image/')) {
    const img = document.createElement('img'); img.src = out; img.alt = 'Tool output'; img.className = 'out-img';
    el.appendChild(img);
    const a = document.createElement('a'); a.href = out; a.download = 'toolsmith-output.png';
    a.className = 'btn sm'; a.textContent = '⬇ Download'; a.style.marginTop = '12px'; el.appendChild(a);
  } else if (kind === 'table' && Array.isArray(out)) {
    const wrap = document.createElement('div'); wrap.className = 'out-table';
    const t = document.createElement('table'); const cols = [...new Set(out.flatMap(r => Object.keys(r || {})))];
    const thead = t.createTHead().insertRow(); cols.forEach(c => { const th = document.createElement('th'); th.textContent = c; thead.appendChild(th); });
    const tb = t.createTBody(); out.forEach(r => { const tr = tb.insertRow(); cols.forEach(c => { tr.insertCell().textContent = r && r[c] != null ? r[c] : ''; }); });
    wrap.appendChild(t); el.appendChild(wrap);
  } else if (kind === 'markdown' && window.marked && window.DOMPurify) {
    const d = document.createElement('div'); d.className = 'out-md';
    d.innerHTML = DOMPurify.sanitize(marked.parse(String(out))); el.appendChild(d);
  } else if (kind === 'html') {
    const f = document.createElement('iframe'); f.className = 'out-html'; f.setAttribute('sandbox', '');
    f.srcdoc = String(out); el.appendChild(f);
  } else if (kind === 'json') {
    const p = document.createElement('pre'); p.className = 'out-pre'; p.textContent = JSON.stringify(out, null, 2); el.appendChild(p);
  } else {
    const p = document.createElement('pre'); p.className = 'out-pre'; p.textContent = typeof out === 'string' ? out : JSON.stringify(out, null, 2);
    el.appendChild(p);
  }
};
window.OUTPUT_LABEL = { text: 'text', markdown: 'text', table: 'table', json: 'data', image: 'image', html: 'page' };
