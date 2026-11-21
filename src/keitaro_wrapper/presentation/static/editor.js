const campaignId = Number(document.body.dataset.campaignId);
let streams = [];
let selectedStream = null;
let busy = false;

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function message(text, error = false) {
  const node = document.querySelector('#editor-message');
  node.textContent = text;
  node.className = error ? 'error' : '';
  node.hidden = !text;
}
async function request(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(
    typeof data.detail === 'string' ? data.detail : 'Проверьте введённые значения.'
  );
  return data;
}
function button(text, action, secondary = true) {
  const node = el('button', text, secondary ? 'secondary' : '');
  node.type = 'button';
  node.addEventListener('click', action);
  return node;
}
function locked(value) {
  busy = value;
  document.querySelectorAll('button, input').forEach(node => { node.disabled = value; });
}
async function mutate(stream, action, extra = {}) {
  if (busy) return false;
  locked(true);
  try {
    const updated = await request('/api/campaigns/' + campaignId + '/streams/' + stream.id, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({revision: stream.revision, action, ...extra}),
    });
    streams = streams.map(item => item.id === updated.id ? updated : item);
    message(action === 'push' ? 'Изменения опубликованы в Keitaro.' :
      action === 'pin' ? 'Настройка фиксации сохранена.' : 'Черновик сохранён.');
    renderStreams();
    return true;
  } catch (error) {
    message(error.message, true);
    try {
      streams = await request('/api/campaigns/' + campaignId + '/streams');
    } catch (_) { /* Черновик остаётся на экране при сетевом сбое. */ }
    return false;
  } finally {
    locked(false);
    // Архивные строки и заблокированные потоки имеют собственные ограничения.
    renderStreams();
  }
}
function renderStreams() {
  const container = document.querySelector('#stream-list');
  container.replaceChildren();
  if (!streams.length) container.append(el('p', 'В этой кампании нет потоков.'));
  for (const stream of streams) {
    const panel = el('section', undefined, 'panel stream' + (stream.dirty ? ' dirty' : ''));
    panel.append(el('h2', stream.baseline.name + ' · #' + stream.id));
    const uncertain = stream.uncertain || stream.publishing;
    const status = uncertain ? 'Результат Push требует сверки с Keitaro.' :
      stream.dirty ? 'Есть неопубликованные изменения' : 'Синхронизировано с Keitaro';
    panel.append(el('p', status, 'stream-status'));
    if (!stream.offers.length) {
      panel.append(el('p', 'Поток без офферов · только просмотр', 'muted'));
      const options = stream.baseline.action_options;
      const target = typeof options === 'string' ? options :
        options?.url || (typeof stream.baseline.action_payload === 'string' ? stream.baseline.action_payload : '');
      if (target) panel.append(el('p', 'Перенаправление: ' + target, 'stream-target'));
      for (const filter of stream.baseline.filters || []) {
        const label = filter.name === 'country' ? 'Страна' : filter.name;
        const mode = filter.mode === 'accept' ? 'разрешить' :
          filter.mode === 'reject' ? 'исключить' : '';
        const values = Array.isArray(filter.payload) ? filter.payload.join(', ') :
          typeof filter.payload === 'object' ? JSON.stringify(filter.payload) : (filter.payload || '');
        panel.append(el('p', label + (mode ? ' · ' + mode : '') + ': ' + values, 'muted'));
      }
      container.append(panel);
      continue;
    }
    const controls = el('div', undefined, 'toolbar');
    if (uncertain) {
      controls.append(button('Сверить после сбоя', () => mutate(stream, 'recover')));
      controls.append(button('Принять состояние Keitaro', () => {
        if (window.confirm('Отбросить неопубликованные изменения этого потока и принять текущий состав из Keitaro? Архив офферов сохранится.')) {
          mutate(stream, 'reset');
        }
      }));
    } else if (stream.dirty) {
      const active = stream.offers.filter(item => !item.removed);
      const total = active.reduce((sum, item) => sum + item.share, 0);
      if (active.length && total === 100) {
        controls.append(button('Push KT', () => mutate(stream, 'push'), false));
      } else {
        panel.append(el('p', 'Публикация недоступна: нужны активные офферы с суммой долей 100%.', 'muted'));
      }
      controls.append(button('Отменить', () => mutate(stream, 'cancel')));
    }
    panel.append(controls);
    const tableWrap = el('div', undefined, 'table-scroll');
    const table = el('table', undefined, 'offers-table');
    const head = el('tr');
    ['Оффер', 'Доля', 'Действия'].forEach(text => head.append(el('th', text)));
    const thead = el('thead'); thead.append(head); table.append(thead);
    const tbody = el('tbody');
    for (const offer of stream.offers) {
      const row = el('tr', undefined, offer.removed ? 'removed' : '');
      row.append(el('td', '#' + offer.id + ' · ' + offer.name));
      const shareCell = el('td');
      if (offer.removed) {
        shareCell.textContent = '0%';
      } else {
        const input = el('input', undefined, 'share-input');
        input.type = 'number'; input.min = '0'; input.max = '100'; input.step = '1';
        input.value = offer.share;
        input.setAttribute('aria-label', 'Доля оффера ' + offer.id);
        input.disabled = busy || uncertain;
        input.addEventListener('change', () => {
          if (input.value.trim() === '' || !Number.isInteger(Number(input.value))) {
            message('Введите целое число от 0 до 100.', true);
            input.value = offer.share;
            return;
          }
          mutate(stream, 'share', {offer_id: offer.id, share: Number(input.value)});
        });
        shareCell.append(input, document.createTextNode('% '));
        const pin = button('', () => mutate(stream, 'pin', {offer_id: offer.id}));
        pin.classList.add('pin');
        const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        icon.setAttribute('viewBox', '0 0 24 24');
        icon.setAttribute('aria-hidden', 'true');
        const shape = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        shape.setAttribute('d', 'M6 3h12M8 3v5l-4 4v2h16v-2l-4-4V3M12 14v8');
        icon.append(shape);
        pin.append(icon);
        pin.setAttribute('aria-pressed', String(offer.pinned));
        pin.setAttribute('aria-label', (offer.pinned ? 'Открепить' : 'Закрепить') + ' долю оффера ' + offer.id);
        pin.title = offer.pinned ? 'Доля закреплена' : 'Закрепить долю';
        pin.disabled = busy || uncertain;
        shareCell.append(pin);
      }
      row.append(shareCell);
      const actions = el('td');
      const control = button(offer.removed ? 'Вернуть' : 'Удалить',
        () => mutate(stream, offer.removed ? 'restore' : 'remove', {offer_id: offer.id}));
      control.disabled = busy || uncertain;
      actions.append(control); row.append(actions); tbody.append(row);
    }
    table.append(tbody); tableWrap.append(table); panel.append(tableWrap);
    const total = stream.offers.filter(item => !item.removed).reduce((sum, item) => sum + item.share, 0);
    panel.append(el('p', 'Итого: ' + total + '% · Закреплённые доли сохраняются при пересчёте.', 'muted'));
    const add = button('Добавить оффер', () => {
      selectedStream = stream.id;
      document.querySelector('#offer-results').replaceChildren();
      document.querySelector('#offer-error').textContent = '';
      document.querySelector('#offer-query').value = '';
      document.querySelector('#offer-dialog').showModal();
      document.querySelector('#offer-query').focus();
    });
    add.disabled = busy || uncertain;
    panel.append(add); container.append(panel);
  }
}
async function loadStreams(refresh = false) {
  if (busy) return;
  locked(true);
  try {
    streams = await request('/api/campaigns/' + campaignId + '/streams?refresh=' + refresh);
    renderStreams();
    message(refresh ? 'Данные обновлены из Keitaro.' : '');
  } catch (error) { message(error.message, true); }
  finally { locked(false); renderStreams(); }
}
async function loadCampaigns(event) {
  event?.preventDefault();
  const params = new URLSearchParams({
    q: document.querySelector('#campaign-query').value,
    all_campaigns: document.querySelector('#all-campaigns').checked,
  });
  const container = document.querySelector('#campaign-list');
  try {
    message('Загрузка кампаний…');
    const campaigns = await request('/api/campaigns?' + params);
    container.replaceChildren();
    for (const item of campaigns) {
      const row = el('div', undefined, 'campaign-row');
      const link = el('a', '#' + item.id + ' · ' + item.name);
      link.href = '/campaigns/' + item.id;
      row.append(link, el('span', item.state || '', 'muted'));
      container.append(row);
    }
    if (!campaigns.length) container.append(el('p', 'Кампании не найдены.'));
    document.querySelector('#filter-note').textContent =
      params.get('all_campaigns') === 'true' ? 'Показаны все доступные кампании.' : 'Показаны test_frolov_*.';
    message('');
  } catch (error) { message(error.message, true); }
}
document.querySelector('#close-dialog').addEventListener('click',
  () => document.querySelector('#offer-dialog').close());
