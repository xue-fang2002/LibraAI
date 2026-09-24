/**
 * 结果面板组件
 */
class ResultPanel {
  constructor(container) {
    this.container = container;
    this.tempId = null;
  }

  clear() {
    this.container.innerHTML = '';
  }

  showTextResult(text, title = '处理结果') {
    this.container.innerHTML = `
      <div class="tb-glass-panel">
        <div class="tb-result-header">
          <span class="success-icon">✅</span>
          <span>${title}</span>
        </div>
        <div class="tb-result-actions">
          <button class="tb-btn tb-btn-sm tb-btn-secondary" onclick="ToolboxApp.copyText(this)">
            📋 复制结果
          </button>
        </div>
        <pre class="tb-result-preview">${this.escapeHtml(text)}</pre>
      </div>
    `;
  }

  showFileResults(files, tempId, title = '处理完成') {
    this.tempId = tempId;
    const hasMultiple = files.length > 1;
    const apiBase = window.TOOLBOX_API_BASE || '';

    this.container.innerHTML = `
      <div class="tb-glass-panel">
        <div class="tb-result-header">
          <span class="success-icon">✅</span>
          <span>${title} · 共 ${files.length} 个文件</span>
        </div>
        <div class="tb-result-actions">
          ${hasMultiple ? `
            <a class="tb-btn tb-btn-primary tb-btn-sm" href="${apiBase}/toolbox/api/download-zip/${tempId}">
              📦 下载全部 (ZIP)
            </a>
          ` : ''}
        </div>
        <div class="tb-result-files">
          ${files.map(f => `
            <div class="tb-result-file-card">
              <div class="file-icon">📄</div>
              <div class="file-name">${this.escapeHtml(f.name)}</div>
              <div class="file-size">${f.size ? this.formatSize(f.size) : ''}</div>
              <a class="tb-btn tb-btn-sm tb-btn-secondary" 
                 href="${apiBase}/toolbox/api/download/${tempId}/${encodeURIComponent(f.name)}"
                 style="margin-top:8px;">
                ⬇️ 下载
              </a>
            </div>
          `).join('')}
        </div>
      </div>
    `;
  }

  showEmpty() {
    this.container.innerHTML = `
      <div class="tb-empty-state">
        <div class="empty-icon">🧰</div>
        <div class="empty-text">选择一个工具开始使用</div>
      </div>
    `;
  }

  escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  formatSize(bytes) {
    if (!bytes) return '';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }
}
