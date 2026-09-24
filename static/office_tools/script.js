// ========================================
// 办公工具 - 前端交互脚本
// 远程使用模型：上传文件夹 zip → 服务端处理 → 结果 zip 下载
// ========================================

// ---- 状态管理 ----
const OTState = {
  currentTab: 'mod1',
  currentSubTabs: { mod1: 'm1-folders', mod3: 'm3-simple', mod4: 'm4-cell', mod5: 'm5-movecopy' },
  uploadedFiles: {},
  extractResult: { items: [], names: [], token: '' },
  pathExtractResult: { items: [], token: '' },
  tasks: JSON.parse(localStorage.getItem('ot_tasks') || '[]'),
  envChecked: false,
  hasWin32com: false
};

// ---- Tab 切换 ----
function switchTab(tabId) {
  document.querySelectorAll('.ot-clay-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.ot-clay-panel').forEach(p => p.classList.remove('active'));
  document.querySelector(`.ot-clay-tab[data-tab="${tabId}"]`).classList.add('active');
  document.getElementById(tabId).classList.add('active');
  OTState.currentTab = tabId;
  const subId = OTState.currentSubTabs[tabId];
  if (subId) switchSubTab(tabId, subId);
  if (tabId === 'mod4' && !OTState.envChecked) checkEnv();
}

function switchSubTab(parentId, subId) {
  const parent = document.getElementById(parentId);
  parent.querySelectorAll('.ot-clay-subtab').forEach(t => t.classList.remove('active'));
  parent.querySelectorAll('.ot-clay-subpanel').forEach(p => p.classList.remove('active'));
  parent.querySelector(`.ot-clay-subtab[data-subtab="${subId}"]`).classList.add('active');
  document.getElementById(subId).classList.add('active');
  OTState.currentSubTabs[parentId] = subId;
}

// ---- 文件上传处理 ----
function handleFileSelect(input, key) {
  const file = input.files[0];
  if (!file) return;
  OTState.uploadedFiles[key] = file;
  const tag = document.getElementById(`${key}-tag`);
  if (tag) {
    tag.style.display = 'inline-flex';
    tag.innerHTML = `<span>📄</span> ${file.name} <span style="cursor:pointer;margin-left:6px;" onclick="clearFile('${key}')">✕</span>`;
  }
}

function handleMultiFileSelect(input, key) {
  const files = Array.from(input.files);
  if (!files.length) return;
  OTState.uploadedFiles[key] = files;
  const tag = document.getElementById(`${key}-tag`);
  if (tag) {
    tag.style.display = 'inline-flex';
    tag.innerHTML = `<span>📄</span> 已选 ${files.length} 个文件 <span style="cursor:pointer;margin-left:6px;" onclick="clearFile('${key}')">✕</span>`;
  }
}

function clearFile(key) {
  delete OTState.uploadedFiles[key];
  const input = document.getElementById(key.includes('template') ? key : `${key}-file`);
  if (input) input.value = '';
  const tag = document.getElementById(`${key}-tag`);
  if (tag) { tag.style.display = 'none'; tag.innerHTML = ''; }
}

function toggleTemplateUpload() {
  const checked = document.getElementById('m1-sheets-fill').checked;
  document.getElementById('m1-sheets-template-group').style.display = checked ? 'block' : 'none';
}

function toggleSuffixInput() {
  const mode = document.querySelector('input[name="m2-mode"]:checked').value;
  document.getElementById('m2-suffix-group').style.display = mode === 'filter' ? 'block' : 'none';
}

// ---- 通用请求 ----
async function otFetch(url, options = {}) {
  const res = await fetch(url, {
    headers: { 'X-Requested-With': 'XMLHttpRequest', ...(options.headers || {}) },
    ...options
  });
  const data = await res.json();
  return data;
}

