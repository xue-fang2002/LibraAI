/* AI 出图面板（独立通道，不耦合 ECharts 手动工具） */
(function () {
  'use strict';

  var dock = document.getElementById('aiDock');
  var mask = document.getElementById('aiMask');
  var btn = document.getElementById('aiChartBtn');
  var closeBtn = document.getElementById('aiClose');
  var textEl = document.getElementById('aiText');
  var genBtn = document.getElementById('aiGen');
  var statusEl = document.getElementById('aiStatus');
  var specEl = document.getElementById('aiSpec');
  var imgWrap = document.getElementById('aiImgWrap');
  var imgEl = document.getElementById('aiImg');
  var dlEl = document.getElementById('aiDl');

  if (!dock || !btn) return;

  function openDock() {
    dock.classList.add('open');
    dock.setAttribute('aria-hidden', 'false');
    if (mask) mask.style.display = 'block';
  }
  function closeDock() {
    dock.classList.remove('open');
    dock.setAttribute('aria-hidden', 'true');
    if (mask) mask.style.display = 'none';
  }

  btn.addEventListener('click', openDock);
  if (closeBtn) closeBtn.addEventListener('click', closeDock);
  if (mask) mask.addEventListener('click', closeDock);

  // 读取当前主题与明暗，作为出图参数
  function currentTheme() {
    return document.body.getAttribute('data-light-theme') || 'quantum';
  }
  function isDark() {
    return document.body.classList.contains('dark');
  }

  function setStatus(msg, kind) {
    if (!statusEl) return;
    statusEl.textContent = msg || '';
    statusEl.className = 'ct-ai-status' + (kind ? ' ct-ai-status-' + kind : '');
  }

  function showSpec(spec) {
    if (!specEl) return;
    specEl.style.display = 'block';
    specEl.innerHTML = '';
    var head = document.createElement('div');
    head.className = 'ct-ai-spec-head';
    head.textContent = '解析结果（' + (spec.type || 'bar') + (spec.title ? ' · ' + spec.title : '') + '）';
    specEl.appendChild(head);
    var pre = document.createElement('pre');
    pre.className = 'ct-ai-spec-json';
    pre.textContent = JSON.stringify(spec, null, 2);
    specEl.appendChild(pre);
  }

  function showImage(blob) {
    if (!imgWrap || !imgEl || !dlEl) return;
    var url = URL.createObjectURL(blob);
    if (imgEl._url) URL.revokeObjectURL(imgEl._url);
    imgEl._url = url;
    imgEl.src = url;
    dlEl.href = url;
    imgWrap.style.display = 'flex';
  }

  function renderSpec(spec) {
    setStatus('正在出图…', '');
    fetch('/chart/ai/render', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify({ spec: spec, theme: currentTheme(), dark: isDark() })
    })
      .then(function (r) {
        if (!r.ok) return r.json().then(function (j) { throw new Error((j && j.msg) || '出图失败'); });
        return r.blob();
      })
      .then(function (blob) {
        showImage(blob);
        setStatus('出图完成 ✓', 'ok');
      })
      .catch(function (e) {
        setStatus('出图失败：' + (e.message || e), 'err');
      });
  }

  genBtn.addEventListener('click', function () {
    var text = (textEl.value || '').trim();
    if (!text) {
      setStatus('请先输入图表描述', 'err');
      return;
    }
    setStatus('AI 解析中…', '');
    if (specEl) specEl.style.display = 'none';
    if (imgWrap) imgWrap.style.display = 'none';

    fetch('/chart/ai/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify({ text: text, theme: currentTheme() })
    })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j || j.code !== 200 || !j.data || !j.data.spec) {
          setStatus('解析失败：' + ((j && j.msg) || '未知错误'), 'err');
          return;
        }
        var spec = j.data.spec;
        showSpec(spec);
        renderSpec(spec);
      })
      .catch(function (e) {
        setStatus('请求失败：' + (e.message || e), 'err');
      });
  });

  // 回车（Ctrl/Cmd+Enter）快捷生成
  textEl.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      genBtn.click();
    }
  });
})();
