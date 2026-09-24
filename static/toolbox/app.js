/**
 * 工具箱核心应用
 * 单页多视图：一级分类 Tab + 二级分类 Pill + 动态工具面板
 */

// API 基础路径（适配 Flask url_for）
window.TOOLBOX_API_BASE = '';

const ToolboxApp = {
  state: {
    category: null,
    tool: null,
    tempId: null,
    files: [],
    processing: false
  },

  init() {
    this.applyHubMode();
    this.renderSearchBar();
    this.renderCategories();
    this.restoreFromHash();
    window.addEventListener('hashchange', () => this.restoreFromHash());
  },

  // ---------- Hub 深链模式：从工具箱 Hub 进入（?hub=1）时只显示对应分类 ----------
  // 隐藏一级分类 Tab 栏，顶部加「返回」按钮；back 参数指定返回地址（默认工具箱首页）
  isHub() {
    return new URLSearchParams(window.location.search).get('hub') === '1';
  },

  isEmbedded() {
    return new URLSearchParams(window.location.search).get('embedded') === '1';
  },

  applyHubMode() {
    if (!this.isHub()) return;
    const bar = document.getElementById('category-bar');
    if (bar) bar.style.display = 'none';

    // embedded=1：被 Hub 首页内联嵌入（iframe），不渲染返回按钮，启动高度自适应上报
    if (this.isEmbedded()) {
      this.startHeightSync();
      return;
    }

    // 返回地址：仅允许站内路径（/开头且非//），防止外链
    let backUrl = new URLSearchParams(window.location.search).get('back') || '/tools';
    if (!backUrl.startsWith('/') || backUrl.startsWith('//')) backUrl = '/tools';
    const page = document.getElementById('toolbox-app');
    if (page && !document.getElementById('tb-hub-back')) {
      const back = document.createElement('a');
      back.id = 'tb-hub-back';
      back.className = 'tb-hub-back';
      back.href = backUrl;
      const label = backUrl === '/tools/document' ? '返回文档处理'
                  : backUrl === '/tools' ? '返回工具箱'
                  : '返回';
      back.textContent = '‹ ' + label;
      page.insertBefore(back, page.firstChild);
    }
  },

  // 内联嵌入时把内容高度上报父页面，父页面 iframe 据此自适应高度，避免空白滚动区
  startHeightSync() {
    const post = () => {
      const h = Math.max(
        document.body.scrollHeight || 0,
        document.documentElement.scrollHeight || 0
      );
      if (window.parent && window.parent !== window) {
        window.parent.postMessage({ type: 'hub-iframe-resize', height: h }, '*');
      }
    };
    window.addEventListener('load', post);
    setTimeout(post, 200);
    setTimeout(post, 600);
    if (window.MutationObserver) {
      const obs = new MutationObserver(() => post());
      obs.observe(document.body, { childList: true, subtree: true, attributes: true });
    }
    window.addEventListener('resize', post);
  },

  // Hub 模式下选中具体工具后的布局调整：
  // 仅当 embedded（Hub 首页内联 iframe）时隐藏二级分类栏以节省高度；
  // Hub 整页跳转（?hub=1 无 embedded）时保留二级栏，确保能在多个工具间切换
  syncHubDetailMode() {
    const app = document.getElementById('toolbox-app');
    const subBar = document.getElementById('sub-bar');
    if (!app) return;
    const detail = this.isHub() && this.isEmbedded() && !!this.state.tool;
    app.classList.toggle('tb-hub-detail', detail);
    // 直接用 inline style 覆盖，避免 #sub-bar / CSS 优先级问题导致隐藏失败
    if (subBar) subBar.style.display = detail ? 'none' : '';

    // 【文档处理入口专属】二级栏只保留当前工具标签，其余隐藏。
    // 判定依据 back=/tools/document —— 该参数仅出现在 toolbox_hub/document.html
    // 的 9 个工具链接中；图片/文本/识别/系统四大区入口是 ?hub=1#/image（无 back 参数），
    // 判定不成立，行为完全不受影响。state.category 守卫确保切到其他分类时自动恢复。
    const fromDocHub = this.isHub()
      && new URLSearchParams(window.location.search).get('back') === '/tools/document';
    if (subBar && fromDocHub && this.state.category === 'document') {
      subBar.querySelectorAll('.tb-sub-pill').forEach(pill => {
        pill.style.display = (this.state.tool && pill.dataset.tool !== this.state.tool)
          ? 'none' : '';
      });
    }
  },

  // ---------- 工具搜索（90+ 工具后，靠翻分类已经找不动了） ----------
  // 搜索框插在一级分类栏上方；输入即跨分类匹配工具名 / 帮助标题 / 说明 / 提示语。
  renderSearchBar() {
    const bar = document.getElementById('category-bar');
    if (!bar || document.getElementById('tb-search')) return;
    const wrap = document.createElement('div');
    wrap.className = 'tb-search-wrap';
    wrap.id = 'tb-search';
    wrap.innerHTML = `
      <span class="tb-search-icon">🔍</span>
      <input type="search" id="tb-search-input" class="tb-search-input"
             placeholder="搜索工具，如：水印 / 匹配 / 房贷 / 拼音" autocomplete="off">
      <button type="button" class="tb-search-clear" id="tb-search-clear" title="清空">✕</button>
    `;
    bar.parentNode.insertBefore(wrap, bar);

    const input = document.getElementById('tb-search-input');
    input.addEventListener('input', () => this.applySearch(input.value));
    document.getElementById('tb-search-clear').addEventListener('click', () => {
      input.value = '';
      this.applySearch('');
      input.focus();
    });
  },

  applySearch(q) {
    this.state.query = (q || '').trim();
    if (this.state.query) {
      this.renderSearchResults(this.state.query);
    } else if (this.state.category) {
      this.renderSubTools(this.state.category);
    }
  },

  renderSearchResults(q) {
    const bar = document.getElementById('sub-bar');
    if (!bar) return;
    const kw = q.toLowerCase();
    const catLabel = {};
    (TOOLBOX_CONFIG.categories || []).forEach(c => { catLabel[c.id] = c.label; });

    const hits = Object.entries(TOOLBOX_CONFIG.tools).filter(([id, t]) => {
      const hay = [id, t.label || '', (t.help || {}).title || '', (t.help || {}).desc || '']
        .concat((t.help || {}).tips || []).join(' ').toLowerCase();
      return hay.indexOf(kw) !== -1;
    });

    if (!hits.length) {
      bar.innerHTML = `<div class="tb-search-empty">没有匹配「${this.escapeHtml(q)}」的工具</div>`;
      return;
    }

    bar.innerHTML = `
      <div class="tb-sub-group">
        <div class="tb-sub-group-title">搜索结果 · ${hits.length} 个</div>
        <div class="tb-sub-group-items">
          ${hits.map(([id, t]) => {
            const cat = catLabel[t.category] || '';
            return `<button class="tb-sub-pill tb-tile" data-tool="${id}"
                       title="${this.escapeHtml(cat)}" onclick="ToolboxApp.jumpToTool('${id}')">
                      <span class="tb-tile-name">${t.label}</span>
                      <span class="tb-tile-badge ok">${this.escapeHtml(cat)}</span>
                    </button>`;
          }).join('')}
        </div>
      </div>
    `;
  },

  // 从搜索结果跳转：先把一级分类切过去（保证 tab 高亮与二级栏状态一致），再选工具
  jumpToTool(toolId) {
    const cfg = TOOLBOX_CONFIG.tools[toolId];
    if (!cfg) return;
    const catId = cfg.category;
    if (this.state.category !== catId) {
      document.querySelectorAll('.tb-category-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.cat === catId);
      });
      this.state.category = catId;
      this.renderSubTools(catId);
    }
    this.selectTool(toolId);
  },

  // ---------- 一级分类渲染 ----------
  renderCategories() {
    const bar = document.getElementById('category-bar');
    bar.innerHTML = TOOLBOX_CONFIG.categories.map(cat => `
      <button class="tb-category-tab" data-cat="${cat.id}" onclick="ToolboxApp.selectCategory('${cat.id}')">
        <span class="tab-icon">${cat.icon}</span>
        <span>${cat.label}</span>
      </button>
    `).join('');
  },

  // ---------- 选择一级分类 ----------
  selectCategory(catId) {
    this.state.category = catId;
    this.state.tool = null;
    this.state.tempId = null;
    this.state.files = [];

    // 更新 Tab 样式
    document.querySelectorAll('.tb-category-tab').forEach(tab => {
      tab.classList.toggle('active', tab.dataset.cat === catId);
    });

    // 渲染二级分类
    this.renderSubTools(catId);

    // 进入分类即自动选中第一个工具，让操作界面立即呈现（避免只有子栏、内容空白）
    const tools = getToolsByCategory(catId);
    if (tools.length > 0) {
      this.selectTool(tools[0].id);
    } else {
      document.getElementById('content-area').innerHTML = `
        <div class="tb-empty-state">
          <div class="empty-icon">🚧</div>
          <div class="empty-text">该分类暂无可用工具</div>
        </div>
      `;
    }

    this.syncHubDetailMode();
    this.updateHash();
  },

  // ---------- 二级导航渲染（按 group 分组，工具多时也不至于一行排到屏幕外） ----------
  renderSubTools(catId) {
    const bar = document.getElementById('sub-bar');
    if (!bar) return;
    const tools = getToolsByCategory(catId);
    const groups = (TOOLBOX_CONFIG.groups || {})[catId] || [];

    // 没配置分组的分类（如 automation）回落成原来的单行 pill
    if (!groups.length) {
      bar.innerHTML = `<div class="tb-sub-group"><div class="tb-sub-group-items">${
        tools.map(t => this.pillHtml(t)).join('')
      }</div></div>`;
      return;
    }

    const bucket = {};
    groups.forEach(g => { bucket[g.id] = []; });
    const fallback = groups[0].id;
    tools.forEach(t => {
      const gid = (TOOLBOX_CONFIG.toolGroups || {})[t.id] || fallback;
      (bucket[gid] || bucket[fallback]).push(t);
    });

    // 分组只用于排序（保持 PDF→Word/PPT→Excel→转换 的逻辑顺序），
    // 不再渲染分组标题：那行灰字点不了也收不起，纯属噪音。卡片直接铺成一片网格。
    bar.innerHTML = `<div class="tb-sub-group"><div class="tb-sub-group-items">${
      groups.filter(g => (bucket[g.id] || []).length)
            .map(g => bucket[g.id].map(t => this.pillHtml(t)).join(''))
            .join('')
    }</div></div>`;
  },

  // 单个工具卡片（沿用 Hub 大卡片排版：图标 + 名称 + 状态徽标）；
  // 「待实现 / 需装依赖」继续用角标提示，避免用户点了才发现用不了。
  // 保留 tb-sub-pill 类名：selectTool 高亮、syncHubDetailMode 隐藏逻辑都依赖它。
  pillHtml(t) {
    const todo = t.status === 'todo';
    const dep = t.requires ? ` title="需在服务器安装：${this.escapeHtml(t.requires)}"` : '';
    const cls = ['tb-sub-pill', 'tb-tile'];
    if (todo) cls.push('is-todo');
    if (t.requires) cls.push('is-dep');

    // label 形如 "📑 PDF 合并"：首个 token 是 emoji 图标，其余是名称。
    // 首 token 含字母/数字（如 "Excel 合并"）时不当作图标，避免把 "Excel" 吃掉。
    let icon = '', name = t.label || t.id;
    const parts = String(name).trim().split(/\s+/);
    if (parts.length > 1 && !/[A-Za-z0-9]/.test(parts[0])) {
      icon = parts.shift();
      name = parts.join(' ');
    }

    const badge = todo
      ? '<span class="tb-tile-badge todo">待实现</span>'
      : (t.requires
        ? '<span class="tb-tile-badge dep">需装依赖</span>'
        : '<span class="tb-tile-badge ok">可用</span>');

    return `
      <button class="${cls.join(' ')}" data-tool="${t.id}"${dep}
              onclick="ToolboxApp.selectTool('${t.id}')">
        ${icon ? `<span class="tb-tile-icon">${icon}</span>` : ''}
        <span class="tb-tile-name">${this.escapeHtml(name)}</span>
        ${badge}
      </button>`;
  },

  // ---------- 选择工具 ----------
  selectTool(toolId) {
    this.state.tool = toolId;
    this.state.tempId = null;
    this.state.files = [];

    // 更新 Pill 样式
    document.querySelectorAll('.tb-sub-pill').forEach(pill => {
      pill.classList.toggle('active', pill.dataset.tool === toolId);
    });

    // 渲染工具面板
    this.renderToolPanel(toolId);
    this.syncHubDetailMode();
    this.updateHash();
  },

  // ---------- 渲染工具面板（核心） ----------
  renderToolPanel(toolId) {
    const config = TOOLBOX_CONFIG.tools[toolId];
    if (!config) return;

    const area = document.getElementById('content-area');
    const help = config.help || {};
    const hasUpload = config.needUpload;
    const hasTextArea = config.hasTextArea;
    const hasSecondTextArea = config.hasSecondTextArea;
    const hasPasteArea = config.hasPasteArea;
    const hasColorPicker = config.hasColorPicker;
    const hasMarkdownPreview = config.hasMarkdownPreview;
    const hasClipboard = config.hasClipboard;
    const hasCustom = !!config.custom;
    const frontendOnly = !!config.frontendOnly;

    // 构建参数表单
    const paramsHtml = this.buildParamsHtml(config.params || []);

    // 构建帮助说明
    const helpHtml = this.buildHelpHtml(help);

    area.innerHTML = `
      <div class="tb-tool-panel active" data-tool="${toolId}">
        <!-- 帮助说明 -->
        ${helpHtml}

        <!-- 上传区（文件类工具） -->
        ${hasUpload ? `
          <div class="tb-glass-panel">
            <div id="workspace-bar"></div>
            <div id="upload-container"></div>
            <div id="file-list-container"></div>
          </div>
        ` : ''}

        <!-- 文本输入区（文本类工具） -->
        ${hasTextArea ? `
          <div class="tb-glass-panel">
            ${hasSecondTextArea ? `
              <div class="tb-textarea-pair">
                <textarea class="tb-textarea" id="text-input-a" placeholder="${config.textAreaPlaceholder || '输入文本...'}"></textarea>
                <textarea class="tb-textarea" id="text-input-b" placeholder="${config.secondTextAreaPlaceholder || '输入对比文本...'}"></textarea>
              </div>
            ` : hasMarkdownPreview ? `
              <div class="tb-md-wrap">
                <textarea class="tb-textarea" id="text-input" placeholder="${config.textAreaPlaceholder || '输入文本...'}"></textarea>
                <div class="tb-md-preview" id="md-preview"></div>
              </div>
            ` : `
              <textarea class="tb-textarea" id="text-input" placeholder="${config.textAreaPlaceholder || '输入文本...'}"></textarea>
            `}
          </div>
        ` : ''}

        <!-- 剪贴板历史（纯前端） -->
        ${hasClipboard ? `
          <div class="tb-glass-panel">
            <div id="clipboard-container"></div>
          </div>
        ` : ''}

        <!-- 自定义面板（纯前端计算类工具，如单位换算 / 房贷） -->
        ${hasCustom ? `
          <div class="tb-glass-panel">
            <div id="custom-mount"></div>
          </div>
        ` : ''}

        <!-- 粘贴区（截图识别） -->
        ${hasPasteArea ? `
          <div class="tb-glass-panel">
            <div class="tb-upload-zone" id="paste-zone" style="padding:32px;">
              <div class="upload-icon">📋</div>
              <div class="upload-text">Ctrl+V 粘贴截图</div>
              <div class="upload-hint">从剪贴板直接粘贴图片</div>
            </div>
          </div>
        ` : ''}

        <!-- 取色器 -->
        ${hasColorPicker ? `
          <div class="tb-glass-panel">
            <div id="color-picker-container" style="text-align:center;padding:20px;">
              <input type="file" id="color-file" accept="image/*" style="display:none;">
              <button class="tb-btn tb-btn-primary" onclick="document.getElementById('color-file').click()">
                🖼️ 选择图片
              </button>
              <canvas id="color-canvas" style="display:none;max-width:100%;margin-top:16px;border-radius:12px;box-shadow:4px 4px 8px var(--shadow-dark),-4px -4px 8px var(--shadow-light);"></canvas>
              <div id="color-info" style="margin-top:16px;font-size:14px;"></div>
            </div>
          </div>
        ` : ''}

        <!-- 参数面板 -->
        ${paramsHtml ? `
          <div class="tb-glass-panel">
            <div class="tb-params-grid">${paramsHtml}</div>
          </div>
        ` : ''}

        <!-- 进度条 -->
        <div id="progress-container"></div>

        <!-- 操作按钮 -->
        <div style="display:flex;gap:12px;justify-content:center;margin: 20px 0;">
          <button class="tb-btn tb-btn-primary" id="btn-process" onclick="ToolboxApp.process()">
            ⚡ 开始处理
          </button>
          <button class="tb-btn tb-btn-secondary" id="btn-clear" onclick="ToolboxApp.clear()">
            🔄 清空
          </button>
        </div>

        <!-- 结果区 -->
        <div id="result-container"></div>
      </div>
    `;

    // 初始化各组件
    if (hasUpload) {
      this.initUpload(config);
    }
    if (hasPasteArea) {
      this.initPaste();
    }
    if (hasColorPicker) {
      this.initColorPicker();
    }
    if (hasMarkdownPreview) {
      this.initMarkdown();
    }
    if (hasClipboard) {
      this.initClipboard();
    }
    if (hasCustom) {
      const mount = document.getElementById('custom-mount');
      const factory = (window.ToolboxCustom || {})[config.custom];
      if (mount && typeof factory === 'function') {
        try {
          factory(toolId, mount);
        } catch (e) {
          mount.innerHTML = `<div class="tb-help-caution">⚠️ 面板加载失败：${this.escapeHtml(String(e.message || e))}</div>`;
        }
      } else {
        mount && (mount.innerHTML = '<div class="tb-help-caution">⚠️ 该工具的面板脚本未加载</div>');
      }
    }

    // 纯前端工具（MD 预览 / 取色 / 剪贴板）：隐藏「开始处理」，避免点了返回 501
    if (frontendOnly) {
      const pbtn = document.getElementById('btn-process');
      if (pbtn) pbtn.style.display = 'none';
    }

    // 初始化参数联动（如 showWhen）
    this.initParamLinkage(config.params || []);

    // 结果区初始化
    this.resultPanel = new ResultPanel(document.getElementById('result-container'));
    this.progressBar = new ProgressBar(document.getElementById('progress-container'));
  },

  // ---------- 构建帮助说明 HTML ----------
  buildHelpHtml(help) {
    if (!help.title) return '';
    const tipsHtml = (help.tips || []).map(t => `<li>${this.escapeHtml(t)}</li>`).join('');
    const cautionHtml = help.caution ? `
      <div class="tb-help-caution">⚠️ ${this.escapeHtml(help.caution)}</div>
    ` : '';
    return `
      <div class="tb-help-banner">
        <button class="tb-help-toggle" onclick="this.classList.toggle('expanded'); this.nextElementSibling.classList.toggle('show')">
          <span>📖</span>
          <span>${this.escapeHtml(help.title)}</span>
          <span class="chevron">▼</span>
        </button>
        <div class="tb-help-body">
          <div class="tb-help-desc">${this.escapeHtml(help.desc || '')}</div>
          ${tipsHtml ? `<ul class="tb-help-tips">${tipsHtml}</ul>` : ''}
          <div class="tb-help-meta">
            ${help.input ? `<span>📥 输入: ${this.escapeHtml(help.input)}</span>` : ''}
            ${help.output ? `<span>📤 输出: ${this.escapeHtml(help.output)}</span>` : ''}
          </div>
          ${cautionHtml}
        </div>
      </div>
    `;
  },

  // ---------- 构建参数表单 HTML ----------
  buildParamsHtml(params) {
    if (!params || params.length === 0) return '';
    return params.map(p => {
      const tip = p.tip ? `<span class="param-tip">${this.escapeHtml(p.tip)}</span>` : '';
      let inputHtml = '';

      switch (p.type) {
        case 'range':
          inputHtml = `
            <div style="display:flex;align-items:center;gap:10px;">
              <input type="range" name="${p.name}" min="${p.min}" max="${p.max}" value="${p.value}"
                     oninput="this.nextElementSibling.textContent=this.value+'${p.unit||''}'">
              <span class="tb-range-value">${p.value}${p.unit||''}</span>
            </div>`;
          break;
        case 'select':
          inputHtml = `
            <select name="${p.name}">
              ${p.options.map(o => `<option value="${o.value}" ${o.value==p.value?'selected':''}>${this.escapeHtml(o.label)}</option>`).join('')}
            </select>`;
          break;
        case 'checkbox':
          inputHtml = `
            <label class="checkbox-wrap">
              <input type="checkbox" name="${p.name}" ${p.value ? 'checked' : ''}>
              <span>启用</span>
            </label>`;
          break;
        case 'password':
          inputHtml = `<input type="password" name="${p.name}" value="${p.value || ''}" placeholder="${p.placeholder || ''}" autocomplete="off">`;
          break;
        case 'number':
          inputHtml = `<input type="number" name="${p.name}" min="${p.min||''}" max="${p.max||''}" value="${p.value}" placeholder="${p.placeholder||''}">`;
          break;
        default:
          inputHtml = `<input type="text" name="${p.name}" value="${p.value||''}" placeholder="${p.placeholder||''}">`;
      }

      return `
        <div class="tb-param-item" data-param="${p.name}" ${p.showWhen ? `data-show-when='${JSON.stringify(p.showWhen)}'` : ''}>
          <div class="param-label">
            <span>${this.escapeHtml(p.label)}</span>
            ${p.unit ? `<span style="opacity:0.6;font-weight:400;">(${this.escapeHtml(p.unit)})</span>` : ''}
            ${tip}
          </div>
          ${inputHtml}
        </div>
      `;
    }).join('');
  },

  // ---------- 参数联动（showWhen） ----------
  // showWhen 支持两种写法：
  //   {field:"action", value:"rotate"}          单值
  //   {field:"action", values:["a","b"]}        多值（任一命中即显示）
  initParamLinkage(params) {
    params.forEach(p => {
      if (p.showWhen) {
        const trigger = document.querySelector(`[name="${p.showWhen.field}"]`);
        const target = document.querySelector(`.tb-param-item[data-param="${p.name}"]`);
        if (trigger && target) {
          const check = () => {
            const val = trigger.type === 'checkbox' ? trigger.checked : trigger.value;
            const w = p.showWhen;
            let hit;
            if (Array.isArray(w.values)) {
              hit = w.values.some(v => String(val) === String(v));
            } else {
              hit = String(val) === String(w.value);
            }
            target.style.display = hit ? 'block' : 'none';
          };
          trigger.addEventListener('change', check);
          trigger.addEventListener('input', check);
          check();
        }
      }
    });
  },

  // ---------- 初始化上传 ----------
  initUpload(config) {
    const uploadContainer = document.getElementById('upload-container');
    const fileListContainer = document.getElementById('file-list-container');

    this.uploadZone = new UploadZone(uploadContainer, {
      accept: config.accept,
      multiple: (config.maxCount || 1) > 1,
      maxSize: config.maxSize || 50,
      allowFolder: config.allowFolder,
      onFiles: (files) => {
        this.fileList.addFiles(files);
      }
    });

    this.fileList = new FileList(fileListContainer, {
      onChange: (files) => { this.state.files = files; },
      onReorder: config.id === 'document_pdf_merge' ? (files) => { this.state.files = files; } : null
    });

    this.initWorkspaceEntry();
  },

  // ---------- 工作区入口（持久文件池） ----------
  initWorkspaceEntry() {
    const bar = document.getElementById('workspace-bar');
    if (!bar || !window.ToolboxWorkspace) return;
    bar.innerHTML = `
      <div class="ws-entry">
        <span class="ws-entry-hint">文件已在工作区？直接取用，不用重复上传</span>
        <button type="button" class="ws-entry-btn" id="wsOpenBtn">📂 打开工作区</button>
      </div>
    `;
    const btn = bar.querySelector('#wsOpenBtn');
    if (!btn) return;
    btn.addEventListener('click', () => {
      window.ToolboxWorkspace.open((files) => {
        if (this.fileList) this.fileList.addFiles(files);
        else this.state.files = (this.state.files || []).concat(files);
      });
    });
  },

  // ---------- 初始化粘贴 ----------
  initPaste() {
    const zone = document.getElementById('paste-zone');
    zone.addEventListener('paste', (e) => {
      const items = e.clipboardData.items;
      const files = [];
      for (const item of items) {
        if (item.type.startsWith('image/')) {
          const file = item.getAsFile();
          if (file) files.push(file);
        }
      }
      if (files.length > 0) {
        this.state.files = files;
        zone.innerHTML = `
          <div class="upload-icon">✅</div>
          <div class="upload-text">已粘贴 ${files.length} 张图片</div>
          <div class="upload-hint">${files.map(f => f.name).join(', ')}</div>
        `;
      }
    });
    zone.tabIndex = 0;
    zone.focus();
  },

  // ---------- 初始化取色器 ----------
  initColorPicker() {
    const input = document.getElementById('color-file');
    const canvas = document.getElementById('color-canvas');
    const info = document.getElementById('color-info');
    const ctx = canvas.getContext('2d');

    input.addEventListener('change', (e) => {
      const file = e.target.files[0];
      if (!file) return;
      const img = new Image();
      img.onload = () => {
        canvas.width = img.width;
        canvas.height = img.height;
        ctx.drawImage(img, 0, 0);
        canvas.style.display = 'block';
      };
      img.src = URL.createObjectURL(file);
    });

    canvas.addEventListener('click', (e) => {
      const rect = canvas.getBoundingClientRect();
      const scaleX = canvas.width / rect.width;
      const scaleY = canvas.height / rect.height;
      const x = (e.clientX - rect.left) * scaleX;
      const y = (e.clientY - rect.top) * scaleY;
      const pixel = ctx.getImageData(x, y, 1, 1).data;
      const hex = '#' + [pixel[0], pixel[1], pixel[2]].map(v => v.toString(16).padStart(2, '0')).join('');
      const rgb = `rgb(${pixel[0]}, ${pixel[1]}, ${pixel[2]})`;
      info.innerHTML = `
        <div style="display:inline-block;width:40px;height:40px;border-radius:8px;background:${hex};box-shadow:2px 2px 4px var(--shadow-dark),-2px -2px 4px var(--shadow-light);vertical-align:middle;margin-right:12px;"></div>
        <span style="font-weight:600;">${hex}</span> · <span style="opacity:0.8;">${rgb}</span>
        <button class="tb-btn tb-btn-sm tb-btn-secondary" style="margin-left:12px;" onclick="ToolboxApp.copyText('${hex}')">📋 复制</button>
      `;
    });
  },

  // ---------- Markdown 实时预览（纯前端，不请求后端） ----------
  initMarkdown() {
    const ta = document.getElementById('text-input');
    const box = document.getElementById('md-preview');
    if (!ta || !box) return;
    const render = () => { box.innerHTML = ToolboxApp.renderMarkdown(ta.value); };
    ta.addEventListener('input', render);
    render();
  },

  // 极简 Markdown 渲染器：先转义再渲染，避免 HTML 注入
  renderMarkdown(src) {
    const esc = (s) => String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    let text = esc(src || '');

    // 代码块
    text = text.replace(/```([\s\S]*?)```/g, (m, code) =>
      `<pre class="md-pre"><code>${code.replace(/^\n/, '')}</code></pre>`);
    // 行内代码
    text = text.replace(/`([^`\n]+)`/g, '<code class="md-code">$1</code>');
    // 标题
    text = text.replace(/^(#{1,6})\s+(.*)$/gm, (m, h, t) => {
      const lv = h.length;
      return `<h${lv} class="md-h md-h${lv}">${t}</h${lv}>`;
    });
    // 分割线
    text = text.replace(/^\s*(---|\*\*\*)\s*$/gm, '<hr class="md-hr">');
    // 引用
    text = text.replace(/^&gt;\s?(.*)$/gm, '<blockquote class="md-quote">$1</blockquote>');
    // 粗体 / 斜体
    text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    // 链接 / 图片
    text = text.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img class="md-img" alt="$1" src="$2">');
    text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
      '<a class="md-link" href="$2" target="_blank" rel="noopener">$1</a>');
    // 列表
    text = text.replace(/(?:^|\n)((?:[-*+]\s+.*(?:\n|$))+)/g, (m, block) => {
      const items = block.trim().split('\n').map(li => `<li>${li.replace(/^[-*+]\s+/, '')}</li>`).join('');
      return `\n<ul class="md-ul">${items}</ul>\n`;
    });
    text = text.replace(/(?:^|\n)((?:\d+\.\s+.*(?:\n|$))+)/g, (m, block) => {
      const items = block.trim().split('\n').map(li => `<li>${li.replace(/^\d+\.\s+/, '')}</li>`).join('');
      return `\n<ol class="md-ol">${items}</ol>\n`;
    });
    // 段落
    text = text.split(/\n{2,}/).map(p => {
      const t = p.trim();
      if (!t) return '';
      if (/^<(h\d|ul|ol|blockquote|pre|hr)/.test(t)) return t;
      return `<p class="md-p">${t.replace(/\n/g, '<br>')}</p>`;
    }).join('\n');
    return text || '<p class="md-empty">左侧输入 Markdown，这里实时预览</p>';
  },

  // ---------- 剪贴板历史（纯前端 localStorage） ----------
  initClipboard() {
    const box = document.getElementById('clipboard-container');
    if (!box) return;
    const KEY = 'tb_clipboard_history';
    const load = () => {
      try { return JSON.parse(localStorage.getItem(KEY) || '[]'); } catch (e) { return []; }
    };
    const save = (list) => {
      try { localStorage.setItem(KEY, JSON.stringify(list.slice(0, 30))); } catch (e) { /* 忽略 */ }
    };
    const render = () => {
      const list = load();
      box.innerHTML = `
        <div class="tb-md-wrap" style="gap:10px;align-items:center;">
          <input class="tb-textarea" id="clip-input" placeholder="也可手动粘贴/输入内容后回车添加"
                 style="min-height:42px;flex:1;">
          <button class="tb-btn tb-btn-primary" id="clip-add">添加</button>
          <button class="tb-btn tb-btn-secondary" id="clip-clear">清空</button>
        </div>
        <div class="tb-clip-list">
          ${list.length ? list.map((it, i) => `
            <div class="tb-clip-item">
              <span class="tb-clip-time">${it.time || ''}</span>
              <span class="tb-clip-text">${this.escapeHtml(it.text || '')}</span>
              <button class="tb-btn tb-btn-sm tb-btn-secondary" data-i="${i}">复制</button>
            </div>`).join('') : '<div class="tb-clip-empty">暂无记录（在本页按 Ctrl+C 会自动记录）</div>'}
        </div>
      `;
      const add = () => {
        const el = document.getElementById('clip-input');
        const v = (el && el.value || '').trim();
        if (!v) return;
        const list2 = load();
        list2.unshift({ text: v, time: new Date().toLocaleTimeString('zh-CN', { hour12: false }) });
        save(list2);
        render();
      };
      const btnAdd = document.getElementById('clip-add');
      if (btnAdd) btnAdd.addEventListener('click', add);
      const input = document.getElementById('clip-input');
      if (input) input.addEventListener('keydown', (e) => { if (e.key === 'Enter') add(); });
      const btnClear = document.getElementById('clip-clear');
      if (btnClear) btnClear.addEventListener('click', () => { save([]); render(); });
      box.querySelectorAll('.tb-clip-item button').forEach(b => {
        b.addEventListener('click', () => {
          const idx = parseInt(b.dataset.i, 10);
          const it = load()[idx];
          if (it) this.copyText(it.text);
        });
      });
    };
    render();

    // 记录本页的复制动作
    if (!this._clipBound) {
      document.addEventListener('copy', () => {
        const sel = (window.getSelection() || '').toString().trim();
        if (!sel) return;
        const list = load();
        if (list.length && list[0].text === sel) return;
        list.unshift({ text: sel, time: new Date().toLocaleTimeString('zh-CN', { hour12: false }) });
        save(list);
        if (TOOLBOX_CONFIG.tools[this.state.tool] &&
            TOOLBOX_CONFIG.tools[this.state.tool].hasClipboard) {
          const box2 = document.getElementById('clipboard-container');
          if (box2) render();
        }
      });
      this._clipBound = true;
    }
  },

  // ---------- 收集参数 ----------
  collectParams() {
    const config = TOOLBOX_CONFIG.tools[this.state.tool];
    if (!config || !config.params) return {};
    const params = {};
    config.params.forEach(p => {
      const el = document.querySelector(`[name="${p.name}"]`);
      if (!el) return;
      if (p.type === 'checkbox') {
        params[p.name] = el.checked;
      } else if (p.type === 'number') {
        params[p.name] = parseFloat(el.value) || 0;
      } else {
        params[p.name] = el.value;
      }
    });
    return params;
  },

  // ---------- 收集文本输入 ----------
  collectText() {
    const config = TOOLBOX_CONFIG.tools[this.state.tool];
    if (!config) return {};
    const result = {};
    if (config.hasTextArea) {
      const ta = document.getElementById('text-input');
      if (ta) result.text = ta.value;
      const taA = document.getElementById('text-input-a');
      const taB = document.getElementById('text-input-b');
      if (taA) result.text_a = taA.value;
      if (taB) result.text_b = taB.value;
    }
    return result;
  },

  // ---------- 处理流程 ----------
  async process() {
    if (this.state.processing) return;
    const config = TOOLBOX_CONFIG.tools[this.state.tool];
    if (!config) return;

    const btn = document.getElementById('btn-process');
    btn.disabled = true;
    btn.textContent = '⏳ 处理中...';
    this.state.processing = true;

    try {
      // 1. 如有文件，先上传
      let tempId = this.state.tempId;
      if (config.needUpload && this.state.files.length > 0 && !tempId) {
        this.progressBar.show();
        this.progressBar.set(10, '正在上传文件...');
        tempId = await this.uploadFiles(this.state.files);
        this.state.tempId = tempId;
        this.progressBar.set(30, '上传完成，开始处理...');
      }

      // 2. 收集参数
      const params = { ...this.collectParams(), ...this.collectText() };

      // 3. 调用处理 API
      if (!config.needUpload || tempId) {
        if (config.stream) {
          await this.processStream(tempId, params);
        } else {
          this.progressBar.simulate(2000);
          const result = await this.callApi(this.state.category, this.state.tool, tempId, params);
          this.progressBar.finish();

          if (result.code === 200) {
            this.showResult(result.data, tempId);
          } else {
            this.toast(result.msg || '处理失败', 'error');
            this.progressBar.error(result.msg || '处理失败');
          }
        }
      } else if (config.needUpload && this.state.files.length === 0) {
        this.toast('请先上传文件', 'info');
      }
    } catch (err) {
      console.error(err);
      this.toast('请求出错: ' + err.message, 'error');
      this.progressBar.error(err.message);
    } finally {
      // 流式任务在 processStream 内部自行收尾（done/timeout），这里不动按钮
      if (!config.stream) {
        btn.disabled = false;
        btn.textContent = '⚡ 开始处理';
        this.state.processing = false;
      }
    }
  },

  // ---------- 流式长任务（SSE 进度回传） ----------
  async processStream(tempId, params) {
    const btn = document.getElementById('btn-process');
    const container = document.getElementById('result-container');
    container.innerHTML = `<div class="tb-stream-log" id="stream-log"></div>`;
    const logEl = document.getElementById('stream-log');

    const launch = await this.callApi(this.state.category, this.state.tool, tempId, params);
    if (launch.code !== 200) {
      this.toast(launch.msg || '启动失败', 'error');
      this.progressBar.error(launch.msg || '启动失败');
      if (btn) { btn.disabled = false; btn.textContent = '⚡ 开始处理'; }
      this.state.processing = false;
      return;
    }
    // 无文件流程（needUpload=false）本地 tempId 为 null，后端会自动分配
    // 工作目录并随启动结果返回真实 temp_id —— SSE 必须用它订阅。
    const streamId = (launch.data && launch.data.temp_id) || tempId;

    this.progressBar.show();
    this.progressBar.set(0, '正在启动自动化...');

    const finish = () => {
      if (btn) { btn.disabled = false; btn.textContent = '⚡ 开始处理'; }
      this.state.processing = false;
    };

    const url = `${window.TOOLBOX_API_BASE}/toolbox/api/${this.state.category}/${this.state.tool}/stream/${streamId}`;
    const es = new EventSource(url);

    // 停止按钮：长任务可随时中止（杀掉后台子进程树）
    const stopBtn = document.createElement('button');
    stopBtn.className = 'tb-stream-stop';
    stopBtn.textContent = '■ 停止';
    stopBtn.addEventListener('click', async () => {
      stopBtn.disabled = true;
      stopBtn.textContent = '■ 停止中...';
      try {
        await fetch(`${window.TOOLBOX_API_BASE}/toolbox/api/${this.state.category}/${this.state.tool}/cancel/${streamId}`, {
          method: 'POST', headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
      } catch (_) { /* 忽略网络错误，SSE 的 stopped 事件会收尾 */ }
    });
    container.appendChild(stopBtn);

    const stopStream = (reason) => {
      if (es) es.close();
      if (stopBtn && stopBtn.parentNode) stopBtn.parentNode.removeChild(stopBtn);
      const stopped = document.createElement('div');
      stopped.className = 'tb-stream-line tb-stream-stopped';
      stopped.textContent = '⛔ ' + (reason || '已停止') + '（详见上方日志）';
      logEl.appendChild(stopped);
      logEl.scrollTop = logEl.scrollHeight;
      this.progressBar.error(reason || '已停止');
      finish();
    };

    es.onmessage = (e) => {
      let ev;
      try { ev = JSON.parse(e.data); } catch (_) { return; }
      if (ev.type === 'log') {
        const line = document.createElement('div');
        line.className = 'tb-stream-line';
        line.textContent = ev.msg;
        logEl.appendChild(line);
        logEl.scrollTop = logEl.scrollHeight;
      } else if (ev.type === 'progress') {
        const d = ev.data || {};
        const pct = d.total ? Math.round((d.current || 0) / d.total * 100) : 0;
        const label = `进度 ${d.current || 0}/${d.total || 0} · ${d.status || ''}` + (d.company ? ` · ${d.company}` : '');
        this.progressBar.show();
        this.progressBar.set(pct, label);
      } else if (ev.type === 'done') {
        this.progressBar.finish();
        const done = document.createElement('div');
        done.className = 'tb-stream-line tb-stream-done';
        done.textContent = '✅ 执行完毕（详见上方日志）';
        logEl.appendChild(done);
        logEl.scrollTop = logEl.scrollHeight;
        if (stopBtn && stopBtn.parentNode) stopBtn.parentNode.removeChild(stopBtn);
        es.close();
        finish();
      } else if (ev.type === 'stopped') {
        stopStream('已停止');
      } else if (ev.type === 'timeout') {
        this.toast('进度拉取超时，请检查服务端日志', 'error');
        es.close();
        finish();
      }
    };
    es.onerror = () => {
      // EventSource 会自动重连；连接异常不打断本地 UI
    };
  },

  // ---------- 上传文件 ----------
  async uploadFiles(files) {
    const formData = new FormData();
    files.forEach(f => formData.append('files', f));
    const res = await fetch(`${window.TOOLBOX_API_BASE}/toolbox/api/upload`, {
      method: 'POST',
      body: formData,
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    });
    const data = await res.json();
    if (data.code !== 200) throw new Error(data.msg || '上传失败');
    return data.data.temp_id;
  },

  // ---------- 调用处理 API ----------
  async callApi(category, tool, tempId, params) {
    const res = await fetch(`${window.TOOLBOX_API_BASE}/toolbox/api/${category}/${tool}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: JSON.stringify({ temp_id: tempId, params })
    });
    return res.json();
  },

  // ---------- 显示结果 ----------
  showResult(data, tempId) {
    const config = TOOLBOX_CONFIG.tools[this.state.tool];

    // 文本结果
    if (data.result !== undefined) {
      this.resultPanel.showTextResult(data.result, config.help.title);
      return;
    }
    if (data.passwords) {
      this.resultPanel.showTextResult(data.passwords.join('\n'), '生成的密码');
      return;
    }
    if (data.uuids) {
      this.resultPanel.showTextResult(data.uuids.join('\n'), '生成的 UUID');
      return;
    }
    if (data.diff !== undefined) {
      this.resultPanel.showTextResult(data.diff, '差异对比结果');
      return;
    }
    if (data.matches) {
      const text = data.matches.map((m, i) =>
        `[${i+1}] 「${m.match}」 位置:${m.start}-${m.end} 捕获组:${JSON.stringify(m.groups)}`
      ).join('\n') || '无匹配结果';
      this.resultPanel.showTextResult(text, `找到 ${data.count} 处匹配`);
      return;
    }

    // 文件结果
    if (data.results && Array.isArray(data.results)) {
      this.resultPanel.showFileResults(data.results, tempId, config.help.title);
      return;
    }

    // 默认 JSON
    this.resultPanel.showTextResult(JSON.stringify(data, null, 2), '处理结果');
  },

  // ---------- 清空 ----------
  clear() {
    const config = TOOLBOX_CONFIG.tools[this.state.tool];
    if (!config) return;

    // 清空文件
    if (this.fileList) this.fileList.clear();
    this.state.files = [];
    this.state.tempId = null;

    // 清空文本
    if (config.hasTextArea) {
      const ta = document.getElementById('text-input');
      if (ta) ta.value = '';
      const taA = document.getElementById('text-input-a');
      const taB = document.getElementById('text-input-b');
      if (taA) taA.value = '';
      if (taB) taB.value = '';
    }

    // 重置参数
    if (config.params) {
      config.params.forEach(p => {
        const el = document.querySelector(`[name="${p.name}"]`);
        if (!el) return;
        if (p.type === 'checkbox') el.checked = p.value;
        else el.value = p.value;
        // 触发联动
        el.dispatchEvent(new Event('input'));
        el.dispatchEvent(new Event('change'));
      });
    }

    // 清空结果
    if (this.resultPanel) this.resultPanel.clear();
    if (this.progressBar) this.progressBar.hide();

    this.toast('已清空', 'info');
  },

  // ---------- URL Hash 管理 ----------
  updateHash() {
    // 深链恢复期间不要回写 hash，否则会与 hashchange 互相触发形成竞态
    if (this._restoring) return;
    if (this.state.category && this.state.tool) {
      window.location.hash = `#/${this.state.category}/${this.state.tool}`;
    }
  },

  restoreFromHash() {
    const hash = window.location.hash;
    const match = hash.match(/^#\/([^/]+)\/(.+)$/);
    if (match) {
      const [, cat, tool] = match;
      if (TOOLBOX_CONFIG.tools[tool] && TOOLBOX_CONFIG.tools[tool].category === cat) {
        this._restoring = true;
        try {
          if (this.state.category !== cat) {
            this.selectCategory(cat);
          }
          // 延迟确保二级分类已渲染
          setTimeout(() => {
            this._restoring = true;
            try { this.selectTool(tool); } finally { this._restoring = false; }
          }, 0);
        } finally {
          // 同步部分结束即解锁，setTimeout 内部自行管理
          this._restoring = false;
        }
        return;
      }
    }
    // 仅分类深链：#/image —— 工具箱 Hub 大区卡片跳转用
    const catOnly = hash.match(/^#\/([^/]+)$/);
    if (catOnly) {
      const cat = catOnly[1];
      if (TOOLBOX_CONFIG.categories.some(c => c.id === cat)) {
        if (this.state.category !== cat) {
          this.selectCategory(cat);
        } else {
          this.syncHubDetailMode();
        }
        return;
      }
    }

    // 默认选中第一个分类
    if (!this.state.category) {
      this.selectCategory(TOOLBOX_CONFIG.categories[0].id);
    }
  },

  // ---------- Toast ----------
  toast(message, type = 'info') {
    let el = document.getElementById('tb-toast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'tb-toast';
      el.className = 'tb-toast';
      document.body.appendChild(el);
    }
    el.className = `tb-toast ${type}`;
    el.textContent = message;
    requestAnimationFrame(() => el.classList.add('show'));
    setTimeout(() => el.classList.remove('show'), 3000);
  },

  // ---------- 复制文本 ----------
  copyText(textOrBtn) {
    let text = textOrBtn;
    if (typeof textOrBtn === 'object' && textOrBtn.target) {
      // 按钮点击，找旁边的 pre
      const panel = textOrBtn.target.closest('.tb-glass-panel');
      const pre = panel?.querySelector('.tb-result-preview');
      text = pre ? pre.textContent : '';
    }
    navigator.clipboard.writeText(text).then(() => {
      this.toast('已复制到剪贴板', 'success');
    }).catch(() => {
      // fallback
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      this.toast('已复制到剪贴板', 'success');
    });
  },

  escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
};

// ---------- 页面加载后初始化 ----------
document.addEventListener('DOMContentLoaded', () => {
  ToolboxApp.init();
});
