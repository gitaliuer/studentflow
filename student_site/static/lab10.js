/* Лабораторная №10: массив -> DOM, форма -> JSON -> localStorage -> другая страница.
 * Никакие данные пользователей / оценки / пароли из серверного HTML не читаются.
 * Один общий учебный план на origin браузера; это не механизм авторизации.
 */
(() => {
  'use strict';
  const STORAGE_KEY = 'studentflow.lab10.plan.v1';
  // Задание 3: карточки и <option> строятся из массива, а не прописаны в HTML.
  const COURSES = Object.freeze([
    { id: 'html', title: 'HTML құрылымы', category: 'Веб-негіздер', description: 'Семантикалық тегтер, бет құрылымы және қолжетімділік.', hours: 2 },
    { id: 'css', title: 'CSS және бейімделу', category: 'Интерфейс', description: 'Торлар, стильдер және мобильді экрандарға бейімдеу.', hours: 3 },
    { id: 'javascript', title: 'JavaScript оқиғалары', category: 'Клиенттік логика', description: 'Форма мәндерін оқу, submit және input оқиғалары.', hours: 4 },
    { id: 'dom', title: 'DOM және массивтер', category: 'Клиенттік логика', description: 'Массивтен элементтер құру және қауіпсіз мәтін шығару.', hours: 3 },
    { id: 'storage', title: 'JSON және localStorage', category: 'Деректер', description: 'Беттер арасында оқу деректерін сақтау және қайта оқу.', hours: 2 },
    { id: 'testing', title: 'UI тестілеу', category: 'Сапаны тексеру', description: 'Сценарийлер, қателерді тексеру және скриншоттар.', hours: 2 },
  ]);
  const PRIORITIES = Object.freeze({ low: 'Төмен', normal: 'Қалыпты', high: 'Жоғары' });
  const findCourse = id => COURSES.find(course => course.id === id);

  function node(tag, text, className) {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text; // Не используем innerHTML.
    if (className) result.className = className;
    return result;
  }

  function validItem(item) {
    return item !== null && typeof item === 'object' && !Array.isArray(item)
      && Object.keys(item).sort().join(',') === 'courseId,hours,priority'
      && typeof item.courseId === 'string' && Boolean(findCourse(item.courseId))
      && Number.isInteger(item.hours) && item.hours >= 1 && item.hours <= 40
      && typeof item.priority === 'string' && Object.hasOwn(PRIORITIES, item.priority);
  }

  // Задание 4: прочитать JSON, проверить схему, не доверять содержимому браузера.
  function readPlan() {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw === null) return { items: [], exists: false };
      if (raw.length > 4096) throw new Error('Too much data');
      const data = JSON.parse(raw);
      if (!data || Object.keys(data).sort().join(',') !== 'items,version'
          || data.version !== 1 || !Array.isArray(data.items)
          || data.items.length > COURSES.length || !data.items.every(validItem)
          || new Set(data.items.map(item => item.courseId)).size !== data.items.length) {
        throw new Error('Invalid schema');
      }
      return { items: data.items, exists: true };
    } catch (error) {
      const blocked = error.name === 'SecurityError';
      return { items: [], exists: !blocked, error: blocked
        ? 'Браузер localStorage-қа кіруге тыйым салды. Сайт үшін сақтауға рұқсат беріп, бетті жаңартыңыз.'
        : 'Сақталған оқу жоспары оқылмады немесе пішімі дұрыс емес. «Сақталған жоспар» бетінде тек оқу жоспарын тазалап, қайта бастаңыз.' };
    }
  }

  function status(message, isError = false) {
    const box = document.getElementById('lab-status');
    box.textContent = message;
    box.className = message ? `lab-status alert ${isError ? 'alert-error' : 'alert-success'}` : 'lab-status';
  }

  function initEditor() {
    const catalog = document.getElementById('course-catalog');
    const form = document.getElementById('plan-form');
    const select = document.getElementById('course-id');
    const hours = document.getElementById('plan-hours');
    const priority = document.getElementById('plan-priority');
    const save = document.getElementById('save-plan');
    const summary = document.getElementById('editor-summary');
    const preview = document.getElementById('form-preview');
    const refreshSummary = () => {
      const saved = readPlan();
      save.disabled = Boolean(saved.error);
      summary.textContent = saved.error ? 'Жоспарды оқу қатесі. Алдымен сақталған деректерді тексеріңіз.'
        : `Сақталған тақырыптар: ${saved.items.length}. Жалпы: ${saved.items.reduce((sum, item) => sum + item.hours, 0)} сағат.`;
      if (saved.error) status(saved.error, true);
      return saved;
    };
    const previewForm = () => {
      const course = findCourse(select.value);
      preview.textContent = course
        ? `${course.title} · ${hours.value || '—'} сағат · ${PRIORITIES[priority.value] || '—'}`
        : 'Тақырыпты таңдаңыз.';
    };
    COURSES.forEach(course => {
      const option = node('option', course.title);
      option.value = course.id;
      select.append(option);
      const card = node('article', undefined, 'panel lab-course');
      card.append(node('span', course.category, 'lab-tag'), node('h3', course.title), node('p', course.description));
      const footer = node('div', undefined, 'lab-course-footer');
      const choose = node('button', 'Таңдау', 'btn btn-secondary');
      choose.type = 'button';
      choose.setAttribute('aria-label', `${course.title}: таңдау`);
      choose.addEventListener('click', () => {
        select.value = course.id;
        hours.value = String(course.hours);
        previewForm();
        hours.focus();
      });
      footer.append(node('small', `${course.hours} сағат ұсынылады`), choose);
      card.append(footer);
      catalog.append(card);
    });
    document.getElementById('catalog-count').textContent = `${COURSES.length} тақырып`;
    form.addEventListener('input', previewForm);
    form.addEventListener('change', previewForm);
    // Задание 2: перехват submit без перезагрузки, чтение значений HTML-формы.
    form.addEventListener('submit', event => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      const values = new FormData(form);
      const item = { courseId: String(values.get('courseId')), hours: Number(values.get('hours')), priority: String(values.get('priority')) };
      if (!validItem(item)) { status('Тақырыпты, 1–40 бүтін сағатты және басымдықты дұрыс таңдаңыз.', true); return; }
      // Читаем заново перед изменением, чтобы учесть последнюю запись другой вкладки.
      const saved = refreshSummary();
      if (saved.error) return;
      const index = saved.items.findIndex(existing => existing.courseId === item.courseId);
      const items = saved.items.slice();
      if (index < 0) items.push(item); else items[index] = item;
      try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, items }));
        refreshSummary();
        status(`${findCourse(item.courseId).title}: ${item.hours} сағат сақталды. Нәтижені «Сақталған жоспар» бетінен ашыңыз.`);
      } catch (error) {
        status('Жоспар сақталмады: браузер жады толы немесе сақтау бұғатталған. Дерек өзгертілмеді.', true);
      }
    });
    refreshSummary();
    window.addEventListener('pageshow', () => { status(''); refreshSummary(); });
    window.addEventListener('storage', event => {
      if (event.key === STORAGE_KEY || event.key === null) { status('Жоспар басқа қойындыда өзгертілді.'); refreshSummary(); }
    });
  }

  function initSaved() {
    const rows = document.getElementById('plan-rows');
    const clear = document.getElementById('clear-plan');
    const render = () => {
      const saved = readPlan(); // Другая HTML-страница: getItem -> parse -> DOM.
      rows.replaceChildren();
      status(saved.error || '', Boolean(saved.error));
      document.getElementById('plan-empty').hidden = Boolean(saved.error) || saved.items.length > 0;
      clear.disabled = !saved.exists;
      document.getElementById('plan-count').textContent = saved.error ? '—' : String(saved.items.length);
      document.getElementById('plan-total').textContent = saved.error ? '—' : String(saved.items.reduce((sum, item) => sum + item.hours, 0));
      document.getElementById('plan-high').textContent = saved.error ? '—' : String(saved.items.filter(item => item.priority === 'high').length);
      saved.items.forEach(item => {
        const course = findCourse(item.courseId);
        const row = node('tr');
        row.append(node('td', course.title), node('td', course.category), node('td', String(item.hours)), node('td', PRIORITIES[item.priority]));
        rows.append(row);
      });
    };
    document.getElementById('reload-plan').addEventListener('click', render);
    clear.addEventListener('click', () => {
      if (!window.confirm('Осы браузердегі оқу жоспарын тазарту керек пе? Бұл әрекетті қайтару мүмкін емес. SQLite және басқа деректер өзгермейді.')) return;
      try {
        window.localStorage.removeItem(STORAGE_KEY); // Не localStorage.clear().
        render();
        status('Оқу жоспары тазартылды.');
      } catch (error) {
        status('Жоспар тазартылмады: браузер сақтауға рұқсат бермеді.', true);
      }
    });
    window.addEventListener('pageshow', render);
    window.addEventListener('storage', event => { if (event.key === STORAGE_KEY || event.key === null) render(); });
    render();
  }

  document.addEventListener('DOMContentLoaded', () => {
    const page = document.querySelector('[data-lab-page]');
    if (!page) return;
    if (page.dataset.labPage === 'editor') initEditor();
    if (page.dataset.labPage === 'saved') initSaved();
  });
})();
