/**
 * 上传区组件 - 支持拖拽 + 点击选择
 */
class UploadZone {
  constructor(container, options = {}) {
    this.container = container;
    this.accept = options.accept || "*";
    this.multiple = options.multiple !== false;
    this.maxSize = (options.maxSize || 50) * 1024 * 1024;
    this.allowFolder = options.allowFolder || false;
    this.onFiles = options.onFiles || (() => {});
    this.render();
  }

  render() {
    this.container.innerHTML = `
      <div class="tb-upload-zone" id="upload-zone">
        <div class="upload-icon">📤</div>
        <div class="upload-text">拖拽文件到此处</div>
        <div class="upload-hint">或点击选择文件 · 支持 ${this.allowFolder ? '文件夹' : '多文件'}</div>
        <input type="file" id="file-input" ${this.multiple ? 'multiple' : ''} accept="${this.accept}">
      </div>
    `;

    this.zone = this.container.querySelector('#upload-zone');
    this.input = this.container.querySelector('#file-input');

    // 点击选择
    this.input.addEventListener('change', (e) => {
      this.handleFiles(Array.from(e.target.files));
      this.input.value = '';
    });

    // 拖拽事件
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
      this.zone.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
      });
    });

    ['dragenter', 'dragover'].forEach(eventName => {
      this.zone.addEventListener(eventName, () => this.zone.classList.add('dragover'));
    });

    ['dragleave', 'drop'].forEach(eventName => {
      this.zone.addEventListener(eventName, () => this.zone.classList.remove('dragover'));
    });

    this.zone.addEventListener('drop', (e) => {
      const items = e.dataTransfer.items;
      if (items && this.allowFolder) {
        this.readEntries(items);
      } else {
        this.handleFiles(Array.from(e.dataTransfer.files));
      }
    });
  }

  async readEntries(items) {
    const files = [];
    for (const item of items) {
      const entry = item.webkitGetAsEntry ? item.webkitGetAsEntry() : null;
      if (entry) {
        const f = await this.traverseEntry(entry);
        files.push(...f);
      } else if (item.getAsFile) {
        files.push(item.getAsFile());
      }
    }
    this.handleFiles(files);
  }

  traverseEntry(entry, path = '') {
    return new Promise((resolve) => {
      if (entry.isFile) {
        entry.file((file) => resolve([file]));
      } else if (entry.isDirectory) {
        const reader = entry.createReader();
        const files = [];
        const read = () => {
          reader.readEntries(async (entries) => {
            if (entries.length === 0) return resolve(files);
            for (const e of entries) {
              const f = await this.traverseEntry(e, path + entry.name + '/');
              files.push(...f);
            }
            read();
          });
        };
        read();
      }
    });
  }

  handleFiles(files) {
    // 格式过滤
    let accepted = files;
    if (this.accept && this.accept !== '*') {
      const exts = this.accept.split(',').map(s => s.trim().toLowerCase());
      accepted = files.filter(f => {
        const name = f.name.toLowerCase();
        return exts.some(ext => {
          if (ext.includes('/')) {
            // mime type like image/*
            if (ext.endsWith('/*')) return f.type.startsWith(ext.replace('/*', ''));
            return f.type === ext;
          }
          return name.endsWith(ext);
        });
      });
      const skipped = files.length - accepted.length;
      if (skipped > 0) ToolboxApp.toast(`已跳过 ${skipped} 个不支持的文件`, 'info');
    }

    // 大小过滤
    const valid = [];
    for (const f of accepted) {
      if (f.size > this.maxSize) {
        ToolboxApp.toast(`「${f.name}」超过大小限制，已跳过`, 'error');
      } else {
        valid.push(f);
      }
    }

    if (valid.length > 0) {
      this.onFiles(valid);
    }
  }

  destroy() {
    this.container.innerHTML = '';
  }
}
