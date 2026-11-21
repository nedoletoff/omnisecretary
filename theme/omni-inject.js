/* OmniSecretary: надёжное внедрение в Open WebUI (меню и логотип). */
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
    ['/portal/obsidian-setup', 'Obsidian ⚙'],
    ['/portal/search', 'Поиск'],
    ['/portal/llm', 'Модели и ключи'],
    ['/portal/account', 'Настройки портала'],
    ['/#/settings', 'Настройки Open WebUI']
  ];

  function rect(el) { return el.getBoundingClientRect(); }

  function findSidebar() {
    var candidates = [];
    var all = document.querySelectorAll('nav, aside, div, section');
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      var r = rect(el);
      if (r.width < 140 || r.height < 220) continue;
      var text = (el.innerText || '').slice(0, 800);
      var cls = (el.className && el.className.toString()) + ' ' + (el.id || '');
      var score = 0;
      if (/sidebar|side.?bar/i.test(cls)) score += 5;
      if (/new chat|чат|chats/i.test(text)) score += 4;
      if (/calendar|календарь/i.test(text)) score += 2;
      if (/settings|настройки/i.test(text)) score += 1;
      if (score >= 4) candidates.push({ el: el, score: score, height: r.height });
    }
    if (!candidates.length) return null;
    candidates.sort(function (a, b) {
      return (b.score - a.score) || (b.height - a.height);
    });
    return candidates[0].el;
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
    nav.insertBefore(buildMenu(), nav.firstChild);
  }

  function replaceLogo() {
    Array.prototype.slice.call(document.querySelectorAll('img')).forEach(function (img) {
      var hint = (img.getAttribute('src') || '') + ' ' + (img.getAttribute('alt') || '');
      if (/logo|open.?webui|favicon/i.test(hint)) {
        img.setAttribute('src', LOGO);
        img.setAttribute('alt', 'OmniSecretary');
      }
    });

    Array.prototype.slice.call(document.querySelectorAll('svg')).forEach(function (svg) {
      var r = rect(svg);
      if (r.width < 16 || r.height < 16 || r.width > 120 || r.height > 120) return;
      var p = svg.parentElement;
      var hint = '';
      for (var depth = 0; p && depth < 4; depth++, p = p.parentElement) {
        hint += ' ' + ((p.className && p.className.toString()) || '') + ' ' + (p.id || '') + ' ' + (p.getAttribute('aria-label') || '');
      }
      var textHint = (svg.parentElement && svg.parentElement.innerText || '').slice(0, 120);
      if (!/logo|brand|header|sidebar|open.?webui/i.test(hint) && !/open.?webui/i.test(textHint)) return;
      var img = document.createElement('img');
      img.src = LOGO;
      img.alt = 'OmniSecretary';
      img.className = 'omni-logo-img';
      img.style.width = Math.min(32, Math.max(20, r.width)) + 'px';
      img.style.height = Math.min(32, Math.max(20, r.height)) + 'px';
      svg.parentNode.replaceChild(img, svg);
    });
  }

  // Панель нужна только в чате Open WebUI — на страницах входа и портала
  // она мешает и дублирует нативную навигацию.
  function isChatPage() {
    var path = window.location.pathname || '/';
    return path === '/' || path.indexOf('/#') === 0;
  }

  function apply() {
    if (isChatPage()) injectMenu();
    replaceLogo();
  }

  document.addEventListener('DOMContentLoaded', function () {
    apply();
    setTimeout(apply, 300);
    setTimeout(apply, 1200);
    setTimeout(apply, 3000);
  });

  if (document.body) {
    new MutationObserver(function () { apply(); }).observe(document.body, {
      childList: true, subtree: true
    });
  }
})();
