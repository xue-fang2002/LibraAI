/**
 * 文件列表组件
 */
class FileList {
  constructor(container, options = {}) {
    this.container = container;
    this.files = [];
    this.onChange = options.onChange || (() => {});
    this.onReorder = options.onReorder || null; // 用于PDF合并等需要排序的场景
    this.render();
  }

  render() {
    this.container.innerHTML = '<div class="tb-file-list" id="file-list"></div>';
    this.el = this.container.querySelector('#file-list');
    this.refresh();
  }

  addFiles(newFiles) {
    // 去重
    for (const f of newFiles) {
      const exists = this.files.some(ex => ex.name === f.name && ex.size === f.size);
      if (!exists) this.files.push(f);
    }
    this.refresh();
    this.onChange(this.files);
  }

  removeFile(index) {
    this.files.splice(index, 1);
    this.refresh();
    this.onChange(this.files);
  }

  getFiles() {
    return this.files;
  }

  clear() {
    this.files = [];
    this.refresh();
    this.onChange(this.files);
  }

  refresh() {
    if (this.files.length === 0) {
      this.el.innerHTML = '';
      return;
    }

    this.el.innerHTML = this.files.map((f, i) => `
      <div class="tb-file-item" data-index="${i}" ${this.onReorder ? 'draggable="true"' : ''}>
        <span class="file-icon">${this.getFileIcon(f.name)}</span>
        <div class="file-info">
          <div class="file-name">${this.escapeHtml(f.name)}</div>
          <div class="file-size">${this.formatSize(f.size)}</div>
        </div>
        <button class="file-remove" data-index="${i}" title="移除">×</button>
      </div>
    `).join('');

    // 删除事件
    this.el.querySelectorAll('.file-remove').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.removeFile(parseInt(btn.dataset.index));
      });
    });

    // 拖拽排序（仅需要时）
    if (this.onReorder) {
      this.initDragSort();
    }
  }

  initDragSort() {
    let dragSrc = null;
    this.el.querySelectorAll('.tb-file-item').forEach(item => {
      item.addEventListener('dragstart', (e) => {
        dragSrc = item;
        item.style.opacity = '0.4';
        e.dataTransfer.effectAllowed = 'move';
      });
      item.addEventListener('dragend', () => {
        item.style.opacity = '1';
        this.el.querySelectorAll('.tb-file-item').forEach(i => i.style.borderTop = '');
      });
      item.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        if (item !== dragSrc) {
          const rect = item.getBoundingClientRect();
          const mid = rect.top + rect.height / 2;
          item.style.borderTop = e.clientY < mid ? '2px solid var(--primary)' : '';
          item.style.borderBottom = e.clientY >= mid ? '2px solid var(--primary)' : '';
        }
      });
      item.addEventListener('dragleave', () => {
        item.style.borderTop = '';
        item.style.borderBottom = '';
      });
      item.addEventListener('drop', (e) => {
        e.preventDefault();
        item.style.borderTop = '';
        item.style.borderBottom = '';
        if (dragSrc && dragSrc !== item) {
          const fromIdx = parseInt(dragSrc.dataset.index);
          const toIdx = parseInt(item.dataset.index);
          const [moved] = this.files.splice(fromIdx, 1);
          this.files.splice(toIdx, 0, moved);
          this.refresh();
          this.onChange(this.files);
          if (this.onReorder) this.onReorder(this.files);
        }
      });
    });
  }

  getFileIcon(filename) {
    const ext = filename.split('.').pop().toLowerCase();
    const map = {
      pdf: '📄', doc: '📝', docx: '📝', xls: '📊', xlsx: '📊',
      ppt: '🎬', pptx: '🎬', txt: '📃', json: '📋', csv: '📊',
      jpg: '🖼️', jpeg: '🖼️', png: '🖼️', gif: '🖼️', webp: '🖼️', bmp: '🖼️', ico: '🖼️',
      zip: '📦', rar: '📦', '7z': '📦'
    };
    return map[ext] || '📎';
  }

  formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }

  escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
}
