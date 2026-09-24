/**
 * 办公自动化 AI 助手
 * 自然语言 -> 后端生成 JSON 计划 -> 计划预览 -> 确认后自动执行。
 *
 * 安全约定：
 * - 输入文件（zip/Excel）只能由用户在页面上传，AI 不输出任何路径/文件名；
 *   执行前会校验 `OTState.uploadedFiles`，缺文件就阻断并提示，不会盲跑。
 * - 执行走的是各模块原本的 executeXxx()，不另起一套调用逻辑。
 */
(function () {
  'use strict';

  // ---------------- 能力注册表（与 index.html / script.js 的元素一一对应） ----------------
  const OT_AI_CAPS = [
    {
      id: 'mod1-folders', label: '批量建文件夹', group: '批量创建',
      tab: 'mod1', subtab: 'm1-folders', exec: 'executeMod1Folders',
      desc: '按 Excel 某一列的名称批量创建文件夹，结果打包下载',
      params: [
        { name: 'column_header', label: '名称所在列标题', type: 'text', sel: '#m1-folders-col' },
        { name: 'target_dirs', label: '目标子目录（每行一个）', type: 'textarea', sel: '#m1-folders-dirs' }
      ],
      files: [{ key: 'm1-folders', label: '名称Excel' }]
    },
    {
      id: 'mod1-files', label: '批量建文件', group: '批量创建',
      tab: 'mod1', subtab: 'm1-files', exec: 'executeMod1Files',
      desc: '按 Excel 某一列的名称批量创建 excel/txt/word 文件',
      params: [
        { name: 'column_header', label: '名称所在列标题', type: 'text', sel: '#m1-files-col' },
        { name: 'target_dirs', label: '目标子目录（每行一个）', type: 'textarea', sel: '#m1-files-dirs' },
        { name: 'file_type', label: '文件类型', type: 'enum', radio: 'm1-file-type', options: ['excel', 'txt', 'word'] }
      ],
      files: [{ key: 'm1-files', label: '名称Excel' }]
    },
    {
      id: 'mod1-sheets', label: '批量建分表', group: '批量创建',
      tab: 'mod1', subtab: 'm1-sheets', exec: 'executeMod1Sheets',
      desc: '给多个 Excel 批量添加分表，可选用模板填充',
      params: [
        { name: 'column_header', label: '分表名称列标题', type: 'text', sel: '#m1-sheets-col' },
        { name: 'fill', label: '用模板填充内容', type: 'bool', sel: '#m1-sheets-fill' }
      ],
      files: [
        { key: 'm1-sheets', label: '名称Excel' },
        { key: 'm1-sheets-targets', label: '目标Excel（可多选）' }
      ]
    },
    {
      id: 'mod2-extract', label: '提取清单', group: '提取清单',
      tab: 'mod2', subtab: null, exec: 'executeMod2Extract',
      desc: '提取压缩包内文件/文件夹清单，可再导出成表格',
      params: [
        { name: 'mode', label: '提取对象', type: 'enum', radio: 'm2-mode', options: ['all', 'files', 'filter'] },
        { name: 'recursive', label: '含子文件夹', type: 'bool', sel: '#m2-recursive' },
        { name: 'suffix', label: '按后缀筛选（如 xlsx）', type: 'text', sel: '#m2-suffix' },
        { name: 'remove_suffix', label: '去掉文件名后缀', type: 'bool', sel: '#m2-remove-suffix' }
      ],
      files: [{ key: 'm2-folder', label: '文件夹压缩包(zip)' }]
    },
    {
      id: 'mod3-simple', label: '简单替换改名', group: '批量改名',
      tab: 'mod3', subtab: 'm3-simple', exec: 'executeMod3Simple',
      desc: '把文件名里的某段文字批量替换成另一段',
      params: [
        { name: 'old_text', label: '查找文字', type: 'text', sel: '#m3-simple-old' },
        { name: 'new_text', label: '替换为', type: 'text', sel: '#m3-simple-new' }
      ],
      files: [{ key: 'm3-simple-folder', label: '文件夹压缩包(zip)' }]
    },
    {
      id: 'mod3-excel', label: 'Excel对照改名', group: '批量改名',
      tab: 'mod3', subtab: 'm3-excel', exec: 'executeMod3Excel',
      desc: '按 Excel 对照表（原名列/新名列）批量重命名',
      params: [
        { name: 'old_col', label: '原文件名列标题', type: 'text', sel: '#m3-excel-oldcol' },
        { name: 'new_col', label: '新文件名列标题', type: 'text', sel: '#m3-excel-newcol' }
      ],
      files: [
        { key: 'm3-excel-folder', label: '文件夹压缩包(zip)' },
        { key: 'm3-excel', label: '对照Excel' }
      ]
    },
    {
      id: 'mod4-cell', label: '单元格替换', group: 'Excel专项',
      tab: 'mod4', subtab: 'm4-cell', exec: 'executeMod4Cell',
      desc: '批量替换多个 Excel 指定单元格的内容（需服务器装 Office）',
      params: [
        { name: 'cell_addr', label: '单元格地址', type: 'text', sel: '#m4-cell-addr' },
        { name: 'old_text', label: '查找文字', type: 'text', sel: '#m4-cell-old' },
        { name: 'new_text', label: '替换为', type: 'text', sel: '#m4-cell-new' }
      ],
      files: [{ key: 'm4-cell-folder', label: '文件夹压缩包(zip)' }]
    },
    {
      id: 'mod4-pdf', label: 'Excel转PDF', group: 'Excel专项',
      tab: 'mod4', subtab: 'm4-pdf', exec: 'executeMod4Pdf',
      desc: '批量把 Excel 转成 PDF（需服务器装 Office）',
      params: [],
      files: [{ key: 'm4-pdf-folder', label: '文件夹压缩包(zip)' }]
    },
    {
      id: 'mod4-delete', label: '删除分表', group: 'Excel专项',
      tab: 'mod4', subtab: 'm4-delete', exec: 'executeMod4Delete',
      desc: '批量删除 Excel 里的指定分表（不可逆，谨慎使用）',
      params: [
        { name: 'sheet_names', label: '待删除分表名（逗号分隔）', type: 'text', sel: '#m4-delete-names' }
      ],
      files: [{ key: 'm4-delete-folder', label: '文件夹压缩包(zip)' }]
    },
    {
      id: 'mod5-movecopy', label: '移动/复制', group: '文件搬运',
      tab: 'mod5', subtab: 'm5-movecopy', exec: 'executeMod5MoveCopy',
      desc: '按 Excel 映射表批量移动或复制文件',
      params: [
        { name: 'action', label: '动作', type: 'enum', radio: 'm5-action', options: ['copy', 'move'] },
        { name: 'recursive', label: '含子文件夹', type: 'bool', sel: '#m5-recursive' }
      ],
      files: [
        { key: 'm5-folder', label: '源文件夹压缩包(zip)' },
        { key: 'm5', label: '映射Excel' }
      ]
    },
    {
      id: 'mod5-paths', label: '路径提取', group: '文件搬运',
      tab: 'mod5', subtab: 'm5-paths', exec: 'executeMod5Paths',
      desc: '提取压缩包内所有文件的路径清单（绝对/相对）',
      params: [
        { name: 'path_type', label: '路径类型', type: 'enum', radio: 'm5-paths-type', options: ['absolute', 'relative'] },
        { name: 'recursive', label: '含子文件夹', type: 'bool', sel: '#m5-paths-recursive' }
      ],
      files: [{ key: 'm5-paths-folder', label: '文件夹压缩包(zip)' }]
    }
  ];

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  const OA = {
    plan: null,
    busy: false,
    modal: null,
    built: false,

    ensure() {
      if (this.built) return;

      const fab = document.createElement('button');
      fab.type = 'button';
      fab.className = 'oa-fab';
      fab.id = 'oaFab';
      fab.textContent = '🤖 AI 助手';
      document.body.appendChild(fab);
      fab.addEventListener('click', () => this.open());

      const mask = document.createElement('div');
      mask.className = 'oa-mask';
      mask.id = 'oaMask';
      mask.style.display = 'none';
      mask.innerHTML = `
        <div class="oa-modal">
          <div class="oa-head">
            <span class="oa-title">🤖 办公 AI 助手</span>
            <button type="button" class="oa-x" id="oaClose" title="关闭">×</button>
          </div>
          <p class="oa-hint">描述你要做的事，例如：「把文件名里的 2023 全部改成 2024」</p>
          <textarea class="oa-text" id="oaText" placeholder="输入需求…"></textarea>
          <div class="oa-actions">
            <button type="button" class="oa-btn primary" id="oaGenBtn">✨ 生成计划</button>
          </div>
          <div class="oa-status" id="oaStatus"></div>
          <div class="oa-plan" id="oaPlan" style="display:none;"></div>
          <div class="oa-actions" id="oaExecBar" style="display:none;">
            <button type="button" class="oa-btn" id="oaReGenBtn">↺ 重新生成</button>
            <button type="button" class="oa-btn primary" id="oaExecBtn">✓ 确认执行</button>
          </div>
        </div>
      `;
      document.body.appendChild(mask);
      this.modal = mask;
      this.built = true;

      mask.addEventListener('click', (e) => { if (e.target === mask) this.close(); });
      mask.querySelector('#oaClose').addEventListener('click', () => this.close());
      mask.querySelector('#oaGenBtn').addEventListener('click', () => this.genPlan());
      mask.querySelector('#oaReGenBtn').addEventListener('click', () => this.genPlan());
      mask.querySelector('#oaExecBtn').addEventListener('click', () => this.execute());
      mask.querySelector('#oaText').addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); this.genPlan(); }
      });
    },

    open() {
      this.ensure();
      this.plan = null;
      this.modal.querySelector('#oaPlan').style.display = 'none';
      this.modal.querySelector('#oaExecBar').style.display = 'none';
      this.setStatus('');
      this.modal.style.display = 'flex';
      setTimeout(() => this.modal.querySelector('#oaText').focus(), 60);
    },

    close() {
      if (this.modal) this.modal.style.display = 'none';
    },

    setStatus(msg, kind) {
      const el = this.modal.querySelector('#oaStatus');
      el.textContent = msg || '';
      el.className = 'oa-status' + (kind ? ' oa-status-' + kind : '');
    },

    // 给后端 prompt 用的能力摘要（不含任何 DOM 细节）
    buildCapList() {
      return OT_AI_CAPS.map(c => ({
        id: c.id,
        label: c.label,
        group: c.group,
        desc: c.desc,
        params: (c.params || []).map(p => ({
          name: p.name,
          label: p.label,
          type: p.type,
          options: p.options || undefined
        }))
      }));
    },

    capById(id) {
      return OT_AI_CAPS.find(c => c.id === id) || null;
    },

    // 注意：OTState 是 const 声明，不挂 window，必须用裸标识符引用
    uploadedMap() {
      try {
        return (typeof OTState !== 'undefined' && OTState.uploadedFiles) || {};
      } catch (e) {
        return {};
      }
    },

    hasFile(key) {
      return !!this.uploadedMap()[key];
    },

    genPlan() {
      const text = (this.modal.querySelector('#oaText').value || '').trim();
      if (!text) { this.setStatus('请先输入需求描述', 'err'); return; }
      if (this.busy) return;
      this.busy = true;
      this.plan = null;
      this.modal.querySelector('#oaPlan').style.display = 'none';
      this.modal.querySelector('#oaExecBar').style.display = 'none';
      this.setStatus('AI 正在生成计划…');

      fetch('/office_tools/api/ai/plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        body: JSON.stringify({ text: text, caps: this.buildCapList() })
      })
        .then(r => r.json())
        .then(j => {
          if (!j || j.code !== 200) throw new Error((j && j.msg) || '生成失败');
          const data = j.data || {};
          if (!data.plan) {
            this.setStatus('未找到匹配功能：' + (data.reply || '请换个说法描述'), 'err');
            return;
          }
          this.plan = data.plan;
          this.renderPlan(data.plan);
          this.setStatus('计划已生成，请确认后执行', 'ok');
        })
        .catch(e => this.setStatus('生成失败：' + (e.message || e), 'err'))
        .finally(() => { this.busy = false; });
    },

    renderPlan(plan) {
      const cap = this.capById(plan.tool) || { params: [], files: [] };
      const box = this.modal.querySelector('#oaPlan');

      const paramRows = Object.entries(plan.params || {}).map(([k, v]) => {
        const def = (cap.params || []).find(p => p.name === k) || {};
        let shown = v;
        if (def.type === 'bool') shown = v ? '是' : '否';
        return `<div class="oa-param"><span>${esc(def.label || k)}</span><b>${esc(String(shown))}</b></div>`;
      }).join('');

      const filesHtml = (cap.files || []).map(f => {
        const ready = this.hasFile(f.key);
        return `<span class="oa-file ${ready ? '' : 'oa-file-miss'}" title="${esc(f.label)}">${ready ? '✓' : '✗'} ${esc(f.label)}</span>`;
      }).join('');

      box.innerHTML = `
        <div class="oa-plan-head">执行计划</div>
        ${plan.reply ? `<div class="oa-reply">${esc(plan.reply)}</div>` : ''}
        <div class="oa-tool">🧰 ${esc(plan.label || plan.tool)}<span class="oa-group">${esc(cap.group || '')}</span></div>
        <div class="oa-params">${paramRows || '<div class="oa-none">（该能力无需参数）</div>'}</div>
        <div class="oa-files">${filesHtml || '<span class="oa-none">（无需上传文件）</span>'}</div>
        <div class="oa-tip">输入文件需你自己在页面上传；未就绪时执行会被拦下并提示。</div>
      `;
      box.style.display = 'block';
      this.modal.querySelector('#oaExecBar').style.display = 'flex';
    },

    setParam(p, val) {
      if (p.type === 'bool') {
        const el = document.querySelector(p.sel);
        if (el) {
          el.checked = !!val;
          el.dispatchEvent(new Event('change', { bubbles: true }));
        }
        return;
      }
      if (p.type === 'enum') {
        const radios = document.querySelectorAll(`input[name="${p.radio}"]`);
        let hit = false;
        radios.forEach(r => {
          const match = String(r.value) === String(val);
          if (match) { r.checked = true; hit = true; }
        });
        if (!hit && radios.length) radios[0].checked = true;
        radios.forEach(r => r.dispatchEvent(new Event('change', { bubbles: true })));
        return;
      }
      const el = document.querySelector(p.sel);
      if (el) {
        el.value = val;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }
    },

    async execute() {
      const plan = this.plan;
      if (!plan || this.busy) return;
      const cap = this.capById(plan.tool);
      if (!cap) { this.setStatus('能力配置缺失', 'err'); return; }

      this.busy = true;
      this.setStatus('正在切换功能并应用计划…');
      try {
        // 1. 切到对应 tab / 子 tab
        if (typeof switchTab === 'function') switchTab(cap.tab);
        if (cap.subtab && typeof switchSubTab === 'function') switchSubTab(cap.tab, cap.subtab);

        // 2. 填参数
        (cap.params || []).forEach(p => {
          if (!(p.name in (plan.params || {}))) return;
          this.setParam(p, plan.params[p.name]);
        });

        // 3. 校验输入文件是否已上传（AI 不碰文件，缺了就拦下）
        const missing = (cap.files || []).filter(f => !this.hasFile(f.key));
        if (missing.length) {
          this.setStatus('已切到「' + cap.label + '」并填好参数，还需你上传：' +
            missing.map(f => f.label).join('、'), 'err');
          this.close();
          return;
        }

        // 4. 执行该模块原本的处理函数
        const fn = window[cap.exec];
        if (typeof fn !== 'function') {
          this.setStatus('未找到执行函数：' + cap.exec, 'err');
          return;
        }
        this.close();
        await fn();
      } catch (e) {
        this.setStatus('执行失败：' + (e.message || e), 'err');
      } finally {
        this.busy = false;
      }
    }
  };

  window.OfficeAI = OA;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => OA.ensure());
  } else {
    OA.ensure();
  }
})();
