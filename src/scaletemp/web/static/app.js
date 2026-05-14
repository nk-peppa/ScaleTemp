const zh = {refresh:'刷新页面', tare:'去皮', experiment:'实验数据测算', weightCurve:'实时重量曲线（30s）', rawFilter:'原始 / 滤波数据曲线（30s）', conversion:'实时原始数据-克重转换曲线', sensorStatus:'状态', calibration:'校准', calibrationHelp:'输入当前砝码克重，系统将保存当前稳定原始值作为校准点。', savePoint:'保存校准点'};
const en = {refresh:'Refresh', tare:'Tare', experiment:'Experimental Workflow', weightCurve:'Live weight curve (30s)', rawFilter:'Raw / filtered curve (30s)', conversion:'Raw-to-grams conversion', sensorStatus:'Status', calibration:'Calibration', calibrationHelp:'Enter current mass; the current stable raw value is saved as a calibration point.', savePoint:'Save point'};
let lang = 'zh';

document.getElementById('langBtn').onclick = () => {
  lang = lang === 'zh' ? 'en' : 'zh';
  const dict = lang === 'zh' ? zh : en;
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = dict[el.dataset.i18n]; });
};

const darkGrid = 'rgba(148,163,184,.14)';
const darkTick = '#94a3b8';
const chartOptions = {
  responsive:true,
  animation:false,
  plugins:{legend:{labels:{color:'#cbd5e1'}}},
  scales:{
    x:{ticks:{color:darkTick, maxTicksLimit:8}, grid:{color:darkGrid}},
    y:{ticks:{color:darkTick}, grid:{color:darkGrid}}
  }
};

function makeTimeChart(id, datasets, extraOptions = {}){
  const options = JSON.parse(JSON.stringify(chartOptions));
  Object.assign(options, extraOptions);
  return new Chart(document.getElementById(id), {type:'line', data:{labels:[], datasets}, options});
}

let weightAxisMin = -10;
let weightAxisMax = 300;
const weightChart = makeTimeChart('weightChart', [
  {label:'grams', borderColor:'#5eead4', data:[], pointRadius:0, tension:.25}
]);
weightChart.options.scales.y.min = weightAxisMin;
weightChart.options.scales.y.max = weightAxisMax;
const rawChart = makeTimeChart('rawChart', [
  {label:'raw', borderColor:'#60a5fa', data:[], pointRadius:0},
  {label:'filtered', borderColor:'#a78bfa', data:[], pointRadius:0}
]);
const conversionChart = new Chart(document.getElementById('conversionChart'), {
  type:'line',
  data:{datasets:[
    {label:'calibration curve', borderColor:'#f97316', backgroundColor:'rgba(249,115,22,.10)', data:[], pointRadius:0, tension:.18},
    {label:'current sample', type:'scatter', borderColor:'#5eead4', backgroundColor:'#5eead4', data:[], pointRadius:6, pointHoverRadius:8}
  ]},
  options:{
    responsive:true,
    animation:false,
    parsing:false,
    plugins:{legend:{labels:{color:'#cbd5e1'}}},
    scales:{
      x:{type:'linear', title:{display:true, text:'Raw ADC', color:'#cbd5e1'}, ticks:{color:darkTick}, grid:{color:darkGrid}},
      y:{title:{display:true, text:'Mass (g)', color:'#cbd5e1'}, ticks:{color:darkTick}, grid:{color:darkGrid}}
    }
  }
});

function renderCalibrationCards(points){
  const container = document.getElementById('calibrationCards');
  if (!points || points.length === 0) {
    container.innerHTML = '<span class="empty-card">No calibration points</span>';
    return;
  }
  container.innerHTML = points.map((p, idx) => `
    <div class="calibration-point-card">
      <span>#${idx + 1}</span>
      <strong>${Number(p.grams).toFixed(2)} g</strong>
      <small>Raw ${Math.round(Number(p.raw))}</small>
      <button class="delete-calibration" data-index="${idx}" title="Delete calibration point">×</button>
    </div>`).join('');
}

