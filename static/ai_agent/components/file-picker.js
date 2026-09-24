/**
 * 文件选择组件：从工具箱工作区挑文件，或上传新文件到工作区。
 *
 * 设计取舍：文件统一以「工作区」为唯一来源 ——
 * 上传的新文件直接进工作区，执行时后端按文件名从工作区复制进临时目录。
 * 这样只有一条链路，也不需要为任务中心另建一套文件存储。
 */
(function () {
  'use strict';

  var API = {
    list: '/toolbox/api/workspace/list',
    upload: '/toolbox/api/workspace/upload',
    remove: '/toolbox/api/workspace/delete',
  };

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function humanSize(n) {
    if (!n && n !== 0) return '';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1024 / 1024).toFixed(1) + ' MB';
  }

  function create(container) {
    var state = { files: [], selected: {} };

    function render() {
      var listHtml = state.files.length
        ? state.files.map(function (f) {
            var on = !!state.selected[f.name];
            return '<label class="fp-item' + (on ? ' is-on' : '') + '">' +
              '<input type="checkbox" data-name="' + esc(f.name) + '"' +
                (on ? ' checked' : '') + '>' +
              '<span class="fp-name">' + esc(f.name) + '</span>' +
              '<span class="fp-size">' + humanSize(f.size) + '</span>' +
            '</label>';
          }).join('')
        : '<div class="fp-empty">工作区还没有文件，点右上角上传</div>';

      container.innerHTML =
        '<div class="fp-head">' +
          '<span class="fp-title">📎 选择输入文件</span>' +
          '<span class="fp-count" data-role="count"></span>' +
          '<button type="button" class="agent-btn tiny" data-act="upload">⬆ 上传文件</button>' +
          '<button type="button" class="agent-btn tiny" data-act="toggleall" data-role="allBtn">☑ 全选</button>' +
          '<button type="button" class="agent-btn tiny" data-act="remove" data-role="removeBtn">🗑 删除所选</button>' +
          '<button type="button" class="agent-btn tiny" data-act="refresh">↻</button>' +
        '</div>' +
        '<div class="fp-list">' + listHtml + '</div>' +
        '<input type="file" multiple style="display:none" data-role="input">';

      updateCount();

      container.querySelector('[data-act="refresh"]')
        .addEventListener('click', function () { load(); });
      container.querySelector('[data-act="upload"]')
        .addEventListener('click', function () {
          container.querySelector('[data-role="input"]').click();
        });
      container.querySelector('[data-act="toggleall"]')
        .addEventListener('click', function () { toggleSelectAll(); });
      container.querySelector('[data-act="remove"]')
        .addEventListener('click', function () { doRemove(); });
      container.querySelector('[data-role="input"]')
        .addEventListener('change', function (e) {
          doUpload(Array.prototype.slice.call(e.target.files || []));
        });

      var boxes = container.querySelectorAll('input[type=checkbox]');
      for (var i = 0; i < boxes.length; i++) {
        boxes[i].addEventListener('change', function (e) {
          var name = e.target.getAttribute('data-name');
          if (e.target.checked) state.selected[name] = true;
          else delete state.selected[name];
          e.target.parentNode.classList.toggle('is-on', e.target.checked);
          updateCount();
        });
      }
    }

    function updateCount() {
      var el = container.querySelector('[data-role="count"]');
      if (el) el.textContent = getSelected().length ? ('已选 ' + getSelected().length + ' 个') : '';
      updateAllBtn();
    }

    /**
     * 全选 / 取消全选切换。
     * 已选中全部文件 → 清空选中（按钮显示「取消全选」）；否则选中全部（按钮显示「全选」）。
     */
    function toggleSelectAll() {
      if (!state.files.length) return;
      var allOn = state.files.every(function (f) { return !!state.selected[f.name]; });
      state.selected = {};
      if (!allOn) {
        state.files.forEach(function (f) { state.selected[f.name] = true; });
      }
      render();
    }

    function updateAllBtn() {
      var btn = container.querySelector('[data-role="allBtn"]');
      if (!btn) return;
      if (!state.files.length) {
        btn.disabled = true;
        btn.textContent = '☑ 全选';
        return;
      }
      var allOn = state.files.every(function (f) { return !!state.selected[f.name]; });
      btn.disabled = false;
      btn.textContent = allOn ? '☐ 取消全选' : '☑ 全选';
    }

    function load() {
      fetch(API.list, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          state.files = ((j.data || {}).files) || [];
          // 清掉已不存在的选中项
          Object.keys(state.selected).forEach(function (n) {
            if (!state.files.some(function (f) { return f.name === n; })) delete state.selected[n];
          });
          render();
        })
        .catch(function () { render(); });
    }

    function doUpload(files) {
      if (!files.length) return;
      var fd = new FormData();
      files.forEach(function (f) { fd.append('files', f); });
      var btn = container.querySelector('[data-act="upload"]');
      if (btn) { btn.disabled = true; btn.textContent = '上传中…'; }
      fetch(API.upload, {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        body: fd,
      })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (j.code !== 200) { alert((j && j.msg) || '上传失败'); return; }
          ((j.data || {}).files || []).forEach(function (n) { state.selected[n] = true; });
          load();
        })
        .catch(function (e) { alert('上传失败：' + e.message); })
        .finally(function () {
          if (btn) { btn.disabled = false; btn.textContent = '⬆ 上传文件'; }
        });
    }

    /**
     * 删除勾选的工作区文件。
     *
     * 后端只有单文件删除接口（body: {name}），因此逐个并发删除再统一刷新。
     * 删除的是工作区里的持久文件，不可恢复，故先确认。
     */
    function doRemove() {
      var names = getSelected();
      if (!names.length) {
        alert('请先勾选要删除的文件');
        return;
      }
      if (!window.confirm('确定从工作区删除选中的 ' + names.length + ' 个文件？删除后不可恢复。')) return;

      var btn = container.querySelector('[data-act="remove"]');
      if (btn) { btn.disabled = true; btn.textContent = '删除中…'; }

      Promise.all(names.map(function (name) {
        return fetch(API.remove, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
          body: JSON.stringify({ name: name })
        }).then(function (r) { return r.json(); });
      }))
        .then(function (list) {
          var bad = list.filter(function (j) { return j && j.code !== 200; });
          if (bad.length) {
            alert('部分文件删除失败：' + (bad[0].msg || '未知错误'));
          }
          state.selected = {};
          load();
        })
        .catch(function (e) { alert('删除失败：' + (e.message || e)); })
        .finally(function () {
          if (btn) { btn.disabled = false; btn.textContent = '🗑 删除所选'; }
        });
    }

    function getSelected() {
      return Object.keys(state.selected);
    }

    load();

    return { getSelected: getSelected, refresh: load };
  }

  window.AgentFilePicker = { create: create };
})();
