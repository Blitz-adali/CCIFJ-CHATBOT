/*!
 * CCIFJ Concierge embed script.
 *
 * Adds a floating "chat" button to any page (TYPO3 included) and opens the
 * concierge in a panel. One line in the page template is enough:
 *
 *   <script src="https://concierge.example.org/embed.js" defer></script>
 *
 * Optional attributes on the script tag:
 *   data-lang="fr"            en, fr or ja. Default: the page's <html lang>, else the browser language.
 *   data-position="left"      Put the button on the left. Default: right.
 *   data-label="Ask CCIFJ"    Accessible name of the button.
 *   data-open="true"          Open the panel when the page loads.
 *
 * The chat itself runs in an iframe served from the same place as this file,
 * so the host page needs no other change and shares no cookies or data with it.
 */
(function () {
  'use strict';
  if (window.__ccifjConcierge) return;
  var script = document.currentScript;
  if (!script || !script.src) return;

  var base = new URL('.', script.src);
  var supported = ['en', 'fr', 'ja'];
  var labels = {
    en: { open: 'Chat with the CCIFJ concierge', close: 'Close the chat', title: 'CCIFJ Concierge' },
    fr: { open: 'Discuter avec le concierge CCIFJ', close: 'Fermer le chat', title: 'Concierge CCIFJ' },
    ja: { open: 'CCIFJコンシェルジュに相談', close: 'チャットを閉じる', title: 'CCIFJ コンシェルジュ' }
  };

  function pickLanguage() {
    var candidates = [script.getAttribute('data-lang'), document.documentElement.lang, navigator.language];
    for (var i = 0; i < candidates.length; i += 1) {
      var code = String(candidates[i] || '').toLowerCase().split(/[-_]/)[0];
      if (code === 'jp') code = 'ja';
      if (supported.indexOf(code) !== -1) return code;
    }
    return 'en';
  }

  var lang = pickLanguage();
  var text = labels[lang];
  var side = script.getAttribute('data-position') === 'left' ? 'left' : 'right';
  var frame = null;
  var open = false;

  var style = document.createElement('style');
  style.textContent =
    '.ccifj-launcher{position:fixed;bottom:20px;' + side + ':20px;z-index:2147483000;width:60px;height:60px;border:0;border-radius:50%;' +
    'background:#073f69;color:#fff;cursor:pointer;box-shadow:0 8px 24px rgba(7,63,105,.35);display:grid;place-items:center;transition:transform .15s}' +
    '.ccifj-launcher:hover{transform:translateY(-2px)}' +
    '.ccifj-launcher:focus-visible{outline:3px solid #008dc3;outline-offset:3px}' +
    '.ccifj-panel{position:fixed;bottom:92px;' + side + ':20px;z-index:2147483000;width:420px;height:min(680px,calc(100vh - 112px));' +
    'max-width:calc(100vw - 24px);border:1px solid #dcdedb;border-radius:18px;overflow:hidden;background:#f2eee8;' +
    'box-shadow:0 18px 48px rgba(24,55,75,.28);display:none}' +
    '.ccifj-panel.is-open{display:block}' +
    '.ccifj-panel iframe{width:100%;height:100%;border:0;display:block}' +
    '@media (max-width:560px){.ccifj-panel{inset:0;width:100%;height:100%;max-width:none;border:0;border-radius:0}' +
    '.ccifj-panel.is-open~.ccifj-launcher,.ccifj-launcher.is-open{bottom:auto;top:10px;' + side + ':10px;width:44px;height:44px}}';

  var panel = document.createElement('div');
  panel.className = 'ccifj-panel';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', text.title);

  var button = document.createElement('button');
  button.type = 'button';
  button.className = 'ccifj-launcher';
  var chatIcon = '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/></svg>';
  var closeIcon = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';

  function render() {
    button.innerHTML = open ? closeIcon : chatIcon;
    button.setAttribute('aria-label', script.getAttribute('data-label') && !open ? script.getAttribute('data-label') : open ? text.close : text.open);
    button.setAttribute('aria-expanded', String(open));
    button.classList.toggle('is-open', open);
    panel.classList.toggle('is-open', open);
  }

  function setOpen(next) {
    open = next;
    if (open && !frame) {
      // The chat loads only when first opened, so it costs the host page nothing until then.
      frame = document.createElement('iframe');
      frame.title = text.title;
      frame.allow = 'clipboard-write';
      frame.src = new URL('?lang=' + lang + '&embed=1', base).href;
      frame.addEventListener('load', function () {
        if (open && frame.contentWindow) frame.contentWindow.postMessage({ type: 'ccifj:focus' }, base.origin);
      });
      panel.appendChild(frame);
    } else if (open && frame && frame.contentWindow) {
      frame.contentWindow.postMessage({ type: 'ccifj:focus' }, base.origin);
    }
    render();
    if (!open) button.focus();
  }

  button.addEventListener('click', function () { setOpen(!open); });
  document.addEventListener('keydown', function (event) { if (event.key === 'Escape' && open) setOpen(false); });

  function mount() {
    document.head.appendChild(style);
    document.body.appendChild(panel);
    document.body.appendChild(button);
    render();
    if (script.getAttribute('data-open') === 'true') setOpen(true);
  }
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount);

  window.__ccifjConcierge = { open: function () { setOpen(true); }, close: function () { setOpen(false); }, toggle: function () { setOpen(!open); } };
})();