async function refresh(){
  const data = await fetch('/api/readings').then(r=>r.json());
  const labels = data.t.map(v => new Date(v*1000).toLocaleTimeString());

  weightChart.data.labels = labels;
  weightChart.data.datasets[0].data = data.grams;
  const finiteGrams = data.grams.filter(Number.isFinite);
  if (finiteGrams.length) {
    const minGram = Math.min(...finiteGrams);
    const maxGram = Math.max(...finiteGrams);
    if (minGram < weightAxisMin) weightAxisMin = Math.floor(minGram / 10) * 10;
    if (maxGram > weightAxisMax) weightAxisMax = Math.ceil(maxGram / 10) * 10;
    weightChart.options.scales.y.min = weightAxisMin;
    weightChart.options.scales.y.max = weightAxisMax;
  }
  weightChart.update();

  rawChart.data.labels = labels;
  rawChart.data.datasets[0].data = data.raw;
  rawChart.data.datasets[1].data = data.filtered;
  rawChart.update();

  const curve = data.conversion_curve || {raw:[], grams:[]};
  conversionChart.data.datasets[0].data = curve.raw.map((raw, idx) => ({x: raw, y: curve.grams[idx]}));
  conversionChart.data.datasets[1].data = data.reading ? [{x: data.reading.filtered_raw, y: data.reading.grams}] : [];
  conversionChart.update();

  if (data.current_ip) {
    document.getElementById('deviceInfo').textContent = `HX711 + CZL611N + Orange Pi Zero 3 · IP ${data.current_ip}`;
  }
  document.getElementById('weightNow').textContent = `${data.reading.grams.toFixed(2)} g`;
  document.getElementById('rawNow').textContent = `Raw: ${data.reading.raw_adc} | Filtered: ${data.reading.filtered_raw.toFixed(1)}`;
  const sensor = data.sensor || {};
  document.getElementById('sensorMode').textContent = `Sensor: ${sensor.mode || '--'}${sensor.error ? ' | ' + sensor.error : ''}`;
  const badge = document.getElementById('stableBadge');
  badge.textContent = data.reading.stable ? 'STABLE' : 'UNSTABLE';
  badge.className = data.reading.stable ? 'badge stable' : 'badge unstable';
  renderCalibrationCards(data.calibration_points);
}

setInterval(refresh, 500);
refresh();

document.getElementById('tareBtn').onclick = async()=>{
  await fetch('/api/tare',{method:'POST'});
  refresh();
};

document.getElementById('filterSlider').oninput = async e => {
  const body = new FormData();
  body.append('strength', e.target.value);
  await fetch('/api/filter',{method:'POST', body});
};

async function setFilterWindowLimit(value){
  const clamped = Math.min(Math.max(Number(value) || 0, 0), 10000);
  document.getElementById('filterWindowSlider').value = clamped;
  document.getElementById('filterWindowInput').value = clamped;
  const body = new FormData();
  body.append('limit', clamped);
  await fetch('/api/filter-window',{method:'POST', body});
}

document.getElementById('filterWindowSlider').oninput = e => setFilterWindowLimit(e.target.value);
document.getElementById('filterWindowInput').onchange = e => setFilterWindowLimit(e.target.value);

document.getElementById('calibrationForm').onsubmit = async e => {
  e.preventDefault();
  const body = new FormData(e.target);
  const data = await fetch('/api/calibration-point',{method:'POST', body}).then(r=>r.json());
  renderCalibrationCards(data.calibration_points);
  e.target.reset();
  refresh();
};


document.getElementById('calibrationCards').addEventListener('click', async event => {
  const button = event.target.closest('.delete-calibration');
  if (!button) return;
  const index = button.dataset.index;
  const data = await fetch(`/api/calibration-point/${index}`, {method:'DELETE'}).then(r=>r.json());
  renderCalibrationCards(data.calibration_points);
  refresh();
});