document.querySelector('#offer-search').addEventListener('submit', async event => {
  event.preventDefault();
  const results = document.querySelector('#offer-results');
  const errorNode = document.querySelector('#offer-error');
  errorNode.textContent = 'Поиск…';
  try {
    const offers = await request('/api/offers?q=' + encodeURIComponent(document.querySelector('#offer-query').value));
    results.replaceChildren();
    const stream = streams.find(item => item.id === selectedStream);
    const available = offers.filter(item => !stream.offers.some(offer => offer.id === item.id && !offer.removed));
    for (const offer of available) {
      const row = el('div', undefined, 'offer-result');
      row.append(el('span', '#' + offer.id + ' · ' + offer.name));
      row.append(button('Добавить', async () => {
        if (await mutate(streams.find(item => item.id === selectedStream), 'add', {offer_id: offer.id})) {
          document.querySelector('#offer-dialog').close();
        } else {
          errorNode.textContent = document.querySelector('#editor-message').textContent;
        }
      }, false));
      results.append(row);
    }
    errorNode.textContent = available.length ? 'Показано до 100 совпадений.' : 'Доступные офферы не найдены.';
  } catch (error) { errorNode.textContent = error.message; }
});
if (campaignId) {
  document.querySelector('#page-title').textContent = 'Потоки кампании #' + campaignId;
  document.querySelector('#page-description').textContent =
    'Черновики сохраняются автоматически. Жёлтые потоки ожидают Push KT.';
  document.querySelector('#fetch-streams').addEventListener('click', () => loadStreams(true));
  document.querySelector('#reload-drafts').addEventListener('click', () => loadStreams());
  loadStreams();
} else {
  document.querySelector('#campaign-search').addEventListener('submit', loadCampaigns);
  document.querySelector('#all-campaigns').addEventListener('change', loadCampaigns);
  loadCampaigns();
}