// ---- 结果 zip 下载 ----
function triggerDownload(url) {
  const a = document.createElement('a');
  a.href = url;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// ---- 日志渲染 ----
function renderLog(containerId, logEntries) {
  const container = document.getElementById(containerId);
  if (!container) return;
  if (!logEntries || logEntries.length === 0) {
    container.innerHTML = '<div class="ot-log-empty">暂无日志</div>';
    return;
  }
  container.innerHTML = logEntries.map(e =>
    `<div class="ot-log-entry ${e.status}">${escapeHtml(e.msg)}</div>`
  ).join('');
  container.scrollTop = container.scrollHeight;
}

function renderStats(containerId, stats) {
  const container = document.getElementById(containerId);
  if (!container || !stats) return;
  const items = [];
  if (stats.success !== undefined) items.push(`<div class="ot-stat-card success"><span class="ot-stat-value">${stats.success}</span><span class="ot-stat-label">成功</span></div>`);
  if (stats.skip !== undefined) items.push(`<div class="ot-stat-card skip"><span class="ot-stat-value">${stats.skip}</span><span class="ot-stat-label">跳过</span></div>`);
  if (stats.fail !== undefined) items.push(`<div class="ot-stat-card error"><span class="ot-stat-value">${stats.fail}</span><span class="ot-stat-label">失败</span></div>`);
  if (stats.missing !== undefined) items.push(`<div class="ot-stat-card error"><span class="ot-stat-value">${stats.missing}</span><span class="ot-stat-label">缺失</span></div>`);
  container.innerHTML = items.join('');
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function addTaskLog(containerId, msg, status = 'info') {
  const container = document.getElementById(containerId);
  if (!container) return;
  if (container.querySelector('.ot-log-empty')) container.innerHTML = '';
  const entry = document.createElement('div');
  entry.className = `ot-log-entry ${status}`;
  entry.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
  container.appendChild(entry);
  container.scrollTop = container.scrollHeight;
}

function logStats(log) {
  if (!log) return { success: 0, skip: 0, fail: 0 };
  return {
    success: log.filter(l => l.status === 'success').length,
    skip: log.filter(l => l.status === 'skip').length,
    fail: log.filter(l => l.status === 'error').length,
  };
}

// ---- 任务中心 ----
function saveTask(title, type, stats, log) {
  const task = {
    id: Date.now(),
    title,
    type,
    stats,
    log,
    time: new Date().toLocaleString(),
    status: stats.fail > 0 ? 'fail' : 'success'
  };
  OTState.tasks.unshift(task);
  if (OTState.tasks.length > 50) OTState.tasks.pop();
  localStorage.setItem('ot_tasks', JSON.stringify(OTState.tasks));
  renderTaskList();
}

function renderTaskList(filter = 'all') {
  const container = document.getElementById('task-list');
  const tasks = filter === 'all' ? OTState.tasks : OTState.tasks.filter(t => t.status === filter);
  if (tasks.length === 0) {
    container.innerHTML = `
      <div class="ot-clay-task-empty">
        <span class="ot-empty-icon">📋</span>
        <p>${filter === 'all' ? '暂无任务记录' : '暂无' + (filter === 'success' ? '成功' : '失败') + '记录'}</p>
        <span class="ot-empty-hint">执行任务后将在此显示历史记录</span>
      </div>`;
    return;
  }
  container.innerHTML = tasks.map(t => `
    <div class="ot-clay-task-item">
      <div class="ot-task-status ${t.status}">${t.status === 'success' ? '✅' : '❌'}</div>
      <div class="ot-task-info">
        <div class="ot-task-title">${escapeHtml(t.title)}</div>
        <div class="ot-task-meta">${escapeHtml(t.time)} · ${escapeHtml(t.type)}</div>
      </div>
      <div class="ot-task-stats">
        ${t.stats.success !== undefined ? `<span class="ot-task-stat">✅ ${t.stats.success}</span>` : ''}
        ${t.stats.fail !== undefined ? `<span class="ot-task-stat">❌ ${t.stats.fail}</span>` : ''}
        ${t.stats.skip !== undefined ? `<span class="ot-task-stat">⏭ ${t.stats.skip}</span>` : ''}
      </div>
    </div>
  `).join('');
}

function switchTaskTab(filter) {
  document.querySelectorAll('.ot-clay-task-tab').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  renderTaskList(filter);
}

function clearAllTasks() {
  if (!confirm('确定要清空所有任务记录吗？')) return;
  OTState.tasks = [];
  localStorage.removeItem('ot_tasks');
  renderTaskList();
}

// ---- 模块1：批量创建 ----

async function executeMod1Folders() {
  const file = OTState.uploadedFiles['m1-folders'];
  if (!file) { alert('请上传名称Excel文件'); return; }
  const col = document.getElementById('m1-folders-col').value.trim();
  const dirs = document.getElementById('m1-folders-dirs').value.trim();
  addTaskLog('m1-folders-log', '正在执行批量创建文件夹...', 'info');
  const form = new FormData();
  form.append('excel', file);
  form.append('column_header', col);
  form.append('target_dirs', dirs.split('\n').map(s => s.trim()).filter(Boolean).join(';'));
  const data = await otFetch('/office_tools/api/mod1/folders', { method: 'POST', body: form });
  if (data.code === 200) {
    renderStats('m1-folders-stats', {
      success: data.data.log.filter(l => l.status === 'success').length,
      skip: data.data.log.filter(l => l.status === 'skip').length,
      fail: data.data.log.filter(l => l.status === 'error').length
    });
    renderLog('m1-folders-log', data.data.log);
    saveTask('批量创建文件夹', '批量创建', logStats(data.data.log), data.data.log);
    if (data.data.download_url) triggerDownload(data.data.download_url);
  } else {
    addTaskLog('m1-folders-log', '执行失败: ' + data.msg, 'error');
  }
}

async function executeMod1Files() {
  const file = OTState.uploadedFiles['m1-files'];
  if (!file) { alert('请上传名称Excel文件'); return; }
  const col = document.getElementById('m1-files-col').value.trim();
  const dirs = document.getElementById('m1-files-dirs').value.trim();
  const ftype = document.querySelector('input[name="m1-file-type"]:checked').value;
  addTaskLog('m1-files-log', '正在执行批量创建文件...', 'info');
  const form = new FormData();
  form.append('excel', file);
  form.append('column_header', col);
  form.append('target_dirs', dirs.split('\n').map(s => s.trim()).filter(Boolean).join(';'));
  form.append('file_type', ftype);
  const data = await otFetch('/office_tools/api/mod1/files', { method: 'POST', body: form });
  if (data.code === 200) {
    renderStats('m1-files-stats', logStats(data.data.log));
    renderLog('m1-files-log', data.data.log);
    saveTask('批量创建文件', '批量创建', logStats(data.data.log), data.data.log);
    if (data.data.download_url) triggerDownload(data.data.download_url);
  } else {
    addTaskLog('m1-files-log', '执行失败: ' + data.msg, 'error');
  }
}

async function executeMod1Sheets() {
  const file = OTState.uploadedFiles['m1-sheets'];
  const targets = OTState.uploadedFiles['m1-sheets-targets'];
  if (!file) { alert('请上传名称Excel文件'); return; }
  if (!targets || !targets.length) { alert('请上传需要添加分表的Excel文件（可多选）'); return; }
  const col = document.getElementById('m1-sheets-col').value.trim();
  const fill = document.getElementById('m1-sheets-fill').checked;
  addTaskLog('m1-sheets-log', '正在执行批量创建分表...', 'info');
  const form = new FormData();
  form.append('excel', file);
  form.append('column_header', col);
  form.append('fill', fill ? 'true' : 'false');
  targets.forEach(t => form.append('target_files', t));
  if (fill && OTState.uploadedFiles['m1-sheets-template']) {
    form.append('template', OTState.uploadedFiles['m1-sheets-template']);
  }
  const data = await otFetch('/office_tools/api/mod1/sheets', { method: 'POST', body: form });
  if (data.code === 200) {
    renderStats('m1-sheets-stats', logStats(data.data.log));
    renderLog('m1-sheets-log', data.data.log);
    saveTask('批量创建Excel分表', '批量创建', logStats(data.data.log), data.data.log);
    if (data.data.download_url) triggerDownload(data.data.download_url);
  } else {
    addTaskLog('m1-sheets-log', '执行失败: ' + data.msg, 'error');
  }
}

// ---- 模块2：提取清单 ----

async function executeMod2Extract() {
  const folder = OTState.uploadedFiles['m2-folder'];
  if (!folder) { alert('请上传文件夹压缩包（zip）'); return; }
  const mode = document.querySelector('input[name="m2-mode"]:checked').value;
  const recursive = document.getElementById('m2-recursive').checked;
  const suffix = document.getElementById('m2-suffix').value.trim();
  const removeSuffix = document.getElementById('m2-remove-suffix').checked;
  addTaskLog('m2-log', '正在提取...', 'info');
  const form = new FormData();
  form.append('folder_zip', folder);
  form.append('mode', mode);
  form.append('recursive', recursive ? 'true' : 'false');
  form.append('suffix', suffix);
  form.append('remove_suffix', removeSuffix ? 'true' : 'false');
  const data = await otFetch('/office_tools/api/mod2/extract', { method: 'POST', body: form });
  if (data.code === 200) {
    OTState.extractResult = { items: data.data.items, names: data.data.names, token: data.data.token };
    document.getElementById('m2-count').textContent = `(${data.data.count} 条)`;
    const tbody = document.getElementById('m2-tbody');
    if (data.data.names.length === 0) {
      tbody.innerHTML = '<tr><td colspan="2" class="ot-td-empty">未找到匹配项</td></tr>';
    } else {
      tbody.innerHTML = data.data.names.map((name, i) =>
        `<tr><td>${i + 1}</td><td>${escapeHtml(name)}</td></tr>`
      ).join('');
    }
  } else {
    alert(data.msg);
  }
}

async function exportMod2(type) {
  if (!OTState.extractResult.token) { alert('请先执行提取'); return; }
  const form = new FormData();
  form.append('token', OTState.extractResult.token);
  form.append('file_type', type);
  const data = await otFetch('/office_tools/api/mod2/export-detailed', { method: 'POST', body: form });
  if (data.code === 200 && data.data.download_url) {
    triggerDownload(data.data.download_url);
  } else {
    alert(data.msg);
  }
}

// ---- 模块3：批量改名 ----

async function executeMod3Simple() {
  const folder = OTState.uploadedFiles['m3-simple-folder'];
  if (!folder) { alert('请上传文件夹压缩包（zip）'); return; }
  const oldText = document.getElementById('m3-simple-old').value;
  const newText = document.getElementById('m3-simple-new').value;
  if (!oldText) { alert('请填写查找文字'); return; }
  addTaskLog('m3-simple-log', '正在执行批量替换...', 'info');
  const form = new FormData();
  form.append('folder_zip', folder);
  form.append('old_text', oldText);
  form.append('new_text', newText);
  const data = await otFetch('/office_tools/api/mod3/rename-simple', { method: 'POST', body: form });
  if (data.code === 200) {
    renderStats('m3-simple-stats', { success: data.data.count, skip: 0, fail: data.data.log.length - data.data.count });
    renderLog('m3-simple-log', data.data.log);
    saveTask('简单文件名替换', '批量改名', { success: data.data.count, skip: 0, fail: data.data.log.length - data.data.count }, data.data.log);
    if (data.data.download_url) triggerDownload(data.data.download_url);
  } else {
    addTaskLog('m3-simple-log', '执行失败: ' + data.msg, 'error');
  }
}

async function executeMod3Excel() {
  const folder = OTState.uploadedFiles['m3-excel-folder'];
  const file = OTState.uploadedFiles['m3-excel'];
  if (!folder) { alert('请上传文件夹压缩包（zip）'); return; }
  if (!file) { alert('请上传对照Excel文件'); return; }
  const oldCol = document.getElementById('m3-excel-oldcol').value.trim();
  const newCol = document.getElementById('m3-excel-newcol').value.trim();
  addTaskLog('m3-excel-log', '正在执行Excel对照重命名...', 'info');
  const form = new FormData();
  form.append('folder_zip', folder);
  form.append('excel', file);
  form.append('old_col', oldCol);
  form.append('new_col', newCol);
  const data = await otFetch('/office_tools/api/mod3/rename-excel', { method: 'POST', body: form });
  if (data.code === 200) {
    renderStats('m3-excel-stats', { success: data.data.success, skip: data.data.skip, fail: data.data.fail });
    renderLog('m3-excel-log', data.data.log);
    saveTask('Excel对照重命名', '批量改名', { success: data.data.success, skip: data.data.skip, fail: data.data.fail }, data.data.log);
    if (data.data.download_url) triggerDownload(data.data.download_url);
  } else {
    addTaskLog('m3-excel-log', '执行失败: ' + data.msg, 'error');
  }
}

// ---- 模块4：Excel专项 ----

async function checkEnv() {
  OTState.envChecked = true;
  const banner = document.getElementById('m4-env-banner');
  const data = await otFetch('/office_tools/api/mod4/env-check');
  OTState.hasWin32com = !!data.data.has_win32com;
  const eng = data.data.engine;
  if (eng) {
    const label = eng === 'com' ? 'Microsoft Office（COM）' : 'LibreOffice headless';
    banner.className = 'ot-clay-env-banner ok';
    banner.innerHTML = `<span class="ot-env-icon">✅</span><span>转换引擎：${label}，转PDF可用</span>`;
  } else {
    banner.className = 'ot-clay-env-banner error';
    banner.innerHTML = `<span class="ot-env-icon">⚠️</span><span>${data.data.message || '未检测到转换引擎，转PDF不可用'}</span>`;
  }
}

async function executeMod4Cell() {
  const folder = OTState.uploadedFiles['m4-cell-folder'];
  if (!folder) { alert('请上传文件夹压缩包（zip）'); return; }
  const addr = document.getElementById('m4-cell-addr').value.trim();
  const oldText = document.getElementById('m4-cell-old').value.trim();
  const newText = document.getElementById('m4-cell-new').value;
  if (!addr || !oldText) { alert('请填写完整信息'); return; }
  addTaskLog('m4-cell-log', '正在执行单元格替换...', 'info');
  const form = new FormData();
  form.append('folder_zip', folder);
  form.append('cell_addr', addr);
  form.append('old_text', oldText);
  form.append('new_text', newText);
  const data = await otFetch('/office_tools/api/mod4/replace-cell', { method: 'POST', body: form });
  if (data.code === 200) {
    renderLog('m4-cell-log', data.data.log);
    saveTask('单元格批量替换', 'Excel专项', logStats(data.data.log), data.data.log);
    if (data.data.download_url) triggerDownload(data.data.download_url);
  } else {
    addTaskLog('m4-cell-log', '执行失败: ' + data.msg, 'error');
  }
}

async function executeMod4Pdf() {
  const folder = OTState.uploadedFiles['m4-pdf-folder'];
  if (!folder) { alert('请上传文件夹压缩包（zip）'); return; }
  addTaskLog('m4-pdf-log', '正在执行Excel转PDF...', 'info');
  const form = new FormData();
  form.append('folder_zip', folder);
  const data = await otFetch('/office_tools/api/mod4/to-pdf', { method: 'POST', body: form });
  if (data.code === 200) {
    renderLog('m4-pdf-log', data.data.log);
    saveTask('Excel批量转PDF', 'Excel专项', logStats(data.data.log), data.data.log);
    if (data.data.download_url) triggerDownload(data.data.download_url);
  } else {
    addTaskLog('m4-pdf-log', '执行失败: ' + data.msg, 'error');
  }
}

async function executeMod4Delete() {
  const folder = OTState.uploadedFiles['m4-delete-folder'];
  if (!folder) { alert('请上传文件夹压缩包（zip）'); return; }
  const sheetNames = document.getElementById('m4-delete-names').value.trim();
  if (!sheetNames) { alert('请输入待删除的分表名称'); return; }
  addTaskLog('m4-delete-log', '正在执行批量删除分表...', 'info');
  const form = new FormData();
  form.append('folder_zip', folder);
  form.append('mode', 'folder');
  form.append('sheet_names', sheetNames);
  const data = await otFetch('/office_tools/api/mod4/delete-sheets', { method: 'POST', body: form });
  if (data.code === 200) {
    renderLog('m4-delete-log', data.data.log);
    saveTask('批量删除Excel分表', 'Excel专项', { success: data.data.total_deleted, skip: 0, fail: 0 }, data.data.log);
    if (data.data.download_url) triggerDownload(data.data.download_url);
  } else {
    addTaskLog('m4-delete-log', '执行失败: ' + data.msg, 'error');
  }
}

// ---- 模块5：文件搬运 ----

async function executeMod5MoveCopy() {
  const folder = OTState.uploadedFiles['m5-folder'];
  const file = OTState.uploadedFiles['m5'];
  const action = document.querySelector('input[name="m5-action"]:checked').value;
  const recursive = document.getElementById('m5-recursive').checked;
  if (!folder) { alert('请上传源文件夹压缩包（zip）'); return; }
  if (!file) { alert('请上传映射Excel文件'); return; }
  addTaskLog('m5-movecopy-log', '正在执行文件搬运...', 'info');
  const form = new FormData();
  form.append('folder_zip', folder);
  form.append('excel', file);
  form.append('action', action);
  form.append('recursive', recursive ? 'true' : 'false');
  const data = await otFetch('/office_tools/api/mod5/movecopy', { method: 'POST', body: form });
  if (data.code === 200) {
    renderStats('m5-movecopy-stats', { success: data.data.success, fail: data.data.fail, missing: data.data.missing });
    renderLog('m5-movecopy-log', data.data.log);
    saveTask(`文件批量${action === 'copy' ? '复制' : '移动'}`, '文件搬运', { success: data.data.success, fail: data.data.fail, missing: data.data.missing }, data.data.log);
    if (data.data.download_url) triggerDownload(data.data.download_url);
  } else {
    addTaskLog('m5-movecopy-log', '执行失败: ' + data.msg, 'error');
  }
}

async function executeMod5Paths() {
  const folder = OTState.uploadedFiles['m5-paths-folder'];
  if (!folder) { alert('请上传文件夹压缩包（zip）'); return; }
  const pathType = document.querySelector('input[name="m5-paths-type"]:checked').value;
  const recursive = document.getElementById('m5-paths-recursive').checked;
  const form = new FormData();
  form.append('folder_zip', folder);
  form.append('path_type', pathType);
  form.append('recursive', recursive ? 'true' : 'false');
  const data = await otFetch('/office_tools/api/mod5/extract-paths', { method: 'POST', body: form });
  if (data.code === 200) {
    OTState.pathExtractResult = { items: data.data.items, token: data.data.token };
    document.getElementById('m5-paths-count').textContent = `(${data.data.count} 条)`;
    const tbody = document.getElementById('m5-paths-tbody');
    if (data.data.items.length === 0) {
      tbody.innerHTML = '<tr><td colspan="3" class="ot-td-empty">未找到文件</td></tr>';
    } else {
      tbody.innerHTML = data.data.items.map(item =>
        `<tr><td>${escapeHtml(item.filename)}</td><td>${escapeHtml(item.path)}</td><td>${escapeHtml(item.type)}</td></tr>`
      ).join('');
    }
  } else {
    alert(data.msg);
  }
}

async function exportMod5Paths(type) {
  if (!OTState.pathExtractResult.token) { alert('请先执行提取'); return; }
  const form = new FormData();
  form.append('token', OTState.pathExtractResult.token);
  form.append('file_type', type);
  const data = await otFetch('/office_tools/api/mod5/export-paths', { method: 'POST', body: form });
  if (data.code === 200 && data.data.download_url) {
    triggerDownload(data.data.download_url);
  } else {
    alert(data.msg);
  }
}

// ---- 模板下载 ----
function downloadTemplate(type) {
  window.open(`/office_tools/api/template/${type}`, '_blank');
}

// ---- 弹窗 ----
function showHelp() {
  document.getElementById('help-modal').style.display = 'flex';
}

function closeHelp() {
  document.getElementById('help-modal').style.display = 'none';
}

function showEnvCheck() {
  document.getElementById('env-modal').style.display = 'flex';
  checkEnvModal();
}

function closeEnv() {
  document.getElementById('env-modal').style.display = 'none';
}

async function checkEnvModal() {
  const body = document.getElementById('env-modal-body');
  body.innerHTML = '<div class="ot-env-item"><span class="ot-env-status ot-env-checking">⏳</span><span>正在检测...</span></div>';
  const data = await otFetch('/office_tools/api/mod4/env-check');
  const d = data.data;
  body.innerHTML = `
    <div class="ot-env-item"><span class="ot-env-status">${d.has_win32com ? '✅' : '⚠️'}</span><span>Microsoft Office（COM）：${d.has_win32com ? '已就绪' : '未检测到'}</span></div>
    <div class="ot-env-item"><span class="ot-env-status">${d.has_libreoffice ? '✅' : '⚠️'}</span><span>LibreOffice headless：${d.has_libreoffice ? '已就绪' : '未检测到'}</span></div>
    <div class="ot-env-item"><span class="ot-env-status">✅</span><span>openpyxl：已就绪（.xlsx/.xlsm 免 Office）</span></div>
    <div class="ot-env-item"><span class="ot-env-status">✅</span><span>xlrd：已就绪</span></div>
    <div class="ot-env-item"><span class="ot-env-status">✅</span><span>python-docx：已就绪</span></div>
    <div style="margin-top:12px;font-size:12px;color:var(--ot-text-muted);">${data.data.message}</div>
  `;
}

// ---- Hub 深链支持：#mod4 或 #mod4/m4-pdf（工具箱 → 文档处理区跳转用） ----
function applyHashDeepLink() {
  const h = (window.location.hash || '').replace(/^#/, '');
  if (!h) return;
  const parts = h.split('/');
  const tabId = parts[0];
  if (!document.querySelector('.ot-clay-tab[data-tab="' + tabId + '"]')) return;
  switchTab(tabId);
  if (parts[1]) {
    const subBtn = document.querySelector('#' + tabId + ' .ot-clay-subtab[data-subtab="' + parts[1] + '"]');
    if (subBtn) switchSubTab(tabId, parts[1]);
  }
}
document.addEventListener('DOMContentLoaded', () => {
  applyHashDeepLink();
  window.addEventListener('hashchange', applyHashDeepLink);
});

// ---- 拖拽上传 ----
document.addEventListener('DOMContentLoaded', () => {
  renderTaskList();
  document.querySelectorAll('.ot-clay-upload-zone').forEach(zone => {
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.style.borderColor = 'var(--ot-primary)'; zone.style.background = 'rgba(107,141,214,0.08)'; });
    zone.addEventListener('dragleave', e => { e.preventDefault(); zone.style.borderColor = ''; zone.style.background = ''; });
    zone.addEventListener('drop', e => {
      e.preventDefault();
      zone.style.borderColor = ''; zone.style.background = '';
      const files = e.dataTransfer.files;
      if (files.length > 0) {
        const input = zone.nextElementSibling;
        if (input && input.type === 'file') {
          const dt = new DataTransfer();
          dt.items.add(files[0]);
          input.files = dt.files;
          input.dispatchEvent(new Event('change'));
        }
      }
    });
  });
});
