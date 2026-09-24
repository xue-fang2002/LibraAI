/**
 * AI 工具助手（Phase 3）
 * 自然语言 -> 后端生成 JSON 计划 -> 计划预览 -> 用户确认后执行。
 * 执行 = 切到对应工具 + 填参数 + 从工作区取文件 + 调用既有 process() 链路。
 */
(function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  const AI = {
    plan: null,
    busy: false,
    modal: null,
    built: false,

    ensure() {
      if (this.built) return;

      // 悬浮入口按钮
      const fab = document.createElement('button');
      fab.type = 'button';
      fab.className = 'aa-fab';
      fab.id = 'aiAsstFab';
      fab.textContent = '🤖 AI 助手';
      document.body.appendChild(fab);
      fab.addEventListener('click', () => this.open());

      // 弹窗
      const mask = document.createElement('div');
      mask.className = 'aa-mask';
      mask.id = 'aaMask';
      mask.style.display = 'none';
      mask.innerHTML = `
        <div class="aa-modal">
          <div class="aa-head">
            <span class="aa-title">🤖 AI 工具助手</span>
            <button type="button" class="aa-x" id="aaClose" title="关闭">×</button>
          </div>
          <p class="aa-hint">描述你想做的事，例如：「把工作区里的图片压缩到 60% 再转成 PNG」</p>
          <textarea class="aa-text" id="aaText" placeholder="输入需求…"></textarea>
          <div class="aa-actions">
            <button type="button" class="aa-btn primary" id="aaGenBtn">✨ 生成计划</button>
          </div>
          <div class="aa-status" id="aaStatus"></div>
          <div class="aa-plan" id="aaPlan" style="display:none;"></div>
          <div class="aa-actions" id="aaExecBar" style="display:none;">
            <button type="button" class="aa-btn" id="aaReGenBtn">↺ 重新生成</button>
            <button type="button" class="aa-btn primary" id="aaExecBtn">✓ 确认执行</button>
          </div>
          <input type="hidden" id="aaDummy">
        </div>
      `;
      document.body.appendChild(mask);
      this.modal = mask;
      this.built = true;

      mask.addEventListener('click', (e) => { if (e.target === mask) this.close(); });
      mask.querySelector('#aaClose').addEventListener('click', () => this.close());
      mask.querySelector('#aaGenBtn').addEventListener('click', () => this.genPlan());
      mask.querySelector('#aaReGenBtn').addEventListener('click', () => this.genPlan());
      mask.querySelector('#aaExecBtn').addEventListener('click', () => this.execute());
      mask.querySelector('#aaText').addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); this.genPlan(); }
      });
    },

    open() {
      this.ensure();
      this.plan = null;
      this.modal.querySelector('#aaPlan').style.display = 'none';
      this.modal.querySelector('#aaExecBar').style.display = 'none';
      this.setStatus('');
      this.modal.style.display = 'flex';
      setTimeout(() => this.modal.querySelector('#aaText').focus(), 60);
    },

    close() {
      if (this.modal) this.modal.style.display = 'none';
    },

    setStatus(msg, kind) {
      const el = this.modal.querySelector('#aaStatus');
      el.textContent = msg || '';
      el.className = 'aa-status' + (kind ? ' aa-status-' + kind : '');
    },

    // ---------- 工具摘要（给后端 prompt 用） ----------
    buildToolList() {
      const cats = {};
      (TOOLBOX_CONFIG.categories || []).forEach(c => { cats[c.id] = c.label; });
      const tools = [];
      Object.entries(TOOLBOX_CONFIG.tools || {}).forEach(([id, t]) => {
        tools.push({
          id: id,
          label: t.label,
          category: cats[t.category] || t.category,
          needUpload: !!t.needUpload,
          params: (t.params || []).map(p => ({
            name: p.name,
            label: p.label,
            type: p.type || 'text',
            min: p.min,
            max: p.max,
            options: (p.options || []).map(o =>
              typeof o === 'object' ? { value: o.value, label: o.label } : { value: o, label: o })
          }))
        });
      });
      return tools;
    },

    // ---------- 生成计划 ----------
    genPlan() {
      const text = (this.modal.querySelector('#aaText').value || '').trim();
      if (!text) { this.setStatus('请先输入需求描述', 'err'); return; }
      if (this.busy) return;
      this.busy = true;
      this.plan = null;
      this.modal.querySelector('#aaPlan').style.display = 'none';
      this.modal.querySelector('#aaExecBar').style.display = 'none';
      this.setStatus('AI 正在生成计划…');

      fetch('/toolbox/api/ai/plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        body: JSON.stringify({ text: text, tools: this.buildToolList() })
      })
        .then(r => r.json())
        .then(j => {
          if (!j || j.code !== 200) throw new Error((j && j.msg) || '生成失败');
          const data = j.data || {};
          if (!data.plan) {
            this.setStatus('未找到匹配工具：' + (data.reply || '请换个说法描述'), 'err');
            return;
          }
          this.plan = data.plan;
          this.renderPlan(data.plan);
          this.setStatus('计划已生成，请确认后执行', 'ok');
        })
        .catch(e => this.setStatus('生成失败：' + (e.message || e), 'err'))
        .finally(() => { this.busy = false; });
    },

    // ---------- 计划预览 ----------
    renderPlan(plan) {
      const box = this.modal.querySelector('#aaPlan');
      const cfg = (TOOLBOX_CONFIG.tools || {})[plan.tool] || {};

      const paramRows = Object.entries(plan.params || {}).map(([k, v]) => {
        const def = (cfg.params || []).find(p => p.name === k) || {};
        return `<div class="aa-param"><span>${esc(def.label || k)}</span><b>${esc(String(v))}</b></div>`;
      }).join('');

      const filesHtml = (plan.files && plan.files.length)
        ? plan.files.map(f => `<span class="aa-file" title="${esc(f)}">📄 ${esc(f)}</span>`).join('')
        : '<span class="aa-nofile">（不使用工作区文件）</span>';

      box.innerHTML = `
        <div class="aa-plan-head">执行计划</div>
        ${plan.reply ? `<div class="aa-reply">${esc(plan.reply)}</div>` : ''}
        <div class="aa-tool">🧰 ${esc(plan.label || plan.tool)}<span class="aa-cat">${esc(plan.category || '')}</span></div>
        <div class="aa-params">${paramRows || '<div class="aa-nofile">（无参数）</div>'}</div>
        <div class="aa-files">${filesHtml}</div>
      `;
      box.style.display = 'block';
      this.modal.querySelector('#aaExecBar').style.display = 'flex';
    },

    // ---------- 确认执行 ----------
    async execute() {
      const plan = this.plan;
      if (!plan || this.busy) return;
      const cfg = (TOOLBOX_CONFIG.tools || {})[plan.tool];
      if (!cfg) { this.setStatus('工具配置缺失', 'err'); return; }

      this.busy = true;
      this.setStatus('正在切换工具并应用计划…');
      try {
        // 1. 切到目标工具
        ToolboxApp.selectCategory(cfg.category);
        ToolboxApp.selectTool(plan.tool);

        // 2. 填参数（赋值后派发 input/change，让 range 等控件的联动标签同步刷新）
        (cfg.params || []).forEach(p => {
          if (!(p.name in (plan.params || {}))) return;
          const el = document.querySelector(`[name="${p.name}"]`);
          if (!el) return;
          if (p.type === 'checkbox') el.checked = !!plan.params[p.name];
          else el.value = plan.params[p.name];
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
        });

        // 3. 从工作区取文件
        if (cfg.needUpload && plan.files && plan.files.length) {
          const files = [];
          const failed = [];
          for (const name of plan.files) {
            try {
              const r = await fetch('/toolbox/api/workspace/file/' + encodeURIComponent(name));
              if (!r.ok) throw new Error();
              const blob = await r.blob();
              files.push(new File([blob], name, { type: blob.type || 'application/octet-stream' }));
            } catch (e) { failed.push(name); }
          }
          if (failed.length) {
            this.setStatus('这些文件不在工作区：' + failed.join('、'), 'err');
            this.busy = false;
            return;
          }
          if (ToolboxApp.fileList) ToolboxApp.fileList.addFiles(files);
        } else if (cfg.needUpload && (!plan.files || !plan.files.length) && ToolboxApp.state.files.length === 0) {
          this.setStatus('该工具需要文件，但计划里没有指定工作区文件', 'err');
          this.busy = false;
          return;
        }

        // 4. 执行既有处理链路
        this.close();
        ToolboxApp.process();
      } catch (e) {
        this.setStatus('执行失败：' + (e.message || e), 'err');
      } finally {
        this.busy = false;
      }
    }
  };

  window.ToolboxAI = AI;

  // 页面加载即创建悬浮入口（ensure 幂等，open 时再次调用也安全）
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => AI.ensure());
  } else {
    AI.ensure();
  }
})();
