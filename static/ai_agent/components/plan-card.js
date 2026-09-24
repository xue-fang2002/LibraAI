/**
 * 计划卡组件：只负责「把计划渲染成卡片」和「读出用户改过的参数」。
 * 不碰网络请求，也不碰页面其它部分 —— 那些归 agent.js 管。
 * 显式挂到 window（本项目里 const 声明不会自动成为 window 属性）。
 */
(function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  var EXEC_LABEL = {
    inplace: '可直接执行',
    async: '后台执行（可看进度）',
    jump: '需跳转到对应模块',
    link: '打开网址',
  };

  function paramInput(spec, value) {
    var name = spec.name;
    var val = value == null ? '' : value;
    if (spec.type === 'enum' && spec.options && spec.options.length) {
      var opts = spec.options.map(function (o) {
        return '<option value="' + esc(o) + '"' +
          (String(o) === String(val) ? ' selected' : '') + '>' + esc(o) + '</option>';
      }).join('');
      return '<select data-param="' + esc(name) + '">' + opts + '</select>';
    }
    if (spec.type === 'bool') {
      return '<select data-param="' + esc(name) + '">' +
        '<option value="true"' + (val === true || val === 'true' ? ' selected' : '') + '>是</option>' +
        '<option value="false"' + (val === false || val === 'false' ? ' selected' : '') + '>否</option>' +
        '</select>';
    }
    var type = spec.type === 'number' ? 'number' : 'text';
    return '<input type="' + type + '" data-param="' + esc(name) +
      '" value="' + esc(val) + '">';
  }

  var PlanCard = {
    /** 把 plan 渲染进 container。onConfirm / onCancel 由调用方注入。 */
    render: function (container, plan, handlers) {
      var cap = plan.capability || {};
      var params = cap.params || [];
      var values = plan.params || {};

      var html = '<div class="plan-card">' +
        '<div class="plan-head">' +
          '<span class="plan-title">🧰 ' + esc(cap.label || plan.capability) + '</span>' +
          '<span class="plan-badge">' + esc(plan.module_name || plan.module || '') + '</span>' +
          '<span class="plan-badge">' + esc(EXEC_LABEL[plan.execution] || plan.execution) + '</span>' +
        '</div>' +
        (plan.reply ? '<div class="plan-reply">' + esc(plan.reply) + '</div>' : '') +
        '<div class="plan-params">' +
          params.map(function (p) {
            return '<div class="plan-param"><label>' + esc(p.label || p.name) +
              (p.required ? ' *' : '') + '</label>' + paramInput(p, values[p.name]) + '</div>';
          }).join('') +
        '</div>' +
        (plan.needs_files
          ? ((plan.execution === 'inplace' || plan.execution === 'async')
              ? '<div class="plan-files-pick" data-role="files"></div>'
              : '<div class="plan-warn">该能力需要文件，跳转后请在目标页面上传。</div>')
          : '') +
        (plan.missing && plan.missing.length
          ? '<div class="plan-warn">还缺必填参数：' + esc(plan.missing.join('、')) + '</div>' : '') +
        '<div class="plan-foot">' +
          '<button type="button" class="agent-btn primary" data-act="confirm">' +
            (plan.execution === 'inplace' ? '✓ 确认执行' : '→ 前往执行') +
          '</button>' +
          '<button type="button" class="agent-btn" data-act="cancel">✕ 取消</button>' +
        '</div>' +
        '<div class="plan-result" data-role="result"></div>' +
      '</div>';
      container.innerHTML = html;

      var card = container.querySelector('.plan-card');

      // 需要文件的就地执行能力：挂一个文件选择器（文件来自工具箱工作区）
      var fpBox = card.querySelector('[data-role="files"]');
      if (fpBox && window.AgentFilePicker) {
        card._filePicker = window.AgentFilePicker.create(fpBox);
      }

      card.querySelector('[data-act="confirm"]').addEventListener('click', function () {
        if (handlers && handlers.onConfirm) handlers.onConfirm(PlanCard.readParams(card));
      });
      card.querySelector('[data-act="cancel"]').addEventListener('click', function () {
        container.innerHTML = '';
        if (handlers && handlers.onCancel) handlers.onCancel();
      });
      return card;
    },

    /** 读出卡片上选中的工作区文件名（没有文件选择器时返回空数组）。 */
    getFiles: function (card) {
      if (!card || !card._filePicker) return [];
      try {
        return card._filePicker.getSelected() || [];
      } catch (e) {
        return [];
      }
    },

    /** 读出卡片上当前的参数值（用户可能改过）。 */
    readParams: function (card) {
      var out = {};
      var els = card.querySelectorAll('[data-param]');
      for (var i = 0; i < els.length; i++) {
        var el = els[i];
        var v = el.value;
        if (el.tagName === 'SELECT' && (v === 'true' || v === 'false')) {
          out[el.getAttribute('data-param')] = (v === 'true');
        } else if (el.type === 'number' && v !== '') {
          out[el.getAttribute('data-param')] = Number(v);
        } else {
          out[el.getAttribute('data-param')] = v;
        }
      }
      return out;
    },

    /** 把执行结果塞进卡片底部的 result 区。 */
    showResult: function (card, payload) {
      var box = card.querySelector('[data-role="result"]');
      if (!box) return;
      var res = payload && payload.result;
      if (res && res.kind === 'image') {
        box.innerHTML = '<img src="' + esc(res.url) + '" alt="' + esc(res.title || '图表') + '">';
        return;
      }
      if (res && res.kind === 'text') {
        box.innerHTML = '<div class="plan-text">' + esc(res.text) + '</div>' +
          '<button type="button" class="agent-btn tiny" data-act="copy">复制结果</button>';
        var copyBtn = box.querySelector('[data-act="copy"]');
        if (copyBtn) {
          copyBtn.addEventListener('click', function () {
            if (navigator.clipboard) navigator.clipboard.writeText(res.text || '');
            copyBtn.textContent = '已复制';
          });
        }
        return;
      }
      if (res && res.kind === 'files') {
        var lis = (res.files || []).map(function (f) {
          return '<li><a class="task-link" href="' + esc(f.url) + '">' +
            esc(f.name) + '</a></li>';
        }).join('');
        box.innerHTML = '<div class="plan-reply">已生成 ' + (res.files || []).length +
          ' 个文件</div><ul class="plan-files">' + lis + '</ul>' +
          '<a class="task-link" href="' + esc(res.zip) + '">打包下载 ZIP →</a>';
        return;
      }
      if (res && res.kind === 'link') {
        box.innerHTML = '<a class="task-link" href="' + esc(res.url) +
          '" target="_blank" rel="noopener">打开目标地址 →</a>';
        return;
      }
      box.innerHTML = '<div class="plan-reply">已完成。</div>';
    },

    showError: function (card, msg) {
      var box = card.querySelector('[data-role="result"]');
      if (box) box.innerHTML = '<div class="plan-warn">⚠️ ' + esc(msg) + '</div>';
    },

    busy: function (card, isBusy, text) {
      var btn = card.querySelector('[data-act="confirm"]');
      if (!btn) return;
      btn.disabled = !!isBusy;
      if (text) btn.textContent = text;
    },

    /** 切换到「后台执行中」视图：显示进度条占位。 */
    showAsync: function (card) {
      var box = card.querySelector('[data-role="result"]');
      if (!box) return;
      box.innerHTML = '<div class="plan-progress">' +
        '<div class="plan-progress-bar"><span class="plan-progress-fill"></span></div>' +
        '<div class="plan-progress-msg">后台执行中…</div>' +
      '</div>';
    },

    /** 根据轮询结果更新进度条与文案。 */
    updateProgress: function (card, prog, status) {
      var box = card.querySelector('[data-role="result"]');
      if (!box) return;
      var fill = box.querySelector('.plan-progress-fill');
      var msg = box.querySelector('.plan-progress-msg');
      var cur = Number(prog.current || 0);
      var total = Number(prog.total || 0);
      var pct = (total > 0) ? Math.round((cur / total) * 100) : (status === 'done' ? 100 : 0);
      if (fill) fill.style.width = Math.max(0, Math.min(100, pct)) + '%';
      if (msg) {
        var label = (status === 'done') ? '执行完成' : (status === 'failed' ? '执行失败' : '后台执行中…');
        msg.textContent = (prog.message ? prog.message + '  ' : '') + label +
          (total > 0 ? '  (' + cur + '/' + total + ')' : '');
      }
    },
  };

  window.AgentPlanCard = PlanCard;
})();
