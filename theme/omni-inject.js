/* OmniSecretary: внедрение в боковое меню и замена логотипа Open WebUI. */
(function () {
  'use strict';

  var LOGO = '/omni-logo.svg';
  var LINKS = [
    ['/', 'Чат'],
    ['/portal/workspace', 'Главная'],
    ['/portal/calendar', 'Календарь'],
    ['/portal/tasks', 'Задачи'],
    ['/portal/notes', 'Заметки'],
    ['/portal/obsidian', 'Obsidian'],
    ['/portal/obsidian-setup', 'Obsidian: настройка'],
    ['/portal/search', 'Поиск'],
    ['/portal/llm', 'Модели и ключи'],
    ['/#/settings', 'Настройки Open WebUI']
  ];

  function findSidebar() {
    var nodes = Array.prototype.slice.call(document.querySelectorAll(
      'nav, aside, [class*="sidebar"], [class*="Sidebar"], [id*="sidebar"], [aria-label*="Sidebar"]'
    ));
    var visible = nodes.filter(function (el) {
      var r = el.getBoundingClientRect();
      return r.width > 100 && r.height > 200;
    });
    if (!visible.length) return null;
    visible.sort(function (a, b) {
      return b.getBoundingClientRect().height - a.getBoundingClientRect().height;
    });
    return visible[0];
  }

  function buildMenu() {
    var box = document.createElement('div');
    box.className = 'omni-sidebar-menu';
    box.setAttribute('data-omni', '1');
    var html = '<div class="omni-sidebar-title">OmniSecretary</div>';
    LINKS.forEach(function (pair) {
      html += '<a class="omni-sidebar-link" href="' + pair[0] + '">' + pair[1] + '</a>';
    });
    box.innerHTML = html;
    return box;
  }

  function injectMenu() {
    var nav = findSidebar();
    if (!nav || nav.querySelector('.omni-sidebar-menu')) return;
    var menu = buildMenu();
    nav.insertBefore(menu, nav.firstChild);
  }

  function replaceLogo() {
    Array.prototype.slice.call(document.querySelectorAll('img')).forEach(function (img) {
      var src = (img.getAttribute('src') || '') + ' ' + (img.getAttribute('alt') || '');
      if (/logo|open.?webui|favicon/i.test(src)) {
        img.setAttribute('src', LOGO);
        img.setAttribute('alt', 'OmniSecretary');
      }
    });
    Array.prototype.slice.call(document.querySelectorAll('svg')).forEach(function (svg) {
      var p = svg.parentElement;
      if (!p) return;
      var hint = (p.className && p.className.toString()) + ' ' + (p.id || '');
      if (/logo|brand|header/i.test(hint) && svg.width > 16) {
        var img = document.createElement('img');
        img.src = LOGO;
        img.alt = 'OmniSecretary';
        img.className = 'omni-logo-img';
        svg.parentNode.replaceChild(img, svg);
      }
    });
  }

  function apply() {
    injectMenu();
    replaceLogo();
  }

  document.addEventListener('DOMContentLoaded', function () {
    apply();
    setTimeout(apply, 400);
    setTimeout(apply, 1500);
  });

  var observer = new MutationObserver(function () { apply(); });
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
