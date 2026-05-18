let selected = null;
let sessionId = null;
let currentSteps = [];
let currentIndex = 0;

const configs = {
  calibration: {
    title: 'Calibration / 校准',
    fields: [
      ['duration_s', '每个校准点采集时长 / Duration per point (s)', 'number', '3'],
      ['masses', '校准质量列表 / Masses (g)', 'text', '0,100,200,300,500,1000'],
    ],
  },
  filtering: {
    title: 'Filtering Test / 滤波测试',
    fields: [
      ['duration_s', '稳定载荷采集时长 / Stable-load duration (s)', 'number', '10'],
    ],
  },
  dynamic: {
    title: 'Dynamic Response / 动态响应',
    fields: [
      ['duration_s', '每个动态阶段采集时长 / Duration per phase (s)', 'number', '5'],
    ],
  },
  repeatability: {
    title: 'Repeatability / 重复性',
    fields: [
      ['duration_s', '每次试验采集时长 / Duration per trial (s)', 'number', '2'],
      ['trials', '重复次数 / Trials', 'number', '5'],
    ],
  },
  drift: {
    title: 'Creep/Drift / 蠕变漂移',
    fields: [
      ['duration_s', '漂移采集时长 / Drift duration (s)', 'number', '600'],
    ],
  },
  auto_zero: {
    title: 'Auto-zero / 自动回零',
    fields: [
      ['duration_s', '每阶段采集时长 / Duration per phase (s)', 'number', '6'],
      ['load_mass', '自定义负载重量 / Custom load mass (g)', 'number', '500'],
    ],
  },
};

function renderFields(name){
  const config = configs[name];
  document.getElementById('dynamicFields').innerHTML = config.fields.map(([id, label, type, value]) => `
    <label>${label}<input name="${id}" type="${type}" min="1" step="1" value="${value}" /></label>
  `).join('');
}

function renderSteps(){
  const list = document.getElementById('stepList');
  list.innerHTML = currentSteps.map((step, idx) => `<div class="step-item ${idx === currentIndex ? 'active' : ''}">${idx + 1}. ${step.label}</div>`).join('');
}

document.querySelectorAll('[data-exp]').forEach(btn => btn.onclick = () => {
  document.querySelectorAll('[data-exp]').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  selected = btn.dataset.exp;
  sessionId = null;
  currentSteps = [];
  currentIndex = 0;
  document.getElementById('expTitle').textContent = configs[selected].title;
  document.getElementById('expPrompt').textContent = btn.dataset.prompt;
  document.getElementById('result').innerHTML = '';
  document.getElementById('countdown').textContent = '';
  document.getElementById('captureStep').style.display = 'none';
  renderFields(selected);
  renderSteps();
});

async function countdown(seconds = 3){
  const el = document.getElementById('countdown');
  for (let n = Math.max(1, Math.round(seconds)); n > 0; n--) {
    el.textContent = `采集中 / Collecting: ${n}s`;
    await new Promise(r=>setTimeout(r,1000));
  }
  el.textContent = '采集完成，数据处理中... / Collection complete, processing data...';
}

function renderResult(result){
  const figEntries = result.figures.flatMap(f=>Object.values(f));
  const links = figEntries.map(p => `<a href="/download?path=${encodeURIComponent(p)}">下载 ${p.split('/').pop()}</a>`);
  const pngFiles = figEntries.filter(p => p.endsWith('.png'));
  const pdfFiles = figEntries.filter(p => p.endsWith('.pdf'));
  const bundleQuery = files => files.map(p => `paths=${encodeURIComponent(p)}`).join('&');
  const previews = pngFiles.map(p => `
    <div class="figure-preview"><img class="preview-image" data-full="/download?path=${encodeURIComponent(p)}" src="/download?path=${encodeURIComponent(p)}" alt="${p.split('/').pop()}" /><a href="/download?path=${encodeURIComponent(p)}">${p.split('/').pop()}</a></div>
  `).join('');
  document.getElementById('result').innerHTML = `
    <h3>Completed: ${result.name}</h3>
    <p>Raw CSV: <a href="/download?path=${encodeURIComponent(result.raw_csv)}">download</a></p>
    <p>Processed CSV: <a href="/download?path=${encodeURIComponent(result.processed_csv)}">download</a></p>
    <div class="bundle-actions">
      ${pngFiles.length ? `<a class="button primary" href="/download-bundle?kind=png&${bundleQuery(pngFiles)}">一键下载 PNG</a>` : ''}
      ${pdfFiles.length ? `<a class="button" href="/download-bundle?kind=pdf&${bundleQuery(pdfFiles)}">一键下载 PDF</a>` : ''}
    </div>
    <div>${links.join('')}</div>
    <div class="figure-previews">${previews}</div>
    <pre>${JSON.stringify(result.metadata,null,2)}</pre>`;
}

document.getElementById('expForm').onsubmit = async e => {
  e.preventDefault();
  if(!selected){ alert('请选择实验'); return; }
  const body = new FormData(e.target);
  body.append('name', selected);
  const session = await fetch('/api/experiment-session/start', {method:'POST', body}).then(r=>r.json());
  sessionId = session.session_id;
  currentSteps = session.steps;
  currentIndex = 0;
  document.getElementById('result').innerHTML = '';
  document.getElementById('captureStep').style.display = 'inline-flex';
  renderSteps();
};

document.getElementById('captureStep').onclick = async () => {
  if (!sessionId) return;
  const duration = Number(new FormData(document.getElementById('expForm')).get('duration_s')) || 3;
  const request = fetch(`/api/experiment-session/${sessionId}/capture`, {method:'POST'}).then(r=>r.json());
  await countdown(duration);
  const payload = await request;
  if (payload.error) { document.getElementById('result').textContent = payload.error; return; }
  if (payload.done) {
    document.getElementById('countdown').textContent = 'Done';
    document.getElementById('captureStep').style.display = 'none';
    currentIndex = currentSteps.length;
    renderSteps();
    renderResult(payload.result);
  } else {
    currentIndex = payload.current_index;
    document.getElementById('countdown').textContent = `Step saved (${payload.samples} samples). Next: ${payload.next_step.label}`;
    renderSteps();
  }
};


const modal = document.getElementById('imageModal');
const modalImage = document.getElementById('modalImage');
document.getElementById('result').addEventListener('click', event => {
  const img = event.target.closest('.preview-image');
  if (!img) return;
  modalImage.src = img.dataset.full;
  modal.classList.add('open');
});
modal.addEventListener('click', () => modal.classList.remove('open'));
