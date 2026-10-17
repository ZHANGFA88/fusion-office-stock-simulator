(() => {
  'use strict';
  if (document.getElementById('cue-back-to-top')) return;

  const style = document.createElement('style');
  style.id = 'cue-back-to-top-style';
  style.textContent = `
    #cue-back-to-top {
      position: fixed;
      inset-inline-end: calc(16px + env(safe-area-inset-right, 0px));
      bottom: calc(76px + env(safe-area-inset-bottom, 0px));
      z-index: 80;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 7px;
      min-width: 44px;
      min-height: 44px;
      max-width: calc(100vw - 24px);
      padding: 10px 14px;
      border: 1px solid rgba(190, 149, 207, .7);
      border-radius: 999px;
      background: rgba(28, 25, 35, .96);
      color: #fff;
      box-shadow: 0 4px 18px rgba(0, 0, 0, .34);
      font: 600 13px/1.2 system-ui, -apple-system, "Segoe UI", sans-serif;
      cursor: pointer;
      opacity: 0;
      visibility: hidden;
      transform: translateY(8px);
      transition: opacity .18s ease, transform .18s ease, visibility .18s;
      -webkit-tap-highlight-color: transparent;
    }
    #cue-back-to-top[hidden] { display: none !important; }
    #cue-back-to-top[data-visible="true"] {
      opacity: 1;
      visibility: visible;
      transform: translateY(0);
    }
    #cue-back-to-top:hover { background: #352d40; border-color: #e4c2f0; }
    #cue-back-to-top:focus-visible { outline: 3px solid #9fddff; outline-offset: 3px; }
    @media (max-width: 520px) {
      #cue-back-to-top {
        inset-inline-end: calc(12px + env(safe-area-inset-right, 0px));
        bottom: calc(82px + env(safe-area-inset-bottom, 0px));
        padding: 11px 13px;
      }
    }
    @media (prefers-reduced-motion: reduce) {
      #cue-back-to-top { transition: none; }
    }
  `;
  document.head.appendChild(style);

  const isEnglish = (() => {
    const lang = (document.documentElement.lang || '').toLowerCase();
    if (lang.startsWith('en')) return true;
    try {
      if (new URLSearchParams(location.search).get('lang') === 'en') return true;
      return localStorage.getItem('office_lang') === 'en';
    } catch (_) {
      return false;
    }
  })();
  const label = isEnglish ? 'Back to top' : '返回顶部';
  const button = document.createElement('button');
  button.type = 'button';
  button.id = 'cue-back-to-top';
  button.hidden = true;
  button.setAttribute('aria-label', label);
  button.title = label;
  const arrow = document.createElement('span');
  arrow.setAttribute('aria-hidden', 'true');
  arrow.textContent = '↑';
  const text = document.createElement('span');
  text.textContent = label;
  button.append(arrow, text);
  document.body.appendChild(button);

  const reducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  button.addEventListener('click', () => {
    window.scrollTo({ top: 0, behavior: reducedMotion ? 'auto' : 'smooth' });
  });

  let framePending = false;
  const updateVisibility = () => {
    const y = window.scrollY || document.documentElement.scrollTop || 0;
    const visible = y > 320;
    button.hidden = !visible;
    button.dataset.visible = visible ? 'true' : 'false';
    framePending = false;
  };
  window.addEventListener('scroll', () => {
    if (framePending) return;
    framePending = true;
    window.requestAnimationFrame(updateVisibility);
  }, { passive: true });
  updateVisibility();
})();
