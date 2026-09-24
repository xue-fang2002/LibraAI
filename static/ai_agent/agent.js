/**
 * AI 任务中心主控制器：绑事件、发请求、把结果交给组件渲染。
 * 所有 fetch 都带 X-Requested-With（全局 CSRF 要求）。
 */
(function () {
  'use strict';

  var ta = document.getElementById('agentQuery');
  var planBtn = document.getElementById('agentPlanBtn');
  var hint = document.getElementById('agentHint');
  var planArea = document.getElementById('agentPlanArea');
  var logPanelEl = document.getElementById('agentLogPanel');
  var refreshBtn = document.getElementById('agentRefreshBtn');
  if (!ta || !planBtn) return;

  var currentPlan = null;   // 最近一次生成的计划

  function ajax(url, options) {
    var opt = options || {};
    return fetch(url, {
      method: opt.method || 'GET',
      headers: Object.assign(
        { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        opt.headers || {}),
      body: opt.body ? JSON.stringify(opt.body) : undefined,
    }).then(function (r) {
      return r.json().catch(function () { return {}; });
    });
  }

  function setHint(msg, isErr) {
    if (!hint) return;
    hint.textContent = msg;
    hint.style.color = isErr ? '#c0392b' : '';
  }

  /** 刷新执行日志面板（每次任务跑完都调，保证成功/失败明细可见） */
  function refreshLogs() {
    if (window.AgentLogPanel && logPanelEl) window.AgentLogPanel.load();
  }

  function generatePlan() {
    var text = (ta.value || '').trim();
    if (!text) { setHint('请先描述你要做的事', true); return; }

    planBtn.disabled = true;
    planBtn.textContent = '⏳ 规划中...';
    setHint('正在判断该用哪个模块…');

    ajax('/agent/api/plan', { method: 'POST', body: { text: text } })
      .then(function (j) {
        if (j.code !== 200) { setHint((j && j.msg) || '规划失败', true); return; }
        var d = j.data || {};
        if (!d.planned) {
          planArea.innerHTML = '';
          setHint(d.reply || '没能生成计划', true);
          return;
        }
        currentPlan = d;
        setHint('已生成计划，确认后执行');
        window.AgentPlanCard.render(planArea, d, { onConfirm: onConfirm, onCancel: onCancel });
      })
      .catch(function (e) { setHint('请求失败：' + e.message, true); })
      .finally(function () {
        planBtn.disabled = false;
        planBtn.textContent = '✨ 生成计划';
      });
  }

  function onConfirm(params) {
    if (!currentPlan) return;
    var card = planArea.querySelector('.plan-card');
    var taskId = currentPlan.task_id;
    var exec = currentPlan.execution;

    // 就地 / 异步 执行且需要文件时，把选中的工作区文件名一并发给后端
    // （后端按名从工作区复制进临时目录，见 executor._run_toolbox / run_async）
    if ((exec === 'inplace' || exec === 'async') && currentPlan.needs_files) {
      var files = window.AgentPlanCard.getFiles(card);
      if (!files.length) {
        window.AgentPlanCard.showError(card, '请先选择至少一个输入文件');
        return;
      }
      params.__files = files;
    }

    // 异步型：提交后立即返回 running，前端轮询进度，完成后展示结果
    if (exec === 'async') {
      window.AgentPlanCard.busy(card, true, '⏳ 后台执行中…');
      setHint('任务已提交，后台执行中…');
      ajax('/agent/api/tasks/' + taskId + '/confirm', { method: 'POST', body: { params: params } })
        .then(function (j) {
          if (j.code !== 200) {
            window.AgentPlanCard.busy(card, false, '✓ 确认执行');
            window.AgentPlanCard.showError(card, (j && j.msg) || '提交失败');
            setHint('提交失败', true);
            return;
          }
          window.AgentPlanCard.showAsync(card);
          if (window.AgentLogPanel) {
            window.AgentLogPanel.startLive(taskId, currentPlan.query || '');
          }
          pollProgress(taskId, card);
        })
        .catch(function (e) {
          window.AgentPlanCard.busy(card, false, '✓ 确认执行');
          window.AgentPlanCard.showError(card, e.message || '请求失败');
        });
      return;
    }

    // 跳转型：直接开新标签去目标页面，同时把任务标记完成
    if (exec !== 'inplace') {
      window.AgentPlanCard.busy(card, true, '正在打开…');
      ajax('/agent/api/tasks/' + taskId + '/confirm', { method: 'POST', body: { params: params } })
        .then(function (j) {
          if (j.code !== 200) {
            window.AgentPlanCard.showError(card, (j && j.msg) || '确认失败');
            return;
          }
          var url = (j.data || {}).link || currentPlan.link;
          if (url) window.open(url, '_blank', 'noopener');
          window.AgentPlanCard.showResult(card, { result: { kind: 'link', url: url } });
          setHint('已打开目标页面，请在其中完成剩余操作');
          refreshLogs();
        })
        .finally(function () {
          window.AgentPlanCard.busy(card, false, '→ 前往执行');
        });
      return;
    }

    // 原地执行型：后端跑完，结果直接回显在卡片里
    window.AgentPlanCard.busy(card, true, '⏳ 执行中...');
    setHint('正在执行，请稍候…');
    ajax('/agent/api/tasks/' + taskId + '/confirm', { method: 'POST', body: { params: params } })
      .then(function (j) {
        if (j.code !== 200) {
          window.AgentPlanCard.showError(card, (j && j.msg) || '执行失败');
          setHint('执行失败', true);
          return;
        }
        window.AgentPlanCard.showResult(card, j.data || {});
        setHint('执行完成');
        refreshLogs();
      })
      .catch(function (e) {
        window.AgentPlanCard.showError(card, e.message || '请求失败');
      })
      .finally(function () {
        window.AgentPlanCard.busy(card, false, '✓ 确认执行');
      });
  }

  function pollProgress(taskId, card) {
    var timer = setInterval(function () {
      ajax('/agent/api/tasks/' + taskId + '/progress')
        .then(function (j) {
          if (j.code !== 200) return;
          var d = j.data || {};
          window.AgentPlanCard.updateProgress(card, d.progress || {}, d.status);
          // 实时把进度写进执行日志面板
          if (window.AgentLogPanel && d.progress && d.progress.message) {
            window.AgentLogPanel.pushLine(d.progress.message);
          }
          if (d.status === 'done' || d.status === 'failed') {
            clearInterval(timer);
            var failed = d.status === 'failed';
            var msg = ((d.result || {}).error)
              || (d.progress && d.progress.message) || '执行失败';
            if (!failed) {
              window.AgentPlanCard.showResult(card, { result: d.result || {} });
              setHint('执行完成');
            } else {
              window.AgentPlanCard.showError(card, msg);
              setHint('执行失败', true);
            }
            window.AgentPlanCard.busy(card, false, '✓ 已完成');
            // 收尾：把这一条画成带成功/失败明细的结果块
            if (window.AgentLogPanel) {
              window.AgentLogPanel.finishLive(taskId, failed ? msg : '');
            } else {
              refreshLogs();
            }
          }
        })
        .catch(function () { /* 轮询失败不影响下次重试 */ });
    }, 1200);
  }

  function onCancel() {
    setHint('已取消该计划');
  }

  planBtn.addEventListener('click', generatePlan);
  ta.addEventListener('keydown', function (e) {
    if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); generatePlan(); }
  });
  if (refreshBtn) refreshBtn.addEventListener('click', refreshLogs);

  // 首屏加载执行日志（最近若干条任务及其成功/失败明细）
  if (window.AgentLogPanel && logPanelEl) window.AgentLogPanel.init(logPanelEl);
})();
