const zh = {refresh:'刷新页面', tare:'去皮', experiment:'实验数据测算', weightCurve:'实时重量曲线', rawFilter:'原始 / 滤波数据曲线', conversion:'实时原始数据-克重转换曲线', sensorStatus:'状态', calibration:'校准', calibrationHelp:'输入当前砝码克重，系统将保存当前稳定原始值作为校准点。', savePoint:'保存校准点'};
const en = {refresh:'Refresh', tare:'Tare', experiment:'Experimental Workflow', weightCurve:'Live weight curve', rawFilter:'Raw / filtered curve', conversion:'Raw-to-grams conversion', sensorStatus:'Status', calibration:'Calibration', calibrationHelp:'Enter current mass; the current stable raw value is saved as a calibration point.', savePoint:'Save point'};
let lang = 'zh';
document.getElementById('langBtn').onclick = () => { lang = lang === 'zh' ? 'en' : 'zh'; const dict = lang === 'zh' ? zh : en; document.querySelectorAll('[data-i18n]').forEach(el => el.textContent = dict[el.dataset.i18n]); };
const chartOptions = {responsive:true, animation:false, plugins:{legend:{labels:{color:'#cbd5e1'}}}, scales:{x:{ticks:{color:'#94a3b8'}, grid:{color:'rgba(148,163,184,.14)'}}, y:{ticks:{color:'#94a3b8'}, grid:{color:'rgba(148,163,184,.14)'}}}};
function makeChart(id, datasets){ return new Chart(document.getElementById(id), {type:'line', data:{labels:[], datasets}, options:chartOptions}); }
const weightChart = makeChart('weightChart', [{label:'grams', borderColor:'#5eead4', data:[], pointRadius:0, tension:.25}]);
const rawChart = makeChart('rawChart', [{label:'raw', borderColor:'#60a5fa', data:[], pointRadius:0}, {label:'filtered', borderColor:'#a78bfa', data:[], pointRadius:0}]);
const conversionChart = makeChart('conversionChart', [{label:'raw→g', borderColor:'#f97316', data:[], pointRadius:0, showLine:false}]);
async function refresh(){
  const data = await fetch('/api/readings').then(r=>r.json());
  const labels = data.t.map(v => new Date(v*1000).toLocaleTimeString());
  weightChart.data.labels = labels; weightChart.data.datasets[0].data = data.grams; weightChart.update();
  rawChart.data.labels = labels; rawChart.data.datasets[0].data = data.raw; rawChart.data.datasets[1].data = data.filtered; rawChart.update();
  conversionChart.data.labels = data.filtered.map(v=>Math.round(v)); conversionChart.data.datasets[0].data = data.grams; conversionChart.update();
  document.getElementById('weightNow').textContent = `${data.reading.grams.toFixed(2)} g`;
  document.getElementById('rawNow').textContent = `Raw: ${data.reading.raw_adc} | Filtered: ${data.reading.filtered_raw.toFixed(1)}`;
  const badge = document.getElementById('stableBadge'); badge.textContent = data.reading.stable ? 'STABLE' : 'UNSTABLE'; badge.className = data.reading.stable ? 'badge stable' : 'badge unstable';
}
setInterval(refresh, 500); refresh();
document.getElementById('tareBtn').onclick = async()=>{ await fetch('/api/tare',{method:'POST'}); refresh(); };
document.getElementById('filterSlider').oninput = async e => { const body = new FormData(); body.append('strength', e.target.value); await fetch('/api/filter',{method:'POST', body}); };
document.getElementById('calibrationForm').onsubmit = async e => { e.preventDefault(); const body = new FormData(e.target); const data = await fetch('/api/calibration-point',{method:'POST', body}).then(r=>r.json()); document.getElementById('calibrationResult').textContent = JSON.stringify(data,null,2); };
