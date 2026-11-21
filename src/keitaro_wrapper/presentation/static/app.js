const form = document.querySelector('#campaign-form');
const submit = document.querySelector('#submit');
const result = document.querySelector('#result');
const retry = document.querySelector('#retry');
let pending = null;
let busy = false;

/** Выполняет JSON-запрос и сохраняет HTTP-код ошибки для формы. */
async function jsonRequest(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((item) => item.msg).join('; ')
      : data.detail;
    const error = new Error(detail || `Ошибка HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

/** Загружает офферы и блокирует создание при ошибке справочников. */
async function loadCatalog() {
  const error = document.querySelector('#catalog-error');
  const reload = document.querySelector('#reload');
  error.hidden = true;
  reload.hidden = true;
  try {
    const data = await jsonRequest('/api/catalog');
    document.querySelector('#domain').textContent = data.domain;
    document.querySelector('#group').textContent = data.group;
    const select = document.querySelector('#offer');
    select.replaceChildren(new Option('Выберите оффер', ''));
    data.offers.forEach((offer) =>
      select.add(new Option(`${offer.id} · ${offer.name}`, offer.id))
    );
    select.disabled = false;
    submit.disabled = busy || pending !== null;
    if (!data.offers.length) throw new Error('В Keitaro нет активных офферов.');
  } catch (reason) {
    error.textContent = reason.message;
    error.hidden = false;
    reload.hidden = false;
    submit.disabled = true;
  }
}

/** Обновляет журнал без вставки пользовательских данных через HTML. */
async function loadHistory() {
  const error = document.querySelector('#history-error');
  try {
    const operations = await jsonRequest('/api/operations');
    const body = document.querySelector('#history');
    body.replaceChildren();
    operations.forEach((operation) => {
      const row = document.createElement('tr');
      const name = document.createElement('td');
      if (operation.status === 'completed' && operation.url) {
        const link = document.createElement('a');
        link.href = operation.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = operation.name;
        name.append(link);
      } else name.textContent = operation.name;
      const parameters = document.createElement('td');
      parameters.textContent = `${operation.country_code} / ${operation.offer_id}`;
      const status = document.createElement('td');
      status.textContent =
        { completed: 'Создана', pending: 'В процессе', failed: 'Ошибка' }[
          operation.status
        ] || operation.status;
      status.className =
        operation.status === 'failed' ? 'status failed' : 'status';
      status.title = operation.error || '';
      const id = document.createElement('td');
      id.textContent = operation.campaign_id ?? '—';
      row.append(name, parameters, status, id);
      body.append(row);
    });
    if (!operations.length) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 4;
      cell.textContent = 'Пока нет операций. Создайте первую кампанию.';
      row.append(cell);
      body.append(row);
    }
    error.hidden = true;
  } catch (reason) {
    error.textContent = `Не удалось загрузить историю: ${reason.message}`;
    error.hidden = false;
  }
}

/** Блокирует изменение команды до получения достоверного результата. */
function freezeForm(frozen) {
  form.querySelectorAll('input, select').forEach((field) => {
    field.disabled = frozen;
  });
  submit.disabled = frozen;
}

/** Отправляет сохранённую команду с прежним ключом при повторе. */
async function sendPending() {
  busy = true;
  retry.hidden = true;
  freezeForm(true);
  result.hidden = false;
  result.textContent = 'Создаём кампанию и настраиваем потоки…';
  try {
    const operation = await jsonRequest('/api/campaigns', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(pending),
    });
    if (operation.status === 'pending') {
      result.textContent =
        'Операция ещё выполняется. Проверьте результат через несколько секунд. Если процесс был прерван, проверьте кампанию в Keitaro.';
      retry.hidden = false;
    } else {
      result.replaceChildren();
      if (operation.status === 'completed') {
        const link = document.createElement('a');
        link.href = operation.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = operation.url;
        result.append(
          `Создана ${operation.name} · ID ${operation.campaign_id}`,
          document.createElement('br'),
          link
        );
        form.reset();
      } else {
        result.textContent = `${operation.error} ID кампании: ${
          operation.campaign_id ?? 'не получен'
        }. Перед новой попыткой проверьте кампанию в Keitaro. Частично созданная кампания обычно остаётся отключённой; при сбое включения её состояние нужно проверить.`;
      }
      pending = null;
      sessionStorage.removeItem('keitaro-pending');
      freezeForm(false);
    }
  } catch (reason) {
    if (reason.status === 422 || reason.status === 409) {
      result.textContent = reason.message;
      pending = null;
      sessionStorage.removeItem('keitaro-pending');
      freezeForm(false);
    } else {
      result.textContent = `${reason.message} Повторная проверка использует тот же ключ и не создаст дубликат.`;
      retry.hidden = false;
    }
  } finally {
    busy = false;
    await loadHistory();
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (busy) return;
  pending = {
    request_id: crypto.randomUUID(),
    name: document.querySelector('#name').value,
    country_code: document.querySelector('#country').value,
    offer_id: Number(document.querySelector('#offer').value),
  };
  sessionStorage.setItem('keitaro-pending', JSON.stringify(pending));
  await sendPending();
});
retry.addEventListener('click', sendPending);
document.querySelector('#reload').addEventListener('click', loadCatalog);
document.querySelector('#refresh').addEventListener('click', loadHistory);
try {
  pending = JSON.parse(sessionStorage.getItem('keitaro-pending'));
} catch {
  sessionStorage.removeItem('keitaro-pending');
}
if (pending) {
  freezeForm(true);
  result.hidden = false;
  result.textContent =
    'Есть незавершённый запрос. Проверьте его результат перед созданием следующей кампании.';
  retry.hidden = false;
}
loadCatalog();
loadHistory();
