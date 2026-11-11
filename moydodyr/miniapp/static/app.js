const tg = window.Telegram?.WebApp;
tg?.ready(); tg?.expand();
const form = document.querySelector('#order-form'), error = document.querySelector('#error');
const initData = tg?.initData || '';
let catalog, photos = [], estimateTimer, estimateVersion = 0;
let draftId = sessionStorage.getItem('moydodyr-draft') || crypto.randomUUID();
sessionStorage.setItem('moydodyr-draft', draftId);
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'X-Telegram-Init-Data': initData, ...options.headers}});
  const text = await response.text(); let data;
  try { data = JSON.parse(text); } catch { data = {error: text}; }
  if (!response.ok) throw new Error(data.error || 'Не удалось выполнить запрос.');
  return data;
}
function addChoices(container, items, name, multiple = false) {
  const root = document.querySelector(container);
  Object.entries(items).forEach(([key, value], index) => {
    const label = document.createElement('label'), input = document.createElement('input'), text = document.createElement('span');
    label.className = 'choice'; input.type = multiple ? 'checkbox' : 'radio'; input.name = name; input.value = key;
    if (!multiple && index === 0) input.checked = true;
    text.textContent = typeof value === 'string' ? value : value.title;
    label.append(input, text); root.append(label);
  });
}
function selected() {
  return {object: form.querySelector('[name=object]:checked')?.value,
    service: form.querySelector('[name=service]:checked')?.value,
    extras: [...form.querySelectorAll('[name=extras]:checked')].map(x => x.value), area: form.area.value};
}
async function calculate() {
  const version = ++estimateVersion, output = document.querySelector('#estimate');
  if (!form.area.value.trim()) { output.textContent = 'Укажите площадь'; return; }
  try {
    const result = await api('/api/estimate', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(selected())});
    if (version !== estimateVersion) return;
    output.textContent = result.estimate ? new Intl.NumberFormat('ru-RU').format(result.estimate.total) + ' ₽' : 'Уточнит менеджер';
  } catch (e) { if (version === estimateVersion) output.textContent = e.message; }
}
form.addEventListener('input', e => { if (['object','service','extras','area'].includes(e.target.name)) { clearTimeout(estimateTimer); estimateVersion++; estimateTimer = setTimeout(calculate, 350); } });
const dateInput = form.elements.date;
const today = new Intl.DateTimeFormat('en-CA', {timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
dateInput.min = today; const maxDate = new Date(today + 'T12:00:00Z'); maxDate.setUTCDate(maxDate.getUTCDate() + 365); dateInput.max = maxDate.toISOString().slice(0,10);
document.querySelector('#photos').addEventListener('change', e => {
  photos = [...e.target.files];
  document.querySelector('#photo-list').textContent = photos.length ? photos.map(x => x.name).join(' · ') : '';
});
form.addEventListener('submit', async event => {
  event.preventDefault(); error.textContent = '';
  clearTimeout(estimateTimer);
  const next = document.querySelector('#submit'); next.disabled = true;
  await calculate();
  next.disabled = false;
  if (photos.length > 5) { error.textContent = 'Можно загрузить не более пяти фотографий.'; return; }
  const choice = selected(), details = document.querySelector('#review-details'); details.textContent = '';
  const rows = [['Объект',catalog.objects[choice.object]], ['Уборка',catalog.services[choice.service]?.title],
    ['Площадь',choice.area + ' м²'], ['Дополнительные работы',choice.extras.map(k => catalog.extras[k].title).join(', ') || 'Не выбраны'],
    ['Предварительная стоимость',document.querySelector('#estimate').textContent], ['Адрес',form.address.value],
    ['Желаемые дата и время',form.date.value + ', ' + (form.time.value || 'любое время')],
    ['Имя',form.elements.name.value], ['Телефон',form.phone.value], ['Комментарий',form.comment.value || '—'],
    ['Фотографии',photos.length ? String(photos.length) : 'Нет']];
  rows.forEach(([label, value]) => { const p=document.createElement('p'), strong=document.createElement('strong'); strong.textContent=label; p.append(strong, document.createTextNode(value || '—')); details.append(p); });
  form.hidden=true; document.querySelector('#review').hidden=false; window.scrollTo({top:0,behavior:'smooth'});
});
document.querySelector('#edit').onclick = () => { document.querySelector('#review').hidden=true; form.hidden=false; };
document.querySelector('#confirm').addEventListener('click', async () => {
  const reviewError = document.querySelector('#review-error'); reviewError.textContent = '';
  const submit = document.querySelector('#confirm'); submit.disabled = true;
  try {
    if (photos.length > 5) throw new Error('Можно загрузить не более пяти фотографий.');
    const photoIds = [];
    for (const photo of photos) {
      if (photo.size > 20 * 1024 * 1024) throw new Error('Фотография должна быть не больше 20 МБ.');
      const uploaded = await api('/api/photo', {method:'POST', headers:{'X-Draft-Id':draftId}, body:photo});
      photoIds.push(uploaded.file_id);
    }
    const data = {...selected(), draft_id:draftId, address:form.address.value, date:form.date.value,
      time:form.time.value, name:form.elements.name.value, phone:form.phone.value,
      comment:form.comment.value, photos:photoIds};
    const result = await api('/api/submit', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    sessionStorage.removeItem('moydodyr-draft'); tg?.disableClosingConfirmation?.();
    document.querySelector('#review').hidden = true; document.querySelector('#success').hidden = false;
    document.querySelector('#success-text').textContent = 'Номер вашей заявки: ' + result.order_id;
    window.scrollTo({top:0,behavior:'smooth'});
  } catch (e) { reviewError.textContent = e.message || 'Попробуйте ещё раз.'; submit.disabled = false; }
});
(async () => {
  try {
    if (!initData) throw new Error('Откройте приложение через кнопку в Telegram-боте.');
    catalog = await api('/api/catalog');
    addChoices('#objects', catalog.objects, 'object'); addChoices('#services', catalog.services, 'service');
    addChoices('#extras', catalog.extras, 'extras', true); tg?.enableClosingConfirmation?.();
  } catch (e) { error.textContent = e.message; document.querySelector('#submit').disabled = true; }
})();
