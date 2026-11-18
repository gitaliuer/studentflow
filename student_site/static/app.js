// Только удобства интерфейса. Доступ и права проверяет FastAPI.
document.addEventListener('DOMContentLoaded', () => {
  const menu = document.querySelector('.mobile-menu');
  const sidebar = document.querySelector('.sidebar');
  if (menu && sidebar) {
    menu.addEventListener('click', () => {
      const isOpen = sidebar.classList.toggle('sidebar-open');
      menu.setAttribute('aria-expanded', String(isOpen));
    });
    sidebar.querySelectorAll('a').forEach(a => a.addEventListener('click', () => {
      sidebar.classList.remove('sidebar-open');
      menu.setAttribute('aria-expanded', 'false');
    }));
  }
  document.querySelectorAll('form[data-confirm]').forEach(form => {
    form.addEventListener('submit', event => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });
});
