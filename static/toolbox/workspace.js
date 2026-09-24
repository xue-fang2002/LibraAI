/**
 * 工作区面板（Phase 2 工作区上传）
 * 持久文件池：上传一次，所有工具都能取用。
 * 取用方式：服务端文件 -> fetch blob -> File 对象 -> 交给当前工具的上传链路（零侵入）。
 */
(function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function fmtSize(bytes) {
    if (!bytes && bytes !== 0) return '-';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1024 / 1024).toFixed(1) + ' MB';
  }

  function fmtTime(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  const WS = {
    files: [],
    selected: {},
    onUse: null,
    modal: null,
    built: false,

    // ---------- 构建弹窗 ----------
    ensure() {
      if (this.built) return;
      const mask = document.createElement('div');
      mask.className = 'ws-mask';
      mask.id = 'wsMask';
      mask.style.display = 'none';
      mask.innerHTML = `
        <div class="ws-modal">
          <div class="ws-head">
            <span class="ws-title">📂 工作区</span>
            <button type="button" class="ws-x" id="wsClose" title="关闭">×</button>
          </div>
          <p class="ws-hint">上传一次，所有工具都能直接取用</p>
          <div class="ws-actions">
            <button type="button" class="ws-btn" id="wsUploadBtn">⬆ 上传文件</button>
            <button type="button" class="ws-btn" id="wsSelectAllBtn">☑ 全选</button>
            <button type="button" class="ws-btn" id="wsDeleteBtn">🗑 删除所选</button>
            <button type="button" class="ws-btn primary" id="wsUseBtn">✓ 使用所选</button>
          </div>
          <div class="ws-status" id="wsStatus"></div>
          <div class="ws-list" id="wsList"></div>
          <input type="file" id="wsFileInput" multiple hidden>
        </div>
      `;
      document.body.appendChild(mask);
      this.modal = mask;
      this.built = true;

      mask.addEventListener('click', (e) => {
        if (e.target === mask) this.close();
      });
      mask.querySelector('#wsClose').addEventListener('click', () => this.close());
      mask.querySelector('#wsUploadBtn').addEventListener('click', () => {
        mask.querySelector('#wsFileInput').click();
      });
      mask.querySelector('#wsFileInput').addEventListener('change', (e) => {
        const files = Array.from(e.target.files || []);
        if (files.length) this.upload(files);
        e.target.value = '';
      });
      mask.querySelector('#wsDeleteBtn').addEventListener('click', () => this.removeSelected());
      mask.querySelector('#wsSelectAllBtn').addEventListener('click', () => this.toggleSelectAll());
      mask.querySelector('#wsUseBtn').addEventListener('click', () => this.useSelected());
    },

    // ---------- 打开 / 关闭 ----------
    open(onUse) {
      this.ensure();
      if (typeof onUse === 'function') this.onUse = onUse;
      this.selected = {};
      const useBtn = this.modal.querySelector('#wsUseBtn');
      useBtn.style.display = this.onUse ? '' : 'none';
      this.modal.style.display = 'flex';
      this.refresh();
    },

    close() {
      if (this.modal) this.modal.style.display = 'none';
      this.onUse = null;
    },

    setStatus(msg, kind) {
      const el = this.modal && this.modal.querySelector('#wsStatus');
      if (!el) return;
      el.textContent = msg || '';
      el.className = 'ws-status' + (kind ? ' ws-status-' + kind : '');
    },

    headers(extra) {
      return Object.assign({}, extra || {});
    },

    // ---------- 列表 ----------
    refresh() {
      return fetch('/toolbox/api/workspace/list')
        .then((r) => r.json())
        .then((j) => {
          if (!j || j.code !== 200) throw new Error((j && j.msg) || '加载失败');
          this.files = (j.data && j.data.files) || [];
          this.render();
        })
        .catch((e) => this.setStatus('加载失败：' + (e.message || e), 'err'));
    },

    render() {
      const list = this.modal.querySelector('#wsList');
      if (!this.files.length) {
        list.innerHTML = '<div class="ws-empty">工作区还是空的，点「上传文件」添加</div>';
        this.updateSelectAllLabel();
        return;
      }
      list.innerHTML = this.files.map((f) => {
        const on = !!this.selected[f.name];
        return `
          <div class="ws-item${on ? ' on' : ''}" data-name="${esc(f.name)}">
            <div class="ws-item-main">
              <span class="ws-name" title="${esc(f.name)}">${esc(f.name)}</span>
              <span class="ws-meta">${fmtSize(f.size)} · ${fmtTime(f.mtime)}</span>
            </div>
            <span class="ws-check">${on ? '✓' : ''}</span>
          </div>
        `;
      }).join('');

      list.querySelectorAll('.ws-item').forEach((el) => {
        el.addEventListener('click', () => {
          const name = el.dataset.name;
          if (this.selected[name]) delete this.selected[name];
          else this.selected[name] = true;
          this.render();
        });
      });
      this.updateSelectAllLabel();
    },

    /** 全选 / 取消全选（当全部已选时切换为取消） */
    toggleSelectAll() {
      if (!this.files.length) return;
      const all = this.files.every((f) => this.selected[f.name]);
      if (all) {
        this.selected = {};
      } else {
        this.files.forEach((f) => { this.selected[f.name] = true; });
      }
      this.render();
    },

    /** 刷新「全选」按钮文案与可用态 */
    updateSelectAllLabel() {
      const btn = this.modal && this.modal.querySelector('#wsSelectAllBtn');
      if (!btn) return;
      const empty = !this.files.length;
      btn.disabled = empty;
      btn.classList.toggle('is-disabled', empty);
      const all = !empty && this.files.every((f) => this.selected[f.name]);
      btn.textContent = all ? '☐ 取消全选' : '☑ 全选';
    },

    selectedNames() {
      return Object.keys(this.selected);
    },

    // ---------- 上传 ----------
    upload(files) {
      const fd = new FormData();
      files.forEach((f) => fd.append('files', f));
      this.setStatus('上传中…');
      return fetch('/toolbox/api/workspace/upload', {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        body: fd
      })
        .then((r) => r.json())
        .then((j) => {
          if (!j || j.code !== 200) throw new Error((j && j.msg) || '上传失败');
          this.setStatus(`已上传 ${(j.data.files || []).length} 个文件 ✓`, 'ok');
          return this.refresh();
        })
        .catch((e) => this.setStatus('上传失败：' + (e.message || e), 'err'));
    },

    // ---------- 删除 ----------
    removeSelected() {
      const names = this.selectedNames();
      if (!names.length) {
        this.setStatus('请先选择文件', 'err');
        return;
      }
      if (!window.confirm(`确定删除选中的 ${names.length} 个文件？`)) return;
      this.setStatus('删除中…');
      Promise.all(names.map((name) =>
        fetch('/toolbox/api/workspace/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
          body: JSON.stringify({ name: name })
        }).then((r) => r.json())
      ))
        .then(() => {
          this.selected = {};
          this.setStatus('已删除 ✓', 'ok');
          return this.refresh();
        })
        .catch((e) => this.setStatus('删除失败：' + (e.message || e), 'err'));
    },

    // ---------- 取用：拉成 File 交给当前工具 ----------
    useSelected() {
      const names = this.selectedNames();
      if (!names.length) {
        this.setStatus('请先选择文件', 'err');
        return;
      }
      if (typeof this.onUse !== 'function') return;
      this.setStatus('取用中…');

      Promise.all(names.map((name) =>
        fetch('/toolbox/api/workspace/file/' + encodeURIComponent(name))
          .then((r) => {
            if (!r.ok) throw new Error(name + ' 读取失败');
            return r.blob();
          })
          .then((blob) => new File([blob], name, { type: blob.type || 'application/octet-stream' }))
      ))
        .then((files) => {
          this.onUse(files);
          this.setStatus(`已加入 ${files.length} 个文件 ✓`, 'ok');
          setTimeout(() => this.close(), 400);
        })
        .catch((e) => this.setStatus('取用失败：' + (e.message || e), 'err'));
    }
  };

  window.ToolboxWorkspace = WS;
})();
