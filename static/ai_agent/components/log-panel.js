/**
 * 执行日志面板：单一日志框，只显示「当前任务」的日志。
 *
 * 交互（用户要求：日志做成一个框，只显示当前任务日志，不要历史任务堆叠）：
 *  - 面板只有一条记录 = 当前任务（仅正在执行的任务；历史已完成任务不回显）
 *  - 上半部分是任务头：任务描述 + 状态徽标 + 成功/失败统计 + 下载入口
 *  - 下半部分是唯一一个滚动日志框，实时追加日志行，跑完补全明细
 *  - 页面刷新后日志框回到空态，内容不常留（用户要求）
 */
(function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  var STATUS_BADGE = {
    pending:  ['待确认', ''],
    confirmed:['已确认', ''],
    running:  ['执行中', 'running'],
    done:     ['已完成', 'ok'],
    failed:   ['失败', 'err'],
    cancelled:['已取消', ''],
  };

  var LogPanel = {
    el: null,
    liveTask: null,   // 当前正在实时跟踪的任务 {id, lastLine}

    init: function (container) {
      this.el = container;
      this.load();
    },

    /** 拉最近任务：只在有「正在执行」的任务时才显示，否则保持空态。
     *  用户要求：日志内容不要一直留着——历史已完成任务不随页面加载回显。 */
    load: function () {
      var self = this;
      if (!self.el) return;
      fetch('/agent/api/recent-logs?limit=10', {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          var list = ((j.data || {}).list) || [];
          var running = null;
          for (var i = 0; i < list.length; i++) {
            if (list[i].status === 'running') { running = list[i]; break; }
          }
          if (running) {
            self.renderTask(running);
          } else {
            self.el.innerHTML = '<div class="log-empty">暂无正在执行的任务。下达任务后，这里会实时显示当前任务的日志。</div>';
          }
        })
        .catch(function (e) {
          self.el.innerHTML = '<div class="log-empty">日志加载失败：' + esc(e.message) + '</div>';
        });
    },

    /** 结果明细（成功/失败逐条），完成态才显示 */
    itemsHtml: function (t) {
      var res = (t.result && t.result.items && t.result.items.length) ? t.result : null;
      if (!res) return '';
      return '<div class="log-items">' + res.items.map(function (it) {
        return '<div class="log-item ' + (it.ok ? 'is-ok' : 'is-err') + '">' +
          '<span class="log-item-name">' + esc(it.index + '. ' + it.name) + '</span>' +
          '<span class="log-item-tag">' + (it.ok ? '✅ 完成' : '❌ 失败') + '</span>' +
          (it.error ? '<div class="log-item-err">' + esc(it.error) + '</div>' : '') +
        '</div>';
      }).join('') + '</div>';
    },

    /** 日志框（唯一一个，滚动） */
    logBoxHtml: function (lines) {
      return '<div class="log-box">' + (
        (lines || []).length
          ? lines.map(function (l) {
              return '<div class="log-line' + (/❌|失败|错误/.test(l) ? ' is-err' : '') + '">' +
                esc(l) + '</div>';
            }).join('')
          : '<div class="log-line">（暂无日志）</div>'
      ) + '</div>';
    },

    /** 渲染当前任务的完整单框（头部 + 日志框） */
    renderTask: function (t) {
      if (!this.el || !t) return;
      var badge = STATUS_BADGE[t.status] || ['', ''];

      var stat = '';
      var res = (t.result && t.result.items && t.result.items.length) ? t.result : null;
      if (res) {
        stat = '<span class="log-stat ok">成功 ' + res.success + '</span>' +
          (res.fail ? '<span class="log-stat err">失败 ' + res.fail + '</span>' : '');
      } else if (t.status === 'failed' && t.error) {
        stat = '<span class="log-stat err">' + esc(t.error) + '</span>';
      }

      var zip = (t.result_json || {}).zip;
      var link = zip ? '<a class="log-link" href="' + esc(zip) + '">下载结果 →</a>' : '';

      this.el.innerHTML =
        '<div class="log-entry' + (t.status === 'running' ? ' is-live' : '') + '" data-id="' + esc(t.id) + '">' +
          '<div class="log-head">' +
            '<span class="log-query">' + esc(t.query || ('任务 #' + t.id)) + '</span>' +
            stat +
            '<span class="log-stat ' + badge[1] + '">' + badge[0] + '</span>' +
            '<span class="log-time">' + esc(t.updated_at || t.created_at || '') + '</span>' +
            link +
          '</div>' +
          this.itemsHtml(t) +
          this.logBoxHtml(t.lines || []) +
        '</div>';
      this.scrollBottom();
    },

    /** 开始实时跟踪一个任务：整个面板切成该任务的单框 */
    startLive: function (taskId, query) {
      if (!this.el) return;
      this.el.innerHTML =
        '<div class="log-entry is-live" data-id="' + esc(taskId) + '">' +
          '<div class="log-head">' +
            '<span class="log-query">' + esc(query || ('任务 #' + taskId)) + '</span>' +
            '<span class="log-stat running">执行中</span>' +
          '</div>' +
          '<div class="log-box"></div>' +
        '</div>';
      this.liveTask = { id: String(taskId), lastLine: '' };
    },

    /** 实时追加一行进度（去重），自动滚到底 */
    pushLine: function (line) {
      if (!this.liveTask || !line || line === this.liveTask.lastLine) return;
      var entry = this.el && this.el.querySelector('.log-entry[data-id="' + this.liveTask.id + '"]');
      var box = entry ? entry.querySelector('.log-box') : null;
      if (!box) return;
      var hint = box.querySelector('.log-line');
      if (hint && hint.textContent === '（暂无日志）') box.innerHTML = '';
      var div = document.createElement('div');
      div.className = 'log-line' + (/❌|失败|错误/.test(line) ? ' is-err' : '');
      div.textContent = line;
      box.appendChild(div);
      this.liveTask.lastLine = line;
      this.scrollBottom();
    },

    scrollBottom: function () {
      if (!this.el) return;
      var box = this.el.querySelector('.log-box');
      if (box) box.scrollTop = box.scrollHeight;
    },

    /** 任务结束：拉服务端结果重画单框（状态徽标 + 成功/失败明细 + 完整日志） */
    finishLive: function (taskId, fallbackError) {
      var self = this;
      if (!this.liveTask || String(this.liveTask.id) !== String(taskId)) return;
      var entry = this.el.querySelector('.log-entry[data-id="' + this.liveTask.id + '"]');
      var query = entry
        ? ((entry.querySelector('.log-query') || {}).textContent || '')
        : '';
      this.liveTask = null;
      fetch('/agent/api/tasks/' + taskId + '/log', {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          var d = j.data || {};
          self.renderTask({
            id: taskId,
            query: query,
            status: d.status || 'done',
            error: fallbackError || '',
            created_at: '', updated_at: '',
            result_json: {},
            lines: d.lines || [],
            result: d.result || {},
          });
        })
        .catch(function () { self.load(); });
    },
  };

  window.AgentLogPanel = LogPanel;
})();
